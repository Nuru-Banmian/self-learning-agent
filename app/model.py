import asyncio
from typing import Any

import httpx

from app.settings import Settings
from app.store import Store

MODEL = "qwen3.7-plus-2026-05-26"
CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "create_todos",
        "description": "仅在用户明确安排或要求记录时，一次提交本句所有待办。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "date_text"],
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "原文中的事项连续片段",
                            },
                            "date_text": {
                                "type": ["string", "null"],
                                "description": "原文日期短语；没说日期则为null",
                            },
                        },
                    },
                }
            },
        },
    },
}


def function_tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS = [
    CREATE_TOOL,
    function_tool(
        "update_todo",
        "修改一项待办，必须使用实际稳定ID。只提交用户要求修改的字段。",
        {
            "todo_id": {"type": "string"},
            "title": {"type": "string"},
            "date_text": {
                "type": ["string", "null"],
                "description": "原文日期；null表示改为未安排",
            },
        },
        ["todo_id"],
    ),
    function_tool(
        "complete_todo",
        "用户明确要求标记完成一项待办，使用实际稳定ID。",
        {"todo_id": {"type": "string"}},
        ["todo_id"],
    ),
    function_tool("list_todos", "查询真实待办及状态，不修改。", {}, []),
    function_tool(
        "plan_day",
        "根据真实待办生成基础当天计划。只返回可选行动建议，不声称建议已安排，不写待办。空清单不虚构安排。",
        {"suggestions": {"type": "array", "maxItems": 5, "items": {"type": "string"}}},
        ["suggestions"],
    ),
    function_tool(
        "accept_suggestion",
        "用户明确要求将当前会话一项行动建议加入待办，使用建议稳定ID。",
        {"suggestion_id": {"type": "string"}},
        ["suggestion_id"],
    ),
]


class ModelError(Exception):
    pass


async def call_model(
    settings: Settings,
    store: Store,
    run_id: str,
    messages: list[dict[str, Any]],
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    key = settings.dashscope_api_key.get_secret_value()
    if not key:
        raise ModelError("模型未配置，请在后端 .env 填写百炼凭据。待办列表仍可查看。")
    async with httpx.AsyncClient(
        timeout=settings.model_timeout_seconds,
        transport=transport,
        trust_env=False,
    ) as client:
        attempts = min(settings.max_model_calls, settings.model_retries + 1)
        for attempt in range(attempts):
            store.count_call(run_id)
            try:
                response = await client.post(
                    settings.dashscope_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": MODEL,
                        "messages": messages,
                        "enable_thinking": False,
                        "tools": TOOLS,
                        "tool_choice": "auto",
                        "max_tokens": 1800,
                    },
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()["choices"][0]["message"]
                if not isinstance(result, dict):
                    raise TypeError("invalid message")
                return result
            except httpx.RequestError:
                pass
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504):
                    break
            except (ValueError, KeyError, IndexError, TypeError):
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(0.2 * (attempt + 1))
    # Never echo provider bodies, headers or exception strings containing credentials.
    raise ModelError("模型暂时不可用或响应无效，未保存待办；已有待办仍可查看。")

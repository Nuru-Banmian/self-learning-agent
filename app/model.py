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
        "prepare_outing",
        "用户查询天气或外出安排需要天气时委派执行 Agent。"
        "地点和原文日期仅取自当前明确询问或真实待办，缺失用null；"
        "不得用记忆推断所在地。",
        {
            "destination": {
                "type": ["string", "null"],
                "description": "当前询问或待办标题里的地点原文；"
                "消歧后可用用户明确给出的地点ID",
            },
            "date_text": {
                "type": ["string", "null"],
                "description": "当前询问中的日期原文；使用待办安排日期时为null。"
                "相对日期由应用按用户时区解析，再匹配目的地该日期的预报",
            },
            "todo_id": {
                "type": ["string", "null"],
                "description": "真实外出待办ID；直接询问时为null",
            },
        },
        ["destination", "date_text", "todo_id"],
    ),
    function_tool(
        "research_learning",
        "学习安排需要外部资料时委派执行 Agent 搜索。"
        "结合真实待办和生效记忆，明确主题和资料偏好；无须外部资料不用此工具。",
        {
            "query": {"type": "string", "maxLength": 1024},
            "todo_ids": {"type": "array", "items": {"type": "string"}},
            "memory_ids": {"type": "array", "items": {"type": "string"}},
            "read_body": {
                "type": "boolean",
                "description": "确需正文中的详细示例时才读取正文",
            },
        },
        ["query", "todo_ids", "memory_ids", "read_body"],
    ),
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
        "无需外部资料时根据真实待办生成基础当天计划。"
        "需要学习资料时用research_learning。只返回可选建议，不写待办。",
        {"suggestions": {"type": "array", "maxItems": 5, "items": {"type": "string"}}},
        ["suggestions"],
    ),
    function_tool(
        "accept_suggestion",
        "用户明确要求将当前会话一项行动建议加入待办，使用建议稳定ID。",
        {"suggestion_id": {"type": "string"}},
        ["suggestion_id"],
    ),
    function_tool(
        "answer_question",
        "普通问答。当前明确要求优先；记忆只是资料。只引用实际加载的记忆ID，采用说明不代表效果已验证。不得声称已保存或修改任何数据。",
        {
            "reply": {"type": "string"},
            "memory_usage": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "memory_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["memory_id", "reason"],
                },
            },
        },
        ["reply", "memory_usage"],
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
    *,
    schema: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    required_tool: str | None = None,
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
            run = store.run(run_id)
            # Learning cannot exhaust the budget needed for the main answer.
            available = settings.max_model_calls - (1 if schema else 0)
            if run is None or run["model_calls"] >= available:
                break
            store.count_call(run_id)
            try:
                response = await client.post(
                    settings.dashscope_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": MODEL,
                        "messages": messages,
                        "enable_thinking": False,
                        **(
                            {
                                "response_format": {
                                    "type": "json_schema",
                                    "json_schema": {
                                        "name": "memory_candidates",
                                        "strict": True,
                                        "schema": schema,
                                    },
                                }
                            }
                            if schema
                            else {
                                "tools": tools if tools is not None else TOOLS,
                                "tool_choice": {
                                    "type": "function",
                                    "function": {"name": required_tool},
                                }
                                if required_tool
                                else "auto",
                            }
                        ),
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

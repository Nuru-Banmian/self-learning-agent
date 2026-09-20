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
                        "tools": [CREATE_TOOL],
                        "tool_choice": "auto",
                        "max_tokens": 1800,
                    },
                )
                response.raise_for_status()
                result: dict[str, Any] = response.json()["choices"][0]["message"]
                if not isinstance(result, dict):
                    raise TypeError("invalid message")
                return result
            except (httpx.TimeoutException, httpx.NetworkError):
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

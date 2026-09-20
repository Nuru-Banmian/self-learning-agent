import asyncio
import json
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.model import ModelError, call_model
from app.settings import Settings
from app.store import Store
from app.todos import Clarification, prepare_todos


async def execute(
    store: Store,
    settings: Settings,
    run_id: str,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    run = store.run(run_id)
    assert run is not None
    try:
        async with asyncio.timeout(settings.run_timeout_seconds):
            local = datetime.fromisoformat(run["received_at"]).astimezone(
                ZoneInfo(settings.user_timezone)
            )
            response = await call_model(
                settings,
                store,
                run_id,
                [
                    {
                        "role": "system",
                        "content": (
                            "你是生活助理的主 Agent。"
                            "仅明确安排/记录指令才调用 create_todos。"
                            "问题、否定、建议均不能写入。日期不明确先追问。"
                            "一次工具调用包含本句全部事项，标题使用用户原文的连续片段。"
                            "date_text使用原文日期，不自行转为绝对日期；无日期用null。"
                            "不调用工具时正常回答，不声称已保存。"
                            f"消息接收本地时间：{local.isoformat()}。"
                            "实际已有待办："
                            + json.dumps(store.todos(), ensure_ascii=False)
                        ),
                    },
                    {"role": "user", "content": run["content"]},
                ],
                transport,
            )
            calls = response.get("tool_calls") or []
            if not calls:
                reply = response.get("content") or "请说明安排。"
                if re.search(
                    r"(?:已|成功|帮你|为你).{0,8}(?:保存|记录|添加|记下|安排)", reply
                ):
                    todos = store.todos()
                    reply = (
                        "当前已保存的待办：\n"
                        + "\n".join(
                            f"• {t['title']} — {t['scheduled_date'] or '未安排'}"
                            for t in todos
                        )
                        if todos
                        else "尚无待办。请明确要记录的事项与日期。"
                    )
                store.finish(run_id, "completed", "本轮未新增待办。\n" + reply)
                return
            if len(calls) != 1 or calls[0]["function"]["name"] != "create_todos":
                raise ValueError("无法验证操作，请明确要记录的待办。")
            prepared = prepare_todos(
                calls[0]["function"]["arguments"], run["content"], local.date()
            )
            store.event(run_id, "tool_call", {"role": "main", "tool": "create_todos"})
            reply = "已保存：\n" + "\n".join(
                f"• {i['title']} — {i['scheduled_date'] or '未安排'}" for i in prepared
            )
            store.finish(run_id, "completed", reply, prepared)
    except Clarification as exc:
        store.finish(run_id, "completed", str(exc))
    except (ModelError, ValueError) as exc:
        store.finish(run_id, "failed", str(exc), error="validation_or_model")
    except TimeoutError:
        store.finish(run_id, "failed", "处理超时，未保存待办。", error="timeout")
    except sqlite3.Error:
        store.finish(run_id, "failed", "保存失败，本次待办未写入。", error="storage")
    except (KeyError, TypeError, IndexError):
        store.finish(
            run_id, "failed", "模型返回无效操作，未保存待办。", error="invalid_tool"
        )

import asyncio
import json
import sqlite3
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.model import ModelError, call_model
from app.settings import Settings
from app.store import Store
from app.todos import (
    Clarification,
    DayPlan,
    authorize_change,
    overview,
    prepare_accept,
    prepare_change,
    prepare_todos,
    render_overview,
)


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
            if run["action"]:
                action = run["action"]
                if action["tool"] == "accept_suggestion":
                    accepted = prepare_accept(
                        action["arguments"], store.suggestions(run["session_id"]), None
                    )
                    store.event(
                        run_id, "tool_call", {"role": "main", "tool": action["tool"]}
                    )
                    store.finish(
                        run_id, "completed", "", accept=accepted, tool=action["tool"]
                    )
                    return
                change = prepare_change(
                    action["tool"], action["arguments"], local.date()
                )
                store.event(
                    run_id, "tool_call", {"role": "main", "tool": action["tool"]}
                )
                store.finish(
                    run_id, "completed", "", change=change, tool=action["tool"]
                )
                return
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
                            "查询待办用list_todos；修改用update_todo，完成用complete_todo。"
                            "使用实际稳定ID，重名或代词不清先追问，不替用户选择。"
                            "修改只提交要求修改的字段。"
                            "今天该做什么或规划今天用plan_day，根据真实待办给具体行动步骤；不要另调list_todos。"
                            "行动建议应给出可执行的小步骤，不只重复待办标题，不把未来待办当作今天必须完成。"
                            "用户接受建议用accept_suggestion；只能使用当前会话的建议ID。"
                            "不调用工具时正常回答，不声称已保存。"
                            f"消息接收本地时间：{local.isoformat()}。"
                            "实际已有待办："
                            + json.dumps(store.todos(), ensure_ascii=False)
                            + "。当前会话行动建议："
                            + json.dumps(
                                store.suggestions(run["session_id"]), ensure_ascii=False
                            )
                        ),
                    },
                    {"role": "user", "content": run["content"]},
                ],
                transport,
            )
            calls = response.get("tool_calls") or []
            if not calls:
                reply = render_overview(overview(store.todos(), local.date()))
                store.finish(
                    run_id,
                    "completed",
                    "本轮未新增或修改待办。请明确事项、目标及日期，或使用面板操作。\n"
                    + reply,
                )
                return
            if len(calls) != 1:
                raise ValueError("无法验证操作，请明确要记录的待办。")
            function = calls[0]["function"]
            tool = function["name"]
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("模型操作参数无效，未修改待办。")
            if tool == "list_todos":
                store.event(run_id, "tool_call", {"role": "main", "tool": tool})
                data = overview(store.todos(), local.date())
                store.event(
                    run_id, "tool_result", {"tool": tool, "status": "success", **data}
                )
                store.finish(run_id, "completed", render_overview(data))
                return
            if tool == "plan_day":
                try:
                    plan = DayPlan.model_validate(arguments)
                except ValidationError:
                    raise Clarification("计划内容无效，请重试；待办未修改。") from None
                store.event(run_id, "tool_call", {"role": "main", "tool": tool})
                suggestions = [
                    {
                        "id": str(uuid4()),
                        "title": title,
                        "scheduled_date": local.date().isoformat(),
                    }
                    for title in plan.suggestions
                ]
                reply = render_overview(overview(store.todos(), local.date()))
                reply += (
                    "\n\n行动建议（尚未加入待办；明确加入后安排在 "
                    + local.date().isoformat()
                    + "）：\n"
                )
                reply += (
                    "\n".join(f"• {s['title']} [建议 {s['id']}]" for s in suggestions)
                    or "暂无行动建议。"
                )
                store.finish(run_id, "completed", reply, suggestions=suggestions)
                return
            if tool == "accept_suggestion":
                accepted = prepare_accept(
                    arguments, store.suggestions(run["session_id"]), run["content"]
                )
                store.event(run_id, "tool_call", {"role": "main", "tool": tool})
                store.finish(run_id, "completed", "", accept=accepted, tool=tool)
                return
            if tool in ("update_todo", "complete_todo"):
                change = authorize_change(
                    tool, arguments, run["content"], store.todos(), local.date()
                )
                store.event(run_id, "tool_call", {"role": "main", "tool": tool})
                store.finish(run_id, "completed", "", change=change, tool=tool)
                return
            if tool != "create_todos":
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

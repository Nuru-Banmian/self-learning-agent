"""Source-grounded memory proposals; the application owns scope and writes."""

import json
import re
from datetime import datetime, time, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.memory_policy import category_of, general_time_condition, source_clauses
from app.model import call_model
from app.settings import Settings
from app.store import Store


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=3, max_length=300)
    source_message_id: str
    category: Literal["preference", "background", "condition"]
    topic: str = Field(min_length=2, max_length=30)
    scope: Literal["general", "task"]
    task_id: str | None
    validity: Literal["ongoing", "today", "tomorrow", "task"]


class Proposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidates: list[Candidate] = Field(max_length=8)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    memory_id: str
    reason: str = Field(min_length=1, max_length=300)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply: str = Field(min_length=1, max_length=6000)
    memory_usage: list[Usage] = Field(max_length=6)


def active(memory: dict[str, Any], now: datetime, todos: list[dict[str, Any]]) -> bool:
    if memory["state"] != "active":
        return False
    if datetime.fromisoformat(memory["valid_from"]) > now:
        return False
    if memory["expires_at"] and now >= datetime.fromisoformat(memory["expires_at"]):
        return False
    return memory["scope"] != "task" or any(
        t["id"] == memory["task_id"] and t["status"] == "pending" for t in todos
    )


def select_memories(store: Store, query: str, now: datetime) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    todos = store.todos()
    for memory in reversed(store.memories()):
        if not active(memory, now, todos):
            continue
        if memory["category"] == "preference" and re.search(r"这次|本次", query):
            continue
        if memory["scope"] == "task" and not any(
            t["id"] == memory["task_id"] and (t["id"] in query or t["title"] in query)
            for t in todos
        ):
            continue
        topic = memory["topic"].casefold()
        topic_terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", topic)
        relevant = any(
            term in query.casefold()
            or (
                re.fullmatch(r"[\u4e00-\u9fff]+", term)
                and any(term[i : i + 2] in query for i in range(len(term) - 1))
            )
            for term in topic_terms
        )
        if not relevant and not (
            memory["category"] == "condition"
            and any(w in query for w in ("今天", "计划", "安排"))
        ):
            continue
        # Only the exact evidence excerpt is injected, never old conversation history.
        item = {
            k: memory[k]
            for k in (
                "id",
                "content",
                "category",
                "topic",
                "scope",
                "task_id",
                "valid_from",
                "expires_at",
            )
        }
        item["source"] = {k: memory["source"][k] for k in ("message_id", "session_id")}
        if len(json.dumps(selected + [item], ensure_ascii=False)) > 4000:
            continue
        selected.append(item)
        if len(selected) == 6:
            break
    return selected


def answer_text(reply: str, query: str) -> str:
    # Model prose is not a persistence receipt. Only the committed event/panel is.
    forbidden = r"记住|已.{0,6}(?:保存|记录|更新|修改|删除|完成)|记下了"
    if re.search(r"这次|本次", query):
        forbidden += r"|以后|下次|今后|永久"
    parts = re.split(r"(?<=[。！？\n])", reply)
    result = "".join(p for p in parts if not re.search(forbidden, p)).strip()
    return result or "已收到你的表达。本次按当前要求处理，保存结果见记忆面板。"


async def learn(
    store: Store,
    settings: Settings,
    run: dict[str, Any],
    local: datetime,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    clauses = source_clauses(run["content"])
    if not any(category_of(clause) for clause in clauses):
        store.memory_record(run["id"], learning="empty")
        return
    store.event(run["id"], "role", {"role": "learning", "status": "processing"})
    response = await call_model(
        settings,
        store,
        run["id"],
        [
            {
                "role": "system",
                "content": (
                    "你是学习 Agent，只提出用户记忆候选，不执行操作。"
                    "只提取当前用户自己明确表达的背景、偏好、临时条件，不保存待办、问题、推测或引用。"
                    "content 必须是用户原文完整分句，不能改写或遗漏否定、时间、限定。"
                    "topic 必须是原文中至少两个字符的连续片段，不能生成原文没有的词。"
                    "例如‘今天只有半小时’的 topic=半小时，不能用原文没有的‘时间’。"
                    "持续偏好为 preference/general/ongoing；"
                    "背景为 background/general/ongoing；"
                    "今天或明天条件为 condition/general/today或tomorrow；"
                    "本次某待办条件为 condition/task/task，使用给定待办ID，"
                    "原文必须包含完整待办标题。"
                    "无法确认或没有信息输出空 candidates。只输出指定 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message_id": run["message_id"],
                        "content": run["content"],
                        "local_time": local.isoformat(),
                        "tasks": [
                            {"id": t["id"], "title": t["title"]} for t in store.todos()
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        transport,
        schema=Proposals.model_json_schema(),
    )
    proposals = Proposals.model_validate_json(response.get("content") or "")
    valid = []
    rejected = 0
    for candidate in proposals.candidates:
        category = category_of(candidate.content)
        if (
            candidate.source_message_id != run["message_id"]
            or candidate.content not in clauses
            or category != candidate.category
            or candidate.topic not in candidate.content
        ):
            rejected += 1
            continue
        if any(
            re.search(
                r"仅|只限|只在|限于|限时|暂时|最近|期间|时候|时$|"
                r"(?:这|本|下|今|明|后|上)(?:天|次|周|星期|月|年)|"
                r"\d+(?:月|日|号)|\d{4}-",
                c,
            )
            for c in clauses
            if c != candidate.content
        ):
            rejected += 1
            continue
        start = local
        end = None
        if category == "condition":
            targets = [t for t in store.todos() if t["title"] in candidate.content]
            if candidate.scope == "task":
                if (
                    len(targets) != 1
                    or targets[0]["id"] != candidate.task_id
                    or targets[0]["status"] != "pending"
                ):
                    rejected += 1
                    continue
            elif (
                targets
                or candidate.task_id
                or not general_time_condition(candidate.content)
            ):
                rejected += 1
                continue
            if "今天" in candidate.content or "明天" in candidate.content:
                validity = "today" if "今天" in candidate.content else "tomorrow"
                if candidate.validity != validity:
                    rejected += 1
                    continue
                day = local.date() + timedelta(days=validity == "tomorrow")
                start = datetime.combine(day, time(), local.tzinfo)
                end = datetime.combine(day + timedelta(days=1), time(), local.tzinfo)
            elif candidate.scope != "task" or candidate.validity != "task":
                rejected += 1
                continue
        elif (
            candidate.scope != "general"
            or candidate.task_id
            or candidate.validity != "ongoing"
        ):
            rejected += 1
            continue
        valid.append(
            candidate.model_dump()
            | {
                "valid_from": start.isoformat(),
                "expires_at": end.isoformat() if end else None,
            }
        )
    store.save_memories(run["id"], valid, rejected)

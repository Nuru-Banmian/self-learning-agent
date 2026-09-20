"""Application-owned memory maintenance; no model has direct write access."""

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.memory_policy import category_of, same_subject, subject_topic, validate_memory
from app.todos import Clarification


class MemoryTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    memory_id: str
    expected_source_id: str


class MemoryUpdate(MemoryTarget):
    content: str = Field(min_length=3, max_length=300)
    topic: str = Field(min_length=2, max_length=30)
    scope: Literal["general", "task"]
    task_id: str | None
    validity: Literal["ongoing", "today", "tomorrow", "task"]


def prepare_memory_change(
    tool: str, arguments: dict[str, Any], local: datetime, todos: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        target = (
            MemoryUpdate if tool == "update_memory" else MemoryTarget
        ).model_validate(arguments)
    except ValidationError:
        raise Clarification(
            "记忆参数无效，请刷新面板并明确内容、范围和期限；未修改。"
        ) from None
    change = target.model_dump() | {"tool": tool}
    if isinstance(target, MemoryUpdate):
        value = validate_memory(
            target.model_dump() | {"category": category_of(target.content)},
            local,
            todos,
        )
        if value is None:
            raise Clarification(
                "内容与范围或期限不一致，请用完整陈述明确适用条件；未修改。"
            )
        change |= value
    return change


def chat_memory_change(
    text: str,
    memories: list[dict[str, Any]],
    todos: list[dict[str, Any]],
    local: datetime,
) -> dict[str, Any] | None:
    """Recognize explicit corrections; uncertain intent/targets never write."""
    text = text.strip().rstrip("。！!")
    explicit = re.fullmatch(
        r"(?:请)?(?:把|将)记忆\s*(.+?)\s*(?:改为|改成)\s*(.+)", text
    )
    correction = re.fullmatch(r"(?:请)?(?:更正|纠正)(?:一下)?[：:，,\s]+(.+)", text)
    deletion = re.fullmatch(r"(?:请)?删除记忆\s*(.+)", text)
    if not (explicit or correction or deletion):
        if re.search(
            r"更正|纠正|(?:记忆|偏好|以后).*(?:改成|改为)|删除记忆", text
        ) and not re.search(r"这次|本次", text):
            raise Clarification(
                "请明确要更正的记忆和完整新内容，或说明仅本次例外；未修改记忆。"
            )
        return None
    if re.search(r"[？?吗“”\"]|如果|假如|不要|不用|别", text):
        raise Clarification("请明确更正或删除指令；未修改记忆。")
    new_content = explicit[2] if explicit else correction[1] if correction else ""
    category = category_of(new_content) if new_content else None
    task_targets = [t for t in todos if t["title"] in new_content]
    scope = "task" if category == "condition" and task_targets else "general"
    task_id = (
        task_targets[0]["id"] if len(task_targets) == 1 and scope == "task" else None
    )
    if explicit or deletion:
        match = explicit if explicit is not None else deletion
        assert match is not None
        reference = match[1].strip()
        targets = [m for m in memories if reference in (m["id"], m["content"])]
    else:
        targets = [
            m
            for m in memories
            if m["category"] == category
            and m["scope"] == scope
            and m["task_id"] == task_id
            and (
                not ("今天" in new_content or "明天" in new_content)
                or datetime.fromisoformat(m["valid_from"])
                .astimezone(local.tzinfo)
                .date()
                == local.date() + timedelta(days="明天" in new_content)
            )
            and same_subject(
                m,
                {
                    "content": new_content,
                    "topic": subject_topic(new_content),
                    "category": category,
                },
            )
        ]
    if len(targets) != 1:
        raise Clarification(
            "无法唯一定位旧记忆，请用“把记忆 完整ID 改为 完整陈述”或面板操作；未修改。"
        )
    old = targets[0]
    arguments = {"memory_id": old["id"], "expected_source_id": old["message_id"]}
    if deletion:
        return prepare_memory_change("delete_memory", arguments, local, todos)
    if old["scope"] != scope or old["task_id"] != task_id:
        raise Clarification(
            "新内容改变了适用范围，请说明是否仅本次例外，或在面板明确编辑范围；未修改。"
        )
    topic = (
        old["topic"]
        if old["topic"] in new_content
        else subject_topic(
            re.sub(r"^我(?:住在|居住在|从事|工作是|是)", "", new_content)
        )[:30]
    )
    validity = (
        "today"
        if "今天" in new_content
        else "tomorrow"
        if "明天" in new_content
        else "task"
        if scope == "task"
        else "ongoing"
    )
    return prepare_memory_change(
        "update_memory",
        arguments
        | {
            "content": new_content,
            "topic": topic,
            "scope": scope,
            "task_id": task_id,
            "validity": validity,
        },
        local,
        todos,
    )

"""Conservative source and scope rules shared by extraction and persistence."""

import re
from datetime import datetime, time, timedelta
from typing import Any


def subject_topic(text: str) -> str:
    # Assertion grammar is not evidence that two facts share a subject.
    return re.sub(
        r"^(?:(?:我|以后|更|通常|一般|一直|比较|不喜欢|喜欢|偏好|习惯|优先|不爱|倾向|阅读|观看|看|正在学习|在学)\s*)+",
        "",
        text,
    ).strip()


def same_subject(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_attribute = fact_attribute(left["content"])
    right_attribute = fact_attribute(right["content"])
    if left_attribute or right_attribute:
        return left_attribute == right_attribute
    if general_time_condition(left["content"]) and general_time_condition(
        right["content"]
    ):
        return True
    a, b = subject_topic(left["topic"]), subject_topic(right["topic"])
    if not a or not b:
        return False
    if a in right["content"] or b in left["content"]:
        return True
    # Background identifiers (e.g. 主题1 vs 主题2) must not merge on a shared stem.
    if left["category"] == right["category"] == "background":
        return False
    return topic_matches(a, b) or topic_matches(b, a)


def topic_matches(topic: str, content: str) -> bool:
    terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", subject_topic(topic).casefold())
    return any(
        term in content.casefold()
        or (
            re.fullmatch(r"[\u4e00-\u9fff]+", term)
            and any(term[i : i + 2] in content for i in range(len(term) - 1))
        )
        for term in terms
    )


def overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        all(left[k] == right[k] for k in ("category", "scope", "task_id"))
        and (
            left["expires_at"] is None
            or datetime.fromisoformat(left["expires_at"])
            > datetime.fromisoformat(right["valid_from"])
        )
        and (
            right["expires_at"] is None
            or datetime.fromisoformat(right["expires_at"])
            > datetime.fromisoformat(left["valid_from"])
        )
        and same_subject(left, right)
    )


def validate_memory(
    candidate: dict[str, Any], local: datetime, todos: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """One scope/expiry policy for learning, explicit corrections and panel edits."""
    content = candidate["content"]
    category = category_of(content)
    if (
        source_clauses(content) != [content]
        or category is None
        or candidate["category"] != category
        or candidate["topic"] not in content
    ):
        return None
    start, end = local, None
    if category == "condition":
        targets = [t for t in todos if t["title"] in content]
        if candidate["scope"] == "task":
            if (
                len(targets) != 1
                or targets[0]["id"] != candidate["task_id"]
                or targets[0]["status"] != "pending"
            ):
                return None
        elif targets or candidate["task_id"] or not general_time_condition(content):
            return None
        if "今天" in content or "明天" in content:
            validity = "today" if "今天" in content else "tomorrow"
            if candidate["validity"] != validity:
                return None
            day = local.date() + timedelta(days=validity == "tomorrow")
            start = datetime.combine(day, time(), local.tzinfo)
            end = datetime.combine(day + timedelta(days=1), time(), local.tzinfo)
        elif candidate["scope"] != "task" or candidate["validity"] != "task":
            return None
    elif (
        candidate["scope"] != "general"
        or candidate["task_id"]
        or candidate["validity"] != "ongoing"
    ):
        return None
    return candidate | {
        "valid_from": start.isoformat(),
        "expires_at": end.isoformat() if end else None,
    }


def general_time_condition(content: str) -> bool:
    return (
        re.fullmatch(
            r"(?:我)?(?:今天|明天)(?:我)?(?:只有|仅有|有)"
            r"(?:半(?:个)?小时|[一二两三四五六七八九十百\d]+(?:个小时|小时|分钟))"
            r"(?:时间|空闲时间)?",
            content,
        )
        is not None
    )


def fact_attribute(content: str) -> str | None:
    for attribute, pattern in (
        ("residence", r"我(?:住在|居住在)"),
        ("occupation", r"我(?:从事|工作是)"),
        ("identity", r"我是"),
    ):
        if re.match(pattern, content):
            return attribute
    return None


def source_clauses(content: str) -> list[str]:
    # Conservative supported assertions, never quotations, hypotheticals or tasks.
    if re.search(
        r'[“”「」"：:？?]|假如|如果|可能|也许|据说|他说|她说|网页|文章|助理|示例|翻译|解释这',
        content,
    ):
        return []
    clauses: list[str] = []
    parts = re.split(r"([，,。；;\n！？!?])", content)
    for index in range(0, len(parts), 2):
        clause = parts[index].strip()
        if not clause:
            continue
        # Keep a stated preference's ordered steps together, including the
        # original comma. A full stop ends the preference, never joins a task.
        if (
            clauses
            and parts[index - 1] in ("，", ",")
            and category_of(clauses[-1]) == "preference"
            and "先" in clauses[-1]
            and re.match(r"再(?:看|读|做|练习)", clause)
        ):
            clauses[-1] += parts[index - 1] + parts[index]
        else:
            clauses.append(clause)
    return clauses


def mixed_todo_content(content: str) -> str | None:
    """Separate a standalone dated intention from ordinary personal statements.

    Only complete clauses are accepted. Adjacent scope qualifiers remain attached
    to memory, and the returned task still passes the full todo authorization.
    """
    clauses = source_clauses(content)
    tasks = [
        c
        for c in clauses
        if re.match(r"^(?:今天|明天|后天)我要\S", c)
        and not re.search(r"仅|只|限于|时候|期间|的话|但是|但|不过", c)
    ]
    if len(tasks) != 1 or len(clauses) < 2:
        return None
    if all(
        category_of(c) in ("preference", "background") for c in clauses if c != tasks[0]
    ):
        return tasks[0]
    return None


def category_of(clause: str) -> str | None:
    if re.search(
        r"这周|本周|下周|这月|本月|今年|最近|暂时|这几天|\d+月|\d+号|\d{4}-", clause
    ):
        return None
    if re.search(r"吗|是否|是不是|请记录|我要|打算|准备|帮我|提醒我", clause):
        return None
    if re.search(r"今天|明天|这次|本次|这项|这件", clause):
        return (
            "condition"
            if re.search(r"只有|只能|仅有|限于|用|需要|优先|喜欢", clause)
            else None
        )
    if re.match(
        r"(?:我|以后)(?:(?:看|阅读|学习)(?:技术)?(?:资料|文档|教程))?"
        r"(?:更|通常|一般|一直|比较)?(?:喜欢|偏好|习惯|优先|不喜欢|不爱|倾向)",
        clause,
    ):
        return "preference"
    if re.match(
        r"我(?:是|住在|居住在|正在学习|在学|目前在学|从事|工作是|有.{1,40}基础)",
        clause,
    ):
        return "background"
    return None

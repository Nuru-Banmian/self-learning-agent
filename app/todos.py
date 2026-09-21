import re
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.memory_policy import mixed_todo_content


class Clarification(Exception):
    pass


GROUP_LABELS = {
    "today": "今日未完成",
    "overdue": "逾期未完成",
    "unscheduled": "未安排",
    "upcoming": "未来安排",
    "completed": "已完成",
}


def overview(todos: list[dict[str, Any]], today: date) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in GROUP_LABELS}
    for todo in todos:
        day = todo["scheduled_date"]
        group = (
            "completed"
            if todo["status"] == "completed"
            else "unscheduled"
            if day is None
            else "overdue"
            if day < today.isoformat()
            else "today"
            if day == today.isoformat()
            else "upcoming"
        )
        groups[group].append(todo)
    return {"today": today.isoformat(), "groups": groups}


def render_overview(data: dict[str, Any]) -> str:
    lines = [f"已有待办（{data['today']}）："]
    for key, label in GROUP_LABELS.items():
        lines.append(label + "：")
        lines.extend(
            f"• {t['title']} — {t['scheduled_date'] or '未安排'} [待办 {t['id']}]"
            for t in data["groups"][key]
        )
        if not data["groups"][key]:
            lines.append("无")
    return "\n".join(lines)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: Literal[
        "update_todo",
        "complete_todo",
        "accept_suggestion",
        "update_memory",
        "delete_memory",
        "reprocess_memory",
        "accept_roadmap_node",
        "accept_roadmap_nodes",
        "complete_roadmap_node",
        "continue_learning",
    ]
    arguments: dict[str, Any]


class TodoTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    todo_id: str


class TodoUpdate(TodoTarget):
    title: str = Field(default="", max_length=200)
    date_text: str | None = None


class DayPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    suggestions: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        max_length=5
    )


class SuggestionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suggestion_id: str


def prepare_accept(
    arguments: dict[str, Any],
    suggestions: list[dict[str, Any]],
    content: str | None,
) -> str:
    try:
        target = SuggestionTarget.model_validate(arguments)
    except ValidationError:
        raise Clarification("请指定有效的建议标识，本次未新增。") from None
    if content is not None:
        text = re.sub(r"^(?:请|麻烦)?(?:帮我)?", "", content).strip().rstrip("。！!")
        if re.search(r"不要|不用|别|如果|假如|[?？]|吗|[“”\"「」]", text):
            raise Clarification("请明确要求加入哪一项建议，本次未新增。")
        if text in ("加进去", "加入待办"):
            # An already accepted suggestion remains a possible conversational
            # referent; its write state cannot establish the user's new intent.
            if len(suggestions) != 1:
                raise Clarification("请指定一项建议的标题或标识，本次未新增。")
            selected = suggestions[0]
        else:
            match = re.fullmatch(r"(?:把|将)(.+?)(?:加入待办|加进去)", text)
            if not match:
                match = re.fullmatch(r"加入建议\s*(.+)", text)
            if not match:
                raise Clarification("请说“把建议标题加入待办”，本次未新增。")
            selected = unique_target(match[1], suggestions)
        if selected["id"] != target.suggestion_id:
            raise Clarification("请重新指定建议，模型目标与指令不一致，本次未新增。")
    if not any(s["id"] == target.suggestion_id for s in suggestions):
        raise Clarification("请指定当前会话中的建议，本次未新增。")
    return target.suggestion_id


def prepare_change(tool: str, arguments: dict[str, Any], today: date) -> dict[str, Any]:
    try:
        if tool == "complete_todo":
            target = TodoTarget.model_validate(arguments)
            return {"id": target.todo_id, "status": "completed"}
        update = TodoUpdate.model_validate(arguments)
        change: dict[str, Any] = {"id": update.todo_id}
        if "title" in update.model_fields_set:
            if not update.title:
                raise ValueError("empty title")
            change["title"] = update.title
        if "date_text" in update.model_fields_set:
            change["scheduled_date"] = (
                date_from_text(update.date_text, today) if update.date_text else None
            )
        if len(change) == 1:
            raise ValueError("empty update")
        return change
    except (ValidationError, ValueError):
        raise Clarification("请提供有效的待办标识、标题或日期，本次未修改。") from None


def unique_target(reference: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    reference = re.sub(r"^(?:待办|建议)\s*", "", reference.strip())
    matches = [r for r in records if reference in (r["id"], r["title"])]
    if len(matches) != 1:
        raise Clarification("请使用列表中的完整标识指定一项，当前目标不明确，未修改。")
    return matches[0]


def authorize_change(
    tool: str,
    arguments: dict[str, Any],
    content: str,
    todos: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    text = re.sub(r"^(?:请|麻烦)?(?:帮我)?", "", content).strip().rstrip("。！!")
    if re.search(r"不要|不用|别|如果|假如|建议|[?？]|吗|[“”\"「」]", text):
        raise Clarification("请明确修改或完成的指令，本次未修改待办。")
    expected: dict[str, Any]
    if tool == "complete_todo":
        match = re.fullmatch(r"(?:把|将)(.+?)(?:标记为已完成|标记完成|标为完成)", text)
        if not match:
            match = re.fullmatch(r"(?:完成|标记完成)\s*(.+)", text)
        if not match:
            raise Clarification("请说“把待办名称标记完成”，或在列表中操作。")
        target = unique_target(match[1], todos)
        expected = {"todo_id": target["id"]}
    else:
        match = re.fullmatch(
            r"(?:把|将)(.+?)(?:的)?(改到|改期到|日期改为|标题改为|改名为)(.+)", text
        )
        if not match:
            raise Clarification(
                "请说“把待办名称改到明天”或“把待办名称标题改为新标题”。"
            )
        target = unique_target(match[1], todos)
        expected = {"todo_id": target["id"]}
        if match[2] in ("标题改为", "改名为"):
            expected["title"] = match[3].strip()
        else:
            expected["date_text"] = (
                None if match[3].strip() == "未安排" else match[3].strip()
            )
    # Independently derive intent from the user, then compare the model proposal.
    desired = prepare_change(tool, expected, today)
    if desired != prepare_change(tool, arguments, today):
        raise Clarification("请重新明确目标和修改内容，模型操作与指令不一致，未修改。")
    return desired


class TodoProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    date_text: str | None


class CreateTodos(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[TodoProposal] = Field(min_length=1, max_length=20)


DATE_TOKEN = re.compile(
    r"大后天|今天|明天|后天|昨天|前天|\d{4}-\d{1,2}-\d{1,2}|"
    r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]|"
    r"过几天|这几天|改天|最近|稍后|以后|下次|周[一二三四五六日天末]|"
    r"星期|下[周个月年]|月底|月初|节后|假期|\d+天后|"
    r"明年|今年|后年|明早|明晚|今晚|早上|上午|下午|晚上|"
    r"春节|元旦|国庆|中秋|端午|清明|劳动节|周末|农历|阴历"
)


def date_from_text(text: str, today: date) -> str:
    offsets = {"今天": 0, "明天": 1, "后天": 2}
    if text in offsets:
        return (today + timedelta(days=offsets[text])).isoformat()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        match = re.fullmatch(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]", text)
        if match:
            year, month, day = match.groups()
            result = date(int(year) if year else today.year, int(month), int(day))
            if year is None and result < today:
                raise ValueError("year unclear")
            return result.isoformat()
    except ValueError:
        pass
    raise Clarification("请提供明确的年月日（例如 2026-10-01），本次尚未保存待办。")


def prepare_todos(arguments: str, content: str, today: date) -> list[dict[str, Any]]:
    # A tool call is a proposal, never proof of write authorization.
    denied = re.search(
        r"[?？]|吗|是否|建议|如果|假如|例如|比如|解释|翻译|示例|"
        r"应该|如何|怎么|能否|举例|要不要|会不会|"
        r"不要|不用|别|无需|不想|不打算|取消|[“”\"「」]",
        content,
    )
    # Check the full user message before separating its independent task clause.
    content = mixed_todo_content(content) or content
    permitted = re.match(
        r"^(?:请|麻烦)?(?:帮我|给我)?(?:记录|记下|记一下|添加|新增|加入|安排)|"
        r"^(?:(?:今天|明天|后天)\s*)?我(?:要|准备|打算)|"
        r"^我(?:今天|明天|后天)(?:要|准备|打算)",
        content,
    )
    if denied or not permitted:
        raise Clarification("请明确说“请记录…”并列出事项和日期，本次尚未保存待办。")
    try:
        proposal = CreateTodos.model_validate_json(arguments)
    except ValidationError:
        raise Clarification(
            "请重新说明要记录的事项，模型操作参数无效，尚未保存。"
        ) from None
    source_dates = DATE_TOKEN.findall(content)
    # Reject unresolvable dates even when the model silently drops their evidence.
    for token in source_dates:
        date_from_text(token, today)
    # Missing date evidence must not silently turn unfamiliar time phrases into
    # "unscheduled". Unconsumed text is also a sign the proposal omitted an item.
    residue = content[permitted.end() :]
    for item in proposal.items:
        residue = residue.replace(item.title, "")
    residue = DATE_TOKEN.sub("", residue)
    residue = re.sub(
        r"以及|并且|然后|还要|还有|和|及|再|并|也|[\s，,。；;、：:！!]", "", residue
    )
    if residue:
        raise Clarification(
            "请明确完整的待办及日期，当前仍有未能可靠解释的内容，尚未保存。"
        )
    prepared = []
    for item in proposal.items:
        if item.title not in content:
            raise Clarification(
                "请明确待办的原始内容，本次操作无法对应来源，尚未保存。"
            )
        position = content.index(item.title)
        clause_start = (
            max(content.rfind(mark, 0, position) for mark in "，,。；;\n") + 1
        )
        clause_end = min(
            (p for mark in "，,。；;\n" if (p := content.find(mark, position)) >= 0),
            default=len(content),
        )
        clause_dates = DATE_TOKEN.findall(content[clause_start:clause_end])
        if len(set(clause_dates)) > 1:
            raise Clarification(
                "请用逗号分开不同日期的事项，并分别写明日期，本次尚未保存。"
            )
        preceding = list(DATE_TOKEN.finditer(content[:position]))
        expected = (
            clause_dates[0]
            if len(clause_dates) == 1
            else (preceding[-1].group() if preceding else None)
        )
        if expected and item.date_text != expected:
            raise Clarification(
                "请分别确认事项对应的日期，本次日期归属不一致，尚未保存。"
            )
        if item.date_text is None:
            if source_dates:
                raise Clarification(
                    "请分别说明每项待办的日期，当前日期对应不明确，尚未保存。"
                )
            day = None
        else:
            if item.date_text not in source_dates:
                raise Clarification(
                    "请明确每项待办的日期，模型日期与原文不符，尚未保存。"
                )
            day = date_from_text(item.date_text, today)
        prepared.append({"title": item.title, "scheduled_date": day})
    return prepared

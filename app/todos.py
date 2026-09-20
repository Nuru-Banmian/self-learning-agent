import re
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Clarification(Exception):
    pass


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
    r"星期|下[周个月年]|月底|月初|节后|假期|\d+天后"
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
        r"不要|不用|别|无需|不想|不打算|取消|[“”\"「」]",
        content,
    )
    permitted = re.match(
        r"^(?:请|麻烦)?(?:帮我|给我)?(?:记录|记下|记一下|添加|新增|加入|安排)|"
        r"^(?:(?:今天|明天|后天)\s*)?我(?:要|准备|打算)|"
        r"^(?:今天|明天|后天)(?![的是])",
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
    prepared = []
    for item in proposal.items:
        if item.title not in content:
            raise Clarification(
                "请明确待办的原始内容，本次操作无法对应来源，尚未保存。"
            )
        position = content.index(item.title)
        preceding = list(DATE_TOKEN.finditer(content[:position]))
        if preceding and item.date_text != preceding[-1].group():
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

"""Read explicit scheduling conditions; only the application creates proposals."""

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.model import call_model
from app.roadmaps import explicit_roadmap, unquoted_request
from app.scheduling import ScheduleRequest
from app.settings import Settings
from app.store import Store
from app.todos import Clarification


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_reference: str
    start_text: str | None
    budget_text: str | None
    availability_text: str | None
    deadline_text: str | None
    clear: bool
    unsupported: list[str] = Field(max_length=8)


def scheduling_request(content: str) -> bool:
    return bool(
        re.search(r"排期|(?:安排|规划|清空|清除|取消|设置).{0,12}日期", content)
    )


async def chat_schedule(
    store: Store,
    settings: Settings,
    run: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> ScheduleRequest | None:
    content = run["content"]
    # Existing todo commands retain precedence even when their titles contain
    # scheduling vocabulary. Their existing authorization still validates writes.
    if re.search(
        r"^\s*(?:请|麻烦)?(?:帮我|给我)?(?:记录|记下|记一条|添加待办|创建待办|完成|标记完成)|"
        r"改到|改期到|日期改为|标题改为|改名为|标记为?已?完成",
        unquoted_request(content),
    ):
        return None
    if explicit_roadmap(content):
        # A new learning request first creates an undated route. Scheduling is a
        # separate explicit request once the user can inspect its nodes.
        return None
    if not scheduling_request(content):
        return None
    text = unquoted_request(content)
    if not scheduling_request(text) or re.search(
        r"不要|不用|不需要|不必|无需|别|如果|假如|比如|例如|解释|翻译|什么意思|是否|要不要",
        text,
    ):
        raise Clarification("本轮没有明确排期授权，未生成或修改日期方案。")
    if not re.search(r"路线|节点|排期", text):
        return None
    routes = store.roadmaps()
    if not routes:
        raise Clarification("请先生成学习路线，再指定路线排期；未修改安排。")
    message = await call_model(
        settings,
        store,
        run["id"],
        [
            {
                "role": "system",
                "content": (
                    "提取用户本轮明确要求的路线排期条件，仅调用schedule_conditions。"
                    "所有非空文本字段必须是本轮原文的连续片段，不能转述或推算日期。"
                    "roadmap_reference取明确的路线标题或完整ID；这条/该路线用空串。"
                    "start_text是起始日期词（如明天、2026-10-01），未说明为null。"
                    "budget_text取完整时长原文（如30分钟、1小时30分钟、半小时），不得截断复合时长。"
                    "availability_text取每天、工作日、周末或具体星期的原文。"
                    "deadline_text取截止日原文词，没有期限为null。"
                    "clear仅在明确清空或取消节点日期时为true。缺失字段为null，不从已有路线或记忆猜测。"
                    "不支持的条件、各日不同预算、未列明日期的每周次数、含糊日期、要求仅部分节点排期，写入unsupported以便澄清。"
                    "禁止忽略限制条件，不新增待办。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"request": content, "roadmaps": routes}, ensure_ascii=False
                ),
            },
        ],
        transport,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "schedule_conditions",
                    "description": "提取待审阅排期条件",
                    "parameters": Conditions.model_json_schema(),
                },
            }
        ],
        required_tool="schedule_conditions",
    )
    calls = message.get("tool_calls") or []
    if len(calls) != 1 or calls[0]["function"]["name"] != "schedule_conditions":
        raise Clarification("未能核实排期条件，请在路线面板填写起始日期和时间预算。")
    conditions = Conditions.model_validate_json(calls[0]["function"]["arguments"])
    for value in (
        conditions.roadmap_reference,
        conditions.start_text,
        conditions.budget_text,
        conditions.availability_text,
        conditions.deadline_text,
    ):
        if value and value not in content:
            raise Clarification(
                "排期条件与本轮原文不一致，请重新指定日期和时间预算；未修改安排。"
            )
    if conditions.unsupported:
        raise Clarification(
            "请澄清排期条件："
            + "；".join(conditions.unsupported)
            + "。可在面板指定起始日期、学习日和统一的每日分钟数。"
        )
    matched = (
        [r for r in routes if conditions.roadmap_reference in (r["id"], r["title"])]
        if conditions.roadmap_reference
        else routes
    )
    if len(matched) != 1:
        raise Clarification(
            "目标路线不明确，请使用路线完整标题或在面板选择；未修改安排。"
        )
    route = matched[0]
    if conditions.clear:
        if not re.search(r"(?:清空|清除|取消).{0,12}(?:日期|排期)", text):
            raise Clarification("请明确是否清空日期；未修改安排。")
        return ScheduleRequest(
            roadmap_id=route["id"], expected_version=route["version"], clear=True
        )
    budget = conditions.budget_text or ""
    duration = re.sub(
        r"^(?:每天|每日|每个学习日)?(?:可(?:以)?|能|有|最多|只有)?"
        r"(?:用|学习|投入)?\s*",
        "",
        budget,
    ).strip()
    match = re.fullmatch(
        r"(?:(?P<hours>\d+(?:\.\d+)?)\s*(?:个)?小时"
        r"(?:(?P<extra>\d+)\s*分钟|(?P<half>半))?|"
        r"(?P<minutes>\d+)\s*分钟|(?P<half_hour>半小时))",
        duration,
    )
    minutes = (
        float(match["hours"] or 0) * 60
        + int(match["extra"] or match["minutes"] or 0)
        + (30 if match["half"] or match["half_hour"] else 0)
        if match
        else 0
    )
    availability = conditions.availability_text or ""
    if availability in ("每天", "每日"):
        weekdays = list(range(7))
    elif availability in ("工作日", "每个工作日"):
        weekdays = list(range(5))
    elif availability in ("周末", "每个周末"):
        weekdays = [5, 6]
    else:
        days = re.findall(r"(?:周|星期)([一二三四五六日天])", availability)
        weekdays = sorted({"一二三四五六日".index(d.replace("天", "日")) for d in days})
        if not re.fullmatch(
            r"(?:每)?(?:周|星期)[一二三四五六日天]"
            r"(?:[、,，和及 ]+(?:周|星期)[一二三四五六日天])*",
            availability,
        ):
            weekdays = []  # Never silently ignore an unparsed day or range.
    if (
        not conditions.start_text
        or not minutes
        or not weekdays
        or int(minutes) != minutes
    ):
        raise Clarification(
            "请提供起始日期、每天可用分钟数及具体学习日（每天/工作日/周末或列出星期）；未修改安排。"
        )
    return ScheduleRequest(
        roadmap_id=route["id"],
        expected_version=route["version"],
        start_text=conditions.start_text,
        daily_minutes=int(minutes),
        weekdays=weekdays,
        deadline_text=conditions.deadline_text,
    )

"""Date proposals and atomic confirmation; models never choose calendar dates."""

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app import roadmap_store
from app.todos import Clarification, date_from_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS roadmap_dates (
    node_id TEXT PRIMARY KEY REFERENCES roadmap_nodes, scheduled_date TEXT);
CREATE TABLE IF NOT EXISTS schedule_proposals (
    id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs, status TEXT NOT NULL,
    content TEXT NOT NULL);
"""


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    start_text: str | None = None
    daily_minutes: int | None = Field(default=None, ge=1, le=1440)
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=lambda: list(range(7)), min_length=1, max_length=7
    )
    deadline_text: str | None = None
    clear: bool = False


class ScheduleConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)


def snapshot(route: dict[str, Any]) -> dict[str, Any]:
    # Includes live todo dates/titles, which can change without a route version bump.
    return {"version": route["version"], "nodes": route["nodes"]}


def preview(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    timezone = args["timezone"]
    request = ScheduleRequest.model_validate(args["request"])
    route = roadmap_store.read(db, request.roadmap_id)
    if not route or route["version"] != request.expected_version:
        raise Clarification("路线已变化或不存在，请刷新后重新预览；未修改安排。")
    nodes = [n for n in route["nodes"] if n["status"] != "completed"]
    if not nodes:
        raise Clarification("路线节点均已完成，无需排期；完成记录保持原状。")
    entries = []
    basis: dict[str, Any] = {
        "timezone": timezone,
        "received_at": run["received_at"],
        "clear": request.clear,
    }
    if not request.clear:
        if not request.start_text or request.daily_minutes is None:
            raise Clarification(
                "请提供排期起始日期、每天可用学习分钟数及可学习的星期；未修改安排。"
            )
        today = (
            datetime.fromisoformat(run["received_at"])
            .astimezone(ZoneInfo(timezone))
            .date()
        )
        start = date.fromisoformat(date_from_text(request.start_text, today))
        deadline = (
            date.fromisoformat(date_from_text(request.deadline_text, today))
            if request.deadline_text
            else None
        )
        if start < today:
            raise Clarification("起始日期早于原请求当天，请提供未来日期；未修改安排。")
        day, used = start, 0
        basis.update(
            start_date=start.isoformat(),
            daily_minutes=request.daily_minutes,
            weekdays=sorted(set(request.weekdays)),
            deadline=deadline.isoformat() if deadline else None,
        )
        for node in nodes:
            remaining = node["estimated_minutes"]
            allocations = []
            # Long nodes span days; the due date is the final study day.
            while remaining:
                if (
                    day.weekday() not in request.weekdays
                    or used == request.daily_minutes
                ):
                    day += timedelta(days=1)
                    used = 0
                    continue
                if deadline and day > deadline:
                    raise Clarification(
                        "期限与学习时间预算冲突，无法按节点耗时完成。请延长期限或增加可用时间；未修改安排。"
                    )
                minutes = min(remaining, request.daily_minutes - used)
                allocations.append({"date": day.isoformat(), "minutes": minutes})
                used += minutes
                remaining -= minutes
            entries.append(entry(node, day.isoformat(), allocations))
    else:
        entries = [entry(n, None, []) for n in nodes]
    proposal = {
        "id": str(uuid4()),
        "roadmap_id": route["id"],
        "title": route["title"],
        "basis": basis,
        "entries": entries,
        "snapshot": snapshot(route),
    }
    db.execute(
        "INSERT INTO schedule_proposals VALUES(?,?,?,'pending',?)",
        (
            proposal["id"],
            route["id"],
            run["id"],
            json.dumps(proposal, ensure_ascii=False),
        ),
    )
    return proposal | {"status": "pending"}, render(
        proposal, "排期预览，确认前不修改安排"
    )


def entry(
    node: dict[str, Any], day: str | None, allocations: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "node_id": node["id"],
        "position": node["position"],
        "title": node["todo_title"],
        "todo_id": node["todo_id"],
        "previous_date": node["scheduled_date"],
        "scheduled_date": day,
        "allocations": allocations,
    }


def confirm(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    target = ScheduleConfirmation.model_validate(args)
    row = db.execute(
        "SELECT * FROM schedule_proposals WHERE id=? AND roadmap_id=?",
        (target.proposal_id, target.roadmap_id),
    ).fetchone()
    if not row:
        raise Clarification("排期方案不存在或不属于此路线，请重新预览；未修改安排。")
    proposal = json.loads(row["content"]) | {"status": row["status"]}
    if row["status"] == "applied":
        return proposal, "此排期方案已确认，本次未重复修改。"
    route = roadmap_store.read(db, target.roadmap_id)
    assert route is not None
    if row["status"] == "stale" or snapshot(route) != proposal["snapshot"]:
        proposal["status"] = "stale"
        db.execute(
            "UPDATE schedule_proposals SET status='stale' WHERE id=?",
            (target.proposal_id,),
        )
        old_nodes = {n["id"]: n for n in proposal["snapshot"]["nodes"]}
        changes = [
            f"节点 {n['position']}：{n['todo_title']}，"
            f"当前日期 {n['scheduled_date'] or '未安排'}，状态 {n['status']}，"
            f"关联待办 {n['todo_id'] or '未加入'}"
            for n in route["nodes"]
            if old_nodes.get(n["id"]) != n
        ]
        return (
            proposal,
            "方案已过期，相关状态或版本已变化，未修改安排。请重新预览并确认。\n"
            + "\n".join(changes),
        )
    for item in proposal["entries"]:
        db.execute(
            "INSERT INTO roadmap_dates VALUES(?,?) ON CONFLICT(node_id) "
            "DO UPDATE SET scheduled_date=excluded.scheduled_date",
            (item["node_id"], item["scheduled_date"]),
        )
        if item["todo_id"]:
            db.execute(
                "UPDATE todos SET scheduled_date=?,updated_at=? WHERE id=?",
                (item["scheduled_date"], run["received_at"], item["todo_id"]),
            )
    db.execute("UPDATE roadmaps SET version=version+1 WHERE id=?", (target.roadmap_id,))
    db.execute(
        "UPDATE schedule_proposals SET status='applied' WHERE id=?",
        (target.proposal_id,),
    )
    return proposal | {"status": "applied"}, render(
        proposal, "排期已确认，已同步关联待办"
    )


def render(proposal: dict[str, Any], heading: str) -> str:
    basis = proposal["basis"]
    text = f"{heading}：{proposal['title']}\n路线 {proposal['roadmap_id']}\n"
    if basis["clear"]:
        text += "依据：明确清空未完成节点日期。\n"
    else:
        weekdays = "、".join("一二三四五六日"[i] for i in basis["weekdays"])
        text += (
            f"依据：从 {basis['start_date']} 起，周{weekdays}，"
            f"每天 {basis['daily_minutes']} 分钟；"
            f"期限 {basis['deadline'] or '未指定'}；时区 {basis['timezone']}。"
            "日期为节点预计完成日，可跨天学习。\n"
        )
    for item in proposal["entries"]:
        text += (
            f"{item['position']}. {item['title']}："
            f"{item['previous_date'] or '未安排'} → "
            f"{item['scheduled_date'] or '未安排'}；"
            + ("同步关联待办" if item["todo_id"] else "仅保存节点日期，不创建待办")
            + "\n"
        )
        text += (
            "、".join(
                f"{a['date']} 学习 {a['minutes']} 分钟" for a in item["allocations"]
            )
            + "\n"
        )
    return text + "已完成节点和完成记录保持原状。请在路线面板查看并确认。"

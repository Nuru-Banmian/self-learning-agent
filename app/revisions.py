"""Reviewable route differences committed with the existing request transaction."""

import json
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app import roadmap_store
from app.roadmap_presentation import render_nodes, validate_summary
from app.scheduling import SchedulingRemoved
from app.todos import Clarification

SCHEMA = """
CREATE TABLE IF NOT EXISTS revision_proposals (
    id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs, status TEXT NOT NULL,
    content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roadmap_history (
    node_id TEXT PRIMARY KEY REFERENCES roadmap_nodes);
"""


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roadmap_id: str = Field(min_length=1, max_length=100)
    proposal_id: str = Field(min_length=1, max_length=100)
    sync_todo_ids: list[str] = Field(default_factory=list, max_length=8)


def snapshot(route: dict[str, Any]) -> dict[str, Any]:
    return {k: route[k] for k in ("version", "nodes", "history_nodes")}


def same_snapshot(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    # Older pending proposals predate the separate read-only planned_date field.
    def comparable(value: dict[str, Any]) -> dict[str, Any]:
        return value | {
            key: [
                {k: v for k, v in node.items() if k != "planned_date"}
                for node in value[key]
            ]
            for key in ("nodes", "history_nodes")
        }

    return comparable(current) == comparable(previous)


def unchanged_date(
    value: str | None, before: dict[str, Any] | None, *, explicit: bool = False
) -> bool:
    if before is None:
        return value is None
    if explicit and value is None:
        return before["scheduled_date"] is None and before.get("planned_date") is None
    # Old callers echo the displayed actual todo date; new callers may echo the
    # independent historical plan. Neither compatibility spelling writes a date.
    return value == before["scheduled_date"] or (
        "planned_date" in before and value == before["planned_date"]
    )


def date_changes_blocked(proposal: dict[str, Any]) -> bool:
    if proposal.get("status") == "applied":
        return False
    old = {node["id"]: node for node in proposal["snapshot"]["nodes"]}
    return any(
        not unchanged_date(node.get("scheduled_date"), old.get(node["id"]))
        for node in proposal["nodes"]
    ) or any(
        entry["todo_before"]
        and entry["todo_after"]
        and entry["todo_before"]["scheduled_date"]
        != entry["todo_after"]["scheduled_date"]
        for entry in proposal["entries"]
    )


def reject_date_changes() -> None:
    raise SchedulingRemoved(
        "路线排期已取消，此调整包含日期变化，整份方案未应用。"
        "请重新生成不含日期变化的内容调整方案；单条待办可手动编辑日期。"
    )


def preview(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    # Imported here because route content models also serve the model adapter.
    from app.revision_chat import Revision

    request = Revision.model_validate(args["request"])
    route = roadmap_store.read(db, request.roadmap_id)
    if not route or route["version"] != request.expected_version:
        raise Clarification("路线已变化，请刷新后重新生成调整方案；未修改。")
    if args.get("snapshot") and not same_snapshot(args["snapshot"], snapshot(route)):
        raise Clarification("生成方案期间路线已变化，请重新生成；未修改。")
    sources = route["sources"] + args.get("sources", [])
    old = {n["id"]: n for n in route["nodes"]}
    nodes: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, requested in enumerate(request.nodes, 1):
        node = requested.model_dump(exclude={"node_id"})
        identity = requested.node_id or str(uuid4())
        if identity in seen or (requested.node_id and identity not in old):
            raise Clarification("节点重复或不属于当前路线，请重新生成调整方案。")
        seen.add(identity)
        if not set(node["source_ids"]) <= {s["id"] for s in sources}:
            raise Clarification("调整引用了未取得的资料，未保存方案。")
        before = old.get(identity)
        if "scheduled_date" in requested.model_fields_set and not unchanged_date(
            requested.scheduled_date, before, explicit=True
        ):
            reject_date_changes()
        # Dates remain stored independently. The proposal merely describes the
        # preserved public date, so completed-node checks retain their meaning.
        node["scheduled_date"] = before["scheduled_date"] if before else None
        for display, full in (
            ("display_title", "todo_title"),
            ("display_goal", "goal"),
        ):
            if before and before[full] == node[full] and not node[display]:
                node[display] = before.get(display, "")
        if (
            before
            and before["status"] == "completed"
            and any(before.get(k, "") != v for k, v in node.items())
        ):
            raise Clarification("已完成节点内容和日期不能改写；新学习目标请新增节点。")
        after = node | {"id": identity, "position": position}
        nodes.append(after)
        if before is None or any(before.get(k, "") != v for k, v in after.items()):
            todo_before = before["todo"] if before else None
            todo_after = dict(todo_before) if todo_before else None
            if todo_after and before:
                # Preserve independent todo edits unless this field is changed.
                if before["todo_title"] != after["todo_title"]:
                    todo_after["title"] = after["todo_title"]
            entries.append(
                {
                    "kind": "modify" if before else "add",
                    "before": before,
                    "after": after,
                    "todo_before": todo_before,
                    "todo_after": todo_after,
                }
            )
    for identity, before in old.items():
        if identity not in seen:
            entries.append(
                {
                    "kind": "archive",
                    "before": before,
                    "after": None,
                    "todo_before": before["todo"],
                    "todo_after": before["todo"],
                }
            )
    if (
        not entries
        and request.title == route["title"]
        and request.goal == route["goal"]
    ):
        raise Clarification(
            "方案没有实际变化，未保存调整方案；请重新描述具体需要修改的内容。"
        )
    gaps = list(args.get("gaps", []))
    # Replacing or removing an exercise can remove setup used by later nodes.
    # This is an uncertainty warning, not a semantic dependency validator.
    if any(
        e["before"]
        and (
            e["kind"] == "archive" or e["before"]["exercise"] != e["after"]["exercise"]
        )
        and any(
            n["id"] in old
            and old[n["id"]]["position"] > e["before"]["position"]
            and n["exercise"] == old[n["id"]]["exercise"]
            for n in nodes
        )
        for e in entries
    ):
        gaps.append(
            "前序练习已修改或移出，保留的后续练习的先修依赖尚未核验。"
            "请检查变量初始化、文件和环境准备；若缺失，请补充调整方案后再确认。"
        )
    proposal = {
        "id": str(uuid4()),
        "roadmap_id": route["id"],
        "before": {"title": route["title"], "goal": route["goal"]},
        "after": {"title": request.title, "goal": request.goal},
        "nodes": nodes,
        "entries": entries,
        "snapshot": snapshot(route),
        "sources": sources,
        "gaps": gaps,
        "sync_todo_ids": sorted(
            {
                e["todo_before"]["id"]
                for e in entries
                if e["todo_before"]
                and e["kind"] == "modify"
                and e["before"]["status"] != "completed"
            }
        ),
    }
    db.execute(
        "INSERT INTO revision_proposals VALUES(?,?,?,'pending',?)",
        (
            proposal["id"],
            route["id"],
            run["id"],
            json.dumps(proposal, ensure_ascii=False),
        ),
    )
    return proposal | {"status": "pending"}, render(
        proposal, "调整预览，当前路线和待办未修改"
    )


def confirm(
    db: sqlite3.Connection, run: sqlite3.Row, args: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    target = Confirmation.model_validate(args)
    row = db.execute(
        "SELECT * FROM revision_proposals WHERE id=? AND roadmap_id=?",
        (target.proposal_id, target.roadmap_id),
    ).fetchone()
    if not row:
        raise Clarification("调整方案不存在或不属于此路线；未修改。")
    proposal = json.loads(row["content"]) | {"status": row["status"]}
    if row["status"] == "applied":
        return proposal, "此调整方案已应用，本次未重复修改。"
    if date_changes_blocked(proposal):
        reject_date_changes()
    route = roadmap_store.read(db, target.roadmap_id)
    assert route is not None
    if row["status"] == "stale" or not same_snapshot(
        snapshot(route), proposal["snapshot"]
    ):
        db.execute(
            "UPDATE revision_proposals SET status='stale' WHERE id=?",
            (target.proposal_id,),
        )
        return proposal | {"status": "stale"}, (
            "方案冲突：路线、关联待办或完成进度已变化，未应用任何调整。"
            "请查看当前路线并重新生成有效差异。"
        )
    if set(target.sync_todo_ids) != set(proposal["sync_todo_ids"]):
        return proposal, (
            "尚未明确同意方案中全部待办同步范围，整份方案保持待确认；"
            "当前路线和待办均未修改。只调整未加入节点时，请另行生成方案。"
        )
    # Free all positive positions before assigning the proposed order. Historical
    # rows keep unique negative positions, their identity and their todo links.
    rows = db.execute(
        "SELECT id FROM roadmap_nodes WHERE roadmap_id=? ORDER BY position",
        (route["id"],),
    ).fetchall()
    floor = min([n["position"] for n in route["history_nodes"]] + [0]) - len(rows) - 1
    for i, node in enumerate(rows):
        db.execute(
            "UPDATE roadmap_nodes SET position=? WHERE id=?", (floor - i, node["id"])
        )
    old = {n["id"]: n for n in route["nodes"]}
    for node in proposal["nodes"]:
        content = {
            k: v
            for k, v in node.items()
            if k not in ("id", "position", "scheduled_date")
        }
        if node["id"] in old:
            db.execute(
                "UPDATE roadmap_nodes SET position=?,content=? WHERE id=?",
                (node["position"], json.dumps(content, ensure_ascii=False), node["id"]),
            )
        else:
            db.execute(
                "INSERT INTO roadmap_nodes VALUES(?,?,?,?,NULL)",
                (
                    node["id"],
                    route["id"],
                    node["position"],
                    json.dumps(content, ensure_ascii=False),
                ),
            )
    for entry in proposal["entries"]:
        if entry["kind"] == "archive":
            db.execute(
                "INSERT INTO roadmap_history VALUES(?)", (entry["before"]["id"],)
            )
        if entry["todo_before"] != entry["todo_after"]:
            todo = entry["todo_after"]
            db.execute(
                "UPDATE todos SET title=?,updated_at=? WHERE id=?",
                (todo["title"], run["received_at"], todo["id"]),
            )
    content = json.loads(
        db.execute(
            "SELECT content FROM roadmaps WHERE id=?", (route["id"],)
        ).fetchone()[0]
    )
    if content["title"] != proposal["after"]["title"]:
        content.pop("display_title", None)
    content.update(proposal["after"], sources=proposal["sources"])
    content["gaps"] = list(dict.fromkeys(content["gaps"] + proposal["gaps"]))
    if content["gaps"]:
        content["status"] = "partial"
    db.execute(
        "UPDATE roadmaps SET content=?,version=version+1 WHERE id=?",
        (json.dumps(content, ensure_ascii=False), route["id"]),
    )
    db.execute(
        "UPDATE revision_proposals SET status='applied' WHERE id=?",
        (target.proposal_id,),
    )
    return proposal | {"status": "applied"}, render(
        proposal | {"status": "applied"}, "调整已应用，已同步明确确认的待办"
    )


def render(proposal: dict[str, Any], heading: str) -> str:
    text = heading + "\n" + render_nodes(proposal["nodes"])
    if proposal.get("status") == "applied":
        text += "\n新候选尚未加入待办，具体变更可展开查看。"
    else:
        text += "\n展开调整方案审阅具体变更与待办同步范围，再明确确认。"
    if proposal["gaps"]:
        text += "\n部分资料或先修条件有限制，请展开方案查看。"
    return validate_summary(text)

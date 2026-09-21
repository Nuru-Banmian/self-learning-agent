"""Roadmap persistence inside the existing request commit transaction."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.todos import Clarification

SCHEMA = """
CREATE TABLE IF NOT EXISTS roadmaps (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs,
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
    content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roadmap_nodes (
    id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps,
    position INTEGER NOT NULL, content TEXT NOT NULL,
    todo_id TEXT UNIQUE REFERENCES todos,
    UNIQUE(roadmap_id, position));
CREATE TABLE IF NOT EXISTS roadmap_completions (
    node_id TEXT PRIMARY KEY REFERENCES roadmap_nodes,
    fact TEXT NOT NULL);
"""


def read(db: sqlite3.Connection, roadmap_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone()
    if row is None:
        return None
    nodes = []
    for node in db.execute(
        "SELECT n.*,t.title,t.scheduled_date,t.status,c.fact FROM roadmap_nodes n "
        "LEFT JOIN todos t ON t.id=n.todo_id "
        "LEFT JOIN roadmap_completions c ON c.node_id=n.id "
        "WHERE roadmap_id=? ORDER BY position",
        (roadmap_id,),
    ):
        nodes.append(
            json.loads(node["content"])
            | {
                "id": node["id"],
                "position": node["position"],
                "todo_id": node["todo_id"],
                "status": node["status"]
                if node["todo_id"]
                else ("completed" if node["fact"] else "pending"),
                "completion": json.loads(node["fact"]) if node["fact"] else None,
                "todo": {
                    "id": node["todo_id"],
                    "title": node["title"],
                    "scheduled_date": node["scheduled_date"],
                    "status": node["status"],
                }
                if node["todo_id"]
                else None,
            }
        )
    content: dict[str, Any] = json.loads(row["content"])
    completed = sum(n["status"] == "completed" for n in nodes)
    return content | {
        "id": row["id"],
        "run_id": row["run_id"],
        "version": row["version"],
        "created_at": row["created_at"],
        "nodes": nodes,
        "progress": {
            "completed": completed,
            "total": len(nodes),
            "remaining": len(nodes) - completed,
        },
    }


def complete(
    db: sqlite3.Connection, run: sqlite3.Row, node_id: str, operation: str
) -> tuple[dict[str, Any], bool]:
    """Keep the first completion fact; caller owns the request transaction."""
    row = db.execute(
        "SELECT roadmap_id FROM roadmap_nodes WHERE id=?", (node_id,)
    ).fetchone()
    assert row is not None
    route = read(db, row["roadmap_id"])
    assert route is not None
    node = next(n for n in route["nodes"] if n["id"] == node_id)
    created = node["completion"] is None
    if created:
        if node["todo_id"] and node["todo"]["status"] != "completed":
            db.execute(
                "UPDATE todos SET status='completed',updated_at=? WHERE id=?",
                (run["received_at"], node["todo_id"]),
            )
        fact = {
            "completed_at": datetime.now(UTC).isoformat(),
            "run_id": run["id"],
            "message_id": run["message_id"],
            "session_id": run["session_id"],
            "operation": operation,
            "content": run["content"],
            "action": json.loads(run["action"]) if run["action"] else None,
            "node": {k: v for k, v in node.items() if k != "completion"},
            "sources": route["sources"],
        }
        db.execute(
            "INSERT INTO roadmap_completions VALUES(?,?)",
            (node_id, json.dumps(fact, ensure_ascii=False)),
        )
        db.execute("UPDATE roadmaps SET version=version+1 WHERE id=?", (route["id"],))
    current = read(db, route["id"])
    assert current is not None
    return current, created


def mark_mastered(
    db: sqlite3.Connection, run: sqlite3.Row, target: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    route = read(db, target["roadmap_id"])
    node = (
        next((n for n in route["nodes"] if n["id"] == target["node_id"]), None)
        if route
        else None
    )
    if not route or not node:
        raise Clarification("节点不存在或不属于此路线，请刷新后重新选择；未修改。")
    if not node["completion"] and route["version"] != target["expected_version"]:
        raise Clarification("路线已变化，请刷新后重新确认已掌握的节点；未修改。")
    return complete(db, run, node["id"], "mastered")


def save(
    db: sqlite3.Connection, run: sqlite3.Row, content: dict[str, Any]
) -> dict[str, Any]:
    roadmap_id = str(uuid4())
    db.execute(
        "INSERT INTO roadmaps VALUES(?,?,1,?,?)",
        (
            roadmap_id,
            run["id"],
            run["received_at"],
            json.dumps(
                {k: v for k, v in content.items() if k != "nodes"}, ensure_ascii=False
            ),
        ),
    )
    for position, node in enumerate(content["nodes"], 1):
        db.execute(
            "INSERT INTO roadmap_nodes VALUES(?,?,?,?,NULL)",
            (
                str(uuid4()),
                roadmap_id,
                position,
                json.dumps(node, ensure_ascii=False),
            ),
        )
    result = read(db, roadmap_id)
    assert result is not None
    return result


def accept_node(
    db: sqlite3.Connection,
    run: sqlite3.Row,
    target: dict[str, Any],
) -> tuple[dict[str, Any], str | None, bool]:
    route, results = accept_nodes(db, run, target | {"node_ids": [target["node_id"]]})
    result = results[0]
    return route, result["todo_id"], result["status"] == "created"


def accept_nodes(
    db: sqlite3.Connection,
    run: sqlite3.Row,
    target: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Caller owns BEGIN IMMEDIATE: selection, writes and run evidence commit together.
    route = read(db, target["roadmap_id"])
    if route is None:
        raise Clarification("路线不存在，请刷新后重新选择；未新增待办。")
    selected = set(target["node_ids"])
    if not selected <= {n["id"] for n in route["nodes"]}:
        raise Clarification("节点不属于此路线，请重新选择；未新增待办。")
    nodes = [n for n in route["nodes"] if n["id"] in selected]
    pending = [n for n in nodes if not n["todo_id"] and n.get("status") != "completed"]
    if pending and route["version"] != target["expected_version"]:
        raise Clarification("路线已变化，请刷新后重新选择；未新增待办。")
    results = []
    for node in nodes:
        todo_id = node["todo_id"]
        state = "completed" if node["status"] == "completed" else "already_added"
        if node in pending:
            todo_id = str(uuid4())
            db.execute(
                "INSERT INTO todos VALUES(?,?,NULL,'pending',?,?,?)",
                (
                    todo_id,
                    node["todo_title"],
                    run["message_id"],
                    run["received_at"],
                    run["received_at"],
                ),
            )
            db.execute(
                "UPDATE roadmap_nodes SET todo_id=? WHERE id=?", (todo_id, node["id"])
            )
            state = "created"
        results.append({"node_id": node["id"], "todo_id": todo_id, "status": state})
    if pending:
        db.execute("UPDATE roadmaps SET version=version+1 WHERE id=?", (route["id"],))
    current = read(db, route["id"])
    assert current is not None
    return current, results

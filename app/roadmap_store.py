"""Roadmap persistence inside the existing request commit transaction."""

import json
import sqlite3
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
"""


def read(db: sqlite3.Connection, roadmap_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone()
    if row is None:
        return None
    nodes = []
    for node in db.execute(
        "SELECT n.*,t.title,t.scheduled_date,t.status FROM roadmap_nodes n "
        "LEFT JOIN todos t ON t.id=n.todo_id WHERE roadmap_id=? ORDER BY position",
        (roadmap_id,),
    ):
        nodes.append(
            json.loads(node["content"])
            | {
                "id": node["id"],
                "position": node["position"],
                "todo_id": node["todo_id"],
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
    return content | {
        "id": row["id"],
        "run_id": row["run_id"],
        "version": row["version"],
        "created_at": row["created_at"],
        "nodes": nodes,
    }


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
) -> tuple[dict[str, Any], str, bool]:
    route = read(db, target["roadmap_id"])
    if route is None:
        raise Clarification("路线不存在，请刷新后重新选择；未新增待办。")
    node = next((n for n in route["nodes"] if n["id"] == target["node_id"]), None)
    if node is None:
        raise Clarification("节点不属于此路线，请重新选择；未新增待办。")
    if node["todo_id"]:
        return route, node["todo_id"], False
    if route["version"] != target["expected_version"]:
        raise Clarification("路线已变化，请刷新后重新选择；未新增待办。")
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
    db.execute("UPDATE roadmap_nodes SET todo_id=? WHERE id=?", (todo_id, node["id"]))
    db.execute("UPDATE roadmaps SET version=version+1 WHERE id=?", (route["id"],))
    current = read(db, route["id"])
    assert current is not None
    return current, todo_id, True

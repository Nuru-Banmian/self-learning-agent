"""Durable, source-grounded learning clarification and generation ownership."""

import json
import re
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.todos import Clarification

SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_requests (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL, content TEXT NOT NULL,
    last_run_id TEXT NOT NULL REFERENCES runs, roadmap_id TEXT REFERENCES roadmaps);
CREATE TABLE IF NOT EXISTS learning_request_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs,
    request_id TEXT NOT NULL REFERENCES learning_requests,
    memory_revision INTEGER NOT NULL);
"""

QUESTIONS = {
    "goal": "希望学完后能做什么？",
    "background": "目前有哪些相关基础？",
    "time_budget": "每次或每周可以投入多少时间？",
}


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str | None
    topic: str = Field(min_length=1, max_length=100)
    goal: str | None = Field(
        max_length=400,
        description="用户希望达成的用途或能力原话；仅说想学某主题不是目标，必须为null。",
    )
    background: str | None = Field(max_length=400)
    time_budget: str | None = Field(max_length=400)


def read_all(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        json.loads(r["content"])
        | {"id": r["id"], "version": r["version"], "roadmap_id": r["roadmap_id"]}
        for r in db.execute("SELECT * FROM learning_requests ORDER BY rowid")
    ]


def save(
    db: sqlite3.Connection,
    run: dict[str, Any],
    intake: Intake,
    snapshot: list[dict[str, Any]],
    loaded: list[dict[str, Any]],
    revision: int,
    *,
    new_intent: bool,
    selected_id: str | None,
) -> dict[str, Any]:
    target = next((r for r in snapshot if r["id"] == intake.request_id), None)
    duplicates = [
        r
        for r in read_all(db)
        if any(
            m["id"] == run["message_id"]
            or (
                m["content"] == run["content"]
                and (r["questions"] or len(r["messages"]) > 1)
            )
            for m in r["messages"]
        )
    ]
    if not target and not selected_id and duplicates:
        if len(duplicates) != 1:
            raise Clarification("该回复对应多个目标，请在面板选择学习需求。")
        target = next((r for r in snapshot if r["id"] == duplicates[0]["id"]), None)
        if target is None:
            raise Clarification("此学习需求已提交，请刷新后查看。")
    if selected_id and (not target or target["id"] != selected_id):
        raise Clarification("请续答所选学习需求，未生成路线。")
    if intake.request_id and not target:
        raise Clarification("待续学习需求不存在，请重新选择。")
    pending = [r for r in snapshot if not r["roadmap_id"]]
    if not new_intent and not target:
        raise Clarification("请选择要补充的学习需求，未生成路线。")
    if target and not selected_id and len(pending) > 1:
        matches = [
            r
            for r in pending
            if r["topic"] in run["content"] or r["id"] in run["content"]
        ]
        if len(matches) != 1 or matches[0]["id"] != target["id"]:
            raise Clarification("有多个待续学习目标，请在面板选择目标后补充。")
    messages = list(target["messages"]) if target else []
    if not any(m["content"] == run["content"] for m in messages):
        messages.append({"id": run["message_id"], "content": run["content"]})
    if len(messages) > 20 or sum(len(m["content"]) for m in messages) > 16000:
        raise Clarification("此需求补充过长，请重新概括学习目标。")
    evidence = [m["content"] for m in messages] + [m["content"] for m in loaded]
    known = {key: getattr(intake, key) for key in QUESTIONS}
    for value in [intake.topic, *known.values()]:
        if value and not any(value in source for source in evidence):
            raise ValueError("学习需求含无来源的信息，请重试。")
    if known["goal"] and re.fullmatch(
        r"(?:我)?(?:想|要|希望|打算)?(?:学习|学会|学|了解|掌握)?\s*"
        + re.escape(intake.topic)
        + r"[。！!]?",
        known["goal"],
        re.I,
    ):
        known["goal"] = None
    content = {
        "topic": intake.topic,
        "messages": messages,
        "known": known,
        "questions": [
            question for key, question in QUESTIONS.items() if not known[key]
        ],
        "memories": loaded,
    }
    request_id = target["id"] if target else str(uuid4())
    current = db.execute(
        "SELECT * FROM learning_requests WHERE id=?", (request_id,)
    ).fetchone()
    if current:
        if current["roadmap_id"]:
            raise Clarification("此学习需求已生成路线，请在学习路线面板查看。")
        if target is None or current["version"] != target["version"]:
            raise Clarification("学习需求已更新，请刷新后继续。")
        owner = db.execute(
            "SELECT status FROM runs WHERE id=?", (current["last_run_id"],)
        ).fetchone()
        if owner[0] in ("running", "queued") and current["last_run_id"] != run["id"]:
            raise Clarification("此学习需求正在处理，请等待结果后继续。")
    version = current["version"] + 1 if current else 1
    db.execute(
        "INSERT INTO learning_requests VALUES(?,?,?,?,NULL) "
        "ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,content=excluded.content,last_run_id=excluded.last_run_id",
        (request_id, version, json.dumps(content, ensure_ascii=False), run["id"]),
    )
    db.execute(
        "INSERT INTO learning_request_runs VALUES(?,?,?)",
        (run["id"], request_id, revision),
    )
    return content | {"id": request_id, "version": version, "roadmap_id": None}


def complete(
    db: sqlite3.Connection, run_id: str, roadmap_id: str, revision: int
) -> None:
    linked = db.execute(
        "SELECT * FROM learning_request_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if linked:
        if linked["memory_revision"] != revision:
            raise Clarification("记忆在生成期间已更新，请重新续答以采用最新状态。")
        updated = db.execute(
            "UPDATE learning_requests SET roadmap_id=? "
            "WHERE id=? AND last_run_id=? AND roadmap_id IS NULL",
            (roadmap_id, linked["request_id"], run_id),
        )
        if updated.rowcount != 1:
            raise Clarification("学习需求已变化，请刷新后继续。")

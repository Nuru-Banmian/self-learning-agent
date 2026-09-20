import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.todos import Clarification


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions,
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    received_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions,
                    message_id TEXT NOT NULL REFERENCES messages, content TEXT NOT NULL,
                    received_at TEXT NOT NULL, status TEXT NOT NULL,
                    reply TEXT NOT NULL DEFAULT '', todo_ids TEXT NOT NULL DEFAULT '[]',
                    error TEXT, model_calls INTEGER NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run
                    ON runs(session_id) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    scheduled_date TEXT, status TEXT NOT NULL,
                    message_id TEXT NOT NULL REFERENCES messages,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs, kind TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS suggestions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
                    title TEXT NOT NULL, scheduled_date TEXT NOT NULL,
                    todo_id TEXT REFERENCES todos);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL, category TEXT NOT NULL,
                    topic TEXT NOT NULL, scope TEXT NOT NULL,
                    task_id TEXT REFERENCES todos,
                    valid_from TEXT NOT NULL, expires_at TEXT, state TEXT NOT NULL,
                    message_id TEXT NOT NULL REFERENCES messages,
                    updated_at TEXT NOT NULL);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            if "action" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN action TEXT")
            if "memory" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN memory TEXT NOT NULL DEFAULT '{}'"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_session(self, now: str) -> dict[str, str]:
        session = {"id": str(uuid4()), "created_at": now}
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (:id, :created_at)", session)
        return session

    def session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            messages = db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY rowid",
                (session_id,),
            ).fetchall()
            latest = db.execute(
                "SELECT id FROM runs WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return dict(row) | {
                "messages": [dict(m) for m in messages],
                "latest_run_id": latest[0] if latest else None,
            }

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            return dict(row) | {
                "todo_ids": json.loads(row["todo_ids"]),
                "action": json.loads(row["action"]) if row["action"] else None,
                "memory": json.loads(row["memory"]),
            }

    def memories(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT x.*,m.content AS source_content,m.session_id FROM memories x "
                "JOIN messages m ON x.message_id=m.id ORDER BY x.rowid"
            ).fetchall()
            return [
                dict(r)
                | {
                    "source": {
                        "message_id": r["message_id"],
                        "session_id": r["session_id"],
                        "content": r["source_content"],
                    }
                }
                for r in rows
            ]

    def memory_record(self, run_id: str, **changes: Any) -> None:
        with self.connect() as db:
            row = db.execute("SELECT memory FROM runs WHERE id=?", (run_id,)).fetchone()
            record = json.loads(row[0]) | changes
            db.execute(
                "UPDATE runs SET memory=? WHERE id=?",
                (
                    json.dumps(record, ensure_ascii=False),
                    run_id,
                ),
            )
            self._event(db, run_id, "memory", record)

    def save_memories(
        self, run_id: str, candidates: list[dict[str, Any]], rejected: int
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            saved = []
            conflicts = []
            for c in candidates:
                previous = db.execute(
                    "SELECT * FROM memories WHERE category=? AND scope=? "
                    "AND task_id IS ? AND state IN ('active','conflict')",
                    (c["category"], c["scope"], c["task_id"]),
                ).fetchall()
                previous = [
                    p
                    for p in previous
                    if (
                        p["expires_at"] is None
                        or datetime.fromisoformat(p["expires_at"])
                        > datetime.fromisoformat(c["valid_from"])
                    )
                    and (
                        c["expires_at"] is None
                        or datetime.fromisoformat(p["valid_from"])
                        < datetime.fromisoformat(c["expires_at"])
                    )
                ]
                if any(p["content"] == c["content"] for p in previous):
                    continue
                # Conservatively quarantine overlapping topics, rather than silently
                # replacing a preference; explicit correction belongs to Issue #5.
                overlap = [
                    p
                    for p in previous
                    if (p["topic"] in c["content"] or c["topic"] in p["content"])
                ]
                state = "conflict" if overlap else "active"
                for p in overlap:
                    db.execute(
                        "UPDATE memories SET state='conflict' WHERE id=?", (p["id"],)
                    )
                    conflicts.append(p["id"])
                memory_id = str(uuid4())
                db.execute(
                    "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        c["content"],
                        c["category"],
                        c["topic"],
                        c["scope"],
                        c["task_id"],
                        c["valid_from"],
                        c["expires_at"],
                        state,
                        run["message_id"],
                        run["received_at"],
                    ),
                )
                saved.append(memory_id)
                if overlap:
                    conflicts.append(memory_id)
            record = json.loads(run["memory"]) | {
                "learning": "saved" if saved else "empty",
                "saved_ids": saved,
                "rejected": rejected,
                "conflict_ids": conflicts,
                "effect_verified": False,
            }
            db.execute(
                "UPDATE runs SET memory=? WHERE id=?",
                (
                    json.dumps(record, ensure_ascii=False),
                    run_id,
                ),
            )
            if saved:
                self._event(db, run_id, "memory_saved", {"memory_ids": saved})
            self._event(db, run_id, "memory", record)

    def suggestions(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT s.* FROM suggestions s JOIN runs r ON r.id=s.run_id "
                    "WHERE r.session_id=? ORDER BY s.rowid",
                    (session_id,),
                )
            ]

    def claim(
        self,
        run_id: str,
        session_id: str,
        content: str,
        now: str,
        action: dict[str, Any] | None = None,
    ) -> bool:
        encoded = (
            json.dumps(action, sort_keys=True, ensure_ascii=False) if action else None
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if existing:
                if (
                    existing["session_id"] != session_id
                    or existing["content"] != content
                    or existing["action"] != encoded
                ):
                    raise Conflict("请求标识已用于其他消息，请使用新的标识。")
                return False
            active = db.execute(
                "SELECT id FROM runs WHERE session_id=? AND status='running'",
                (session_id,),
            ).fetchone()
            if active:
                raise Conflict("此会话正在处理上一条消息，请等待完成。")
            message_id = str(uuid4())
            db.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, ?)",
                (message_id, session_id, content, now),
            )
            db.execute(
                "INSERT INTO runs(id,session_id,message_id,content,received_at,status) "
                "VALUES(?,?,?,?,?,'running')",
                (run_id, session_id, message_id, content, now),
            )
            db.execute("UPDATE runs SET action=? WHERE id=?", (encoded, run_id))
            self._event(db, run_id, "role", {"role": "main", "status": "processing"})
            return True

    @staticmethod
    def _event(
        db: sqlite3.Connection, run_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        db.execute(
            "INSERT INTO events(run_id,kind,data) VALUES(?,?,?)",
            (run_id, kind, json.dumps(data, ensure_ascii=False)),
        )

    def event(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        with self.connect() as db:
            self._event(db, run_id, kind, data)

    def events(self, run_id: str, after: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, after),
            ).fetchall()
            return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def count_call(self, run_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET model_calls=model_calls+1 WHERE id=?", (run_id,)
            )

    def finish(
        self,
        run_id: str,
        status: str,
        reply: str,
        items: list[dict[str, Any]] | None = None,
        error: str | None = None,
        *,
        change: dict[str, Any] | None = None,
        tool: str = "create_todos",
        suggestions: list[dict[str, Any]] | None = None,
        accept: str | None = None,
    ) -> None:
        # Todos, success evidence, assistant message and terminal state commit together.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "running":
                return
            ids = []
            if accept:
                suggestion = db.execute(
                    "SELECT s.* FROM suggestions s JOIN runs r ON r.id=s.run_id "
                    "WHERE s.id=? AND r.session_id=?",
                    (accept, run["session_id"]),
                ).fetchone()
                if suggestion is None:
                    raise Clarification("请指定当前会话中的建议，本次未新增。")
                if suggestion["todo_id"]:
                    raise Clarification("这项建议已经加入待办，本次没有重复新增。")
                items = [
                    {
                        "title": suggestion["title"],
                        "scheduled_date": suggestion["scheduled_date"],
                    }
                ]
                reply = (
                    f"已保存：{suggestion['title']} — {suggestion['scheduled_date']}"
                )
            if change:
                todo = db.execute(
                    "SELECT * FROM todos WHERE id=?", (change["id"],)
                ).fetchone()
                if todo is None:
                    raise Clarification("请刷新列表，指定存在的待办，本次未修改。")
                db.execute(
                    "UPDATE todos SET title=?,scheduled_date=?,status=?,updated_at=? "
                    "WHERE id=?",
                    (
                        change.get("title", todo["title"]),
                        change.get("scheduled_date", todo["scheduled_date"]),
                        change.get("status", todo["status"]),
                        run["received_at"],
                        todo["id"],
                    ),
                )
                ids.append(todo["id"])
                reply = (
                    f"已更新：{change.get('title', todo['title'])} — "
                    + (change.get("scheduled_date", todo["scheduled_date"]) or "未安排")
                    + (
                        " — 已完成"
                        if change.get("status", todo["status"]) == "completed"
                        else " — 待完成"
                    )
                )
            for item in items or []:
                todo_id = str(uuid4())
                ids.append(todo_id)
                db.execute(
                    "INSERT INTO todos VALUES(?,?,?,'pending',?,?,?)",
                    (
                        todo_id,
                        item["title"],
                        item["scheduled_date"],
                        run["message_id"],
                        run["received_at"],
                        run["received_at"],
                    ),
                )
                if accept:
                    db.execute(
                        "UPDATE suggestions SET todo_id=? WHERE id=?", (todo_id, accept)
                    )
            if suggestions is not None:
                for suggestion in suggestions:
                    db.execute(
                        "INSERT INTO suggestions VALUES(?,?,?,?,NULL)",
                        (
                            suggestion["id"],
                            run_id,
                            suggestion["title"],
                            suggestion["scheduled_date"],
                        ),
                    )
                self._event(
                    db,
                    run_id,
                    "tool_result",
                    {
                        "tool": "plan_day",
                        "status": "success",
                        "suggestions": suggestions,
                    },
                )
            if items or change:
                self._event(
                    db,
                    run_id,
                    "tool_result",
                    {
                        "tool": tool,
                        "status": "success",
                        "todo_ids": ids,
                    },
                )
                self._event(db, run_id, "saved", {"todo_ids": ids})
            memory = json.loads(run["memory"])
            if status == "failed" and memory.get("saved_ids"):
                status = "partial"
                reply += "\n\n记忆已提交保存，但本轮其他处理未完成。"
            if status == "completed" and memory.get("learning") == "failed":
                status, error = "partial", "memory_learning"
                reply += "\n\n本轮学习保存失败，未新增记忆；回答与待办结果仍可查看。"
            if memory.get("conflict_ids"):
                reply += "\n\n发现可能冲突的记忆，已暂停这些记忆生效，请澄清适用范围。"
            if memory.get("rejected"):
                reply += (
                    "\n\n部分记忆候选无法核实，未保存；"
                    "请用完整陈述明确背景、偏好或条件。"
                )
            db.execute(
                "INSERT INTO messages VALUES(?,?,'assistant',?,?)",
                (
                    str(uuid4()),
                    run["session_id"],
                    reply,
                    run["received_at"],
                ),
            )
            db.execute(
                "UPDATE runs SET status=?,reply=?,todo_ids=?,error=? WHERE id=?",
                (status, reply, json.dumps(ids), error, run_id),
            )
            self._event(db, run_id, "reply", {"text": reply})
            self._event(db, run_id, "terminal", {"status": status, "error": error})

    def interrupt_unfinished(self) -> None:
        with self.connect() as db:
            ids = [
                r[0] for r in db.execute("SELECT id FROM runs WHERE status='running'")
            ]
        for run_id in ids:
            self.finish(
                run_id, "failed", "处理已中断，未保存待办。", error="interrupted"
            )

    def todos(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT t.*,m.content,m.session_id FROM todos t "
                "JOIN messages m ON m.id=t.message_id ORDER BY t.rowid"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "scheduled_date": r["scheduled_date"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "source": {
                        "message_id": r["message_id"],
                        "session_id": r["session_id"],
                        "content": r["content"],
                    },
                }
                for r in rows
            ]

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import learning_requests as learning_request_store
from app import revisions, roadmap_store, scheduling
from app.memory_policy import overlaps
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
                CREATE TABLE IF NOT EXISTS memory_sources (
                    message_id TEXT PRIMARY KEY REFERENCES messages);
                INSERT OR IGNORE INTO memory_sources SELECT message_id FROM memories;
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
                    criterion TEXT NOT NULL, observation TEXT NOT NULL,
                    status TEXT NOT NULL, baseline_run_id TEXT REFERENCES runs,
                    created_at TEXT NOT NULL);
            """)
            db.executescript(roadmap_store.SCHEMA)
            db.executescript(revisions.SCHEMA)
            db.executescript(scheduling.SCHEMA)
            db.executescript(learning_request_store.SCHEMA)
            columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            if "presentation" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN presentation "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
            if "roadmap" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN roadmap TEXT NOT NULL DEFAULT '{}'"
                )
            if "execution" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN execution TEXT NOT NULL DEFAULT '{}'"
                )
            if "action" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN action TEXT")
            if "memory" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN memory TEXT NOT NULL DEFAULT '{}'"
                )
            if "research" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN research TEXT NOT NULL DEFAULT '{}'"
                )
            if "weather" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN weather TEXT NOT NULL DEFAULT '{}'"
                )
            if "reply_message_id" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN reply_message_id TEXT")
                db.execute(
                    "UPDATE runs SET reply_message_id=(SELECT id FROM messages m "
                    "WHERE m.session_id=runs.session_id AND m.role='assistant' "
                    "AND m.received_at=runs.received_at "
                    "AND m.content=runs.reply LIMIT 1)"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS runs_by_reply ON runs(reply_message_id)"
            )
            if "retry_of" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN retry_of TEXT REFERENCES runs")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_retry ON runs(retry_of)")
            if "result_committed" not in columns:
                db.execute(
                    "ALTER TABLE runs ADD COLUMN "
                    "result_committed INTEGER NOT NULL DEFAULT 0"
                )
                db.execute(
                    "UPDATE runs SET result_committed=1 WHERE todo_ids != '[]' "
                    "OR EXISTS(SELECT 1 FROM suggestions s WHERE s.run_id=runs.id) "
                    "OR EXISTS(SELECT 1 FROM events e WHERE e.run_id=runs.id "
                    "AND e.kind IN ('memory_updated','memory_deleted'))"
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
            db.execute("BEGIN")
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
                "messages": [self._message(db, m) for m in messages],
                "latest_run_id": latest[0] if latest else None,
                "run_ids": [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM runs WHERE session_id=? ORDER BY rowid",
                        (session_id,),
                    )
                ],
            }

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            return dict(row) | {
                **self._presentation(row),
                "todo_ids": json.loads(row["todo_ids"]),
                "action": json.loads(row["action"]) if row["action"] else None,
                "memory": json.loads(row["memory"]),
                "execution": json.loads(row["execution"]),
                "checkpoints": [
                    dict(c) | {"reviewer": "user"}
                    for c in db.execute(
                        "SELECT * FROM checkpoints WHERE run_id=? ORDER BY rowid",
                        (run_id,),
                    )
                ],
                "research": json.loads(row["research"]),
                "roadmap": json.loads(row["roadmap"]),
                "weather": json.loads(row["weather"]),
                "retryable": self._retryable(row),
                "retry_run_id": next(
                    (
                        r[0]
                        for r in db.execute(
                            "SELECT id FROM runs WHERE retry_of=?",
                            (run_id,),
                        )
                    ),
                    None,
                ),
                "queue_position": db.execute(
                    "SELECT COUNT(*) FROM runs WHERE session_id=? "
                    "AND status IN ('running','queued') "
                    "AND rowid < (SELECT rowid FROM runs WHERE id=?)",
                    (row["session_id"], run_id),
                ).fetchone()[0]
                if row["status"] == "queued"
                else 0,
                "events": self._events(db, run_id, 0),
                "messages": [
                    self._message(db, m)
                    for m in db.execute(
                        "SELECT * FROM messages WHERE id IN (?,?) ORDER BY rowid",
                        (row["message_id"], row["reply_message_id"]),
                    )
                ],
            }

    @staticmethod
    def _presentation(run: sqlite3.Row) -> dict[str, Any]:
        presentation = json.loads(run["presentation"])
        route = json.loads(run["roadmap"])
        research = json.loads(run["research"])
        links = presentation.get("roadmap_links", [])
        if not links and route:
            links = [
                {"roadmap_id": route["id"], "node_id": None, "title": route["title"]}
            ]
        return {
            "roadmap_context": bool(
                presentation.get("roadmap_context")
                or route
                or research.get("purpose") in ("roadmap", "revision")
            ),
            "roadmap_links": links,
        }

    def _message(self, db: sqlite3.Connection, message: sqlite3.Row) -> dict[str, Any]:
        run = db.execute(
            "SELECT * FROM runs WHERE reply_message_id=?", (message["id"],)
        ).fetchone()
        return dict(message) | (self._presentation(run) if run else {})

    def session_roadmap_links(
        self, session_id: str, before_run_id: str
    ) -> list[dict[str, Any]]:
        """Read the latest preceding route reference without loading run history."""
        with self.connect() as db:
            for row in db.execute(
                "SELECT roadmap,research,presentation FROM runs WHERE session_id=? "
                "AND rowid < (SELECT rowid FROM runs WHERE id=?) "
                "AND (roadmap != '{}' OR presentation != '{}') ORDER BY rowid DESC",
                (session_id, before_run_id),
            ):
                links: list[dict[str, Any]] = self._presentation(row)["roadmap_links"]
                if links:
                    return links
        return []

    def add_checkpoint(
        self, run_id: str, check: dict[str, Any], now: str
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM checkpoints WHERE id=?", (check["id"],)
            ).fetchone()
            if existing:
                if existing["run_id"] != run_id or any(
                    existing[k] != v for k, v in check.items()
                ):
                    raise Conflict("检查点标识已用于其他内容，请保留原记录。")
                return dict(existing) | {"reviewer": "user"}
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run["status"] in ("running", "queued"):
                raise Conflict("请在本轮结束后核验检查点。")
            if check["baseline_run_id"]:
                baseline = db.execute(
                    "SELECT * FROM runs WHERE id=?", (check["baseline_run_id"],)
                ).fetchone()
                model = json.loads(run["execution"]).get("model")
                if (
                    baseline is None
                    or baseline["id"] == run_id
                    or baseline["status"] in ("running", "queued")
                    or not model
                    or model != json.loads(baseline["execution"]).get("model")
                    or not run["model_calls"]
                    or not baseline["model_calls"]
                ):
                    raise Conflict("对比须选另一条已结束、同模型且有模型调用的记录。")
            record = check | {"run_id": run_id, "created_at": now}
            db.execute(
                "INSERT INTO checkpoints VALUES "
                "(:id,:run_id,:criterion,:observation,:status,:baseline_run_id,:created_at)",
                record,
            )
            return record | {"reviewer": "user"}

    def execution_record(self, run_id: str, record: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET execution=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), run_id),
            )

    def weather_record(self, run_id: str, record: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET weather=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), run_id),
            )
            self._event(db, run_id, "weather", record)

    def research_record(self, run_id: str, record: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET research=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), run_id),
            )
            self._event(db, run_id, "research", record)

    def roadmaps(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                {
                    "id": r["id"],
                    "version": r["version"],
                    "created_at": r["created_at"],
                    **{
                        k: json.loads(r["content"])[k]
                        for k in ("title", "goal", "status")
                    },
                }
                for r in db.execute("SELECT * FROM roadmaps ORDER BY rowid DESC")
            ]

    def learning_requests(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return learning_request_store.read_all(db)

    def save_learning_request(
        self,
        run: dict[str, Any],
        intake: learning_request_store.Intake,
        snapshot: list[dict[str, Any]],
        loaded: list[dict[str, Any]],
        revision: int,
        *,
        new_intent: bool,
        selected_id: str | None,
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._memory_revision(db) != revision:
                raise Clarification("记忆已更新，请重新补充以采用最新状态。")
            saved = learning_request_store.save(
                db,
                run,
                intake,
                snapshot,
                loaded,
                revision,
                new_intent=new_intent,
                selected_id=selected_id,
            )
            self._event(db, run["id"], "learning_request_saved", saved)
            return saved

    def roadmap(self, roadmap_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN")
            return roadmap_store.read(db, roadmap_id)

    def memories(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT x.*,m.content AS source_content,m.session_id FROM memories x "
                "JOIN messages m ON x.message_id=m.id "
                "WHERE x.state != 'deleted' ORDER BY x.rowid"
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

    def source_processed(self, message_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM memory_sources WHERE message_id=?", (message_id,)
                ).fetchone()
                is not None
            )

    @staticmethod
    def _memory_revision(db: sqlite3.Connection) -> int:
        return int(
            db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events "
                "WHERE kind IN ('memory_saved','memory_updated','memory_deleted')"
            ).fetchone()[0]
        )

    def memory_revision(self) -> int:
        with self.connect() as db:
            return self._memory_revision(db)

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
        self,
        run_id: str,
        candidates: list[dict[str, Any]],
        rejected: int,
        expected_revision: int,
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._memory_revision(db) != expected_revision:
                raise Clarification(
                    "记忆已在其他会话更新，请重新表达；本轮旧候选未保存。"
                )
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if db.execute(
                "SELECT 1 FROM memory_sources WHERE message_id=?", (run["message_id"],)
            ).fetchone():
                return
            db.execute("INSERT INTO memory_sources VALUES(?)", (run["message_id"],))
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
                # Ambiguous ordinary assertions pause conflicting same-scope facts.
                overlap = [p for p in previous if overlaps(dict(p), c)]
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
                "saved": [
                    dict(
                        db.execute(
                            "SELECT * FROM memories WHERE id=?", (mid,)
                        ).fetchone()
                    )
                    | {
                        "source": {
                            "message_id": run["message_id"],
                            "session_id": run["session_id"],
                            "content": run["content"],
                        }
                    }
                    for mid in saved
                ],
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
            self._check_queue(db, session_id)
            message_id = str(uuid4())
            db.execute(
                "INSERT INTO messages VALUES (?, ?, 'user', ?, ?)",
                (message_id, session_id, content, now),
            )
            db.execute(
                "INSERT INTO runs(id,session_id,message_id,content,received_at,status) "
                "VALUES(?,?,?,?,?,'queued')",
                (run_id, session_id, message_id, content, now),
            )
            db.execute("UPDATE runs SET action=? WHERE id=?", (encoded, run_id))
            self._event(db, run_id, "queued", {"status": "queued"})
            return True

    @staticmethod
    def _check_queue(db: sqlite3.Connection, session_id: str) -> None:
        count = db.execute(
            "SELECT COUNT(*) FROM runs WHERE session_id=? "
            "AND status IN ('running','queued')",
            (session_id,),
        ).fetchone()[0]
        if count >= 10:
            raise Conflict("此会话已有 10 条待处理请求，请等待后重试。")

    @staticmethod
    def _retryable(run: sqlite3.Row) -> bool:
        return run["status"] in ("failed", "partial") and not run["result_committed"]

    def retry(self, run_id: str) -> str:
        # One child per attempt makes retry submission itself idempotent.
        # Reuse the source message so committed learning cannot be repeated.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            child = db.execute(
                "SELECT id FROM runs WHERE retry_of=?", (run_id,)
            ).fetchone()
            if child:
                return str(child[0])
            if not self._retryable(run):
                return run_id
            self._check_queue(db, run["session_id"])
            new_id = str(uuid4())
            memory = json.loads(run["memory"])
            retained = {
                k: memory[k] for k in ("saved_ids", "conflict_ids") if k in memory
            }
            db.execute(
                "INSERT INTO runs(id,session_id,message_id,content,received_at,status,"
                "action,memory,retry_of) VALUES(?,?,?,?,?,'queued',?,?,?)",
                (
                    new_id,
                    run["session_id"],
                    run["message_id"],
                    run["content"],
                    run["received_at"],
                    run["action"],
                    json.dumps(retained),
                    run_id,
                ),
            )
            self._event(db, new_id, "queued", {"status": "queued", "retry_of": run_id})
            return new_id

    def start_next(self, session_id: str) -> str | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM runs WHERE session_id=? AND status='running'",
                (session_id,),
            ).fetchone():
                return None
            row = db.execute(
                "SELECT id FROM runs WHERE session_id=? AND status='queued' "
                "ORDER BY rowid LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            run_id = str(row[0])
            db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            self._event(db, run_id, "role", {"role": "main", "status": "processing"})
            return run_id

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
            return self._events(db, run_id, after)

    @staticmethod
    def _events(
        db: sqlite3.Connection, run_id: str, after: int
    ) -> list[dict[str, Any]]:
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
        memory_change: dict[str, Any] | None = None,
        roadmap: dict[str, Any] | None = None,
        accept_node: dict[str, Any] | None = None,
        accept_nodes: dict[str, Any] | None = None,
        complete_node: dict[str, Any] | None = None,
        revision_preview: dict[str, Any] | None = None,
        revision_confirm: dict[str, Any] | None = None,
        schedule_preview: dict[str, Any] | None = None,
        schedule_confirm: dict[str, Any] | None = None,
        roadmap_context: bool = False,
        roadmap_links: list[dict[str, Any]] | None = None,
    ) -> None:
        # Todos, success evidence, assistant message and terminal state commit together.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] not in ("running", "queued"):
                return
            if roadmap_context or roadmap_links:
                db.execute(
                    "UPDATE runs SET presentation=? WHERE id=?",
                    (
                        json.dumps(
                            {
                                "roadmap_context": roadmap_context,
                                "roadmap_links": roadmap_links or [],
                            }
                        ),
                        run_id,
                    ),
                )
            ids = []
            if revision_preview or revision_confirm:
                proposal, reply = (
                    revisions.preview(db, run, revision_preview)
                    if revision_preview
                    else revisions.confirm(db, run, revision_confirm or {})
                )
                current_route = roadmap_store.read(db, proposal["roadmap_id"])
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (json.dumps(current_route, ensure_ascii=False), run_id),
                )
                self._event(
                    db,
                    run_id,
                    "roadmap_revision_"
                    + ("preview" if revision_preview else proposal["status"]),
                    proposal,
                )
            if schedule_preview or schedule_confirm:
                proposal, reply = (
                    scheduling.preview(db, run, schedule_preview)
                    if schedule_preview
                    else scheduling.confirm(db, run, schedule_confirm or {})
                )
                current_route = roadmap_store.read(db, proposal["roadmap_id"])
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (json.dumps(current_route, ensure_ascii=False), run_id),
                )
                self._event(
                    db,
                    run_id,
                    "roadmap_schedule_preview"
                    if schedule_preview
                    else "roadmap_schedule_stale"
                    if proposal["status"] == "stale"
                    else "roadmap_schedule_confirmed",
                    proposal,
                )
            if roadmap:
                saved_roadmap = roadmap_store.save(db, run, roadmap)
                learning_request_store.complete(
                    db, run_id, saved_roadmap["id"], self._memory_revision(db)
                )
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (
                        json.dumps(saved_roadmap, ensure_ascii=False),
                        run_id,
                    ),
                )
                self._event(db, run_id, "roadmap_saved", saved_roadmap)
            if accept_node:
                accepted_route, todo_id, created = roadmap_store.accept_node(
                    db, run, accept_node
                )
                if todo_id:
                    ids.append(todo_id)
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (
                        json.dumps(accepted_route, ensure_ascii=False),
                        run_id,
                    ),
                )
                self._event(
                    db,
                    run_id,
                    "roadmap_node_accepted",
                    {
                        **accept_node,
                        "todo_id": todo_id,
                        "created": created,
                    },
                )
                reply = (
                    "已加入所选节点，日期以路线当前安排为准。"
                    if created
                    else "该节点已完成，本次跳过，未新增待办。"
                    if next(
                        n
                        for n in accepted_route["nodes"]
                        if n["id"] == accept_node["node_id"]
                    )["status"]
                    == "completed"
                    else "该节点已关联待办，本次未重复新增。"
                )
            if accept_nodes:
                accepted_route, results = roadmap_store.accept_nodes(
                    db, run, accept_nodes
                )
                ids.extend(r["todo_id"] for r in results if r["todo_id"])
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (json.dumps(accepted_route, ensure_ascii=False), run_id),
                )
                self._event(
                    db,
                    run_id,
                    "roadmap_nodes_accepted",
                    {**accept_nodes, "results": results},
                )
                created_count = sum(r["status"] == "created" for r in results)
                existing = sum(r["status"] == "already_added" for r in results)
                completed = sum(r["status"] == "completed" for r in results)
                reply = (
                    f"本次新增 {created_count} 项（日期以节点安排为准）；"
                    f"已加入 {existing} 项，"
                    f"跳过已完成 {completed} 项。未选节点保持原状。"
                    if results
                    else "未选择节点，未新增待办；路线已保留。"
                )
            if complete_node:
                completed_route, created = roadmap_store.mark_mastered(
                    db, run, complete_node
                )
                completed_node = next(
                    n
                    for n in completed_route["nodes"]
                    if n["id"] == complete_node["node_id"]
                )
                if completed_node["todo_id"]:
                    ids.append(completed_node["todo_id"])
                db.execute(
                    "UPDATE runs SET roadmap=? WHERE id=?",
                    (json.dumps(completed_route, ensure_ascii=False), run_id),
                )
                self._event(
                    db,
                    run_id,
                    "roadmap_node_completed",
                    {**complete_node, "created": created},
                )
                reply = (
                    "已标记为已掌握。" if created else "节点已完成，保留原完成记录。"
                )
                reply += (
                    "关联待办已完成。" if completed_node["todo_id"] else "未创建待办。"
                )
            if memory_change:
                target = db.execute(
                    "SELECT * FROM memories WHERE id=? AND state != 'deleted'",
                    (memory_change["memory_id"],),
                ).fetchone()
                if (
                    target is None
                    or target["message_id"] != memory_change["expected_source_id"]
                ):
                    raise Clarification(
                        "记忆已变化或已删除，请刷新后重新选择；未修改。"
                    )
                for source_id in (target["message_id"], run["message_id"]):
                    db.execute(
                        "INSERT OR IGNORE INTO memory_sources VALUES(?)", (source_id,)
                    )
                if memory_change["tool"] == "update_memory":
                    others = db.execute(
                        "SELECT * FROM memories WHERE id != ? AND state='active'",
                        (target["id"],),
                    ).fetchall()
                    if any(overlaps(dict(m), memory_change) for m in others):
                        raise Clarification(
                            "新内容与另一条生效记忆重叠，请明确要更正的那条记忆；未修改。"
                        )
                    db.execute(
                        "UPDATE memories SET content=?,category=?,topic=?,scope=?,"
                        "task_id=?,valid_from=?,expires_at=?,state='active',"
                        "message_id=?,updated_at=? WHERE id=?",
                        tuple(
                            memory_change[k]
                            for k in (
                                "content",
                                "category",
                                "topic",
                                "scope",
                                "task_id",
                                "valid_from",
                                "expires_at",
                            )
                        )
                        + (run["message_id"], run["received_at"], target["id"]),
                    )
                    reply = "已更正记忆：" + memory_change["content"]
                    kind = "memory_updated"
                else:
                    db.execute(
                        "UPDATE memories SET state='deleted' WHERE id=?",
                        (target["id"],),
                    )
                    reply, kind = "已删除记忆。", "memory_deleted"
                self._event(db, run_id, kind, {"memory_id": target["id"]})
                db.execute(
                    "UPDATE runs SET memory=? WHERE id=?",
                    (
                        json.dumps({"changed_ids": [target["id"]], "operation": kind}),
                        run_id,
                    ),
                )
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
                if not (change.get("status") == todo["status"] == "completed"):
                    db.execute(
                        "UPDATE todos SET title=?,scheduled_date=?,"
                        "status=?,updated_at=? "
                        "WHERE id=?",
                        (
                            change.get("title", todo["title"]),
                            change.get("scheduled_date", todo["scheduled_date"]),
                            change.get("status", todo["status"]),
                            run["received_at"],
                            todo["id"],
                        ),
                    )
                if any(
                    key in change and change[key] != todo[key]
                    for key in ("title", "scheduled_date")
                ):
                    db.execute(
                        "UPDATE roadmaps SET version=version+1 WHERE id IN "
                        "(SELECT roadmap_id FROM roadmap_nodes WHERE todo_id=?)",
                        (todo["id"],),
                    )
                ids.append(todo["id"])
                if change.get("status") == "completed":
                    node = db.execute(
                        "SELECT id FROM roadmap_nodes WHERE todo_id=?", (todo["id"],)
                    ).fetchone()
                    if node:
                        completed_route, created = roadmap_store.complete(
                            db, run, node["id"], "complete_todo"
                        )
                        db.execute(
                            "UPDATE runs SET roadmap=? WHERE id=?",
                            (json.dumps(completed_route, ensure_ascii=False), run_id),
                        )
                        self._event(
                            db,
                            run_id,
                            "roadmap_node_completed",
                            {"node_id": node["id"], "created": created},
                        )
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
                        "tool": tool,
                        "role": "main",
                        "status": "success" if status == "completed" else "partial",
                        "suggestions": suggestions,
                    },
                )
            if items or change or accept_node or accept_nodes or complete_node:
                self._event(
                    db,
                    run_id,
                    "tool_result",
                    {
                        "tool": (
                            "complete_roadmap_node"
                            if complete_node
                            else "accept_roadmap_nodes"
                            if accept_nodes
                            else "accept_roadmap_node"
                            if accept_node
                            else tool
                        ),
                        "status": "success",
                        "todo_ids": ids,
                    },
                )
                self._event(db, run_id, "saved", {"todo_ids": ids})
            memory = json.loads(run["memory"])
            weather = json.loads(run["weather"])
            if weather and weather["status"] == "running":
                weather["status"] = "partial" if weather["location"] else "error"
                weather["gaps"].append(
                    "本轮已中断或失败，天气准备未完成；已有信息保留。"
                )
                for call in weather["calls"]:
                    if call["status"] == "running":
                        call.update(status="error", error="interrupted")
                        self._event(
                            db, run_id, "tool_result", {"role": "execution", **call}
                        )
                db.execute(
                    "UPDATE runs SET weather=? WHERE id=?",
                    (json.dumps(weather, ensure_ascii=False), run_id),
                )
                self._event(db, run_id, "weather", weather)
                self._event(
                    db,
                    run_id,
                    "role",
                    {"role": "execution", "status": weather["status"]},
                )
                if weather["location"]:
                    status = "partial"
                    reply += "\n已确认的目的地信息保留在天气面板中。"
            research = json.loads(run["research"])
            if research and status == "failed":
                research["status"] = "partial" if research["sources"] else "error"
                research["gaps"].append("本轮已中断或失败，查询与学习安排未完成。")
                for call in research["calls"]:
                    if call["status"] == "running":
                        call.update(status="error", error="interrupted")
                        self._event(
                            db, run_id, "tool_result", {"role": "execution", **call}
                        )
                db.execute(
                    "UPDATE runs SET research=? WHERE id=?",
                    (json.dumps(research, ensure_ascii=False), run_id),
                )
                self._event(db, run_id, "research", research)
                if research["sources"]:
                    status = "partial"
                    if research.get("purpose") in ("roadmap", "revision"):
                        reply = (
                            "处理未完成，尚未生成有效的路线或调整方案，未新增待办。"
                            "请展开资料记录查看限制后重试。"
                        )
                    else:
                        reply += "\n\n已取得的外部资料保留：\n" + "\n".join(
                            f"[{s['id']}] {s['title']} ({s['material_type']})\n"
                            f"{s['url']}\n{s['snippet']}"
                            for s in research["sources"]
                        )
            before_memory_notice = reply
            if status == "failed" and memory.get("saved_ids"):
                status = "partial"
                reply += "\n\n记忆已提交保存，但本轮其他处理未完成。"
            if (
                status in ("completed", "partial")
                and memory.get("learning") == "failed"
            ):
                status, error = "partial", error or "memory_learning"
                reply += "\n\n本轮学习保存失败，未新增记忆；回答与待办结果仍可查看。"
            if memory.get("conflict_ids"):
                reply += "\n\n发现可能冲突的记忆，已暂停这些记忆生效，请澄清适用范围。"
            if memory.get("rejected"):
                reply += (
                    "\n\n部分记忆候选无法核实，未保存；"
                    "请用完整陈述明确背景、偏好或条件。"
                )
            if (
                roadmap or revision_preview or revision_confirm
            ) and reply != before_memory_notice:
                reply = before_memory_notice + "\n记忆处理有限制，请查看处理记录。"
            reply_message_id = str(uuid4())
            db.execute(
                "INSERT INTO messages VALUES(?,?,'assistant',?,?)",
                (
                    reply_message_id,
                    run["session_id"],
                    reply,
                    run["received_at"],
                ),
            )
            db.execute(
                "UPDATE runs SET status=?,reply=?,todo_ids=?,error=?,"
                "reply_message_id=?,result_committed=? WHERE id=?",
                (
                    status,
                    reply,
                    json.dumps(ids),
                    error,
                    reply_message_id,
                    bool(
                        items
                        or change
                        or memory_change
                        or accept
                        or suggestions
                        or roadmap
                        or accept_node
                        or accept_nodes
                        or complete_node
                        or revision_preview
                        or revision_confirm
                        or schedule_preview
                        or schedule_confirm
                    ),
                    run_id,
                ),
            )
            self._event(db, run_id, "reply", {"text": reply})
            self._event(db, run_id, "terminal", {"status": status, "error": error})

    def interrupt_unfinished(self) -> None:
        with self.connect() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM runs WHERE status IN ('running','queued') "
                    "ORDER BY rowid"
                )
            ]
        for run_id in ids:
            self.finish(
                run_id, "failed", "处理已中断，未保存待办。", error="interrupted"
            )

    def todos(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT t.*,m.content,m.session_id,n.roadmap_id,n.id AS node_id "
                "FROM todos t "
                "JOIN messages m ON m.id=t.message_id "
                "LEFT JOIN roadmap_nodes n ON n.todo_id=t.id ORDER BY t.rowid"
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "scheduled_date": r["scheduled_date"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "roadmap": {"id": r["roadmap_id"], "node_id": r["node_id"]}
                    if r["roadmap_id"]
                    else None,
                    "source": {
                        "message_id": r["message_id"],
                        "session_id": r["session_id"],
                        "content": r["content"],
                    },
                }
                for r in rows
            ]

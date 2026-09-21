import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.evidence import Checkpoint
from app.memory import active
from app.runtime import execute
from app.settings import Settings
from app.store import Conflict, Store
from app.todos import Action, overview


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    content: str = Field(min_length=1, max_length=8000)
    action: Action | None = None


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    config = settings or Settings()
    now = clock or (lambda: datetime.now(UTC))
    store = Store(config.db_path)
    tasks: set[asyncio.Task[None]] = set()

    async def drain(session_id: str, run_id: str) -> None:
        current: str | None = run_id
        while current is not None:
            try:
                await execute(store, config, current, transport)
            except Exception:
                # A new role/adapter must not strand its run or expose raw errors.
                store.finish(
                    current, "failed", "处理失败，已提交结果保留。", error="internal"
                )
            current = store.start_next(session_id)

    def schedule(session_id: str) -> None:
        run_id = store.start_next(session_id)
        if run_id:
            task = asyncio.create_task(drain(session_id, run_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store.interrupt_unfinished()
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.interrupt_unfinished()

    app = FastAPI(title="生活助理", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_configured": bool(config.dashscope_api_key.get_secret_value()),
            "timezone": config.user_timezone,
        }

    @app.post("/api/sessions", status_code=201)
    def create_session() -> dict[str, str]:
        return store.create_session(now().isoformat())

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str) -> dict[str, Any]:
        result = store.session(session_id)
        if result is None:
            raise HTTPException(404, "会话不存在")
        return result

    @app.post("/api/sessions/{session_id}/messages", status_code=202)
    async def chat(session_id: str, message: ChatInput) -> dict[str, Any]:
        session(session_id)
        try:
            fresh = store.claim(
                message.request_id,
                session_id,
                message.content,
                now().isoformat(),
                message.action.model_dump() if message.action else None,
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        if fresh:
            schedule(session_id)
        return get_run(message.request_id)

    @app.get("/api/sessions/{session_id}/suggestions")
    def suggestions(session_id: str) -> list[dict[str, Any]]:
        session(session_id)
        return store.suggestions(session_id)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = store.run(run_id)
        if run is None:
            raise HTTPException(404, "请求不存在")
        return run

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> StreamingResponse:
        get_run(run_id)
        try:
            after = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "无效事件标识") from None

        async def stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                for event in store.events(run_id, cursor):
                    cursor = event["seq"]
                    data = json.dumps(event["data"], ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {event['kind']}\ndata: {data}\n\n"
                    if event["kind"] == "terminal":
                        return
                if get_run(run_id)["status"] not in ("running", "queued"):
                    # Re-read after terminal commits to avoid missing the final batch.
                    if not store.events(run_id, cursor):
                        return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/runs/{run_id}/checkpoints", status_code=201)
    def checkpoint(run_id: str, check: Checkpoint) -> dict[str, Any]:
        get_run(run_id)
        try:
            return store.add_checkpoint(run_id, check.model_dump(), now().isoformat())
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/runs/{run_id}/retry", status_code=202)
    async def retry(run_id: str) -> dict[str, Any]:
        original = get_run(run_id)
        try:
            child_id = store.retry(run_id)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        schedule(original["session_id"])
        return get_run(child_id)

    @app.get("/api/todos")
    def todos() -> list[dict[str, Any]]:
        return store.todos()

    @app.get("/api/roadmaps")
    def roadmaps() -> list[dict[str, Any]]:
        return store.roadmaps()

    @app.get("/api/learning-requests")
    def learning_requests() -> list[dict[str, Any]]:
        return store.learning_requests()

    @app.get("/api/roadmaps/{roadmap_id}")
    def roadmap(roadmap_id: str) -> dict[str, Any]:
        result = store.roadmap(roadmap_id)
        if result is None:
            raise HTTPException(404, "学习路线不存在")
        return result

    @app.get("/api/memories")
    def memories() -> list[dict[str, Any]]:
        return [
            m | {"active": active(m, now(), store.todos())} for m in store.memories()
        ]

    @app.get("/api/todos/overview")
    def todo_overview() -> dict[str, Any]:
        return overview(
            store.todos(), now().astimezone(ZoneInfo(config.user_timezone)).date()
        )

    frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app

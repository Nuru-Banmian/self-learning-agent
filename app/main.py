import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.runtime import execute
from app.settings import Settings
from app.store import Conflict, Store


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    content: str = Field(min_length=1, max_length=8000)


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
                message.request_id, session_id, message.content, now().isoformat()
            )
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        if fresh:
            task = asyncio.create_task(
                execute(store, config, message.request_id, transport)
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return get_run(message.request_id)

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
                if get_run(run_id)["status"] != "running":
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

    @app.get("/api/todos")
    def todos() -> list[dict[str, Any]]:
        return store.todos()

    frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app

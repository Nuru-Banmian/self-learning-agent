"""Opt-in real Bailian check; never touches the user's application database."""

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def main():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="assistant-live-") as directory:
        settings = Settings(db_path=Path(directory) / "live.sqlite3")
        if not settings.dashscope_api_key.get_secret_value():
            raise SystemExit("SKIP: backend credential is not configured")
        with TestClient(create_app(settings)) as client:
            session = client.post("/api/sessions").json()["id"]
            response = client.post(
                f"/api/sessions/{session}/messages",
                json={
                    "request_id": "live-create",
                    "content": "请记录明天学习 Python 生成器和整理书桌",
                },
            )
            response.raise_for_status()
            client.get("/api/runs/live-create/events")
            run = client.get("/api/runs/live-create").json()
            todos = client.get("/api/todos").json()
            print(
                json.dumps(
                    {
                        "provider": "real Bailian",
                        "proxy": "disabled",
                        "status": run["status"],
                        "model_calls": run["model_calls"],
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "reply": run["reply"],
                        "todos": todos,
                    },
                    ensure_ascii=False,
                )
            )
            assert run["status"] == "completed" and len(todos) == 2
            assert {t["title"] for t in todos} == {"学习 Python 生成器", "整理书桌"}
            assert len(run["todo_ids"]) == 2


if __name__ == "__main__":
    main()

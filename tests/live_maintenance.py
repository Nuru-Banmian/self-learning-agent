"""Opt-in real model verification at HTTP/SSE seams, with an isolated database."""

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit


def main():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="assistant-maintenance-") as directory:
        settings = Settings(db_path=Path(directory) / "live.sqlite3")
        if not settings.dashscope_api_key.get_secret_value():
            raise SystemExit("SKIP: backend credential is not configured")
        with TestClient(create_app(settings)) as c:
            session = c.post("/api/sessions").json()["id"]

            def check(content, request_id):
                run, events = submit(c, content, request_id, session)
                print(
                    json.dumps(
                        {
                            "step": request_id,
                            "status": run["status"],
                            "model_calls": run["model_calls"],
                            "reply": run["reply"],
                            "saved": "event: saved" in events,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert run["status"] == "completed"
                return run

            created = check("请记录今天学习 Python 生成器", "create")
            assert len(created["todo_ids"]) == 1
            changed = check("把学习 Python 生成器改到明天", "change")
            assert changed["todo_ids"] == created["todo_ids"]
            renamed = check("把学习 Python 生成器标题改为练习 Python 生成器", "rename")
            assert renamed["todo_ids"] == created["todo_ids"]
            before = c.get("/api/todos").json()
            check("今天我该干什么？", "plan")
            assert c.get("/api/todos").json() == before
            suggestions = c.get(f"/api/sessions/{session}/suggestions").json()
            assert suggestions
            accepted = check(f"把建议 {suggestions[0]['id']} 加入待办", "accept")
            assert len(accepted["todo_ids"]) == 1
            completed = check(f"把待办 {created['todo_ids'][0]} 标记完成", "complete")
            assert completed["todo_ids"] == created["todo_ids"]
            queried = check("查询我的待办", "query")
            assert (
                "已完成" in queried["reply"]
                and "练习 Python 生成器" in queried["reply"]
            )
            assert c.get("/api/todos").json()[0]["status"] == "completed"
            print(
                json.dumps(
                    {
                        "provider": "real Bailian",
                        "proxy": "disabled",
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "todo_count": len(c.get("/api/todos").json()),
                        "result": "PASS",
                    }
                )
            )


if __name__ == "__main__":
    main()

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from tests.test_chat import tool_response
from tests.test_maintenance import action, operation_response

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


@contextmanager
def server_process(port, db_path, provider_url, api_key="fixture-key"):
    environment = os.environ | {
        "DB_PATH": str(db_path),
        "DASHSCOPE_API_KEY": api_key,
        "DASHSCOPE_BASE_URL": provider_url,
        "PYTHONUTF8": "1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
        ) as c:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("application process exited during startup")
                try:
                    if c.get("/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("application startup deadline exceeded")
            yield c, process
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def test_real_process_restart_keeps_todos_messages_requests_and_replays(tmp_path):
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = tool_response([{"title": "整理书桌", "date_text": None}])
            if request["messages"][-1]["content"] == "今天我该干什么":
                payload = operation_response(
                    "plan_day", {"suggestions": ["整理学习笔记"]}
                )
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{provider.server_port}/v1"
    port = free_port()
    database = tmp_path / "restart.db"
    try:
        with server_process(port, database, url) as (c, process):
            original_pid = process.pid
            session = c.post("/api/sessions").json()["id"]
            body = {"request_id": "persisted-request", "content": "请记录整理书桌"}
            c.post(f"/api/sessions/{session}/messages", json=body).raise_for_status()
            c.get("/api/runs/persisted-request/events").raise_for_status()
            before = c.get("/api/todos").json()
            assert len(before) == 1
            assert c.get("/api/runs/persisted-request").json()["status"] == "completed"
            action(
                c,
                session,
                "edit",
                "update_todo",
                {"todo_id": before[0]["id"], "title": "整理书架"},
            )
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": "plan", "content": "今天我该干什么"},
            ).raise_for_status()
            c.get("/api/runs/plan/events")
            suggestions = c.get(f"/api/sessions/{session}/suggestions").json()
            action(
                c,
                session,
                "accept",
                "accept_suggestion",
                {"suggestion_id": suggestions[0]["id"]},
            )
            action(c, session, "done", "complete_todo", {"todo_id": before[0]["id"]})
            before = c.get("/api/todos").json()
            assert len(before) == 2 and before[0]["status"] == "completed"
        assert process.poll() is not None
        # Restart with no model credential: reads and replay must still work.
        with server_process(port, database, url, api_key="") as (c, restarted):
            assert restarted.pid != original_pid
            assert c.get("/api/todos").json() == before
            assert len(c.get(f"/api/sessions/{session}").json()["messages"]) == 10
            again, events = action(
                c,
                session,
                "accept-after-restart",
                "accept_suggestion",
                {"suggestion_id": suggestions[0]["id"]},
            )
            assert not again["todo_ids"] and "event: saved" not in events
            assert (
                c.get(f"/api/sessions/{session}/suggestions").json()[0]["todo_id"]
                == before[1]["id"]
            )
            replay = c.post(f"/api/sessions/{session}/messages", json=body).json()
            assert replay["todo_ids"] == [before[0]["id"]]
            new_session = c.post("/api/sessions").json()["id"]
            assert new_session != session
            assert c.get("/api/todos").json() == before
            c.post(
                f"/api/sessions/{new_session}/messages",
                json={
                    "request_id": "unavailable-model",
                    "content": "请记录整理书桌",
                },
            )
            c.get("/api/runs/unavailable-model/events")
            assert c.get("/api/runs/unavailable-model").json()["status"] == "failed"
            assert c.get("/api/todos").json() == before
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)

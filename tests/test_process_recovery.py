"""Kill real Uvicorn processes; only the external provider is simulated."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_chat import tool_response
from tests.test_maintenance import operation_response
from tests.test_memory import candidate
from tests.test_process import free_port, server_process


@contextmanager
def controlled_provider(*, learning=False):
    gate = threading.Event()
    entered = threading.Event()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            content = body["messages"][-1]["content"]
            if "response_format" in body:
                source = json.loads(content)
                payload = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"candidates": [candidate(source)]}
                                )
                            }
                        }
                    ]
                }
            else:
                entered.set()
                gate.wait(10)
                title = "整理书架" if "书架" in content else "整理书桌"
                payload = (
                    operation_response(
                        "answer_question",
                        {"reply": "可以从一个小例子开始。", "memory_usage": []},
                    )
                    if learning
                    else tool_response([{"title": title, "date_text": None}])
                )
            encoded = json.dumps(payload).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", gate, entered, requests
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def wait_terminal(client, run_id):
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] not in ("running", "queued"):
            return run
        time.sleep(0.05)
    raise AssertionError("request did not terminate")


@pytest.mark.parametrize("after_commit", [False, True])
def test_kill_before_or_after_commit_and_retry_via_public_api(tmp_path, after_commit):
    with controlled_provider() as (url, gate, entered, requests):
        port = free_port()
        db = tmp_path / "crash.db"
        body = {"request_id": "crash", "content": "请记录整理书桌"}
        with server_process(port, db, url) as (c, process):
            session = c.post("/api/sessions").json()["id"]
            messages = f"/api/sessions/{session}/messages"
            c.post(messages, json=body).raise_for_status()
            assert entered.wait(5)
            with c.stream("GET", "/api/runs/crash/events") as stream:
                lines = stream.iter_lines()
                assert next(lines).startswith("id:")
                if after_commit:
                    gate.set()
                    # Stop consuming before reply/terminal delivery; writes are real.
                    for line in lines:
                        if line == "event: saved":
                            break
                    else:
                        raise AssertionError("missing committed save event")
                    assert len(c.get("/api/todos").json()) == 1
                else:
                    assert c.get("/api/todos").json() == []
                    queued = c.post(
                        messages,
                        json={"request_id": "queued", "content": "请记录整理书架"},
                    )
                    assert queued.json()["status"] == "queued"
                process.kill()
                process.wait(5)
        gate.set()
        with server_process(port, db, url) as (c, restarted):
            assert restarted.pid != process.pid
            recovered = c.get("/api/runs/crash").json()
            assert recovered["status"] == ("completed" if after_commit else "failed")
            assert recovered["events"][-1]["kind"] == "terminal"
            assert recovered["messages"][-1]["content"] == recovered["reply"]
            if not after_commit:
                assert recovered["error"] == "interrupted"
                queued = c.get("/api/runs/queued").json()
                assert queued["status"] == "failed" and queued["error"] == "interrupted"
            retry = c.post("/api/runs/crash/retry").json()
            done = wait_terminal(c, retry["id"])
            assert done["status"] == "completed"
            assert c.post("/api/runs/crash/retry").json()["id"] == done["id"]
            assert len(c.get("/api/todos").json()) == 1
            assert len(requests) == (1 if after_commit else 2)
            # Ordinary replay never executes the original message again.
            assert c.post(messages, json=body).json()["status"] == recovered["status"]
            sequence = [e["seq"] for e in done["events"]]
            assert sequence == sorted(set(sequence))
            cursor = next(e["seq"] for e in done["events"] if e["kind"] == "saved")
            replay = c.get(
                f"/api/runs/{done['id']}/events", headers={"Last-Event-ID": str(cursor)}
            ).text
            assert "event: saved" not in replay and "event: terminal" in replay
            assert (
                c.get(
                    f"/api/runs/{done['id']}/events",
                    headers={"Last-Event-ID": str(sequence[-1])},
                ).text
                == ""
            )


def test_socket_disconnect_and_simultaneous_duplicate_submissions(tmp_path):
    with controlled_provider() as (url, gate, entered, requests):
        with server_process(free_port(), tmp_path / "disconnect.db", url) as (c, _):
            session = c.post("/api/sessions").json()["id"]
            messages = f"/api/sessions/{session}/messages"
            body = {"request_id": "same", "content": "请记录整理书桌"}
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(
                    pool.map(lambda _: c.post(messages, json=body), range(6))
                )
            assert all(r.status_code == 202 for r in responses)
            assert entered.wait(5)
            with c.stream("GET", "/api/runs/same/events") as stream:
                assert next(stream.iter_lines()).startswith("id:")
            # TCP/SSE connection is now closed while the application is still running.
            second = c.post(
                messages, json={"request_id": "next", "content": "请记录整理书架"}
            )
            assert second.json()["status"] == "queued"
            other = c.post("/api/sessions").json()["id"]
            assert c.get(f"/api/sessions/{other}").json()["messages"] == []
            assert c.get("/api/todos").json() == []
            gate.set()
            assert wait_terminal(c, "next")["status"] == "completed"
            assert len(requests) == 2
            assert [t["title"] for t in c.get("/api/todos").json()] == [
                "整理书桌",
                "整理书架",
            ]
            assert "整理书桌" in requests[1]["messages"][0]["content"]
            assert len(c.get(f"/api/sessions/{session}").json()["messages"]) == 4


def test_kill_after_learning_commit_keeps_receipt_and_retry_skips_learning(tmp_path):
    with controlled_provider(learning=True) as (url, gate, entered, requests):
        port = free_port()
        db = tmp_path / "learning-crash.db"
        with server_process(port, db, url) as (c, process):
            session = c.post("/api/sessions").json()["id"]
            c.post(
                f"/api/sessions/{session}/messages",
                json={
                    "request_id": "learning-crash",
                    "content": "我喜欢优先阅读官方资料",
                },
            ).raise_for_status()
            assert entered.wait(5)
            saved = c.get("/api/memories").json()
            assert len(saved) == 1
            run = c.get("/api/runs/learning-crash").json()
            assert run["status"] == "running"
            assert any(e["kind"] == "memory_saved" for e in run["events"])
            process.kill()
            process.wait(5)
        gate.set()
        with server_process(port, db, url) as (c, _):
            interrupted = c.get("/api/runs/learning-crash").json()
            assert interrupted["status"] == "partial"
            assert interrupted["error"] == "interrupted" and interrupted["retryable"]
            assert c.get("/api/memories").json() == saved
            retry = c.post("/api/runs/learning-crash/retry").json()
            done = wait_terminal(c, retry["id"])
            assert done["status"] == "completed"
            assert c.get("/api/memories").json() == saved
            assert sum("response_format" in r for r in requests) == 1
            assert done["model_calls"] == 1

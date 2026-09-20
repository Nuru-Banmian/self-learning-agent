import asyncio
import json
import sqlite3
import time

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import make_client, submit, tool_response

ITEMS = [
    {"title": "学习 Python", "date_text": "明天"},
    {"title": "搭首页", "date_text": "明天"},
]
CONTENT = "明天我要学习 Python，还要搭首页"


def test_retry_same_id_replays_but_new_id_creates_and_conflicts_are_rejected(tmp_path):
    with make_client(tmp_path, tool_response(ITEMS)) as c:
        session = c.post("/api/sessions").json()["id"]
        run, events = submit(c, CONTENT, session=session)
        replay, replay_events = submit(c, CONTENT, session=session)
        assert replay == run and replay_events == events
        assert len(c.get("/api/todos").json()) == 2
        conflict = c.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "request-1",
                "content": "其他内容",
            },
        )
        assert conflict.status_code == 409
        submit(c, CONTENT, "request-2", session)
        assert len(c.get("/api/todos").json()) == 4
        assert len(c.get(f"/api/sessions/{session}").json()["messages"]) == 4


def test_second_insert_failure_rolls_back_entire_batch_and_success_evidence(tmp_path):
    with make_client(tmp_path, tool_response(ITEMS)) as c:
        # Fault injection at the storage boundary; all assertions use public HTTP.
        with sqlite3.connect(tmp_path / "test.db") as db:
            db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON todos
                          WHEN NEW.title='搭首页'
                          BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END""")
        run, events = submit(c, CONTENT)
        assert run["status"] == "failed" and run["error"] == "storage"
        assert run["todo_ids"] == []
        assert c.get("/api/todos").json() == []
        assert "event: saved" not in events
        assert "已保存" not in run["reply"]


def test_provider_failure_has_bounded_retries_and_redacted_errors(tmp_path):
    attempts = []

    def provider(request):
        attempts.append(request)
        return httpx.Response(503, text="sensitive-provider-response-test-secret")

    settings = Settings(
        db_path=tmp_path / "test.db",
        dashscope_api_key="test-secret",
        model_retries=3,
        max_model_calls=2,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        run, events = submit(c, CONTENT)
        assert run["status"] == "failed" and run["model_calls"] == 2
        assert len(attempts) == 2
        assert "test-secret" not in json.dumps(run) + events
        assert c.get("/api/todos").status_code == 200


def test_total_deadline_and_session_serialization(tmp_path):
    async def provider(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=tool_response(ITEMS))

    settings = Settings(
        db_path=tmp_path / "test.db",
        dashscope_api_key="test-secret",
        run_timeout_seconds=0.1,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        url = f"/api/sessions/{session}/messages"
        body = {"request_id": "slow", "content": CONTENT}
        assert c.post(url, json=body).status_code == 202
        assert c.post(url, json=body).status_code == 202
        queued = c.post(url, json=body | {"request_id": "concurrent"})
        assert queued.status_code == 202 and queued.json()["status"] == "queued"
        c.get("/api/runs/slow/events")
        run = c.get("/api/runs/slow").json()
        assert run["status"] == "failed" and run["error"] == "timeout"
        assert c.get("/api/todos").json() == []


def test_plain_model_claim_is_not_a_save_receipt(tmp_path):
    payload = {"choices": [{"message": {"content": "已经保存待办了"}}]}
    with make_client(tmp_path, payload) as c:
        run, events = submit(c, "请记录整理书桌")
        assert c.get("/api/todos").json() == []
        assert "已经保存" not in run["reply"]
        assert "未新增" in run["reply"]
        assert "event: saved" not in events


def test_protocol_disconnect_terminates_run_and_releases_session(tmp_path):
    def provider(request):
        raise httpx.RemoteProtocolError("provider disconnected test-secret")

    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        dashscope_api_key="test-secret",
        model_retries=0,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        url = f"/api/sessions/{session}/messages"
        c.post(url, json={"request_id": "broken", "content": CONTENT})
        for _ in range(20):
            run = c.get("/api/runs/broken").json()
            if run["status"] != "running":
                break
            time.sleep(0.01)
        assert run["status"] == "failed"
        assert "test-secret" not in json.dumps(run)
        assert "event: terminal" in c.get("/api/runs/broken/events").text
        assert (
            c.post(url, json={"request_id": "next", "content": CONTENT}).status_code
            == 202
        )

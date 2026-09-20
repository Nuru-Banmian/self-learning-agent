import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import tool_response
from tests.test_maintenance import operation_response
from tests.test_memory import candidate


def test_partial_result_with_committed_suggestions_is_replayed_without_writes(tmp_path):
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=operation_response("plan_day", {"suggestions": ["阅读一个官方示例"]}),
        )

    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "partial.db",
        dashscope_api_key="fixture",
        model_retries=0,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "partial", "content": "我喜欢官方资料"},
        )
        c.get("/api/runs/partial/events")
        run = c.get("/api/runs/partial").json()
        assert run["status"] == "partial"
        assert not run["retryable"]
        assert c.post("/api/runs/partial/retry").json() == run
        assert len(c.get(f"/api/sessions/{session}/suggestions").json()) == 1
        assert c.get("/api/todos").json() == [] and len(calls) == 2


def test_retry_after_learning_commit_does_not_repeat_or_restore_deleted_memory(
    tmp_path,
):
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"candidates": [candidate(source)]}
                                )
                            }
                        }
                    ]
                },
            )
        if len(requests) == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=operation_response(
                "answer_question",
                {"reply": "可以从一个小例子开始。", "memory_usage": []},
            ),
        )

    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "learning.db",
        dashscope_api_key="fixture",
        model_retries=0,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        url = f"/api/sessions/{session}/messages"
        c.post(url, json={"request_id": "learn", "content": "我喜欢优先阅读官方资料"})
        c.get("/api/runs/learn/events")
        original = c.get("/api/runs/learn").json()
        assert original["status"] == "partial" and original["retryable"]
        memory = c.get("/api/memories").json()[0]
        c.post(
            url,
            json={
                "request_id": "delete",
                "content": "删除记忆",
                "action": {
                    "tool": "delete_memory",
                    "arguments": {
                        "memory_id": memory["id"],
                        "expected_source_id": memory["source"]["message_id"],
                    },
                },
            },
        )
        c.get("/api/runs/delete/events")
        with ThreadPoolExecutor(max_workers=4) as pool:
            retries = list(
                pool.map(lambda _: c.post("/api/runs/learn/retry").json(), range(4))
            )
        assert len({r["id"] for r in retries}) == 1
        retry_id = retries[0]["id"]
        c.get(f"/api/runs/{retry_id}/events")
        run = c.get(f"/api/runs/{retry_id}").json()
        assert run["status"] == "completed"
        assert run["memory"]["saved_ids"] == original["memory"]["saved_ids"]
        assert c.get("/api/memories").json() == []
        assert sum("response_format" in request for request in requests) == 1
        assert all(e["kind"] != "memory_saved" for e in run["events"])


def test_unexpected_adapter_failure_is_terminal_redacted_and_releases_queue(tmp_path):
    async def provider(request):
        await asyncio.sleep(0.1)
        raise RuntimeError("private-api-key")

    settings = Settings(
        _env_file=None, db_path=tmp_path / "unexpected.db", dashscope_api_key="fixture"
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        for request_id in ("first", "next"):
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": request_id, "content": "请记录整理书桌"},
            )
        c.get("/api/runs/next/events")
        for request_id in ("first", "next"):
            run = c.get(f"/api/runs/{request_id}").json()
            assert run["status"] == "failed" and run["error"] == "internal"
            assert run["events"][-1]["kind"] == "terminal"
            assert "private-api-key" not in json.dumps(run)
        assert c.get("/api/todos").json() == []


def test_explicit_retry_is_idempotent_and_preserves_failed_attempt(tmp_path):
    attempts = []

    async def provider(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, text="private-fixture-secret")
        await asyncio.sleep(0.1)
        return httpx.Response(
            200, json=tool_response([{"title": "整理书桌", "date_text": None}])
        )

    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "retry.db",
        dashscope_api_key="fixture",
        model_retries=0,
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        url = f"/api/sessions/{session}/messages"
        body = {"request_id": "original", "content": "请记录整理书桌"}
        c.post(url, json=body).raise_for_status()
        c.get("/api/runs/original/events")
        before = c.get("/api/runs/original").json()
        assert before["status"] == "failed"
        retry = c.post("/api/runs/original/retry")
        assert retry.status_code == 202
        new_id = retry.json()["id"]
        assert new_id != "original" and retry.json()["retry_of"] == "original"
        assert c.post("/api/runs/original/retry").json()["id"] == new_id
        c.get(f"/api/runs/{new_id}/events")
        after = c.get(f"/api/runs/{new_id}").json()
        assert after["status"] == "completed" and not after["retryable"]
        assert c.post(f"/api/runs/{new_id}/retry").json() == after
        assert c.post(url, json=body).json()["events"] == before["events"]
        original = c.get("/api/runs/original").json()
        assert original["status"] == "failed" and original["retry_run_id"] == new_id
        assert original["events"] == before["events"]
        assert after["message_id"] == original["message_id"]
        assert len(c.get("/api/todos").json()) == 1 and len(attempts) == 2
        assert "private-fixture-secret" not in json.dumps([original, after])
        assert c.post("/api/runs/missing/retry").status_code == 404


def test_queued_turn_waits_for_committed_previous_turn_and_status_is_durable(tmp_path):
    observed = []

    async def provider(request):
        body = json.loads(request.content)
        observed.append(body)
        await asyncio.sleep(0.15)
        title = "整理书桌" if len(observed) == 1 else "整理书架"
        return httpx.Response(
            200, json=tool_response([{"title": title, "date_text": None}])
        )

    settings = Settings(
        _env_file=None, db_path=tmp_path / "runs.db", dashscope_api_key="fixture"
    )
    with TestClient(create_app(settings, transport=httpx.MockTransport(provider))) as c:
        session = c.post("/api/sessions").json()["id"]
        url = f"/api/sessions/{session}/messages"
        first = c.post(url, json={"request_id": "first", "content": "请记录整理书桌"})
        assert first.status_code == 202
        second = c.post(url, json={"request_id": "second", "content": "请记录整理书架"})
        assert second.status_code == 202
        assert second.json()["status"] == "queued"
        assert second.json()["queue_position"] == 1
        assert c.get("/api/todos").json() == []
        assert c.get(f"/api/sessions/{session}").json()["run_ids"] == [
            "first",
            "second",
        ]
        stream = c.get("/api/runs/second/events").text
        result = c.get("/api/runs/second").json()
        assert result["status"] == "completed"
        assert [t["title"] for t in c.get("/api/todos").json()] == [
            "整理书桌",
            "整理书架",
        ]
        assert "整理书桌" in observed[1]["messages"][0]["content"]
        assert result["messages"][0]["content"] == "请记录整理书架"
        assert result["messages"][-1]["content"] == result["reply"]
        assert [e["kind"] for e in result["events"]][-3:] == [
            "saved",
            "reply",
            "terminal",
        ]
        assert "event: queued" in stream and "event: terminal" in stream

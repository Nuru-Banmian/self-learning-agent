import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def tool_response(items):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "create_todos",
                                "arguments": json.dumps({"items": items}),
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_chat_creates_real_todos_and_reads_them_in_new_session(tmp_path):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=tool_response(
                [
                    {"title": "学习 Python", "date_text": "明天"},
                    {"title": "搭首页", "date_text": "明天"},
                ]
            ),
        )

    app = create_app(
        Settings(db_path=tmp_path / "test.db", dashscope_api_key="test-secret"),
        transport=httpx.MockTransport(provider),
        clock=lambda: datetime(2026, 9, 20, 16, 5, tzinfo=UTC),
    )
    with TestClient(app) as client:
        session = client.post("/api/sessions").json()["id"]
        response = client.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "request-1",
                "content": "明天我要学习 Python，还要搭首页",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        events = client.get(f"/api/runs/{run_id}/events").text
        assert "event: saved" in events
        assert "event: terminal" in events
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "completed"
        assert len(run["todo_ids"]) == 2
        client.post("/api/sessions")
        todos = client.get("/api/todos").json()
        assert [t["title"] for t in todos] == ["学习 Python", "搭首页"]
        assert all(t["scheduled_date"] == "2026-09-22" for t in todos)
        assert all(t["status"] == "pending" for t in todos)
        assert all(
            t["source"]["content"] == "明天我要学习 Python，还要搭首页" for t in todos
        )
        assert {t["id"] for t in todos} == set(run["todo_ids"])
        messages = client.get(f"/api/sessions/{session}").json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "2026-09-22" in messages[-1]["content"]
        assert requests[0]["model"] == "qwen3.7-plus-2026-05-26"
        assert requests[0]["enable_thinking"] is False
        assert "test-secret" not in json.dumps(requests)


def make_client(tmp_path, payload, **settings):
    return TestClient(
        create_app(
            Settings(
                db_path=tmp_path / "test.db",
                dashscope_api_key="test-secret",
                **settings,
            ),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
            clock=lambda: datetime(2026, 9, 20, 16, 5, tzinfo=UTC),
        )
    )


def submit(client, content, request_id="request-1", session=None):
    session = session or client.post("/api/sessions").json()["id"]
    response = client.post(
        f"/api/sessions/{session}/messages",
        json={
            "request_id": request_id,
            "content": content,
        },
    )
    assert response.status_code == 202
    events = client.get(f"/api/runs/{request_id}/events").text
    return client.get(f"/api/runs/{request_id}").json(), events


@pytest.mark.parametrize(
    ("content", "title", "date_text", "expected"),
    [
        ("请记录整理书桌", "整理书桌", None, None),
        ("请记录2026-10-01整理书桌", "整理书桌", "2026-10-01", "2026-10-01"),
        ("请记录9月25日整理书桌", "整理书桌", "9月25日", "2026-09-25"),
    ],
)
def test_dates_are_resolved_by_application(
    tmp_path, content, title, date_text, expected
):
    with make_client(
        tmp_path, tool_response([{"title": title, "date_text": date_text}])
    ) as c:
        run, _ = submit(c, content)
        assert run["status"] == "completed"
        assert c.get("/api/todos").json()[0]["scheduled_date"] == expected


@pytest.mark.parametrize(
    ("content", "item"),
    [
        ("请记录过几天整理书桌", {"title": "整理书桌", "date_text": "过几天"}),
        ("请记录过几天整理书桌", {"title": "整理书桌", "date_text": None}),
        ("请记录明天整理书桌", {"title": "整理书桌", "date_text": "2026-09-22"}),
        ("请记录整理书桌", {"title": "购买电脑", "date_text": None}),
        ("建议我明天整理书桌吗？", {"title": "整理书桌", "date_text": "明天"}),
        ("不要记录明天整理书桌", {"title": "整理书桌", "date_text": "明天"}),
        ("如果明天我要整理书桌该怎么做", {"title": "整理书桌", "date_text": "明天"}),
        ("解释这句话：明天我要整理书桌", {"title": "整理书桌", "date_text": "明天"}),
        (
            "请记录明天整理书桌",
            {"title": "整理书桌", "date_text": "明天", "role": "admin"},
        ),
    ],
)
def test_untrusted_tool_proposals_need_source_date_and_write_permission(
    tmp_path, content, item
):
    with make_client(tmp_path, tool_response([item])) as c:
        run, events = submit(c, content)
        assert c.get("/api/todos").json() == []
        assert run["todo_ids"] == []
        assert "event: saved" not in events
        assert "请" in run["reply"]


def test_model_cannot_swap_dates_between_two_source_items(tmp_path):
    payload = tool_response(
        [
            {"title": "整理书桌", "date_text": "后天"},
            {"title": "学习 Python", "date_text": "明天"},
        ]
    )
    with make_client(tmp_path, payload) as c:
        run, _ = submit(c, "请记录明天整理书桌，后天学习 Python")
        assert c.get("/api/todos").json() == []
        assert "请" in run["reply"]

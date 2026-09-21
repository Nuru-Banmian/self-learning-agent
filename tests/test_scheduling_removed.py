"""Retired scheduling still rejects old clients through HTTP and SSE."""

import pytest

from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_roadmaps import REQUEST, roadmap_client


def test_old_schedule_action_fails_without_model_or_business_writes(tmp_path):
    with roadmap_client(tmp_path, []) as client:
        generated, _ = submit(client, REQUEST)
        route = generated["roadmap"]
        failed, events = action(
            client,
            generated["session_id"],
            "old-preview",
            "preview_roadmap_schedule",
            {
                "roadmap_id": route["id"],
                "expected_version": route["version"],
                "start_text": "2026-10-01",
                "daily_minutes": 30,
            },
        )
        assert failed["status"] == "failed"
        assert failed["error"] == "roadmap_scheduling_removed"
        assert failed["model_calls"] == 0
        assert "roadmap_scheduling_removed" in events
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route
        assert client.get("/api/todos").json() == []


@pytest.mark.parametrize(
    "content",
    [
        "请将这条路线的第一个节点明天加入待办",
        "请安排明天学习这条路线的第一个节点",
        "请添加明天学习这条路线的第一个节点",
    ],
)
def test_node_join_cannot_fall_back_to_dated_ordinary_creation(tmp_path, content):
    import httpx

    from tests.test_chat import tool_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        if content in request.content.decode():
            return httpx.Response(
                200,
                json=tool_response(
                    [{"title": "学习这条路线的第一个节点", "date_text": "明天"}]
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        generated, _ = submit(client, REQUEST)
        result, _ = submit(
            client,
            content,
            "join-with-date",
            generated["session_id"],
        )
        assert client.get("/api/todos").json() == []
        assert "面板" in result["reply"]


@pytest.mark.parametrize(
    ("content", "title"),
    [
        ("记录明天整理学习路线节点说明", "整理学习路线节点说明"),
        (
            "不要把这条路线的节点加入待办，记录明天买牛奶",
            "买牛奶",
        ),
    ],
)
def test_ordinary_todo_is_not_blocked_by_node_words(tmp_path, content, title):
    import httpx

    from tests.test_chat import tool_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        if content in request.content.decode():
            return httpx.Response(
                200, json=tool_response([{"title": title, "date_text": "明天"}])
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        generated, _ = submit(client, REQUEST)
        route = generated["roadmap"]
        result, events = submit(client, content, "ordinary", generated["session_id"])
        assert result["status"] == "completed"
        assert result["model_calls"] > 0
        assert "面板" not in result["reply"]
        todos = client.get("/api/todos").json()
        if content.startswith("不要"):
            # Existing ordinary-todo authorization requires clarification for
            # mixed negation; route routing must not replace that decision.
            assert todos == []
            assert "event: saved" not in events
        else:
            assert len(todos) == 1
            assert todos[0]["title"] == title
            assert todos[0]["scheduled_date"] is not None
            assert "event: saved" in events
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route

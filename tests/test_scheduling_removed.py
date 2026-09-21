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

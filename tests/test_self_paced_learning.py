"""Self-paced intake through public HTTP/SSE; SQLite edits seed old data only."""

import json
import sqlite3

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import operation_response
from tests.test_roadmaps import roadmap_client, roadmap_provider


def self_paced_provider(requests, *, missing_goal=False):
    base = roadmap_provider(requests)

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            != "plan_learning_roadmap"
        ):
            return base(request)
        context = json.loads(body["messages"][1]["content"])
        selected_id = context.get("selected_learning_request_id")
        selected = next(
            (r for r in context["learning_requests"] if r["id"] == selected_id), None
        )
        source = body["messages"][-1]["content"]
        if selected:
            source += "\n" + "\n".join(m["content"] for m in selected["messages"])
        topic = "SQLite" if "SQLite" in source else "Redis"
        return httpx.Response(
            200,
            json=operation_response(
                "plan_learning_roadmap",
                {
                    "intake": {
                        "request_id": selected_id,
                        "topic": topic,
                        "goal": "实现缓存"
                        if "实现缓存" in source and not missing_goal
                        else None,
                        "background": "Python 基础"
                        if "Python 基础" in source
                        else None,
                        "time_budget": None,
                        "needed_fields": ["goal", "background", "time_budget"],
                    },
                    "query": f"{topic} cache documentation",
                    "todo_ids": [],
                    "memory_ids": [],
                    "read_body": False,
                },
            ),
        )

    return provider


def seed_legacy_requests(tmp_path, *, missing_goal):
    # Public writes create all normal ownership/audit records. Editing only the
    # stored clarification represents a pre-upgrade time-gated database.
    with roadmap_client(tmp_path, [], self_paced_provider([], missing_goal=True)) as c:
        for topic in ("Redis", "SQLite"):
            content = f"我想学习 {topic}，有 Python 基础"
            if not missing_goal:
                content += "，目标是实现缓存"
            submit(c, content, topic)
        pending = c.get("/api/learning-requests").json()
    with sqlite3.connect(tmp_path / "roadmaps.db") as db:
        for request in pending:
            content = {
                k: v
                for k, v in request.items()
                if k not in ("id", "version", "roadmap_id")
            }
            if not missing_goal:
                content["known"]["goal"] = "实现缓存"
                content["questions"] = []
            content["questions"].append("每次或每周可以投入多少时间？")
            db.execute(
                "UPDATE learning_requests SET content=? WHERE id=?",
                (json.dumps(content, ensure_ascii=False), request["id"]),
            )
    return pending


@pytest.mark.parametrize("missing_goal", [False, True])
def test_legacy_questions_project_without_calls_or_changing_user_input(
    tmp_path, missing_goal
):
    pending = seed_legacy_requests(tmp_path, missing_goal=missing_goal)
    calls = []

    def forbidden_provider(request):
        calls.append(str(request.url))
        raise AssertionError("Reading an old request must not call a provider")

    with roadmap_client(tmp_path, [], forbidden_provider) as c:
        for _ in range(2):
            current = c.get("/api/learning-requests").json()
            assert len(current) == 2
            for original, request in zip(pending, current, strict=True):
                assert request["questions"] == (
                    ["希望学完后能做什么？"] if missing_goal else []
                )
                assert request["version"] == original["version"]
                assert request["messages"] == original["messages"]
                assert request["roadmap_id"] is None
            assert c.get("/api/roadmaps").json() == []
            assert c.get("/api/todos").json() == []
    assert calls == []


@pytest.mark.parametrize("content", [None, ""])
def test_old_time_only_request_continues_selected_goal_without_a_new_answer(
    tmp_path, content
):
    seed_legacy_requests(tmp_path, missing_goal=False)
    requests = []
    with roadmap_client(tmp_path, requests, self_paced_provider(requests)) as c:
        before = c.get("/api/learning-requests").json()
        selected = before[1]
        session = c.post("/api/sessions").json()["id"]
        payload = {
            "request_id": "selected-continuation",
            "action": {
                "tool": "continue_learning",
                "arguments": {"request_id": selected["id"]},
            },
        }
        if content is not None:
            payload["content"] = content
        response = c.post(f"/api/sessions/{session}/messages", json=payload)
        assert response.status_code == 202, response.text
        events = c.get("/api/runs/selected-continuation/events").text
        run = c.get("/api/runs/selected-continuation").json()
        assert run["roadmap"], run
        assert "event: roadmap_saved" in events
        assert "SQLite" in run["roadmap"]["request"]
        assert "Redis" not in run["roadmap"]["request"]
        current = c.get("/api/learning-requests").json()
        assert current[0] == before[0]
        assert (
            current[1]["messages"][: len(selected["messages"])] == selected["messages"]
        )
        assert current[1]["questions"] == []
        assert current[1]["roadmap_id"] == run["roadmap"]["id"]
        assert c.get("/api/todos").json() == []
        call_count = len(requests)
        c.post(f"/api/sessions/{session}/messages", json=payload).raise_for_status()
        assert c.post("/api/runs/selected-continuation/retry").json()["id"] == run["id"]
        assert len(requests) == call_count
        assert len(c.get("/api/roadmaps").json()) == 1


def test_continue_with_goal_still_missing_only_asks_for_that_goal(tmp_path):
    seed_legacy_requests(tmp_path, missing_goal=True)
    requests = []
    with roadmap_client(tmp_path, requests, self_paced_provider(requests)) as c:
        before = c.get("/api/learning-requests").json()
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "missing-goal",
                "action": {
                    "tool": "continue_learning",
                    "arguments": {"request_id": before[0]["id"]},
                },
            },
        ).raise_for_status()
        c.get("/api/runs/missing-goal/events")
        run = c.get("/api/runs/missing-goal").json()
        current = c.get("/api/learning-requests").json()
        assert current[0]["questions"] == ["希望学完后能做什么？"]
        assert current[1] == before[1]
        assert "时间" not in run["reply"]
        assert not run["roadmap"]
        assert not any(url.endswith("/search/unified") for url, _ in requests)
        assert c.get("/api/todos").json() == []

"""Parent-spec journey across slice boundaries, via HTTP/SSE and real SQLite."""

from tests.test_chat import submit
from tests.test_learning_requests import memory_intake_provider
from tests.test_maintenance import action
from tests.test_roadmap_batch import selection
from tests.test_roadmap_revisions import revision_args
from tests.test_roadmap_schedule import preview_args
from tests.test_roadmaps import roadmap_client


def test_memory_clarification_selection_manual_date_completion_revision_and_restart(
    tmp_path,
):
    requests = []
    with roadmap_client(tmp_path, requests, memory_intake_provider(requests)) as c:
        submit(c, "我有 Python 基础", "background")
        initial, _ = submit(c, "我想学习 Redis，每次30分钟", "start")
        assert initial["memory"]["usage"]
        pending = c.get("/api/learning-requests").json()
        assert pending[0]["questions"] == ["希望学完后能做什么？"]
        assert c.get("/api/roadmaps").json() == []
        assert c.get("/api/todos").json() == []

    with roadmap_client(tmp_path, requests, memory_intake_provider(requests)) as c:
        assert c.get("/api/learning-requests").json() == pending
        generated, events = submit(c, "目标是实现缓存", "goal")
        assert "event: roadmap_saved" in events
        route = generated["roadmap"]
        assert any("/search/unified" in url for url, _ in requests)
        assert c.get("/api/todos").json() == []
        session = generated["session_id"]
        accepted, _ = action(
            c,
            session,
            "select",
            "accept_roadmap_nodes",
            selection(route, [route["nodes"][0]["id"]]),
        )
        route = accepted["roadmap"]
        assert len(c.get("/api/todos").json()) == 1
        assert route["nodes"][0]["todo"]["scheduled_date"] is None

    # All later maintenance must remain available without either provider.
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        session = c.post("/api/sessions").json()["id"]
        before = c.get("/api/todos").json()
        preview, _ = action(
            c, session, "schedule", "preview_roadmap_schedule", preview_args(route)
        )
        assert c.get("/api/todos").json() == before
        assert preview["error"] == "roadmap_scheduling_removed"
        changed, _ = action(
            c,
            session,
            "manual-date",
            "update_todo",
            {"todo_id": route["nodes"][0]["todo_id"], "date_text": "2026-10-01"},
        )
        assert changed["status"] == "completed"
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        accepted, _ = action(
            c, session, "all", "accept_roadmap_nodes", selection(route)
        )
        route = accepted["roadmap"]
        assert [n["todo"]["scheduled_date"] for n in route["nodes"]] == [
            "2026-10-01",
            None,
        ]
        draft = revision_args(route)
        preview, _ = action(
            c, session, "old-preview", "preview_roadmap_revision", draft
        )
        old = preview["roadmap"]["revision_proposals"][0]
        other = c.post("/api/sessions").json()["id"]
        completed, _ = action(
            c,
            other,
            "complete",
            "complete_todo",
            {"todo_id": route["nodes"][0]["todo_id"]},
        )
        fact = completed["roadmap"]["nodes"][0]["completion"]
        stale, events = action(
            c,
            session,
            "old-confirm",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": old["id"],
                "sync_todo_ids": old["sync_todo_ids"],
            },
        )
        assert "event: roadmap_revision_stale" in events
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        assert route["nodes"][0]["completion"] == fact
        # Revise only the unfinished node; carry the completed content unchanged.
        draft = revision_args(route)
        draft["nodes"][0]["exercise"] = route["nodes"][0]["exercise"]
        draft["nodes"][0]["todo_title"] = route["nodes"][0]["todo_title"]
        draft["nodes"][1]["exercise"] = "设置 EX 10，立即查看 TTL，等待后再 GET"
        draft["nodes"][1]["todo_title"] = "观察缓存倒计时和过期结果"
        preview, _ = action(
            c, session, "new-preview", "preview_roadmap_revision", draft
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
        before = c.get("/api/todos").json()
        confirmation = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": [],
        }
        refused, events = action(
            c, session, "refuse", "confirm_roadmap_revision", confirmation
        )
        assert "event: roadmap_revision_pending" in events
        assert c.get("/api/todos").json() == before
        confirmation["sync_todo_ids"] = proposal["sync_todo_ids"]
        done, _ = action(c, session, "apply", "confirm_roadmap_revision", confirmation)
        route = done["roadmap"]
        assert route["nodes"][0]["completion"] == fact
        assert route["nodes"][1]["todo"]["title"] == "观察缓存倒计时和过期结果"
        assert route["nodes"][1]["todo"]["scheduled_date"] is None
        assert route["progress"] == {"completed": 1, "total": 2, "remaining": 1}
        for request_id in ("apply", "repeat-apply"):
            repeated, _ = action(
                c, session, request_id, "confirm_roadmap_revision", confirmation
            )
            assert repeated["roadmap"] == route
        retry = c.post("/api/runs/apply/retry").json()
        assert retry["id"] == "apply"
        todos = c.get("/api/todos").json()
        assert len(todos) == 2
        assert (
            done["model_calls"] == stale["model_calls"] == refused["model_calls"] == 0
        )

    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        c.post("/api/sessions")
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == todos
        assert c.get("/api/memories").json()[0]["content"] == "我有 Python 基础"

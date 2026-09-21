"""Self-paced joins and revisions against public HTTP/SSE and legacy SQLite."""

import json
import sqlite3

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_roadmap_batch import selection
from tests.test_roadmap_progress import target
from tests.test_roadmap_revisions import revision_args
from tests.test_roadmaps import REQUEST, roadmap_client, roadmap_provider


def seed_planned_dates(tmp_path, route, day="2026-10-01"):
    with sqlite3.connect(tmp_path / "roadmaps.db") as db:
        db.executemany(
            "INSERT OR REPLACE INTO roadmap_dates VALUES(?,?)",
            [(node["id"], day) for node in route["nodes"]],
        )


@pytest.mark.parametrize("batch", [False, True])
def test_join_historical_dates_is_unscheduled_and_repeat_preserves_manual_date(
    tmp_path, batch
):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route)
        tool = "accept_roadmap_nodes" if batch else "accept_roadmap_node"
        args = selection(route) if batch else target(route)
        joined, _ = action(c, session, "join", tool, args)
        todos = c.get("/api/todos").json()
        assert len(todos) == (2 if batch else 1)
        assert all(todo["scheduled_date"] is None for todo in todos)
        todo_id = joined["roadmap"]["nodes"][0]["todo_id"]
        action(
            c,
            session,
            "manual",
            "update_todo",
            {"todo_id": todo_id, "date_text": "2026-10-03"},
        )
        before = c.get("/api/todos").json()
        repeated, _ = action(c, session, "repeat", tool, args)
        assert c.get("/api/todos").json() == before
        node = repeated["roadmap"]["nodes"][0]
        assert node["scheduled_date"] == "2026-10-03"
        assert node["planned_date"] == "2026-10-01"


@pytest.mark.parametrize("date_mode", ["omitted", "planned", "actual"])
def test_content_revision_keeps_historical_plan_and_actual_todo_separate(
    tmp_path, date_mode
):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route)
        joined, _ = action(c, session, "join", "accept_roadmap_node", target(route))
        todo_id = joined["roadmap"]["nodes"][0]["todo_id"]
        action(
            c,
            session,
            "manual",
            "update_todo",
            {"todo_id": todo_id, "date_text": "2026-10-03"},
        )
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        args = revision_args(route)
        for node in args["nodes"]:
            if date_mode == "omitted":
                node.pop("scheduled_date")
            elif date_mode == "planned":
                node["scheduled_date"] = "2026-10-01"
        preview, events = action(
            c, session, "preview", "preview_roadmap_revision", args
        )
        assert "event: roadmap_revision_preview" in events, preview
        assert preview["roadmap"]["nodes"] == route["nodes"]
        proposal = preview["roadmap"]["revision_proposals"][0]
        confirmed, _ = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": proposal["id"],
                "sync_todo_ids": proposal["sync_todo_ids"],
            },
        )
        assert confirmed["status"] == "completed", confirmed
        node = confirmed["roadmap"]["nodes"][0]
        assert node["todo"]["title"] == args["nodes"][0]["todo_title"]
        assert node["scheduled_date"] == "2026-10-03"
        assert node["planned_date"] == "2026-10-01"
        assert confirmed["roadmap"]["nodes"][1]["scheduled_date"] == "2026-10-01"
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            assert {
                row[0] for row in db.execute("SELECT scheduled_date FROM roadmap_dates")
            } == {"2026-10-01"}


@pytest.mark.parametrize(
    ("planned", "requested", "new_node"),
    [
        (None, "2026-10-02", False),
        ("2026-10-01", "2026-10-02", False),
        ("2026-10-01", None, False),
        (None, "2026-10-02", True),
    ],
)
def test_revision_date_add_change_clear_rejects_entire_preview(
    tmp_path, planned, requested, new_node
):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route, planned)
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        args = revision_args(route)
        args["nodes"][1]["scheduled_date"] = requested
        if new_node:
            args["nodes"][1]["node_id"] = None
        result, events = action(
            c, session, "rejected", "preview_roadmap_revision", args
        )
        assert result["status"] == "failed", result
        assert result["error"] == "roadmap_scheduling_removed"
        assert "event: roadmap_revision_preview" not in events
        assert "roadmap_scheduling_removed" in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == []


def legacy_proposal(tmp_path, proposal_id, date_change=None):
    """Retain old persisted shape: no additive planned_date read field."""
    with sqlite3.connect(tmp_path / "roadmaps.db") as db:
        proposal = json.loads(
            db.execute(
                "SELECT content FROM revision_proposals WHERE id=?", (proposal_id,)
            ).fetchone()[0]
        )
        for key in ("nodes", "history_nodes"):
            for node in proposal["snapshot"][key]:
                node.pop("planned_date", None)
        for entry in proposal["entries"]:
            if entry["before"]:
                entry["before"].pop("planned_date", None)
        if date_change == "node":
            proposal["nodes"][0]["scheduled_date"] = "2026-10-04"
            proposal["entries"][0]["after"]["scheduled_date"] = "2026-10-04"
        elif date_change == "todo":
            proposal["entries"][0]["todo_after"]["scheduled_date"] = "2026-10-04"
        elif date_change == "clear":
            proposal["nodes"][0]["scheduled_date"] = None
            proposal["entries"][0]["after"]["scheduled_date"] = None
        db.execute(
            "UPDATE revision_proposals SET content=? WHERE id=?",
            (json.dumps(proposal, ensure_ascii=False), proposal_id),
        )


@pytest.mark.parametrize("date_change", [None, "node", "todo", "clear"])
def test_legacy_pending_revision_checks_dates_without_losing_snapshot_compatibility(
    tmp_path, date_change
):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route)
        joined, _ = action(c, session, "join", "accept_roadmap_node", target(route))
        todo_id = joined["roadmap"]["nodes"][0]["todo_id"]
        action(
            c,
            session,
            "manual",
            "update_todo",
            {"todo_id": todo_id, "date_text": "2026-10-03"},
        )
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        preview, _ = action(
            c, session, "preview", "preview_roadmap_revision", revision_args(route)
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
    legacy_proposal(tmp_path, proposal["id"], date_change)
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        assert before["revision_proposals"][0]["date_changes_blocked"] is bool(
            date_change
        )
        todos = c.get("/api/todos").json()
        other = c.post("/api/sessions").json()["id"]
        args = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": proposal["sync_todo_ids"],
        }
        result, events = action(c, other, "confirm", "confirm_roadmap_revision", args)
        if date_change:
            assert result["status"] == "failed", result
            assert result["error"] == "roadmap_scheduling_removed"
            assert "event: roadmap_revision_applied" not in events
            assert c.get(f"/api/roadmaps/{route['id']}").json() == before
            assert c.get("/api/todos").json() == todos
        else:
            assert "event: roadmap_revision_applied" in events, result
            node = result["roadmap"]["nodes"][0]
            assert node["planned_date"] == "2026-10-01"
            assert node["scheduled_date"] == "2026-10-03"
            assert node["todo"]["title"] == "完成一个简单的 Redis 读写练习"
            again, _ = action(c, other, "again", "confirm_roadmap_revision", args)
            assert again["roadmap"] == result["roadmap"]


def test_revision_new_node_has_no_date_and_completed_fact_is_unchanged(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route)
        completed, _ = action(
            c, session, "complete", "complete_roadmap_node", target(route)
        )
        route = completed["roadmap"]
        args = revision_args(route)
        args["nodes"][0]["node_id"] = None
        for node in args["nodes"]:
            node.pop("scheduled_date")
        preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
        proposal = preview["roadmap"]["revision_proposals"][0]
        result, _ = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        assert result["roadmap"]["nodes"][0]["scheduled_date"] is None
        assert result["roadmap"]["nodes"][0]["planned_date"] is None
        assert (
            result["roadmap"]["history_nodes"][0]["completion"]
            == (route["nodes"][0]["completion"])
        )
        assert result["roadmap"]["history_nodes"][0]["planned_date"] == "2026-10-01"


@pytest.mark.parametrize("model_changes_date", [False, True])
def test_model_revision_omits_dates_or_fails_without_saving_changes(
    tmp_path, model_changes_date
):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        name = body.get("tools", [{}])[0].get("function", {}).get("name")
        if name == "revision_plan":
            return httpx.Response(
                200, json=operation_response(name, {"query": None, "unsupported": []})
            )
        if name == "revision_answer":
            schema = body["tools"][0]["function"]["parameters"]
            node_type = schema["properties"]["nodes"]["items"]["$ref"].rsplit("/", 1)[1]
            assert "scheduled_date" not in schema["$defs"][node_type]["properties"]
            context = json.loads(body["messages"][-1]["content"])
            args = revision_args(context["route"])
            for node in args["nodes"]:
                node.pop("scheduled_date")
            if model_changes_date:
                args["nodes"][0]["scheduled_date"] = "2026-10-04"
            return httpx.Response(200, json=operation_response(name, args))
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        seed_planned_dates(tmp_path, route)
        joined, _ = action(c, session, "join", "accept_roadmap_node", target(route))
        action(
            c,
            session,
            "manual",
            "update_todo",
            {
                "todo_id": joined["roadmap"]["nodes"][0]["todo_id"],
                "date_text": "2026-10-03",
            },
        )
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        todos = c.get("/api/todos").json()
        result, events = submit(c, "这条路线太难了，改简单一点", "revise", session)
        assert result["model_calls"] == 2, result
        if model_changes_date:
            assert result["status"] == "failed"
            assert result["error"] == "roadmap_scheduling_removed"
            assert c.get(f"/api/roadmaps/{route['id']}").json() == before
        else:
            assert "event: roadmap_revision_preview" in events, result
            proposal = result["roadmap"]["revision_proposals"][0]
            assert not proposal["date_changes_blocked"]
            assert proposal["nodes"][0]["scheduled_date"] == "2026-10-03"
            assert result["roadmap"]["nodes"] == before["nodes"]
        assert c.get("/api/todos").json() == todos


def test_already_applied_legacy_date_revision_is_idempotent_history(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        preview, _ = action(
            c, session, "preview", "preview_roadmap_revision", revision_args(route)
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
    legacy_proposal(tmp_path, proposal["id"], "node")
    with sqlite3.connect(tmp_path / "roadmaps.db") as db:
        db.execute(
            "UPDATE revision_proposals SET status='applied' WHERE id=?",
            (proposal["id"],),
        )
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        repeated, _ = action(
            c,
            session,
            "repeat",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        assert repeated["status"] == "completed", repeated
        assert "已应用" in repeated["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == before
        assert c.get("/api/todos").json() == []


def test_model_classified_date_request_stops_before_search_or_revision(tmp_path):
    requests = []
    base = roadmap_provider(requests)

    def provider(request):
        body = json.loads(request.content)
        name = body.get("tools", [{}])[0].get("function", {}).get("name")
        if name == "revision_plan":
            return httpx.Response(
                200,
                json=operation_response(
                    name,
                    {
                        "query": "Redis study schedule",
                        "unsupported": ["日期调整不受支持"],
                        "date_change_requested": True,
                    },
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, requests, provider) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        before = len(requests)
        result, events = action(
            c,
            generated["session_id"],
            "date-request",
            "revise_roadmap",
            {
                "roadmap_id": route["id"],
                "expected_version": route["version"],
                "instruction": "把第一个节点安排在明天",
            },
        )
        assert result["status"] == "failed", result
        assert result["error"] == "roadmap_scheduling_removed"
        assert result["model_calls"] == 1
        assert "roadmap_scheduling_removed" in events
        assert len(requests) == before
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == []

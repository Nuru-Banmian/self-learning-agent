"""Retired scheduling compatibility at public HTTP/SSE and process boundaries."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process
from tests.test_roadmap_progress import target
from tests.test_roadmaps import REQUEST, roadmap_client


def preview_args(route, **changes):
    return {
        "roadmap_id": route["id"],
        "expected_version": route["version"],
        "start_text": "2026-10-01",
        "daily_minutes": 30,
        **changes,
    }


def seed_schedule(database, route, run_id):
    """Historical database fixture; all acceptance observations use the API."""
    proposal = {
        "id": "legacy-schedule",
        "roadmap_id": route["id"],
        "entries": [
            {
                "node_id": n["id"],
                "title": n["todo_title"],
                "scheduled_date": "2026-10-01",
                "allocations": [],
            }
            for n in route["nodes"]
        ],
        "basis": {"daily_minutes": 30},
    }
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO schedule_proposals VALUES(?,?,?,'pending',?)",
            (proposal["id"], route["id"], run_id, json.dumps(proposal)),
        )
        for node in route["nodes"]:
            db.execute(
                "INSERT INTO roadmap_dates VALUES(?,?)", (node["id"], "2026-10-01")
            )
    return {"roadmap_id": route["id"], "proposal_id": proposal["id"]}


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"clear": True},
        {"weekdays": [5, 6]},
        {"start_text": None},
        {"daily_minutes": None},
        {"deadline_text": "2026-10-01"},
        {"start_text": "明天"},
    ],
)
def test_all_legacy_preview_shapes_fail_without_business_writes(tmp_path, changes):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        failed, events = action(
            c,
            generated["session_id"],
            "preview",
            "preview_roadmap_schedule",
            preview_args(route, **changes),
        )
        assert failed["status"] == "failed"
        assert failed["error"] == "roadmap_scheduling_removed"
        assert failed["model_calls"] == 0
        assert "roadmap_scheduling_removed" in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize(
    "content",
    [
        "请为这条路线排期，从2026-10-01开始，每天15分钟",
        "请为路线排期",
        "清空这条路线的日期",
        "把这条路线改到明天",
        "请调整路线，把第一个节点改到明天",
        "这条路线从明天开始每天半小时排期",
        "这条路线不排期",
        "不要为路线排期",
        "解释一下路线排期是什么意思",
    ],
)
def test_chat_scheduling_never_collects_conditions_or_writes(tmp_path, content):
    calls = []
    with roadmap_client(tmp_path, calls) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        before = len(calls)
        answer, events = submit(c, content, "removed", generated["session_id"])
        assert answer["status"] == "completed"
        assert answer["model_calls"] == 0 and len(calls) == before
        assert "自己的节奏" in answer["reply"]
        assert "event: roadmap_schedule_preview" not in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == []


def test_old_pending_confirmation_concurrency_retry_and_restart_preserve_history(
    tmp_path,
):
    database = tmp_path / "roadmaps.db"
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        args = seed_schedule(database, route, generated["id"])
        baseline = c.get(f"/api/roadmaps/{route['id']}").json()
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda i: action(
                        c, sessions[i], f"confirm-{i}", "confirm_roadmap_schedule", args
                    ),
                    range(2),
                )
            )
        for failed, events in results:
            assert failed["status"] == "failed"
            assert failed["error"] == "roadmap_scheduling_removed"
            assert "roadmap_scheduling_removed" in events
        retry = c.post("/api/runs/confirm-0/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        assert (
            c.get(f"/api/runs/{retry['id']}").json()["error"]
            == "roadmap_scheduling_removed"
        )
        assert c.get(f"/api/roadmaps/{route['id']}").json() == baseline
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        assert c.get(f"/api/roadmaps/{route['id']}").json() == baseline
        assert c.get("/api/todos").json() == []
        session = c.post("/api/sessions").json()["id"]
        accepted, _ = action(
            c, session, "join", "accept_roadmap_node", target(baseline)
        )
        assert accepted["roadmap"]["nodes"][0]["todo"]["scheduled_date"] is None


@pytest.mark.parametrize("operation", ["preview", "confirm"])
@pytest.mark.parametrize(
    "legacy_status,committed",
    [
        ("queued", False),
        ("running", False),
        ("failed", False),
        ("completed", True),
        ("partial", True),
    ],
)
def test_legacy_run_recovery_and_retry_do_not_replay_dates(
    tmp_path, operation, legacy_status, committed
):
    database = tmp_path / "roadmaps.db"
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        confirmation = seed_schedule(database, route, generated["id"])
        args = preview_args(route) if operation == "preview" else confirmation
        failed, _ = action(
            c,
            generated["session_id"],
            "legacy-run",
            f"{operation}_roadmap_schedule",
            args,
        )
        baseline = c.get(f"/api/roadmaps/{route['id']}").json()
    # Persisted old-version requests, including partial main success.
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE runs SET status=?,result_committed=?,error=NULL "
            "WHERE id='legacy-run'",
            (legacy_status, int(committed)),
        )
    port = free_port()
    with server_process(port, database, "http://fixture.invalid/v1", api_key="") as (
        c,
        process,
    ):
        original = c.get("/api/runs/legacy-run").json()
        if legacy_status in ("queued", "running"):
            assert original["status"] == "failed"
            assert original["error"] == "roadmap_scheduling_removed"
        retry = c.post("/api/runs/legacy-run/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        if committed:
            assert done == original and done["id"] == "legacy-run"
            assert done["status"] == legacy_status
        else:
            assert done["status"] == "failed"
            assert done["error"] == "roadmap_scheduling_removed"
        assert c.get(f"/api/roadmaps/{route['id']}").json() == baseline
        assert c.get("/api/todos").json() == []
        process.kill()
        process.wait(5)
    with server_process(port, database, "http://fixture.invalid/v1", api_key="") as (
        c,
        _,
    ):
        assert c.get(f"/api/roadmaps/{route['id']}").json() == baseline
        assert c.get(f"/api/runs/{done['id']}").json() == done


@pytest.mark.parametrize("title", ["整理项目排期", "复习学习路线", "检查集群节点"])
def test_ordinary_todo_rescheduling_with_scheduling_word_in_title(tmp_path, title):
    from tests.test_chat import make_client, tool_response
    from tests.test_maintenance import operation_response

    with make_client(
        tmp_path, tool_response([{"title": title, "date_text": None}])
    ) as c:
        created, _ = submit(c, "请记录" + title)
        todo_id = created["todo_ids"][0]
    with make_client(
        tmp_path,
        operation_response(
            "update_todo", {"todo_id": todo_id, "date_text": "2026-10-01"}
        ),
    ) as c:
        session = c.post("/api/sessions").json()["id"]
        changed, _ = submit(c, f"把{title}改到2026-10-01", "change", session)
        assert changed["todo_ids"] == [todo_id], changed
        assert c.get("/api/todos").json()[0]["scheduled_date"] == "2026-10-01"

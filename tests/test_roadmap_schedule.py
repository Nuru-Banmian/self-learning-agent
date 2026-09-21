"""Scheduling acceptance through HTTP/SSE and real SQLite."""

import pytest

from tests.test_chat import submit
from tests.test_maintenance import action
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


def test_preview_confirm_restart_and_future_acceptance(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        todos = c.get("/api/todos").json()
        preview, events = action(
            c, session, "preview", "preview_roadmap_schedule", preview_args(route)
        )
        assert preview["status"] == "completed", preview
        assert "event: roadmap_schedule_preview" in events
        proposal = preview["roadmap"]["schedule_proposals"][0]
        assert [n["scheduled_date"] for n in proposal["entries"]] == [
            "2026-10-01",
            "2026-10-02",
        ]
        assert c.get("/api/todos").json() == todos
        assert all(n["scheduled_date"] is None for n in preview["roadmap"]["nodes"])
    with roadmap_client(tmp_path, [], dashscope_api_key="") as c:
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        assert route["schedule_proposals"][0] == proposal
        session = c.post("/api/sessions").json()["id"]
        args = {"roadmap_id": route["id"], "proposal_id": proposal["id"]}
        done, events = action(c, session, "confirm", "confirm_roadmap_schedule", args)
        assert "event: roadmap_schedule_confirmed" in events
        route = done["roadmap"]
        assert [n["scheduled_date"] for n in route["nodes"]] == [
            "2026-10-01",
            "2026-10-02",
        ]
        assert c.get("/api/todos").json()[0]["scheduled_date"] == "2026-10-01"
        assert len(c.get("/api/todos").json()) == 1
        repeat, _ = action(c, session, "repeat", "confirm_roadmap_schedule", args)
        assert repeat["roadmap"] == route
        accepted, _ = action(
            c, session, "later", "accept_roadmap_node", target(route, 1)
        )
        assert accepted["roadmap"]["nodes"][1]["todo"]["scheduled_date"] == "2026-10-02"


def test_chat_schedule_extracts_conditions_but_application_computes_dates(tmp_path):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "schedule_conditions"
        ):
            return httpx.Response(
                200,
                json=operation_response(
                    "schedule_conditions",
                    {
                        "roadmap_reference": "",
                        "start_text": "2026-10-01",
                        "budget_text": "每天15分钟",
                        "availability_text": "每天",
                        "deadline_text": None,
                        "clear": False,
                        "unsupported": [],
                    },
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        run, _ = submit(c, REQUEST)
        preview, events = submit(
            c,
            "请为这条路线排期，从2026-10-01开始，每天15分钟",
            "schedule",
            run["session_id"],
        )
        assert "event: roadmap_schedule_preview" in events, preview
        proposal = preview["roadmap"]["schedule_proposals"][0]
        assert [e["scheduled_date"] for e in proposal["entries"]] == [
            "2026-10-02",
            "2026-10-04",
        ]
        assert proposal["entries"][0]["allocations"] == [
            {"date": "2026-10-01", "minutes": 15},
            {"date": "2026-10-02", "minutes": 15},
        ]
        assert c.get("/api/todos").json() == []


def test_todo_change_then_revert_still_invalidates_pending_confirmation(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        route, session = run["roadmap"], run["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        preview, _ = action(
            c, session, "preview", "preview_roadmap_schedule", preview_args(route)
        )
        proposal = preview["roadmap"]["schedule_proposals"][0]
        other = c.post("/api/sessions").json()["id"]
        for i, day in enumerate(("2026-10-03", None)):
            action(
                c,
                other,
                f"change-{i}",
                "update_todo",
                {"todo_id": route["nodes"][0]["todo_id"], "date_text": day},
            )
            current = c.get(f"/api/roadmaps/{route['id']}").json()
            assert current["nodes"][0]["scheduled_date"] == day
        done, events = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_schedule",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        assert "event: roadmap_schedule_stale" in events
        assert "过期" in done["reply"]
        assert c.get("/api/todos").json()[0]["scheduled_date"] is None


def test_budget_conflict_weekdays_clear_and_completed_protection(tmp_path):
    from tests.test_roadmap_batch import selection

    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        route, session = run["roadmap"], run["session_id"]
        accepted, _ = action(
            c, session, "batch", "accept_roadmap_nodes", selection(route)
        )
        route = accepted["roadmap"]
        impossible, _ = action(
            c,
            session,
            "impossible",
            "preview_roadmap_schedule",
            preview_args(route, deadline_text="2026-10-01"),
        )
        assert "预算冲突" in impossible["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        for i, changes in enumerate(
            ({"start_text": None}, {"daily_minutes": None}, {"start_text": "下周"})
        ):
            missing, _ = action(
                c,
                session,
                f"missing-{i}",
                "preview_roadmap_schedule",
                preview_args(route, **changes),
            )
            assert not missing["roadmap"]
        preview, _ = action(
            c,
            session,
            "preview",
            "preview_roadmap_schedule",
            preview_args(route, weekdays=[5, 6]),
        )
        proposal = preview["roadmap"]["schedule_proposals"][0]
        assert [e["scheduled_date"] for e in proposal["entries"]] == [
            "2026-10-03",
            "2026-10-04",
        ]
        done, _ = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_schedule",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        route = done["roadmap"]
        completed, _ = action(
            c, session, "complete", "complete_roadmap_node", target(route)
        )
        route = completed["roadmap"]
        fact = route["nodes"][0]["completion"]
        preview, _ = action(
            c,
            session,
            "clear",
            "preview_roadmap_schedule",
            {
                "roadmap_id": route["id"],
                "expected_version": route["version"],
                "clear": True,
            },
        )
        proposal = preview["roadmap"]["schedule_proposals"][0]
        assert len(proposal["entries"]) == 1
        assert preview["roadmap"]["nodes"][1]["scheduled_date"] == "2026-10-04"
        done, _ = action(
            c,
            session,
            "confirm-clear",
            "confirm_roadmap_schedule",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        assert done["roadmap"]["nodes"][0]["completion"] == fact
        assert done["roadmap"]["nodes"][0]["scheduled_date"] == "2026-10-03"
        assert done["roadmap"]["nodes"][1]["scheduled_date"] is None
        assert len(c.get("/api/todos").json()) == 2


def test_confirmation_atomic_failure_retry_and_concurrent_staleness(tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    from tests.test_roadmap_batch import selection

    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        route, session = run["roadmap"], run["session_id"]
        accepted, _ = action(
            c, session, "batch", "accept_roadmap_nodes", selection(route)
        )
        route = accepted["roadmap"]
        preview, _ = action(
            c, session, "preview", "preview_roadmap_schedule", preview_args(route)
        )
        proposal = preview["roadmap"]["schedule_proposals"][0]
        args = {"roadmap_id": route["id"], "proposal_id": proposal["id"]}
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        todos = c.get("/api/todos").json()
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute(
                "CREATE TRIGGER fail_schedule BEFORE INSERT ON events "
                "WHEN NEW.kind='roadmap_schedule_confirmed' "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        failed, events = action(c, session, "failure", "confirm_roadmap_schedule", args)
        assert failed["status"] == "failed" and failed["error"] == "storage"
        assert "event: roadmap_schedule_confirmed" not in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == before
        assert c.get("/api/todos").json() == todos
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute("DROP TRIGGER fail_schedule")
        retried = c.post("/api/runs/failure/retry").json()
        c.get(f"/api/runs/{retried['id']}/events")
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        assert route["nodes"][1]["scheduled_date"] == "2026-10-02"
        proposals = []
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(2)]
        for i in range(2):
            preview, _ = action(
                c,
                sessions[i],
                f"p-{i}",
                "preview_roadmap_schedule",
                preview_args(route, start_text=f"2026-10-0{3 + i}"),
            )
            proposals.append(preview["roadmap"]["schedule_proposals"][0])
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda i: action(
                        c,
                        sessions[i],
                        f"c-{i}",
                        "confirm_roadmap_schedule",
                        {"roadmap_id": route["id"], "proposal_id": proposals[i]["id"]},
                    ),
                    range(2),
                )
            )
        assert (
            sum("event: roadmap_schedule_stale" in events for _, events in results) == 1
        )
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        assert current["version"] == route["version"] + 1
        assert len(c.get("/api/todos").json()) == 2


def test_relative_dates_use_original_request_timezone_after_failed_retry(tmp_path):
    import sqlite3
    from datetime import UTC, datetime

    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings
    from tests.test_roadmaps import roadmap_provider

    now = [datetime(2026, 9, 30, 17, tzinfo=UTC)]  # Oct 1 in Shanghai
    app = create_app(
        Settings(
            _env_file=None,
            db_path=tmp_path / "roadmaps.db",
            dashscope_api_key="test",
            iqs_api_key="test",
            user_timezone="Asia/Shanghai",
        ),
        transport=httpx.MockTransport(roadmap_provider([])),
        clock=lambda: now[0],
    )
    with TestClient(app) as c:
        run, _ = submit(c, REQUEST)
        route, session = run["roadmap"], run["session_id"]
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute(
                "CREATE TRIGGER fail_preview BEFORE INSERT ON events "
                "WHEN NEW.kind='roadmap_schedule_preview' "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        failed, _ = action(
            c,
            session,
            "preview",
            "preview_roadmap_schedule",
            preview_args(route, start_text="明天"),
        )
        assert failed["error"] == "storage"
        now[0] = datetime(2026, 10, 5, 17, tzinfo=UTC)
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute("DROP TRIGGER fail_preview")
        retry = c.post("/api/runs/preview/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        proposal = done["roadmap"]["schedule_proposals"][0]
        assert [e["scheduled_date"] for e in proposal["entries"]] == [
            "2026-10-02",
            "2026-10-03",
        ]
        assert proposal["basis"]["received_at"] == failed["received_at"]


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_schedule_survives_real_process_death(tmp_path, after_commit, operation):
    from tests.test_process import free_port, server_process

    port, database = free_port(), tmp_path / "restart.db"
    gate = tmp_path / "release"
    gate.touch()
    options = {
        "factory": "tests.roadmap_demo:create_demo_app",
        "extra_env": {"ROADMAP_PROVIDER_GATE": str(gate)},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        process,
    ):
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        args = preview_args(route)
        if operation == "confirm":
            preview, _ = action(c, session, "preview", "preview_roadmap_schedule", args)
            args = {
                "roadmap_id": route["id"],
                "proposal_id": preview["roadmap"]["schedule_proposals"][0]["id"],
            }
        if not after_commit:
            gate.unlink()
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": "blocked", "content": REQUEST},
            ).raise_for_status()
        body = {
            "request_id": "schedule",
            "content": "面板排期操作",
            "action": {"tool": f"{operation}_roadmap_schedule", "arguments": args},
        }
        c.post(f"/api/sessions/{session}/messages", json=body).raise_for_status()
        event_name = (
            "roadmap_schedule_preview"
            if operation == "preview"
            else "roadmap_schedule_confirmed"
        )
        if after_commit:
            with c.stream("GET", "/api/runs/schedule/events") as stream:
                for line in stream.iter_lines():
                    if line == f"event: {event_name}":
                        break
                else:
                    raise AssertionError("missing committed scheduling event")
        else:
            assert c.get("/api/runs/schedule").json()["status"] == "queued"
        process.kill()
        process.wait(5)
    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        restarted,
    ):
        assert restarted.pid != process.pid
        original = c.get("/api/runs/schedule").json()
        assert original["status"] == ("completed" if after_commit else "failed")
        assert c.post(f"/api/sessions/{session}/messages", json=body).json() == original
        retry = c.post("/api/runs/schedule/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert done["status"] == "completed"
        assert len(done["roadmap"]["schedule_proposals"]) == 1
        assert done["roadmap"]["schedule_proposals"][0]["status"] == (
            "pending" if operation == "preview" else "applied"
        )
        assert c.get("/api/todos").json()[0]["scheduled_date"] == (
            "2026-10-01" if operation == "confirm" else None
        )
        saved = next(e for e in done["events"] if e["kind"] == event_name)
        remaining = c.get(
            f"/api/runs/{done['id']}/events",
            headers={"Last-Event-ID": str(saved["seq"])},
        ).text
        assert (
            f"event: {event_name}" not in remaining and "event: terminal" in remaining
        )


@pytest.mark.parametrize(
    "content",
    [
        "不要为路线排期",
        "解释一下路线排期是什么意思",
        "他说‘请为路线排期’，这句话什么意思",
    ],
)
def test_no_schedule_from_negation_or_quoted_intent(tmp_path, content):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        untouched = run["roadmap"]
        answer, _ = submit(c, content, "denied", run["session_id"])
        assert answer["model_calls"] == 0
        assert c.get(f"/api/roadmaps/{untouched['id']}").json() == untouched


def test_learning_without_scheduling_still_generates_default_undated_route(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST + "，不要安排日期")
        assert run["roadmap"]
        assert run["roadmap"]["schedule_proposals"] == []
        assert all(n["scheduled_date"] is None for n in run["roadmap"]["nodes"])


@pytest.mark.parametrize(
    "budget,reference", [("每天1小时30分钟", ""), ("每天30分钟", "Redis 缓存入门")]
)
def test_compound_budget_and_quoted_route_title(tmp_path, budget, reference):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "schedule_conditions"
        ):
            return httpx.Response(
                200,
                json=operation_response(
                    "schedule_conditions",
                    {
                        "roadmap_reference": reference,
                        "start_text": "2026-10-01",
                        "budget_text": budget,
                        "availability_text": "每天",
                        "deadline_text": None,
                        "clear": False,
                        "unsupported": [],
                    },
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        ref = f"「{reference}」" if reference else ""
        run, events = submit(
            c,
            f"请为路线{ref}排期，从2026-10-01开始，{budget}",
            "preview",
            generated["session_id"],
        )
        assert "event: roadmap_schedule_preview" in events, run
        proposal = run["roadmap"]["schedule_proposals"][0]
        assert proposal["basis"]["daily_minutes"] == (90 if "小时" in budget else 30)


def test_ordinary_todo_rescheduling_with_scheduling_word_in_title(tmp_path):
    from tests.test_chat import make_client, tool_response
    from tests.test_maintenance import operation_response

    with make_client(
        tmp_path, tool_response([{"title": "整理项目排期", "date_text": None}])
    ) as c:
        created, _ = submit(c, "请记录整理项目排期")
        todo_id = created["todo_ids"][0]
    with make_client(
        tmp_path,
        operation_response(
            "update_todo", {"todo_id": todo_id, "date_text": "2026-10-01"}
        ),
    ) as c:
        session = c.post("/api/sessions").json()["id"]
        changed, _ = submit(c, "把整理项目排期改到2026-10-01", "change", session)
        assert changed["todo_ids"] == [todo_id], changed
        assert c.get("/api/todos").json()[0]["scheduled_date"] == "2026-10-01"


@pytest.mark.parametrize(
    "budget,availability", [("每天30到60分钟", "每天"), ("30分钟", "周一三五")]
)
def test_ambiguous_budget_or_partial_weekdays_requires_clarification(
    tmp_path, budget, availability
):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "schedule_conditions"
        ):
            return httpx.Response(
                200,
                json=operation_response(
                    "schedule_conditions",
                    {
                        "roadmap_reference": "",
                        "start_text": "2026-10-01",
                        "budget_text": budget,
                        "availability_text": availability,
                        "deadline_text": None,
                        "clear": False,
                        "unsupported": [],
                    },
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        run, events = submit(
            c,
            f"请为路线排期，从2026-10-01开始，{availability}，{budget}",
            "ambiguous",
            generated["session_id"],
        )
        assert "event: roadmap_schedule_preview" not in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route

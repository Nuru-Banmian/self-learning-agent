"""Real process death before/after roadmap commit, observed through HTTP/SSE."""

import time

import pytest

from tests.test_maintenance import action
from tests.test_process import free_port, server_process
from tests.test_roadmap_batch import selection
from tests.test_roadmaps import REQUEST


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_death_recovers_roadmap_and_acceptance_atomically(
    tmp_path, after_commit
):
    port = free_port()
    database = tmp_path / "restart.db"
    gate = tmp_path / "provider-release"
    options = {
        "factory": "tests.roadmap_demo:create_demo_app",
        "extra_env": {"ROADMAP_PROVIDER_GATE": str(gate)},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        process,
    ):
        session = c.post("/api/sessions").json()["id"]
        message = {"request_id": "generation", "content": REQUEST}
        c.post(f"/api/sessions/{session}/messages", json=message).raise_for_status()
        for _ in range(100):
            run = c.get("/api/runs/generation").json()
            if run["research"].get("sources"):
                break
            time.sleep(0.02)
        assert run["research"]["sources"]
        with c.stream("GET", "/api/runs/generation/events") as stream:
            lines = stream.iter_lines()
            assert next(lines).startswith("id:")
            if after_commit:
                gate.touch()
                for line in lines:
                    if line == "event: roadmap_saved":
                        break
                else:
                    raise AssertionError("missing saved roadmap event")
            process.kill()
            process.wait(5)
    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        restarted,
    ):
        assert restarted.pid != process.pid
        original = c.get("/api/runs/generation").json()
        assert original["status"] == ("completed" if after_commit else "partial")
        assert bool(original["roadmap"]) == after_commit
        assert len(c.get("/api/roadmaps").json()) == int(after_commit)
        retried = c.post("/api/runs/generation/retry").json()
        c.get(f"/api/runs/{retried['id']}/events").raise_for_status()
        done = c.get(f"/api/runs/{retried['id']}").json()
        assert done["status"] == "completed"
        route = done["roadmap"]
        assert len(c.get("/api/roadmaps").json()) == 1
        assert c.get("/api/todos").json() == []
        if not after_commit:
            original["retry_run_id"] = done["id"]
        assert (
            c.post(f"/api/sessions/{session}/messages", json=message).json() == original
        )
        saved = next(e for e in done["events"] if e["kind"] == "roadmap_saved")
        remaining = c.get(
            f"/api/runs/{done['id']}/events",
            headers={"Last-Event-ID": str(saved["seq"])},
        ).text
        assert (
            "event: roadmap_saved" not in remaining and "event: terminal" in remaining
        )
        other = c.post("/api/sessions").json()["id"]
        args = {
            "roadmap_id": route["id"],
            "node_id": route["nodes"][0]["id"],
            "expected_version": route["version"],
        }
        accepted, _ = action(c, other, "accept", "accept_roadmap_node", args)
        assert len(accepted["todo_ids"]) == 1
        restarted.kill()
        restarted.wait(5)
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        _,
    ):
        replay, _ = action(c, other, "accept", "accept_roadmap_node", args)
        assert replay == accepted
        assert len(c.get("/api/todos").json()) == 1
        assert (
            c.get(f"/api/roadmaps/{route['id']}").json()["nodes"][0]["todo_id"]
            == accepted["todo_ids"][0]
        )


@pytest.mark.parametrize("after_commit", [False, True])
def test_batch_acceptance_process_death_and_sse_replay(tmp_path, after_commit):
    port, database = free_port(), tmp_path / "batch-restart.db"
    gate = tmp_path / "release"
    gate.touch()
    options = {
        "factory": "tests.roadmap_demo:create_demo_app",
        "extra_env": {"ROADMAP_PROVIDER_GATE": str(gate), "ROADMAP_BATCH_DEMO": "1"},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        process,
    ):
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "route", "content": REQUEST},
        ).raise_for_status()
        c.get("/api/runs/route/events")
        route = c.get("/api/runs/route").json()["roadmap"]
        args = selection(route)
        if not after_commit:
            # Keep a preceding request at the external provider boundary, so the
            # accepted batch is durably queued but cannot commit before the kill.
            gate.unlink()
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": "blocked", "content": REQUEST},
            ).raise_for_status()
        body = {
            "request_id": "batch",
            "content": "确认全部加入",
            "action": {"tool": "accept_roadmap_nodes", "arguments": args},
        }
        c.post(f"/api/sessions/{session}/messages", json=body).raise_for_status()
        if after_commit:
            # Disconnect immediately after the committed acceptance event, without
            # waiting for the terminal event, then really kill the process.
            with c.stream("GET", "/api/runs/batch/events") as stream:
                for line in stream.iter_lines():
                    if line == "event: roadmap_nodes_accepted":
                        break
                else:
                    raise AssertionError("missing batch acceptance event")
        else:
            assert c.get("/api/runs/batch").json()["status"] == "queued"
        process.kill()
        process.wait(5)
    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        restarted,
    ):
        assert restarted.pid != process.pid
        original = c.get("/api/runs/batch").json()
        assert len(c.get("/api/todos").json()) == (3 if after_commit else 0)
        assert original["status"] == ("completed" if after_commit else "failed")
        assert c.post(f"/api/sessions/{session}/messages", json=body).json() == original
        retry = c.post("/api/runs/batch/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert done["status"] == "completed"
        assert "本次新增 3" in done["reply"]
        saved = next(e for e in done["events"] if e["kind"] == "roadmap_nodes_accepted")
        assert [r["status"] for r in saved["data"]["results"]] == ["created"] * 3
        events = c.get(
            f"/api/runs/{done['id']}/events",
            headers={"Last-Event-ID": str(saved["seq"])},
        ).text
        assert (
            "event: roadmap_nodes_accepted" not in events
            and "event: terminal" in events
        )
        other = c.post("/api/sessions").json()["id"]
        repeated, _ = action(c, other, "again", "accept_roadmap_nodes", args)
        assert "本次新增 0" in repeated["reply"]
        assert len(c.get("/api/todos").json()) == 3

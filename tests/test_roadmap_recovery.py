"""Real process death before/after roadmap commit, observed through HTTP/SSE."""

import time

import pytest

from tests.test_maintenance import action
from tests.test_process import free_port, server_process
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

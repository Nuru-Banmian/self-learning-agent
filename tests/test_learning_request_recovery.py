"""Real process interruption and explicit retry at the HTTP/SSE seam."""

import time

from tests.test_chat import submit
from tests.test_process import free_port, server_process


def test_interrupted_continuation_keeps_goal_and_retry_completes_once(tmp_path):
    port = free_port()
    database = tmp_path / "learning.db"
    gate = tmp_path / "release"
    options = {
        "factory": "tests.learning_request_demo:create_demo_app",
        "extra_env": {"LEARNING_PROVIDER_GATE": str(gate)},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        process,
    ):
        submit(c, "我有 Python 基础", "background")
        submit(c, "我想学习 Redis，每次30分钟", "start")
        pending = c.get("/api/learning-requests").json()[0]
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "continue", "content": "目标是实现缓存"},
        )
        for _ in range(100):
            run = c.get("/api/runs/continue").json()
            if run["research"].get("sources"):
                break
            time.sleep(0.02)
        assert run["research"]["sources"]
        process.kill()
        process.wait(timeout=10)
    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        _,
    ):
        assert c.get("/api/runs/continue").json()["retryable"]
        current = c.get("/api/learning-requests").json()[0]
        assert current["id"] == pending["id"] and current["roadmap_id"] is None
        assert current["known"]["goal"] == "实现缓存"
        retry = c.post("/api/runs/continue/retry").json()
        events = c.get(f"/api/runs/{retry['id']}/events").text
        assert "event: roadmap_saved" in events
        assert len(c.get("/api/roadmaps").json()) == 1
        assert c.get("/api/learning-requests").json()[0]["roadmap_id"]
        assert not c.get("/api/todos").json()
        c.post("/api/runs/continue/retry")
        assert len(c.get("/api/roadmaps").json()) == 1

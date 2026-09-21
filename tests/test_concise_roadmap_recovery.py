"""Persisted concise replies across public continuation and process recovery."""

import time

from tests.test_chat import submit
from tests.test_learning_requests import intake_provider
from tests.test_process import free_port, server_process
from tests.test_roadmaps import REQUEST, roadmap_client


def assert_concise_route(run):
    route = run["roadmap"]
    assert run["status"] == "completed"
    assert run["roadmap_context"]
    assert 0 < len(run["reply"]) <= 800
    assert run["roadmap_links"] == [
        {"roadmap_id": route["id"], "node_id": None, "title": route["title"]}
    ]
    assert 1 <= len(route["nodes"]) <= 8
    positions = []
    for node in route["nodes"]:
        title = node.get("display_title") or node["todo_title"]
        goal = node.get("display_goal") or node["goal"]
        positions.append(run["reply"].index(title))
        assert goal in run["reply"]
        assert node["exercise"] not in run["reply"]
        assert node["completion_criteria"] not in run["reply"]
    assert positions == sorted(positions)
    assert "预计" not in run["reply"]
    for source in route["sources"]:
        assert source["url"] not in run["reply"]
        assert source["snippet"] not in run["reply"]


def test_killed_generation_recovers_short_partial_reply_then_retry_and_restart(
    tmp_path,
):
    port = free_port()
    database = tmp_path / "concise-restart.db"
    gate = tmp_path / "provider-release"
    options = {
        "factory": "tests.support.concise_roadmap_demo:create_demo_app",
        "extra_env": {"ROADMAP_PROVIDER_GATE": str(gate)},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        client,
        process,
    ):
        session = client.post("/api/sessions").json()["id"]
        message = {"request_id": "generation", "content": REQUEST}
        client.post(
            f"/api/sessions/{session}/messages", json=message
        ).raise_for_status()
        for _ in range(100):
            running = client.get("/api/runs/generation").json()
            if running["research"].get("sources"):
                break
            time.sleep(0.02)
        assert running["status"] == "running"
        sources = running["research"]["sources"]
        assert sources
        assert client.get("/api/roadmaps").json() == []
        with client.stream("GET", "/api/runs/generation/events") as stream:
            assert next(stream.iter_lines()).startswith("id:")
            process.kill()
            process.wait(5)

    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        client,
        restarted,
    ):
        assert restarted.pid != process.pid
        interrupted = client.get("/api/runs/generation").json()
        assert interrupted["status"] == "partial"
        assert interrupted["roadmap_context"]
        assert interrupted["roadmap"] == {}
        assert interrupted["roadmap_links"] == []
        assert 0 < len(interrupted["reply"]) <= 800
        assert interrupted["research"]["sources"] == sources
        assert interrupted["research"]["gaps"]
        assert interrupted["retryable"]
        for source in sources:
            assert source["url"] not in interrupted["reply"]
            assert source["snippet"] not in interrupted["reply"]
        assert "event: terminal" in client.get("/api/runs/generation/events").text
        assert client.get("/api/roadmaps").json() == []
        partial_messages = client.get(f"/api/sessions/{session}").json()["messages"]
        assert partial_messages[-1]["content"] == interrupted["reply"]
        assert partial_messages[-1]["roadmap_context"]
        assert partial_messages[-1]["roadmap_links"] == []

        retry_response = client.post("/api/runs/generation/retry")
        retry_response.raise_for_status()
        retry = retry_response.json()
        events = client.get(f"/api/runs/{retry['id']}/events").text
        assert "event: roadmap_saved" in events and "event: terminal" in events
        done = client.get(f"/api/runs/{retry['id']}").json()
        assert done["retry_of"] == "generation"
        assert_concise_route(done)
        route = done["roadmap"]
        assert len(client.get("/api/roadmaps").json()) == 1
        assert client.get("/api/todos").json() == []
        saved_session = client.get(f"/api/sessions/{session}").json()
        assert saved_session["messages"][-1]["content"] == done["reply"]
        assert saved_session["messages"][-1]["roadmap_links"] == done["roadmap_links"]
        original = client.get("/api/runs/generation").json()
        restarted.kill()
        restarted.wait(5)

    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        client,
        second_restart,
    ):
        assert second_restart.pid != restarted.pid
        assert client.get("/api/runs/generation").json() == original
        assert client.get(f"/api/runs/{done['id']}").json() == done
        assert client.get(f"/api/sessions/{session}").json() == saved_session
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route
        assert client.get("/api/todos").json() == []


def test_continued_learning_saves_concise_all_node_reply_and_accurate_navigation(
    tmp_path,
):
    requests = []
    with roadmap_client(tmp_path, requests, intake_provider(requests)) as client:
        initial, _ = submit(
            client, "我想学习 Redis，有 Python 基础，每次30分钟", "initial"
        )
        pending = client.get("/api/learning-requests").json()[0]
        assert pending["roadmap_id"] is None
        assert not initial["roadmap"]
        assert client.get("/api/roadmaps").json() == []

    with roadmap_client(tmp_path, requests, intake_provider(requests)) as client:
        run, events = submit(client, "目标是实现缓存", "continue")
        assert run["session_id"] != initial["session_id"]
        assert "event: roadmap_saved" in events and "event: terminal" in events
        assert_concise_route(run)
        route = run["roadmap"]
        continued = client.get("/api/learning-requests").json()[0]
        assert continued["id"] == pending["id"]
        assert continued["roadmap_id"] == route["id"]
        assert client.get("/api/todos").json() == []
        saved_session = client.get(f"/api/sessions/{run['session_id']}").json()
        assert saved_session["messages"][-1]["content"] == run["reply"]
        assert saved_session["messages"][-1]["roadmap_links"] == run["roadmap_links"]

    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as client:
        assert client.get(f"/api/runs/{run['id']}").json() == run
        assert client.get(f"/api/sessions/{run['session_id']}").json() == saved_session
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route

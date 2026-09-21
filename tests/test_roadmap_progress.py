"""Completion facts through public HTTP/SSE and persistent SQLite."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_process import free_port, server_process
from tests.test_roadmap_batch import selection
from tests.test_roadmaps import REQUEST, roadmap_client, roadmap_provider


def target(route, index=0):
    return {
        "roadmap_id": route["id"],
        "node_id": route["nodes"][index]["id"],
        "expected_version": route["version"],
    }


def test_linked_todo_completion_preserves_node_snapshot_and_operation(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(
            c, session, "accept", "accept_roadmap_nodes", selection(route)
        )
        todo_id = accepted["todo_ids"][0]
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        assert before["progress"] == {"completed": 0, "total": 2, "remaining": 2}
        done, events = action(c, session, "done", "complete_todo", {"todo_id": todo_id})
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        node = current["nodes"][0]
        assert current["progress"] == {"completed": 1, "total": 2, "remaining": 1}
        assert node["status"] == node["todo"]["status"] == "completed"
        fact = node["completion"]
        assert fact["completed_at"]
        assert fact["run_id"] == "done"
        assert fact["message_id"] == done["message_id"]
        assert fact["operation"] == "complete_todo"
        assert fact["content"] == "面板操作"
        assert fact["action"]["arguments"] == {"todo_id": todo_id}
        assert fact["node"]["exercise"] == before["nodes"][0]["exercise"]
        assert fact["sources"] == route["sources"]
        assert "event: roadmap_node_completed" in events
        assert done["roadmap"] == current
        todos = c.get("/api/todos").json()
        again, _ = action(c, session, "again", "complete_todo", {"todo_id": todo_id})
        assert again["roadmap"] == current
        assert c.get("/api/todos").json() == todos
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        assert c.get(f"/api/roadmaps/{route['id']}").json() == current


def test_chat_completes_linked_todo_and_snapshot_survives_later_edit(tmp_path):
    todo_id = ""

    def provider(request):
        body = json.loads(request.content)
        if body.get("tools") and body.get("messages", [{}])[-1].get(
            "content", ""
        ).startswith("完成待办"):
            return httpx.Response(
                200, json=operation_response("complete_todo", {"todo_id": todo_id})
            )
        return roadmap_provider([])(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        todo_id = accepted["todo_ids"][0]
        done, _ = submit(c, f"完成待办 {todo_id}", "chat-done", session)
        node = done["roadmap"]["nodes"][0]
        assert node["status"] == "completed"
        assert node["completion"]["content"] == f"完成待办 {todo_id}"
        action(
            c, session, "edit", "update_todo", {"todo_id": todo_id, "title": "新标题"}
        )
        current = c.get(f"/api/roadmaps/{route['id']}").json()["nodes"][0]
        assert current["todo"]["title"] == "新标题"
        assert current["completion"] == node["completion"]


@pytest.mark.parametrize("linked", [False, True])
def test_concurrent_mastery_and_acceptance_recheck_committed_state(tmp_path, linked):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(3)]
        if linked:
            accepted, _ = action(
                c, sessions[0], "accept", "accept_roadmap_node", target(route)
            )
            route = accepted["roadmap"]
        jobspec = [("complete_roadmap_node", target(route))] * 2
        jobspec.append(
            ("complete_todo", {"todo_id": route["nodes"][0]["todo_id"]})
            if linked
            else ("accept_roadmap_node", target(route))
        )
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(
                pool.map(
                    lambda i: action(c, sessions[i], f"race-{i}", *jobspec[i])[0],
                    range(3),
                )
            )
        assert all(r["status"] == "completed" for r in results)
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        # Acceptance may win and invalidate the mastery confirmation; refresh it.
        done, _ = action(
            c, sessions[0], "refresh-done", "complete_roadmap_node", target(current)
        )
        current = done["roadmap"]
        assert current["progress"]["completed"] == 1
        node = current["nodes"][0]
        assert node["completion"]
        todos = c.get("/api/todos").json()
        assert len(todos) == int(bool(node["todo_id"]))
        assert all(t["status"] == "completed" for t in todos)
        repeated, _ = action(
            c, sessions[1], "duplicate", "complete_roadmap_node", target(route)
        )
        assert repeated["roadmap"] == current
        assert c.get("/api/todos").json() == todos


@pytest.mark.parametrize("tool", ["complete_todo", "complete_roadmap_node"])
@pytest.mark.parametrize("table", ["roadmap_completions", "events"])
def test_completion_failure_rolls_back_and_retry_commits_once(tmp_path, tool, table):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        args = (
            {"todo_id": accepted["todo_ids"][0]}
            if tool == "complete_todo"
            else target(route)
        )
        todos = c.get("/api/todos").json()
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            condition = (
                " WHEN NEW.kind='roadmap_node_completed'" if table == "events" else ""
            )
            db.execute(
                f"CREATE TRIGGER fail_completion BEFORE INSERT ON {table}{condition} "
                "BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END"
            )
        failed, events = action(c, session, "failure", tool, args)
        assert failed["status"] == "failed" and failed["error"] == "storage"
        assert "event: roadmap_node_completed" not in events
        assert c.get("/api/todos").json() == todos
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute("DROP TRIGGER fail_completion")
        retry = c.post("/api/runs/failure/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert done["status"] == "completed"
        assert done["roadmap"]["progress"]["completed"] == 1
        assert not done["retryable"]
        assert c.post(f"/api/runs/{done['id']}/retry").json()["id"] == done["id"]


def test_invalid_and_stale_mastery_does_not_write(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        other, _ = submit(c, REQUEST, "other-route")
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        current = accepted["roadmap"]
        for i, args in enumerate(
            [
                target(route, 1),
                target(current) | {"node_id": other["roadmap"]["nodes"][0]["id"]},
                target(current) | {"roadmap_id": "missing"},
            ]
        ):
            run, _ = action(c, session, f"invalid-{i}", "complete_roadmap_node", args)
            assert not run["roadmap"]
            assert "未修改" in run["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == current
        ambiguous, _ = submit(
            c, "把节点 练习 Redis 字符串读写 标记为已掌握", "ambiguous", session
        )
        assert "不明确" in ambiguous["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == current


def test_mastery_with_and_without_todo_and_repeated_acceptance(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        done, events = action(
            c, session, "mastered", "complete_roadmap_node", target(route)
        )
        assert done["model_calls"] == 0
        assert "event: roadmap_node_completed" in events
        assert done["roadmap"]["nodes"][0]["todo"]["status"] == "completed"
        assert done["roadmap"]["nodes"][0]["completion"]["operation"] == "mastered"
        other = c.post("/api/sessions").json()["id"]
        done, _ = submit(
            c, f"把节点 {route['nodes'][1]['id']} 标记为已掌握", "chat", other
        )
        assert done["status"] == "completed"
        assert done["model_calls"] == 0
        current = done["roadmap"]
        assert current["progress"] == {"completed": 2, "total": 2, "remaining": 0}
        assert current["nodes"][1]["todo_id"] is None
        assert len(c.get("/api/todos").json()) == 1
        again, _ = action(c, other, "repeat", "complete_roadmap_node", target(route, 1))
        assert again["roadmap"] == current
        for i in range(2):
            skipped, _ = action(
                c, other, f"skip-{i}", "accept_roadmap_node", target(route, i)
            )
            assert "已完成" in skipped["reply"]
            assert None not in skipped["todo_ids"]
        batch, _ = action(c, other, "batch", "accept_roadmap_nodes", selection(route))
        assert "跳过已完成 2" in batch["reply"]
        assert len(c.get("/api/todos").json()) == 1
        assert c.get(f"/api/roadmaps/{route['id']}").json() == current


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("after_commit", [False, True])
def test_completion_process_death_and_sse_replay(tmp_path, after_commit, linked):
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
        if linked:
            accepted, _ = action(
                c, session, "accept", "accept_roadmap_node", target(route)
            )
            route = accepted["roadmap"]
        args = target(route)
        if not after_commit:
            # Hold a preceding request at the provider boundary until process death.
            gate.unlink()
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": "blocked", "content": REQUEST},
            ).raise_for_status()
        body = {
            "request_id": "batch",
            "content": "确认标记已掌握",
            "action": {"tool": "complete_roadmap_node", "arguments": args},
        }
        c.post(f"/api/sessions/{session}/messages", json=body).raise_for_status()
        if after_commit:
            # Disconnect immediately after the committed completion event, without
            # waiting for the terminal event, then really kill the process.
            with c.stream("GET", "/api/runs/batch/events") as stream:
                for line in stream.iter_lines():
                    if line == "event: roadmap_node_completed":
                        break
                else:
                    raise AssertionError("missing completion event")
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
        assert len(c.get("/api/todos").json()) == int(linked)
        assert original["status"] == ("completed" if after_commit else "failed")
        assert c.post(f"/api/sessions/{session}/messages", json=body).json() == original
        retry = c.post("/api/runs/batch/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert done["status"] == "completed"
        assert done["roadmap"]["progress"]["completed"] == 1
        saved = next(e for e in done["events"] if e["kind"] == "roadmap_node_completed")
        assert saved["data"]["created"]
        events = c.get(
            f"/api/runs/{done['id']}/events",
            headers={"Last-Event-ID": str(saved["seq"])},
        ).text
        assert (
            "event: roadmap_node_completed" not in events
            and "event: terminal" in events
        )
        other = c.post("/api/sessions").json()["id"]
        repeated, _ = action(c, other, "again", "complete_roadmap_node", args)
        assert repeated["roadmap"] == done["roadmap"]
        todos = c.get("/api/todos").json()
        assert len(todos) == int(linked)
        assert all(t["status"] == "completed" for t in todos)
        assert c.get(f"/api/roadmaps/{route['id']}").json() == done["roadmap"]

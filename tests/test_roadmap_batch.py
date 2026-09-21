"""Batch acceptance through HTTP/SSE with a real temporary SQLite database."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_roadmaps import (
    REQUEST,
    roadmap_answer,
    roadmap_client,
    roadmap_provider,
)


def batch_provider(request):
    body = json.loads(request.content)
    if body.get("tools", [{}])[0].get("function", {}).get("name") == "roadmap_answer":
        answer = roadmap_answer()
        answer["nodes"].append(
            answer["nodes"][1]
            | {
                "goal": "验证缓存缺失",
                "exercise": "读取不存在的键",
                "completion_criteria": "读取结果为空",
                "todo_title": "验证 Redis 缓存缺失",
            }
        )
        return httpx.Response(200, json=operation_response("roadmap_answer", answer))
    return roadmap_provider([])(request)


def selection(route, nodes=None):
    return {
        "roadmap_id": route["id"],
        "node_ids": nodes if nodes is not None else [n["id"] for n in route["nodes"]],
        "expected_version": route["version"],
    }


def test_batch_acceptance_replay_and_cross_session_remaining_nodes(tmp_path):
    with roadmap_client(tmp_path, [], batch_provider) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        session = generated["session_id"]
        assert c.get("/api/todos").json() == []
        args = selection(route, [n["id"] for n in route["nodes"][:2]])
        first, events = action(c, session, "first", "accept_roadmap_nodes", args)
        assert first["status"] == "completed"
        assert "event: roadmap_nodes_accepted" in events
        assert "本次新增 2" in first["reply"]
        assert len(c.get("/api/todos").json()) == 2
        assert first["roadmap"]["nodes"][2]["todo_id"] is None
        replay, _ = action(c, session, "first", "accept_roadmap_nodes", args)
        assert replay == first
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        other = c.post("/api/sessions").json()["id"]
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        args = selection(route)
        rest, _ = action(c, other, "rest", "accept_roadmap_nodes", args)
        assert "本次新增 1" in rest["reply"] and "已加入 2" in rest["reply"]
        repeated, _ = action(c, other, "again", "accept_roadmap_nodes", args)
        assert "本次新增 0" in repeated["reply"] and "已加入 3" in repeated["reply"]
        assert repeated["model_calls"] == 0
        todos = c.get("/api/todos").json()
        assert len(todos) == 3
        assert all(t["scheduled_date"] is None for t in todos)
        assert len({t["roadmap"]["node_id"] for t in todos}) == 3


def test_chat_batch_requires_unambiguous_route_and_resolves_stable_identity(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        first, _ = submit(c, REQUEST)
        submit(c, REQUEST, "another-route")
        route = first["roadmap"]
        ambiguous, _ = submit(c, "把路线 Redis 缓存入门 全部加入待办", "ambiguous")
        assert "不明确" in ambiguous["reply"]
        assert c.get("/api/todos").json() == []
        accepted, _ = submit(c, f"把路线 {route['id']} 全部加入待办", "accept")
        assert "本次新增 2" in accepted["reply"]
        assert accepted["model_calls"] == 0
        assert len(c.get("/api/todos").json()) == 2


def test_batch_empty_invalid_stale_and_completed_nodes(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        for rid, node_ids in [
            ("empty", []),
            ("invalid", [route["nodes"][0]["id"], "missing"]),
        ]:
            run, _ = action(
                c, session, rid, "accept_roadmap_nodes", selection(route, node_ids)
            )
            assert not run["todo_ids"]
            assert c.get("/api/roadmaps/" + route["id"]).json() == route
            assert c.get("/api/todos").json() == []
        node_id = route["nodes"][0]["id"]
        accepted, _ = action(
            c,
            session,
            "one",
            "accept_roadmap_nodes",
            selection(route, [node_id, node_id]),
        )
        assert len(accepted["todo_ids"]) == 1
        stale, _ = action(c, session, "stale", "accept_roadmap_nodes", selection(route))
        assert "已变化" in stale["reply"]
        assert len(c.get("/api/todos").json()) == 1
        action(
            c,
            session,
            "complete",
            "complete_todo",
            {"todo_id": accepted["todo_ids"][0]},
        )
        current = c.get("/api/roadmaps/" + route["id"]).json()
        again, _ = action(c, session, "all", "accept_roadmap_nodes", selection(current))
        assert "本次新增 1" in again["reply"]
        assert again["roadmap"]["nodes"][0]["todo"]["status"] == "completed"
        assert len(c.get("/api/todos").json()) == 2


def test_concurrent_batches_and_single_accept_share_one_association(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(3)]
        batch = selection(route)
        single = {
            "roadmap_id": route["id"],
            "node_id": route["nodes"][0]["id"],
            "expected_version": 1,
        }
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [
                pool.submit(
                    action, c, sessions[i], f"race-{i}", "accept_roadmap_nodes", batch
                )
                for i in range(2)
            ]
            jobs.append(
                pool.submit(
                    action, c, sessions[2], "race-single", "accept_roadmap_node", single
                )
            )
            results = [job.result()[0] for job in jobs]
        assert all(r["status"] == "completed" for r in results)
        # A stale overlapping selection may require refresh, but never duplicates.
        current = c.get("/api/roadmaps/" + route["id"]).json()
        action(c, sessions[0], "remaining", "accept_roadmap_nodes", selection(current))
        assert len(c.get("/api/todos").json()) == 2


@pytest.mark.parametrize("fail_at", [1, 2])
def test_sqlite_write_failure_rolls_back_entire_batch_and_explicit_retry(
    tmp_path, fail_at
):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute(
                "CREATE TRIGGER fail_batch BEFORE INSERT ON todos "
                f"WHEN (SELECT count(*) FROM todos) = {fail_at - 1} "
                "BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END"
            )
        args = selection(route)
        failed, events = action(c, session, "failure", "accept_roadmap_nodes", args)
        assert failed["status"] == "failed" and failed["error"] == "storage"
        assert failed["todo_ids"] == []
        assert "event: roadmap_nodes_accepted" not in events
        assert c.get("/api/todos").json() == []
        assert c.get("/api/roadmaps/" + route["id"]).json() == route
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute("DROP TRIGGER fail_batch")
        replay, _ = action(c, session, "failure", "accept_roadmap_nodes", args)
        assert replay == failed
        retry = c.post("/api/runs/failure/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        assert len(c.get("/api/todos").json()) == 2

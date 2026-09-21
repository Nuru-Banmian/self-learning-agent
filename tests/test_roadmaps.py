"""Public HTTP/SSE with real SQLite; only provider traffic is simulated."""

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action, operation_response

REQUEST = "我想学习 Redis，有 Python 基础，目标是实现缓存，每次可投入30分钟"


def complete_intake(content):
    return {
        "request_id": None,
        "needed_fields": ["goal", "background", "time_budget"],
        "topic": "Redis",
        "goal": "实现缓存",
        "background": "Python 基础" if "Python 基础" in content else "我会 Python",
        "time_budget": re.search(r"每次[^，。]*?30分钟", content)[0],
    }


def roadmap_answer():
    return {
        "title": "Redis 缓存入门",
        "goal": "实现并验证一个带过期时间的缓存",
        "nodes": [
            {
                "goal": "理解字符串读写",
                "estimated_minutes": 30,
                "source_ids": ["S1"],
                "exercise": "在本地运行 SET greeting hello，再用 GET greeting 读取",
                "completion_criteria": "GET 返回 hello，能解释键和值",
                "todo_title": "练习 Redis 字符串读写",
            },
            {
                "goal": "验证缓存过期",
                "estimated_minutes": 30,
                "source_ids": ["S1"],
                "exercise": "设置 EX 10 并用 TTL 观察，10秒后读取",
                "completion_criteria": "过期前返回值，过期后返回空",
                "todo_title": "验证 Redis 缓存过期",
            },
        ],
        "memory_usage": [],
        "gaps": [],
    }


def roadmap_provider(requests):
    def provider(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if request.url.path == "/search/unified":
            return httpx.Response(
                200,
                json={
                    "pageItems": [
                        {
                            "title": "Redis strings",
                            "link": "https://redis.io/docs/latest/"
                            "develop/data-types/strings/",
                            "snippet": "SET stores a string value; "
                            "EX sets expiry in seconds.",
                        }
                    ]
                },
            )
        if "response_format" in body:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
            )
        if body["tools"][0]["function"]["name"] == "roadmap_answer":
            result = operation_response("roadmap_answer", roadmap_answer())
        else:
            result = operation_response(
                "plan_learning_roadmap",
                {
                    "intake": complete_intake(body["messages"][-1]["content"]),
                    "query": "Redis strings expiry official documentation",
                    "todo_ids": [],
                    "memory_ids": [],
                    "read_body": False,
                },
            )
        return httpx.Response(200, json=result)

    return provider


def roadmap_client(tmp_path, requests, provider=None, **settings):
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "roadmaps.db",
                **({"dashscope_api_key": "test", "iqs_api_key": "test-iqs"} | settings),
            ),
            transport=httpx.MockTransport(provider or roadmap_provider(requests)),
        )
    )


@pytest.mark.parametrize(
    "request_text,save_preference,expect_gap",
    [
        (REQUEST, True, True),
        (REQUEST + "，优先官方资料", False, True),
        (REQUEST + "，这次只看视频", True, False),
        (REQUEST + "，这次不要官方资料，只看视频", True, False),
        (REQUEST + "，这次优先官方资料但不要视频", False, True),
    ],
)
def test_official_preference_uncertainty_survives_model_omission_and_restart(
    tmp_path, request_text, save_preference, expect_gap
):
    from tests.test_memory import candidate

    preference = "我喜欢优先阅读官方资料"
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            candidates = [candidate(source)] if source["content"] == preference else []
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"candidates": candidates})}}
                    ]
                },
            )
        if body.get("messages", [{}])[-1].get("content") == preference:
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question", {"reply": "收到", "memory_usage": []}
                ),
            )
        if request.url.path == "/search/unified":
            return httpx.Response(
                200,
                json={
                    "pageItems": [
                        {
                            "title": "Redis 官方教程转载",
                            "link": "https://example.com/redis",
                            "snippet": "SET GET EX",
                        }
                    ]
                },
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        if save_preference:
            saved, _ = submit(c, preference, "preference")
            assert saved["memory"]["saved_ids"]
        run, events = submit(c, request_text, "route")
        route = run["roadmap"]
        assert "event: terminal" in events
        assert (
            any("官方" in gap and "核实" in gap for gap in route["gaps"]) == expect_gap
        )
        if expect_gap:
            assert run["status"] == route["status"] == "partial"
            assert route["gaps"][0] in run["reply"]
            assert route["gaps"][0] in run["research"]["gaps"]
        else:
            assert route["gaps"] == []
        assert c.get("/api/todos").json() == []
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route


def test_natural_learning_request_saves_sourced_ordered_roadmap_without_todos(tmp_path):
    requests = []
    with roadmap_client(tmp_path, requests) as c:
        run, events = submit(c, REQUEST)
        assert run["status"] == "completed", run
        assert "event: roadmap_saved" in events
        assert '"role": "execution"' in events
        assert any("/search/unified" in url for url, _ in requests)
        assert c.get("/api/todos").json() == []
        routes = c.get("/api/roadmaps").json()
        assert len(routes) == 1
        route = c.get(f"/api/roadmaps/{routes[0]['id']}").json()
        assert route["id"] == run["roadmap"]["id"]
        assert route["request"] == REQUEST
        assert [n["position"] for n in route["nodes"]] == [1, 2]
        assert len({n["id"] for n in route["nodes"]}) == 2
        assert [n["estimated_minutes"] for n in route["nodes"]] == [30, 30]
        assert all(
            n["goal"] and n["exercise"] and n["completion_criteria"] and n["todo_title"]
            for n in route["nodes"]
        )
        assert route["sources"][0]["material_type"] == "snippet"
        assert all(
            n["source_ids"] == ["S1"] and n["todo_id"] is None for n in route["nodes"]
        )
        assert not run["retryable"]
        assert c.post(f"/api/runs/{run['id']}/retry").json()["id"] == run["id"]
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        c.post("/api/sessions")
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert len(c.get("/api/roadmaps").json()) == 1


def test_single_node_acceptance_survives_cross_session_replay_without_model(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        route = run["roadmap"]
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        session = c.post("/api/sessions").json()["id"]
        args = {
            "roadmap_id": route["id"],
            "node_id": route["nodes"][0]["id"],
            "expected_version": 1,
        }
        for rid in ("accept", "accept", "accept-again"):
            accepted, events = action(c, session, rid, "accept_roadmap_node", args)
            assert accepted["status"] == "completed"
            assert accepted["model_calls"] == 0
            assert "event: roadmap_node_accepted" in events
            todos = c.get("/api/todos").json()
            assert len(todos) == 1
            assert todos[0]["scheduled_date"] is None
            assert todos[0]["roadmap"] == {
                "id": route["id"],
                "node_id": args["node_id"],
            }
            assert accepted["todo_ids"] == [todos[0]["id"]]
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        assert current["nodes"][0]["todo"]["id"] == todos[0]["id"]
        assert current["nodes"][1]["todo_id"] is None
        assert current["nodes"][0]["exercise"] == route["nodes"][0]["exercise"]


@pytest.mark.parametrize(
    "content",
    [
        "解释一下学习路线是什么意思",
        "他说‘我想学习 Redis’，这句话什么意思",
        "我想学习 Redis，但不要搜索",
        "我不要学习 Redis",
        REQUEST + "，不要在网上搜索资料",
        REQUEST + "，不要再帮我搜索",
        "请记录明天学习 Redis",
        "记一条明天学习 Redis 的待办",
    ],
)
def test_non_planning_intent_cannot_be_changed_to_roadmap_by_model(tmp_path, content):
    requests = []
    with roadmap_client(tmp_path, requests) as c:
        run, _ = submit(c, content)
        assert not run["roadmap"]
        assert c.get("/api/roadmaps").json() == []
        assert not any("/search/unified" in url for url, _ in requests)


def test_learning_without_dates_still_generates_route(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST + "，不需要排期")
        assert run["roadmap"]
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize("negative", ["不要在网上搜索资料", "不要再帮我搜索"])
def test_negative_search_cannot_be_bypassed_with_ordinary_research(tmp_path, negative):
    requests = []
    base = roadmap_provider(requests)

    def provider(request):
        response = base(request)
        payload = response.json()
        if "choices" in payload:
            message = payload["choices"][0]["message"]
            for call in message.get("tool_calls", []):
                if call["function"]["name"] == "plan_learning_roadmap":
                    call["function"]["name"] = "research_learning"
        return httpx.Response(200, json=payload)

    with roadmap_client(tmp_path, requests, provider) as c:
        run, _ = submit(c, REQUEST + "，" + negative)
        assert not any("/search/unified" in url for url, _ in requests)
        assert not run["roadmap"]
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize(
    "content",
    [
        "我有 Python 基础，想学习 Redis，目标是实现缓存，每次可投入30分钟",
        "我想学习 Redis 的数据记录和过期，有 Python 基础，"
        "目标是实现缓存，每次可投入30分钟",
        REQUEST + "，暂时不要加入待办",
        REQUEST + "，不要学习集群，只学基础缓存",
        "请帮我制定 Redis 学习路线，我会 Python，目标是实现缓存，"
        "每次可投入30分钟，不要学习集群，只学基础缓存",
        REQUEST + "，请说明每个节点的完成标准",
        "我想学习 Redis 的‘SET’命令，有 Python 基础，目标是实现缓存，每次可投入30分钟",
    ],
)
def test_complete_learning_intent_does_not_require_fixed_words(tmp_path, content):
    requests = []
    with roadmap_client(tmp_path, requests) as c:
        run, _ = submit(c, content)
        assert run["roadmap"]
        assert any("/search/unified" in url for url, _ in requests)
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize(
    "kind,expected,has_route",
    [
        ("empty", "empty", False),
        ("error", "error", False),
        ("body", "partial", True),
        ("organization", "partial", False),
        ("fake_source", "partial", False),
        ("timeout", "partial", False),
    ],
)
def test_failed_research_keeps_actual_material_without_fake_roadmap(
    tmp_path, kind, expected, has_route
):
    requests = []
    base = roadmap_provider(requests)

    async def provider(request):
        body = json.loads(request.content)
        if request.url.path == "/search/unified" and kind in ("empty", "error"):
            return (
                httpx.Response(200, json={"pageItems": []})
                if kind == "empty"
                else httpx.Response(503)
            )
        if request.url.path == "/readpage/basic":
            return httpx.Response(200, json={"data": {"statusCode": 403, "text": None}})
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            if kind == "timeout":
                await asyncio.sleep(2)
            answer = roadmap_answer()
            if kind == "organization":
                answer["nodes"][0]["estimated_minutes"] = 0
            if kind == "fake_source":
                answer["nodes"][0]["source_ids"] = ["S999"]
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        response = base(request)
        if kind == "body" and "tools" in body:
            return httpx.Response(
                200,
                json=operation_response(
                    "plan_learning_roadmap",
                    {
                        "intake": complete_intake(REQUEST),
                        "query": "Redis official strings",
                        "todo_ids": [],
                        "memory_ids": [],
                        "read_body": True,
                    },
                ),
            )
        return response

    with roadmap_client(
        tmp_path,
        requests,
        provider,
        run_timeout_seconds=0.4 if kind == "timeout" else 60,
    ) as c:
        run, events = submit(c, REQUEST)
        assert run["status"] == "partial"
        assert run["research"]["status"] == expected
        assert bool(run["roadmap"]) == has_route
        assert c.get("/api/todos").json() == []
        assert len(c.get("/api/roadmaps").json()) == int(has_route)
        assert "event: terminal" in events
        if kind not in ("empty", "error"):
            assert run["research"]["sources"][0]["material_type"] == "snippet"
            assert run["research"]["gaps"]
        if has_route:
            assert run["roadmap"]["gaps"]
            assert not run["retryable"]


def test_committed_route_with_learning_failure_is_not_regenerated_on_retry(tmp_path):
    calls = []
    base = roadmap_provider(calls)

    def provider(request):
        if "response_format" in json.loads(request.content):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}}]}
            )
        return base(request)

    with roadmap_client(tmp_path, calls, provider) as c:
        run, _ = submit(c, "我喜欢官方资料，" + REQUEST)
        assert run["status"] == "partial" and run["error"] == "memory_learning"
        assert run["roadmap"] and not run["retryable"]
        count = len(calls)
        assert c.post(f"/api/runs/{run['id']}/retry").json() == run
        assert len(calls) == count
        assert len(c.get("/api/roadmaps").json()) == 1


def test_concurrent_accepts_invalid_membership_and_stale_selection(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        route = run["roadmap"]
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(2)]
        args = {
            "roadmap_id": route["id"],
            "node_id": route["nodes"][0]["id"],
            "expected_version": 1,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda i: action(
                        c, sessions[i], f"accept-{i}", "accept_roadmap_node", args
                    )[0],
                    range(2),
                )
            )
        assert all(r["status"] == "completed" for r in results)
        assert len(c.get("/api/todos").json()) == 1
        args["node_id"] = route["nodes"][1]["id"]
        stale, _ = action(c, sessions[0], "stale", "accept_roadmap_node", args)
        assert not stale["todo_ids"] and "已变化" in stale["reply"]
        args["node_id"] = "nonexistent"
        invalid, _ = action(c, sessions[0], "invalid", "accept_roadmap_node", args)
        assert not invalid["todo_ids"]
        second, _ = submit(c, REQUEST, "second")
        args["node_id"] = second["roadmap"]["nodes"][0]["id"]
        mismatch, _ = action(c, sessions[0], "mismatch", "accept_roadmap_node", args)
        assert "不属于" in mismatch["reply"]
        assert len(c.get("/api/todos").json()) == 1


def test_chat_explicit_node_and_ambiguous_titles_do_not_depend_on_model(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        run, _ = submit(c, REQUEST)
        node = run["roadmap"]["nodes"][0]
        submit(c, REQUEST, "second")
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        ambiguous, _ = submit(c, f"把节点 {node['todo_title']} 加入待办", "ambiguous")
        assert "不明确" in ambiguous["reply"]
        assert c.get("/api/todos").json() == []
        accepted, _ = submit(c, f"把节点 {node['id']} 加入待办", "chat-accept")
        assert accepted["model_calls"] == 0
        assert len(c.get("/api/todos").json()) == 1


def test_roadmap_access_does_not_allow_cross_session_ordinary_suggestion(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        submit(c, REQUEST)

    def provider(request):
        return httpx.Response(
            200, json=operation_response("plan_day", {"suggestions": ["整理笔记"]})
        )

    with roadmap_client(tmp_path, [], provider) as c:
        planned, _ = submit(c, "今天我该干什么", "ordinary")
        suggestion = c.get(f"/api/sessions/{planned['session_id']}/suggestions").json()[
            0
        ]
        other = c.post("/api/sessions").json()["id"]
        run, _ = action(
            c,
            other,
            "forbidden",
            "accept_suggestion",
            {"suggestion_id": suggestion["id"]},
        )
        assert "当前会话" in run["reply"] and c.get("/api/todos").json() == []
        assert len(c.get("/api/roadmaps").json()) == 1


def test_empty_targeted_search_can_use_bounded_broader_search_with_gap(tmp_path):
    calls = []
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if request.url.path == "/search/unified":
            calls.append(body["query"])
            if "site:" in body["query"]:
                return httpx.Response(200, json={"pageItems": []})
        elif (
            "tools" in body and body["tools"][0]["function"]["name"] != "roadmap_answer"
        ):
            return httpx.Response(
                200,
                json=operation_response(
                    "plan_learning_roadmap",
                    {
                        "intake": complete_intake(REQUEST),
                        "query": "Redis TTL site:redis.io",
                        "todo_ids": [],
                        "memory_ids": [],
                        "read_body": False,
                    },
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        run, _ = submit(c, REQUEST)
        assert run["roadmap"]
        assert run["status"] == "partial"
        assert calls == ["Redis TTL site:redis.io", "Redis TTL"]
        assert "定向检索" in " ".join(run["roadmap"]["gaps"])
        assert c.get("/api/todos").json() == []

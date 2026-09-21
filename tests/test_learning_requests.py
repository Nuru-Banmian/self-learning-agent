"""Learning clarification through public HTTP/SSE and persistent SQLite."""

import asyncio
import json
import threading
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_memory import candidate
from tests.test_memory_maintenance import target
from tests.test_roadmaps import roadmap_client, roadmap_provider


def intake_provider(requests):
    base = roadmap_provider(requests)

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "plan_learning_roadmap"
        ):
            context = json.loads(body["messages"][1]["content"])
            pending = context.get("learning_requests", [])
            content = body["messages"][-1]["content"]
            continued = "缓存" in content
            return httpx.Response(
                200,
                json=operation_response(
                    "plan_learning_roadmap",
                    {
                        "query": "Redis cache official documentation",
                        "todo_ids": [],
                        "memory_ids": [],
                        "read_body": False,
                        "intake": {
                            "request_id": pending[0]["id"] if pending else None,
                            "needed_fields": ["goal", "background", "time_budget"],
                            "topic": "Redis",
                            "goal": "实现缓存" if continued else None,
                            "background": "Python 基础",
                            "time_budget": "每次30分钟",
                        },
                    },
                ),
            )
        return base(request)

    return provider


def test_missing_goal_survives_restart_and_new_session_reply(tmp_path):
    requests = []
    with roadmap_client(tmp_path, requests, intake_provider(requests)) as c:
        run, events = submit(c, "我想学习 Redis，有 Python 基础，每次30分钟", "initial")
        assert not run["roadmap"]
        assert "event: learning_request_saved" in events
        pending = c.get("/api/learning-requests").json()
        assert len(pending) == 1
        assert pending[0]["questions"] == ["希望学完后能做什么？"]
        assert not requests
        assert c.get("/api/todos").json() == []

    with roadmap_client(tmp_path, requests, intake_provider(requests)) as c:
        assert c.get("/api/learning-requests").json() == pending
        run, events = submit(c, "目标是实现缓存", "reply")
        assert run["roadmap"], run
        assert "Redis" in run["roadmap"]["request"]
        assert "实现缓存" in run["roadmap"]["request"]
        assert (
            c.get("/api/learning-requests").json()[0]["roadmap_id"]
            == run["roadmap"]["id"]
        )
        assert c.get("/api/todos").json() == []


def test_repeated_intent_does_not_create_a_second_pending_goal(tmp_path):
    requests = []
    with roadmap_client(tmp_path, requests, intake_provider(requests)) as c:
        content = "我想学习 Redis，有 Python 基础，每次30分钟"
        submit(c, content, "first")

        # A provider may treat a repeated initial message as a fresh intent.
        def provider(request):
            response = intake_provider(requests)(request)
            body = response.json()
            calls = (
                body.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
            )
            for call in calls:
                if call["function"]["name"] == "plan_learning_roadmap":
                    args = json.loads(call["function"]["arguments"])
                    args["intake"]["request_id"] = None
                    call["function"]["arguments"] = json.dumps(args)
            return httpx.Response(200, json=body)

    with roadmap_client(tmp_path, requests, provider) as c:
        submit(c, content, "replayed")
        assert len(c.get("/api/learning-requests").json()) == 1


def memory_intake_provider(requests):
    base = intake_provider(requests)

    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            proposals = [candidate(source, category="background", topic="Python")]
            if source["content"] != "我有 Python 基础":
                proposals = []
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"candidates": proposals})}}
                    ]
                },
            )
        if body.get("messages", [{}])[-1].get("content") == "我有 Python 基础":
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question", {"reply": "收到", "memory_usage": []}
                ),
            )
        response = base(request)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "plan_learning_roadmap"
        ):
            payload = response.json()
            call = payload["choices"][0]["message"]["tool_calls"][0]["function"]
            args = json.loads(call["arguments"])
            context = json.loads(body["messages"][1]["content"])
            content = body["messages"][-1]["content"]
            args["intake"]["background"] = (
                "从零开始"
                if "从零开始" in content
                else "Python 基础"
                if any("Python 基础" in m["content"] for m in context["memories"])
                else None
            )
            call["arguments"] = json.dumps(args)
            return httpx.Response(200, json=payload)
        return response

    return provider


def test_existing_background_reduces_questions_and_deleted_memory_is_rechecked(
    tmp_path,
):
    requests = []
    with roadmap_client(tmp_path, requests, memory_intake_provider(requests)) as c:
        submit(c, "我有 Python 基础", "background")
        run, _ = submit(c, "我想学习 Redis，每次30分钟", "start")
        pending = c.get("/api/learning-requests").json()[0]
        assert pending["questions"] == ["希望学完后能做什么？"]
        assert run["memory"]["usage"]
        memory = c.get("/api/memories").json()[0]
        session = c.post("/api/sessions").json()["id"]
        deleted, _ = action(c, session, "delete", "delete_memory", target(memory))
        assert deleted["status"] == "completed"
        reply, _ = submit(c, "目标是实现缓存", "goal")
        assert not reply["roadmap"]
        pending = c.get("/api/learning-requests").json()[0]
        assert pending["questions"] == ["目前有哪些相关基础？"]
        assert pending["memories"] == []


def test_multiple_goals_require_selection_then_only_selected_goal_completes(tmp_path):
    requests = []
    base = intake_provider(requests)

    def provider(request):
        response = base(request)
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            != "plan_learning_roadmap"
        ):
            return response
        context = json.loads(body["messages"][1]["content"])
        payload = response.json()
        call = payload["choices"][0]["message"]["tool_calls"][0]["function"]
        args = json.loads(call["arguments"])
        if "SQLite" in body["messages"][-1]["content"]:
            args["intake"].update(request_id=None, topic="SQLite")
        elif context.get("selected_learning_request_id"):
            args["intake"].update(
                request_id=context["selected_learning_request_id"], topic="SQLite"
            )
        call["arguments"] = json.dumps(args)
        return httpx.Response(200, json=payload)

    with roadmap_client(tmp_path, requests, provider) as c:
        submit(c, "我想学习 Redis，有 Python 基础，每次30分钟", "redis")
        submit(c, "我想学习 SQLite，有 Python 基础，每次30分钟", "sqlite")
        pending = c.get("/api/learning-requests").json()
        assert len(pending) == 2
        reply, _ = submit(c, "目标是实现缓存", "ambiguous")
        assert "多个" in reply["reply"]
        assert c.get("/api/learning-requests").json() == pending
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "selected",
                "content": "目标是实现缓存",
                "action": {
                    "tool": "continue_learning",
                    "arguments": {"request_id": pending[1]["id"]},
                },
            },
        ).raise_for_status()
        c.get("/api/runs/selected/events")
        result = c.get("/api/runs/selected").json()
        assert result["roadmap"], result
        current = c.get("/api/learning-requests").json()
        assert current[0]["roadmap_id"] is None
        assert current[1]["roadmap_id"] == result["roadmap"]["id"]


def test_current_correction_overrides_background_without_changing_long_term_memory(
    tmp_path,
):
    requests = []
    with roadmap_client(tmp_path, requests, memory_intake_provider(requests)) as c:
        submit(c, "我有 Python 基础", "background")
        submit(c, "我想学习 Redis，每次30分钟", "start")
        before = c.get("/api/memories").json()
        run, _ = submit(c, "这次按从零开始，目标是实现缓存", "correct")
        assert run["roadmap"], run
        pending = c.get("/api/learning-requests").json()[0]
        assert pending["known"]["background"] == "从零开始"
        assert "这次按从零开始" in run["roadmap"]["request"]
        assert c.get("/api/memories").json() == before
        replay, _ = submit(c, "这次按从零开始，目标是实现缓存", "replay")
        assert "已生成" in replay["reply"]
        assert len(c.get("/api/roadmaps").json()) == 1
        assert c.post(f"/api/runs/{run['id']}/retry").json()["id"] == run["id"]
        assert c.get("/api/todos").json() == []


def test_repeating_the_topic_is_not_a_specific_learning_goal(tmp_path):
    base = intake_provider([])

    def provider(request):
        response = base(request)
        payload = response.json()
        for call in (
            payload.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
        ):
            if call["function"]["name"] == "plan_learning_roadmap":
                args = json.loads(call["function"]["arguments"])
                args["intake"]["goal"] = "学习 Redis"
                call["function"]["arguments"] = json.dumps(args)
        return httpx.Response(200, json=payload)

    with roadmap_client(tmp_path, [], provider) as c:
        run, _ = submit(c, "我想学习 Redis，有 Python 基础，每次30分钟")
        assert not run["roadmap"]
        assert c.get("/api/learning-requests").json()[0]["questions"] == [
            "希望学完后能做什么？"
        ]


def test_memory_change_during_generation_prevents_commit_and_retry_rechecks(tmp_path):
    entered, release = threading.Event(), threading.Event()
    base = memory_intake_provider([])

    async def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        submit(c, "我有 Python 基础", "background")
        submit(c, "我想学习 Redis，每次30分钟", "start")
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "waiting", "content": "目标是实现缓存"},
        )
        assert entered.wait(5)
        memory = c.get("/api/memories").json()[0]
        other = c.post("/api/sessions").json()["id"]
        action(c, other, "delete", "delete_memory", target(memory))
        release.set()
        c.get("/api/runs/waiting/events")
        run = c.get("/api/runs/waiting").json()
        assert run["status"] == "partial"
        assert not c.get("/api/roadmaps").json()
        retry = c.post("/api/runs/waiting/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        pending = c.get("/api/learning-requests").json()[0]
        assert pending["questions"] == ["目前有哪些相关基础？"]
        assert pending["memories"] == []
        assert not c.get("/api/todos").json()


def test_cross_session_concurrent_replies_and_replay_only_save_one_route(tmp_path):
    entered, release = threading.Event(), threading.Event()
    base = intake_provider([])

    async def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        submit(c, "我想学习 Redis，有 Python 基础，每次30分钟", "start")
        session = c.post("/api/sessions").json()["id"]
        payload = {"request_id": "first", "content": "目标是实现缓存"}
        c.post(f"/api/sessions/{session}/messages", json=payload)
        assert entered.wait(5)
        try:
            replay, _ = submit(c, "目标是实现缓存", "second")
            assert "正在处理" in replay["reply"]
        finally:
            release.set()
        events = c.get("/api/runs/first/events").text
        assert "event: roadmap_saved" in events
        c.post(f"/api/sessions/{session}/messages", json=payload)
        assert (
            c.get("/api/runs/first/events", headers={"Last-Event-ID": "1"}).text.count(
                "event: roadmap_saved"
            )
            == 1
        )
        replay, _ = submit(c, "目标是实现缓存", "third")
        assert "已生成" in replay["reply"]
        assert len(c.get("/api/roadmaps").json()) == 1
        assert len(c.get("/api/learning-requests").json()) == 1


def test_expired_time_memory_is_not_reused_from_saved_clarification(tmp_path):
    now = datetime(2026, 9, 21, 8, tzinfo=UTC)
    base = intake_provider([])

    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            proposals = [
                candidate(
                    source, category="condition", topic="半小时", validity="today"
                )
            ]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"candidates": proposals})}}
                    ]
                },
            )
        if body.get("messages", [{}])[-1].get("content") == "今天只有半小时":
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question", {"reply": "收到", "memory_usage": []}
                ),
            )
        response = base(request)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "plan_learning_roadmap"
        ):
            payload = response.json()
            call = payload["choices"][0]["message"]["tool_calls"][0]["function"]
            args = json.loads(call["arguments"])
            context = json.loads(body["messages"][1]["content"])
            args["intake"]["time_budget"] = "半小时" if context["memories"] else None
            call["arguments"] = json.dumps(args)
            return httpx.Response(200, json=payload)
        return response

    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "expired.db",
                dashscope_api_key="test",
                iqs_api_key="test",
            ),
            transport=httpx.MockTransport(provider),
            clock=lambda: now,
        )
    ) as c:
        submit(c, "今天只有半小时", "time")
        submit(c, "我想学习 Redis，有 Python 基础", "start")
        assert c.get("/api/learning-requests").json()[0]["questions"] == [
            "希望学完后能做什么？"
        ]
        now = datetime(2026, 9, 22, 8, tzinfo=UTC)
        run, _ = submit(c, "目标是实现缓存", "next-day")
        assert not run["roadmap"]
        assert c.get("/api/learning-requests").json()[0]["questions"] == [
            "每次或每周可以投入多少时间？"
        ]


def test_replayed_answer_does_not_attach_to_another_pending_goal(tmp_path):
    with roadmap_client(tmp_path, [], intake_provider([])) as c:
        submit(c, "我想学习 Redis，有 Python 基础，每次30分钟", "first")
        submit(c, "目标是实现缓存", "completed")
        submit(c, "我想学习 Redis，有 Python 基础，每次30分钟，先聊聊", "another")
        before = c.get("/api/learning-requests").json()
        assert len(before) == 2 and before[1]["roadmap_id"] is None
        submit(c, "目标是实现缓存", "replayed")
        assert c.get("/api/learning-requests").json() == before
        assert len(c.get("/api/roadmaps").json()) == 1


def test_pending_one_time_preference_does_not_affect_other_topics(tmp_path):
    base = intake_provider([])

    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            proposals = (
                [candidate(source)] if source["content"] == "我喜欢官方资料" else []
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"candidates": proposals})}}
                    ]
                },
            )
        text = body.get("messages", [{}])[-1].get("content", "")
        if text in ("我喜欢官方资料", "SQLite 资料适合从哪里开始？"):
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question", {"reply": "先看资料", "memory_usage": []}
                ),
            )
        response = base(request)
        if (
            "我想学习 SQLite" in text
            and body.get("tools", [{}])[0].get("function", {}).get("name")
            == "plan_learning_roadmap"
        ):
            payload = response.json()
            call = payload["choices"][0]["message"]["tool_calls"][0]["function"]
            args = json.loads(call["arguments"])
            args["intake"].update(request_id=None, topic="SQLite")
            args["query"] = "SQLite cache official documentation"
            call["arguments"] = json.dumps(args)
            return httpx.Response(200, json=payload)
        return response

    with roadmap_client(tmp_path, [], provider) as c:
        submit(c, "我喜欢官方资料", "preference")
        submit(c, "我想学习 Redis，有 Python 基础，每次30分钟，这次只看视频", "redis")
        ordinary, _ = submit(c, "SQLite 资料适合从哪里开始？", "ordinary")
        assert [m["content"] for m in ordinary["memory"]["loaded"]] == [
            "我喜欢官方资料"
        ]
        fresh, _ = submit(
            c, "我想学习 SQLite，有 Python 基础，每次30分钟，目标是实现缓存", "sqlite"
        )
        assert fresh["roadmap"], fresh
        assert "官方资料" in fresh["research"]["task"]["query"]
        # The temporary exception still applies when actually continuing Redis.
        pending = c.get("/api/learning-requests").json()[0]
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "redis-reply",
                "content": "目标是实现缓存",
                "action": {
                    "tool": "continue_learning",
                    "arguments": {"request_id": pending["id"]},
                },
            },
        )
        c.get("/api/runs/redis-reply/events")
        continued = c.get("/api/runs/redis-reply").json()
        assert continued["roadmap"]
        assert not continued["memory"]["loaded"]


def test_time_budget_not_required_when_it_does_not_affect_requested_overview(tmp_path):
    base = intake_provider([])

    def provider(request):
        response = base(request)
        payload = response.json()
        for call in (
            payload.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
        ):
            if call["function"]["name"] == "plan_learning_roadmap":
                args = json.loads(call["function"]["arguments"])
                args["intake"].update(
                    time_budget=None, needed_fields=["goal", "background"]
                )
                call["function"]["arguments"] = json.dumps(args)
        return httpx.Response(200, json=payload)

    with roadmap_client(tmp_path, [], provider) as c:
        run, _ = submit(
            c, "我想学习 Redis，有 Python 基础，目标是实现缓存，只给学习顺序不排期"
        )
        assert run["roadmap"], run
        request = c.get("/api/learning-requests").json()[0]
        assert request["known"]["time_budget"] is None
        assert request["questions"] == []
        assert c.get("/api/todos").json() == []

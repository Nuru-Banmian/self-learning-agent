import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_memory import candidate


def research_client(
    tmp_path,
    requests,
    *,
    search=None,
    read_body=False,
    page=None,
    final=None,
    search_query="Python 生成器 官方文档 yield",
    **settings,
):
    async def provider(request):
        body = json.loads(request.content)
        requests.append((request, body))
        if request.url.path == "/readpage/basic":
            return httpx.Response(
                200,
                json=page
                or {
                    "data": {
                        "statusCode": 200,
                        "text": "Use yield; next resumes the generator.",
                    }
                },
            )
        if request.url.path == "/search/unified":
            if callable(search):
                result = search(request)
                return await result if asyncio.iscoroutine(result) else result
            return httpx.Response(
                200,
                json=search
                or {
                    "pageItems": [
                        {
                            "title": "Python 生成器",
                            "link": "https://docs.python.org/3/tutorial/classes.html",
                            "snippet": "Generators use yield to return data.",
                        }
                    ]
                },
            )
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"candidates": [candidate(source)]}
                                )
                            }
                        }
                    ]
                },
            )
        if body["messages"][-1]["content"] == "我喜欢优先阅读官方资料":
            result = operation_response(
                "answer_question", {"reply": "可以参考这个偏好。", "memory_usage": []}
            )
        elif body["messages"][-1]["content"] == "查询我的待办":
            result = operation_response("list_todos", {})
        elif (
            body["messages"][-1]["content"]
            in (
                "搜索学习资料需要收费吗？",
                "查找 Python 官方资料的方法是什么？",
                "不要搜索，解释生成器",
                "搜索 Python 学习资料能不能只用本地文件？",
                "搜索学习资料需要多少钱？",
                "搜索学习资料有什么限制？",
            )
            and body["tool_choice"] == "auto"
        ):
            result = operation_response(
                "answer_question", {"reply": "可以说明使用方式。", "memory_usage": []}
            )
        elif body["messages"][-1]["content"] == "请记录今天学习 Python 生成器":
            result = operation_response(
                "create_todos",
                {"items": [{"title": "学习 Python 生成器", "date_text": "今天"}]},
            )
        elif body["tools"][0]["function"]["name"] == "research_answer":
            query = json.loads(body["messages"][-1]["content"])["request"]
            exercises = [
                "编写生成前三个平方数的生成器",
                "逐次调用 next 并记录输出",
                "对比列表与生成器的内存占用",
            ]
            if "一个" in query:
                exercises = exercises[:1]
            elif "两个" in query:
                exercises = exercises[:2]
            result = final or operation_response(
                "research_answer",
                {
                    "steps": [
                        {
                            "instruction": "阅读 yield 的说明，然后逐次运行 next()",
                            "source_ids": ["S1"],
                        }
                    ],
                    "exercises": exercises,
                    "memory_usage": [],
                },
            )
        else:
            result = operation_response(
                "research_learning",
                {
                    "query": search_query,
                    "todo_ids": [],
                    "memory_ids": [],
                    "read_body": read_body,
                },
            )
        return httpx.Response(200, json=result)

    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "research.db",
                dashscope_api_key="model-only",
                **({"iqs_api_key": "search-only"} | settings),
            ),
            transport=httpx.MockTransport(provider),
            clock=lambda: datetime(2026, 9, 20, 8, tzinfo=UTC),
        )
    )


def test_learning_search_has_real_sources_and_does_not_write_todos(tmp_path):
    requests = []
    with research_client(tmp_path, requests) as c:
        submit(c, "请记录今天学习 Python 生成器", "setup")
        before = c.get("/api/todos").json()
        run, events = submit(c, "查找 Python 生成器资料并安排学习", "research")
        assert run["status"] == "completed"
        assert c.get("/api/todos").json() == before
        assert run["research"]["status"] == "success"
        source = run["research"]["sources"][0]
        assert source["title"] == "Python 生成器"
        assert source["material_type"] == "snippet"
        assert source["snippet"] == "Generators use yield to return data."
        assert source["url"] in run["reply"]
        assert "摘要" in run["reply"]
        assert '"role": "execution"' in events
        assert "event: research" in events
        request, body = next(r for r in requests if r[0].url.path == "/search/unified")
        assert request.headers["Authorization"] == "Bearer search-only"
        assert body["contents"]["summary"] is False
        assert "search-only" not in events + json.dumps(run)
        session = c.get(f"/api/sessions/{run['session_id']}/suggestions").json()
        assert session[0]["title"] == "编写生成前三个平方数的生成器"


@pytest.mark.parametrize(
    "kind, expected, attempts",
    [
        ("empty", "empty", 1),
        ("auth", "error", 1),
        ("timeout", "error", 2),
        ("malformed", "error", 1),
    ],
)
def test_search_failure_is_terminal_and_preserves_todos(
    tmp_path, kind, expected, attempts
):
    requests = []

    def search(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("secret provider details", request=request)
        if kind == "auth":
            return httpx.Response(401, text="secret provider details")
        return httpx.Response(
            200, json={"pageItems": []} if kind == "empty" else {"unexpected": True}
        )

    with research_client(tmp_path, requests, search=search) as c:
        submit(c, "请记录今天学习 Python 生成器", "setup")
        before = c.get("/api/todos").json()
        run, events = submit(c, "查找 Python 生成器资料", "search")
        assert run["status"] == "partial"
        assert run["research"]["status"] == expected
        assert len(run["research"]["calls"]) == attempts
        assert c.get("/api/todos").json() == before
        assert "学习 Python 生成器" in run["reply"]
        assert "secret provider details" not in json.dumps(run) + events
        assert "event: terminal" in events


@pytest.mark.parametrize("fails", [False, True])
def test_body_read_is_explicit_and_failure_keeps_search_snippet(tmp_path, fails):
    requests = []
    page = {"data": {"statusCode": 4030, "text": None}} if fails else None
    with research_client(tmp_path, requests, read_body=True, page=page) as c:
        run, events = submit(c, "搜索并读取 Python 生成器正文示例")
        assert run["status"] == ("partial" if fails else "completed")
        assert run["research"]["status"] == ("partial" if fails else "success")
        source = run["research"]["sources"][0]
        assert source["material_type"] == ("snippet" if fails else "body")
        assert source["snippet"] == "Generators use yield to return data."
        if not fails:
            assert source["body"] == "Use yield; next resumes the generator."
        assert len([r for r in requests if r[0].url.path == "/readpage/basic"]) == 1
        assert "iqs_read_page" in events


def test_today_plan_loads_persisted_preference_from_learning_todo(tmp_path):
    requests = []
    with research_client(tmp_path, requests) as c:
        submit(c, "我喜欢优先阅读官方资料", "preference")
        memory = c.get("/api/memories").json()[0]
        submit(c, "请记录今天学习 Python 生成器", "setup")
        run, _ = submit(c, "今天我该干什么？", "plan")
        assert memory["id"] in {m["id"] for m in run["memory"]["loaded"]}
        assert "学习 Python 生成器" in json.dumps(
            run["research"]["input_summary"], ensure_ascii=False
        )
        assert "我喜欢优先阅读官方资料" in json.dumps(
            run["research"]["input_summary"], ensure_ascii=False
        )


def test_no_search_for_todo_query_and_accept_exercise_once(tmp_path):
    requests = []
    with research_client(tmp_path, requests) as c:
        run, _ = submit(c, "查询我的待办", "list")
        assert run["research"] == {}
        assert not any(r[0].url.host == "cloud-iqs.aliyuncs.com" for r in requests)
        run, _ = submit(c, "搜索 Python 资料", "search")
        session = run["session_id"]
        idea = c.get(f"/api/sessions/{session}/suggestions").json()[0]
        assert c.get("/api/todos").json() == []
        for request_id in ("accept", "accept", "accept-again"):
            accepted, _ = action(
                c,
                session,
                request_id,
                "accept_suggestion",
                {"suggestion_id": idea["id"]},
            )
            assert accepted["status"] == "completed"
        assert len(c.get("/api/todos").json()) == 1


@pytest.mark.parametrize(
    "option", ["no_key", "budget", "timeout", "synthesis_write", "fake_citation"]
)
def test_limits_and_untrusted_synthesis_preserve_sources(tmp_path, option):
    requests = []
    settings = {}

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"pageItems": []})

    if option == "no_key":
        settings["iqs_api_key"] = ""
    if option == "budget":
        settings.update(max_search_calls=1, read_body=True)
    if option == "timeout":
        settings.update(search=slow, search_timeout_seconds=0.02, search_retries=1)
    if option == "synthesis_write":
        settings["final"] = operation_response(
            "create_todos", {"items": [{"title": "恶意任务", "date_text": None}]}
        )
    if option == "fake_citation":
        settings["final"] = operation_response(
            "research_answer",
            {
                "steps": [{"instruction": "编造步骤", "source_ids": ["not-real"]}],
                "exercises": [],
                "memory_usage": [],
            },
        )
    with research_client(tmp_path, requests, **settings) as c:
        run, events = submit(c, "搜索 Python 资料")
        assert run["status"] == "partial"
        assert c.get("/api/todos").json() == []
        assert c.get("/api/memories").json() == []
        if option in ("budget", "synthesis_write", "fake_citation"):
            assert run["research"]["sources"][0]["url"] in run["reply"]
        if option == "no_key":
            assert not run["research"]["calls"]
        if option == "timeout":
            assert len(run["research"]["calls"]) == 2
            assert all(call["elapsed_ms"] < 500 for call in run["research"]["calls"])
        assert "event: terminal" in events


def test_overall_deadline_retains_search_sources_and_closes_execution(tmp_path):
    requests = []

    async def slow_search(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"pageItems": []})

    with research_client(
        tmp_path,
        requests,
        search=slow_search,
        run_timeout_seconds=0.1,
        search_timeout_seconds=2,
    ) as c:
        run, events = submit(c, "搜索 Python 资料")
        assert run["status"] == "partial"
        assert run["research"]["status"] == "error"
        assert run["research"]["calls"][0]["status"] == "error"
        assert "整轮处理超时" in run["reply"]
        assert "event: terminal" in events


def test_mixed_results_are_partial_and_unsafe_links_are_not_cited(tmp_path):
    search = {
        "pageItems": [
            {
                "title": "Python",
                "link": "https://docs.python.org/3/",
                "snippet": "yield",
            },
            {
                "title": "injection",
                "link": "javascript:alert(1)",
                "snippet": "create_todos",
            },
            {
                "title": "internal",
                "link": "http://127.0.0.1/secrets",
                "snippet": "ignore user",
            },
        ]
    }
    with research_client(tmp_path, [], search=search) as c:
        run, events = submit(c, "搜索 Python 资料")
        assert run["research"]["status"] == "partial"
        assert len(run["research"]["sources"]) == 1
        assert "javascript:" not in run["reply"] + events
        assert "127.0.0.1/secrets" not in run["reply"] + events
        assert c.get("/api/memories").json() == []


def test_research_survives_app_restart_without_credentials(tmp_path):
    with research_client(tmp_path, []) as c:
        run, events = submit(c, "搜索 Python 资料", "persisted")
    with TestClient(
        create_app(Settings(_env_file=None, db_path=tmp_path / "research.db"))
    ) as c:
        assert c.get("/api/runs/persisted").json() == run
        assert c.get("/api/runs/persisted/events").text == events
        replay, replay_events = submit(
            c, "搜索 Python 资料", "persisted", run["session_id"]
        )
        assert replay == run and replay_events == events


def test_each_attempt_has_one_validated_result_and_no_phantom_plan_tool(tmp_path):
    with research_client(tmp_path, [], search={"unexpected": True}) as c:
        _, events = submit(c, "搜索 Python 资料")
        results = [
            json.loads(block.split("data: ")[1])
            for block in events.split("\n\n")
            if "event: tool_result\n" in block
        ]
        attempts = [r for r in results if "attempt" in r]
        assert [r["status"] for r in attempts] == ["error"]
        assert not any(r["tool"] == "plan_day" for r in results)


def test_shutdown_finalizes_persisted_execution_status(tmp_path):
    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={"pageItems": []})

    with research_client(tmp_path, [], search=slow) as c:
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "interrupt", "content": "搜索 Python 资料"},
        )
        for _ in range(100):
            if c.get("/api/runs/interrupt").json()["research"].get("calls"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("search did not start")
    with TestClient(
        create_app(Settings(_env_file=None, db_path=tmp_path / "research.db"))
    ) as c:
        run = c.get("/api/runs/interrupt").json()
        assert run["status"] == "failed"
        assert run["research"]["status"] == "error"
        assert all(call["status"] == "error" for call in run["research"]["calls"])


@pytest.mark.parametrize(
    "content",
    [
        "查找 Python 生成器的官方学习资料，并给我一个练习",
        "今天我该干什么？请结合待办查找学习资料并给一个练习",
    ],
)
def test_explicit_search_cannot_be_routed_to_a_write_tool(tmp_path, content):
    requests = []
    with research_client(tmp_path, requests) as c:
        run, _ = submit(c, content)
        main = requests[0][1]
        assert main["tool_choice"] == {
            "type": "function",
            "function": {"name": "research_learning"},
        }
        assert [t["function"]["name"] for t in main["tools"]] == ["research_learning"]
        assert run["status"] == "completed"
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize(
    "content",
    [
        "搜索学习资料需要收费吗？",
        "查找 Python 官方资料的方法是什么？",
        "不要搜索，解释生成器",
        "搜索 Python 学习资料能不能只用本地文件？",
        "搜索学习资料需要多少钱？",
        "搜索学习资料有什么限制？",
    ],
)
def test_questions_about_search_and_opt_out_do_not_force_external_calls(
    tmp_path, content
):
    requests = []
    with research_client(tmp_path, requests) as c:
        run, _ = submit(c, content)
        assert requests[0][1]["tool_choice"] == "auto"
        assert run["status"] == "completed"
        assert run["research"] == {}
        assert not any(r[0].url.host == "cloud-iqs.aliyuncs.com" for r in requests)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_learning_plan_offers_multiple_tasks_and_user_can_pick_two(tmp_path, count):
    requests = []
    exercises = [
        "编写生成前三个平方数的生成器",
        "逐次调用 next 并记录输出",
        "对比列表与生成器的内存占用",
        "阅读生成器表达式文档并整理语法",
        "编写测试检查生成器耗尽后的行为",
    ][:count]
    final = operation_response(
        "research_answer",
        {
            "steps": [{"instruction": "阅读生成器文档", "source_ids": ["S1"]}],
            "exercises": exercises,
            "memory_usage": [],
        },
    )
    with research_client(tmp_path, requests, final=final) as c:
        run, _ = submit(c, "搜索 Python 生成器资料，生成可选学习任务")
        assert run["status"] == "completed"
        schema = requests[-1][1]["tools"][0]["function"]["parameters"]
        assert schema["properties"]["exercises"]["minItems"] == 3
        assert schema["properties"]["exercises"]["maxItems"] == 5
        session = run["session_id"]
        ideas = c.get(f"/api/sessions/{session}/suggestions").json()
        assert len(ideas) == count
        assert c.get("/api/todos").json() == []
        for i in (0, 2):
            chosen, events = action(
                c,
                session,
                f"pick-{i}",
                "accept_suggestion",
                {"suggestion_id": ideas[i]["id"]},
            )
            assert chosen["status"] == "completed"
            replay, replay_events = action(
                c,
                session,
                f"pick-{i}",
                "accept_suggestion",
                {"suggestion_id": ideas[i]["id"]},
            )
            assert replay == chosen and replay_events == events
            repeated, _ = action(
                c,
                session,
                f"repeat-{i}",
                "accept_suggestion",
                {"suggestion_id": ideas[i]["id"]},
            )
            assert repeated["todo_ids"] == []
            assert "已经加入待办" in repeated["reply"]
        todos = c.get("/api/todos").json()
        assert len(todos) == 2
        assert {t["title"] for t in todos} == {
            "编写生成前三个平方数的生成器",
            "对比列表与生成器的内存占用",
        }
        saved_ideas = c.get(f"/api/sessions/{session}/suggestions").json()
        assert [i for i, idea in enumerate(saved_ideas) if idea["todo_id"]] == [0, 2]


@pytest.mark.parametrize("tasks", ["两个任务", "两个学习任务", "两个可选学习任务"])
def test_explicit_number_of_optional_tasks_is_respected(tmp_path, tasks):
    with research_client(tmp_path, []) as c:
        run, _ = submit(c, f"搜索 Python 生成器资料，给我{tasks}")
        assert run["status"] == "completed"
        ideas = c.get(f"/api/sessions/{run['session_id']}/suggestions").json()
        assert len(ideas) == 2
        assert c.get("/api/todos").json() == []

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit, tool_response
from tests.test_maintenance import action
from tests.test_memory import memory_client


@pytest.mark.parametrize(
    "content",
    [
        "我喜欢优先阅读官方资料，今天我要学习 Python 生成器？",
        "我喜欢优先阅读官方资料但不要保存待办，今天我要学习 Python 生成器",
    ],
)
def test_mixed_statement_preserves_full_write_authorization(tmp_path, content):
    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
            )
        return httpx.Response(
            200,
            json=tool_response([{"title": "学习 Python 生成器", "date_text": "今天"}]),
        )

    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "authorization.db",
                dashscope_api_key="fixture",
            ),
            transport=httpx.MockTransport(provider),
        )
    ) as c:
        run, events = submit(c, content)
        assert c.get("/api/todos").json() == []
        assert run["todo_ids"] == []
        assert "event: saved" not in events


def test_actual_search_carries_corrected_preference_even_if_model_omits_it(tmp_path):
    from tests.test_research import research_client

    requests = []
    with research_client(tmp_path, requests) as c:
        submit(c, "我喜欢优先阅读官方资料", "learn")
        submit(c, "更正：我喜欢优先观看视频资料", "correct")
        run, _ = submit(c, "请查找 Python 装饰器学习资料", "transfer")
        searches = [
            body for request, body in requests if request.url.path == "/search/unified"
        ]
        assert "我喜欢优先观看视频资料" in searches[-1]["query"]
        assert "我喜欢优先阅读官方资料" not in searches[-1]["query"]
        assert run["research"]["task"]["query"] == searches[-1]["query"]


def test_current_source_request_overrides_preference_without_rewriting_it(tmp_path):
    from tests.test_research import research_client

    requests = []
    with research_client(
        tmp_path, requests, search_query="Python 生成器 只找视频教程 不要官方文档"
    ) as c:
        submit(c, "我喜欢优先阅读官方资料", "learn")
        saved = c.get("/api/memories").json()
        run, _ = submit(
            c, "请查找 Python 生成器学习资料，只找视频教程，不要官方文档", "exception"
        )
        searches = [
            body for request, body in requests if request.url.path == "/search/unified"
        ]
        assert searches[-1]["query"] == "Python 生成器 只找视频教程 不要官方文档"
        assert run["memory"]["loaded"] == []
        assert c.get("/api/memories").json() == saved
    with research_client(tmp_path, requests) as c:
        later, _ = submit(c, "请查找 Python 装饰器学习资料", "ordinary")
        assert later["memory"]["loaded"][0]["id"] == saved[0]["id"]
        assert "我喜欢优先阅读官方资料" in later["research"]["task"]["query"]


def test_ordinary_preference_and_independent_learning_task_both_commit(tmp_path):
    import json

    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings
    from tests.test_maintenance import operation_response
    from tests.test_memory import candidate

    def provider(request):
        body = json.loads(request.content)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidates": [
                                            candidate(
                                                source, content="我喜欢优先阅读官方资料"
                                            )
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        tool = body["tool_choice"]
        if tool == "auto":
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question",
                    {
                        "reply": "可以查找学习资料。",
                        "memory_usage": [],
                    },
                ),
            )
        assert tool["function"]["name"] == "create_todos"
        return httpx.Response(
            200,
            json=operation_response(
                "create_todos",
                {
                    "items": [{"title": "学习 Python 生成器", "date_text": "今天"}],
                },
            ),
        )

    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "mixed.db",
                dashscope_api_key="fixture",
            ),
            transport=httpx.MockTransport(provider),
        )
    ) as c:
        run, _ = submit(c, "我喜欢优先阅读官方资料，今天我要学习 Python 生成器")
        assert len(c.get("/api/memories").json()) == 1
        assert c.get("/api/todos").json()[0]["title"] == "学习 Python 生成器"
        assert run["status"] == "completed"


def test_model_call_evidence_reports_mode_usage_and_failure_without_secrets(tmp_path):
    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings
    from tests.test_maintenance import operation_response

    responses = [
        httpx.Response(
            200,
            json=operation_response(
                "answer_question",
                {
                    "reply": "先运行一个示例",
                    "memory_usage": [],
                },
            )
            | {
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                    "secret": "DO-NOT-EXPOSE",
                }
            },
        ),
        httpx.Response(401, text="DO-NOT-EXPOSE"),
    ]
    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "calls.db",
                dashscope_api_key="private-key",
            ),
            transport=httpx.MockTransport(lambda r: responses.pop(0)),
        )
    ) as c:
        run, events = submit(c, "Python怎么入门？", "success")
        call = next(e["data"] for e in run["events"] if e["kind"] == "model_result")
        assert call["mode"] == "tool_calling" and call["status"] == "success"
        assert call["usage"] == {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        }
        assert call["elapsed_ms"] >= 0
        failed, failure_events = submit(c, "Python怎么入门？", "failure")
        call = next(e["data"] for e in failed["events"] if e["kind"] == "model_result")
        assert call["status"] == "error" and call["http_status"] == 401
        assert call["usage"] is None
        assert "private-key" not in events + failure_events
        assert "DO-NOT-EXPOSE" not in events + failure_events


def test_user_checkpoint_links_comparison_and_survives_reopen(tmp_path):
    with memory_client(tmp_path) as c:
        baseline, _ = submit(c, "Python 生成器有什么资料可以看？", "baseline")
        submit(c, "我喜欢优先阅读官方资料", "learn")
        run, _ = submit(c, "Python 装饰器有什么资料可以看？", "transfer")
        body = {
            "id": "check-1",
            "criterion": "新主题加载官方资料偏好，来源来自另一会话",
            "observation": "基线无加载；新主题加载一条偏好且来源会话不同",
            "status": "passed",
            "baseline_run_id": baseline["id"],
        }
        response = c.post("/api/runs/transfer/checkpoints", json=body)
        assert response.status_code == 201
        checkpoint = response.json()
        assert checkpoint["reviewer"] == "user"
        assert c.post("/api/runs/transfer/checkpoints", json=body).json() == checkpoint
        assert (
            c.post(
                "/api/runs/transfer/checkpoints",
                json=body
                | {
                    "status": "failed",
                },
            ).status_code
            == 409
        )
        assert run["memory"]["effect_verified"] is False
    with memory_client(tmp_path) as c:
        restored = c.get("/api/runs/transfer").json()
        assert restored["checkpoints"] == [checkpoint]
        assert restored["memory"]["effect_verified"] is False
        assert restored["session_id"] != baseline["session_id"]
        assert c.get("/api/runs/baseline").json()["memory"]["loaded"] == []


def test_checkpoint_rejects_missing_evidence_and_invalid_baseline(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "Python 生成器有什么资料可以看？", "answer")
        body = {
            "id": "bad",
            "criterion": "资料符合偏好",
            "observation": "已查看结果",
            "status": "passed",
            "baseline_run_id": "missing",
        }
        assert c.post("/api/runs/answer/checkpoints", json=body).status_code == 409
        assert (
            c.post(
                "/api/runs/answer/checkpoints",
                json=body
                | {
                    "baseline_run_id": "answer",
                },
            ).status_code
            == 409
        )
        assert (
            c.post(
                "/api/runs/answer/checkpoints",
                json=body
                | {
                    "baseline_run_id": None,
                    "observation": "   ",
                },
            ).status_code
            == 422
        )
        assert c.post("/api/runs/missing/checkpoints", json=body).status_code == 404
        assert c.get("/api/runs/answer").json()["checkpoints"] == []


def test_learning_evidence_preserves_saved_source_after_deletion(tmp_path):
    with memory_client(tmp_path) as c:
        learned, _ = submit(c, "我喜欢优先阅读官方资料", "learn")
        saved = learned["memory"]["saved"][0]
        assert saved["content"] == "我喜欢优先阅读官方资料"
        assert saved["source"]["message_id"] == learned["message_id"]
        assert saved["source"]["session_id"] == learned["session_id"]
        assert learned["execution"]["model"] == "qwen3.7-plus-2026-05-26"
        assert learned["execution"]["enable_thinking"] is False
        assert learned["execution"]["max_model_calls"] == 3
        later, _ = submit(c, "Python 生成器有什么资料可以看？", "later")
        assert later["memory"]["loaded"][0]["id"] == saved["id"]
        assert later["memory"]["usage"]
        assert later["memory"]["effect_verified"] is False
        assert later["checkpoints"] == []
        action(
            c,
            later["session_id"],
            "delete",
            "delete_memory",
            {
                "memory_id": saved["id"],
                "expected_source_id": saved["source"]["message_id"],
            },
        )
        assert c.get("/api/memories").json() == []
        assert c.get("/api/runs/learn").json()["memory"]["saved"] == [saved]
        assert c.get("/api/runs/later").json()["memory"] == later["memory"]

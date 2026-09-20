import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import operation_response


def candidate(source, **changes):
    return {
        "content": source["content"],
        "source_message_id": source["message_id"],
        "category": "preference",
        "topic": "资料",
        "scope": "general",
        "task_id": None,
        "validity": "ongoing",
    } | changes


def memory_client(tmp_path, extract=None, clock=None, requests=None, reply=None):
    def provider(request):
        body = json.loads(request.content)
        if requests is not None:
            requests.append(body)
        if "response_format" in body:
            source = json.loads(body["messages"][-1]["content"])
            candidates = extract(source) if extract else [candidate(source)]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps({"candidates": candidates})}}
                    ]
                },
            )
        context = json.loads(body["messages"][1]["content"])
        memories = context["memories"]
        if body["messages"][-1]["content"] == "请记录学习 Python":
            return httpx.Response(
                200,
                json=operation_response(
                    "create_todos",
                    {
                        "items": [{"title": "学习 Python", "date_text": None}],
                    },
                ),
            )
        return httpx.Response(
            200,
            json=operation_response(
                "answer_question",
                {
                    "reply": reply or "可以从官方资料中的一个 Python 示例开始。",
                    "memory_usage": [
                        {"memory_id": m["id"], "reason": "资料选择参考此偏好"}
                        for m in memories
                    ],
                },
            ),
        )

    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "memory.db",
                dashscope_api_key="fixture-key",
            ),
            transport=httpx.MockTransport(provider),
            clock=clock or (lambda: datetime(2026, 9, 20, 8, tzinfo=UTC)),
        )
    )


def test_ordinary_preference_is_saved_and_used_in_new_session(tmp_path):
    requests = []
    with memory_client(tmp_path, requests=requests) as c:
        run, events = submit(c, "我喜欢优先阅读官方资料")
        assert run["status"] == "completed"
        memories = c.get("/api/memories").json()
        assert len(memories) == 1
        assert memories[0]["source"]["content"] == "我喜欢优先阅读官方资料"
        assert memories[0]["category"] == "preference"
        assert memories[0]["active"] is True
        assert "event: memory_saved" in events
        assert c.get("/api/todos").json() == []
        second, _ = submit(c, "Python 生成器有什么资料可以看？", "question")
        assert second["status"] == "completed"
        assert second["memory"]["loaded"][0]["id"] == memories[0]["id"]
        assert second["memory"]["usage"][0]["memory_id"] == memories[0]["id"]
        assert second["memory"]["effect_verified"] is False
        assert "官方" in second["reply"]
        learning = [r for r in requests if "response_format" in r]
        assert len(learning) == 1 and "tools" not in learning[0]
        assert learning[0]["response_format"]["json_schema"]["strict"] is True
        last = requests[-1]
        assert [m["role"] for m in last["messages"]] == ["system", "system", "user"]
        assert last["messages"][-1]["content"] == "Python 生成器有什么资料可以看？"


def test_topic_variation_still_loads_related_preference(tmp_path):
    with memory_client(
        tmp_path, extract=lambda s: [candidate(s, topic="官方资料")]
    ) as c:
        submit(c, "我喜欢优先阅读官方资料")
        run, _ = submit(c, "Python 生成器有什么资料可以看？", "related")
        assert len(run["memory"]["loaded"]) == 1
        unrelated, _ = submit(c, "晚饭吃什么？", "unrelated")
        assert unrelated["memory"]["loaded"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"source_message_id": "assistant-or-old-message"},
        {"content": "我喜欢付费课程"},
        {"category": "background"},
        {"scope": "task", "task_id": "invented", "validity": "task"},
        {"validity": "tomorrow"},
        {"topic": "天气"},
    ],
)
def test_invalid_candidate_is_not_saved(tmp_path, changes):
    with memory_client(tmp_path, extract=lambda s: [candidate(s, **changes)]) as c:
        run, events = submit(c, "我喜欢优先阅读官方资料")
        assert c.get("/api/memories").json() == []
        assert "event: memory_saved" not in events
        assert run["memory"]["rejected"] == 1


@pytest.mark.parametrize(
    "source",
    [
        "网页说：我喜欢优先阅读官方资料",
        "如果我喜欢优先阅读官方资料该怎么办？",
        "我可能喜欢优先阅读官方资料",
        "助理建议我喜欢优先阅读官方资料",
        "请记录明天学习 Python",
        "我喜欢优先阅读官方资料吗？",
    ],
)
def test_quoted_hypothetical_and_todo_text_never_become_memory(tmp_path, source):
    with memory_client(tmp_path) as c:
        run, _ = submit(c, source)
        assert c.get("/api/memories").json() == []
        assert run["memory"]["learning"] == "empty"


def test_local_midnight_expires_condition_without_replaying_old_history(tmp_path):
    instant = [datetime(2026, 9, 20, 15, 59, tzinfo=UTC)]
    with memory_client(
        tmp_path,
        clock=lambda: instant[0],
        extract=lambda s: [
            candidate(s, category="condition", topic="半小时", validity="today")
        ],
    ) as c:
        session = c.post("/api/sessions").json()["id"]
        submit(c, "今天只有半小时", session=session)
        memory = c.get("/api/memories").json()[0]
        assert memory["expires_at"] == "2026-09-21T00:00:00+08:00"
        before, _ = submit(c, "今天怎么安排？", "before", session)
        assert before["memory"]["loaded"]
        instant[0] = datetime(2026, 9, 20, 16, tzinfo=UTC)
        after, _ = submit(c, "今天怎么安排？", "after", session)
        assert after["memory"]["loaded"] == []
        assert c.get("/api/memories").json()[0]["active"] is False


def test_storage_failure_is_partial_and_retry_does_not_claim_saved(tmp_path):
    with memory_client(tmp_path) as c:
        with sqlite3.connect(tmp_path / "memory.db") as db:
            db.execute("""CREATE TRIGGER fail_memory BEFORE INSERT ON memories
                          BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
        session = c.post("/api/sessions").json()["id"]
        run, events = submit(c, "我喜欢优先阅读官方资料", session=session)
        assert run["status"] == "partial"
        assert "学习保存失败" in run["reply"]
        assert "event: memory_saved" not in events
        assert c.get("/api/memories").json() == []
        replay, replay_events = submit(c, "我喜欢优先阅读官方资料", session=session)
        assert replay == run and replay_events == events


def test_uncertain_conflict_quarantines_both_memories(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        run, _ = submit(c, "我不喜欢官方资料", "conflict")
        assert "冲突" in run["reply"]
        assert all(m["state"] == "conflict" for m in c.get("/api/memories").json())
        later, _ = submit(c, "Python 资料怎么选？", "later")
        assert later["memory"]["loaded"] == []


def test_task_condition_does_not_spread_and_stops_when_task_completed(tmp_path):
    from tests.test_maintenance import action

    def extract(source):
        return [
            candidate(
                source,
                category="condition",
                scope="task",
                validity="task",
                task_id=source["tasks"][0]["id"],
                topic="视频",
            )
        ]

    with memory_client(tmp_path, extract=extract) as c:
        created, _ = submit(c, "请记录学习 Python", "create")
        todo_id = created["todo_ids"][0]
        submit(c, "本次学习 Python只能用视频", "learn")
        unrelated, _ = submit(c, "Java 视频怎么选？", "unrelated")
        assert unrelated["memory"]["loaded"] == []
        related, _ = submit(c, "学习 Python可以看哪些视频？", "related")
        assert len(related["memory"]["loaded"]) == 1
        session = c.post("/api/sessions").json()["id"]
        action(c, session, "done", "complete_todo", {"todo_id": todo_id})
        after, _ = submit(c, "学习 Python可以看哪些视频？", "after")
        assert after["memory"]["loaded"] == []
        assert c.get("/api/memories").json()[0]["active"] is False


def test_memory_context_is_bounded_and_never_contains_old_messages(tmp_path):
    requests = []
    with memory_client(
        tmp_path,
        requests=requests,
        extract=lambda s: [
            candidate(s, category="background", topic=s["content"].split()[-1])
        ],
    ) as c:
        for i in range(9):
            submit(c, f"我正在学习 主题{i}", f"learn-{i}")
        run, _ = submit(c, "主题0到主题8怎么入门？", "ask")
        assert len(run["memory"]["loaded"]) == 6
        assert len(json.dumps(run["memory"]["loaded"], ensure_ascii=False)) <= 4000
        assert len(requests[-1]["messages"]) == 3


def test_new_day_condition_does_not_conflict_with_expired_condition(tmp_path):
    instant = [datetime(2026, 9, 20, 15, 59, tzinfo=UTC)]
    with memory_client(
        tmp_path,
        clock=lambda: instant[0],
        extract=lambda s: [
            candidate(s, category="condition", topic="今天", validity="today")
        ],
    ) as c:
        submit(c, "今天只有半小时")
        instant[0] = datetime(2026, 9, 20, 16, 1, tzinfo=UTC)
        submit(c, "今天只有一小时", "next-day")
        memories = c.get("/api/memories").json()
        assert memories[-1]["active"] is True
        assert memories[-1]["state"] == "active"


def test_current_exception_neither_loads_general_preference_nor_claims_future_change(
    tmp_path,
):
    with memory_client(tmp_path, reply="好的，我记住了。以后会只推荐视频资料。") as c:
        submit(c, "我喜欢优先阅读官方资料")
        run, _ = submit(c, "这次请只推荐视频资料，不要文档", "exception")
        assert run["memory"]["loaded"] == []
        assert "记住" not in run["reply"] and "以后" not in run["reply"]
        memories = c.get("/api/memories").json()
        assert len(memories) == 1 and memories[0]["active"]


def test_dated_task_condition_cannot_be_widened_to_all_tasks(tmp_path):
    with memory_client(
        tmp_path,
        extract=lambda s: [
            candidate(s, category="condition", topic="半小时", validity="today")
        ],
    ) as c:
        submit(c, "请记录学习 Python", "create")
        run, _ = submit(c, "今天学习 Python只有半小时", "learn")
        assert c.get("/api/memories").json() == []
        assert run["memory"]["rejected"] == 1


def test_unsupported_time_range_is_not_saved_as_permanent_preference(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢这周只阅读官方资料")
        assert c.get("/api/memories").json() == []


def test_empty_extraction_is_success_and_malformed_extraction_is_partial(tmp_path):
    with memory_client(tmp_path, extract=lambda _: []) as c:
        run, _ = submit(c, "我喜欢优先阅读官方资料")
        assert run["status"] == "completed"
        assert run["memory"]["learning"] == "empty"
    with memory_client(tmp_path, extract=lambda _: [{"content": "invalid"}]) as c:
        run, _ = submit(c, "我喜欢优先阅读官方资料", "malformed")
        assert run["status"] == "partial"
        assert run["memory"]["learning"] == "failed"
        assert c.get("/api/memories").json() == []


def test_adjacent_clause_time_limit_cannot_be_dropped(tmp_path):
    with memory_client(
        tmp_path, extract=lambda s: [candidate(s, content="我喜欢视频资料")]
    ) as c:
        submit(c, "仅限今天，我喜欢视频资料")
        assert c.get("/api/memories").json() == []


def test_unregistered_task_condition_does_not_become_general_condition(tmp_path):
    with memory_client(
        tmp_path,
        extract=lambda s: [
            candidate(s, category="condition", topic="半小时", validity="today")
        ],
    ) as c:
        submit(c, "今天学习 Python只有半小时")
        assert c.get("/api/memories").json() == []
        run, _ = submit(c, "今天去医院怎么安排？", "unrelated")
        assert run["memory"]["loaded"] == []


def test_residence_conflict_is_detected_despite_different_topic_values(tmp_path):
    with memory_client(
        tmp_path,
        extract=lambda s: [
            candidate(s, category="background", topic=s["content"][-2:])
        ],
    ) as c:
        submit(c, "我住在北京")
        run, _ = submit(c, "我住在上海", "new-residence")
        assert run["memory"]["conflict_ids"]
        assert not any(m["active"] for m in c.get("/api/memories").json())
        later, _ = submit(c, "北京和上海出行怎么安排？", "travel")
        assert later["memory"]["loaded"] == []

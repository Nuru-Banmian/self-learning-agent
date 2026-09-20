import asyncio
import sqlite3
import threading
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_memory import candidate, memory_client


def test_panel_scope_edit_and_expiry_work_without_model(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()[0]
    instant = [datetime(2026, 9, 20, 15, 59, tzinfo=UTC)]
    config = Settings(
        _env_file=None, db_path=tmp_path / "memory.db", dashscope_api_key=""
    )
    with TestClient(create_app(config, clock=lambda: instant[0])) as c:
        session = c.post("/api/sessions").json()["id"]
        result, _ = action(
            c,
            session,
            "limited",
            "update_memory",
            edit_arguments(
                old, content="今天只有半小时", topic="半小时", validity="today"
            ),
        )
        assert result["status"] == "completed" and result["model_calls"] == 0
        current = c.get("/api/memories").json()[0]
        assert current["active"] and current["category"] == "condition"
        assert current["expires_at"] == "2026-09-21T00:00:00+08:00"
        instant[0] = datetime(2026, 9, 20, 16, tzinfo=UTC)
        assert c.get("/api/memories").json()[0]["active"] is False
        action(c, session, "delete", "delete_memory", target(current))
        assert c.get("/api/memories").json() == []


def test_stale_panel_cannot_overwrite_new_correction_or_restore_old_source(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()[0]
        result, _ = submit(c, f"把记忆 {old['id']} 改为 我喜欢视频资料", "correct")
        assert result["status"] == "completed"
        session = c.post("/api/sessions").json()["id"]
        action(
            c,
            session,
            "stale",
            "update_memory",
            edit_arguments(old, content="我喜欢付费资料"),
        )
        action(
            c,
            session,
            "reprocess-old",
            "reprocess_memory",
            {"source_message_id": old["source"]["message_id"]},
        )
        assert c.get("/api/memories").json()[0]["content"] == "我喜欢视频资料"


def test_correction_matches_same_subject_with_different_topic_wording(tmp_path):
    with memory_client(
        tmp_path, extract=lambda s: [candidate(s, topic="官方资料")]
    ) as c:
        submit(c, "我喜欢优先阅读官方资料")
        run, events = submit(c, "更正：我喜欢视频资料", "correct")
        assert run["status"] == "completed" and "event: memory_updated" in events
        assert [m["content"] for m in c.get("/api/memories").json()] == [
            "我喜欢视频资料"
        ]


def test_correction_of_today_condition_does_not_replace_tomorrow(tmp_path):
    def extract(source):
        return [
            candidate(
                source,
                category="condition",
                topic="半小时",
                validity="tomorrow" if "明天" in source["content"] else "today",
            )
        ]

    with memory_client(tmp_path, extract=extract) as c:
        submit(c, "明天只有半小时", "tomorrow")
        submit(c, "今天只有半小时", "today")
        run, events = submit(c, "更正：今天只有一小时", "correct")
        assert "event: memory_updated" in events
        assert sorted(m["content"] for m in c.get("/api/memories").json()) == [
            "今天只有一小时",
            "明天只有半小时",
        ]


def test_edit_cannot_activate_two_contradictory_facts(tmp_path):
    with memory_client(
        tmp_path,
        extract=lambda s: [
            candidate(
                s,
                category="background" if "住在" in s["content"] else "preference",
                topic="北京" if "住在" in s["content"] else "资料",
            )
        ],
    ) as c:
        submit(c, "我喜欢官方资料")
        submit(c, "我住在北京", "residence")
        before = c.get("/api/memories").json()
        session = c.post("/api/sessions").json()["id"]
        result, events = action(
            c,
            session,
            "edit",
            "update_memory",
            edit_arguments(before[0], content="我住在上海", topic="上海"),
        )
        assert "未修改" in result["reply"] and "event: memory_updated" not in events
        assert c.get("/api/memories").json() == before


def test_answer_in_flight_cannot_use_memory_deleted_by_other_session(tmp_path):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()[0]
    entered, release = threading.Event(), threading.Event()

    async def provider(request):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(
            200,
            json=operation_response(
                "answer_question",
                {
                    "reply": "按你的旧偏好，请阅读官方资料。",
                    "memory_usage": [{"memory_id": old["id"], "reason": "旧偏好"}],
                },
            ),
        )

    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                db_path=tmp_path / "memory.db",
                dashscope_api_key="fixture",
            ),
            transport=httpx.MockTransport(provider),
        )
    ) as c:
        session = c.post("/api/sessions").json()["id"]
        c.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": "waiting", "content": "Python 资料怎么选？"},
        )
        assert entered.wait(5)
        another = c.post("/api/sessions").json()["id"]
        action(c, another, "delete", "delete_memory", target(old))
        release.set()
        c.get("/api/runs/waiting/events")
        result = c.get("/api/runs/waiting").json()
        assert "旧偏好" not in result["reply"]
        assert result["memory"]["loaded"] == []
        assert result["memory"]["usage"] == []


def test_clear_chat_correction_replaces_same_scope_with_traceable_source(tmp_path):
    with memory_client(
        tmp_path,
        extract=lambda s: [
            candidate(s, category="background", topic=s["content"][-2:])
        ],
    ) as c:
        submit(c, "我住在北京")
        old = c.get("/api/memories").json()[0]
        corrected, events = submit(c, "更正：我住在上海", "correction")
        assert corrected["status"] == "completed"
        assert "event: memory_updated" in events
        memories = c.get("/api/memories").json()
        assert len(memories) == 1
        assert memories[0]["id"] == old["id"]
        assert memories[0]["content"] == "我住在上海"
        assert memories[0]["source"]["content"] == "更正：我住在上海"
        later, _ = submit(c, "上海周末出行怎么安排？", "later")
        assert [m["content"] for m in later["memory"]["loaded"]] == ["我住在上海"]


def test_saved_task_exception_takes_priority_without_replacing_general_preference(
    tmp_path,
):
    def extract(source):
        if source["content"].startswith("本次"):
            return [
                candidate(
                    source,
                    category="condition",
                    scope="task",
                    task_id=source["tasks"][0]["id"],
                    validity="task",
                )
            ]
        return [candidate(source)]

    with memory_client(tmp_path, extract=extract) as c:
        submit(c, "我喜欢优先阅读官方资料")
        submit(c, "请记录学习 Python", "todo")
        submit(c, "本次学习 Python只能用视频资料", "exception")
        related, _ = submit(c, "学习 Python有什么资料？", "related")
        assert [m["content"] for m in related["memory"]["loaded"]] == [
            "本次学习 Python只能用视频资料"
        ]
        other, _ = submit(c, "Java 有什么资料？", "other")
        assert [m["content"] for m in other["memory"]["loaded"]] == [
            "我喜欢优先阅读官方资料"
        ]
        assert len(c.get("/api/memories").json()) == 2


def target(memory):
    return {
        "memory_id": memory["id"],
        "expected_source_id": memory["source"]["message_id"],
    }


def edit_arguments(memory, **changes):
    return (
        target(memory)
        | {
            "content": "我喜欢视频资料",
            "topic": "资料",
            "scope": "general",
            "task_id": None,
            "validity": "ongoing",
        }
        | changes
    )


@pytest.mark.parametrize("tool", ["update_memory", "delete_memory"])
def test_memory_storage_failure_rolls_back_and_replay_never_claims_success(
    tmp_path, tool
):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()[0]
        with sqlite3.connect(tmp_path / "memory.db") as db:
            db.execute("""CREATE TRIGGER fail_change BEFORE UPDATE ON memories
                          BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        session = c.post("/api/sessions").json()["id"]
        args = edit_arguments(old) if tool == "update_memory" else target(old)
        result, events = action(c, session, "fail", tool, args)
        assert result["status"] == "failed"
        assert "已更正" not in result["reply"] and "已删除" not in result["reply"]
        assert (
            "event: memory_updated" not in events
            and "event: memory_deleted" not in events
        )
        assert c.get("/api/memories").json() == [old]
        assert action(c, session, "fail", tool, args) == (result, events)


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": "task", "task_id": "missing", "validity": "task"},
        {"content": "我喜欢这周看视频资料"},
        {"validity": "today"},
        {"topic": "天气"},
        {"expected_source_id": "stale"},
    ],
)
def test_panel_rejects_invalid_or_stale_change_without_writing(tmp_path, changes):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()[0]
        session = c.post("/api/sessions").json()["id"]
        result, events = action(
            c, session, "invalid", "update_memory", edit_arguments(old, **changes)
        )
        assert "未修改" in result["reply"]
        assert "event: memory_updated" not in events
        assert c.get("/api/memories").json() == [old]


@pytest.mark.parametrize(
    "text",
    [
        "更正一下",
        "以后改成视频",
        "更正：我喜欢视频资料吗？",
        "如果更正：我喜欢视频资料",
        "把记忆 它 改为 我喜欢视频资料",
        "更正：今天只有半小时",
    ],
)
def test_ambiguous_correction_does_not_guess_or_learn_new_fact(tmp_path, text):
    with memory_client(tmp_path) as c:
        submit(c, "我喜欢优先阅读官方资料")
        old = c.get("/api/memories").json()
        result, _ = submit(c, text, "ambiguous")
        assert "未修改" in result["reply"]
        assert c.get("/api/memories").json() == old


def test_deleted_memory_stays_deleted_after_reprocessing_restart_and_retry(tmp_path):
    with memory_client(tmp_path) as c:
        session = c.post("/api/sessions").json()["id"]
        learned, _ = submit(c, "我喜欢优先阅读官方资料", session=session)
        memory = c.get("/api/memories").json()[0]
        deleted, events = action(c, session, "delete", "delete_memory", target(memory))
        assert deleted["status"] == "completed"
        assert "event: memory_deleted" in events
        assert c.get("/api/memories").json() == []
        assert submit(c, "我喜欢优先阅读官方资料", session=session)[0] == learned
        # Explicitly reprocess the original source via the public interface.
        result, _ = action(
            c,
            session,
            "reprocess",
            "reprocess_memory",
            {"source_message_id": memory["source"]["message_id"]},
        )
        assert result["status"] == "completed"
        assert result["model_calls"] == 0
        assert c.get("/api/memories").json() == []
    with memory_client(tmp_path) as c:
        assert c.get("/api/memories").json() == []
        action(c, session, "delete", "delete_memory", target(memory))
        later, _ = submit(c, "Python 资料怎么选？", "after-restart")
        assert later["memory"]["loaded"] == []
        submit(c, "我喜欢优先阅读官方资料", "new-expression")
        new = c.get("/api/memories").json()[0]
        assert new["id"] != memory["id"]
        assert new["source"]["message_id"] != memory["source"]["message_id"]


def test_panel_edit_commits_new_source_and_later_answer_uses_it(tmp_path):
    with memory_client(tmp_path) as c:
        session = c.post("/api/sessions").json()["id"]
        submit(c, "我喜欢优先阅读官方资料", session=session)
        old = c.get("/api/memories").json()[0]
        args = {
            "memory_id": old["id"],
            "expected_source_id": old["source"]["message_id"],
            "content": "我喜欢视频资料",
            "topic": "资料",
            "scope": "general",
            "task_id": None,
            "validity": "ongoing",
        }
        edited, events = action(c, session, "edit", "update_memory", args)
        assert edited["status"] == "completed"
        assert edited["model_calls"] == 0
        assert "event: memory_updated" in events
        current = c.get("/api/memories").json()[0]
        assert current["id"] == old["id"]
        assert current["content"] == "我喜欢视频资料"
        assert current["source"]["message_id"] != old["source"]["message_id"]
        replay, replay_events = action(c, session, "edit", "update_memory", args)
        assert (replay, replay_events) == (edited, events)
        for index, selected_session in enumerate([session, None]):
            later, _ = submit(
                c, "Python 资料怎么选？", f"later-{index}", selected_session
            )
            assert [m["content"] for m in later["memory"]["loaded"]] == [
                "我喜欢视频资料"
            ]

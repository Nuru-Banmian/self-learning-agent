import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import make_client, submit, tool_response


def operation_response(tool, arguments):
    payload = tool_response([])
    payload["choices"][0]["message"]["tool_calls"][0]["function"] = {
        "name": tool,
        "arguments": json.dumps(arguments),
    }
    return payload


def action(client, session, request_id, tool, arguments):
    body = {
        "request_id": request_id,
        "content": "面板操作",
        "action": {"tool": tool, "arguments": arguments},
    }
    response = client.post(f"/api/sessions/{session}/messages", json=body)
    assert response.status_code == 202
    events = client.get(f"/api/runs/{request_id}/events").text
    return client.get(f"/api/runs/{request_id}").json(), events


def test_panel_changes_stable_todo_and_replays_original_result(tmp_path):
    with make_client(
        tmp_path, tool_response([{"title": "整理书桌", "date_text": "明天"}])
    ) as c:
        session = c.post("/api/sessions").json()["id"]
        created, _ = submit(c, "请记录明天整理书桌", session=session)
        todo_id = created["todo_ids"][0]
        args = {"todo_id": todo_id, "title": "整理书架", "date_text": "今天"}
        changed, events = action(c, session, "edit", "update_todo", args)
        assert changed["status"] == "completed"
        assert changed["todo_ids"] == [todo_id]
        assert changed["model_calls"] == 0
        assert "event: saved" in events
        todo = c.get("/api/todos").json()[0]
        assert (todo["id"], todo["title"], todo["scheduled_date"]) == (
            todo_id,
            "整理书架",
            "2026-09-21",
        )
        replay, replay_events = action(c, session, "edit", "update_todo", args)
        assert replay == changed and replay_events == events
        completed, _ = action(c, session, "done", "complete_todo", {"todo_id": todo_id})
        assert completed["todo_ids"] == [todo_id]
        assert c.get("/api/todos").json()[0]["status"] == "completed"
        assert len(c.get("/api/todos").json()) == 1


def test_groups_roll_over_at_user_midnight_without_moving_tasks(tmp_path):
    instant = [datetime(2026, 9, 20, 15, 59, tzinfo=UTC)]
    payload = tool_response(
        [
            {"title": "整理书桌", "date_text": "今天"},
            {"title": "学习 Python", "date_text": "今天"},
        ]
    )
    app = create_app(
        Settings(db_path=tmp_path / "test.db", dashscope_api_key="test-secret"),
        clock=lambda: instant[0],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with TestClient(app) as c:
        session = c.post("/api/sessions").json()["id"]
        created, _ = submit(c, "请记录今天整理书桌和学习 Python", session=session)
        before = c.get("/api/todos/overview").json()
        assert before["today"] == "2026-09-20"
        assert len(before["groups"]["today"]) == 2
        action(c, session, "done", "complete_todo", {"todo_id": created["todo_ids"][1]})
        instant[0] = datetime(2026, 9, 20, 16, 1, tzinfo=UTC)
        after = c.get("/api/todos/overview").json()
        assert after["today"] == "2026-09-21"
        assert after["groups"]["today"] == []
        assert after["groups"]["overdue"][0]["scheduled_date"] == "2026-09-20"
        assert len(after["groups"]["completed"]) == 1
        action(
            c,
            session,
            "move",
            "update_todo",
            {"todo_id": created["todo_ids"][0], "date_text": "明天"},
        )
        assert (
            c.get("/api/todos/overview").json()["groups"]["upcoming"][0][
                "scheduled_date"
            ]
            == "2026-09-22"
        )
        action(
            c,
            session,
            "unschedule",
            "update_todo",
            {"todo_id": created["todo_ids"][0], "date_text": None},
        )
        assert len(c.get("/api/todos/overview").json()["groups"]["unscheduled"]) == 1


def test_chat_maintenance_requires_unique_target_and_exact_authorized_change(tmp_path):
    payload = tool_response([{"title": "整理书桌", "date_text": "明天"}])
    with make_client(tmp_path, payload) as c:
        session = c.post("/api/sessions").json()["id"]
        created, _ = submit(c, "请记录明天整理书桌", session=session)
        target = created["todo_ids"][0]
        payload.update(
            operation_response("update_todo", {"todo_id": target, "date_text": "今天"})
        )
        changed, _ = submit(c, "把整理书桌改到今天", "change", session)
        assert changed["todo_ids"] == [target]
        assert c.get("/api/todos").json()[0]["scheduled_date"] == "2026-09-21"
        snapshot = c.get("/api/todos").json()
        for number, message in enumerate(
            [
                "不要把整理书桌改到今天",
                "把整理书桌改到改天",
                "把它改到今天",
                "把整理书桌改到今天可以吗？",
                "把整理书桌和学习 Python 改到今天",
            ]
        ):
            rejected, events = submit(c, message, f"denied-{number}", session)
            assert not rejected["todo_ids"] and "event: saved" not in events
            assert c.get("/api/todos").json() == snapshot
        payload.update(operation_response("complete_todo", {"todo_id": target}))
        completed, _ = submit(c, "把整理书桌标记完成", "complete", session)
        assert completed["todo_ids"] == [target]
        payload.update(tool_response([{"title": "整理书桌", "date_text": "明天"}]))
        submit(c, "请记录明天整理书桌", "duplicate-title", session)
        snapshot = c.get("/api/todos").json()
        payload.update(operation_response("complete_todo", {"todo_id": target}))
        rejected, _ = submit(c, "把整理书桌标记完成", "ambiguous", session)
        assert "请" in rejected["reply"] and not rejected["todo_ids"]
        assert c.get("/api/todos").json() == snapshot


def test_plan_uses_real_state_and_suggestion_requires_authorization_once(tmp_path):
    payload = tool_response([{"title": "学习 Python", "date_text": "今天"}])
    with make_client(tmp_path, payload) as c:
        session = c.post("/api/sessions").json()["id"]
        submit(c, "请记录今天学习 Python", session=session)
        snapshot = c.get("/api/todos").json()
        payload.update(
            operation_response(
                "plan_day", {"suggestions": ["做一个生成器练习", "整理学习笔记"]}
            )
        )
        plan, events = submit(c, "今天我该干什么？", "plan", session)
        assert plan["status"] == "completed" and plan["todo_ids"] == []
        assert "学习 Python" in plan["reply"] and "行动建议" in plan["reply"]
        assert "event: saved" not in events
        assert c.get("/api/todos").json() == snapshot
        suggestions = c.get(f"/api/sessions/{session}/suggestions").json()
        assert len(suggestions) == 2
        selected = suggestions[0]["id"]
        payload.update(
            operation_response("accept_suggestion", {"suggestion_id": selected})
        )
        for number, content in enumerate(
            [
                "加进去",
                "不要把做一个生成器练习加入待办",
                "可以把做一个生成器练习加入待办吗？",
            ]
        ):
            denied, _ = submit(c, content, f"no-add-{number}", session)
            assert denied["todo_ids"] == []
            assert c.get("/api/todos").json() == snapshot
        accepted, events = submit(c, "把做一个生成器练习加入待办", "accept", session)
        assert len(accepted["todo_ids"]) == 1 and "event: saved" in events
        todos = c.get("/api/todos").json()
        assert len(todos) == 2 and todos[1]["title"] == "做一个生成器练习"
        assert todos[1]["scheduled_date"] == "2026-09-21"
        replay, _ = submit(c, "把做一个生成器练习加入待办", "accept", session)
        assert replay == accepted
        again, events = submit(c, "把做一个生成器练习加入待办", "accept-again", session)
        assert again["todo_ids"] == [] and "event: saved" not in events
        assert c.get("/api/todos").json() == todos
        assert (
            c.get(f"/api/sessions/{session}/suggestions").json()[0]["todo_id"]
            == todos[1]["id"]
        )


@pytest.mark.parametrize("tool", ["update_todo", "complete_todo", "accept_suggestion"])
def test_write_failure_rolls_back_state_and_can_be_retried_with_new_request(
    tmp_path, tool
):
    payload = tool_response([{"title": "整理书桌", "date_text": None}])
    with make_client(tmp_path, payload) as c:
        session = c.post("/api/sessions").json()["id"]
        created, _ = submit(c, "请记录整理书桌", session=session)
        args = {"todo_id": created["todo_ids"][0]}
        if tool == "update_todo":
            args["title"] = "整理书架"
        if tool == "accept_suggestion":
            payload.update(
                operation_response("plan_day", {"suggestions": ["整理学习笔记"]})
            )
            submit(c, "今天我该干什么", "plan", session)
            args = {
                "suggestion_id": c.get(f"/api/sessions/{session}/suggestions").json()[
                    0
                ]["id"]
            }
        before = c.get("/api/todos").json()
        verb = "INSERT" if tool == "accept_suggestion" else "UPDATE"
        with sqlite3.connect(tmp_path / "test.db") as db:
            db.execute(
                f"CREATE TRIGGER fail_write BEFORE {verb} ON todos "
                "BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END"
            )
        failed, events = action(c, session, "failure", tool, args)
        assert failed["status"] == "failed" and failed["error"] == "storage"
        assert failed["todo_ids"] == [] and "event: saved" not in events
        assert c.get("/api/todos").json() == before
        with sqlite3.connect(tmp_path / "test.db") as db:
            db.execute("DROP TRIGGER fail_write")
        replay, _ = action(c, session, "failure", tool, args)
        assert replay == failed
        succeeded, _ = action(c, session, "new-attempt", tool, args)
        assert len(succeeded["todo_ids"]) == 1


def test_offline_panel_validates_dates_ids_and_request_identity(tmp_path):
    payload = tool_response([{"title": "整理书桌", "date_text": None}])
    with make_client(tmp_path, payload) as c:
        session = c.post("/api/sessions").json()["id"]
        created, _ = submit(c, "请记录整理书桌", session=session)
    with TestClient(
        create_app(
            Settings(_env_file=None, db_path=tmp_path / "test.db", dashscope_api_key="")
        )
    ) as c:
        snapshot = c.get("/api/todos").json()
        for number, args in enumerate(
            [
                {"todo_id": created["todo_ids"][0], "date_text": "改天"},
                {"todo_id": created["todo_ids"][0], "date_text": "2026-02-30"},
                {"todo_id": created["todo_ids"][0], "title": " "},
                {"todo_id": "missing", "title": "整理书架"},
                {"todo_id": created["todo_ids"][0], "role": "admin"},
            ]
        ):
            rejected, events = action(
                c, session, f"invalid-{number}", "update_todo", args
            )
            assert rejected["todo_ids"] == [] and "event: saved" not in events
            assert c.get("/api/todos").json() == snapshot
        args = {"todo_id": created["todo_ids"][0], "title": "整理书架"}
        changed, _ = action(c, session, "offline", "update_todo", args)
        assert (
            changed["todo_ids"] == created["todo_ids"] and changed["model_calls"] == 0
        )
        conflict = c.post(
            f"/api/sessions/{session}/messages",
            json={
                "request_id": "offline",
                "content": "面板操作",
                "action": {
                    "tool": "update_todo",
                    "arguments": args | {"title": "其他标题"},
                },
            },
        )
        assert conflict.status_code == 409


def test_empty_plan_query_and_cross_session_suggestion_scope(tmp_path):
    payload = operation_response("plan_day", {"suggestions": ["选择一件今天想做的事"]})
    with make_client(tmp_path, payload) as c:
        session = c.post("/api/sessions").json()["id"]
        plan, _ = submit(c, "今天我该干什么", "plan", session)
        assert not c.get("/api/todos").json()
        assert "行动建议" in plan["reply"]
        assert "•" not in plan["reply"].split("行动建议")[0]
        suggestion = c.get(f"/api/sessions/{session}/suggestions").json()[0]
        other = c.post("/api/sessions").json()["id"]
        rejected, _ = action(
            c,
            other,
            "wrong-session",
            "accept_suggestion",
            {"suggestion_id": suggestion["id"]},
        )
        assert rejected["todo_ids"] == []
        payload.update(operation_response("list_todos", {}))
        query, events = submit(c, "查询待办", "query", other)
        assert "今日未完成" in query["reply"] and "未安排" in query["reply"]
        assert "event: tool_result" in events and "event: saved" not in events
        assert not c.get("/api/todos").json()

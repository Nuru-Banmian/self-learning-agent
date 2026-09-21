"""Issue 35 browser fixture: mocked suppliers, real HTTP/SSE and SQLite.

Use a new DB_PATH and `uvicorn tests.support.self_paced_demo:create_demo_app
--factory`. Seeding uses public actions, then inserts explicit pre-upgrade
history. Existing databases are read without reseeding on process restart.
"""

import json
import os
import sqlite3
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_roadmap_batch import batch_provider
from tests.test_roadmap_revisions import revision_args
from tests.test_roadmaps import roadmap_answer


def provider(request):
    body = json.loads(request.content)
    if request.url.path == "/search/unified" or "response_format" in body:
        return batch_provider(request)
    tool = body.get("tools", [{}])[0].get("function", {}).get("name")
    if tool == "roadmap_answer":
        prompt = json.dumps(body["messages"], ensure_ascii=False)
        if "SQLite" in prompt:
            answer = roadmap_answer()
            answer["title"] = "SQLite 本地数据入门"
            answer["goal"] = "保存本地数据"
            for node in answer["nodes"]:
                node["todo_title"] = node["todo_title"].replace("Redis", "SQLite")
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        return batch_provider(request)
    if tool == "plan_learning_roadmap":
        context = json.loads(body["messages"][1]["content"])
        selected = context.get("selected_learning_request_id")
        prior = next(
            (r for r in context["learning_requests"] if r["id"] == selected), None
        )
        content = body["messages"][-1]["content"]
        topic = (
            prior["topic"]
            if prior
            else (
                "SQLite"
                if "SQLite" in content
                else "Python"
                if "学习 Python" in content
                else "Redis"
            )
        )
        goal = (
            "保存本地数据"
            if topic == "SQLite"
            else "编写命令行程序"
            if topic == "Python"
            else "实现缓存"
        )
        background = "Python 基础" if topic != "Python" else "从零开始"
        return httpx.Response(
            200,
            json=operation_response(
                "plan_learning_roadmap",
                {
                    "intake": {
                        "request_id": selected,
                        "topic": topic,
                        "goal": goal,
                        "background": background
                        if selected or topic == "Redis"
                        else None,
                        "needed_fields": ["goal", "background"],
                    },
                    "query": f"{topic} 入门资料",
                    "todo_ids": [],
                    "memory_ids": [],
                    "read_body": False,
                },
            ),
        )
    return batch_provider(request)


def create_demo_app():
    path = Path(os.environ["DB_PATH"])
    app = create_app(
        Settings(
            _env_file=None,
            db_path=path,
            dashscope_api_key="fixture",
            iqs_api_key="fixture",
        ),
        transport=httpx.MockTransport(provider),
    )
    with sqlite3.connect(path) as db:
        if db.execute("SELECT COUNT(*) FROM roadmaps").fetchone()[0]:
            return app
    with TestClient(app) as client:
        generated, _ = submit(
            client, "我想学习 Redis，有 Python 基础，目标是实现缓存", "seed-route"
        )
        assert generated["roadmap"], generated
        route = generated["roadmap"]
        session = generated["session_id"]
        joined, _ = action(
            client,
            session,
            "seed-join",
            "accept_roadmap_nodes",
            {
                "roadmap_id": route["id"],
                "expected_version": route["version"],
                "node_ids": [route["nodes"][0]["id"]],
            },
        )
        todo_id = joined["roadmap"]["nodes"][0]["todo_id"]
        changed, _ = action(
            client,
            session,
            "seed-date",
            "update_todo",
            {"todo_id": todo_id, "date_text": "2026-10-09"},
        )
        assert changed["status"] == "completed", changed
        for topic, content in (
            ("sqlite", "我想学习 SQLite，有 Python 基础，目标是保存本地数据"),
            ("python", "我想学习 Python，从零开始，目标是编写命令行程序"),
        ):
            pending, _ = submit(client, content, f"seed-{topic}", session=session)
            assert not pending["roadmap"], pending
        with sqlite3.connect(path) as db:
            for index, node in enumerate(route["nodes"]):
                db.execute(
                    "INSERT INTO roadmap_dates VALUES(?,?)",
                    (node["id"], f"2026-10-0{index + 1}"),
                )
            rows = db.execute(
                "SELECT id,content FROM learning_requests WHERE roadmap_id IS NULL"
            ).fetchall()
            for request_id, raw in rows:
                record = json.loads(raw)
                record["known"]["background"] = (
                    "Python 基础" if record["topic"] == "SQLite" else "从零开始"
                )
                record["questions"] = ["每次能投入多少分钟、每周几次？"]
                db.execute(
                    "UPDATE learning_requests SET content=? WHERE id=?",
                    (json.dumps(record, ensure_ascii=False), request_id),
                )
            history = {
                "basis": {
                    "clear": False,
                    "start_date": "2026-10-01",
                    "daily_minutes": 30,
                    "weekdays": list(range(7)),
                    "deadline": None,
                    "timezone": "Asia/Shanghai",
                },
                "entries": [
                    {
                        "node_id": node["id"],
                        "position": index + 1,
                        "title": node["todo_title"],
                        "todo_id": todo_id if index == 0 else None,
                        "previous_date": None,
                        "scheduled_date": f"2026-10-0{index + 1}",
                        "allocations": [],
                    }
                    for index, node in enumerate(route["nodes"])
                ],
            }
            db.execute(
                "INSERT INTO schedule_proposals VALUES(?,?,?,?,?)",
                (
                    "legacy-schedule",
                    route["id"],
                    "seed-route",
                    "pending",
                    json.dumps(history, ensure_ascii=False),
                ),
            )
        route = client.get(f"/api/roadmaps/{route['id']}").json()
        args = revision_args(route)
        for node in args["nodes"]:
            node.pop("scheduled_date")
        preview, _ = action(
            client, session, "seed-revision", "preview_roadmap_revision", args
        )
        assert preview["status"] == "completed", preview
        with sqlite3.connect(path) as db:
            proposal_id, raw = db.execute(
                "SELECT id,content FROM revision_proposals"
            ).fetchone()
            proposal = json.loads(raw)
            proposal["nodes"][0]["scheduled_date"] = "2026-10-20"
            proposal["entries"][0]["after"]["scheduled_date"] = "2026-10-20"
            proposal["entries"][0]["todo_after"]["scheduled_date"] = "2026-10-20"
            db.execute(
                "UPDATE revision_proposals SET content=? WHERE id=?",
                (json.dumps(proposal, ensure_ascii=False), proposal_id),
            )
        metadata = {
            "session_id": session,
            "roadmap_id": route["id"],
            "legacy_schedule": "legacy-schedule",
            "legacy_revision": proposal_id,
            "pending": client.get("/api/learning-requests").json(),
            "providers": "simulated",
            "database": str(path),
        }
        path.with_suffix(".fixture.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return app

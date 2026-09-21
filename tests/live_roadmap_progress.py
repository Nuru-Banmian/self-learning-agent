"""Opt-in completion acceptance with real model/IQS and an isolated database."""

import json
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process
from tests.test_roadmap_progress import target


def main():
    directory = Path("output/issue22/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    records = []

    def record(step, **data):
        records.append({"step": step, **data})
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(step, data.get("status", ""), flush=True)

    settings = Settings()
    record(
        "environment",
        output=str(directory),
        mode="real-model-and-IQS",
        proxy="trust_env=False",
        mainland_egress="unverified-in-this-run",
    )
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record("credentials", status="skipped")
        return
    options = {
        "api_key": settings.dashscope_api_key.get_secret_value(),
        "client_timeout": 140,
        "extra_env": {"RUN_TIMEOUT_SECONDS": "120"},
    }
    database = (directory / "live.db").resolve()
    port = free_port()
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        run, events = submit(
            c,
            "我想学习 Redis，有 Python 基础，目标是实现并验证缓存，"
            "每次可投入30分钟。请给三个有资料来源的学习节点。",
            "generate",
        )
        record("generation", status=run["status"], run=run, events=events)
        route, session = run["roadmap"], run["session_id"]
        assert len(route.get("nodes", [])) >= 3, (
            "Need three nodes for this demonstration"
        )
        for i in range(2):
            accepted, events = action(
                c, session, f"accept-{i}", "accept_roadmap_node", target(route, i)
            )
            route = accepted["roadmap"]
            record(
                f"accept-{i}", status=accepted["status"], run=accepted, events=events
            )
        done, events = submit(
            c, f"完成待办 {route['nodes'][0]['todo_id']}", "chat-done", session
        )
        record("chat-completion", status=done["status"], run=done, events=events)
        assert done["roadmap"]["progress"]["completed"] == 1
        route = done["roadmap"]
        for i in (1, 2):
            done, events = action(
                c, session, f"mastered-{i}", "complete_roadmap_node", target(route, i)
            )
            record(f"mastered-{i}", status=done["status"], run=done, events=events)
            route = done["roadmap"]
        assert route["progress"]["completed"] == 3
        todos = c.get("/api/todos").json()
        assert len(todos) == 2 and all(t["status"] == "completed" for t in todos)
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        other = c.post("/api/sessions").json()["id"]
        repeated, events = action(
            c, other, "again", "complete_roadmap_node", target(route, 2)
        )
        assert repeated["roadmap"] == route
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        assert c.get("/api/todos").json() == todos
        record(
            "restart-and-repeat",
            status="passed",
            route=route,
            todos=todos,
            run=repeated,
            events=events,
        )


if __name__ == "__main__":
    main()

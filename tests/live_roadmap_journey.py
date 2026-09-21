"""Opt-in combined acceptance on a backup of real sourced roadmaps."""

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process
from tests.test_roadmap_batch import selection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saved-db", type=Path, required=True)
    parser.add_argument("--mainland-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.saved_db.is_file():
        parser.error("saved database does not exist")
    directory = Path("output/issue18/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    database = (directory / "live.db").resolve()
    with sqlite3.connect(args.saved_db) as source, sqlite3.connect(database) as dest:
        source.backup(dest)
    records = []

    def record(step, **data):
        records.append({"step": step, **data})
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(step, data.get("status", ""), flush=True)

    record(
        "environment",
        directory=str(directory),
        saved_source=str(args.saved_db),
        mode="real-model-and-IQS-with-saved-routes",
        mainland_egress="user-confirmed-no-proxy"
        if args.mainland_confirmed
        else "unverified",
    )
    settings = Settings()
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record("credentials", status="skipped")
        return
    port = free_port()
    options = dict(
        api_key=settings.dashscope_api_key.get_secret_value(),
        client_timeout=200,
        extra_env={"RUN_TIMEOUT_SECONDS": "180"},
    )
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        routes = c.get("/api/roadmaps").json()
        assert len(routes) >= 2
        summary = next(r for r in routes if "Redis" in r["title"])
        route = c.get(f"/api/roadmaps/{summary['id']}").json()
        session = c.post("/api/sessions").json()["id"]
        prefix = directory.name
        accepted, _ = action(
            c, session, prefix + "-all", "accept_roadmap_nodes", selection(route)
        )
        route = accepted["roadmap"]
        assert all(n["todo_id"] for n in route["nodes"])
        todos = c.get("/api/todos").json()
        preview, events = submit(
            c,
            f"请为路线「{route['title']}」排期，从明天开始，每天可学习1小时30分钟。",
            prefix + "-schedule",
            session,
        )
        record("schedule-preview", run=preview, events=events)
        assert "event: roadmap_schedule_preview" in events
        assert c.get("/api/todos").json() == todos
        proposal = preview["roadmap"]["schedule_proposals"][0]
        scheduled, events = action(
            c,
            session,
            prefix + "-dates",
            "confirm_roadmap_schedule",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        record("schedule-confirmed", run=scheduled, events=events)
        assert "event: roadmap_schedule_confirmed" in events
        route = scheduled["roadmap"]
        completed, _ = action(
            c,
            session,
            prefix + "-complete",
            "complete_todo",
            {"todo_id": route["nodes"][0]["todo_id"]},
        )
        route = completed["roadmap"]
        fact = route["nodes"][0]["completion"]
        before = c.get("/api/todos").json()
        preview, events = submit(
            c,
            f"请调整路线「{route['title']}」：第二个节点改为用容器内redis-cli执行一次SET和GET，"
            "不写Python代码，完成标准改为读回刚写入的hello。请搜索新的入门资料。"
            "只修改第二个节点，保留节点标识和日期，其他节点特别是已完成节点原样保留。",
            prefix + "-revision",
            session,
        )
        record("revision-preview", run=preview, events=events)
        assert "event: roadmap_revision_preview" in events
        assert preview["roadmap"]["nodes"] == route["nodes"]
        assert c.get("/api/todos").json() == before
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert proposal["sync_todo_ids"] == [route["nodes"][1]["todo_id"]]
        confirmation = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": [],
        }
        refused, events = action(
            c, session, prefix + "-refuse", "confirm_roadmap_revision", confirmation
        )
        assert "event: roadmap_revision_pending" in events
        assert refused["roadmap"]["nodes"] == route["nodes"]
        assert c.get("/api/todos").json() == before
        confirmation["sync_todo_ids"] = proposal["sync_todo_ids"]
        done, events = action(
            c, session, prefix + "-apply", "confirm_roadmap_revision", confirmation
        )
        record("revision-confirmed", run=done, events=events)
        assert "event: roadmap_revision_applied" in events
        assert done["roadmap"]["nodes"][0]["completion"] == fact
        assert [n["scheduled_date"] for n in done["roadmap"]["nodes"]] == [
            n["scheduled_date"] for n in route["nodes"]
        ]
        expected = done["roadmap"]
        final_todos = c.get("/api/todos").json()
        assert len(final_todos) == len(before)
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        c.post("/api/sessions")
        assert c.get(f"/api/roadmaps/{route['id']}").json() == expected
        assert c.get("/api/todos").json() == final_todos
        record("process-restart", status="passed", route=expected)


if __name__ == "__main__":
    main()

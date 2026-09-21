"""Opt-in real model/IQS scheduling demo with isolated SQLite and saved evidence."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_roadmap_progress import target


def main():
    directory = Path("output/issue23/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    records = []

    def record(step, **data):
        records.append({"step": step, **data})
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(step, data.get("status", ""), flush=True)

    settings = Settings(db_path=directory / "live.db", run_timeout_seconds=120)
    record(
        "environment",
        output=str(directory),
        mode="real-model-and-IQS",
        mainland_egress="unverified-in-this-run",
    )
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record("credentials", status="skipped")
        return
    with TestClient(create_app(settings)) as c:
        generated, events = submit(
            c,
            "我想学习 Redis，有 Python 基础，目标是实现缓存，每次可投入30分钟。"
            "请生成两个有资料来源的学习节点。",
            "generation",
        )
        record("generation", status=generated["status"], run=generated, events=events)
        route, session = generated["roadmap"], generated["session_id"]
        assert route["nodes"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        todos = c.get("/api/todos").json()
        preview, events = submit(
            c,
            f"请为路线 {route['id']} 排期，从明天开始，每天可学习30分钟。",
            "preview",
            session,
        )
        record(
            "natural-language-preview",
            status=preview["status"],
            run=preview,
            events=events,
        )
        assert preview["model_calls"] >= 1
        assert "event: roadmap_schedule_preview" in events
        proposal = preview["roadmap"]["schedule_proposals"][0]
        tomorrow = datetime.fromisoformat(preview["received_at"]).astimezone(
            ZoneInfo(settings.user_timezone)
        ).date() + timedelta(days=1)
        assert proposal["entries"][0]["allocations"][0]["date"] == tomorrow.isoformat()
        assert proposal["basis"]["daily_minutes"] == 30
        assert c.get("/api/todos").json() == todos
        daily = {}
        for node, item in zip(route["nodes"], proposal["entries"], strict=True):
            assert (
                sum(a["minutes"] for a in item["allocations"])
                == node["estimated_minutes"]
            )
            for allocation in item["allocations"]:
                daily[allocation["date"]] = (
                    daily.get(allocation["date"], 0) + allocation["minutes"]
                )
        assert max(daily.values()) <= 30
        done, events = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_schedule",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        record("confirmation", status=done["status"], run=done, events=events)
        assert (
            done["roadmap"]["nodes"][0]["todo"]["scheduled_date"]
            == proposal["entries"][0]["scheduled_date"]
        )
        assert len(c.get("/api/todos").json()) == 1
    with TestClient(create_app(settings)) as c:
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        assert current == done["roadmap"]
        record("restart", status="passed", roadmap=current)
    record("acceptance", status="passed")


if __name__ == "__main__":
    main()

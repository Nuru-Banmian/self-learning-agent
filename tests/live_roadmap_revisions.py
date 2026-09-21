"""Real model and IQS revision evidence, isolated from the user's database."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action


def main():
    directory = Path("output/issue24/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    records = []

    def record(step, **data):
        records.append({"step": step, **data})
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(step, data.get("status", ""), flush=True)

    settings = Settings(db_path=directory / "live.db", run_timeout_seconds=180)
    record(
        "environment",
        directory=str(directory),
        mode="real-model-and-IQS",
        mainland_egress="not-independently-verified",
    )
    if not settings.dashscope_api_key.get_secret_value():
        record("credentials", status="skipped")
        return
    # Reuse a previously sourced route to focus this slice on actual revision.
    saved = Path("output/issue23/live/054fc8b7e8/live.db")
    if saved.exists():
        with (
            sqlite3.connect(saved) as source,
            sqlite3.connect(settings.db_path) as dest,
        ):
            source.backup(dest)
        record("saved-roadmap", source=str(saved), status="copied")
    with TestClient(create_app(settings)) as c:
        if not c.get("/api/roadmaps").json():
            generated, events = submit(
                c,
                "我想学习 Redis，有 Python 基础，目标是实现缓存，"
                "每次可投入30分钟。请生成两个节点。",
            )
            record(
                "generation", status=generated["status"], run=generated, events=events
            )
        routes = c.get("/api/roadmaps").json()
        assert routes, "No sourced route; retained evidence"
        route = c.get(f"/api/roadmaps/{routes[0]['id']}").json()
        session = c.post("/api/sessions").json()["id"]
        before = route["nodes"]
        run, events = submit(
            c,
            f"请调整路线「{route['title']}」：太难了，改简单一点。"
            "请搜索新的官方入门资料；把第一个节点改成只用 redis-cli "
            "执行一次 SET 和 GET，"
            "不写 Python 代码，"
            "完成标准改成能读回刚写入的值。保留节点身份和已有日期，其他节点原样保留。",
            directory.name + "-revision",
            session,
        )
        record("natural-language-preview", status=run["status"], run=run, events=events)
        assert "event: roadmap_revision_preview" in events
        route = run["roadmap"]
        assert route["nodes"] == before
        proposal = route["revision_proposals"][0]
        assert proposal["entries"], "No actual adjustment; not accepted as evidence"
        assert run["model_calls"] >= 1
        assert run["research"]["calls"]
        args = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": [],
        }
        if proposal["sync_todo_ids"]:
            refused, ev = action(
                c, session, directory.name + "-refuse", "confirm_roadmap_revision", args
            )
            record("refuse", status=refused["status"], run=refused, events=ev)
            assert refused["roadmap"]["nodes"] == before
        args["sync_todo_ids"] = proposal["sync_todo_ids"]
        done, ev = action(
            c, session, directory.name + "-confirm", "confirm_roadmap_revision", args
        )
        record("confirmation", status=done["status"], run=done, events=ev)
        assert "event: roadmap_revision_applied" in ev
        expected = done["roadmap"]
        assert [n["id"] for n in expected["nodes"]] == [n["id"] for n in before]
    with TestClient(create_app(settings)) as c:
        assert c.get(f"/api/roadmaps/{expected['id']}").json() == expected
        record("reopen", status="passed", roadmap=expected)


if __name__ == "__main__":
    main()

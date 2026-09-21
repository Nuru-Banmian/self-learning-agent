"""Opt-in real model/IQS acceptance, retaining each attempt and its SQLite DB."""

import json
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process


def main():
    directory = Path("output/issue19/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    settings = Settings()
    records = []

    def record(data):
        records.append(data)
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in data.items() if k not in ("run", "route", "events")},
                ensure_ascii=False,
            ),
            flush=True,
        )

    record(
        {
            "output": str(directory),
            "mode": "real-model-and-IQS",
            "proxy": "trust_env=False",
            "mainland_egress": "unverified-in-this-run",
        }
    )
    if (
        not settings.dashscope_api_key.get_secret_value()
        or not settings.iqs_api_key.get_secret_value()
    ):
        record({"status": "skipped", "reason": "Missing model or IQS credentials"})
        return
    options = dict(
        api_key=settings.dashscope_api_key.get_secret_value(),
        client_timeout=140,
        extra_env={"RUN_TIMEOUT_SECONDS": "120"},
    )
    database = (directory / "live.db").resolve()
    port = free_port()
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        run, events = submit(c, "我喜欢优先阅读官方资料", "preference")
        record(
            {
                "step": "preference",
                "status": run["status"],
                "run": run,
                "events": events,
            }
        )
        for topic, content in [
            (
                "redis",
                "我想学习 Redis，我会 Python，目标是做一个带过期时间的缓存，"
                "每次可投入30分钟，请给学习路线并读取资料正文",
            ),
            (
                "python",
                "我想学习 Python 生成器，我会 Python 函数和循环，"
                "目标是用生成器逐行处理日志，每次可投入30分钟，请给学习路线并读取资料正文",
            ),
        ]:
            before = c.get("/api/todos").json()
            run, events = submit(c, content, topic)
            route = run["roadmap"]
            record(
                {
                    "step": topic,
                    "status": run["status"],
                    "nodes": len(route.get("nodes", [])),
                    "research_status": run["research"].get("status"),
                    "run": run,
                    "events": events,
                    "route": route,
                }
            )
            assert "event: terminal" in events
            assert c.get("/api/todos").json() == before
            if not route:
                record(
                    {"step": topic, "status": "failed", "reason": "No saved roadmap"}
                )
                continue
            assert run["research"]["calls"]
            assert not run["retryable"]
            session = c.post("/api/sessions").json()["id"]
            args = {
                "roadmap_id": route["id"],
                "node_id": route["nodes"][0]["id"],
                "expected_version": route["version"],
            }
            for request_id in (f"{topic}-accept", f"{topic}-accept", f"{topic}-again"):
                accepted, _ = action(
                    c, session, request_id, "accept_roadmap_node", args
                )
                assert accepted["model_calls"] == 0
            assert len(c.get("/api/todos").json()) == len(before) + 1
            assert all(t["scheduled_date"] is None for t in c.get("/api/todos").json())
        saved = c.get("/api/roadmaps").json()
        snapshots = [c.get(f"/api/roadmaps/{r['id']}").json() for r in saved]
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        c.post("/api/sessions")
        assert [c.get(f"/api/roadmaps/{r['id']}").json() for r in saved] == snapshots
        record(
            {
                "step": "restart-and-cross-session",
                "status": "passed",
                "roadmaps": len(saved),
            }
        )


if __name__ == "__main__":
    main()

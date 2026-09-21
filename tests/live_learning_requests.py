"""Opt-in real model and IQS clarification acceptance with process restart."""

import json
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.test_chat import submit
from tests.test_process import free_port, server_process


def main():
    directory = Path("output/issue20/live") / uuid4().hex[:10]
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
                {
                    k: v
                    for k, v in data.items()
                    if k not in ("run", "events", "pending")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    record(
        {
            "output": str(directory),
            "mode": "real-model-and-IQS",
            "mainland_egress": "unverified",
        }
    )
    if (
        not settings.dashscope_api_key.get_secret_value()
        or not settings.iqs_api_key.get_secret_value()
    ):
        record({"status": "skipped", "reason": "Missing model or IQS credentials"})
        return
    database = (directory / "live.db").resolve()
    port = free_port()
    options = dict(
        api_key=settings.dashscope_api_key.get_secret_value(),
        client_timeout=180,
        extra_env={"RUN_TIMEOUT_SECONDS": "150"},
    )
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        for rid, text in [
            ("background", "我有 Python 基础"),
            ("preference", "我喜欢优先阅读官方资料"),
            ("start", "我想学习 Redis，每次能投入30分钟"),
        ]:
            run, events = submit(c, text, rid)
            record(
                {
                    "step": rid,
                    "reply": run["reply"],
                    "status": run["status"],
                    "run": run,
                    "events": events,
                }
            )
        pending = c.get("/api/learning-requests").json()
        record({"step": "pending", "pending": pending})
        assert len(pending) == 1 and pending[0]["questions"] == ["希望学完后能做什么？"]
        assert not c.get("/api/roadmaps").json()
        assert not c.get("/api/todos").json()
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        assert c.get("/api/learning-requests").json() == pending
        run, events = submit(
            c, "做一个带过期时间的缓存，主要给自己的 Python 小项目用", "continue"
        )
        record(
            {
                "step": "new-session-after-restart",
                "reply": run["reply"],
                "status": run["status"],
                "run": run,
                "events": events,
            }
        )
        assert run["roadmap"]
        assert (
            c.get("/api/learning-requests").json()[0]["roadmap_id"]
            == run["roadmap"]["id"]
        )
        assert not c.get("/api/todos").json()
        repeated, events = submit(
            c, "做一个带过期时间的缓存，主要给自己的 Python 小项目用", "repeat"
        )
        record(
            {
                "step": "repeat",
                "reply": repeated["reply"],
                "run": repeated,
                "events": events,
            }
        )
        assert len(c.get("/api/roadmaps").json()) == 1
    record({"status": "passed"})


if __name__ == "__main__":
    main()

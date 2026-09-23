"""One real Redis route attempt, with every rejected draft retained for review.

Run python -m tests.live.live_redis_exercises. This does not execute lesson code.
MODEL_TIMEOUT_SECONDS may be overridden for an explicitly labelled diagnostic.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.live.live_concise_roadmaps import event_records, review_material
from tests.test_chat import submit
from tests.test_process import free_port, server_process


def main() -> None:
    settings = Settings()
    directory = Path("output/issue33/redis-state-fix/verification") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    database = (directory / "live.db").resolve()
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": "real-model-and-IQS-public-HTTP-SSE",
        "model_timeout_seconds": settings.model_timeout_seconds,
        "run_timeout_seconds": 180,
        "exercise_execution": "not-run",
        "content_quality": "manual-review-required",
        "mainland_egress": "not-independently-verified",
    }

    def save() -> None:
        (directory / "result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    save()
    print(directory, flush=True)
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record["status"] = "skipped-missing-credentials"
        save()
        return
    with server_process(
        free_port(),
        database,
        settings.dashscope_base_url,
        api_key=settings.dashscope_api_key.get_secret_value(),
        client_timeout=200,
        extra_env={"RUN_TIMEOUT_SECONDS": "180"},
    ) as (client, _):
        run, events = submit(
            client,
            "我想学习 Redis，我会 Python 函数和循环，想做简单后端，"
            "目标是用 Python 实现带过期时间的缓存，不用考虑时间。"
            "请给具体学习路线并读取资料正文。",
            "redis",
        )
        routes = client.get("/api/roadmaps").json()
        snapshots = [client.get(f"/api/roadmaps/{r['id']}").json() for r in routes]
        todos = client.get("/api/todos").json()
        record.update(
            run=run,
            events=events,
            model_results=[
                item["data"]
                for item in event_records(events)
                if item["event"] == "model_result"
            ],
            rejected_candidates=[
                item["data"]
                for item in event_records(events)
                if item["event"] == "exercise_validation"
            ],
            todos_unchanged=todos == [],
            terminal_sse="event: terminal" in events,
            route_generated=bool(run.get("roadmap")),
        )
        save()
        (directory / "review.md").write_text(
            review_material("redis", run["content"], run), encoding="utf-8"
        )
    with server_process(
        free_port(),
        database,
        settings.dashscope_base_url,
        api_key="",
        extra_env={"IQS_API_KEY": ""},
    ) as (client, _):
        record["restart_exact_routes"] = snapshots == [
            client.get(f"/api/roadmaps/{r['id']}").json() for r in routes
        ]
        record["restart_exact_todos"] = todos == client.get("/api/todos").json()
        record["restart_exact_run"] = run == client.get("/api/runs/redis").json()
    save()
    print(
        run["status"],
        "route",
        record["route_generated"],
        "rejected drafts",
        len(record["rejected_candidates"]),
        flush=True,
    )


if __name__ == "__main__":
    main()

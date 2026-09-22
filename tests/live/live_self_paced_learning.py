"""Opt-in Issue #35 real model/IQS acceptance in an isolated, retained database.

Run ``python -m tests.live.live_self_paced_learning``. Structural checks do not
replace the per-node manual review material written alongside each result.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.live.live_concise_roadmaps import (
    event_records,
    review_material,
    structural_checks,
)
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process


def main(output_root=Path("output/issue35/live")):
    directory = output_root / uuid4().hex[:10]
    directory.mkdir(parents=True)
    database = (directory / "live.db").resolve()
    records = []
    failed = False

    def record(step, *, checks=None, **data):
        nonlocal failed
        if checks is not None:
            failed |= not all(checks.values())
            data |= {
                "checks": checks,
                "status": "passed" if all(checks.values()) else "failed",
            }
        records.append({"step": step, **data})
        (directory / "results.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(step, data.get("status", ""), flush=True)

    settings = Settings()
    record(
        "environment",
        directory=str(directory.resolve()),
        timestamp=datetime.now(UTC).isoformat(),
        database=str(database),
        mode="real-model-and-IQS-over-HTTP-SSE",
        provider_proxies="trust_env=False",
        mainland_egress="not-independently-verified-in-this-run",
        content_quality="manual-review-required",
        browser="not-covered-by-this-runner",
    )
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record("credentials", status="skipped", reason="Model or IQS key missing")
        return
    options = {
        "api_key": settings.dashscope_api_key.get_secret_value(),
        "client_timeout": 200,
        "extra_env": {"RUN_TIMEOUT_SECONDS": "180"},
    }
    port = free_port()
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        session_id = c.post("/api/sessions").json()["id"]
        initial, initial_events = submit(c, "我想学习 Redis", "redis-start", session_id)
        pending = c.get("/api/learning-requests").json()
        questions = [q for request in pending for q in request["questions"]]
        record(
            "redis-initial-clarification",
            checks={
                "request_saved": len(pending) == 1,
                "only_goal_or_background_questions": bool(questions)
                and not re.search(
                    r"时间|分钟|频率|日期|每周|每天", " ".join(questions)
                ),
                "no_premature_route_or_todo": not c.get("/api/roadmaps").json()
                and not c.get("/api/todos").json(),
                "no_search_before_necessary_goal": not initial.get("research"),
            },
            run=initial,
            events=initial_events,
            learning_requests=pending,
        )
        topics = [
            (
                "redis",
                "我会 Python 函数和循环，想做简单后端，"
                "目标是用 Python 实现带过期时间的缓存，不用考虑时间。"
                "请给具体学习路线并读取资料正文。",
                session_id,
                pending[0]["id"] if len(pending) == 1 else None,
            ),
            (
                "python-generators",
                "我想学习 Python 生成器，我会 Python 函数和循环，"
                "目标是用生成器逐行处理日志，请给具体学习路线并读取资料正文。",
                c.post("/api/sessions").json()["id"],
                None,
            ),
        ]
        generated = []
        for topic, content, session_id, request_id in topics:
            before = c.get("/api/todos").json()
            if request_id:
                c.post(
                    f"/api/sessions/{session_id}/messages",
                    json={
                        "request_id": topic,
                        "content": content,
                        "action": {
                            "tool": "continue_learning",
                            "arguments": {"request_id": request_id},
                        },
                    },
                ).raise_for_status()
                raw_events = c.get(f"/api/runs/{topic}/events").text
                run = c.get(f"/api/runs/{topic}").json()
            else:
                run, raw_events = submit(c, content, topic, session_id)
            events = event_records(raw_events)
            session = c.get(f"/api/sessions/{session_id}").json()
            checks, model_results = structural_checks(
                run, events, session, before, c.get("/api/todos").json()
            )
            record(
                topic,
                checks=checks,
                content_quality="unverified-pending-manual-review",
                summary_code_points=len(run.get("reply") or ""),
                model_results=model_results,
                search_calls=run.get("research", {}).get("calls", []),
                run=run,
                events=raw_events,
                session=session,
            )
            (directory / f"{topic}-manual-review.md").write_text(
                review_material(topic, content, run), encoding="utf-8"
            )
            if run.get("roadmap"):
                generated.append(run["roadmap"])
        if generated:
            route = generated[0]
            session_id = c.post("/api/sessions").json()["id"]
            rejected, events = submit(
                c,
                f"请给路线 {route['id']} 从明天开始排期，每天安排30分钟",
                "natural-schedule",
                session_id,
            )
            record(
                "natural-schedule-refusal",
                checks={
                    "readable_self_paced_refusal": "节奏" in rejected["reply"],
                    "no_route_change": c.get(f"/api/roadmaps/{route['id']}").json()
                    == route,
                    "no_todos": c.get("/api/todos").json() == [],
                },
                run=rejected,
                events=events,
            )
            rejected, events = action(
                c,
                session_id,
                "model-date-revision",
                "revise_roadmap",
                {
                    "roadmap_id": route["id"],
                    "expected_version": route["version"],
                    "instruction": "只把第一个节点的学习日期改为2030-01-09，"
                    "其他全部不变。",
                },
            )
            record(
                "model-date-revision-refusal",
                checks={
                    "real_model_participated": rejected["model_calls"] > 0,
                    "no_route_change": c.get(f"/api/roadmaps/{route['id']}").json()
                    == route,
                    "no_todos": c.get("/api/todos").json() == [],
                    "no_revision_preview": "event: roadmap_revision_preview"
                    not in events,
                },
                run=rejected,
                events=events,
            )
            joined, events = action(
                c,
                session_id,
                "join-first-node",
                "accept_roadmap_node",
                {
                    "roadmap_id": route["id"],
                    "node_id": route["nodes"][0]["id"],
                    "expected_version": route["version"],
                },
            )
            todos = c.get("/api/todos").json()
            record(
                "explicit-node-acceptance",
                checks={
                    "one_undated_todo": len(todos) == 1
                    and todos[0]["scheduled_date"] is None,
                },
                run=joined,
                events=events,
                todos=todos,
            )
            if todos:
                edited, events = action(
                    c,
                    session_id,
                    "ordinary-manual-date",
                    "update_todo",
                    {"todo_id": todos[0]["id"], "date_text": "2030-01-09"},
                )
                record("ordinary-manual-date", run=edited, events=events)
                route = c.get(f"/api/roadmaps/{route['id']}").json()
                todos = c.get("/api/todos").json()
                revised, events = action(
                    c,
                    session_id,
                    "model-content-revision",
                    "revise_roadmap",
                    {
                        "roadmap_id": route["id"],
                        "expected_version": route["version"],
                        "instruction": "只在第一个节点的完成标准末尾增加："
                        "能独立复述本节点操作和观察结果。"
                        "其余全部字段、节点身份、来源、顺序、已有练习与日期保持原样。"
                        "复用现有资料，不搜索。",
                    },
                )
                after = c.get(f"/api/roadmaps/{route['id']}").json()
                proposals = after["revision_proposals"]
                proposal = next(
                    (
                        p
                        for p in proposals
                        if p["id"] not in {p["id"] for p in route["revision_proposals"]}
                    ),
                    None,
                )
                record(
                    "model-content-revision-preview",
                    checks={
                        "real_model_participated": revised["model_calls"] > 0,
                        "proposal_saved": proposal is not None,
                        "current_nodes_unchanged": after["nodes"] == route["nodes"],
                        "todos_unchanged": c.get("/api/todos").json() == todos,
                    },
                    run=revised,
                    events=events,
                )
                if proposal:
                    done, events = action(
                        c,
                        session_id,
                        "confirm-content-revision",
                        "confirm_roadmap_revision",
                        {
                            "roadmap_id": route["id"],
                            "proposal_id": proposal["id"],
                            "sync_todo_ids": proposal["sync_todo_ids"],
                        },
                    )
                    current = c.get(f"/api/roadmaps/{route['id']}").json()
                    record(
                        "confirm-content-revision",
                        checks={
                            "revision_applied": "event: roadmap_revision_applied"
                            in events,
                            "first_criteria_changed": current["nodes"][0][
                                "completion_criteria"
                            ]
                            != route["nodes"][0]["completion_criteria"],
                            "manual_date_preserved": c.get("/api/todos").json() == todos
                            and todos[0]["scheduled_date"] == "2030-01-09",
                            "sources_preserved": current["sources"] == route["sources"],
                            "node_ids_preserved": [n["id"] for n in current["nodes"]]
                            == [n["id"] for n in route["nodes"]],
                            "plan_dates_preserved": [
                                n["planned_date"] for n in current["nodes"]
                            ]
                            == [n["planned_date"] for n in route["nodes"]],
                        },
                        run=done,
                        events=events,
                    )
        routes = c.get("/api/roadmaps").json()
        saved = [c.get(f"/api/roadmaps/{r['id']}").json() for r in routes]
        saved_todos = c.get("/api/todos").json()
    with server_process(port, database, settings.dashscope_base_url, api_key="") as (
        c,
        _,
    ):
        c.post("/api/sessions").raise_for_status()
        record(
            "restart-and-cross-session",
            checks={
                "two_routes_preserved": len(saved) == 2
                and saved == [c.get(f"/api/roadmaps/{r['id']}").json() for r in routes],
                "todos_preserved": c.get("/api/todos").json() == saved_todos,
            },
        )
    record("structural-result", status="failed" if failed else "passed")
    if failed:
        raise SystemExit("Structural checks failed; inspect retained evidence.")


if __name__ == "__main__":
    main()

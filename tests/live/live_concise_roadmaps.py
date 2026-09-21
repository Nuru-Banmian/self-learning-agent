"""Opt-in Issue #34 real-service HTTP/SSE evidence and manual review material.

Run with ``python -m tests.live.live_concise_roadmaps``. Each attempt retains an
isolated SQLite database and all public results; content quality remains a manual
check even when the structural assertions pass. No generated node is accepted.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.settings import Settings
from tests.test_chat import submit
from tests.test_process import free_port, server_process

TOPICS = [
    (
        "redis",
        "我想学习 Redis，我会 Python 函数和循环，目标是用 Python 实现带过期时间的缓存，"
        "每次可投入30分钟，请给学习路线并读取资料正文。",
    ),
    (
        "python-generators",
        "我想学习 Python 生成器，我会 Python 函数和循环，"
        "目标是用生成器逐行处理日志，每次可投入30分钟，请给学习路线并读取资料正文。",
    ),
]


def event_records(events):
    """Keep actual model outcomes separately from the model-call counter."""
    records = []
    for block in events.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        if kind and data:
            records.append({"event": kind, "data": json.loads(data)})
    return records


def review_material(topic, content, run):
    route = run.get("roadmap") or {}
    research = run.get("research") or {}
    lines = [
        f"# {topic}: real-service manual quality review",
        "",
        "Status: UNVERIFIED until a reviewer records observations for every item.",
        "Do not infer quality from HTTP success or these structural checks.",
        "",
        f"Request: {content}",
        f"Run: {run['id']} ({run['status']})",
        f"Summary Unicode code points: {len(run.get('reply') or '')}",
        "",
        "## Review checklist",
        "",
        "- Order and coverage: every saved node appears once in prerequisite order.",
        "- Specificity: each short title and goal identifies a concrete action.",
        "- Compression: key actions survive; no truncated sentence or detail dump.",
        "- Executability: environment, inputs, commands and expected results agree.",
        "- Sources: node IDs map to returned titles/URLs and material types;",
        "  body truncation and missing/failed reads remain visible and truthful.",
        "- Scope: the route reaches the requested goal without irrelevant topics.",
        "- Result: record PASS / FAIL / UNVERIFIED and concrete evidence per item.",
        "",
        "## Persisted default reply",
        "",
        run.get("reply") or "(no reply)",
        "",
        "## Persisted node details",
        "",
    ]
    for index, node in enumerate(route.get("nodes", []), 1):
        lines.extend(
            [
                f"### {index}. {node['todo_title']}",
                "",
                f"Node ID: {node['id']}",
                f"Display title: {node.get('display_title') or node['todo_title']}",
                f"Display goal: {node.get('display_goal') or node['goal']}",
                f"Goal: {node['goal']}",
                f"Exercise: {node['exercise']}",
                f"Completion criteria: {node['completion_criteria']}",
                f"Source IDs: {', '.join(node['source_ids'])}",
                "",
            ]
        )
    lines.extend(["## Actual sources (untrusted reference material)", ""])
    for source in research.get("sources", []):
        lines.extend(
            [
                f"### {source['id']}: {source['title']}",
                "",
                f"URL: {source['url']}",
                f"Material type: {source['material_type']}",
                f"Body truncated: {source.get('body_truncated', 'not-applicable')}",
                f"Search snippet: {source['snippet']}",
                f"Retrieved body: {source.get('body') or '(not retrieved)'}",
                "",
            ]
        )
    lines.extend(["## Reported gaps", "", *research.get("gaps", [])])
    return "\n".join(lines) + "\n"


def structural_checks(run, events, session, before, after):
    route = run.get("roadmap") or {}
    nodes = route.get("nodes", [])
    research = run.get("research") or {}
    reply = run.get("reply") or ""
    sources = research.get("sources", [])
    source_ids = {source["id"] for source in sources}
    model_results = [e["data"] for e in events if e["event"] == "model_result"]
    numbered = re.findall(r"^\s*(\d+)[.)、]\s*.+$", reply, re.MULTILINE)
    node_lines = re.findall(r"^\s*\d+[.)、]\s*(.+)$", reply, re.MULTILINE)
    assistant = [m for m in session["messages"] if m["role"] == "assistant"]
    checks = {
        "terminal_sse": any(e["event"] == "terminal" for e in events),
        "usable_real_route": bool(route) and run["status"] in ("completed", "partial"),
        "node_count_1_to_8": 1 <= len(nodes) <= 8,
        "all_nodes_numbered_in_order": numbered
        == [str(index) for index in range(1, len(nodes) + 1)],
        "each_numbered_node_matches_saved_display": len(node_lines) == len(nodes)
        and all(
            (node.get("display_title") or node["todo_title"]) in line
            and (node.get("display_goal") or node["goal"]) in line
            for node, line in zip(nodes, node_lines, strict=True)
        ),
        "summary_at_most_800_code_points": 0 < len(reply) <= 800,
        "no_default_detail_dump": not re.search(
            r"https?://|```|完成标准[：:]|练习[：:]|搜索摘要[：:]|"
            r"候选待办[：:]|预计\s*\d+\s*分钟",
            reply,
        ),
        "assistant_reply_persisted": bool(assistant)
        and assistant[-1]["content"] == reply,
        "model_success_evidence": run["model_calls"] > 0
        and any(
            result["status"] == "success" and result.get("http_status") == 200
            for result in model_results
        ),
        "real_search_success_evidence": any(
            call["tool"] == "iqs_search"
            and call.get("http_status") == 200
            and call["status"] in ("success", "partial")
            for call in research.get("calls", [])
        ),
        "source_records_preserved": bool(sources) and route.get("sources") == sources,
        "every_node_has_real_source": bool(nodes)
        and all(
            node["source_ids"] and set(node["source_ids"]) <= source_ids
            for node in nodes
        ),
        "todos_unchanged": before == after,
        "partial_has_persisted_gaps": run["status"] != "partial"
        or bool(research.get("gaps")),
    }
    return checks, model_results


def main():
    directory = Path("output/issue34/live") / uuid4().hex[:10]
    directory.mkdir(parents=True)
    database = (directory / "live.db").resolve()
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
        directory=str(directory.resolve()),
        timestamp=datetime.now(UTC).isoformat(),
        mode="real-model-and-IQS-over-HTTP-SSE",
        database=str(database),
        provider_proxies="trust_env=False",
        mainland_egress="unverified-in-this-run",
        content_quality="manual-review-required",
        browser="not-covered-by-this-runner",
    )
    if not (
        settings.dashscope_api_key.get_secret_value()
        and settings.iqs_api_key.get_secret_value()
    ):
        record(
            "credentials", status="skipped", reason="Missing model or IQS credentials"
        )
        return
    options = dict(
        api_key=settings.dashscope_api_key.get_secret_value(),
        client_timeout=200,
        extra_env={"RUN_TIMEOUT_SECONDS": "180"},
    )
    port = free_port()
    snapshots = []
    failed = False
    with server_process(port, database, settings.dashscope_base_url, **options) as (
        c,
        _,
    ):
        for topic, content in TOPICS:
            session_id = c.post("/api/sessions").json()["id"]
            before = c.get("/api/todos").json()
            run, raw_events = submit(c, content, topic, session_id)
            events = event_records(raw_events)
            session = c.get(f"/api/sessions/{session_id}").json()
            checks, model_results = structural_checks(
                run, events, session, before, c.get("/api/todos").json()
            )
            failed |= not all(checks.values())
            record(
                topic,
                status="passed" if all(checks.values()) else "failed",
                content_quality="unverified-pending-manual-review",
                checks=checks,
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
            snapshots.append((topic, session_id, run, session))
        routes = c.get("/api/roadmaps").json()
        saved_routes = [
            c.get(f"/api/roadmaps/{route['id']}").json() for route in routes
        ]
    # A fresh process with no model key verifies that all reads use saved data.
    with server_process(port, database, settings.dashscope_base_url, api_key="") as (
        c,
        _,
    ):
        c.post("/api/sessions").raise_for_status()
        checks = {
            "two_independent_routes": len(saved_routes) == len(TOPICS),
            "roadmaps_preserved": saved_routes
            == [c.get(f"/api/roadmaps/{route['id']}").json() for route in routes],
            "todos_still_empty": c.get("/api/todos").json() == [],
        }
        for topic, session_id, run, session in snapshots:
            checks[f"{topic}_run_preserved"] = c.get(f"/api/runs/{topic}").json() == run
            checks[f"{topic}_session_preserved"] = (
                c.get(f"/api/sessions/{session_id}").json() == session
            )
        failed |= not all(checks.values())
        record(
            "restart-and-cross-session",
            status="passed" if all(checks.values()) else "failed",
            checks=checks,
        )
    record("structural-result", status="failed" if failed else "passed")
    if failed:
        raise SystemExit("Structural acceptance failed; inspect retained evidence.")


if __name__ == "__main__":
    main()

"""Opt-in live model acceptance through real Uvicorn, HTTP/SSE and isolated data.

python -m tests.live_acceptance [--mock-information] --output output/issue9/live
Each invocation creates a new directory; previous failures are never overwritten.
"""

import argparse
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from app.settings import Settings
from tests.live_weather import shanghai_followup
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_process import free_port, server_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-information", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("output/issue9/live"))
    args = parser.parse_args()
    directory = args.output / uuid4().hex[:10]
    directory.mkdir(parents=True)
    settings = Settings()
    records = []

    def record(value):
        records.append(value)
        with (directory / "results.json").open("w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    record(
        {
            "mode": "real-model/mock-information"
            if args.mock_information
            else "real-services",
            "mainland_network": "unverified",
            "proxy": "trust_env=False",
            "output": str(directory),
        }
    )
    if not settings.dashscope_api_key.get_secret_value():
        record({"status": "skipped", "reason": "Missing DASHSCOPE_API_KEY"})
        return
    if not args.mock_information and not (
        settings.iqs_api_key.get_secret_value()
        and settings.qweather_api_key.get_secret_value()
        and settings.qweather_api_host
    ):
        record(
            {
                "status": "skipped",
                "reason": "Full acceptance requires IQS and QWeather credentials/host",
            }
        )
        return
    env = {
        "ACCEPTANCE_MOCK_INFORMATION": "1" if args.mock_information else "0",
        "ACCEPTANCE_AUDIT_PATH": str((directory / "request-shapes.jsonl").resolve()),
        "MAX_MODEL_CALLS": "3",
        "RUN_TIMEOUT_SECONDS": "120",
    }
    options = dict(
        factory="tests.acceptance_app:create_acceptance_app",
        extra_env=env,
        client_timeout=135,
    )
    port = free_port()
    database = directory.resolve() / "acceptance.db"

    def start():
        return server_process(
            port,
            database,
            settings.dashscope_base_url,
            settings.dashscope_api_key.get_secret_value(),
            **options,
        )

    def step(client, name, content):
        started = monotonic()
        run, events = submit(client, content, name)
        (directory / f"{name}.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        record(
            {
                "step": name,
                "status": run["status"],
                "elapsed_ms": round((monotonic() - started) * 1000),
                "model_calls": run["model_calls"],
                "loaded": len(run["memory"].get("loaded", [])),
                "learning": run["memory"].get("learning"),
                "research": run["research"].get("status"),
                "weather": run["weather"].get("status"),
            }
        )
        assert "event: terminal" in events
        return run

    def research(client, name, content):
        before = client.get("/api/todos").json()
        run = step(client, name, content)
        assert run["status"] == "completed" or (
            run["status"] == "partial"
            and run["research"].get("status") == "partial"
            and run["research"].get("gaps")
        ), run["reply"]
        assert run["research"]["sources"] and "学习步骤" in run["reply"]
        assert client.get(f"/api/sessions/{run['session_id']}/suggestions").json()
        assert client.get("/api/todos").json() == before
        return run

    try:
        with start() as (client, first):
            baseline = research(
                client, "baseline", "请查找 Python 生成器学习资料，并给一个小练习"
            )
            assert baseline["memory"]["loaded"] == []
            learned = step(
                client,
                "learn",
                "我看技术资料喜欢先看官方文档，再做一个小练习。"
                "今天我要学 Python 生成器。",
            )
            assert learned["memory"]["saved_ids"]
            assert len(client.get("/api/todos").json()) == 1
            saved = client.get("/api/memories").json()
        assert first.poll() is not None
        with start() as (client, second):
            assert first.pid != second.pid
            assert client.get("/api/memories").json() == saved
            record({"step": "actual-process-restart", "status": "passed"})
            plan = research(
                client, "plan", "今天我该干什么？请查找学习资料并给一个可选练习"
            )
            assert plan["session_id"] != learned["session_id"]
            assert plan["memory"]["loaded"] and plan["memory"]["usage"]
            ideas = client.get(f"/api/sessions/{plan['session_id']}/suggestions").json()
            before = client.get("/api/todos").json()
            for request_id in ("accept", "accept-again"):
                action(
                    client,
                    plan["session_id"],
                    request_id,
                    "accept_suggestion",
                    {"suggestion_id": ideas[0]["id"]},
                )
            todos = client.get("/api/todos").json()
            assert len(todos) == len(before) + 1
            created = next(t for t in todos if t["id"] not in {t["id"] for t in before})
            action(
                client,
                plan["session_id"],
                "complete",
                "complete_todo",
                {"todo_id": created["id"]},
            )
            assert (
                next(
                    t
                    for t in client.get("/api/todos").json()
                    if t["id"] == created["id"]
                )["status"]
                == "completed"
            )
            transfer = research(
                client, "transfer", "请查找 Python 装饰器学习资料，并给一个小练习"
            )
            assert transfer["memory"]["loaded"] and transfer["memory"]["usage"]
            assert "官方" in transfer["research"]["task"]["query"]
            correction = step(client, "correct", "更正：我喜欢优先观看视频资料")
            if not correction["memory"].get("changed_ids"):
                # Topic extraction varies with the real model. Honor the public
                # clarification, then explicitly target the memory we just saved.
                assert "无法唯一定位旧记忆" in correction["reply"]
                correction = step(
                    client,
                    "correct-resolved",
                    f"把记忆 {learned['memory']['saved_ids'][0]} "
                    "改为 我喜欢优先观看视频资料",
                )
            assert correction["memory"].get("changed_ids")
            corrected = research(
                client, "corrected", "请查找 Python 装饰器学习资料，并给一个小练习"
            )
            assert all(
                "官方" not in m["content"] for m in corrected["memory"]["loaded"]
            )
            assert "视频" in corrected["research"]["task"]["query"]
            memory = client.get("/api/memories").json()[0]
            action(
                client,
                corrected["session_id"],
                "delete",
                "delete_memory",
                {
                    "memory_id": memory["id"],
                    "expected_source_id": memory["source"]["message_id"],
                },
            )
            deleted = research(
                client, "deleted", "请查找 Python 装饰器学习资料，并给一个小练习"
            )
            assert deleted["memory"]["loaded"] == []
            checkpoint = {
                "id": "comparison",
                "criterion": "新主题使用持久偏好且仅建议不写入",
                "observation": "跨会话加载偏好，查询含官方；建议前后待办一致。"
                "模拟资料不能证明真实搜索质量。"
                if args.mock_information
                else "公开接口检查：跨会话加载偏好，查询含官方；建议前后待办一致。",
                "status": "passed",
                "baseline_run_id": baseline["id"],
            }
            assert (
                client.post(
                    "/api/runs/transfer/checkpoints", json=checkpoint
                ).status_code
                == 201
            )
            outing = step(client, "outing", "查询明天上海天气，出门需要准备什么？")
            followup = shanghai_followup(outing)
            if followup:
                outing = step(client, "outing-resolved", followup)
            assert outing["weather"]["status"] == "success"
            ambiguous = step(
                client, "ambiguous", "查询明天朝阳天气，出门需要准备什么？"
            )
            assert ambiguous["weather"].get("candidates")
            if args.mock_information:
                resolved = step(
                    client, "resolved", "查询明天地点101071201天气，出门需要准备什么？"
                )
                assert resolved["weather"]["status"] == "success"
                outside = step(
                    client, "outside", "查询2026-10-20上海天气，出门需要准备什么？"
                )
                assert not outside["weather"].get("forecast")
                failure = step(
                    client, "weather-failure", "查询明天失败城天气，出门需要准备什么？"
                )
                assert failure["weather"]["status"] == "error"
            record({"step": "authorization-and-two-flows", "status": "passed"})
        with start() as (client, _):
            restored = client.get("/api/runs/transfer").json()
            assert restored["checkpoints"][0]["criterion"] == checkpoint["criterion"]
            assert restored["memory"] == transfer["memory"]
            assert client.get("/api/memories").json() == []
        shapes = [
            json.loads(line)
            for line in (directory / "request-shapes.jsonl").read_text().splitlines()
        ]
        assert all(
            s["roles"].count("user") == 1 and "assistant" not in s["roles"]
            for s in shapes
        )
        assert {s["model"] for s in shapes} == {"qwen3.7-plus-2026-05-26"}
        record(
            {
                "step": "fixed-model-no-old-conversation-checkpoints-survive-restart",
                "status": "passed",
            }
        )
    except Exception as exc:
        record({"status": "failed", "exception": type(exc).__name__})
        raise
    record(
        {
            "status": "passed",
            "scope": "live model + simulated information"
            if args.mock_information
            else "live services",
            "mainland_network": "unverified",
            "partial_steps": [
                r["step"] for r in records if r.get("status") == "partial"
            ],
        }
    )


if __name__ == "__main__":
    main()

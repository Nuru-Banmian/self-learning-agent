"""Opt-in real HTTP/SSE learning research; never substitutes fixtures for IQS."""

import json
import tempfile
from pathlib import Path
from time import monotonic

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action


def main():
    with tempfile.TemporaryDirectory(prefix="assistant-research-") as directory:
        config = Settings(
            db_path=Path(directory) / "live.db",
            max_model_calls=3,
            run_timeout_seconds=120,
        )
        if not config.iqs_api_key.get_secret_value():
            print("SKIP real IQS: independent IQS_API_KEY is not configured")
            return
        if not config.dashscope_api_key.get_secret_value():
            print("SKIP real model: DASHSCOPE_API_KEY is not configured")
            return
        with TestClient(create_app(config)) as c:
            for request_id, content in [
                ("preference", "我喜欢优先阅读官方资料"),
                ("todo", "请记录今天学习 Python 生成器"),
                (
                    "research",
                    "今天我该干什么？请结合待办查找学习资料，读取正文示例，并给出一个练习",
                ),
                ("transfer", "请查找 JavaScript Promise 的官方学习资料并给一个小练习"),
                ("plain", "查询我的待办"),
            ]:
                before = c.get("/api/todos").json()
                started = monotonic()
                run, events = submit(c, content, request_id)
                print(
                    json.dumps(
                        {
                            "step": request_id,
                            "status": run["status"],
                            "elapsed_ms": round((monotonic() - started) * 1000),
                            "research": run["research"],
                            "reply": run["reply"],
                            "memory": run["memory"],
                            "model_calls": run["model_calls"],
                            "proxy": "trust_env=False",
                            "mainland_egress": "unverified",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert "event: terminal" in events
                if request_id in ("research", "transfer"):
                    assert c.get("/api/todos").json() == before
                    assert run["research"]["sources"]
                    assert run["memory"]["loaded"] and run["memory"]["usage"]
                    assert run["status"] in ("completed", "partial")
                    # Separate local website reachability from the IQS API result.
                    for source in run["research"]["sources"][:2]:
                        started = monotonic()
                        try:
                            with httpx.Client(
                                timeout=12, trust_env=False, follow_redirects=False
                            ) as web:
                                with web.stream("GET", source["url"]) as response:
                                    status = response.status_code
                            outcome = str(status)
                        except httpx.RequestError:
                            outcome = "network_error"
                        print(
                            json.dumps(
                                {
                                    "website": source["url"],
                                    "status": outcome,
                                    "elapsed_ms": round((monotonic() - started) * 1000),
                                    "mainland_egress": "unverified",
                                }
                            ),
                            flush=True,
                        )
                if request_id == "plain":
                    assert not run["research"]
                if request_id == "research":
                    session = run["session_id"]
                    ideas = c.get(f"/api/sessions/{session}/suggestions").json()
                    assert ideas
                    for rid in ("accept", "accept-again"):
                        accepted, _ = action(
                            c,
                            session,
                            rid,
                            "accept_suggestion",
                            {"suggestion_id": ideas[0]["id"]},
                        )
                        assert accepted["status"] == "completed"
                    assert len(c.get("/api/todos").json()) == len(before) + 1
            print("PASS real model/IQS flow; mainland egress remains unverified")


if __name__ == "__main__":
    main()

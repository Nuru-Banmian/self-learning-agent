"""Opt-in real Bailian extraction and transfer through HTTP/SSE."""

import json
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit


class ObserveExtraction(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        if "response_format" in json.loads(request.content):
            await response.aread()
            if response.status_code == 200:
                print(
                    "extraction:",
                    response.json()["choices"][0]["message"]["content"],
                    flush=True,
                )
        return response


def main():
    with tempfile.TemporaryDirectory(prefix="assistant-memory-") as directory:
        config = Settings(db_path=Path(directory) / "live.db")
        if not config.dashscope_api_key.get_secret_value():
            raise SystemExit("SKIP: backend credential is not configured")
        with TestClient(create_app(config, transport=ObserveExtraction())) as c:
            for request_id, content in [
                ("learn", "我喜欢优先阅读官方资料"),
                ("transfer", "Python 生成器有什么资料可以看？请给一个小练习"),
                ("override", "这次请只推荐视频资料，不要文档"),
                ("temporary", "今天只有半小时"),
            ]:
                run, _ = submit(c, content, request_id)
                print(
                    json.dumps(
                        {
                            "step": request_id,
                            "status": run["status"],
                            "reply": run["reply"],
                            "memory": run["memory"],
                            "model_calls": run["model_calls"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert run["status"] == "completed"
                if request_id == "learn":
                    assert len(c.get("/api/memories").json()) == 1
                if request_id == "transfer":
                    assert run["memory"]["loaded"] and run["memory"]["usage"]
                if request_id == "override":
                    assert run["memory"]["loaded"] == []
                    assert not any(w in run["reply"] for w in ("下次", "以后", "记住"))
                if request_id == "temporary":
                    assert any(m["expires_at"] for m in c.get("/api/memories").json())
            assert c.get("/api/todos").json() == []
            print(
                "PASS: real strict extraction, transfer, current exception "
                "and expiry metadata"
            )


if __name__ == "__main__":
    main()

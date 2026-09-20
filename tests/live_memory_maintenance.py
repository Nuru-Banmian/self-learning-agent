"""Opt-in real Bailian maintenance/transfer acceptance using an isolated database."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action


def report(step, run):
    print(
        json.dumps(
            {
                "step": step,
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


def main():
    with tempfile.TemporaryDirectory(prefix="assistant-memory-maintenance-") as folder:
        settings = Settings(db_path=Path(folder) / "live.db")
        if not settings.dashscope_api_key.get_secret_value():
            raise SystemExit("SKIP: backend credential is not configured")
        with TestClient(create_app(settings)) as c:
            session = c.post("/api/sessions").json()["id"]
            report("learn", submit(c, "我喜欢优先阅读官方资料", "learn", session)[0])
            old = c.get("/api/memories").json()[0]
            report(
                "correct",
                submit(
                    c,
                    f"把记忆 {old['id']} 改为 我喜欢优先看视频资料",
                    "correct",
                    session,
                )[0],
            )
            transfer, _ = submit(
                c,
                "Python 生成器有什么资料可以看？请按我的偏好给一个学习步骤",
                "transfer",
            )
            report("transfer", transfer)
            assert [m["content"] for m in transfer["memory"]["loaded"]] == [
                "我喜欢优先看视频资料"
            ]
            assert "视频" in transfer["reply"] and transfer["memory"]["usage"]
            exception, _ = submit(c, "这次请只推荐官方文档资料，不要视频", "exception")
            report("exception", exception)
            assert exception["memory"]["loaded"] == []
            assert c.get("/api/memories").json()[0]["content"] == "我喜欢优先看视频资料"
            current = c.get("/api/memories").json()[0]
            report(
                "delete",
                action(
                    c,
                    session,
                    "delete",
                    "delete_memory",
                    {
                        "memory_id": current["id"],
                        "expected_source_id": current["source"]["message_id"],
                    },
                )[0],
            )
            report(
                "reprocess",
                action(
                    c,
                    session,
                    "reprocess",
                    "reprocess_memory",
                    {"source_message_id": old["source"]["message_id"]},
                )[0],
            )
            later, _ = submit(c, "Java 入门资料怎么选？", "after-delete")
            report("after-delete", later)
            assert later["memory"]["loaded"] == [] and not c.get("/api/memories").json()
            report(
                "new-expression",
                submit(c, "我喜欢优先阅读官方资料", "new-expression")[0],
            )
            new = c.get("/api/memories").json()[0]
            assert (
                new["id"] != old["id"]
                and new["source"]["message_id"] != old["source"]["message_id"]
            )
        print(
            "PASS: real extraction and changed-answer transfer; "
            "correction, exception, deletion and new source",
            flush=True,
        )


if __name__ == "__main__":
    main()

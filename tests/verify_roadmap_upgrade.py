"""Opt-in old-commit SQLite upgrade check through public HTTP interfaces.

Run with ``python -m tests.verify_roadmap_upgrade`` from the repository root.
Only external providers are simulated; each attempt retains its evidence.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_roadmaps import REQUEST, roadmap_provider

BASE = "8878ff91a50c108d187da43be3d7fb6a0a45ab56"
OLD_WRITER = """
import json
import sys
from pathlib import Path
from tests.test_chat import submit
from tests.test_memory import memory_client

directory = Path(sys.argv[1])
with memory_client(directory) as client:
    preference, _ = submit(client, "我喜欢优先阅读官方资料", "old-preference")
    todo, _ = submit(client, "请记录学习 Python", "old-todo")
    assert preference["status"] == todo["status"] == "completed"
    paths = ["/api/todos", "/api/memories"]
    for run in (preference, todo):
        paths += [f"/api/runs/{run['id']}",
                  f"/api/sessions/{run['session_id']}"]
    snapshot = {path: client.get(path).json() for path in paths}
    assert len(snapshot["/api/todos"]) == len(snapshot["/api/memories"]) == 1
    (directory / "before.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
"""


def assert_preserved(before, after):
    """New additive fields are allowed; every old observable value must remain."""
    if isinstance(before, dict):
        for key, value in before.items():
            assert key in after, key
            assert_preserved(value, after[key])
    elif isinstance(before, list):
        assert len(before) == len(after)
        for old, new in zip(before, after, strict=True):
            assert_preserved(old, new)
    else:
        assert before == after, (before, after)


def main():
    directory = (Path("output/issue19/upgrade") / uuid4().hex[:10]).resolve()
    old = directory / "old-app"
    old.mkdir(parents=True)
    archive = directory / "old-app.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive}", BASE], check=True
    )
    with zipfile.ZipFile(archive) as files:
        files.extractall(old)
    worker = old / "write_old_database.py"
    worker.write_text(OLD_WRITER, encoding="utf-8")
    subprocess.run([sys.executable, str(worker), str(directory)], cwd=old, check=True)
    before = json.loads((directory / "before.json").read_text(encoding="utf-8"))
    settings = Settings(
        _env_file=None,
        db_path=directory / "memory.db",
        dashscope_api_key="fixture",
        iqs_api_key="fixture",
    )
    transport = httpx.MockTransport(roadmap_provider([]))
    with TestClient(create_app(settings, transport=transport)) as client:
        after = {path: client.get(path).json() for path in before}
        assert_preserved(before, after)
        assert client.get("/api/roadmaps").json() == []
        run, _ = submit(client, REQUEST, "new-roadmap")
        route = run["roadmap"]
        assert route
        session = client.post("/api/sessions").json()["id"]
        accepted, _ = action(
            client,
            session,
            "new-node",
            "accept_roadmap_node",
            {
                "roadmap_id": route["id"],
                "node_id": route["nodes"][0]["id"],
                "expected_version": route["version"],
            },
        )
        assert accepted["status"] == "completed"
        assert len(client.get("/api/todos").json()) == 2
    with TestClient(create_app(settings, transport=transport)) as client:
        assert len(client.get("/api/roadmaps").json()) == 1
        todos = client.get("/api/todos").json()
        assert len(todos) == 2
        assert_preserved(before["/api/todos"][0], todos[0])
        assert client.get(f"/api/roadmaps/{route['id']}").json()["nodes"][0]["todo_id"]
    result = {"base": BASE, "status": "passed", "mode": "mock-providers-real-SQLite"}
    (directory / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result | {"output": str(directory)}))


if __name__ == "__main__":
    main()

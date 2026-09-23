"""Full exercises survive the public route/detail/revision lifecycle."""

import json
from pathlib import Path

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import action, operation_response
from tests.test_roadmap_revisions import revision_args
from tests.test_roadmaps import (
    REQUEST,
    roadmap_answer,
    roadmap_client,
    roadmap_provider,
)

EXERCISE = """在空练习目录中新建 log_pipeline.py，保存下面的完整脚本。
此文件可独立运行，不需要导入前面节点的函数，也不会覆盖业务日志。
使用 Python 3，所有依赖来自标准库。在此目录运行 python log_pipeline.py。
脚本先写入独立样例文件，再通过生成器逐行读取、筛选和统计；重复执行结果相同。
```python
from pathlib import Path


def read_lines(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield line.rstrip("\\n")


def select_errors(lines):
    for line in lines:
        if line.startswith("ERROR "):
            yield line


def main():
    path = Path("sample_pipeline.log")
    path.write_text("INFO start\\nERROR disk\\nINFO ready\\nERROR timeout\\n",
                    encoding="utf-8")
    count = 0
    for line in select_errors(read_lines(path)):
        print(line)
        count += 1
    print("errors:", count)


if __name__ == "__main__":
    main()
```
应依次输出 ERROR disk、ERROR timeout、errors: 2。INFO 行应被过滤。
检查输入文件仍保留四行；生成器只筛选读取结果，不删除原始记录。
""".strip()

REDIS_PREPARATION = """【准备】在 Ubuntu 终端执行：
```sh
sudo apt-get update
sudo apt-get install -y redis-server python3-venv
sudo service redis-server start
redis-cli ping
python3 -m venv .venv
.venv/bin/python -m pip install redis
```
PING 应返回 PONG。所有脚本用 .venv/bin/python 运行。
"""
REDIS_SCRIPT = """import redis
r = redis.Redis(host="localhost", port=6379, decode_responses=True)
KEY = "learning:cache:node1"
r.delete(KEY)
db = {1: "Alice"}
def get_user():
    value = r.get(KEY)
    if value is not None:
        print("Cache", value)
        return value
    value = db[1]
    r.set(KEY, value, ex=60)
    print("DB", value)
    return value
get_user()
get_user()
db[1] = "Bob"
r.delete(KEY)
get_user()
"""


def redis_answer():
    answer = roadmap_answer()
    for i, node in enumerate(answer["nodes"], 1):
        script = REDIS_SCRIPT.replace("node1", f"node{i}")
        node["exercise"] = (
            (REDIS_PREPARATION if i == 1 else "沿用第一节点环境。")
            + f"【操作】保存 lesson{i}.py，运行 .venv/bin/python lesson{i}.py。\n"
            + f"```python\n{script}```\n"
            + "【验证】依次输出 DB Alice、Cache Alice、DB Bob。"
        )
        node["completion_criteria"] = "重复执行输出仍为 DB Alice、Cache Alice、DB Bob"
    return answer


def exercise_provider(answer):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        return base(request)

    return provider


@pytest.mark.parametrize(
    "defect", ["setup", "venv", "reset", "shared", "late-reset", "extra-key"]
)
def test_redis_prerequisite_defects_remain_visible_without_saving(tmp_path, defect):
    answer = redis_answer()
    if defect == "setup":
        answer["nodes"][0]["exercise"] = answer["nodes"][0]["exercise"].replace(
            "sudo service redis-server start", "确保 Redis 已启动"
        )
    elif defect == "venv":
        answer["nodes"][0]["exercise"] = answer["nodes"][0]["exercise"].replace(
            "redis-server python3-venv", "redis-server"
        )
    elif defect == "shared":
        answer["nodes"][1]["exercise"] = answer["nodes"][1]["exercise"].replace(
            "node2", "node1"
        )
    elif defect == "extra-key":
        answer["nodes"][0]["exercise"] = answer["nodes"][0]["exercise"].replace(
            "r.delete(KEY)", 'r.delete(KEY, "business:key")'
        )
    else:
        text = answer["nodes"][1]["exercise"].replace("r.delete(KEY)\n", "", 1)
        if defect == "late-reset":
            text = text.replace(
                "get_user()\nget_user()", "get_user()\nr.delete(KEY)\nget_user()"
            )
        answer["nodes"][1]["exercise"] = text
    with roadmap_client(tmp_path, [], exercise_provider(answer)) as client:
        run, events = submit(client, REQUEST)
        assert run["status"] == "partial"
        assert not run.get("roadmap")
        assert client.get("/api/roadmaps").json() == []
        assert client.get("/api/todos").json() == []
        assert run["research"]["sources"]
        assert "event: terminal" in events
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as client:
        assert (
            client.get(f"/api/runs/{run['id']}").json()["research"] == run["research"]
        )
        assert client.get("/api/roadmaps").json() == []


def test_independent_redis_exercises_survive_restart(tmp_path):
    answer = redis_answer()
    answer["nodes"][0]["exercise"] = answer["nodes"][0]["exercise"].replace(
        "r.delete(KEY)\n", "assert r.ping()\nr.delete(KEY)\n", 1
    )
    with roadmap_client(tmp_path, [], exercise_provider(answer)) as client:
        run, _ = submit(client, REQUEST)
        assert run["status"] == "completed", run["research"]["gaps"]
        assert [n["exercise"] for n in run["roadmap"]["nodes"]] == [
            n["exercise"] for n in answer["nodes"]
        ]
        assert client.get("/api/todos").json() == []
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as client:
        assert (
            client.get(f"/api/roadmaps/{run['roadmap']['id']}").json() == run["roadmap"]
        )


@pytest.mark.parametrize(
    "operations",
    [
        'r.hset(KEY, mapping={"name": "Alice"})\n'
        "print(r.hgetall(KEY))\nr.delete(KEY)\n",
        'r.lpush(KEY, "Alice")\nprint(r.lpop(KEY))\nr.delete(KEY)\n',
    ],
)
def test_hash_and_queue_exercises_are_not_limited_to_string_cache(tmp_path, operations):
    answer = redis_answer()
    answer["nodes"][0]["exercise"] = answer["nodes"][0]["exercise"].replace(
        "r.delete(KEY)\n", "r.delete(KEY)\n" + operations, 1
    )
    with roadmap_client(tmp_path, [], exercise_provider(answer)) as client:
        run, _ = submit(client, REQUEST)
        assert run.get("roadmap"), run["research"]["gaps"]
        assert client.get("/api/todos").json() == []


@pytest.mark.parametrize("budget", [2, 3])
def test_one_targeted_correction_keeps_failed_candidate_and_respects_budget(
    tmp_path, budget
):
    good = redis_answer()
    bad = redis_answer()
    bad["nodes"][1]["exercise"] = bad["nodes"][1]["exercise"].replace("node2", "node1")
    base = roadmap_provider([])
    answers = iter([bad, good])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            return httpx.Response(
                200, json=operation_response("roadmap_answer", next(answers))
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider, max_model_calls=budget) as client:
        run, events = submit(client, REQUEST)
        assert "event: exercise_validation" in events
        assert "复用了缓存键" in events
        assert run["model_calls"] <= budget
        if budget == 3:
            assert run["status"] == "completed"
            assert (
                run["roadmap"]["nodes"][1]["exercise"] == good["nodes"][1]["exercise"]
            )
        else:
            assert run["status"] == "partial"
            assert client.get("/api/roadmaps").json() == []
        assert client.get("/api/todos").json() == []


def test_model_revision_cannot_remove_redis_state_preparation(tmp_path):
    base = exercise_provider(redis_answer())

    def provider(request):
        body = json.loads(request.content)
        name = body.get("tools", [{}])[0].get("function", {}).get("name")
        if name == "revision_plan":
            return httpx.Response(
                200, json=operation_response(name, {"query": None, "unsupported": []})
            )
        if name == "revision_answer":
            route = json.loads(body["messages"][-1]["content"])["route"]
            proposed = revision_args(route)
            proposed["nodes"][0]["exercise"] = route["nodes"][0]["exercise"].replace(
                "r.delete(KEY)\n", "", 1
            )
            return httpx.Response(200, json=operation_response(name, proposed))
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        generated, _ = submit(client, REQUEST)
        route = generated["roadmap"]
        result, events = submit(
            client, "调整这条路线，简化第一个练习", "revision", generated["session_id"]
        )
        assert "顶层专用键重置" in result["reply"]
        assert "event: terminal" in events
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route
        assert client.get("/api/todos").json() == []


def test_failed_live_redis_exercises_are_not_published(tmp_path):
    answer = json.loads(
        (Path(__file__).parent / "fixtures/issue33/redis_failed.json").read_text(
            encoding="utf-8"
        )
    )
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        run, events = submit(client, REQUEST)
        assert run["status"] == "partial"
        assert not run.get("roadmap")
        assert client.get("/api/roadmaps").json() == []
        assert client.get("/api/todos").json() == []
        assert run["research"]["sources"]
        assert any("练习" in gap for gap in run["research"]["gaps"])
        assert "event: terminal" in events


def test_truncated_model_route_is_not_saved_as_complete_content(tmp_path):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            result = operation_response("roadmap_answer", roadmap_answer())
            result["choices"][0]["finish_reason"] = "length"
            return httpx.Response(200, json=result)
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        run, events = submit(client, REQUEST)
        assert run["status"] == "partial"
        assert not run.get("roadmap")
        assert client.get("/api/roadmaps").json() == []
        assert client.get("/api/todos").json() == []
        assert "event: terminal" in events
        assert '"finish_reason": "length"' in events


def test_full_exercise_is_preserved_in_details_revisions_and_restart(tmp_path):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            answer = roadmap_answer()
            answer["nodes"][0]["exercise"] = EXERCISE
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        run, events = submit(client, REQUEST, "route")
        assert run["status"] == "completed", run["reply"]
        assert "event: terminal" in events
        route = run["roadmap"]
        assert route["nodes"][0]["exercise"] == EXERCISE
        assert len(run["reply"]) <= 800 and "```" not in run["reply"]
        details, _ = submit(
            client, "讲解第一个节点的练习", "details", run["session_id"]
        )
        assert EXERCISE in details["reply"]
        args = revision_args(route)
        revised = (
            EXERCISE + "另做一次验证：把样例第一行改成 ERROR start，应统计出三行错误。"
        )
        args["nodes"][0]["exercise"] = revised
        preview, _ = action(
            client, run["session_id"], "preview", "preview_roadmap_revision", args
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert proposal["nodes"][0]["exercise"] == revised
        assert (
            client.get(f"/api/roadmaps/{route['id']}").json()["nodes"][0]["exercise"]
            == EXERCISE
        )
        done, _ = action(
            client,
            run["session_id"],
            "confirm",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": proposal["id"],
                "sync_todo_ids": [],
            },
        )
        assert done["roadmap"]["nodes"][0]["exercise"] == revised
        assert client.get("/api/todos").json() == []
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as client:
        assert client.get(f"/api/roadmaps/{route['id']}").json() == done["roadmap"]
        assert client.get("/api/runs/details").json()["reply"] == details["reply"]

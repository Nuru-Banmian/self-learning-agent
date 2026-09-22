"""Full exercises survive the public route/detail/revision lifecycle."""

import json

import httpx

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

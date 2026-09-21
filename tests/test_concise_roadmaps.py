"""Compact persisted replies at the public HTTP/SSE boundary."""

import json

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


def test_saved_reply_is_compact_and_links_to_its_route_after_restart(tmp_path):
    with roadmap_client(tmp_path, []) as client:
        run, events = submit(client, REQUEST)
        route = run["roadmap"]
        assert len(run["reply"]) <= 800
        positions = []
        for node in route["nodes"]:
            positions.append(run["reply"].index(node["todo_title"]))
            assert node["goal"] in run["reply"]
            assert node["exercise"] not in run["reply"]
            assert node["completion_criteria"] not in run["reply"]
        assert positions == sorted(positions)
        assert "http" not in run["reply"] and "预计" not in run["reply"]
        assert "event: terminal" in events
        assert run["roadmap_links"] == [
            {"roadmap_id": route["id"], "node_id": None, "title": route["title"]}
        ]
        session = client.get(f"/api/sessions/{run['session_id']}").json()
        message = session["messages"][-1]
        assert message["roadmap_links"] == run["roadmap_links"]
        assert message["roadmap_context"]
        assert client.get("/api/todos").json() == []
    with roadmap_client(tmp_path, []) as client:
        assert client.get(f"/api/runs/{run['id']}").json()["reply"] == run["reply"]
        assert client.get(f"/api/sessions/{run['session_id']}").json() == session
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route


def test_eight_detailed_nodes_keep_all_actions_within_persisted_budget(tmp_path):
    answer = roadmap_answer()
    answer["title"] = "Redis 后端缓存的完整学习路线" * 8
    answer["display_title"] = "Redis 后端缓存"
    answer["nodes"] = [
        answer["nodes"][0]
        | {
            "todo_title": f"阶段{i}：" + "结合示例搭建并验证缓存读写行为" * 8,
            "goal": f"动作{i}：" + "验证读写并检查结果。" * 20,
            "display_title": f"缓存步骤{i} 🚀",
            "display_goal": f"设置键 key{i} 并读取，验证值与输入相同。",
        }
        for i in range(1, 9)
    ]
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
        run, _ = submit(client, REQUEST)
        assert len(run["roadmap"]["nodes"]) == 8
        assert len(run["reply"]) <= 800
        assert [
            line.split(".")[0]
            for line in run["reply"].splitlines()
            if line[:1].isdigit()
        ] == list("12345678")
        for i, node in enumerate(answer["nodes"], 1):
            assert node["display_goal"] in run["reply"]
            assert node["display_title"] in run["reply"]
            assert node["goal"] == run["roadmap"]["nodes"][i - 1]["goal"]
        assert "http" not in run["reply"]


def test_revision_summary_preserves_preview_details_and_links(tmp_path):
    with roadmap_client(tmp_path, []) as client:
        run, _ = submit(client, REQUEST)
        route = run["roadmap"]
        preview, _ = action(
            client,
            run["session_id"],
            "preview",
            "preview_roadmap_revision",
            revision_args(route),
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert len(preview["reply"]) <= 800
        for node in proposal["nodes"]:
            assert node["todo_title"] in preview["reply"]
            assert node["exercise"] not in preview["reply"]
        assert "http" not in preview["reply"]
        assert preview["roadmap_links"][0]["roadmap_id"] == route["id"]
        assert preview["roadmap"]["nodes"] == route["nodes"]
        assert proposal["gaps"]
        assert "限制" in preview["reply"]
        assert client.get("/api/todos").json() == []


def test_manual_revision_preserves_completed_node_display_and_full_content(tmp_path):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            answer = roadmap_answer()
            answer["display_title"] = "原路线简短名称"
            answer["nodes"][0].update(
                display_title="字符串读写", display_goal="写入问候语并读回验证。"
            )
            return httpx.Response(
                200, json=operation_response("roadmap_answer", answer)
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        run, _ = submit(client, REQUEST)
        route = run["roadmap"]
        done, _ = action(
            client,
            run["session_id"],
            "mastered",
            "complete_roadmap_node",
            {
                "roadmap_id": route["id"],
                "node_id": route["nodes"][0]["id"],
                "expected_version": route["version"],
            },
        )
        route = done["roadmap"]
        args = revision_args(route)
        args["title"] = "新的路线标题"
        args["nodes"][0]["exercise"] = route["nodes"][0]["exercise"]
        args["nodes"][0]["todo_title"] = route["nodes"][0]["todo_title"]
        args["nodes"][1]["todo_title"] = "检查键的过期状态"
        preview, events = action(
            client, run["session_id"], "edit", "preview_roadmap_revision", args
        )
        assert "event: roadmap_revision_preview" in events
        proposal = preview["roadmap"]["revision_proposals"][0]
        applied, _ = action(
            client,
            run["session_id"],
            "apply",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": proposal["id"],
                "sync_todo_ids": [],
            },
        )
        assert applied["roadmap"]["nodes"][0] == route["nodes"][0]
        assert applied["roadmap"]["title"] == "新的路线标题"
        assert applied["roadmap"].get("display_title") != "原路线简短名称"
        assert len(applied["reply"]) <= 800


@pytest.mark.parametrize("failure", ["internal", "timeout", "invalid"])
def test_unfinished_roadmap_never_appends_sources_to_final_reply(tmp_path, failure):
    import asyncio

    base = roadmap_provider([])

    async def provider(request):
        body = json.loads(request.content)
        if (
            body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            if failure == "internal":
                raise RuntimeError("provider failed")
            if failure == "timeout":
                await asyncio.sleep(2)
            return httpx.Response(200, json=operation_response("roadmap_answer", {}))
        return base(request)

    with roadmap_client(tmp_path, [], provider, run_timeout_seconds=0.3) as client:
        run, _ = submit(client, REQUEST)
        assert run["status"] == "partial"
        assert run["research"]["sources"] and run["research"]["gaps"]
        assert not run["roadmap"] and run["roadmap_links"] == []
        assert run["roadmap_context"]
        assert len(run["reply"]) <= 800 and "http" not in run["reply"]
        assert "未生成" in run["reply"]
        assert client.get("/api/roadmaps").json() == []
        assert client.get("/api/todos").json() == []

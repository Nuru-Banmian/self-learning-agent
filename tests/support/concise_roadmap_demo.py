"""Isolated browser fixture: public app and SQLite, simulated suppliers only."""

import asyncio
import json
import os
from pathlib import Path

import httpx

from app.main import create_app
from app.settings import Settings
from tests.test_maintenance import operation_response
from tests.test_roadmaps import complete_intake, roadmap_answer


def create_demo_app():
    async def provider(request):
        body = json.loads(request.content)
        if request.url.path == "/search/unified":
            if "fail-source" in body["query"]:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "pageItems": [
                        {
                            "title": "Redis strings",
                            "link": "https://redis.io/docs/latest/develop/data-types/strings/",
                            "snippet": (
                                "SET stores a string; EX sets expiry in seconds."
                            ),
                        }
                    ]
                },
            )
        if "response_format" in body:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
            )
        content = body["messages"][-1]["content"]
        if body["tools"][0]["function"]["name"] == "roadmap_answer":
            gate = os.environ.get("ROADMAP_PROVIDER_GATE")
            if gate:
                while not Path(gate).exists():
                    await asyncio.sleep(0.02)
            answer = roadmap_answer()
            if "部分资料" in content:
                answer["title"] = "Redis 缓存复习"
                answer["gaps"] = ["当前仅取得搜索摘要，尚未取得正文。"]
            answer["display_title"] = answer["title"]
            for node in answer["nodes"]:
                node["display_title"] = node["todo_title"]
                node["display_goal"] = node["goal"] if gate else node["exercise"]
            result = operation_response("roadmap_answer", answer)
        else:
            result = operation_response(
                "plan_learning_roadmap",
                {
                    "intake": complete_intake(content),
                    "query": "fail-source"
                    if "资料失败" in content
                    else "Redis strings",
                    "todo_ids": [],
                    "memory_ids": [],
                    "read_body": False,
                },
            )
        return httpx.Response(200, json=result)

    return create_app(
        Settings(
            _env_file=None,
            db_path=Path(os.environ["DB_PATH"]),
            dashscope_api_key="fixture",
            iqs_api_key="fixture",
        ),
        transport=httpx.MockTransport(provider),
    )

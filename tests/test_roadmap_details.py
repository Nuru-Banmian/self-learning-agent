"""Read saved node details through HTTP/SSE and real temporary SQLite."""

import json

import httpx
import pytest

from tests.test_chat import submit
from tests.test_maintenance import operation_response
from tests.test_roadmaps import REQUEST, roadmap_client, roadmap_provider


def test_requested_node_details_are_saved_read_only_and_survive_restart(tmp_path):
    requests = []
    with roadmap_client(tmp_path, requests) as client:
        route_run, _ = submit(client, REQUEST, "route")
        route = route_run["roadmap"]
        before_calls = len(requests)
        run, events = submit(
            client,
            "讲解一下第二个节点，给我资料、练习和完成标准",
            "details",
            route_run["session_id"],
        )
        assert run["status"] == "completed", run
        assert "event: terminal" in events
        assert "设置 EX 10 并用 TTL 观察，10秒后读取" in run["reply"]
        assert "过期前返回值，过期后返回空" in run["reply"]
        assert "在本地运行 SET greeting hello" not in run["reply"]
        assert "Redis strings" in run["reply"]
        assert "仅搜索摘要，未读取正文" in run["reply"]
        assert run["roadmap_links"] == [
            {
                "roadmap_id": route["id"],
                "node_id": route["nodes"][1]["id"],
                "title": route["title"],
            }
        ]
        assert len(requests) == before_calls
        assert client.get("/api/todos").json() == []
        assert len(client.get("/api/roadmaps").json()) == 1
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as client:
        saved = client.get("/api/runs/details").json()
        assert saved["reply"] == run["reply"]
        assert saved["roadmap_links"] == run["roadmap_links"]
        messages = client.get(f"/api/sessions/{run['session_id']}").json()["messages"]
        assert messages[-1]["content"] == run["reply"]
        assert messages[-1]["roadmap_links"] == run["roadmap_links"]


@pytest.mark.parametrize(
    "question",
    [
        "给我第二个节点的练习",
        "第二个节点怎么练习？",
        "我想看第 2 个节点的代码",
        "请讲解一下第二个节点",
        "讲解一下验证缓存过期这个节点",
    ],
)
def test_common_node_questions_read_the_requested_saved_node(tmp_path, question):
    requests = []
    with roadmap_client(tmp_path, requests) as client:
        created, _ = submit(client, REQUEST, "route")
        before = len(requests)
        run, _ = submit(client, question, "details", created["session_id"])
        assert "设置 EX 10 并用 TTL 观察，10秒后读取" in run["reply"]
        assert (
            run["roadmap_links"][0]["node_id"] == created["roadmap"]["nodes"][1]["id"]
        )
        assert len(requests) == before
        assert client.get("/api/todos").json() == []
        assert (
            client.get(f"/api/roadmaps/{created['roadmap']['id']}").json()
            == created["roadmap"]
        )


@pytest.mark.parametrize(
    "question",
    [
        "不要讲解第二个节点",
        "请不要展开第二个节点的资料",
        "“讲解一下第二个节点”的英文怎么说？",
        "“查看路线「路线乙」第二个节点的资料”",
        "解释这句话：查看第二个节点的练习",
        "给我记录第二个节点的练习",
        "第二个节点不用讲解怎么练习",
        "解释一下二叉树节点怎样连接",
        "讲解一下链表节点的代码",
    ],
)
def test_negated_quoted_and_incidental_words_do_not_open_details(tmp_path, question):
    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if (
            "response_format" not in body
            and body.get("messages", [{}])[-1].get("content") == question
        ):
            return httpx.Response(
                200,
                json=operation_response(
                    "answer_question", {"reply": "这是普通聊天答复", "memory_usage": []}
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as client:
        created, _ = submit(client, REQUEST, "route")
        run, _ = submit(client, question, "ordinary", created["session_id"])
        assert run["reply"] == "这是普通聊天答复"
        assert run["roadmap_links"] == []
        assert (
            client.get(f"/api/roadmaps/{created['roadmap']['id']}").json()
            == created["roadmap"]
        )


def test_route_reference_uses_session_or_explicit_identity_and_clarifies_ambiguity(
    tmp_path,
):
    requests = []
    with roadmap_client(tmp_path, requests) as client:
        first, _ = submit(client, REQUEST, "first")
        second, _ = submit(client, REQUEST, "second")
        before = len(requests)
        current, _ = submit(
            client, "查看第二个节点的资料", "current", first["session_id"]
        )
        assert current["roadmap_links"][0]["roadmap_id"] == first["roadmap"]["id"]
        ambiguous, _ = submit(client, "查看第二个节点的资料", "ambiguous")
        assert "明确" in ambiguous["reply"]
        assert ambiguous["roadmap_links"] == []
        route = second["roadmap"]
        node = route["nodes"][1]
        explicit, _ = submit(
            client,
            f"查看路线 {route['id']} 的节点 {node['id']} 的练习",
            "explicit",
            first["session_id"],
        )
        assert explicit["roadmap_links"][0]["roadmap_id"] == route["id"]
        assert explicit["roadmap_links"][0]["node_id"] == node["id"]
        by_node, _ = submit(
            client,
            f"讲解一下节点 {first['roadmap']['nodes'][0]['id']} 的代码",
            "by-node",
            second["session_id"],
        )
        assert by_node["roadmap_links"][0]["roadmap_id"] == first["roadmap"]["id"]
        missing, _ = submit(
            client,
            "查看路线 00000000-0000-0000-0000-000000000000 的第二个节点的资料",
            "missing",
            first["session_id"],
        )
        assert missing["roadmap_links"] == []
        assert "不存在" in missing["reply"]
        assert len(requests) == before
        assert client.get("/api/todos").json() == []
        assert len(client.get("/api/roadmaps").json()) == 2
        for created in (first, second):
            assert (
                client.get(f"/api/roadmaps/{created['roadmap']['id']}").json()
                == created["roadmap"]
            )


def test_quoted_explicit_route_title_takes_precedence_over_current_session(tmp_path):
    requests = []
    base = roadmap_provider(requests)
    titles = iter(["路线甲", "路线乙"])

    def provider(request):
        response = base(request)
        payload = response.json()
        calls = payload.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
        if calls and calls[0]["function"]["name"] == "roadmap_answer":
            answer = json.loads(calls[0]["function"]["arguments"])
            answer["title"] = next(titles)
            calls[0]["function"]["arguments"] = json.dumps(answer)
            return httpx.Response(200, json=payload)
        return response

    with roadmap_client(tmp_path, requests, provider) as client:
        first, _ = submit(client, REQUEST, "first")
        second, _ = submit(client, REQUEST, "second")
        before = len(requests)
        run, events = submit(
            client,
            "查看路线「路线乙」第二个节点的资料",
            "quoted-title",
            first["session_id"],
        )
        route = second["roadmap"]
        assert "event: terminal" in events
        assert run["status"] == "completed"
        assert run["roadmap_links"] == [
            {
                "roadmap_id": route["id"],
                "node_id": route["nodes"][1]["id"],
                "title": "路线乙",
            }
        ]
        assert "路线乙" in run["reply"]
        assert "路线甲" not in run["reply"]
        assert len(requests) == before
        assert client.get("/api/todos").json() == []
        for created in (first, second):
            assert (
                client.get(f"/api/roadmaps/{created['roadmap']['id']}").json()
                == created["roadmap"]
            )


@pytest.mark.parametrize("long_body", [False, True])
def test_explicit_code_request_preserves_saved_body_and_truncation(tmp_path, long_body):
    base = roadmap_provider([])
    source_body = "```python\nclient.set('answer', '42', ex=10)\n```\n"
    if long_body:
        source_body += "缓存读取与过期验证。" * 1100

    def provider(request):
        if request.url.path == "/readpage/basic":
            return httpx.Response(
                200, json={"data": {"statusCode": 200, "text": source_body}}
            )
        response = base(request)
        payload = response.json()
        if payload.get("choices"):
            calls = payload["choices"][0]["message"].get("tool_calls", [])
            if calls and calls[0]["function"]["name"] == "plan_learning_roadmap":
                args = json.loads(calls[0]["function"]["arguments"])
                args["read_body"] = True
                calls[0]["function"]["arguments"] = json.dumps(args)
                return httpx.Response(200, json=payload)
        return response

    with roadmap_client(tmp_path, [], provider) as client:
        created, _ = submit(client, REQUEST, "route")
        route = created["roadmap"]
        assert route["sources"][0]["body_truncated"] is long_body
        run, _ = submit(
            client, "我想看第二个节点的代码", "details", created["session_id"]
        )
        source = route["sources"][0]
        assert source["title"] in run["reply"]
        assert source["url"] in run["reply"]
        assert source["snippet"] in run["reply"]
        assert source["body"] in run["reply"]
        assert ("正文（已截取）" in run["reply"]) is long_body
        assert ("已取得正文" in run["reply"]) is not long_body
        assert "仅搜索摘要" not in run["reply"]
        if long_body:
            assert len(run["reply"]) > 800
        assert client.get(f"/api/roadmaps/{route['id']}").json() == route

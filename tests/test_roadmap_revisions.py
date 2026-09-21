"""Revision behavior at the public HTTP/SSE boundary, using real SQLite."""

import pytest

from tests.test_chat import submit
from tests.test_maintenance import action
from tests.test_roadmaps import REQUEST, roadmap_client


def revision_args(route):
    fields = (
        "goal",
        "estimated_minutes",
        "source_ids",
        "exercise",
        "completion_criteria",
        "todo_title",
        "scheduled_date",
    )
    nodes = [{"node_id": n["id"], **{k: n[k] for k in fields}} for n in route["nodes"]]
    nodes[0]["exercise"] = "只运行一次 SET 和 GET，并记录结果"
    nodes[0]["todo_title"] = "完成一个简单的 Redis 读写练习"
    return {
        "roadmap_id": route["id"],
        "expected_version": route["version"],
        "title": route["title"],
        "goal": route["goal"],
        "nodes": nodes,
    }


def test_replacing_prerequisite_warns_without_rewriting_retained_nodes(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        args = revision_args(route)
        args["nodes"][0]["exercise"] = "改用容器内 redis-cli 执行 SET 和 GET"
        preview, events = action(
            c, generated["session_id"], "preview", "preview_roadmap_revision", args
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert any("先修" in gap and "核验" in gap for gap in proposal["gaps"])
        assert "限制" in preview["reply"] and len(preview["reply"]) <= 800
        assert "event: roadmap_revision_preview" in events
        assert preview["roadmap"]["nodes"] == route["nodes"]
        assert proposal["nodes"][1]["exercise"] == route["nodes"][1]["exercise"]
        assert c.get("/api/todos").json() == []
        done, _ = action(
            c,
            generated["session_id"],
            "confirm",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": proposal["id"],
                "sync_todo_ids": [],
            },
        )
        assert done["roadmap"]["status"] == "partial"
        assert proposal["gaps"][0] in done["roadmap"]["gaps"]
        assert done["roadmap"]["nodes"][1] == route["nodes"][1]
    with roadmap_client(tmp_path, [], dashscope_api_key="", iqs_api_key="") as c:
        assert c.get(f"/api/roadmaps/{route['id']}").json() == done["roadmap"]


def test_unjoined_revision_preview_confirm_and_restart(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        preview, events = action(
            c, session, "preview", "preview_roadmap_revision", revision_args(route)
        )
        assert "event: roadmap_revision_preview" in events, preview
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert preview["roadmap"]["nodes"] == route["nodes"]
        assert c.get("/api/todos").json() == []
    with roadmap_client(tmp_path, [], dashscope_api_key="") as c:
        session = c.post("/api/sessions").json()["id"]
        args = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": [],
        }
        done, events = action(c, session, "confirm", "confirm_roadmap_revision", args)
        assert "event: roadmap_revision_applied" in events, done
        assert (
            done["roadmap"]["nodes"][0]["exercise"]
            == "只运行一次 SET 和 GET，并记录结果"
        )
        assert [n["id"] for n in done["roadmap"]["nodes"]] == [
            n["id"] for n in route["nodes"]
        ]
        assert c.get("/api/todos").json() == []
        repeated, _ = action(c, session, "repeat", "confirm_roadmap_revision", args)
        assert repeated["roadmap"] == done["roadmap"]


@pytest.mark.parametrize(
    "instruction",
    [
        "这条路线太难了，改简单一点，搜索新的入门资料",
        "请调整这条路线，把第一个练习改简单一点，不要修改日期，搜索新的入门资料",
        "请调整这条路线，把第一个节点的标题改为 Redis 入门练习，搜索新的入门资料",
        "请调整这条路线，不要修改路线中已完成的节点，搜索新的入门资料",
    ],
)
@pytest.mark.parametrize("query", ["Redis beginner SET GET official", None])
def test_natural_adjustment_searches_and_only_saves_reviewable_proposal(
    tmp_path, query, instruction
):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    requests = []
    base = roadmap_provider(requests)

    def provider(request):
        body = json.loads(request.content)
        name = body.get("tools", [{}])[0].get("function", {}).get("name")
        if name == "revision_plan":
            return httpx.Response(
                200,
                json=operation_response(
                    name,
                    {"query": query, "unsupported": []},
                ),
            )
        if name == "revision_answer":
            context = json.loads(body["messages"][-1]["content"])
            proposed = revision_args(context["route"])
            proposed["nodes"][0]["source_ids"] = [context["sources"][-1]["id"]]
            return httpx.Response(200, json=operation_response(name, proposed))
        return base(request)

    with roadmap_client(tmp_path, requests, provider) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        before = len([url for url, _ in requests if "/search/unified" in url])
        preview, events = submit(
            c,
            instruction,
            "revision",
            generated["session_id"],
        )
        assert "event: roadmap_revision_preview" in events, preview
        assert (
            len([url for url, _ in requests if "/search/unified" in url]) == before + 1
        )
        assert preview["roadmap"]["nodes"] == route["nodes"]
        proposal = preview["roadmap"]["revision_proposals"][0]
        assert proposal["sources"][-1]["url"].startswith("https://redis.io/")
        assert c.get("/api/todos").json() == []


def test_linked_content_requires_sync_and_refusal_keeps_entire_proposal_pending(
    tmp_path,
):
    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        args = revision_args(route)
        args["nodes"][0]["todo_title"] = route["nodes"][0]["todo_title"]
        args["nodes"][1]["exercise"] = "观察 TTL 倒计时并记录过期结果"
        preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
        proposal = preview["roadmap"]["revision_proposals"][0]
        todo_id = route["nodes"][0]["todo_id"]
        assert proposal["sync_todo_ids"] == [todo_id]
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        todos = c.get("/api/todos").json()
        confirm = {
            "roadmap_id": route["id"],
            "proposal_id": proposal["id"],
            "sync_todo_ids": [],
        }
        refused, events = action(
            c, session, "refuse", "confirm_roadmap_revision", confirm
        )
        assert "event: roadmap_revision_pending" in events
        assert refused["roadmap"] == current
        assert c.get("/api/todos").json() == todos
        confirm["sync_todo_ids"] = [todo_id]
        done, _ = action(c, session, "confirm", "confirm_roadmap_revision", confirm)
        assert done["roadmap"]["nodes"][0]["todo_id"] == todo_id
        assert (
            done["roadmap"]["nodes"][1]["exercise"] == "观察 TTL 倒计时并记录过期结果"
        )
        assert done["roadmap"]["nodes"][1]["scheduled_date"] is None
        assert c.get("/api/todos").json() == todos


def test_completed_content_protected_and_replacement_keeps_history_links(tmp_path):
    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        completed, _ = action(
            c, session, "complete", "complete_roadmap_node", target(accepted["roadmap"])
        )
        route = completed["roadmap"]
        original = route["nodes"][0]
        rejected, _ = action(
            c, session, "invalid", "preview_roadmap_revision", revision_args(route)
        )
        assert "已完成" in rejected["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route
        args = revision_args(route)
        args["nodes"][0]["node_id"] = None
        preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
        p = preview["roadmap"]["revision_proposals"][0]
        assert any(
            e["kind"] == "archive" and e["todo_after"]["id"] == original["todo_id"]
            for e in p["entries"]
            if e["todo_after"]
        )
        done, _ = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": p["id"]},
        )
        route = done["roadmap"]
        assert route["nodes"][0]["id"] != original["id"]
        assert route["nodes"][0]["status"] == "pending"
        assert route["nodes"][0]["completion"] is None
        assert route["nodes"][0]["todo_id"] is None
        assert route["history_nodes"][0]["id"] == original["id"]
        assert route["history_nodes"][0]["completion"] == original["completion"]
        assert route["history_nodes"][0]["todo"] == original["todo"]
        assert c.get("/api/todos").json()[0]["roadmap"]["node_id"] == original["id"]
    with roadmap_client(tmp_path, [], dashscope_api_key="") as c:
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route


@pytest.mark.parametrize(
    "change", ["accept", "complete", "edit", "manual_date", "revision"]
)
def test_another_session_invalidates_old_proposal_without_overwriting(change, tmp_path):
    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        preview, _ = action(
            c, session, "preview", "preview_roadmap_revision", revision_args(route)
        )
        proposal = preview["roadmap"]["revision_proposals"][0]
        other = c.post("/api/sessions").json()["id"]
        if change == "accept":
            action(c, other, "change", "accept_roadmap_node", target(route, 1))
        elif change == "complete":
            action(c, other, "change", "complete_roadmap_node", target(route))
        elif change == "edit":
            action(
                c,
                other,
                "change",
                "update_todo",
                {
                    "todo_id": route["nodes"][0]["todo_id"],
                    "title": "我自行安排的练习",
                    "date_text": "2026-10-04",
                },
            )
        elif change == "manual_date":
            action(
                c,
                other,
                "change",
                "update_todo",
                {
                    "todo_id": route["nodes"][0]["todo_id"],
                    "date_text": "2026-10-04",
                },
            )
        else:
            p, _ = action(
                c,
                other,
                "other-preview",
                "preview_roadmap_revision",
                revision_args(route),
            )
            action(
                c,
                other,
                "change",
                "confirm_roadmap_revision",
                {
                    "roadmap_id": route["id"],
                    "proposal_id": p["roadmap"]["revision_proposals"][0]["id"],
                    "sync_todo_ids": proposal["sync_todo_ids"],
                },
            )
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        todos = c.get("/api/todos").json()
        stale, events = action(
            c,
            session,
            "stale",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": proposal["id"],
                "sync_todo_ids": proposal["sync_todo_ids"],
            },
        )
        assert "event: roadmap_revision_stale" in events
        assert stale["roadmap"]["nodes"] == before["nodes"]
        assert stale["roadmap"]["version"] == before["version"]
        assert c.get("/api/todos").json() == todos


def test_atomic_rollback_and_explicit_retry_preserves_manual_date(tmp_path):
    import sqlite3

    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        action(
            c,
            session,
            "manual-date",
            "update_todo",
            {"todo_id": route["nodes"][0]["todo_id"], "date_text": "2026-10-03"},
        )
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        args = revision_args(route)
        preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
        p = preview["roadmap"]["revision_proposals"][0]
        before = c.get(f"/api/roadmaps/{route['id']}").json()
        todos = c.get("/api/todos").json()
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute(
                "CREATE TRIGGER fail_revision BEFORE INSERT ON events "
                "WHEN NEW.kind='roadmap_revision_applied' "
                "BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        failed, events = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_revision",
            {
                "roadmap_id": route["id"],
                "proposal_id": p["id"],
                "sync_todo_ids": p["sync_todo_ids"],
            },
        )
        assert failed["status"] == "failed"
        assert "event: roadmap_revision_applied" not in events
        assert c.get(f"/api/roadmaps/{route['id']}").json() == before
        assert c.get("/api/todos").json() == todos
        with sqlite3.connect(tmp_path / "roadmaps.db") as db:
            db.execute("DROP TRIGGER fail_revision")
        retry = c.post("/api/runs/confirm/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert (
            done["roadmap"]["nodes"][0]["todo"]["title"]
            == "完成一个简单的 Redis 读写练习"
        )
        assert done["roadmap"]["nodes"][0]["todo"]["scheduled_date"] == "2026-10-03"
        assert len(c.get("/api/todos").json()) == 1
        replay = c.get(f"/api/runs/{retry['id']}/events").text
        assert "event: roadmap_revision_applied" in replay


def test_archived_pending_todo_can_complete_and_remains_queryable(tmp_path):
    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        args = revision_args(route)
        args["nodes"].pop(0)
        preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
        p = preview["roadmap"]["revision_proposals"][0]
        done, _ = action(
            c,
            session,
            "confirm",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": p["id"]},
        )
        assert (
            done["roadmap"]["history_nodes"][0]["todo_id"]
            == route["nodes"][0]["todo_id"]
        )
        completed, _ = action(
            c,
            session,
            "complete",
            "complete_todo",
            {"todo_id": route["nodes"][0]["todo_id"]},
        )
        assert completed["roadmap"]["history_nodes"][0]["completion"]
        assert completed["roadmap"]["history_nodes"][0]["status"] == "completed"


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_revision_survives_real_process_death(tmp_path, after_commit, operation):
    from tests.test_process import free_port, server_process
    from tests.test_roadmap_progress import target

    port, database = free_port(), tmp_path / "restart.db"
    gate = tmp_path / "release"
    gate.touch()
    options = {
        "factory": "tests.roadmap_demo:create_demo_app",
        "extra_env": {"ROADMAP_PROVIDER_GATE": str(gate)},
    }
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        process,
    ):
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        action(
            c,
            session,
            "manual-date",
            "update_todo",
            {"todo_id": route["nodes"][0]["todo_id"], "date_text": "2026-10-01"},
        )
        route = c.get(f"/api/roadmaps/{route['id']}").json()
        args = revision_args(route)
        if operation == "confirm":
            preview, _ = action(c, session, "preview", "preview_roadmap_revision", args)
            args = {
                "roadmap_id": route["id"],
                "proposal_id": preview["roadmap"]["revision_proposals"][0]["id"],
                "sync_todo_ids": preview["roadmap"]["revision_proposals"][0][
                    "sync_todo_ids"
                ],
            }
        if not after_commit:
            gate.unlink()
            c.post(
                f"/api/sessions/{session}/messages",
                json={"request_id": "blocked", "content": REQUEST},
            ).raise_for_status()
        body = {
            "request_id": "schedule",
            "content": "面板内容调整操作",
            "action": {"tool": f"{operation}_roadmap_revision", "arguments": args},
        }
        c.post(f"/api/sessions/{session}/messages", json=body).raise_for_status()
        event_name = (
            "roadmap_revision_preview"
            if operation == "preview"
            else "roadmap_revision_applied"
        )
        if after_commit:
            with c.stream("GET", "/api/runs/schedule/events") as stream:
                for line in stream.iter_lines():
                    if line == f"event: {event_name}":
                        break
                else:
                    raise AssertionError("missing committed revision event")
        else:
            assert c.get("/api/runs/schedule").json()["status"] == "queued"
        process.kill()
        process.wait(5)
    gate.touch()
    with server_process(port, database, "http://fixture.invalid/v1", **options) as (
        c,
        restarted,
    ):
        assert restarted.pid != process.pid
        original = c.get("/api/runs/schedule").json()
        assert original["status"] == ("completed" if after_commit else "failed")
        assert c.post(f"/api/sessions/{session}/messages", json=body).json() == original
        retry = c.post("/api/runs/schedule/retry").json()
        c.get(f"/api/runs/{retry['id']}/events")
        done = c.get(f"/api/runs/{retry['id']}").json()
        assert done["status"] == "completed"
        assert len(done["roadmap"]["revision_proposals"]) == 1
        assert done["roadmap"]["revision_proposals"][0]["status"] == (
            "pending" if operation == "preview" else "applied"
        )
        assert c.get("/api/todos").json()[0]["scheduled_date"] == "2026-10-01"
        saved = next(e for e in done["events"] if e["kind"] == event_name)
        remaining = c.get(
            f"/api/runs/{done['id']}/events",
            headers={"Last-Event-ID": str(saved["seq"])},
        ).text
        assert (
            f"event: {event_name}" not in remaining and "event: terminal" in remaining
        )


def test_noop_does_not_persist_a_fake_adjustment(tmp_path):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        args = revision_args(route)
        args["nodes"][0]["exercise"] = route["nodes"][0]["exercise"]
        args["nodes"][0]["todo_title"] = route["nodes"][0]["todo_title"]
        result, _ = action(
            c, generated["session_id"], "noop", "preview_roadmap_revision", args
        )
        assert "没有实际变化" in result["reply"]
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route


@pytest.mark.parametrize("mode", ["empty", "error", "partial"])
def test_revision_search_reports_gaps_and_never_mutates_current_route(tmp_path, mode):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    requests = []
    base = roadmap_provider(requests)
    revising = False

    def provider(request):
        body = json.loads(request.content)
        name = body.get("tools", [{}])[0].get("function", {}).get("name")
        if name == "revision_plan":
            return httpx.Response(
                200,
                json=operation_response(
                    name, {"query": "Redis beginner", "unsupported": []}
                ),
            )
        if name == "revision_answer":
            context = json.loads(body["messages"][-1]["content"])
            return httpx.Response(
                200, json=operation_response(name, revision_args(context["route"]))
            )
        if revising and request.url.path == "/search/unified":
            if mode == "error":
                return httpx.Response(503)
            if mode == "empty":
                return httpx.Response(200, json={"pageItems": []})
            payload = base(request).json()
            payload["pageItems"].append({"title": "invalid source"})
            return httpx.Response(200, json=payload)
        return base(request)

    with roadmap_client(tmp_path, requests, provider, search_retries=0) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        revising = True
        result, events = submit(
            c, "调整这条路线，搜索新的入门资料", "revision", generated["session_id"]
        )
        assert result["status"] == "partial"
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        assert current["nodes"] == route["nodes"]
        assert c.get("/api/todos").json() == []
        if mode == "partial":
            assert "event: roadmap_revision_preview" in events
            assert current["revision_proposals"][0]["gaps"]
        else:
            assert current == route
            assert "尚未生成" in result["reply"]


def test_parallel_confirmation_applies_only_one_competing_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        sessions = [c.post("/api/sessions").json()["id"] for _ in range(2)]
        proposals = []
        for i in range(2):
            args = revision_args(route)
            args["nodes"][0]["todo_title"] = f"竞争方案 {i}"
            result, _ = action(
                c, sessions[i], f"preview-{i}", "preview_roadmap_revision", args
            )
            proposals.append(result["roadmap"]["revision_proposals"][0])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    action,
                    c,
                    sessions[i],
                    f"confirm-{i}",
                    "confirm_roadmap_revision",
                    {"roadmap_id": route["id"], "proposal_id": p["id"]},
                )
                for i, p in enumerate(proposals)
            ]
            results = [f.result() for f in futures]
        assert sum("event: roadmap_revision_applied" in ev for _, ev in results) == 1
        assert sum("event: roadmap_revision_stale" in ev for _, ev in results) == 1
        current = c.get(f"/api/roadmaps/{route['id']}").json()
        assert current["version"] == route["version"] + 1
        assert c.get("/api/todos").json() == []


@pytest.mark.parametrize(
    ("reference", "new_title"),
    [
        ("买牛奶", "调整路线"),
        ("买牛奶的", "调整路线"),
        ("第一条待办", "调整路线"),
        ("买牛奶", "调整路线，整理资料"),
    ],
)
def test_todo_rename_with_revision_words_keeps_normal_todo_authorization(
    tmp_path, reference, new_title
):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])
    todo_id = ""

    def provider(request):
        body = json.loads(request.content)
        if body.get("tools") and "请记录" in body["messages"][-1]["content"]:
            return httpx.Response(
                200,
                json=operation_response(
                    "create_todos",
                    {"items": [{"title": "买牛奶", "date_text": "明天"}]},
                ),
            )
        if body.get("tools") and "标题改为" in body["messages"][-1]["content"]:
            return httpx.Response(
                200,
                json=operation_response(
                    "update_todo", {"todo_id": todo_id, "title": new_title}
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        created, _ = submit(c, "请记录明天买牛奶", "create", session)
        todo_id = created["todo_ids"][0]
        changed, _ = submit(c, f"把{reference}标题改为{new_title}", "rename", session)
        assert changed["status"] == "completed", changed
        if reference != "第一条待办":
            assert changed["todo_ids"] == [todo_id]
            assert c.get("/api/todos").json()[0]["title"] == new_title
        else:
            assert "目标不明确" in changed["reply"]
            assert c.get("/api/todos").json()[0]["title"] == "买牛奶"
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route


def test_todo_title_with_revision_words_keeps_normal_todo_authorization(tmp_path):
    import json

    import httpx

    from tests.test_maintenance import operation_response
    from tests.test_roadmaps import roadmap_provider

    base = roadmap_provider([])

    def provider(request):
        body = json.loads(request.content)
        if body.get("tools") and "请记录" in body["messages"][-1]["content"]:
            return httpx.Response(
                200,
                json=operation_response(
                    "create_todos",
                    {"items": [{"title": "调整路线图", "date_text": "明天"}]},
                ),
            )
        return base(request)

    with roadmap_client(tmp_path, [], provider) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        saved, _ = submit(c, "请记录明天调整路线图", "todo", generated["session_id"])
        assert len(c.get("/api/todos").json()) == 1, saved
        assert c.get("/api/todos").json()[0]["title"] == "调整路线图"
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route


def test_refused_sync_can_be_replaced_by_separately_confirmed_unjoined_diff(tmp_path):
    from tests.test_roadmap_progress import target

    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route, session = generated["roadmap"], generated["session_id"]
        accepted, _ = action(c, session, "accept", "accept_roadmap_node", target(route))
        route = accepted["roadmap"]
        original, _ = action(
            c,
            session,
            "large-preview",
            "preview_roadmap_revision",
            revision_args(route),
        )
        pending = original["roadmap"]["revision_proposals"][0]
        action(
            c,
            session,
            "refuse",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": pending["id"]},
        )
        small = revision_args(route)
        small["nodes"][0]["exercise"] = route["nodes"][0]["exercise"]
        small["nodes"][0]["todo_title"] = route["nodes"][0]["todo_title"]
        small["nodes"][1]["estimated_minutes"] = 20
        result, _ = action(
            c, session, "small-preview", "preview_roadmap_revision", small
        )
        proposal = result["roadmap"]["revision_proposals"][0]
        assert proposal["id"] != pending["id"]
        assert proposal["sync_todo_ids"] == []
        assert len(proposal["entries"]) == 1
        done, _ = action(
            c,
            session,
            "confirm-small",
            "confirm_roadmap_revision",
            {"roadmap_id": route["id"], "proposal_id": proposal["id"]},
        )
        assert done["roadmap"]["nodes"][0] == route["nodes"][0]
        assert done["roadmap"]["nodes"][1]["estimated_minutes"] == 20
        assert done["roadmap"]["revision_proposals"][1]["status"] == "pending"


@pytest.mark.parametrize(
    "content",
    [
        "不要把这条路线改简单，先保持原状。",
        "这条路线太难了，但先不要调整我的路线。",
        "暂不调整这条路线，先保持原状。",
        "不要修改路线中已完成的节点。",
    ],
)
def test_negated_revision_does_not_request_a_model_or_save_proposal(tmp_path, content):
    with roadmap_client(tmp_path, []) as c:
        generated, _ = submit(c, REQUEST)
        route = generated["roadmap"]
        result, _ = submit(c, content, "negative", generated["session_id"])
        assert result["model_calls"] == 0
        assert c.get(f"/api/roadmaps/{route['id']}").json() == route

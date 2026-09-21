"""Resolve explicit, read-only requests against saved roadmap content."""

import re
from typing import Any

from app.roadmaps import unquoted_request
from app.store import Store
from app.todos import Clarification


def finish_node_details(store: Store, run: dict[str, Any]) -> bool:
    text = unquoted_request(run["content"]).strip()
    if "节点" not in text:
        return False
    if re.search(r"这句话|这段话|这条指令|翻译|比如|例如|假如|如果", text):
        return False
    if re.search(
        r"(?:不要|不用|不需要|无需|不必|别|暂不|先不).{0,6}"
        r"(?:讲解|解释|查看|展开|提供|展示)",
        text,
    ):
        return False
    if re.match(r"^(?:请|麻烦)?(?:帮我|给我)?(?:记录|记下|添加|创建|保存)", text):
        return False
    imperative = re.match(
        r"^(?:请|麻烦)?(?:帮我|给我)?(?:讲解|解释|说明|查看|看看|看一下|展开|展示|提供)|"
        r"^(?:请)?给我.*(?:资料|练习|代码|详情|完成标准)|"
        r"^我想(?:看|了解).*(?:节点)|"
        r"^第\s*[1-8一二三四五六七八]\s*(?:个)?节点.*(?:怎么|如何|是什么|有哪些)",
        text,
    )
    if not imperative:
        return False
    reference = re.search(
        r"路线|第\s*[0-9一二三四五六七八九十]+\s*(?:个)?节点|"
        r"(?:这|那|该|此)(?:个)?节点|节点\s*[0-9a-f]{8}-",
        text,
    )
    if not reference and not any(
        value and value in text
        for summary in store.roadmaps()
        for route in [store.roadmap(summary["id"])]
        if route
        for node in route["nodes"]
        for value in (node["todo_title"], node["goal"], node.get("display_title"))
    ):
        return False
    try:
        # Quoted route or node names still identify the requested saved object.
        # Only intent detection ignores quotations containing whole instructions.
        route, node = resolve_node(store, run, run["content"])
    except Clarification as exc:
        store.finish(run["id"], "completed", str(exc), roadmap_context=True)
        return True
    reply = render_node(route, node)
    store.finish(
        run["id"],
        "completed",
        reply,
        roadmap_context=True,
        roadmap_links=[
            {
                "roadmap_id": route["id"],
                "node_id": node["id"],
                "title": route["title"],
            }
        ],
    )
    return True


def resolve_node(
    store: Store, run: dict[str, Any], text: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    routes = store.roadmaps()
    explicit_ids = re.findall(
        r"路线\s*([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", text, re.I
    )
    if any(route_id not in {r["id"] for r in routes} for route_id in explicit_ids):
        raise Clarification("指定的学习路线不存在，请核对路线标识后重试。")
    matched = [r for r in routes if r["id"] in text or r["title"] in text]
    if not matched and re.search(r"节点\s*[0-9a-f]{8}-", text, re.I):
        for summary in routes:
            candidate = store.roadmap(summary["id"])
            if candidate and any(
                n["id"] in text
                for n in candidate["nodes"] + candidate.get("history_nodes", [])
            ):
                matched.append(summary)
    if not matched:
        links = store.session_roadmap_links(run["session_id"], run["id"])
        ids = {link["roadmap_id"] for link in links}
        matched = [r for r in routes if r["id"] in ids]
    matched = matched or routes
    if len(matched) != 1:
        raise Clarification("请明确要查看哪条学习路线，提供路线标题或标识及节点序号。")
    route = store.roadmap(matched[0]["id"])
    assert route is not None
    nodes = route["nodes"] + route.get("history_nodes", [])
    selected = [
        n
        for n in nodes
        if any(
            n.get(field) and n[field] in text
            for field in ("id", "todo_title", "goal", "display_title")
        )
    ]
    if not selected:
        ordinal = re.search(r"第\s*([1-8一二三四五六七八])\s*(?:个)?节点", text)
        if ordinal:
            value = ordinal[1]
            position = (
                int(value) if value.isdigit() else "一二三四五六七八".index(value) + 1
            )
            selected = [n for n in route["nodes"] if n["position"] == position]
    if len(selected) != 1:
        raise Clarification("请明确要查看的节点序号、标题或标识；当前路线保持原状。")
    return route, selected[0]


def render_node(route: dict[str, Any], node: dict[str, Any]) -> str:
    parts = [
        f"{route['title']} · 第 {node['position']} 个节点：{node['todo_title']}",
        f"目标：{node['goal']}",
        f"练习：{node['exercise']}",
        f"完成标准：{node['completion_criteria']}",
    ]
    for source in route["sources"]:
        if source["id"] not in node["source_ids"]:
            continue
        parts.append(f"[{source['id']}] {source['title']}\n{source['url']}")
        parts.append(f"搜索摘要：{source['snippet']}")
        if source.get("summary"):
            parts.append(f"来源补充摘要：{source['summary']}")
        if source["material_type"] == "body" and source.get("body"):
            label = "正文（已截取）" if source.get("body_truncated") else "已取得正文"
            parts.append(f"{label}：\n{source['body']}")
        else:
            parts.append("仅搜索摘要，未读取正文。")
    if route["gaps"]:
        parts.append("资料缺口：\n" + "\n".join(route["gaps"]))
    return "\n\n".join(parts)

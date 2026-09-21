"""Bounded roadmap summaries; full content remains in the roadmap record."""

import re
from typing import Any


def render_nodes(nodes: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{i}. {n.get('display_title') or n['todo_title']}："
        f"{n.get('display_goal') or n['goal']}"
        for i, n in enumerate(nodes, 1)
    )


def validate_summary(text: str) -> str:
    # Leave space for one final memory-processing limitation, added at commit.
    # Invalid provider prose is rejected, never sliced through a goal or node.
    if len(text) > 780 or re.search(r"https?://|www\.|```", text):
        raise ValueError("路线概述无效，请重试生成简短且完整的节点目标。")
    return text


def render_route(content: dict[str, Any]) -> str:
    title = content.get("display_title") or content["title"]
    text = f"学习路线：{title}\n" + render_nodes(content["nodes"])
    text += "\n\n路线已保存，尚未加入待办。打开详情，选择节点后确认加入，也可暂不加入。"
    if content["gaps"]:
        text += "\n部分资料或处理未完成，请展开详情查看限制。"
    return validate_summary(text)

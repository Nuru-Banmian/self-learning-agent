"""Recognize retired route scheduling without collecting scheduling conditions."""

import re

from app.roadmaps import explicit_roadmap, unquoted_request
from app.store import Store


def scheduling_request(content: str) -> bool:
    return bool(
        re.search(r"排期|(?:安排|规划|清空|清除|取消|设置).{0,12}日期", content)
        or (
            re.search(r"路线|节点", content)
            and re.search(r"改到|改期|日期改为|安排到|安排在", content)
        )
    )


def chat_schedule(store: Store, content: str) -> bool:
    text = unquoted_request(content)
    # A negated date clause protects dates; it must not cancel a separate
    # affirmative content adjustment in the same request.
    affirmative = re.sub(
        r"(?:不要|不用|不需要|不必|无需|别|暂不|不(?=排期|安排日期))"
        r"[^，,。；;！？!?\n]*",
        "",
        text,
    )
    if not scheduling_request(affirmative) and re.search(
        r"调整|修改|简化|重排|改简单|改容易|太难了|学习目标.*改", affirmative
    ):
        return False
    # Direct ordinary todo commands retain their own date authorization checks.
    if re.search(
        r"^\s*(?:请|麻烦)?(?:帮我|给我)?(?:记录|记下|记一条|添加待办|创建待办|完成|标记完成)",
        text,
    ):
        return False
    if explicit_roadmap(content):
        return False
    target = re.fullmatch(
        r"\s*(?:请|麻烦)?(?:帮我)?(?:把|将)(.+?)(?:的)?"
        r"(?:日期改为|改期到|改到).*",
        text,
    )
    if target and (
        target[1].strip().startswith("待办")
        or any(
            target[1].strip() in (todo["id"], todo["title"]) for todo in store.todos()
        )
    ):
        return False
    if not re.search(r"路线|节点", text) and re.search(
        r"改到|改期到|日期改为|标题改为|改名为|标记为?已?完成", text
    ):
        return False
    return scheduling_request(text) and bool(re.search(r"路线|节点|排期", text))

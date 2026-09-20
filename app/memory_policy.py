"""Conservative source and scope rules shared by extraction and persistence."""

import re


def general_time_condition(content: str) -> bool:
    return (
        re.fullmatch(
            r"(?:我)?(?:今天|明天)(?:我)?(?:只有|仅有|有)"
            r"(?:半(?:个)?小时|[一二两三四五六七八九十百\d]+(?:个小时|小时|分钟))"
            r"(?:时间|空闲时间)?",
            content,
        )
        is not None
    )


def fact_attribute(content: str) -> str | None:
    for attribute, pattern in (
        ("residence", r"我(?:住在|居住在)"),
        ("occupation", r"我(?:从事|工作是)"),
        ("identity", r"我是"),
    ):
        if re.match(pattern, content):
            return attribute
    return None


def source_clauses(content: str) -> list[str]:
    # Conservative supported assertions, never quotations, hypotheticals or tasks.
    if re.search(
        r'[“”「」"：:？?]|假如|如果|可能|也许|据说|他说|她说|网页|文章|助理|示例|翻译|解释这',
        content,
    ):
        return []
    return [p.strip() for p in re.split(r"[，,。；;\n！？!?]", content) if p.strip()]


def category_of(clause: str) -> str | None:
    if re.search(
        r"这周|本周|下周|这月|本月|今年|最近|暂时|这几天|\d+月|\d+号|\d{4}-", clause
    ):
        return None
    if re.search(r"吗|是否|是不是|请记录|我要|打算|准备|帮我|提醒我", clause):
        return None
    if re.search(r"今天|明天|这次|本次|这项|这件", clause):
        return (
            "condition"
            if re.search(r"只有|只能|仅有|限于|用|需要|优先|喜欢", clause)
            else None
        )
    if re.match(
        r"(?:我|以后)(?:更|通常|一般|一直|比较)?(?:喜欢|偏好|习惯|优先|不喜欢|不爱|倾向)",
        clause,
    ):
        return "preference"
    if re.match(r"我(?:是|住在|居住在|正在学习|在学|目前在学|从事|工作是)", clause):
        return "background"
    return None

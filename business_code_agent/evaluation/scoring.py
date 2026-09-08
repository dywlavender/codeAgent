"""Review parsing and quote validation shared by the CLI and the workbench.

A review is only accepted when every satisfied check quotes text that really
exists in the candidate answer. Failed or malformed reviews stay failures;
they must never be scored as zero points.
"""

from __future__ import annotations

import json
import re


class RubricInvalid(ValueError):
    """The source contradicts the rubric; this is not an answer failure."""


def quote_matches(quote, candidate):
    # Markdown styling, whitespace and CJK quote decorations do not change a
    # quote's meaning; the surviving text still has to match verbatim.
    normalize = lambda text: re.sub(r"[\s`*【】「」“”\"'‘’·—－-]", "", text)
    source = normalize(candidate or "")
    offset = 0
    for fragment in re.split(r"…+|\.{3}|。。+", quote or ""):
        fragment = normalize(fragment)
        if not fragment:
            continue
        found = source.find(fragment, offset)
        if found < 0:
            return False
        offset = found + len(fragment)
    return offset > 0


def parse_review(answer, count, candidate=None):
    text = str(answer or "").strip()
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    if len(fenced) == 1:
        text = fenced[0]
    value = json.loads(text)
    if value.get("rubricInvalid"):
        raise RubricInvalid("评分标准与源码冲突，需修订标准后重评：" + str(value["rubricInvalid"]))
    checks = value.get("checks", [])
    if len(checks) != count or any(type(c.get("met")) is not bool or not c.get("reason") or not isinstance(c.get("evidence"), str) for c in checks):
        raise ValueError("评分格式错误：每项需要 met 布尔值、reason、evidence")
    if any(c["met"] and not c["evidence"].strip() for c in checks):
        raise ValueError("满足项缺少源码依据")
    if candidate is not None and any(c["met"] and (not c.get("candidateQuote") or not quote_matches(c["candidateQuote"], candidate)) for c in checks):
        raise ValueError("满足项必须引用候选答案中实际存在的原文")
    if not isinstance(value.get("issues"), list) or any(not isinstance(i, str) for i in value["issues"]):
        raise ValueError("缺少 issues 字符串列表")
    return {"status": "completed", "checks": checks, "issues": value["issues"]}


def model_review_score(result, case):
    """Score from the answer's own completed model review, else ``None``."""
    if not case.get("checks"):
        return None
    review = result.get("review") or {}
    if review.get("status") != "completed":
        return None
    try:
        checked = parse_review(
            json.dumps(review), len(case["checks"]),
            result.get("answer") if review.get("version") == 2 else None,
        )
    except (ValueError, TypeError, KeyError):
        return None
    return sum(c["met"] for c in checked["checks"]), len(checked["checks"]), len(checked["issues"]), "模型初评"


def human_review_score(entry, case):
    """Score from one human/imported review record, else ``None``.

    A record only counts when every check is filled; partially reviewed
    answers cannot enter a paired quality total.
    """
    if not case.get("checks"):
        return None
    checks = entry.get("checks") or []
    if len(checks) != len(case["checks"]) or any(x not in (0, 1) for x in checks):
        return None
    return sum(checks), len(checks), len(entry.get("issues") or []), entry.get("operatorType") or "user"

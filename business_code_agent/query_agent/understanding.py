"""Bounded question understanding with no repository/index access."""

from __future__ import annotations

import re

from .models import QueryIntent, QuestionUnderstanding


_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.$]*\b")
_QUOTED = re.compile(r"[`'\"“”‘’]([^`'\"“”‘’]{1,80})[`'\"“”‘’]")
_PROCESS = re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{1,24}?(?:流程|阶段|环节))")
_SYSTEM = re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{1,24}?(?:系统|平台|服务))")
_FIELD_LABEL = re.compile(r"([\u4e00-\u9fffA-Za-z_][\u4e00-\u9fffA-Za-z0-9_]{0,40})\s*(?:字段|属性|列)")
_TABLE_LABEL = re.compile(r"(?:表|table)\s*[`'\"“”‘’]?([A-Za-z_][A-Za-z0-9_]*)", re.I)

_STOP_IDENTIFIERS = {
    "a", "an", "and", "are", "b", "field", "how", "in", "is", "of", "or",
    "table", "the", "to", "why", "where", "what",
}


class QuestionUnderstandingService:
    """Extract only hints explicitly present in the user's question.

    This service intentionally has no database or tool dependency.  Class,
    method and table candidates discovered later belong to retrieval, not to
    question understanding.
    """

    def understand(self, question: str) -> QuestionUnderstanding:
        text = (question or "").strip()
        if not text:
            raise ValueError("question must not be empty")

        identifiers = _unique(match.group(0) for match in _IDENTIFIER.finditer(text))
        identifiers = [item for item in identifiers if item.lower() not in _STOP_IDENTIFIERS]
        processes = _unique(_clean_entity(match.group(1)) for match in _PROCESS.finditer(text))
        systems = _unique(_clean_entity(match.group(1)) for match in _SYSTEM.finditer(text))
        labelled_fields = _unique(_clean_label(match.group(1)) for match in _FIELD_LABEL.finditer(text))
        labelled_tables = _unique(match.group(1) for match in _TABLE_LABEL.finditer(text))

        explicit_code = [
            item for item in identifiers
            if "." in item or item.endswith(("Service", "Controller", "Mapper", "Repository", "Dao"))
        ]
        table_hints = _unique(labelled_tables + [
            item for item in identifiers
            if "_" in item and item.lower() not in {value.lower() for value in labelled_fields}
        ])
        field_hints = _unique(labelled_fields + [
            item for item in identifiers
            if item not in explicit_code and item not in table_hints
            and ("_" in item or any(char.isupper() for char in item[1:]))
        ])

        quoted = _unique(match.group(1).strip() for match in _QUOTED.finditer(text))
        business_objects = _extract_business_objects(text, processes, systems)
        search_terms = _unique(field_hints + table_hints + code_hints_without_members(explicit_code)
                               + business_objects + processes + systems + quoted)
        if not search_terms:
            search_terms = _question_terms(text)
        return QuestionUnderstanding(
            intent=_classify_intent(text),
            business_objects=business_objects,
            processes=processes,
            systems=systems,
            field_hints=field_hints,
            table_hints=table_hints,
            code_hints=explicit_code,
            search_terms=search_terms,
        )


def _classify_intent(text: str) -> QueryIntent:
    if re.search(r"为什么|为何|原因|依据|规则来源|why\b", text, re.I):
        return QueryIntent.RULE_REASON
    if re.search(r"之间.{0,10}(?:关系|联系)|跨(?:流程|系统|服务)|(?:流程|阶段).{0,24}(?:和|与|到|→|->).{0,24}(?:流程|阶段)", text):
        return QueryIntent.CROSS_PROCESS
    if re.search(r"字段|列\b|在哪里(?:生成|修改|校验|使用)|来源|流转|写入|读取|消费|producer|consumer|data\s*trace", text, re.I):
        return QueryIntent.DATA_TRACE
    return QueryIntent.BUSINESS_LOGIC


def _extract_business_objects(text: str, processes: list[str], systems: list[str]) -> list[str]:
    values: list[str] = []
    # Chinese nouns explicitly attached to common business-object markers.
    pattern = re.compile(r"([\u4e00-\u9fff]{1,16})(?:功能|业务|订单|申请|合同|项目|提款|还款)")
    for match in pattern.finditer(text):
        value = match.group(0)
        if not any(value in item or item in value for item in processes + systems):
            values.append(value)
    return _unique(values)


def _question_terms(text: str) -> list[str]:
    """Conservative fallback: terms are copied from the question itself."""
    preferred = ("申请", "提款", "还款", "校验", "拒绝", "规则", "需求", "流程", "阶段", "关系")
    values = [item for item in preferred if item in text]
    for clause in re.findall(r"[\u4e00-\u9fff]{2,32}", text):
        cleaned = clause
        for phrase in (
            "请问", "是什么", "什么意思", "怎么理解", "有没有", "是否", "如何", "哪里",
            "由什么代码实现", "什么代码实现", "代码实现", "对应代码", "在哪", "为什么",
        ):
            cleaned = cleaned.replace(phrase, "")
        cleaned = cleaned.strip()
        if 2 <= len(cleaned) <= 16:
            values.append(cleaned)
    values.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", text))
    return _unique(values)


def code_hints_without_members(values: list[str]) -> list[str]:
    return _unique(value.split(".", 1)[0] for value in values)


def _clean_label(value: str) -> str:
    value = value.strip(" `\"'“”‘’，,。？?：:")
    return "" if value in {"该", "此", "这个", "那个", "哪个", "什么"} else value


def _clean_entity(value: str) -> str:
    # Remove a clause prefix captured from unsegmented Chinese while preserving
    # the explicit suffix-bearing phrase itself (for example, “申请阶段”).
    parts = re.split(r"[，。？?、；;：:\s]|(?:为什么|如何|哪里|什么|以及|或者|和|与|到|从|由|在|的)", value)
    return next((item for item in reversed(parts) if item), value)


def _unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result

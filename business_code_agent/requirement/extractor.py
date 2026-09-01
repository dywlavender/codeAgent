from __future__ import annotations

import re
from collections.abc import Callable

from ..util import camel_fields
from .models import BusinessRule, RequirementDigest, RequirementRelationType


class RequirementDigestExtractor:
    def __init__(self, structured_extractor: Callable[[dict], dict] | None = None):
        self.structured_extractor = structured_extractor

    def extract(self, requirement_id: str, title: str, chunks) -> RequirementDigest:
        if self.structured_extractor:
            return self._validated(requirement_id, title, chunks, self.structured_extractor({"title": title, "chunks": [chunk.content for chunk in chunks]}))
        text = "\n".join(chunk.content for chunk in chunks)
        business_objects, processes, systems, business_keywords = _business_terms(text)
        fields = camel_fields(text)
        qualified_columns = re.findall(
            r"(?<![A-Za-z0-9_])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\.([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![A-Za-z0-9_])",
            text,
        )
        fields.extend(column for _, column in qualified_columns)
        table_names = {table for table, _ in qualified_columns}
        column_names = {column for _, column in qualified_columns}
        fields.extend(value for value in _snake_fields(text) if value in column_names)
        tables = list(dict.fromkeys([
            *[table for table, _ in qualified_columns],
            *re.findall(r"(?:表|table)\s*[`'\"]?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)", text, re.IGNORECASE),
        ]))
        interfaces = list(dict.fromkeys(re.findall(r"(?:GET|POST|PUT|DELETE|PATCH)\s+/[\w/{}/.-]+", text, re.IGNORECASE)))
        rule_sentences = _rule_sentences(text)
        rules = []
        for index, statement in enumerate(rule_sentences, 1):
            matching = [chunk.id for chunk in chunks if statement in chunk.content]
            conditions = [
                value for _, value in re.findall(r"(如果|若)([^，。；]+)", statement)
            ]
            conditions.extend(
                match.group(1) for match in re.finditer(r"当(?!前)([^，。；]+)", statement)
            )
            result = re.split(r"(?:则|需要|必须|应当|应)", statement, maxsplit=1)[-1].strip()
            rules.append(BusinessRule(f"{requirement_id}-RR-{index:03d}", statement, _objects(statement), conditions, result, matching))
        goal = _section_text(chunks, ("目标", "目的"))
        background = _section_text(chunks, ("背景", "现状"))
        exceptions = [sentence for sentence in _sentences(text) if any(word in sentence for word in ("异常", "失败", "拒绝"))]
        compatibility = [sentence for sentence in _sentences(text) if any(word in sentence for word in ("兼容", "存量", "历史数据"))]
        unknowns = []
        if not rules:
            unknowns.append("未从原文识别出明确业务规则")
        if not fields:
            unknowns.append("原文未明确给出代码字段名")
        keywords = list(dict.fromkeys([*business_keywords, *fields, *tables, *interfaces]))[:20]
        return RequirementDigest(
            requirement_id, title, goal, background, business_objects,
            processes, systems, rules,
            list(dict.fromkeys(condition for rule in rules for condition in rule.conditions)),
            [sentence for sentence in _sentences(text) if any(word in sentence for word in ("变更为", "状态", "从", "改为"))],
            interfaces, list(dict.fromkeys(fields)), tables, exceptions, compatibility,
            unknowns, keywords, "DETERMINISTIC_BOUNDED",
        )

    def _validated(self, requirement_id: str, title: str, chunks, payload: dict) -> RequirementDigest:
        allowed = {item.value for item in RequirementRelationType}
        if "relation_type" in payload and payload["relation_type"] not in allowed:
            raise ValueError("structured extractor returned unsupported relation_type")
        rules = []
        for index, raw in enumerate(payload.get("business_rules", []), 1):
            statement = str(raw.get("statement", "")).strip()
            if not statement or not any(statement in chunk.content for chunk in chunks):
                raise ValueError("structured extractor invented a business rule absent from source chunks")
            evidence_ids = [chunk.id for chunk in chunks if statement in chunk.content]
            rules.append(BusinessRule(str(raw.get("id") or f"{requirement_id}-RR-{index:03d}"), statement, _strings(raw.get("business_objects")), _strings(raw.get("conditions")), str(raw.get("result", "")), evidence_ids))
        return RequirementDigest(
            requirement_id, str(payload.get("title") or title), str(payload.get("business_goal", "")),
            str(payload.get("background", "")), _strings(payload.get("business_objects")),
            _strings(payload.get("affected_processes")), _strings(payload.get("affected_systems")), rules,
            _strings(payload.get("conditions")), _strings(payload.get("status_changes")),
            _strings(payload.get("interfaces")), _strings(payload.get("fields")), _strings(payload.get("tables")),
            _strings(payload.get("exceptions")), _strings(payload.get("compatibility_rules")),
            _strings(payload.get("unknowns")), _strings(payload.get("keywords")), "MODEL_STRUCTURED",
        )


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[。！？\n]+", text) if item.strip()]


def _rule_sentences(text: str) -> list[str]:
    markers = ("必须", "需要", "应当", "不得", "只能", "校验", "确定", "生成", "同步", "拒绝")
    return list(dict.fromkeys(sentence for sentence in _sentences(text) if any(word in sentence for word in markers)))


def _objects(text: str) -> list[str]:
    return _business_terms(text)[0]


def _business_terms(text: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bounded lexical extraction used by the requirement digest.

    This intentionally returns lightweight hints, not a second business
    knowledge record.  Requirement parsing only supplies candidate terms for
    later relation matching; the canonical business baseline remains the
    single business-knowledge source.
    """
    objects: list[str] = []
    for value in ("项目编号", "订单编号", "申请编号", "合同编号", "客户编号", "流水号"):
        if value in text:
            objects.append(value)
            break
    for noun in ("项目", "提款", "申请", "订单", "合同", "客户", "还款方式", "账户"):
        if noun in text:
            objects.append(noun)
    processes: list[str] = []
    for value in ("提款申请", "提款结果处理", "申请处理", "订单创建", "结果处理", "核心同步", "同步核心", "校验流程"):
        if value in text:
            processes.append(value)
    if "同步" in text:
        processes.append("同步")
    if "校验" in text:
        processes.append("校验流程")
    systems = [value for value in ("中台", "核心") if value in text]
    systems.extend(match for match in re.findall(r"([一-鿿A-Za-z]{1,12}系统)", text) if match not in systems)
    keywords = list(dict.fromkeys([*objects, *processes, *systems, *camel_fields(text)]))
    return (_unique(objects), _unique(processes), _unique(systems), keywords)


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _snake_fields(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])", text)


def _section_text(chunks, hints: tuple[str, ...]) -> str:
    values = [chunk.content for chunk in chunks if any(hint in " / ".join(chunk.section_path) for hint in hints)]
    return "\n".join(values)[:2000]


def _strings(value) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]

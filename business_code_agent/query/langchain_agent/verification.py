"""Source-bound answer contract and limited, deterministic consistency rules.

Matching a quotation is not a proof of its interpretation. The rules below
check returned data, projection and UI scopes, not arbitrary business semantics.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence import EvidenceCatalog


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref(Record):
    source: str
    start: int
    end: int
    quote: str = ""


class Resource(Record):
    id: str
    label: str
    scope: Literal["api_data", "display", "ui", "business", "requirement", "context"]


class Fact(Record):
    id: str
    resource: str
    kind: Literal[
        "returned_field", "mapping", "behavior", "ui", "requirement", "context"
    ]
    field: str = ""
    inputs: list[str] = Field(default_factory=list)
    output: str = ""
    statement: str = ""
    refs: list[Ref]


class Answer(Record):
    resource: str
    kind: Literal["data_fields", "display", "ui", "behavior", "requirement", "context"]
    required_fields: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "unsupported", "unknown"] = "unknown"
    summary: str = ""


class Recommendation(Record):
    resource: str
    change: str
    benefit: str
    facts: list[str]
    adds_fields: list[str] = Field(default_factory=list)
    unverified_impacts: list[str] = Field(default_factory=list)


class Packet(Record):
    resources: list[Resource]
    facts: list[Fact]
    answers: list[Answer]
    recommendations: list[Recommendation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


FACT_SCOPE = {
    "returned_field": "api_data",
    "mapping": "display",
    "behavior": "business",
    "ui": "ui",
    "requirement": "requirement",
    "context": "context",
}
ANSWER_SCOPE = {
    "data_fields": "api_data",
    "display": "display",
    "behavior": "business",
    "ui": "ui",
    "requirement": "requirement",
    "context": "context",
}
ANSWER_FACT = {
    "data_fields": "returned_field",
    "display": "mapping",
    "behavior": "behavior",
    "ui": "ui",
    "requirement": "requirement",
    "context": "context",
}
CODE_FACTS = {"returned_field", "mapping", "behavior", "ui"}
SCOPE_LABELS = {
    "api_data": "接口返回数据",
    "display": "展示字段",
    "ui": "页面实际使用",
    "business": "业务行为",
    "requirement": "需求目标",
    "context": "业务约定与背景",
}


def decode_packet(text):
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    return Packet.model_validate(json.loads(body))


def _field(value):
    return re.sub(r"\[\d*\]", "[]", value.strip().removeprefix("$."))


def _mentions(quote, token):
    return bool(re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", quote))


def check_and_render(packet: Packet, catalog: EvidenceCatalog):
    issues = []
    resources = {r.id: r for r in packet.resources}
    facts = {}
    if len(resources) != len(packet.resources):
        return "未能完成事实核对：对象标识重复，当前结论未获确认。", {
            "status": "invalid",
            "issues": ["duplicate_resource"],
        }
    for fact in packet.facts:
        resource = resources.get(fact.resource)
        resolved = [catalog.resolve(ref) for ref in fact.refs]
        reason = None
        if fact.id in facts:
            reason = "duplicate_fact"
        elif resource is None or resource.scope != FACT_SCOPE[fact.kind]:
            reason = "fact_scope"
        elif not resolved or any(s is None for s in resolved):
            reason = "source_quote"
        elif fact.kind in CODE_FACTS and any(s.kind != "code" for s in resolved):
            reason = "implementation_source"
        elif fact.kind == "requirement" and any(
            s.kind != "requirement" for s in resolved
        ):
            reason = "requirement_source"
        elif fact.kind == "context" and any(
            s.kind not in {"business_context", "project_document", "code"}
            for s in resolved
        ):
            reason = "context_source"
        elif fact.kind == "returned_field":
            # All path segments must be grounded in literal delivered excerpts.
            # This still does not prove a cross-method or cross-system binding.
            tokens = [x for x in _field(fact.field).replace("[]", "").split(".") if x]
            quote = "\n".join(catalog.excerpt(r) for r in fact.refs)
            if not tokens or not all(_mentions(quote, t) for t in tokens):
                reason = "field_quote"
        elif fact.kind == "mapping":
            quote = "\n".join(catalog.excerpt(r) for r in fact.refs)
            if (
                not fact.field
                or not fact.inputs
                or not fact.output
                or not all(
                    _mentions(quote, t) for t in [fact.field, *fact.inputs, fact.output]
                )
            ):
                reason = "mapping_quote"
        elif not fact.statement.strip():
            reason = "empty_statement"
        if reason:
            issues.append({"code": reason, "fact": fact.id})
        else:
            facts[fact.id] = fact

    rendered = []
    decisions = []

    def citations(values):
        return " ".join(
            dict.fromkeys(catalog.citation(r) for f in values for r in f.refs)
        )

    for answer in packet.answers:
        resource = resources.get(answer.resource)
        if not resource or resource.scope != ANSWER_SCOPE[answer.kind]:
            issues.append({"code": "answer_scope", "resource": answer.resource})
            continue
        selected = [
            facts[i]
            for i in answer.facts
            if i in facts
            and facts[i].resource == resource.id
            and facts[i].kind == ANSWER_FACT[answer.kind]
        ]
        verdict = "unknown"
        text = ""
        if answer.kind == "data_fields":
            # Do not allow the answer model to hide an already-observed field
            # by selecting only projection facts or dropping a positive fact ID.
            selected = [
                f
                for f in facts.values()
                if f.resource == resource.id and f.kind == "returned_field"
            ]
            observed = {_field(f.field): f for f in selected}
            needed = list(
                dict.fromkeys(_field(p) for p in answer.required_fields if p.strip())
            )
            present = [p for p in needed if p in observed]
            unknown = [p for p in needed if p not in observed]
            if needed and not unknown:
                verdict = "supported"
                text = (
                    "返回数据中已包含："
                    + "、".join(f"`{p}`" for p in present)
                    + "。这些数据可供进一步判断，不能据此认定页面已经使用它们。"
                )
            else:
                text = (
                    ("已确认返回：" + "、".join(f"`{p}`" for p in present) + "。")
                    if present
                    else ""
                )
                text += (
                    "尚未确认："
                    + (
                        "、".join(f"`{p}`" for p in unknown)
                        if unknown
                        else "回答所需的具体返回字段"
                    )
                    + "；未确认不能当作接口没有这些数据。"
                )
            if answer.verdict != verdict:
                issues.append(
                    {
                        "code": "availability_conflict",
                        "resource": resource.id,
                        "proposed": answer.verdict,
                        "derived": verdict,
                    }
                )
            decisions.append(
                {
                    "resource": resource.id,
                    "kind": answer.kind,
                    "verdict": verdict,
                    "present": present,
                    "unconfirmed": unknown,
                }
            )
        elif answer.kind == "display" and selected:
            collapsed = [f for f in selected if len(set(f.inputs)) > 1]
            if collapsed:
                text = (
                    "；".join(
                        f"`{f.field}` 将 "
                        + "、".join(f"`{v}`" for v in f.inputs)
                        + f" 合并为 `{f.output}`"
                        for f in collapsed
                    )
                    + "。单看该展示字段不能细分这些输入状态。"
                )
                verdict = "supported"
            else:
                text = "已读取相关映射，但尚未确认它是否合并了本题需要区分的状态。"
        elif answer.kind == "ui" and selected:
            # A model-assigned 'ui' tag and a valid code reference do not prove
            # that the page consumes the returned object. No UI binding checker
            # is implemented yet; do not promote backend code into a UI claim.
            text = "已取得代码引用，但页面请求与字段消费关系仍需独立核对。"
            issues.append({"code": "ui_usage_unverified", "resource": resource.id})
        elif selected:
            # Business interpretation remains model-authored; only references
            # and scope are checked. Do not advertise it as a semantic proof.
            text = "原文解读：" + "；".join(f.statement for f in selected)
            verdict = "referenced"
        else:
            text = "当前未取得可支撑这一层结论的原文，尚不能确认。"
            if answer.facts:
                issues.append(
                    {"code": "insufficient_scope_evidence", "resource": resource.id}
                )
        # Entity labels are model-authored too; do not let a negative headline
        # bypass a corrected data availability decision.
        rendered.append(f"**{SCOPE_LABELS[resource.scope]}**：{text} " + citations(selected))
        if answer.kind != "data_fields":
            decisions.append(
                {"resource": resource.id, "kind": answer.kind, "verdict": verdict}
            )

    for recommendation in packet.recommendations:
        resource = resources.get(recommendation.resource)
        basis = [facts[i] for i in dict.fromkeys(recommendation.facts) if i in facts]
        if not resource or not basis or len(basis) != len(set(recommendation.facts)):
            issues.append({"code": "recommendation_basis"})
            continue
        observed = {
            _field(f.field)
            for f in facts.values()
            if f.resource == resource.id and f.kind == "returned_field"
        }
        existing = [p for p in recommendation.adds_fields if _field(p) in observed]
        if existing:
            issues.append({"code": "field_already_returned", "fields": existing})
            rendered.append(
                "建议先利用已返回的 "
                + "、".join(f"`{p}`" for p in existing)
                + "，不能把它们当作接口缺失字段。"
            )
            continue
        text = f"建议：{recommendation.change}；预期收益：{recommendation.benefit}。"
        if recommendation.unverified_impacts:
            text += "尚需核对：" + "；".join(recommendation.unverified_impacts) + "。"
        else:
            text += "改动影响尚未验证。"
        rendered.append(text + " " + citations(basis))
    if packet.gaps:
        rendered.append("尚未确认：" + "；".join(packet.gaps))
    if not rendered:
        rendered = ["当前已读取的资料未形成可核对的结论，不能据此断言功能缺失。"]
    return "\n\n".join(rendered), {
        "status": ("checked_with_issues" if issues else "checked") if facts else "insufficient",
        "issues": issues,
        "acceptedFacts": [f.model_dump() for f in facts.values()],
        "decisions": decisions,
        "semanticProof": False,
        "sourceCount": len(catalog.sources),
    }


def verify_response(text, catalog):
    try:
        packet = decode_packet(text)
    except (ValueError, ValidationError):
        positions = "、".join(f"`{s.path}:{min(s.lines)}`" for s in catalog.sources[:6])
        return "已读取 " + (
            positions or "本轮资料"
        ) + "，但未形成符合事实核对格式的结论；当前不能确认所问能力。", {
            "status": "invalid",
            "issues": [{"code": "answer_protocol"}],
            "semanticProof": False,
        }
    answer, audit = check_and_render(packet, catalog)
    audit["packet"] = packet.model_dump()
    return answer, audit

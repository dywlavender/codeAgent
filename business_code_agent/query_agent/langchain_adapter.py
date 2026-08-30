from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..knowledge_update.langchain_adapter import ModelConfig, init_configured_chat_model
from .models import QueryIntent, QuestionUnderstanding


class QueryModelInvocationError(RuntimeError):
    """A model-backed query stage failed and should use the deterministic path."""


@dataclass(frozen=True)
class QueryModelResult:
    value: Any
    mode: str = "MODEL"


class LangChainQueryAnalyzer:
    """LangChain 1.2 structured question understanding.

    The model may add semantic hints, but retrieval remains bounded by the
    deterministic retriever and the evidence evaluator.
    """

    def __init__(self, model: Any, *, agent_factory: Callable[..., Any] | None = None, tools=()):
        self.model = model
        self._agent_factory = agent_factory
        self.tools = list(tools)

    @classmethod
    def from_config(cls, value: Mapping[str, Any], *, model_factory=init_configured_chat_model, agent_factory=None):
        return cls(model_factory(ModelConfig.from_mapping(value)), agent_factory=agent_factory)

    def bind_tools(self, tools):
        self.tools = list(tools or [])
        return self

    def understand(self, question: str, history: Sequence[Mapping[str, Any]] = ()) -> QuestionUnderstanding:
        schema = _understanding_schema()
        agent = self._build(schema, "你是代码业务问答 Agent 的问题理解节点。只提取问题中的意图、实体和检索提示，不回答问题，不编造项目事实。")
        context = {"question": question, "recent_conversation": list(history)[-4:]}
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}]})
            structured = result.get("structured_response") if isinstance(result, Mapping) else None
            if structured is None:
                raise ValueError("query model did not return structured_response")
            payload = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
            return _understanding_from_payload(payload, question)
        except Exception as exc:
            raise QueryModelInvocationError("query understanding failed") from exc

    def _build(self, schema, system_prompt):
        factory = self._agent_factory
        if factory is None:
            from langchain.agents import create_agent
            factory = create_agent
        try:
            return factory(model=self.model, tools=self.tools, response_format=schema, system_prompt=system_prompt)
        except Exception as exc:
            raise QueryModelInvocationError("query agent construction failed") from exc


class LangChainQueryComposer:
    """Structured answer synthesis over already verified facts only."""

    def __init__(self, model: Any, *, agent_factory: Callable[..., Any] | None = None):
        self.model = model
        self._agent_factory = agent_factory

    def compose(self, question: str, *, evidence_status: str, facts: list[dict], unknowns: list[str], conflicts: list[dict]) -> dict:
        if evidence_status != "SUFFICIENT" or conflicts or not facts:
            raise QueryModelInvocationError("answer synthesis requires conflict-free sufficient evidence")
        schema = _answer_schema()
        analyzer = LangChainQueryAnalyzer(self.model, agent_factory=self._agent_factory)
        agent = analyzer._build(schema, """你是业务代码问答 Agent 的回答整理节点。
只能根据输入的 verified_facts 组织回答。每条 claim 优先只填写 verified_facts 中对应事实的
fact_indices；不要改写或扩展事实。若填写 statement 或 evidence_ids，它们也必须完全受所选事实支持。
conclusion 只能组合 claims 中已有事实，不能改变肯定/否定、允许/禁止、成功/失败等语义。
不能新增业务事实、代码行为、需求结论或未提供的证据。""")
        catalog = [{"index": index, **fact} for index, fact in enumerate(facts)]
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": json.dumps({
                "question": question, "verified_facts": catalog, "unknowns": unknowns, "conflicts": conflicts,
            }, ensure_ascii=False)}]})
            structured = result.get("structured_response") if isinstance(result, Mapping) else None
            if structured is None:
                raise ValueError("query model did not return structured_response")
            payload = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
            return _validate_composed(payload, facts)
        except QueryModelInvocationError:
            raise
        except Exception as exc:
            raise QueryModelInvocationError("answer synthesis failed") from exc


def _understanding_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class Understanding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        intent: str
        business_objects: list[str] = Field(default_factory=list)
        processes: list[str] = Field(default_factory=list)
        systems: list[str] = Field(default_factory=list)
        field_hints: list[str] = Field(default_factory=list)
        table_hints: list[str] = Field(default_factory=list)
        code_hints: list[str] = Field(default_factory=list)
        search_terms: list[str] = Field(default_factory=list)

    return Understanding


def _answer_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class Claim(BaseModel):
        model_config = ConfigDict(extra="forbid")
        statement: str = ""
        evidence_ids: list[str] = Field(default_factory=list)
        fact_indices: list[int] = Field(default_factory=list)

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        conclusion: str
        claims: list[Claim] = Field(default_factory=list)
        suggested_follow_ups: list[str] = Field(default_factory=list)

    return Answer


def _understanding_from_payload(payload: Any, question: str) -> QuestionUnderstanding:
    if not isinstance(payload, Mapping):
        raise ValueError("query understanding response is invalid")
    intent = QueryIntent(str(payload.get("intent", "BUSINESS_LOGIC")).upper())
    values = {}
    for key in ("business_objects", "processes", "systems", "field_hints", "table_hints", "code_hints", "search_terms"):
        raw = payload.get(key, [])
        values[key] = list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))[:12]
    if not values["search_terms"]:
        values["search_terms"] = [question[:120]]
    return QuestionUnderstanding(intent=intent, **values)


def _validate_composed(payload: Any, facts: list[dict]) -> dict:
    if not isinstance(payload, Mapping) or not str(payload.get("conclusion", "")).strip():
        raise ValueError("query synthesis response is invalid")
    evidence_to_statements: dict[str, list[str]] = {}
    for fact in facts:
        for evidence_id in fact.get("evidenceIds", []):
            evidence_to_statements.setdefault(evidence_id, []).append(str(fact.get("statement", "")))
    claims = []
    for raw in payload.get("claims", []):
        if not isinstance(raw, Mapping):
            raise ValueError("query claim is invalid")
        indices = _fact_indices(raw)
        if indices:
            if any(index < 0 or index >= len(facts) for index in indices):
                raise ValueError("query claim has an invalid fact index")
            selected = [facts[index] for index in indices]
            statement = "；".join(str(item.get("statement", "")).strip() for item in selected if str(item.get("statement", "")).strip())
            evidence_ids = list(dict.fromkeys(
                str(evidence_id)
                for item in selected
                for evidence_id in item.get("evidenceIds", [])
                if str(evidence_id).strip()
            ))
            if not statement or not evidence_ids:
                raise ValueError("query claim selected an empty fact")
            # A model may still return a prose statement alongside indices.
            # Treat it as an assertion to validate, never as the source of the
            # durable claim text.  The persisted claim is reconstructed from
            # the selected verified facts above.
            supplied_statement = str(raw.get("statement", "")).strip()
            if supplied_statement and not all(
                _supported_by_fact(supplied_statement, str(item.get("statement", "")))
                for item in selected
            ):
                raise ValueError("query claim is not supported by its selected facts")
            supplied_ids = _evidence_ids(raw)
            if supplied_ids and not set(supplied_ids).issubset(set(evidence_ids)):
                raise ValueError("query claim references evidence outside its selected facts")
            claims.append({"statement": statement, "evidenceIds": evidence_ids, "factIndices": indices})
            continue

        statement = str(raw.get("statement", "")).strip()
        evidence_ids = _evidence_ids(raw)
        if not statement or not evidence_ids or any(item not in evidence_to_statements for item in evidence_ids):
            raise ValueError("query claim has an invalid evidence reference")
        for evidence_id in evidence_ids:
            if not any(_supported_by_fact(statement, fact_statement) for fact_statement in evidence_to_statements[evidence_id]):
                raise ValueError("query claim is not supported by its evidence")
        claims.append({"statement": statement, "evidenceIds": evidence_ids})
    if not claims:
        raise ValueError("query synthesis must return claims")
    conclusion = str(payload["conclusion"]).strip()
    if not any(_supported_by_fact(conclusion, item["statement"]) for item in claims):
        raise ValueError("query conclusion is not supported by its claims")
    return {
        "conclusion": conclusion,
        "claims": claims,
        "suggestedFollowUps": list(dict.fromkeys(str(item).strip() for item in payload.get("suggested_follow_ups", payload.get("suggestedFollowUps", [])) if str(item).strip()))[:4],
    }


def _fact_indices(value: Mapping[str, Any]) -> list[int]:
    raw = value.get("fact_indices", value.get("factIndexes", value.get("factIndices", [])))
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    result = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            raise ValueError("query claim fact_indices must be integers")
    return list(dict.fromkeys(result))


def _evidence_ids(value: Mapping[str, Any]) -> list[str]:
    raw = value.get("evidence_ids", value.get("evidenceIds", [])) or []
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _supported_by_fact(statement: str, fact: str) -> bool:
    """Check lexical support while explicitly rejecting polarity reversals."""
    statement = str(statement or "").strip()
    fact = str(fact or "").strip()
    if not statement or not fact:
        return False
    statement_terms = _terms(statement)
    fact_terms = _terms(fact)
    common = statement_terms.intersection(fact_terms)
    if not common:
        return False
    statement_polarity = _polarity(statement)
    fact_polarity = _polarity(fact)
    if statement_polarity and fact_polarity and statement_polarity != fact_polarity:
        return False
    # An identifier is a strong anchor.  For natural-language Chinese facts,
    # require at least two shared bigrams/terms so a generic verb cannot carry
    # an unrelated claim by itself.
    if set(_identifiers(statement)).intersection(_identifiers(fact)):
        return True
    return len(common) >= 2 or _compact(statement) in _compact(fact) or _compact(fact) in _compact(statement)


_NEGATIVE_CUES = (
    "不允许", "不可以", "不可", "不能", "不得", "禁止", "不应", "无需", "不需", "不会",
    "不再", "没有", "不存在", "不是", "未", "非", "无", "拒绝", "失败", "not", "never", "without", "no",
)
_POSITIVE_CUES = ("允许", "可以", "能", "应当", "需要", "必须", "会", "是", "成功", "通过")


def _polarity(value: str) -> int:
    lowered = str(value or "").casefold()
    negative = any(cue.casefold() in lowered for cue in _NEGATIVE_CUES)
    # Remove negative phrases before checking positive cues: ``不允许`` must
    # remain negative rather than matching the substring ``允许``.
    for cue in _NEGATIVE_CUES:
        lowered = lowered.replace(cue.casefold(), "")
    positive = any(cue.casefold() in lowered for cue in _POSITIVE_CUES)
    if negative and not positive:
        return -1
    if positive and not negative:
        return 1
    return 0


def _identifiers(value: str) -> set[str]:
    import re
    return {item.casefold() for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", value or "")}


def _compact(value: str) -> str:
    import re
    return re.sub(r"\s+", "", str(value or "").casefold())


def _terms(value: str) -> set[str]:
    import re
    terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", value.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms

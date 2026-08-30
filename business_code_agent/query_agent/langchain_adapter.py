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
        agent = analyzer._build(schema, """你是业务代码问答 Agent 的回答编排节点。
你只能选择和排序输入的 verified_facts，不能撰写事实文本。
每条 claim 必须填写至少一个 fact_indices；conclusion_fact_indices 必须从 claims 已选事实中取值，
并按希望展示的顺序排列。不要返回 statement、evidence_ids 或自由文本 conclusion。
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
        fact_indices: list[int] = Field(min_length=1)

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        claims: list[Claim] = Field(min_length=1)
        conclusion_fact_indices: list[int] = Field(min_length=1)
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
    if not isinstance(payload, Mapping):
        raise ValueError("query synthesis response is invalid")
    claims = []
    selected_indices: set[int] = set()
    for raw in payload.get("claims", []):
        if not isinstance(raw, Mapping):
            raise ValueError("query claim is invalid")
        indices = _fact_indices(raw, required=True)
        selected = [_fact_at(facts, index) for index in indices]
        claims.append({
            "statement": "；".join(item["statement"] for item in selected),
            "evidenceIds": list(dict.fromkeys(
                evidence_id for item in selected for evidence_id in item["evidenceIds"]
            )),
            "factIndices": indices,
        })
        selected_indices.update(indices)
    if not claims:
        raise ValueError("query synthesis must return claims")
    conclusion_indices = _fact_indices({
        "fact_indices": payload.get("conclusion_fact_indices", payload.get("conclusionFactIndices")),
    }, required=True)
    if any(index not in selected_indices for index in conclusion_indices):
        raise ValueError("query conclusion references a fact outside its claims")
    conclusion = "；".join(_fact_at(facts, index)["statement"] for index in conclusion_indices)
    if not conclusion.endswith(("。", "！", "？", ".", "!", "?")):
        conclusion += "。"
    return {
        "conclusion": conclusion,
        "claims": claims,
        "conclusionFactIndices": conclusion_indices,
        "suggestedFollowUps": list(dict.fromkeys(str(item).strip() for item in payload.get("suggested_follow_ups", payload.get("suggestedFollowUps", [])) if str(item).strip()))[:4],
    }


def _fact_indices(value: Mapping[str, Any], *, required: bool = False) -> list[int]:
    raw = value.get("fact_indices", value.get("factIndexes", value.get("factIndices", [])))
    if raw is None:
        raw = []
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    result = []
    for item in raw:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            raise ValueError("query claim fact_indices must be integers")
    result = list(dict.fromkeys(result))
    if required and not result:
        raise ValueError("query synthesis requires fact_indices")
    return result


def _fact_at(facts: list[dict], index: int) -> dict[str, Any]:
    if index < 0 or index >= len(facts):
        raise ValueError("query claim has an invalid fact index")
    fact = facts[index]
    statement = str(fact.get("statement", "")).strip()
    evidence_ids = list(dict.fromkeys(
        str(item).strip() for item in fact.get("evidenceIds", []) if str(item).strip()
    ))
    if not statement or not evidence_ids:
        raise ValueError("query claim selected an empty fact")
    return {"statement": statement, "evidenceIds": evidence_ids}

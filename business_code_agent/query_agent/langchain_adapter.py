from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..knowledge_update.langchain_adapter import ModelConfig, init_configured_chat_model
from .models import QueryIntent, QuestionUnderstanding
from .understanding import QuestionUnderstandingService


class QueryModelInvocationError(RuntimeError):
    """A model-backed query stage failed."""


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
    """Model adapter used by both the legacy and source-led query paths.

    ``compose`` remains available for historical callers that already have a
    verified-fact catalog.  New query runs call ``investigate`` instead: the
    model receives navigation context, can call source tools, and writes an
    answer whose claims cite references created by those reads.
    """

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

    def investigate(
        self,
        question: str,
        *,
        context: Mapping[str, Any] | None = None,
        tools=(),
        ledger=None,
        max_tool_calls: int = 48,
    ) -> dict:
        """Run the autonomous source investigation loop.

        LangChain's agent owns the loop: a tool result is fed back to the
        model, which may search, read another symbol, or finish.  The adapter
        validates only the citation boundary (every cited reference must have
        been returned by a tool in this run); it does not attempt to interpret
        the semantics of the source code itself.
        """
        schema = _investigation_schema()
        analyzer = LangChainQueryAnalyzer(self.model, agent_factory=self._agent_factory, tools=tools)
        agent = analyzer._build(schema, _INVESTIGATION_PROMPT)
        payload = {
            "question": question,
            "navigation_context": context or {},
            "constraints": {
                "source_is_final_authority": True,
                "code_claims_require_read_source_reference": True,
                "do_not_complete_a_call_chain_unless_question_requires_it": True,
                "max_tool_calls": max(1, int(max_tool_calls)),
            },
        }
        try:
            result = _invoke_with_recursion_limit(agent, payload, max_tool_calls)
            structured = result.get("structured_response") if isinstance(result, Mapping) else None
            if structured is None:
                raise ValueError("query investigator did not return structured_response")
            value = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
            return _validate_investigation(value, ledger=ledger)
        except QueryModelInvocationError:
            raise
        except Exception as exc:
            raise QueryModelInvocationError("source investigation failed") from exc


class LangChainQueryInvestigator(LangChainQueryComposer):
    """Named adapter for the source-led query path.

    It intentionally inherits the legacy ``compose`` compatibility method so
    deployments can roll forward without a database migration; new services
    use only :meth:`investigate`.
    """

    pass


def _understanding_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class Understanding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        intent: str = Field(
            description=(
                "问题意图，只能填写 BUSINESS_LOGIC、DATA_TRACE、RULE_REASON 或 "
                "CROSS_PROCESS；不要填写自然语言描述。"
            )
        )
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


_INVESTIGATION_PROMPT = """你是一个基于真实代码仓库调查业务问题的 Code Agent。

你的目标不是复述索引中的调用链，而是回答用户真正提出的业务或技术问题。
业务知识用于理解术语和定位入口；代码索引用于找到候选类、方法和接口；最终代码结论必须以本次通过 read_source 实际读取的源码为依据。

调查循环：
1. 先判断用户真正想知道的是链路、逻辑、条件、计算、状态、数据来源还是跨系统关系；
2. 优先利用 navigation_context，也可以调用 search_business 和 search_code 找到入口；
3. 找到候选后必须调用 read_source，不能根据类名、方法名、索引摘要猜测实现；
4. 读到关键方法调用、条件分支、数据来源或跨系统调用时，只在当前问题需要时调用 follow_call、find_references 或 follow_integration；
5. 判断已经足以回答后立即结束，不要为了补齐完整调用链而无休止追踪；
6. 不确定的内容放入 unknowns，不能把未读取的代码写成确定事实；
7. 每一条 claim 至少填写一个 reference_ids。代码 claim 必须引用 read_source 返回的 SRC-* referenceId；业务说明可以引用 search_business 返回的 evidenceId；
8. 结论要直接回答问题，claims 写具体的条件、判断、分支、计算、状态、返回或调用，不要只写“调用了某方法”。

最终只返回结构化结果，不要在结构化字段之外添加自由文本。"""


def _investigation_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class Claim(BaseModel):
        model_config = ConfigDict(extra="forbid")
        statement: str = Field(min_length=1)
        reference_ids: list[str] = Field(default_factory=list)
        source_type: str = ""

    class Investigation(BaseModel):
        model_config = ConfigDict(extra="forbid")
        conclusion: str = Field(min_length=1)
        claims: list[Claim] = Field(default_factory=list)
        answer_type: str = "FULL"
        source_reference_ids: list[str] = Field(default_factory=list)
        business_evidence_ids: list[str] = Field(default_factory=list)
        unknowns: list[str] = Field(default_factory=list)
        conflicts: list[str] = Field(default_factory=list)
        suggested_follow_ups: list[str] = Field(default_factory=list)

    return Investigation


def _invoke_with_recursion_limit(agent, payload: dict[str, Any], max_tool_calls: int):
    """Give real agents a bounded graph budget while keeping tiny fakes usable."""
    config = {"recursion_limit": max(8, min(128, int(max_tool_calls) * 2 + 4))}
    try:
        return agent.invoke({"messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}, config)
    except TypeError as exc:
        # Small test adapters and third-party wrappers often expose only
        # ``invoke(input)``.  Retry that contract without hiding real model
        # errors raised after invocation begins.
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        return agent.invoke({"messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]})


def _validate_investigation(payload: Any, *, ledger=None) -> dict:
    """Validate citations, then return a provider-independent answer payload."""
    if not isinstance(payload, Mapping):
        raise ValueError("query investigation response is invalid")

    read_refs = {}
    business_ids: set[str] = set()
    if ledger is not None:
        read_refs = {
            str(item.get("referenceId")): dict(item)
            for item in ledger.metadata()
            if item.get("referenceId")
        }
        business_ids = set(ledger.business_evidence_ids)
    else:
        # The adapter is also useful in isolation.  Production always passes a
        # ledger; without it we can still reject empty references while the
        # caller owns the persistence boundary.
        read_refs = {
            str(item): {"referenceId": str(item), "sourceType": "CODE"}
            for item in _string_list(payload.get("source_reference_ids", payload.get("sourceReferenceIds", [])))
        }
        business_ids = set(_string_list(payload.get("business_evidence_ids", payload.get("businessEvidenceIds", []))))

    allowed = set(read_refs) | business_ids
    top_source_ids = _string_list(payload.get("source_reference_ids", payload.get("sourceReferenceIds", [])))
    top_business_ids = _string_list(payload.get("business_evidence_ids", payload.get("businessEvidenceIds", [])))
    for reference_id in [*top_source_ids, *top_business_ids]:
        if reference_id not in allowed:
            raise ValueError(f"query investigation cited an unread or unavailable reference: {reference_id}")

    raw_claims = payload.get("claims", []) or []
    if isinstance(raw_claims, Mapping):
        raw_claims = [raw_claims]
    claims: list[dict[str, Any]] = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping):
            raise ValueError("query investigation claim is invalid")
        statement = str(raw.get("statement") or raw.get("claim") or "").strip()
        if not statement:
            raise ValueError("query investigation claim is empty")
        reference_ids = _string_list(raw.get("reference_ids", raw.get("referenceIds", raw.get("evidence_ids", raw.get("evidenceIds", [])))))
        if not reference_ids:
            reference_ids = list(dict.fromkeys([*top_source_ids, *top_business_ids]))
        if not reference_ids:
            raise ValueError("query investigation claim requires reference_ids")
        invalid = [item for item in reference_ids if item not in allowed]
        if invalid:
            raise ValueError(f"query investigation cited an unread or unavailable reference: {invalid[0]}")
        source_type = str(raw.get("source_type", raw.get("sourceType", "")) or "").upper()
        if not source_type:
            source_type = "CODE" if any(item in read_refs for item in reference_ids) else "BUSINESS"
        if source_type == "CODE" and not any(item in read_refs for item in reference_ids):
            raise ValueError("code investigation claim must cite a read_source reference")
        claims.append({
            "statement": statement,
            "referenceIds": reference_ids,
            "sourceType": source_type,
        })

    conclusion = str(payload.get("conclusion") or "").strip()
    if not conclusion:
        raise ValueError("query investigation conclusion is empty")
    unknowns = _string_list(payload.get("unknowns", []))
    conflicts = _string_list(payload.get("conflicts", []))
    answer_type = str(payload.get("answer_type", payload.get("answerType", "")) or "").upper()
    if answer_type not in {"FULL", "PARTIAL", "CONFLICT", "UNKNOWN"}:
        answer_type = "CONFLICT" if conflicts else "PARTIAL" if claims and unknowns else "FULL" if claims else "UNKNOWN"
    if answer_type == "FULL" and not claims:
        answer_type = "UNKNOWN"
    if answer_type in {"FULL", "PARTIAL"} and not claims:
        answer_type = "UNKNOWN"

    return {
        "conclusion": conclusion,
        "claims": claims,
        "answerType": answer_type,
        "sourceReferenceIds": list(dict.fromkeys(top_source_ids)),
        "businessEvidenceIds": list(dict.fromkeys(top_business_ids)),
        "unknowns": unknowns,
        "conflicts": [{"reason": value, "evidenceIds": list(dict.fromkeys([*top_source_ids, *top_business_ids]))} for value in conflicts],
        "suggestedFollowUps": _string_list(payload.get("suggested_follow_ups", payload.get("suggestedFollowUps", [])))[:4],
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        value = [value]
    try:
        values = list(value)
    except TypeError:
        values = [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))[:48]


def _understanding_from_payload(payload: Any, question: str) -> QuestionUnderstanding:
    if not isinstance(payload, Mapping):
        raise ValueError("query understanding response is invalid")
    intent = _coerce_intent(payload.get("intent"), question)
    values = {}
    for key in ("business_objects", "processes", "systems", "field_hints", "table_hints", "code_hints", "search_terms"):
        raw = payload.get(key, [])
        values[key] = list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))[:12]
    if not values["search_terms"]:
        values["search_terms"] = [question[:120]]
    return QuestionUnderstanding(intent=intent, **values)


def _coerce_intent(value: Any, question: str) -> QueryIntent:
    """Keep the model's intent inside the finite query-intent domain.

    The structured response schema is intentionally kept string-compatible so
    providers that return a semantically useful but malformed value do not
    fail the whole understanding stage before this boundary is reached.  A
    valid enum value is used as-is; common short aliases are normalized, and
    a free-form value (for example, a Chinese explanation of the question)
    falls back to the deterministic classifier.
    """
    raw = "" if value is None else str(value).strip()
    normalized = raw.upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "BUSINESS": QueryIntent.BUSINESS_LOGIC,
        "LOGIC": QueryIntent.BUSINESS_LOGIC,
        "DATA": QueryIntent.DATA_TRACE,
        "TRACE": QueryIntent.DATA_TRACE,
        "RULE": QueryIntent.RULE_REASON,
        "REASON": QueryIntent.RULE_REASON,
        "CROSS": QueryIntent.CROSS_PROCESS,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return QueryIntent(normalized or QueryIntent.BUSINESS_LOGIC.value)
    except ValueError:
        # Intent is a retrieval hint, not a fact supplied by the model.  When
        # the model returns a sentence instead of an enum, use the same local
        # classifier as the deterministic understanding path.
        try:
            return QuestionUnderstandingService().understand(question).intent
        except ValueError:
            return QueryIntent.BUSINESS_LOGIC


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

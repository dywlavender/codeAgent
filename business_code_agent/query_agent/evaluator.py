"""Deterministic evidence-sufficiency rules for the four M4 intents."""

from __future__ import annotations

from typing import Iterable

from .models import (
    EvidenceConflict,
    EvidenceGap,
    EvidenceRole,
    EvidenceStatus,
    QueryAgentState,
    QueryIntent,
    SourceType,
    StructuredFact,
    SufficiencyResult,
)


class EvidenceSufficiencyEvaluator:
    """Answer “what proof is still missing?”, never “what else can be searched?”.

    Iteration limits deliberately do not live here.  The graph/orchestrator owns
    stopping policy; this evaluator only applies evidence rules.
    """

    def evaluate(self, state: QueryAgentState) -> SufficiencyResult:
        facts = list(state.known_facts)
        conflicts = list(state.conflicts)
        if conflicts:
            return SufficiencyResult(
                sufficient=False,
                status=EvidenceStatus.CONFLICT,
                known_facts=facts,
                evidence_gaps=[],
                unknowns=_unique(state.unknowns),
                conflicts=conflicts,
            )

        evidence = _all_evidence(state)
        verified_ids = {item.evidence_id for item in evidence}
        usable_facts = [fact for fact in facts if fact.evidence_ids and set(fact.evidence_ids) <= verified_ids]
        roles = {item.role for item in evidence} | {fact.role for fact in usable_facts}
        source_types = {item.source_type for item in evidence}
        gaps: list[EvidenceGap] = []

        intent = QueryIntent(state.intent)
        if intent is QueryIntent.BUSINESS_LOGIC:
            if SourceType.CODE not in source_types:
                gaps.append(_gap("核心行为的代码实现是什么？", "CODE_EVIDENCE"))
            if not _closed_business_flow(usable_facts):
                gaps.append(_gap("业务链路如何从起点闭合到结果？", "BUSINESS_FLOW"))
        elif intent is QueryIntent.DATA_TRACE:
            code_roles = {
                item.role for item in evidence if item.source_type is SourceType.CODE
            } | {
                fact.role for fact in usable_facts if fact.source_type is SourceType.CODE
            }
            if EvidenceRole.PRODUCER not in code_roles:
                gaps.append(_gap("数据由哪里生成或写入？", "CODE_PRODUCER"))
            if not code_roles.intersection({EvidenceRole.CONSUMER, EvidenceRole.CHECK}):
                gaps.append(_gap("数据在哪里被消费或校验？", "CODE_CONSUMER_OR_CHECK"))
        elif intent is QueryIntent.RULE_REASON:
            if SourceType.CODE not in source_types or EvidenceRole.BEHAVIOR not in roles:
                gaps.append(_gap("当前代码执行了什么规则行为？", "CODE_BEHAVIOR"))
            if _explicitly_requires_requirement(state.question) and not state.requirement_evidence:
                gaps.append(_gap("该规则明确对应的需求依据是什么？", "REQUIREMENT_BASIS"))
            elif not _has_rule_basis(state, usable_facts):
                gaps.append(_gap("该规则的需求依据或已确认业务依据是什么？", "RULE_BASIS"))
        elif intent is QueryIntent.CROSS_PROCESS:
            process_names = _process_names(state, evidence, usable_facts)
            if len(process_names) < 2:
                gaps.append(_gap("关系两端分别属于哪些流程？", "TWO_PROCESS_ENDPOINTS"))
            if not _has_process_link(state, usable_facts):
                gaps.append(_gap("两个流程之间通过什么数据或业务关系连接？", "PROCESS_LINK"))
            code_processes = {item.process for item in evidence if item.source_type is SourceType.CODE and item.process}
            code_processes.update(fact.process for fact in usable_facts if fact.source_type is SourceType.CODE and fact.process)
            for fact in usable_facts:
                if fact.source_type is SourceType.CODE and fact.role is EvidenceRole.PROCESS_LINK:
                    code_processes.update(value for value in (fact.subject, fact.object) if value)
            has_code_link = any(
                fact.source_type is SourceType.CODE and fact.role is EvidenceRole.PROCESS_LINK
                for fact in usable_facts
            )
            asks_direct_call = any(term in state.question for term in ("直接 CALL", "直接调用", "同步调用"))
            if len(code_processes) < 2 or (asks_direct_call and not has_code_link):
                gaps.append(_gap("两个流程各自的代码端点在哪里？", "CODE_PROCESS_ENDPOINTS"))

        # A date/history question needs dated evidence.  A rule statement can
        # prove that a rule exists, but cannot prove when it first took effect.
        if _asks_for_history_date(state.question) and not _has_dated_fact(usable_facts):
            gaps.append(_gap("该规则首次生效的日期或历史版本证据是什么？", "HISTORICAL_EFFECTIVE_DATE"))

        status = EvidenceStatus.SUFFICIENT if not gaps else EvidenceStatus.INSUFFICIENT
        unknowns = _unique(state.unknowns)
        if gaps and not unknowns:
            unknowns = [gap.question for gap in gaps]
        return SufficiencyResult(not gaps, status, usable_facts, gaps, unknowns, conflicts)

    def apply(self, state: QueryAgentState) -> SufficiencyResult:
        """Evaluate and copy the deterministic result back to mutable state."""
        result = self.evaluate(state)
        state.known_facts = result.known_facts
        state.evidence_gaps = result.evidence_gaps
        state.unknowns = result.unknowns
        state.conflicts = result.conflicts
        state.evidence_status = result.status
        return result


def _all_evidence(state: QueryAgentState):
    # Candidate lists are intentionally excluded: candidates cannot prove facts.
    unique = {}
    for item in state.code_evidence + state.business_evidence + state.requirement_evidence:
        unique[item.identity()] = item
    return list(unique.values())


def _closed_business_flow(facts: Iterable[StructuredFact]) -> bool:
    facts = list(facts)
    if any(fact.role is EvidenceRole.PROCESS_LINK for fact in facts):
        return True
    edges = [(fact.subject, fact.object) for fact in facts if fact.subject and fact.object]
    if not edges:
        return False
    starts = {start for start, _ in edges}
    ends = {end for _, end in edges}
    return bool(starts - ends) and bool(ends - starts)


def _has_rule_basis(state: QueryAgentState, facts: Iterable[StructuredFact]) -> bool:
    if state.requirement_evidence:
        return True
    confirmed_business_ids = {
        item.evidence_id for item in state.business_evidence if item.status == "CONFIRMED"
    }
    return bool(confirmed_business_ids) or any(
        fact.source_type is SourceType.BUSINESS
        and fact.role is EvidenceRole.RULE
        and fact.confidence == "CONFIRMED"
        for fact in facts
    )


def _has_process_link(state: QueryAgentState, facts: Iterable[StructuredFact]) -> bool:
    if any(fact.role is EvidenceRole.PROCESS_LINK for fact in facts):
        return True
    return any(
        item.role is EvidenceRole.PROCESS_LINK
        and (item.source_type is SourceType.REQUIREMENT or item.status == "CONFIRMED")
        for item in state.business_evidence + state.requirement_evidence
    )


def _process_names(state: QueryAgentState, evidence, facts: Iterable[StructuredFact]) -> set[str]:
    result = set(state.processes)
    result.update(item.process for item in evidence if item.process)
    result.update(fact.process for fact in facts if fact.process)
    for fact in facts:
        if fact.role is EvidenceRole.PROCESS_LINK:
            result.update(value for value in (fact.subject, fact.object) if value)
    return result


def _gap(question: str, gap_type: str) -> EvidenceGap:
    return EvidenceGap(question=question, gap_type=gap_type)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _asks_for_history_date(question: str) -> bool:
    return any(term in question for term in ("哪一天", "何时", "什么时候", "生效日期", "历史上", "从哪天"))


def _explicitly_requires_requirement(question: str) -> bool:
    return any(term in question for term in ("需求依据", "需求来源", "需求文档", "哪条需求"))


def _has_dated_fact(facts: Iterable[StructuredFact]) -> bool:
    import re
    return any(re.search(r"\b(?:19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?", fact.statement) for fact in facts)

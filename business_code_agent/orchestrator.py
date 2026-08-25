from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from sqlite3 import Connection

from .models import AgentState
from .tools import EvidenceTools
from .util import dumps, stable_id, tokens


class Orchestrator:
    """Single, bounded and observable Evidence Loop."""

    def __init__(self, db: Connection, max_evidence_iterations: int = 3):
        self.db = db
        self.tools = EvidenceTools(db)
        self.max_iterations = max_evidence_iterations

    def answer(self, question: str) -> AgentState:
        state = self._understand(question)
        while state.iteration < self.max_iterations:
            self.advance(state)
            if state.evidence_status == "SUFFICIENT":
                break
        return self.finalize(state)

    def understand(self, question: str) -> AgentState:
        return self._understand(question)

    def advance(self, state: AgentState) -> AgentState:
        """Execute exactly one observable evidence iteration."""
        field = state.search_terms[0]
        state.iteration += 1
        if state.iteration == 1:
            checks = self.tools.find_field_activity(field, ("CHECK_FIELD", "READ_FIELD", "READ_COLUMN"))
            state.code_candidates.extend(item["qualified_name"] for item in checks)
            state.code_evidence.extend(item["evidence_id"] for item in checks)
        elif state.iteration == 2:
            writes = self.tools.find_field_activity(field, ("WRITE_FIELD", "WRITE_COLUMN"))
            state.code_candidates.extend(item["qualified_name"] for item in writes)
            state.code_evidence.extend(item["evidence_id"] for item in writes)
            requirements = self.tools.search_requirements(" ".join(state.search_terms))
            state.requirement_candidates.extend(item["id"] for item in requirements)
            business = self.tools.search_business(" ".join(state.search_terms))
            state.knowledge_candidates.extend(item["id"] for item in business)
            state.business_evidence.extend(item["evidence_id"] for item in business)
        else:
            for requirement_id in state.requirement_candidates:
                chunks = self.tools.read_requirement_chunks(requirement_id, " ".join(state.search_terms))
                state.requirement_evidence.extend(item["evidence_id"] for item in chunks)
        sufficient, gaps = self._evaluate(state)
        state.evidence_gaps = gaps
        state.evidence_status = "SUFFICIENT" if sufficient else "INSUFFICIENT"
        return state

    def finalize(self, state: AgentState) -> AgentState:
        field = state.search_terms[0]
        if state.evidence_status != "SUFFICIENT":
            state.unknowns = [gap["question"] for gap in state.evidence_gaps]
        state.answer = self._compose(state, field)
        self._save(state)
        return state

    def _understand(self, question: str) -> AgentState:
        indexed = self.tools.field_names()
        candidates = [name for name in indexed if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", question, re.IGNORECASE)]
        if not candidates:
            question_tokens = tokens(question)
            candidates = [token for token in question_tokens if any(
                self.tools.normalize_field(token) == self.tools.normalize_field(name) for name in indexed
            )]
        if not candidates:
            suggestions = "、".join(indexed[:10]) or "（仓库尚未发现字段）"
            raise ValueError(f"问题中没有命中已索引字段。可先运行 discover；当前字段示例：{suggestions}")
        field = max(candidates, key=len)
        question_terms = [term for term in tokens(question) if term.lower() != field.lower()]
        return AgentState(question=question, search_terms=[field, *question_terms], business_objects=question_terms)

    def _evaluate(self, state: AgentState) -> tuple[bool, list[dict[str, str]]]:
        fact_types = self.tools.fact_types_for_evidence(state.code_evidence)
        has_check = "CHECK_FIELD" in fact_types
        has_read = bool({"READ_FIELD", "READ_COLUMN"} & fact_types)
        has_write = bool({"WRITE_FIELD", "WRITE_COLUMN"} & fact_types)
        gaps = []
        if not (has_check or has_read):
            gaps.append({"question": "字段在哪里读取或校验？", "suggestedAction": "find_field_activity"})
        if not has_write:
            gaps.append({"question": "字段的值在哪里产生或写入？", "suggestedAction": "find_field_activity"})
        if state.requirement_candidates and not state.requirement_evidence:
            gaps.append({"question": "规则来源于哪段需求原文？", "suggestedAction": "read_requirement_chunk"})
        required_gaps = [gap for gap in gaps if "可选增强" not in gap["question"]]
        return not required_gaps, gaps

    def _compose(self, state: AgentState, field: str) -> str:
        code = [self.tools.evidence(item) for item in dict.fromkeys(state.code_evidence)]
        activity = self.tools.find_field_activity(field)
        writers = [row["qualified_name"] for row in activity if row["fact_type"] in {"WRITE_FIELD", "WRITE_COLUMN"}]
        consumers = [row["qualified_name"] for row in activity if row["fact_type"] in {"CHECK_FIELD", "READ_FIELD", "READ_COLUMN"}]
        producer_text = "、".join(dict.fromkeys(writers)) or "尚未定位的产生位置"
        consumer_text = "、".join(dict.fromkeys(consumers)) or "尚未定位的消费位置"
        activity_types = {item["fact_type"] for item in activity}
        if "CHECK_FIELD" in activity_types:
            consume_action = "读取/校验"
        elif activity_types & {"READ_FIELD", "READ_COLUMN"}:
            consume_action = "读取"
        else:
            consume_action = "消费"
        relation = "已确认业务知识解释了跨流程关联" if state.business_evidence else "当前仅能给出代码事实，业务原因尚未提供"
        lines = [f"结论：`{field}` 由 {producer_text} 产生/写入，由 {consumer_text} {consume_action}；{relation}。"]
        for item in code:
            fact_types = self.tools.fact_types_for_evidence([item["id"]])
            if fact_types & {"WRITE_FIELD", "WRITE_COLUMN"}:
                action = "生成/写入"
            elif "CHECK_FIELD" in fact_types:
                action = "读取/校验"
            else:
                action = "读取"
            lines.append(f"[CODE] {item['locator']}:{item['line_start']} {action}：`{item['excerpt']}`（Evidence {item['id']}）")
        for evidence_id in dict.fromkeys(state.requirement_evidence):
            item = self.tools.evidence(evidence_id)
            lines.append(f"[REQUIREMENT] {item['source_id']} / {item['chunk_id']}：{item['excerpt']}（Evidence {item['id']}）")
        for evidence_id in dict.fromkeys(state.business_evidence):
            item = self.tools.evidence(evidence_id)
            knowledge = self.tools.business_knowledge_by_evidence(evidence_id)
            lines.append(f"[BUSINESS] {item['excerpt']}（状态 {knowledge['status']}，Evidence {item['id']}）")
        if code and state.business_evidence:
            lines.append(f"[INFERENCE] 代码分别证明写入与校验，已确认业务知识解释跨流程关系；因此可推断消费端使用的是产生端确定的 `{field}`。这不是 CALL 关系结论。")
        optional_unknowns = []
        if not state.requirement_evidence:
            optional_unknowns.append("未提供需求依据；不影响代码事实，但无法说明规则来源")
        if not state.business_evidence:
            optional_unknowns.append("未提供已确认业务知识；无法说明业务原因")
        for unknown in dict.fromkeys([*state.unknowns, *optional_unknowns]):
            lines.append(f"[UNKNOWN] {unknown}")
        return "\n".join(lines)

    def _save(self, state: AgentState) -> None:
        run_id = stable_id("RUN", state.question, datetime.now(timezone.utc).isoformat())
        self.db.execute("INSERT INTO agent_run VALUES (?, ?, ?, ?, ?, ?)", (run_id, state.question, dumps(state.to_reference_dict()), state.evidence_status, state.iteration, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

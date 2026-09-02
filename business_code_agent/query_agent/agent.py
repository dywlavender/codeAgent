from __future__ import annotations

import logging

import re
import time
from datetime import datetime, timezone

from ..schema import connect
from ..util import stable_id
from .answer import AnswerBuilder, AnswerRenderer
from .conflicts import ConflictDetector
from .evaluator import EvidenceSufficiencyEvaluator
from .evidence import EvidenceAssembler
from .models import (
    EvidenceConflict, EvidenceRef, EvidenceRole, EvidenceStatus,
    QueryAgentState, SourceType, StructuredFact,
)
from .observability import QueryRunRecorder
from .retriever import QueryRetriever
from .understanding import QuestionUnderstandingService


logger = logging.getLogger(__name__)

class BusinessCodeQueryAgent:
    """One bounded agent. Tools retrieve; deterministic nodes decide proof gaps."""

    def __init__(self, db, *, connection_factory=None, max_iterations: int = 3, query_analyzer=None, answer_composer=None):
        if max_iterations < 1 or max_iterations > 3:
            raise ValueError("max_iterations must be between 1 and 3")
        self.db = db
        self.max_iterations = max_iterations
        self.understander = QuestionUnderstandingService()
        self.query_analyzer = query_analyzer
        self.answer_composer = answer_composer
        self.retriever = QueryRetriever(db, connection_factory=connection_factory)
        self.assembler = EvidenceAssembler(db)
        self.evaluator = EvidenceSufficiencyEvaluator()
        self.conflicts = ConflictDetector()
        self.answer_builder = AnswerBuilder(self.conflicts)
        self.renderer = AnswerRenderer()
        if self.query_analyzer and hasattr(self.query_analyzer, "bind_tools"):
            self.query_analyzer.bind_tools(self._read_only_tools())

    def _read_only_tools(self):
        try:
            from langchain.tools import tool
        except ImportError:
            return []

        @tool
        def search_code_facts(query: str) -> list[dict]:
            """Search bounded Code Fact summaries. Never returns source text."""
            return self._read_only_call(lambda tools: tools.code.search_code(query, 8))

        @tool
        def search_business_knowledge(query: str) -> list[dict]:
            """Search verified business entities and relations."""
            return self._read_only_call(
                lambda tools: tools.business.search_business_knowledge(query)[:8]
            )

        @tool
        def get_business_entry_anchors(business_id: str) -> list[dict]:
            """Read durable FLOW/CAPABILITY entry hints; this never returns answer facts."""
            return self._read_only_call(
                lambda tools: tools.code.get_business_entry_anchors(business_id)
            )

        @tool
        def resolve_entry_anchor(application_id: str, entry_name: str) -> dict:
            """Resolve one entry name against the current application code index."""
            return self._read_only_call(
                lambda tools: tools.code.resolve_entry_anchor(application_id, entry_name)
            )

        @tool
        def search_requirements(query: str) -> list[dict]:
            """Search requirement digests without loading original chunks."""
            return self._read_only_call(
                lambda tools: tools.requirement.search_requirements(query)[:8]
            )

        @tool
        def follow_integration_edge(symbol_id: str) -> list[dict]:
            """Follow verified HTTP/RPC edges from one indexed symbol."""
            return self._read_only_call(
                lambda tools: tools.code.follow_integration_flow(symbol_id)[:24]
            )

        return [
            search_code_facts, search_business_knowledge, get_business_entry_anchors,
            resolve_entry_anchor, search_requirements, follow_integration_edge,
        ]

    def _read_only_call(self, operation):
        """Run a model-requested read on a connection owned by that worker.

        LangGraph executes tool calls in a worker thread.  A ``QueryService``
        created for a file database keeps its recorder connection in the HTTP
        thread, so handing that same SQLite connection to a model tool raises
        ``sqlite3.ProgrammingError``.  The retriever already has the correct
        per-worker connection lifecycle for deterministic parallel retrieval;
        reuse it for these model-facing tools as well.
        """
        if self.retriever.connection_factory is None:
            return operation(self.retriever.tools)
        with self.retriever._worker_bundle() as tools:
            return operation(tools)

    def run(self, question: str, *, history=()) -> dict:
        run_id = stable_id("QRUN", question, datetime.now(timezone.utc).isoformat())
        recorder = QueryRunRecorder(self.db, run_id, question)
        recorder.start()
        state = None
        raw_evidence: list[dict] = []
        try:
            state, understanding_mode = self._timed_node(
                recorder, "UNDERSTAND", 0, {}, lambda: self._understand(question, history)
            )
            recorder.update_intent(state.intent.value)
            recorder.checkpoint("UNDERSTAND", state.to_reference_dict())

            initial = self._timed_node(recorder, "INITIAL_SEARCH", 0, {"terms": state.search_terms}, lambda: self.retriever.initial_search(state, state))
            self._apply_candidates(state, initial)
            self._expand_code_from_knowledge(state)
            self._record_tools(recorder, initial.get("tool_calls", []), 0, "INITIAL_SEARCH")
            recorder.checkpoint("INITIAL_SEARCH", state.to_reference_dict())

            # LOAD_SUMMARY is deliberately a no-raw transition.
            self._timed_node(recorder, "LOAD_SUMMARY", 0, {}, lambda: {"rawEvidenceLoaded": False})
            result = self.evaluator.apply(state)
            recorder.step("EVALUATE", 0, {}, result.to_dict(), self._evidence_count(state), 0.0)
            recorder.checkpoint("EVALUATE", state.to_reference_dict())

            while state.evidence_status is EvidenceStatus.INSUFFICIENT and state.iteration < self.max_iterations:
                state.iteration += 1
                expansion = self._timed_node(
                    recorder, "PLAN_EXPANSION", state.iteration,
                    {"gaps": [gap.gap_type for gap in state.evidence_gaps]},
                    lambda: self.retriever.expand(state, state.evidence_gaps),
                )
                self._apply_candidates(state, expansion)
                self._expand_code_from_knowledge(state)
                self._record_tools(recorder, expansion.get("tool_calls", []), state.iteration, "EXPAND_EVIDENCE")
                recorder.checkpoint("EXPAND_EVIDENCE", state.to_reference_dict())

                loaded = self._timed_node(
                    recorder, "READ_RAW_EVIDENCE", state.iteration, {},
                    lambda: self.assembler.load_raw(state, state.evidence_gaps),
                )
                raw_evidence = self.assembler.apply_budget([*raw_evidence, *loaded])
                self._apply_evidence_and_facts(state, raw_evidence)
                state.unknowns = []
                state.conflicts = self._typed_conflicts(self.conflicts.detect(state.known_facts))
                result = self.evaluator.apply(state)
                recorder.step("EVALUATE", state.iteration, {}, result.to_dict(), self._evidence_count(state), 0.0)
                recorder.checkpoint("EVALUATE", state.to_reference_dict())
                if state.evidence_status in {EvidenceStatus.SUFFICIENT, EvidenceStatus.CONFLICT}:
                    break

            if state.evidence_status is EvidenceStatus.INSUFFICIENT:
                state.unknowns = list(dict.fromkeys([*state.unknowns, *[gap.question for gap in state.evidence_gaps]]))
            answer = self.answer_builder.build(state)
            answer_mode = "DETERMINISTIC"
            if self.answer_composer:
                composed = self.answer_composer.compose(
                    question, evidence_status=state.evidence_status.value,
                    facts=answer["facts"], unknowns=answer["unknowns"], conflicts=answer["conflicts"],
                )
                answer["conclusion"] = composed["conclusion"]
                answer["suggestedFollowUps"] = composed.get("suggestedFollowUps", [])
                answer_mode = "MODEL"
                recorder.step("SYNTHESIZE", state.iteration, {}, {"claims": len(composed.get("claims", []))}, self._evidence_count(state), 0.0)
            answer["answerMode"] = answer_mode
            answer["resolvedQuestion"] = " ".join(state.search_terms)
            answer["entities"] = list(dict.fromkeys([
                *state.business_objects, *state.processes, *state.systems,
                *state.field_hints, *state.table_hints, *state.code_hints,
            ]))[:24]
            state.final_answer = answer
            reference_state = state.to_reference_dict()
            recorder.step("BUILD_ANSWER", state.iteration, {}, {"facts": len(answer["facts"]), "conflicts": len(answer["conflicts"])}, self._evidence_count(state), 0.0)
            recorder.finish(state.to_reference_dict(), answer, state.evidence_status.value, self.assembler.last_stats["sourceCharacters"])
            return {
                "runId": run_id, "status": "completed", "intent": state.intent.value,
                "evidenceStatus": state.evidence_status.value, "iterations": state.iteration,
                "answer": answer, "renderedAnswer": self.renderer.render(answer),
                "evidence": raw_evidence, "metrics": self._metrics(state, raw_evidence),
                "understandingMode": understanding_mode,
                "resolvedQuestion": " ".join(state.search_terms),
                "answerMode": answer_mode,
                "entities": answer["entities"],
                # Structured candidates are navigation references only.  They
                # contain identifiers and metadata, never raw source excerpts
                # or durable business-to-code associations.
                "businessCandidates": reference_state.get("business_candidates", []),
                "codeCandidates": reference_state.get("code_candidates", []),
            }
        except Exception as exc:
            recorder.fail(type(exc).__name__, state.to_reference_dict() if state else None)
            raise

    def _understand(self, question, history=()):
        deterministic_question = _contextual_question(question, history)
        deterministic = self.understander.understand(deterministic_question)
        if not self.query_analyzer:
            logger.info("未配置理解模型，问题理解使用确定性流程")
            return QueryAgentState.from_understanding(question, deterministic), "DETERMINISTIC"
        semantic = self.query_analyzer.understand(question, history)
        merged = _merge_understanding(deterministic, semantic)
        return QueryAgentState.from_understanding(question, merged), "MODEL"

    @staticmethod
    def _apply_candidates(state, result):
        state.code_candidates = _merge_candidates(
            state.code_candidates, result.get("code_candidates", []),
            "evidence_id", "evidenceId", "edge_id", "edgeId",
            "symbol_id", "symbolId", "target_id", "targetId", "id",
        )
        state.business_candidates = _merge_candidates(state.business_candidates, result.get("business_candidates", []), "id", "knowledge_id")
        state.requirement_candidates = _merge_candidates(state.requirement_candidates, result.get("requirement_candidates", []), "id", "requirement_id")

    def _expand_code_from_knowledge(self, state):
        """Use explicit fields in retrieved cards/digests to locate code facts."""
        names = []
        for item in state.business_candidates:
            names.extend(re.findall(r"[a-z][A-Za-z0-9_]*(?:Type|Code|No|Id|Status|Flag|Date|Time)", str(item.get("statement", ""))))
        for item in state.requirement_candidates:
            names.extend(re.findall(r"[a-z][A-Za-z0-9_]*(?:Type|Code|No|Id|Status|Flag|Date|Time)", str(item.get("digest", ""))))
        requested = {self.retriever.tools.code.normalize_field(value) for value in state.field_hints}
        for name in dict.fromkeys(names):
            if requested and self.retriever.tools.code.normalize_field(name) not in requested:
                continue
            rows = self.retriever.tools.code.find_field_activity(name)
            state.code_candidates = _merge_candidates(state.code_candidates, rows, "evidence_id", "symbol_id", "id")

    def _apply_evidence_and_facts(self, state, raw):
        state.code_evidence = []
        state.business_evidence = []
        state.requirement_evidence = []
        facts = []
        code_candidates = list(state.code_candidates)
        requirement_candidates = list(state.requirement_candidates)
        for item in raw:
            source = SourceType(item["sourceType"])
            role = self._role(item, code_candidates)
            process = self._process(state, role)
            ref = EvidenceRef(
                item["evidenceId"], source, item["sourceId"], item.get("sourceVersion", ""),
                item.get("location", {}), item.get("contentHash", ""), role,
                item.get("status", "DERIVED"), process,
            )
            getattr(state, f"{source.value.lower()}_evidence").append(ref)
            if source is SourceType.BUSINESS:
                self._append_supporting_evidence(state, item)
            fact = self._fact(
                item, ref, state, code_candidates, state.business_candidates,
                requirement_candidates,
            )
            if fact:
                facts.append(fact)
        facts.extend(self._integration_facts(raw, code_candidates))
        state.known_facts = _dedupe_facts(facts)

    @staticmethod
    def _integration_facts(raw, candidates):
        loaded_ids = {str(item.get("evidenceId") or "") for item in raw}
        edges: dict[str, dict] = {}
        for candidate in candidates:
            edge_id = str(candidate.get("edge_id") or candidate.get("edgeId") or "")
            if edge_id and str(candidate.get("status") or "").upper() == "VERIFIED":
                edges.setdefault(edge_id, candidate)
        facts = []
        for candidate in edges.values():
            evidence_ids = [str(item) for item in candidate.get("required_evidence_ids", []) if item]
            if not evidence_ids or not set(evidence_ids) <= loaded_ids:
                continue
            source_app = str(candidate.get("source_application_name") or candidate.get("source_application_id") or "source")
            target_app = str(candidate.get("target_application_name") or candidate.get("target_application_id") or "target")
            source_symbol = str(candidate.get("source_qualified_name") or candidate.get("source_symbol_id") or "")
            target_symbol = str(candidate.get("target_qualified_name") or candidate.get("target_symbol_id") or "")
            protocol = str(candidate.get("protocol") or candidate.get("edge_type") or "INTEGRATION")
            key = str(candidate.get("edge_key") or "")
            statement = f"{source_app} 的 {source_symbol} 通过 {protocol} {key} 调用 {target_app} 的 {target_symbol}"
            facts.append(StructuredFact(
                statement, SourceType.CODE, evidence_ids, EvidenceRole.PROCESS_LINK,
                source_app, str(candidate.get("edge_type") or protocol), target_app,
                f"{source_app}→{target_app}", "VERIFIED",
            ))
        return facts

    @staticmethod
    def _append_supporting_evidence(state, item):
        for supporting in item.get("supportingEvidence", []):
            source_name = str(supporting.get("source_type") or "").upper()
            source = (
                SourceType.CODE if source_name == "CODE"
                else SourceType.REQUIREMENT if source_name == "REQUIREMENT"
                else SourceType.BUSINESS
            )
            ref = EvidenceRef(
                str(supporting["id"]), source, str(supporting.get("source_id") or ""),
                str(supporting.get("source_version") or ""),
                {
                    "locator": supporting.get("locator"),
                    "startLine": supporting.get("line_start"),
                    "endLine": supporting.get("line_end"),
                    "chunkId": supporting.get("chunk_id"),
                },
                str(supporting.get("content_hash") or ""), EvidenceRole.RULE,
                "CONFIRMED", "",
            )
            collection = getattr(state, f"{source.value.lower()}_evidence")
            if not any(existing.evidence_id == ref.evidence_id for existing in collection):
                collection.append(ref)

    def _fact(self, item, ref, state, code_candidates, business_candidates, requirement_candidates):
        if str(item.get("status", "")).upper() in {"STALE", "CONFLICT", "REJECTED", "SUGGESTED", "CANDIDATE"}:
            return None
        if state.field_hints and ref.source_type is not SourceType.CODE:
            content_key = self.retriever.tools.code.normalize_field(str(item.get("content", "")))
            if not any(self.retriever.tools.code.normalize_field(field) in content_key for field in state.field_hints):
                return None
        if ref.source_type is SourceType.CODE:
            candidate = next((row for row in code_candidates if _candidate_matches(row, item)), {})
            fact_type = str(candidate.get("fact_type") or candidate.get("factType") or item.get("relationType") or "")
            # An entry anchor is a navigation hint.  Its source file is read
            # by the runtime resolver, but the hint itself cannot become a
            # behaviour fact.
            if fact_type == "ENTRY_ANCHOR":
                return None
            # Declarations prove local technical edges, but are not useful
            # standalone answer claims. Rendering every declaration also lets a
            # search hit masquerade as observed runtime behaviour.
            if fact_type == "CODE_DECLARATION":
                return None
            field = str(candidate.get("subject") or (state.field_hints[0] if state.field_hints else "数据"))
            symbol = str(candidate.get("qualified_name") or candidate.get("qualifiedName") or item["sourceId"])
            application = str(candidate.get("application_name") or candidate.get("applicationName") or "")
            owner = f"{application} 的 {symbol}" if application else symbol
            if fact_type == "HTTP_CALL":
                return StructuredFact(
                    f"{owner} 发起 HTTP {field}", SourceType.CODE, [ref.evidence_id],
                    EvidenceRole.BEHAVIOR, application or symbol, fact_type, field, ref.process,
                )
            if fact_type == "HTTP_ENDPOINT":
                return StructuredFact(
                    f"{owner} 暴露 HTTP {field}", SourceType.CODE, [ref.evidence_id],
                    EvidenceRole.BEHAVIOR, application or symbol, fact_type, field, ref.process,
                )
            if fact_type == "RPC_CALL":
                return StructuredFact(
                    f"{owner} 声明 RPC 调用 {field}", SourceType.CODE, [ref.evidence_id],
                    EvidenceRole.BEHAVIOR, application or symbol, fact_type, field, ref.process,
                )
            if fact_type == "UI_EVENT":
                return StructuredFact(
                    f"{owner} 通过 {field} 触发交互", SourceType.CODE, [ref.evidence_id],
                    EvidenceRole.BEHAVIOR, application or symbol, fact_type, field, ref.process,
                )
            verb = "写入/生成" if "WRITE" in fact_type else "校验" if "CHECK" in fact_type else "读取/使用" if "READ" in fact_type else "执行"
            strategy = _strategy(item.get("content", ""))
            comparable = strategy in {"SNAPSHOT", "REALTIME"}
            relation = "DATA_STRATEGY" if comparable else (fact_type or "CODE_BEHAVIOR")
            fact_role = EvidenceRole.BEHAVIOR if state.intent.value == "RULE_REASON" else ref.role
            return StructuredFact(f"{owner} {verb} {field}", SourceType.CODE, [ref.evidence_id], fact_role, field, relation, strategy, ref.process)
        if ref.source_type is SourceType.BUSINESS:
            statement = " ".join(str(item.get("content", "")).split())[:500]
            field = state.field_hints[0] if state.field_hints else (state.business_objects[0] if state.business_objects else "业务关系")
            strategy = _strategy(statement)
            relation = "DATA_STRATEGY" if strategy in {"SNAPSHOT", "REALTIME"} else "BUSINESS_RULE"
            candidate = next((row for row in business_candidates if _candidate_matches(row, item)), {})
            knowledge_type = str(candidate.get("knowledge_type") or "").upper()
            role = EvidenceRole.PROCESS_LINK if knowledge_type in {"FLOW", "RELATION"} or len(state.processes) >= 2 else EvidenceRole.RULE
            # The human statement is proven by its own source excerpt. Code
            # navigation evidence is loaded separately at runtime, so a stale
            # entry hint cannot make the business fact disappear.
            evidence_ids = [ref.evidence_id]
            return StructuredFact(statement, SourceType.BUSINESS, evidence_ids, role, field, relation, strategy if relation == "DATA_STRATEGY" else "", "")
        statement = self._requirement_rule(item, requirement_candidates)
        field = state.field_hints[0] if state.field_hints else (state.business_objects[0] if state.business_objects else "规则")
        strategy = _strategy(statement)
        relation = "DATA_STRATEGY" if strategy in {"SNAPSHOT", "REALTIME"} else "REQUIREMENT_RULE"
        return StructuredFact(statement, SourceType.REQUIREMENT, [ref.evidence_id], EvidenceRole.RULE, field, relation, strategy if relation == "DATA_STRATEGY" else "", "")

    @staticmethod
    def _requirement_rule(item, candidates):
        chunk_id = item.get("location", {}).get("chunkId")
        for candidate in candidates:
            digest_value = candidate.get("digest", {})
            for rule in digest_value.get("business_rules", digest_value.get("businessRules", [])):
                if not isinstance(rule, dict):
                    continue
                ids = rule.get("evidence_chunk_ids", rule.get("evidenceChunkIds", []))
                if chunk_id in ids:
                    return str(rule.get("statement", ""))[:500]
        return " ".join(str(item.get("content", "")).split())[:500]

    @staticmethod
    def _role(item, candidates):
        candidate = next((row for row in candidates if _candidate_matches(row, item)), {})
        kind = str(candidate.get("fact_type") or candidate.get("factType") or item.get("relationType") or "")
        if "WRITE" in kind:
            return EvidenceRole.PRODUCER
        if "CHECK" in kind:
            return EvidenceRole.CHECK
        if "READ" in kind:
            return EvidenceRole.CONSUMER
        return EvidenceRole.BEHAVIOR

    @staticmethod
    def _process(state, role):
        if not state.processes:
            return ""
        if role is EvidenceRole.PRODUCER:
            return state.processes[0]
        if role in {EvidenceRole.CONSUMER, EvidenceRole.CHECK}:
            return state.processes[-1]
        return state.processes[0]

    @staticmethod
    def _typed_conflicts(values):
        result = []
        for value in values:
            result.append(EvidenceConflict(
                value.get("conflictKey", ""),
                value.get("evidenceIds", []) if value.get("code") else [],
                value.get("evidenceIds", []) if value.get("business") else [],
                value.get("evidenceIds", []) if value.get("requirement") else [],
                value.get("reason", ""),
            ))
        return result

    def _timed_node(self, recorder, name, iteration, input_summary, operation):
        started = time.perf_counter()
        result = operation()
        duration = (time.perf_counter() - started) * 1000
        output = _summary(result)
        recorder.step(name, iteration, input_summary, output, 0, duration)
        return result

    def _record_tools(self, recorder, calls, iteration, step_name):
        step_id = recorder.step(step_name + "_TOOLS", iteration, {}, {"calls": len(calls)}, 0, 0.0)
        for call in calls:
            recorder.tool_call(step_id, call.get("tool", "unknown"), call.get("input", {}), call.get("resultCount", 0), iteration, 0.0)

    @staticmethod
    def _evidence_count(state):
        return len(state.code_evidence) + len(state.business_evidence) + len(state.requirement_evidence)

    def _metrics(self, state, raw):
        used_ids = {eid for fact in state.known_facts for eid in fact.evidence_ids}
        return {
            "evidenceRecallCount": len(used_ids),
            "evidencePrecision": (len(used_ids) / len(raw)) if raw else 0.0,
            "sourceCoverage": sorted({fact.source_type.value for fact in state.known_facts}),
            "expansionCount": state.iteration,
            "sourceCharacters": self.assembler.last_stats["sourceCharacters"],
            "unknownCount": len(state.unknowns),
            "conflictCount": len(state.conflicts),
        }


def _candidate_matches(candidate, evidence):
    source_id = str(evidence.get("sourceId", ""))
    evidence_id = str(evidence.get("evidenceId", ""))
    return source_id in {str(candidate.get(key, "")) for key in ("symbol_id", "symbolId", "id", "target_id", "targetId")} or evidence_id in {str(candidate.get(key, "")) for key in ("evidence_id", "evidenceId")}


def _merge_candidates(current, new, *keys):
    result, positions = [], {}
    for item in [*current, *new]:
        if not isinstance(item, dict):
            item = item.__dict__
        identity = next((str(item.get(key)) for key in keys if item.get(key)), repr(sorted(item.items())))
        if identity not in positions:
            positions[identity] = len(result)
            result.append(item)
        elif _candidate_richness(item) > _candidate_richness(result[positions[identity]]):
            result[positions[identity]] = item
    return result[:100]


def _candidate_richness(item):
    return sum(1 for key in ("fact_type", "factType", "evidence_id", "evidenceId", "subject", "target", "digest", "status") if item.get(key))


def _strategy(text):
    lowered = text.lower()
    if any(term in lowered for term in ("实时查询", "重新查询", "realtime", "live query")):
        return "REALTIME"
    if any(term in lowered for term in ("申请阶段", "沿用", "继续使用", "stored")):
        return "SNAPSHOT"
    if any(term in lowered for term in ("生成", "create", "generate")):
        return "GENERATE"
    if any(term in lowered for term in ("校验", "validate", "check")):
        return "VALIDATE"
    return "OBSERVED"


def _dedupe_facts(facts):
    values = {}
    for fact in facts:
        values.setdefault((fact.source_type.value, fact.statement, tuple(fact.evidence_ids)), fact)
    return list(values.values())


def _summary(value):
    if isinstance(value, QueryAgentState):
        return {"intent": value.intent.value}
    if isinstance(value, dict):
        return {key: (len(item) if isinstance(item, list) else item) for key, item in value.items() if key not in {"content", "evidence"}}
    if isinstance(value, list):
        return {"count": len(value)}
    return {"value": str(value)}


def _contextual_question(question: str, history) -> str:
    if not history or len(question.strip()) > 12:
        return question
    previous = next(
        (item for item in reversed(history) if isinstance(item, dict) and item.get("role", "user") == "user"),
        history[-1] if isinstance(history, (list, tuple)) else {},
    )
    previous_question = previous.get("question", "") if isinstance(previous, dict) else str(previous)
    return f"{previous_question} {question}".strip()


def _merge_understanding(primary, semantic):
    values = {}
    for key in ("business_objects", "processes", "systems", "field_hints", "table_hints", "code_hints", "search_terms"):
        values[key] = list(dict.fromkeys([*getattr(semantic, key, []), *getattr(primary, key, [])]))[:16]
    # The model may classify a phrase semantically, but it cannot invent a new
    # intent outside the finite domain.
    return type(primary)(intent=semantic.intent, **values)

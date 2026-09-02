"""Structured, persistence-safe models for the M4 query agent.

The runtime state may temporarily contain the user's question.  Durable state
uses :meth:`QueryAgentState.to_reference_dict`, which keeps only a hash of the
question, source identifiers and structured facts -- never raw source or
requirement content.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any


class QueryIntent(StrEnum):
    BUSINESS_LOGIC = "BUSINESS_LOGIC"
    DATA_TRACE = "DATA_TRACE"
    RULE_REASON = "RULE_REASON"
    CROSS_PROCESS = "CROSS_PROCESS"


class EvidenceStatus(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    SUFFICIENT = "SUFFICIENT"
    CONFLICT = "CONFLICT"


class AnswerType(StrEnum):
    """How a completed investigation can be presented to the user."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AnswerDecision:
    """The deterministic hand-off between evidence evaluation and rendering."""

    answer_type: AnswerType
    use_model: bool
    reason: str = ""


class SourceType(StrEnum):
    CODE = "CODE"
    BUSINESS = "BUSINESS"
    REQUIREMENT = "REQUIREMENT"


class EvidenceRole(StrEnum):
    BEHAVIOR = "BEHAVIOR"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"
    CHECK = "CHECK"
    PROCESS_ENDPOINT = "PROCESS_ENDPOINT"
    PROCESS_LINK = "PROCESS_LINK"
    RULE = "RULE"


@dataclass(frozen=True)
class QuestionUnderstanding:
    intent: QueryIntent
    business_objects: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    field_hints: list[str] = field(default_factory=list)
    table_hints: list[str] = field(default_factory=list)
    code_hints: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intent"] = self.intent.value
        return value


@dataclass(frozen=True)
class CandidateRef:
    """A search hit is only a reference and cannot support a conclusion."""

    source_type: SourceType
    source_id: str
    score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """Verified evidence metadata; raw content is deliberately absent."""

    evidence_id: str
    source_type: SourceType
    source_id: str
    source_version: str = ""
    location: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    role: EvidenceRole = EvidenceRole.BEHAVIOR
    status: str = "DERIVED"
    process: str = ""

    def identity(self) -> tuple[Any, ...]:
        location = tuple(sorted((str(k), str(v)) for k, v in self.location.items()))
        return self.evidence_id, self.source_type.value, self.source_id, self.source_version, location


@dataclass(frozen=True)
class StructuredFact:
    statement: str
    source_type: SourceType
    evidence_ids: list[str]
    role: EvidenceRole = EvidenceRole.BEHAVIOR
    subject: str = ""
    relation: str = ""
    object: str = ""
    process: str = ""
    confidence: str = "VERIFIED"


@dataclass(frozen=True)
class EvidenceGap:
    question: str
    gap_type: str
    target_type: str = ""
    target_id: str = ""


@dataclass(frozen=True)
class EvidenceConflict:
    topic: str
    code_fact_ids: list[str] = field(default_factory=list)
    business_fact_ids: list[str] = field(default_factory=list)
    requirement_fact_ids: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class QueryAgentState:
    # Runtime-only input. It is excluded by to_reference_dict().
    question: str = field(default="", repr=False)
    question_hash: str = ""
    intent: QueryIntent = QueryIntent.BUSINESS_LOGIC
    business_objects: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    field_hints: list[str] = field(default_factory=list)
    table_hints: list[str] = field(default_factory=list)
    code_hints: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    code_candidates: list[CandidateRef] = field(default_factory=list)
    business_candidates: list[CandidateRef] = field(default_factory=list)
    requirement_candidates: list[CandidateRef] = field(default_factory=list)
    code_evidence: list[EvidenceRef] = field(default_factory=list)
    business_evidence: list[EvidenceRef] = field(default_factory=list)
    requirement_evidence: list[EvidenceRef] = field(default_factory=list)
    known_facts: list[StructuredFact] = field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)
    conflicts: list[EvidenceConflict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    iteration: int = 0
    evidence_status: EvidenceStatus = EvidenceStatus.INSUFFICIENT
    final_answer: dict[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.question and not self.question_hash:
            self.question_hash = sha256(self.question.encode("utf-8")).hexdigest()

    @classmethod
    def from_understanding(cls, question: str, value: QuestionUnderstanding) -> "QueryAgentState":
        return cls(
            question=question,
            intent=value.intent,
            business_objects=list(value.business_objects),
            processes=list(value.processes),
            systems=list(value.systems),
            field_hints=list(value.field_hints),
            table_hints=list(value.table_hints),
            code_hints=list(value.code_hints),
            search_terms=list(value.search_terms),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intent"] = self.intent.value
        value["evidence_status"] = self.evidence_status.value
        return _enum_values(value)

    def to_reference_dict(self) -> dict[str, Any]:
        """Return checkpoint-safe state containing IDs and structured facts."""
        value = self.to_dict()
        value.pop("question", None)
        value.pop("final_answer", None)
        value["code_candidates"] = [_candidate_projection(item, "CODE") for item in value["code_candidates"]]
        value["business_candidates"] = [_candidate_projection(item, "BUSINESS") for item in value["business_candidates"]]
        value["requirement_candidates"] = [_candidate_projection(item, "REQUIREMENT") for item in value["requirement_candidates"]]
        return value


@dataclass(frozen=True)
class SufficiencyResult:
    sufficient: bool
    status: EvidenceStatus
    known_facts: list[StructuredFact]
    evidence_gaps: list[EvidenceGap]
    unknowns: list[str]
    conflicts: list[EvidenceConflict]

    def to_dict(self) -> dict[str, Any]:
        return _enum_values(asdict(self))


def _enum_values(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_enum_values(item) for item in value]
    return value


def _candidate_projection(item: Any, source: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        item = _enum_values(asdict(item))
    common = {"id", "source_id", "sourceId", "score", "reason", "status", "evidence_id", "evidenceId"}
    allowed = {
        "CODE": common | {
            "symbol_id", "symbolId", "qualified_name", "qualifiedName", "fact_type", "factType",
            "subject", "locator", "line_start", "line_end", "target_type", "targetType",
            "edge_id", "edge_type", "edge_key", "protocol", "required_evidence_ids",
            "entry_anchor_id", "entryAnchorId", "entry_resolution", "entryResolution",
            "anchor_status", "anchor_application_id", "anchor_entry_name",
            "application_id", "application_name", "system_id", "system_name",
            "source_application_id", "source_application_name", "source_symbol_id", "source_qualified_name",
            "target_application_id", "target_application_name", "target_symbol_id", "target_qualified_name",
        },
        "BUSINESS": common | {"knowledge_id", "knowledgeId", "title", "knowledge_type", "version"},
        "REQUIREMENT": common | {"requirement_id", "requirementId", "title", "current_version", "versionId", "digest"},
    }[source]
    return {key: value for key, value in item.items() if key in allowed}

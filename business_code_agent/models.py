from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_STATUSES = {"CONFIRMED", "DERIVED", "SUGGESTED", "REJECTED", "STALE", "CONFLICT"}


@dataclass
class AgentState:
    question: str
    intent: str = "business_rule_trace"
    business_objects: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    code_candidates: list[str] = field(default_factory=list)
    requirement_candidates: list[str] = field(default_factory=list)
    knowledge_candidates: list[str] = field(default_factory=list)
    code_evidence: list[str] = field(default_factory=list)
    requirement_evidence: list[str] = field(default_factory=list)
    business_evidence: list[str] = field(default_factory=list)
    evidence_gaps: list[dict[str, str]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    iteration: int = 0
    evidence_status: str = "INSUFFICIENT"
    answer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_reference_dict(self) -> dict[str, Any]:
        """Durable state excludes rendered answers and all raw evidence text."""
        value = self.to_dict()
        value.pop("answer", None)
        return value

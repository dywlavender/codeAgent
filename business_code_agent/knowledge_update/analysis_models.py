from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class UpdateSourceType(StrEnum):
    CODE_CHANGE = "CODE_CHANGE"
    REQUIREMENT = "REQUIREMENT"
    DOCUMENT = "DOCUMENT"
    MANUAL = "MANUAL"
    USER_FEEDBACK = "USER_FEEDBACK"


class ProposalAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    RETIRE = "RETIRE"


@dataclass(frozen=True)
class UpdateSource:
    source_type: UpdateSourceType
    source_id: str
    content: str
    evidence_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.content.strip():
            raise ValueError("source content is required")


@dataclass(frozen=True)
class ImpactCandidate:
    function_id: str
    name: str
    summary: str = ""
    domain: str = ""
    current: Mapping[str, Any] = field(default_factory=dict)
    match_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProposedItem:
    item_type: str
    target_type: str
    rationale: str
    confidence: float
    evidence_ids: tuple[str, ...]
    target_id: str | None = None
    before: Any = None
    after: Any = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], allowed_evidence: set[str]) -> "ProposedItem":
        item_type = str(value.get("item_type") or value.get("itemType") or "").strip().upper()
        target_type = str(value.get("target_type") or value.get("targetType") or "").strip().upper()
        rationale = str(value.get("rationale") or "").strip()
        evidence = tuple(dict.fromkeys(str(item) for item in value.get("evidence_ids", value.get("evidenceIds", []))))
        if not item_type or not target_type or not rationale:
            raise ValueError("proposal item requires item_type, target_type and rationale")
        if value.get("before") is None and value.get("after") is None:
            raise ValueError("proposal item requires before or after")
        unknown = set(evidence) - allowed_evidence
        if unknown:
            raise ValueError("proposal item cites evidence outside the supplied source")
        confidence = float(value.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("proposal item confidence must be between 0 and 1")
        return cls(
            item_type=item_type,
            target_type=target_type,
            target_id=str(value["target_id"]) if value.get("target_id") else None,
            before=value.get("before"),
            after=value.get("after"),
            rationale=rationale,
            confidence=confidence,
            evidence_ids=evidence,
        )


@dataclass(frozen=True)
class UpdateAnalysis:
    title: str
    action: ProposalAction
    summary: str
    proposed_snapshot: Mapping[str, Any]
    items: tuple[ProposedItem, ...]
    target_function_id: str | None = None
    base_version_id: str | None = None
    conflicts: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        allowed_evidence: set[str],
        allowed_targets: set[str],
    ) -> "UpdateAnalysis":
        forbidden = {"status", "reviewer", "review_status", "published_by"} & set(value)
        if forbidden:
            raise ValueError("semantic analysis cannot control review or publication state")
        action = ProposalAction(str(value.get("action", "CREATE")).upper())
        target = value.get("target_function_id", value.get("targetFunctionId"))
        target = str(target) if target else None
        if target and target not in allowed_targets:
            raise ValueError("analysis selected a function outside the bounded candidates")
        if action != ProposalAction.CREATE and not target:
            raise ValueError("UPDATE and RETIRE proposals require a target function")
        snapshot = value.get("proposed_snapshot", value.get("proposedSnapshot"))
        if not isinstance(snapshot, Mapping):
            raise ValueError("proposed_snapshot must be an object")
        if snapshot_evidence(snapshot) - allowed_evidence:
            raise ValueError("proposed_snapshot cites evidence outside the supplied source")
        name = str(snapshot.get("name", "")).strip()
        if not name:
            raise ValueError("proposed_snapshot.name is required")
        items = tuple(
            ProposedItem.from_mapping(item, allowed_evidence)
            for item in value.get("items", [])
        )
        if not items:
            raise ValueError("at least one proposal item is required")
        title = str(value.get("title", "")).strip()
        summary = str(value.get("summary", "")).strip()
        if not title or not summary:
            raise ValueError("analysis title and summary are required")
        return cls(
            title=title,
            action=action,
            summary=summary,
            proposed_snapshot=dict(snapshot),
            items=items,
            target_function_id=target,
            base_version_id=str(value["base_version_id"]) if value.get("base_version_id") else None,
            conflicts=tuple(str(item) for item in value.get("conflicts", [])),
            unknowns=tuple(str(item) for item in value.get("unknowns", [])),
        )


def snapshot_evidence(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found = {
            str(item)
            for key, items in value.items()
            if key in {"evidence_ids", "evidenceIds"} and isinstance(items, (list, tuple))
            for item in items
        }
        return found | set().union(*(snapshot_evidence(item) for item in value.values()), set())
    if isinstance(value, (list, tuple)):
        return set().union(*(snapshot_evidence(item) for item in value), set())
    return set()

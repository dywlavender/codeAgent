from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class FunctionStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class ProposalAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    RETIRE = "RETIRE"


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    PUBLISHED = "PUBLISHED"


@dataclass
class FunctionScenario:
    name: str
    summary: str = ""
    id: str | None = None
    status: str = "ACTIVE"
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class FunctionRule:
    statement: str
    conditions: list[str] = field(default_factory=list)
    result: str = ""
    id: str | None = None
    status: str = "ACTIVE"
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class FunctionEntry:
    entry_type: str
    label: str
    target_type: str = ""
    target_id: str = ""
    locator: str = ""
    id: str | None = None
    status: str = "ACTIVE"
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class DataImpact:
    object_type: str
    object_name: str
    operation: str
    before_state: str = ""
    after_state: str = ""
    description: str = ""
    id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class FunctionSnapshot:
    name: str
    domain: str = ""
    summary: str = ""
    scenarios: list[FunctionScenario | dict[str, Any]] = field(default_factory=list)
    rules: list[FunctionRule | dict[str, Any]] = field(default_factory=list)
    entries: list[FunctionEntry | dict[str, Any]] = field(default_factory=list)
    data_impacts: list[DataImpact | dict[str, Any]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

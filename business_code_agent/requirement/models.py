from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class RequirementStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class RequirementRelationType(StrEnum):
    RELATED_BUSINESS_KNOWLEDGE = "RELATED_BUSINESS_KNOWLEDGE"
    IMPLEMENTS_RULE = "IMPLEMENTS_RULE"
    AFFECTS_METHOD = "AFFECTS_METHOD"
    AFFECTS_API = "AFFECTS_API"
    AFFECTS_FIELD = "AFFECTS_FIELD"
    AFFECTS_TABLE = "AFFECTS_TABLE"
    AFFECTS_PROCESS = "AFFECTS_PROCESS"
    AFFECTS_SYSTEM = "AFFECTS_SYSTEM"
    SUPERSEDES_REQUIREMENT = "SUPERSEDES_REQUIREMENT"
    RELATED_REQUIREMENT = "RELATED_REQUIREMENT"


@dataclass
class BusinessRule:
    id: str
    statement: str
    business_objects: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    result: str = ""
    evidence_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RequirementDigest:
    requirement_id: str
    title: str
    business_goal: str = ""
    background: str = ""
    business_objects: list[str] = field(default_factory=list)
    affected_processes: list[str] = field(default_factory=list)
    affected_systems: list[str] = field(default_factory=list)
    business_rules: list[BusinessRule] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    status_changes: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    compatibility_rules: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    extraction_mode: str = "DETERMINISTIC_BOUNDED"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedSection:
    path: list[str]
    paragraphs: list[str]
    paragraph_start: int
    paragraph_end: int
    page: int | None = None


@dataclass
class RequirementChunk:
    id: str
    section_path: list[str]
    sequence: int
    content: str
    start_offset: int
    end_offset: int
    paragraph_start: int
    paragraph_end: int
    page: int | None = None

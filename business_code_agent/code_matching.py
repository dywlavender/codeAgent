"""Deterministic code candidate ranking shared by requirement enrichment."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from sqlite3 import Connection

from .tools import EvidenceTools
from .util import stable_id, tokens


class TargetType(StrEnum):
    CLASS = "CLASS"
    METHOD = "METHOD"
    API = "API"
    FIELD = "FIELD"
    TABLE = "TABLE"
    COLUMN = "COLUMN"


@dataclass
class CodeKnowledge:
    title: str
    statement: str
    business_objects: list[str] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    code_hints: list[str] = field(default_factory=list)


@dataclass
class SearchPlan:
    queries: list[str]
    field_hints: list[str]
    action_hints: list[str]


@dataclass
class CodeCandidate:
    target_type: TargetType
    target_id: str
    label: str
    score: int
    fact_types: list[str]
    evidence_ids: list[str]
    reason: str


class CodeMatcher:
    def __init__(self, db: Connection):
        self.db = db
        self.tools = EvidenceTools(db)

    def build_search_plan(self, knowledge: CodeKnowledge) -> SearchPlan:
        field_hints = [value for value in [*knowledge.code_hints, *knowledge.keywords] if _field_like(value)]
        action_hints = [value for value in knowledge.code_hints if value.lower() in {
            "create", "generate", "produce", "sync", "process", "validate", "check",
            "read", "get", "query", "use", "consume", "apply", "application",
        }]
        queries = [*knowledge.business_objects, *knowledge.processes, *knowledge.systems, *knowledge.keywords]
        return SearchPlan(_unique(queries)[:8], _unique(field_hints)[:10], _unique(action_hints)[:10])

    def rank(self, knowledge: CodeKnowledge, plan: SearchPlan, limit: int = 25) -> list[CodeCandidate]:
        if limit < 1 or limit > 30:
            raise ValueError("candidate limit must be between 1 and 30")
        terms = _normalised_terms([*plan.queries, *knowledge.keywords, *knowledge.code_hints])
        field_keys = {self.tools.normalize_field(value) for value in plan.field_hints if value}
        action_terms = {value.lower() for value in plan.action_hints}
        rows = self.db.execute(
            """SELECT cs.id,cs.kind,cs.qualified_name,cs.name,cf.fact_type,cf.subject,cf.target,cf.evidence_id,e.locator
                 FROM code_symbol cs LEFT JOIN code_fact cf ON cf.symbol_id=cs.id
                 LEFT JOIN evidence e ON e.id=cf.evidence_id ORDER BY cs.qualified_name,cf.fact_type"""
        )
        grouped: dict[str, dict] = {}
        for row in rows:
            item = grouped.setdefault(row["id"], {
                "id": row["id"], "kind": row["kind"], "label": row["qualified_name"],
                "name": row["name"], "facts": [], "evidence": [],
            })
            if row["fact_type"]:
                item["facts"].append((row["fact_type"], row["subject"], row["target"]))
                item["evidence"].append(row["evidence_id"])
        candidates: list[CodeCandidate] = []
        for item in grouped.values():
            haystack = " ".join([item["label"], *[f"{f} {s} {t}" for f, s, t in item["facts"]]]).lower()
            matched_terms = {term for term in terms if term in _normalise(haystack)}
            exact_fields = {
                self.tools.normalize_field(value)
                for _, subject, target in item["facts"] for value in (subject, target)
            } & field_keys
            name_action = {term for term in action_terms if term in item["name"].lower()}
            if not matched_terms and not exact_fields and not name_action:
                continue
            fact_types = {fact for fact, _, _ in item["facts"]}
            score = len(exact_fields) * 5 + min(3, len(matched_terms)) * 3 + len(name_action) * 3
            if len(matched_terms) >= 2:
                score += 3
            if "WRITE_FIELD" in fact_types or "WRITE_COLUMN" in fact_types:
                score += 2 if exact_fields else 0
            if "CHECK_FIELD" in fact_types and exact_fields:
                score += 4
            candidates.append(CodeCandidate(
                _target_type(item["kind"]), item["id"], item["label"], score,
                sorted(fact_types), _unique(item["evidence"]), _reason(exact_fields, matched_terms, fact_types),
            ))
        for field_name in plan.field_hints:
            key = self.tools.normalize_field(field_name)
            evidence = [row["evidence_id"] for row in self.tools.find_field_activity(field_name)]
            facts = [row["fact_type"] for row in self.tools.find_field_activity(field_name)]
            for row in self.db.execute("SELECT id,qualified_name FROM code_symbol WHERE kind='FIELD'"):
                if self.tools.normalize_field(row["qualified_name"].rsplit(".", 1)[-1]) == key:
                    candidates.append(CodeCandidate(TargetType.FIELD, row["id"], row["qualified_name"], 8, sorted(set(facts)), _unique(evidence), f"字段名与 {field_name} 完全匹配"))
        virtual: dict[tuple[str, str], dict] = {}
        for row in self.db.execute(
            """SELECT cf.fact_type,cf.subject,cf.target,cf.evidence_id,cs.qualified_name
                 FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                WHERE cf.fact_type IN ('READ_TABLE','WRITE_TABLE','READ_COLUMN','WRITE_COLUMN')"""
        ):
            target_type = TargetType.TABLE if row["fact_type"].endswith("TABLE") else TargetType.COLUMN
            normalized = self.tools.normalize_field(row["subject"])
            column_match = any(len(key) >= 3 and (normalized == key or key in normalized or normalized in key) for key in field_keys)
            broad_match = any(term in _normalise(f"{row['subject']} {row['target']} {row['qualified_name']}") for term in terms)
            if target_type == TargetType.COLUMN and not column_match:
                continue
            if target_type == TargetType.TABLE and not broad_match:
                continue
            item = virtual.setdefault((target_type.value, row["subject"].lower()), {"facts": set(), "evidence": [], "symbols": set()})
            item["facts"].add(row["fact_type"]); item["evidence"].append(row["evidence_id"]); item["symbols"].add(row["qualified_name"])
        for (kind, label), item in virtual.items():
            candidates.append(CodeCandidate(TargetType(kind), stable_id(kind, label), label, 5 + (5 if self.tools.normalize_field(label) in field_keys else 0), sorted(item["facts"]), _unique(item["evidence"]), f"{kind} 事实来自 " + ", ".join(sorted(item["symbols"])[:3])))
        dedup: dict[tuple[str, str], CodeCandidate] = {}
        for item in candidates:
            key = (item.target_type.value, item.target_id)
            if key not in dedup or item.score > dedup[key].score:
                dedup[key] = item
        return sorted(dedup.values(), key=lambda item: (-item.score, item.label))[:limit]


def _target_type(kind: str) -> TargetType:
    return {"MYBATIS_STATEMENT": TargetType.API, "API": TargetType.API, "METHOD": TargetType.METHOD, "FIELD": TargetType.FIELD}.get(kind, TargetType.CLASS)


def _reason(exact_fields: set[str], matched_terms: set[str], facts: set[str]) -> str:
    parts = (["字段完全匹配"] if exact_fields else [])
    if matched_terms:
        parts.append("命中 " + ", ".join(sorted(matched_terms)[:4]))
    if facts:
        parts.append("包含 " + ", ".join(sorted(facts)))
    return "；".join(parts)[:240]


def _field_like(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)) and value.lower() not in {"create", "sync", "process", "read", "get", "query", "core", "apply", "application", "result", "withdraw", "subsidy"}


def _normalised_terms(values: list[str]) -> set[str]:
    result = set()
    for value in values:
        value = value.strip().lower()
        if len(value) > 1:
            result.add(_normalise(value))
        result.update(_normalise(token) for token in tokens(value) if len(token) > 1)
    return result


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", value.lower())


def _unique(values) -> list:
    return list(dict.fromkeys(value for value in values if value))

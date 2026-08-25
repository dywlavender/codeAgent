from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..business_tools import BusinessTools
from ..requirement_tools import RequirementTools
from ..tools import EvidenceTools
from ..util import digest, stable_id


@dataclass(frozen=True)
class EvidenceBudget:
    """Hard limits for the raw material retained by one query run."""

    max_code_evidence: int = 12
    max_requirement_evidence: int = 6
    max_business_knowledge: int = 10
    max_source_chars: int = 80_000


class EvidenceAssembler:
    """Load only requested raw material and normalize the three evidence types.

    Candidates are deliberately not accepted as evidence.  A code candidate is
    promoted only after ``read_source`` succeeds; a requirement candidate only
    after its chunk is read.  Business cards are short, human-maintained records,
    so loading the card itself is sufficient.
    """

    def __init__(
        self,
        db,
        *,
        evidence_tools: EvidenceTools | None = None,
        business_tools: BusinessTools | None = None,
        requirement_tools: RequirementTools | None = None,
        budget: EvidenceBudget | None = None,
    ):
        self.db = db
        self.evidence_tools = evidence_tools or EvidenceTools(db)
        self.business_tools = business_tools or BusinessTools(db)
        self.requirement_tools = requirement_tools or RequirementTools(db)
        self.budget = budget or EvidenceBudget()
        self.last_stats = {"sourceCharacters": 0, "dropped": 0, "truncated": 0}

    def load_raw(self, state: Any, gaps: Iterable[Any] | None = None) -> list[dict]:
        """Resolve evaluator gaps to verified, uniformly-shaped Evidence.

        ``state`` may be a dataclass/model or a plain dict.  When gaps identify
        concrete targets only those targets are read.  If no concrete target is
        present, the bounded candidate lists are used as a conservative fallback.
        """
        gaps = list(gaps if gaps is not None else _get(state, "evidence_gaps", "evidenceGaps", default=[]))
        code_candidates = _as_list(_get(state, "code_candidates", "codeCandidates", default=[]))
        business_candidates = _as_list(_get(state, "business_candidates", "businessCandidates", default=[]))
        requirement_candidates = _as_list(_get(state, "requirement_candidates", "requirementCandidates", default=[]))

        requested = self._requested_targets(gaps)
        evidence: list[dict] = []
        evidence.extend(self._load_code(code_candidates, requested["code"]))
        evidence.extend(self._load_business(business_candidates, requested["business"]))
        evidence.extend(
            self._load_requirements(
                requirement_candidates,
                requested["requirement"],
                question=str(_get(state, "question", default="")),
            )
        )
        return self.apply_budget(evidence)

    def apply_budget(self, evidence: Iterable[dict]) -> list[dict]:
        """Deduplicate, priority-sort and enforce source/count/character limits."""
        unique: dict[tuple, dict] = {}
        for item in evidence:
            normalized = self._normalize(item)
            key = evidence_key(normalized)
            previous = unique.get(key)
            if previous is None or _priority(normalized) < _priority(previous):
                unique[key] = normalized

        counts = {"CODE": 0, "REQUIREMENT": 0, "BUSINESS": 0}
        limits = {
            "CODE": self.budget.max_code_evidence,
            "REQUIREMENT": self.budget.max_requirement_evidence,
            "BUSINESS": self.budget.max_business_knowledge,
        }
        retained: list[dict] = []
        used = dropped = truncated = 0
        for item in sorted(unique.values(), key=lambda value: (_priority(value), evidence_key(value))):
            source_type = item["sourceType"]
            if source_type not in limits or counts[source_type] >= limits[source_type]:
                dropped += 1
                continue
            remaining = self.budget.max_source_chars - used
            if remaining <= 0:
                dropped += 1
                continue
            content = item.get("content") or ""
            if len(content) > remaining:
                item = {**item, "content": content[:remaining], "contentHash": digest(content[:remaining]), "truncated": True}
                content = item["content"]
                truncated += 1
            retained.append(item)
            counts[source_type] += 1
            used += len(content)
        self.last_stats = {"sourceCharacters": used, "dropped": dropped, "truncated": truncated, **counts}
        return retained

    def _load_code(self, candidates: list[dict], targets: set[str]) -> list[dict]:
        selected = _select(
            candidates, targets,
            "symbolId", "symbol_id", "id", "targetId", "target_id", "qualified_name", "qualifiedName", "label",
        )
        results = []
        for candidate in selected:
            symbol_id = _get(candidate, "symbolId", "symbol_id", "id", "targetId", "target_id")
            if not symbol_id:
                continue
            try:
                source = self.evidence_tools.read_source(str(symbol_id))
            except (KeyError, OSError, UnicodeError):
                continue
            evidence_id = _get(candidate, "evidenceId", "evidence_id")
            # A symbol hit without a persisted Code Fact/Evidence record is a
            # candidate only; it must not become answer evidence merely because
            # its source file can be opened.
            if not evidence_id:
                continue
            stored = _safe_evidence(self.evidence_tools, evidence_id)
            source_version = (
                (stored or {}).get("source_version")
                or _get(candidate, "sourceVersion", "source_version")
                or self._code_version(str(symbol_id))
                or "INDEXED"
            )
            content = source.get("content", "")
            results.append({
                "evidenceId": evidence_id or stable_id("QEV", "CODE", str(symbol_id), source_version, str(source.get("line_start"))),
                "sourceType": "CODE",
                "sourceId": str(symbol_id),
                "sourceVersion": str(source_version),
                "location": {
                    "file": source.get("path") or (stored or {}).get("locator"),
                    "startLine": source.get("line_start"),
                    "endLine": source.get("line_end"),
                },
                "content": content,
                "contentHash": digest(content),
                "provenanceHash": (stored or {}).get("content_hash"),
                "status": _get(candidate, "status", default="DIRECT"),
                "relationType": _get(candidate, "factType", "fact_type"),
            })
        return results

    def _code_version(self, symbol_id: str) -> str | None:
        try:
            row = self.db.execute(
                """SELECT cf.content_hash FROM code_symbol cs
                     JOIN code_file cf ON cf.id=cs.file_id WHERE cs.id=?""",
                (symbol_id,),
            ).fetchone()
        except (AttributeError, TypeError):
            return None
        return row["content_hash"] if row else None

    def _load_business(self, candidates: list[dict], targets: set[str]) -> list[dict]:
        selected = _select(candidates, targets, "id", "knowledgeId", "knowledge_id", "sourceId", "source_id")
        results = []
        for candidate in selected:
            knowledge_id = _get(candidate, "id", "knowledgeId", "knowledge_id", "sourceId", "source_id")
            if not knowledge_id:
                continue
            try:
                detail = self.business_tools.get_business_knowledge(str(knowledge_id))
            except KeyError:
                continue
            knowledge = detail.get("knowledge", detail)
            content = knowledge.get("statement") or candidate.get("statement") or ""
            evidence_id = knowledge.get("evidence_id") or _get(candidate, "evidenceId", "evidence_id")
            stored = next((item for item in detail.get("evidence", []) if item.get("id") == evidence_id), None)
            supporting = [
                item for item in detail.get("evidence", [])
                if item.get("id") and item.get("id") != evidence_id
            ]
            results.append({
                "evidenceId": evidence_id or stable_id("QEV", "BUSINESS", str(knowledge_id), str(knowledge.get("version", 1))),
                "sourceType": "BUSINESS",
                "sourceId": str(knowledge_id),
                "sourceVersion": str(knowledge.get("version", 1)),
                "location": {"knowledgeId": str(knowledge_id)},
                "content": content,
                "contentHash": (stored or {}).get("content_hash") or digest(content),
                "status": knowledge.get("status") or candidate.get("status") or "SUGGESTED",
                "supportingEvidenceIds": [item["id"] for item in supporting],
                "supportingEvidence": supporting,
            })
        return results

    def _load_requirements(self, candidates: list[dict], targets: dict[str, set[str]], *, question: str) -> list[dict]:
        selected = _select(candidates, set(targets), "id", "requirementId", "requirement_id", "sourceId", "source_id")
        results = []
        for candidate in selected:
            requirement_id = str(_get(candidate, "id", "requirementId", "requirement_id", "sourceId", "source_id") or "")
            if not requirement_id:
                continue
            chunk_ids = set(targets.get(requirement_id, set()))
            if not chunk_ids:
                digest_value = candidate.get("digest") or _safe_digest(self.requirement_tools, requirement_id)
                chunk_ids.update(_digest_chunk_ids(digest_value))
            if not chunk_ids and question:
                # search_chunks is intentionally summary-only: it returns IDs and
                # section metadata, never chunk content.
                try:
                    chunk_ids.update(item["id"] for item in self.requirement_tools.service.search_chunks(requirement_id, question))
                except (KeyError, TypeError, ValueError):
                    pass
            if not chunk_ids and question:
                # Compatibility for phase-one digests whose businessRules are
                # plain strings and therefore carry no evidenceChunkIds.
                for variant in _chunk_queries(question):
                    try:
                        chunk_ids.update(
                            item["id"] for item in self.evidence_tools.read_requirement_chunks(requirement_id, variant)
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                    if chunk_ids:
                        break
            for chunk_id in chunk_ids:
                try:
                    chunk = self.requirement_tools.read_requirement_chunk(requirement_id, chunk_id)
                except KeyError:
                    continue
                content = chunk.get("content", "")
                section = chunk.get("section_path") or _json_value(chunk.get("section_path_json"), [])
                version_id = chunk.get("requirement_version_id") or _get(candidate, "versionId", "version_id") or "CURRENT"
                results.append({
                    "evidenceId": chunk.get("evidence_id") or stable_id("QEV", "REQUIREMENT", requirement_id, str(version_id), str(chunk_id)),
                    "sourceType": "REQUIREMENT",
                    "sourceId": requirement_id,
                    "sourceVersion": str(version_id),
                    "location": {"chunkId": chunk_id, "section": " / ".join(section) if isinstance(section, list) else section},
                    "content": content,
                    "contentHash": chunk.get("content_hash") or digest(content),
                    "status": candidate.get("status") or "DIRECT",
                })
        return results

    @staticmethod
    def _requested_targets(gaps: list[Any]) -> dict:
        result: dict[str, Any] = {"code": set(), "business": set(), "requirement": {}}
        for gap in gaps:
            gap_type = str(_get(gap, "type", default="")).upper()
            target = _get(gap, "target", "targetId", "target_id")
            if isinstance(target, dict):
                target_id = _get(target, "id", "sourceId", "source_id", "requirementId", "requirement_id")
                chunk_id = _get(target, "chunkId", "chunk_id")
            else:
                target_id, chunk_id = target, _get(gap, "chunkId", "chunk_id")
            if not target_id:
                continue
            if "REQUIREMENT" in gap_type or str(target_id).startswith("REQ-"):
                result["requirement"].setdefault(str(target_id), set())
                if chunk_id:
                    result["requirement"][str(target_id)].add(str(chunk_id))
            elif "BUSINESS" in gap_type or str(target_id).startswith("BK-"):
                result["business"].add(str(target_id))
            else:
                result["code"].add(str(target_id))
        return result

    @staticmethod
    def _normalize(item: dict) -> dict:
        source_type = str(_get(item, "sourceType", "source_type", default="UNKNOWN")).upper()
        location = _get(item, "location", default={}) or {}
        content = str(_get(item, "content", default="") or "")
        return {
            **item,
            "evidenceId": str(_get(item, "evidenceId", "evidence_id", default="")),
            "sourceType": source_type,
            "sourceId": str(_get(item, "sourceId", "source_id", default="")),
            "sourceVersion": str(_get(item, "sourceVersion", "source_version", default="")),
            "location": location,
            "content": content,
            "contentHash": _get(item, "contentHash", "content_hash") or digest(content),
        }


def evidence_key(item: dict) -> tuple:
    """Stable M4 deduplication key, including commit/requirement versions."""
    location = item.get("location") or {}
    return (
        item.get("sourceType"), item.get("sourceId"), item.get("sourceVersion"),
        json.dumps(location, ensure_ascii=False, sort_keys=True, default=str),
    )


def _priority(item: dict) -> int:
    status = str(item.get("status") or "").upper()
    if status == "CONFIRMED":
        return 0
    if item.get("sourceType") in {"CODE", "REQUIREMENT"} and status in {"", "DIRECT", "ACTIVE"}:
        return 1
    if status == "DERIVED":
        return 2
    return 3


def _chunk_queries(question: str) -> list[str]:
    values = [question]
    values.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", question))
    for run in re.findall(r"[\u4e00-\u9fff]+", question):
        values.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(value for value in values if len(value) > 1))[:20]


def _get(value: Any, *names: str, default=None):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _as_list(value) -> list:
    return list(value or [])


def _select(candidates: list[dict], targets: set[str], *keys: str) -> list[dict]:
    if not targets:
        return candidates
    return [
        item for item in candidates
        if any(str(_get(item, key, default="")) in targets for key in keys)
    ]


def _safe_evidence(tools: EvidenceTools, evidence_id) -> dict | None:
    if not evidence_id:
        return None
    try:
        return tools.evidence(str(evidence_id))
    except KeyError:
        return None


def _safe_digest(tools: RequirementTools, requirement_id: str) -> dict:
    try:
        return tools.get_requirement_digest(requirement_id)
    except KeyError:
        return {}


def _digest_chunk_ids(value: dict) -> set[str]:
    ids: set[str] = set()
    for rule in value.get("businessRules", value.get("business_rules", [])) if isinstance(value, dict) else []:
        if not isinstance(rule, dict):
            continue
        ids.update(str(item) for item in rule.get("evidenceChunkIds", rule.get("evidence_chunk_ids", [])))
    return ids


def _json_value(value, default):
    if not value:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


# Descriptive aliases for callers that prefer the specification terminology.
ContextBudget = EvidenceBudget

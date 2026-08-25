from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..code_matching import CodeKnowledge, CodeMatcher, SearchPlan
from ..util import stable_id
from ..tools import EvidenceTools
from ..knowledge_update.repository import KnowledgeGovernanceRepository
from .models import RequirementRelationType


class RequirementRelationBuilder:
    def __init__(self, db):
        self.db = db
        self.code_matcher = CodeMatcher(db)
        self.evidence_tools = EvidenceTools(db)

    def enrich(self, requirement_id: str, version_id: str, digest_value, limit: int = 30) -> list[dict]:
        if limit < 1 or limit > 50:
            raise ValueError("relation candidate limit must be between 1 and 50")
        results = []
        results.extend(self._business(version_id, digest_value))
        results.extend(self._code(requirement_id, version_id, digest_value, limit))
        self.db.commit()
        return results

    def _business(self, version_id: str, digest_value) -> list[dict]:
        requirement_terms = _terms(" ".join([
            *digest_value.business_objects, *digest_value.affected_processes,
            *digest_value.affected_systems, *[rule.statement for rule in digest_value.business_rules],
        ]))
        results = []
        repository = KnowledgeGovernanceRepository(self.db)
        for row in repository.list_functions(status="PUBLISHED", limit=100):
            snapshot = row.get("snapshot") or {}
            business_terms = _terms(" ".join([
                snapshot.get("name", ""), snapshot.get("summary", ""),
                *[item.get("name", "") for item in snapshot.get("scenarios", [])],
                *[item.get("statement", "") for item in snapshot.get("rules", [])],
            ]))
            overlap = sorted(requirement_terms & business_terms)
            if not overlap:
                continue
            statement = "；".join(item.get("statement", "") for item in snapshot.get("rules", []))
            explicit = len(overlap) >= 2 or any(statement and (statement in rule.statement or rule.statement in statement) for rule in digest_value.business_rules)
            status = "DERIVED" if explicit else "SUGGESTED"
            evidence_id = self._requirement_evidence_for_terms(version_id, overlap)
            function_evidence = list(snapshot.get("evidence_ids", []))
            for collection in ("scenarios", "rules", "entries", "data_impacts"):
                for item in snapshot.get(collection, []):
                    function_evidence.extend(item.get("evidence_ids", []))
            relation = self._save(
                version_id, "DIGEST", version_id,
                RequirementRelationType.RELATED_BUSINESS_KNOWLEDGE.value,
                "BUSINESS_FUNCTION", row["id"], "DETERMINISTIC_MATCH",
                min(0.95, 0.45 + 0.1 * len(overlap)),
                status if evidence_id and function_evidence else "SUGGESTED",
                f"需求与已发布功能知识共同命中：{', '.join(overlap[:6])}",
                evidence_id, function_evidence[0] if function_evidence else None,
            )
            results.append(relation)
        return results

    def _code(self, requirement_id: str, version_id: str, digest_value, limit: int) -> list[dict]:
        pseudo = CodeKnowledge(
            digest_value.title, " ".join(rule.statement for rule in digest_value.business_rules),
            digest_value.business_objects,
            digest_value.affected_processes, digest_value.affected_systems,
            digest_value.keywords[:10], [*digest_value.fields, *digest_value.interfaces][:10],
        )
        plan = SearchPlan(
            [*digest_value.business_objects, *digest_value.affected_processes, *digest_value.affected_systems, *digest_value.keywords][:8],
            digest_value.fields[:10],
            [value for value in digest_value.keywords if value.lower() in {"create", "generate", "sync", "validate", "check", "process", "read", "get", "query"}][:10],
        )
        candidates = self.code_matcher.rank(pseudo, plan, limit)
        results = []
        for candidate in candidates:
            relation_type = {
                "METHOD": RequirementRelationType.AFFECTS_METHOD.value,
                "API": RequirementRelationType.AFFECTS_API.value,
                "FIELD": RequirementRelationType.AFFECTS_FIELD.value,
                "TABLE": RequirementRelationType.AFFECTS_TABLE.value,
                "COLUMN": RequirementRelationType.AFFECTS_FIELD.value,
            }.get(candidate.target_type.value)
            if not relation_type:
                continue
            req_evidence = self._requirement_evidence_for_candidate(version_id, digest_value, candidate)
            code_evidence = candidate.evidence_ids[0] if candidate.evidence_ids else None
            integrity_ids = [value for value in (req_evidence, code_evidence) if value]
            integrity = self.evidence_tools.validate_evidence_integrity(integrity_ids) if len(integrity_ids) == 2 else []
            semantic_anchor = self._candidate_anchor_match(digest_value, candidate)
            verified = bool(
                req_evidence and code_evidence and self._code_evidence_owned(candidate, code_evidence)
                and len(integrity) == 2 and all(item["valid"] for item in integrity)
                and semantic_anchor
            )
            status = "DERIVED" if verified else "SUGGESTED"
            source_type, source_id = self._source_rule(version_id, digest_value, candidate)
            relation = self._save(version_id, source_type, source_id, relation_type, candidate.target_type.value, candidate.target_id, "DETERMINISTIC_RANKING", min(0.99, candidate.score / 30), status, candidate.reason, req_evidence, code_evidence if verified else None)
            relation["label"] = candidate.label
            results.append(relation)
        return results

    def _requirement_evidence_for_terms(self, version_id: str, terms: list[str]) -> str | None:
        for row in self.db.execute("SELECT evidence_id,content FROM requirement_chunk_v2 WHERE requirement_version_id=? ORDER BY sequence", (version_id,)):
            if any(term in row["content"].lower() for term in terms):
                return row["evidence_id"]
        return None

    def _requirement_evidence_for_candidate(self, version_id: str, digest_value, candidate) -> str | None:
        anchors = [*digest_value.fields, *digest_value.tables]
        anchors.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", candidate.label))
        normalized = {re.sub(r"[^a-z0-9]", "", value.lower()) for value in anchors if len(value) > 2}
        for row in self.db.execute("SELECT evidence_id,content FROM requirement_chunk_v2 WHERE requirement_version_id=? ORDER BY sequence", (version_id,)):
            haystack = re.sub(r"[^a-z0-9]", "", row["content"].lower())
            if any(value in haystack for value in normalized if value):
                return row["evidence_id"]
        return None

    def _code_evidence_owned(self, candidate, evidence_id: str) -> bool:
        if candidate.target_type.value in {"METHOD", "API"}:
            return self.db.execute("SELECT 1 FROM code_fact WHERE symbol_id=? AND evidence_id=?", (candidate.target_id, evidence_id)).fetchone() is not None
        return self.db.execute("SELECT 1 FROM code_fact WHERE evidence_id=?", (evidence_id,)).fetchone() is not None

    def _candidate_anchor_match(self, digest_value, candidate) -> bool:
        """Actions help recall, but only explicit data/table anchors prove a relation."""
        normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
        expected_fields = {normalize(value) for value in digest_value.fields if value}
        expected_tables = {normalize(value) for value in digest_value.tables if value}
        rows = []
        if candidate.evidence_ids:
            marks = ",".join("?" for _ in candidate.evidence_ids)
            rows = self.db.execute(
                f"SELECT fact_type,subject,target FROM code_fact WHERE evidence_id IN ({marks})",
                candidate.evidence_ids,
            ).fetchall()
        actual_fields = {
            normalize(value) for row in rows if row["fact_type"] in {"READ_FIELD", "WRITE_FIELD", "CHECK_FIELD", "READ_COLUMN", "WRITE_COLUMN"}
            for value in (row["subject"], row["target"])
        }
        actual_tables = {
            normalize(row["subject"]) for row in rows if row["fact_type"] in {"READ_TABLE", "WRITE_TABLE"}
        }
        if candidate.target_type.value == "TABLE":
            return bool(expected_tables & actual_tables)
        return bool(expected_fields & actual_fields)

    @staticmethod
    def _source_rule(version_id, digest_value, candidate) -> tuple[str, str]:
        key = re.sub(r"[^a-z0-9]", "", candidate.label.lower())
        for rule in digest_value.business_rules:
            if any(re.sub(r"[^a-z0-9]", "", field.lower()) in key for field in digest_value.fields):
                return "BUSINESS_RULE", f"{version_id}-{rule.id.rsplit('-', 1)[-1]}"
        return "DIGEST", digest_value.requirement_id

    def _save(self, version_id, source_type, source_id, relation_type, target_type, target_id, origin, confidence, status, reason, req_evidence, code_evidence):
        now = datetime.now(timezone.utc).isoformat()
        relation_id = stable_id("RREL", version_id, source_type, source_id, relation_type, target_type, target_id)
        self.db.execute(
            """INSERT OR REPLACE INTO requirement_relation VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (relation_id, version_id, source_type, source_id, relation_type, target_type, target_id,
             origin, confidence, status, reason[:240], req_evidence, code_evidence, now),
        )
        return {"id": relation_id, "relationType": relation_type, "targetType": target_type, "targetId": target_id, "status": status, "requirementEvidenceId": req_evidence, "codeEvidenceId": code_evidence, "reason": reason[:240]}


def _terms(text: str) -> set[str]:
    latin = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower())
    chinese = re.findall(r"[一-鿿]{2,8}", text)
    return {value for value in [*latin, *chinese] if len(value) >= 2}

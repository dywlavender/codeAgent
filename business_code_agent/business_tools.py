from __future__ import annotations

import json
import re
import uuid


class BusinessTools:
    """Read-only tool boundary for published business-function knowledge.

    Only published business-function snapshots are part of this boundary.
    New knowledge is created through the governance service as
    a reviewable proposal and becomes visible here only after publication.
    """

    def __init__(self, db, *, include_legacy: bool | None = None):
        # Keep the keyword accepted for callers upgrading from the previous
        # release, but deliberately ignore it: there is no legacy source to
        # merge anymore.
        self.db = db

    def create_business_knowledge(self, content: str, *, created_by: str = "human") -> dict:
        from .knowledge_update.service import KnowledgeAdminService

        return KnowledgeAdminService(self.db).generate({
            "sourceType": "ADMIN_NOTE",
            "sourceId": f"admin-note-{uuid.uuid4().hex}",
            "content": content,
        }, created_by=created_by)

    def search_business_knowledge(self, query: str) -> list[dict]:
        return self._search_published_functions(query)

    def get_business_knowledge(self, knowledge_id: str) -> dict:
        if not knowledge_id.startswith("BF-"):
            raise KeyError(knowledge_id)
        return self._published_function(knowledge_id)

    def find_related_code(self, knowledge_id: str) -> list[dict]:
        return self.get_business_knowledge(knowledge_id)["relations"]

    def get_business_evidence(self, knowledge_id: str) -> list[dict]:
        return self.get_business_knowledge(knowledge_id)["evidence"]

    def _search_published_functions(self, query: str, limit: int = 20) -> list[dict]:
        from .knowledge_update.repository import KnowledgeGovernanceRepository

        values = []
        needle = query.strip().casefold()
        for row in KnowledgeGovernanceRepository(self.db).list_functions(status="PUBLISHED", limit=100):
            detail = self._published_function(row["id"])
            card = detail["knowledge"]
            haystack = json.dumps(card, ensure_ascii=False).casefold()
            if needle:
                title = str(card.get("title") or "").casefold()
                terms = _search_terms(query)
                if needle not in haystack and title not in needle and not any(term in haystack for term in terms):
                    continue
            values.append({
                "id": card["id"], "title": card["title"], "statement": card["statement"],
                "status": card["status"], "knowledge_type": card["knowledge_type"],
                "version": card["version"], "evidence_id": card.get("evidence_id"),
            })
            if len(values) >= limit:
                break
        return values

    def _published_function(self, function_id: str) -> dict:
        from .knowledge_update.repository import KnowledgeGovernanceRepository

        detail = KnowledgeGovernanceRepository(self.db).get_function(function_id)
        snapshot = detail["snapshot"]
        parts = [snapshot.get("summary", "")]
        if snapshot.get("scenarios"):
            parts.append("业务场景：" + "；".join(item["name"] for item in snapshot["scenarios"]))
        if snapshot.get("rules"):
            parts.append("业务规则：" + "；".join(item["statement"] for item in snapshot["rules"]))
        if snapshot.get("entries"):
            parts.append("功能入口：" + "；".join(item["label"] for item in snapshot["entries"]))
        if snapshot.get("data_impacts"):
            parts.append("数据影响：" + "；".join(
                f"{item['object_name']} {item['operation']}" for item in snapshot["data_impacts"]
            ))
        evidence_ids = list(snapshot.get("evidence_ids", []))
        for collection in ("scenarios", "rules", "entries", "data_impacts"):
            for item in snapshot.get(collection, []):
                evidence_ids.extend(item.get("evidence_ids", []))
        evidence_ids = list(dict.fromkeys(evidence_ids))
        publication = self.db.execute(
            """SELECT id FROM evidence
                 WHERE source_type='MANUAL' AND source_id=? AND source_version=?
                   AND locator=? ORDER BY id LIMIT 1""",
            (function_id, detail["version"]["id"], f"business-function:{function_id}"),
        ).fetchone()
        if publication:
            evidence_ids.insert(0, publication["id"])
        evidence = []
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence = [dict(row) for row in self.db.execute(
                f"""SELECT e.* FROM evidence e
                      LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                     WHERE e.id IN ({placeholders}) AND coalesce(el.status,'ACTIVE')='ACTIVE'""",
                evidence_ids,
            )]
        relations = []
        for entry in snapshot.get("entries", []):
            if not entry.get("target_id"):
                continue
            relations.append({
                "id": f"{detail['version']['id']}:{entry['id']}",
                "source_type": "BUSINESS", "source_id": function_id,
                "relation_type": "RELATED_IMPLEMENTATION",
                "target_type": entry.get("target_type") or "CODE_SYMBOL",
                "target_id": entry["target_id"], "status": "CONFIRMED",
                "confidence": 1.0, "evidence_ids": entry.get("evidence_ids", []),
            })
        pending_revalidation = self.db.execute(
            """SELECT 1 FROM knowledge_update_proposal
                WHERE target_function_id=?
                  AND trigger_type IN ('SOURCE_MODIFIED','SOURCE_DELETED','REQUIREMENT_VERSION_CHANGED')
                  AND status IN ('DRAFT','PENDING_REVIEW','DEFERRED','CHANGES_REQUESTED') LIMIT 1""",
            (function_id,),
        ).fetchone()
        inactive_evidence = False
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            active_count = self.db.execute(
                f"""SELECT count(*) FROM evidence e
                       LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                      WHERE e.id IN ({placeholders}) AND coalesce(el.status,'ACTIVE')='ACTIVE'""",
                evidence_ids,
            ).fetchone()[0]
            inactive_evidence = active_count != len(set(evidence_ids))
        missing_entry_target = False
        ungrounded_code_entry = False
        for entry in snapshot.get("entries", []):
            target_type = str(entry.get("target_type") or "").upper()
            target_id = str(entry.get("target_id") or "")
            if target_type not in {"CODE_SYMBOL", "METHOD", "API", "FIELD"} or not target_id:
                continue
            if not self.db.execute("SELECT 1 FROM code_symbol WHERE id=?", (target_id,)).fetchone():
                missing_entry_target = True
            if not entry.get("evidence_ids"):
                ungrounded_code_entry = True
        stale = bool(pending_revalidation or inactive_evidence or missing_entry_target or ungrounded_code_entry)
        if stale:
            for relation in relations:
                relation["status"] = "STALE"
        return {
            "knowledge": {
                "id": function_id,
                "title": snapshot["name"],
                "statement": "\n".join(part for part in parts if part),
                "status": "STALE" if stale else "CONFIRMED",
                "knowledge_type": "BUSINESS_FUNCTION",
                "version": detail["version"]["version"],
                "evidence_id": evidence_ids[0] if evidence_ids else None,
            },
            "relations": relations,
            "evidence": evidence,
            "reviews": [],
        }


def _search_terms(value: str) -> set[str]:
    terms = {
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", value)
    }
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms

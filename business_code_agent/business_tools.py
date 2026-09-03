from __future__ import annotations

import json
import re


class BusinessTools:
    """Read-only boundary for baseline business knowledge retrieval."""

    def __init__(self, db, **_ignored):
        self.db = db

    def search_business_knowledge(self, query: str) -> list[dict]:
        return self._search_baseline(query)

    def search_business(self, query: str) -> list[dict]:
        """Short helper name for the canonical business baseline."""
        return self._search_baseline(query)

    def get_business_knowledge(self, knowledge_id: str) -> dict:
        if self.db.execute("SELECT 1 FROM business_entity WHERE id=?", (knowledge_id,)).fetchone():
            return self._get_baseline_entity(knowledge_id)
        if self.db.execute("SELECT 1 FROM business_relation_v2 WHERE id=?", (knowledge_id,)).fetchone():
            return self._get_baseline_relation(knowledge_id)
        raise KeyError(knowledge_id)

    def find_related_code(self, knowledge_id: str) -> list[dict]:
        return self.get_business_knowledge(knowledge_id)["relations"]

    def get_business_evidence(self, knowledge_id: str) -> list[dict]:
        return self.get_business_knowledge(knowledge_id)["evidence"]

    def _search_baseline(self, query: str) -> list[dict]:
        needle = query.strip().casefold()
        terms = _search_terms(query)
        values: list[dict] = []
        for row in self.db.execute(
            "SELECT * FROM business_entity WHERE status!='DEPRECATED' ORDER BY entity_type,name"
        ):
            content = json.dumps({
                "name": row["name"], "aliases": json.loads(row["aliases_json"]),
                "definition": row["definition"], "attributes": json.loads(row["attributes_json"]),
            }, ensure_ascii=False)
            haystack = content.casefold()
            if needle and needle not in haystack and not any(term in haystack for term in terms):
                continue
            values.append({
                "id": row["id"], "title": row["name"],
                "statement": _entity_statement(row),
                "status": "CONFIRMED" if row["status"] == "VERIFIED" else row["status"],
                "knowledge_type": row["entity_type"], "version": 1,
                "evidence_id": row["source_evidence_id"],
            })
        for row in self.db.execute(
            "SELECT * FROM business_relation_v2 WHERE status!='DEPRECATED' ORDER BY from_label,to_label"
        ):
            statement = _relation_statement(row)
            haystack = statement.casefold()
            if needle and needle not in haystack and not any(term in haystack for term in terms):
                continue
            values.append({
                "id": row["id"], "title": f"{row['from_label']} → {row['to_label']}",
                "statement": statement,
                "status": "CONFIRMED" if row["status"] == "VERIFIED" else row["status"],
                "knowledge_type": "RELATION", "version": 1, "evidence_id": row["evidence_id"],
            })
        return values[:20]

    def _get_baseline_entity(self, knowledge_id: str) -> dict:
        row = self.db.execute("SELECT * FROM business_entity WHERE id=?", (knowledge_id,)).fetchone()
        from .knowledge_update.entry_anchor_service import EntryAnchorService

        anchors = (
            EntryAnchorService(self.db).list_for_business(row["entity_type"], knowledge_id)
            if row["entity_type"] in {"FLOW", "CAPABILITY"} else []
        )
        business_relations = [dict(item) for item in self.db.execute(
            """SELECT * FROM business_relation_v2
                WHERE status!='DEPRECATED' AND (from_entity_id=? OR to_entity_id=?)""",
            (knowledge_id, knowledge_id),
        )]
        relation_values = [
            {
                "id": item["id"], "source_type": "BUSINESS", "source_id": knowledge_id,
                "relation_type": item["relation_type"], "target_type": "BUSINESS",
                "target_id": item["to_entity_id"] if item["from_entity_id"] == knowledge_id else item["from_entity_id"],
                "status": "CONFIRMED" if item["status"] == "VERIFIED" else item["status"],
                "confidence": item["confidence"], "evidence_ids": [item["evidence_id"]] if item["evidence_id"] else [],
            }
            for item in business_relations
        ]
        evidence_ids = [row["source_evidence_id"]]
        evidence_ids.extend(item["sourceEvidenceId"] for item in anchors if item.get("sourceEvidenceId"))
        evidence_ids.extend(item["evidence_id"] for item in relation_values if item.get("evidence_id"))
        evidence = self._load_evidence(evidence_ids)
        return {
            "knowledge": {
                "id": knowledge_id, "title": row["name"], "statement": _entity_statement(row),
                "status": "CONFIRMED" if row["status"] == "VERIFIED" else row["status"],
                "knowledge_type": row["entity_type"], "version": 1,
                "evidence_id": row["source_evidence_id"],
            },
            "relations": relation_values, "entryAnchors": anchors,
            "evidence": evidence, "reviews": [],
        }

    def _get_baseline_relation(self, knowledge_id: str) -> dict:
        row = self.db.execute("SELECT * FROM business_relation_v2 WHERE id=?", (knowledge_id,)).fetchone()
        evidence_ids = [row["evidence_id"]]
        return {
            "knowledge": {
                "id": knowledge_id, "title": f"{row['from_label']} → {row['to_label']}",
                "statement": _relation_statement(row),
                "status": "CONFIRMED" if row["status"] == "VERIFIED" else row["status"],
                "knowledge_type": "RELATION", "version": 1, "evidence_id": row["evidence_id"],
            },
            "relations": [], "entryAnchors": [], "evidence": self._load_evidence(evidence_ids), "reviews": [],
        }

    def _load_evidence(self, evidence_ids) -> list[dict]:
        values = list(dict.fromkeys(value for value in evidence_ids if value))
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        return [dict(item) for item in self.db.execute(
            f"SELECT * FROM evidence WHERE id IN ({marks})", tuple(values)
        )]


def _entity_statement(row) -> str:
    parts = [row["definition"]]
    aliases = json.loads(row["aliases_json"])
    attributes = json.loads(row["attributes_json"])
    if aliases:
        parts.append("别名：" + "、".join(aliases))
    if attributes:
        parts.append("结构化信息：" + json.dumps(attributes, ensure_ascii=False))
    return "\n".join(part for part in parts if part)


def _relation_statement(row) -> str:
    statement = f"{row['from_label']} {row['relation_type']} {row['to_label']}"
    if row["scope"]:
        statement += f"（范围：{row['scope']}）"
    return statement


def _search_terms(value: str) -> set[str]:
    terms = {token.casefold() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", value)}
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms

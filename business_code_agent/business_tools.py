from __future__ import annotations

import json
import re


class BusinessTools:
    """Read-only boundary for function-centred retrieval navigation."""

    def __init__(self, db, **_ignored):
        self.db = db

    def search_business_knowledge(self, query: str) -> list[dict]:
        if self.db.execute("SELECT 1 FROM business_entity WHERE status!='DEPRECATED' LIMIT 1").fetchone():
            return self._search_baseline(query)
        needle = query.strip().casefold()
        terms = _search_terms(query)
        values = []
        for row in self.db.execute("SELECT * FROM functional_knowledge WHERE status='ACTIVE' ORDER BY name"):
            analysis = self.db.execute("SELECT * FROM functional_analysis WHERE function_id=?", (row["id"],)).fetchone()
            flow = json.loads(analysis["flow_json"]) if analysis else []
            rules = json.loads(analysis["rules_json"]) if analysis else []
            document = {
                "name": row["name"], "aliases": json.loads(row["aliases_json"]),
                "tags": json.loads(row["tags_json"]), "summary": row["summary"],
                "scenarios": json.loads(row["scenarios_json"]),
            }
            anchors = [dict(item) for item in self.db.execute(
                "SELECT project_name,entry_type,class_name FROM functional_entry_anchor WHERE function_id=?",
                (row["id"],),
            )]
            links = [dict(item) for item in self.db.execute(
                "SELECT source_id,target_id,relation_type FROM functional_retrieval_link WHERE function_id=?",
                (row["id"],),
            )]
            tables = [dict(item) for item in self.db.execute(
                "SELECT table_name,purpose FROM functional_key_table WHERE function_id=?", (row["id"],)
            )]
            haystack = json.dumps([document, anchors, links, tables], ensure_ascii=False).casefold()
            if needle and needle not in haystack and not any(term in haystack for term in terms):
                continue
            statement = _statement(document, flow, rules)
            evidence_id = self.db.execute(
                """SELECT evidence_id FROM functional_retrieval_link
                    WHERE function_id=? AND evidence_id IS NOT NULL ORDER BY relation_type LIMIT 1""",
                (row["id"],),
            ).fetchone()
            values.append({
                "id": row["id"], "title": row["name"], "statement": statement,
                "status": "CONFIRMED",
                "knowledge_type": "FUNCTION_NAVIGATION", "version": 1,
                "evidence_id": evidence_id["evidence_id"] if evidence_id else None,
            })
            if len(values) >= 20:
                break
        return values

    def get_business_knowledge(self, knowledge_id: str) -> dict:
        if self.db.execute("SELECT 1 FROM business_entity WHERE id=?", (knowledge_id,)).fetchone():
            return self._get_baseline_entity(knowledge_id)
        if self.db.execute("SELECT 1 FROM business_relation_v2 WHERE id=?", (knowledge_id,)).fetchone():
            return self._get_baseline_relation(knowledge_id)
        row = self.db.execute("SELECT * FROM functional_knowledge WHERE id=? AND status='ACTIVE'", (knowledge_id,)).fetchone()
        if not row:
            raise KeyError(knowledge_id)
        analysis = self.db.execute("SELECT * FROM functional_analysis WHERE function_id=?", (knowledge_id,)).fetchone()
        flow = json.loads(analysis["flow_json"]) if analysis else []
        rules = json.loads(analysis["rules_json"]) if analysis else []
        document = {
            "name": row["name"], "aliases": json.loads(row["aliases_json"]),
            "tags": json.loads(row["tags_json"]), "summary": row["summary"],
            "scenarios": json.loads(row["scenarios_json"]),
        }
        links = [dict(item) for item in self.db.execute(
            "SELECT * FROM functional_retrieval_link WHERE function_id=?", (knowledge_id,)
        )]
        relations = [{
            "id": item["id"], "source_type": "BUSINESS", "source_id": knowledge_id,
            "relation_type": item["relation_type"], "target_type": item["target_type"],
            "target_id": item["target_id"], "status": "DERIVED",
            "confidence": 1.0 if item.get("evidence_id") else 0.8,
            "evidence_ids": [item["evidence_id"]] if item.get("evidence_id") else [],
        } for item in links if item["target_type"] == "CODE_SYMBOL"]
        evidence_ids = list(dict.fromkeys(item["evidence_id"] for item in links if item.get("evidence_id")))
        evidence = []
        if evidence_ids:
            marks = ",".join("?" for _ in evidence_ids)
            evidence = [dict(item) for item in self.db.execute(
                f"SELECT * FROM evidence WHERE id IN ({marks})", tuple(evidence_ids)
            )]
        return {
            "knowledge": {
                "id": knowledge_id, "title": row["name"], "statement": _statement(document, flow, rules),
                "status": "CONFIRMED",
                "knowledge_type": "FUNCTION_NAVIGATION", "version": 1,
                "evidence_id": evidence_ids[0] if evidence_ids else None,
            },
            "relations": relations, "evidence": evidence, "reviews": [],
        }

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

    def _baseline_mappings(self, business_type: str, business_id: str) -> list[dict]:
        values = []
        known = set()
        for item in self.db.execute(
            """SELECT * FROM business_code_mapping
                WHERE business_type=? AND business_id=? AND status IN ('VERIFIED','CANDIDATE')
                ORDER BY confidence DESC""",
            (business_type, business_id),
        ):
            evidence_ids = json.loads(item["evidence_ids_json"])
            code_evidence_id = self._code_evidence_id(evidence_ids)
            values.append({
                "id": item["id"], "source_type": "BUSINESS", "source_id": business_id,
                "relation_type": item["relation_type"], "target_type": "CODE_SYMBOL",
                "target_id": item["code_symbol_id"],
                "qualified_name": item["code_reference"],
                "status": "DERIVED" if item["status"] == "VERIFIED" else "CANDIDATE",
                "confidence": item["confidence"],
                "evidence_id": code_evidence_id,
                "evidence_ids": evidence_ids,
            })
            known.add((item["code_symbol_id"], item["code_reference"]))
        # Query observations are navigation hints until an administrator
        # confirms them.  They are intentionally labelled CANDIDATE so the
        # query agent cannot turn them into answer facts without evidence.
        for item in self.db.execute(
            """SELECT * FROM business_code_mapping_observation
                WHERE business_type=? AND business_id=? AND status='CANDIDATE'
                ORDER BY confidence DESC""",
            (business_type, business_id),
        ):
            key = (item["code_symbol_id"], item["code_reference"])
            if key in known:
                continue
            evidence_ids = json.loads(item["evidence_ids_json"] or "[]")
            code_evidence_id = self._code_evidence_id(evidence_ids)
            values.append({
                "id": item["id"], "source_type": "BUSINESS", "source_id": business_id,
                "relation_type": item["relation_type"], "target_type": "CODE_SYMBOL",
                "target_id": item["code_symbol_id"], "qualified_name": item["code_reference"],
                "status": "CANDIDATE", "confidence": item["confidence"],
                "evidence_id": code_evidence_id,
                "evidence_ids": evidence_ids, "observation_id": item["id"],
            })
            known.add(key)
        return values

    def _code_evidence_id(self, evidence_ids) -> str | None:
        values = list(dict.fromkeys(str(value) for value in evidence_ids if value))
        if not values:
            return None
        marks = ",".join("?" for _ in values)
        code_ids = {
            row["id"] for row in self.db.execute(
                f"SELECT id FROM evidence WHERE id IN ({marks}) AND source_type='CODE'",
                tuple(values),
            )
        }
        return next((value for value in values if value in code_ids), None)

    def _load_evidence(self, evidence_ids) -> list[dict]:
        values = list(dict.fromkeys(value for value in evidence_ids if value))
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        return [dict(item) for item in self.db.execute(
            f"SELECT * FROM evidence WHERE id IN ({marks})", tuple(values)
        )]


def _statement(document: dict, flow: list[dict], rules: list[dict]) -> str:
    parts = [document["summary"]]
    if document["tags"]:
        parts.append("标签：" + "；".join(document["tags"]))
    if document["scenarios"]:
        parts.append("业务场景：" + "；".join(document["scenarios"]))
    if flow:
        parts.append("检索导航流程：" + "；".join(item["statement"] for item in flow))
    if rules:
        parts.append("待代码核实规则：" + "；".join(item["statement"] for item in rules))
    return "\n".join(parts)


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

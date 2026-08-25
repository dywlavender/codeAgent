from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..util import digest, dumps, stable_id


class RequirementRepository:
    def __init__(self, db):
        self.db = db

    def next_id(self) -> str:
        year = datetime.now(timezone.utc).year
        prefix = f"REQ-{year}-"
        largest = 0
        for row in self.db.execute("SELECT id FROM requirement WHERE id LIKE ?", (prefix + "%",)):
            suffix = row["id"].removeprefix(prefix)
            if suffix.isdigit():
                largest = max(largest, int(suffix))
        return f"{prefix}{largest + 1:03d}"

    def next_version(self, requirement_id: str) -> int:
        row = self.db.execute("SELECT max(version) FROM requirement_version WHERE requirement_id=?", (requirement_id,)).fetchone()
        return int(row[0] or 0) + 1

    def save(self, requirement_id: str, parsed: dict, chunks, digest_value, *, version: int) -> str:
        now = datetime.now(timezone.utc).isoformat()
        version_id = f"{requirement_id}-V{version}"
        content_hash = digest(parsed["original"])
        existing = self.db.execute("SELECT id FROM requirement_version WHERE requirement_id=? AND content_hash=?", (requirement_id, content_hash)).fetchone()
        if existing:
            return existing["id"]
        previous_root = self.db.execute("SELECT created_at FROM requirement WHERE id=?", (requirement_id,)).fetchone()
        self.db.execute(
            """INSERT OR REPLACE INTO requirement
               (id,title,source_path,version,content_hash,status,current_version,created_at,updated_at)
               VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)""",
            (requirement_id, parsed["title"], parsed["source_path"], str(version), content_hash, version,
             previous_root["created_at"] if previous_root and previous_root["created_at"] else now, now),
        )
        self.db.execute(
            "INSERT INTO requirement_version VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (version_id, requirement_id, version, parsed["source_path"], parsed["source_type"], content_hash, parsed["original"], now),
        )
        self.db.execute(
            "INSERT INTO requirement_digest_v2 VALUES (?, ?, ?, ?)",
            (version_id, digest_value.business_goal, digest_value.background, dumps(digest_value.to_dict())),
        )
        for chunk in chunks:
            evidence_id = stable_id("EV", "REQUIREMENT", requirement_id, version_id, chunk.id, chunk.content)
            self.db.execute(
                """INSERT INTO evidence
                   (id,source_type,source_id,source_version,locator,line_start,line_end,chunk_id,content_hash,excerpt)
                   VALUES (?, 'REQUIREMENT', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, requirement_id, version_id, parsed["source_path"], chunk.paragraph_start, chunk.paragraph_end, chunk.id, digest(chunk.content), chunk.content),
            )
            self.db.execute("INSERT INTO evidence_lifecycle VALUES (?, 'ACTIVE', NULL, NULL, NULL)", (evidence_id,))
            self.db.execute(
                "INSERT INTO requirement_chunk_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk.id, version_id, dumps(chunk.section_path), chunk.sequence, chunk.content, digest(chunk.content), chunk.page,
                 chunk.paragraph_start, chunk.paragraph_end, chunk.start_offset, chunk.end_offset, evidence_id),
            )
            self.db.execute(
                "INSERT INTO requirement_chunk_fts (requirement_id,requirement_version_id,chunk_id,section_path,content) VALUES (?, ?, ?, ?, ?)",
                (requirement_id, version_id, chunk.id, _fts_text(" / ".join(chunk.section_path)), _fts_text(chunk.content)),
            )
        chunk_map = {chunk.id: chunk for chunk in chunks}
        for rule in digest_value.business_rules:
            self.db.execute(
                "INSERT INTO requirement_rule VALUES (?, ?, ?, ?, ?, ?, 'DERIVED')",
                (f"{version_id}-{rule.id.rsplit('-', 1)[-1]}", version_id, rule.statement, dumps(rule.business_objects), dumps(rule.conditions), rule.result),
            )
            rule_id = f"{version_id}-{rule.id.rsplit('-', 1)[-1]}"
            for chunk_id in rule.evidence_chunk_ids:
                chunk = chunk_map[chunk_id]
                local_start = chunk.content.find(rule.statement)
                self.db.execute(
                    "INSERT INTO requirement_evidence VALUES ('BUSINESS_RULE', ?, ?, ?, ?)",
                    (rule_id, chunk_id, local_start, local_start + len(rule.statement)),
                )
        tags = [*digest_value.business_objects, *digest_value.affected_processes, *digest_value.affected_systems, *digest_value.fields, *digest_value.tables, *digest_value.keywords]
        rules = " ".join(rule.statement for rule in digest_value.business_rules)
        self.db.execute(
            "INSERT INTO requirement_fts (requirement_id,requirement_version_id,title,digest,rules,tags) VALUES (?, ?, ?, ?, ?, ?)",
            (requirement_id, version_id, _fts_text(digest_value.title), _fts_text(dumps(digest_value.to_dict())), _fts_text(rules), _fts_text(" ".join(tags))),
        )
        # Legacy current-version views remain available to the existing M4 code.
        self.db.execute("INSERT OR REPLACE INTO requirement_digest VALUES (?, ?)", (requirement_id, dumps(digest_value.to_dict())))
        self.db.execute("DELETE FROM requirement_chunk WHERE requirement_id=?", (requirement_id,))
        for chunk in chunks:
            evidence_id = self.db.execute("SELECT evidence_id FROM requirement_chunk_v2 WHERE id=?", (chunk.id,)).fetchone()[0]
            self.db.execute("INSERT INTO requirement_chunk VALUES (?, ?, ?, ?, ?)", (chunk.id, requirement_id, chunk.sequence, chunk.content, evidence_id))
        return version_id

    def get_digest(self, requirement_id: str, version: int | None = None) -> dict:
        version_row = self._version(requirement_id, version)
        row = self.db.execute("SELECT digest_json FROM requirement_digest_v2 WHERE requirement_version_id=?", (version_row["id"],)).fetchone()
        if not row:
            raise KeyError(requirement_id)
        value = json.loads(row["digest_json"])
        value["version"] = version_row["version"]
        value["versionId"] = version_row["id"]
        return value

    def get(self, requirement_id: str, version: int | None = None) -> dict:
        root = self.db.execute("SELECT * FROM requirement WHERE id=?", (requirement_id,)).fetchone()
        if not root:
            raise KeyError(requirement_id)
        version_row = self._version(requirement_id, version)
        digest_value = self.get_digest(requirement_id, version_row["version"])
        relations = [dict(row) for row in self.db.execute("SELECT * FROM requirement_relation WHERE requirement_version_id=? ORDER BY status, confidence DESC", (version_row["id"],))]
        version_metadata = dict(version_row)
        version_metadata.pop("original_text", None)
        return {"requirement": dict(root), "version": version_metadata, "digest": digest_value, "relations": relations}

    def read_chunk(self, requirement_id: str, chunk_id: str) -> dict:
        row = self.db.execute(
            """SELECT c.*, rv.requirement_id FROM requirement_chunk_v2 c
                 JOIN requirement_version rv ON rv.id=c.requirement_version_id
                WHERE rv.requirement_id=? AND c.id=?""",
            (requirement_id, chunk_id),
        ).fetchone()
        if not row:
            raise KeyError(chunk_id)
        value = dict(row)
        value["section_path"] = json.loads(value.pop("section_path_json"))
        return value

    def rules(self, requirement_id: str, version: int | None = None) -> list[dict]:
        version_row = self._version(requirement_id, version)
        values = []
        for row in self.db.execute("SELECT * FROM requirement_rule WHERE requirement_version_id=? ORDER BY id", (version_row["id"],)):
            item = dict(row)
            item["evidence"] = [dict(value) for value in self.db.execute("SELECT * FROM requirement_evidence WHERE fact_type='BUSINESS_RULE' AND fact_id=?", (row["id"],))]
            values.append(item)
        return values

    def search(self, query: str, limit: int = 20) -> list[dict]:
        expression = _fts_expression(query)
        if not expression:
            rows = self.db.execute("SELECT id,title,current_version,status,updated_at FROM requirement ORDER BY updated_at DESC LIMIT ?", (limit,))
        else:
            rows = self.db.execute(
                """SELECT DISTINCT r.id,r.title,r.current_version,r.status,r.updated_at
                     FROM requirement_fts f JOIN requirement r ON r.id=f.requirement_id
                    WHERE requirement_fts MATCH ? AND f.requirement_version_id=(r.id || '-V' || r.current_version)
                    ORDER BY rank LIMIT ?""",
                (expression, limit),
            )
        return [dict(row) for row in rows]

    def search_chunks(self, requirement_id: str, query: str, limit: int = 5) -> list[dict]:
        current = self._version(requirement_id, None)
        rows = self.db.execute(
            """SELECT c.id,c.section_path_json,c.sequence,c.evidence_id
                 FROM requirement_chunk_fts f JOIN requirement_chunk_v2 c ON c.id=f.chunk_id
                WHERE f.requirement_id=? AND f.requirement_version_id=?
                  AND requirement_chunk_fts MATCH ? ORDER BY rank LIMIT ?""",
            (requirement_id, current["id"], _fts_expression(query), limit),
        )
        return [{**dict(row), "section_path": json.loads(row["section_path_json"])} for row in rows]

    def history(self, requirement_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT id,requirement_id,version,source_file,source_type,content_hash,created_at
                 FROM requirement_version WHERE requirement_id=? ORDER BY version""",
            (requirement_id,),
        )]

    def changes(self, requirement_id: str) -> list[dict]:
        results = []
        for row in self.db.execute("SELECT * FROM requirement_version_change WHERE requirement_id=? ORDER BY created_at", (requirement_id,)):
            item = dict(row)
            for key in ("added_rules_json", "removed_rules_json", "changed_rules_json", "affected_knowledge_json", "affected_code_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            results.append(item)
        return results

    def _version(self, requirement_id: str, version: int | None):
        if version is None:
            row = self.db.execute("SELECT rv.* FROM requirement_version rv JOIN requirement r ON r.id=rv.requirement_id WHERE r.id=? AND rv.version=r.current_version", (requirement_id,)).fetchone()
        else:
            row = self.db.execute("SELECT * FROM requirement_version WHERE requirement_id=? AND version=?", (requirement_id, version)).fetchone()
        if not row:
            raise KeyError(f"{requirement_id} version {version or 'current'}")
        return row


def _fts_text(value: str) -> str:
    additions = []
    for run in re.findall(r"[一-鿿]+", value):
        additions.extend(run)
        additions.extend(run[index:index + 2] for index in range(len(run) - 1))
    return value + " " + " ".join(additions)


def _fts_expression(query: str) -> str:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[一-鿿]+", query)
    return " AND ".join(f'"{term}"' for term in terms)

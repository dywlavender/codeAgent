from __future__ import annotations

import json
import re
from sqlite3 import Connection

from .util import digest, tokens


class EvidenceTools:
    def __init__(self, db: Connection):
        self.db = db

    def find_field(self, field: str, fact_types: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" for _ in fact_types)
        rows = self.db.execute(
            f"""SELECT cf.fact_type, cf.subject, cf.target, cs.qualified_name,
                       e.id evidence_id, e.locator, e.line_start, e.excerpt
                FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                JOIN evidence e ON e.id=cf.evidence_id
                WHERE lower(cf.subject)=lower(?) AND cf.fact_type IN ({marks})
                ORDER BY e.locator, e.line_start""",
            (field, *fact_types),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def normalize_field(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def find_field_activity(self, field: str, fact_types: tuple[str, ...] | None = None) -> list[dict]:
        """Find Java field and SQL column activity through a common name key."""
        allowed = fact_types or (
            "READ_FIELD", "WRITE_FIELD", "CHECK_FIELD", "READ_COLUMN", "WRITE_COLUMN",
        )
        marks = ",".join("?" for _ in allowed)
        rows = self.db.execute(
            f"""SELECT cf.fact_type, cf.subject, cf.target, cs.id symbol_id,
                       cs.qualified_name, e.id evidence_id, e.locator,
                       e.line_start, e.line_end, e.excerpt
                  FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                  JOIN evidence e ON e.id=cf.evidence_id
                 WHERE cf.fact_type IN ({marks})
                 ORDER BY e.locator, e.line_start""",
            allowed,
        )
        key = self.normalize_field(field)
        return [
            dict(row) for row in rows
            if self.normalize_field(row["subject"]) == key or self.normalize_field(row["target"]) == key
        ]

    def field_names(self) -> list[str]:
        names: dict[str, str] = {}
        for row in self.db.execute("SELECT name FROM code_symbol WHERE kind='FIELD'"):
            names.setdefault(self.normalize_field(row["name"]), row["name"])
        for row in self.db.execute(
            "SELECT subject, target FROM code_fact WHERE fact_type IN ('READ_FIELD','WRITE_FIELD','CHECK_FIELD','READ_COLUMN','WRITE_COLUMN')"
        ):
            names.setdefault(self.normalize_field(row["subject"]), row["subject"])
            if row["target"] and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", row["target"]):
                names.setdefault(self.normalize_field(row["target"]), row["target"])
        return sorted(names.values(), key=str.lower)

    def fact_types_for_evidence(self, evidence_ids: list[str]) -> set[str]:
        if not evidence_ids:
            return set()
        marks = ",".join("?" for _ in evidence_ids)
        return {
            row["fact_type"] for row in self.db.execute(
                f"SELECT DISTINCT fact_type FROM code_fact WHERE evidence_id IN ({marks})",
                evidence_ids,
            )
        }

    def find_field_reads(self, field: str) -> list[dict]:
        return self.find_field(field, ("READ_FIELD",))

    def find_field_writes(self, field: str) -> list[dict]:
        return self.find_field(field, ("WRITE_FIELD",))

    def find_field_checks(self, field: str) -> list[dict]:
        return self.find_field(field, ("CHECK_FIELD",))

    def search_symbol(self, query: str) -> list[dict]:
        pattern = f"%{query.lower()}%"
        return [dict(row) for row in self.db.execute(
            "SELECT id, kind, qualified_name, line_start, line_end FROM code_symbol WHERE lower(qualified_name) LIKE ? ORDER BY qualified_name",
            (pattern,),
        )]

    def search_code(self, query: str, limit: int = 50) -> list[dict]:
        """Search indexed symbol/fact summaries without reading full source files."""
        terms = [term.lower() for term in tokens(query)]
        rows = self.db.execute(
            """SELECT cs.id, cs.kind, cs.qualified_name,
                      group_concat(coalesce(cf.fact_type,'') || ':' || coalesce(cf.subject,''), ' ') summary
                 FROM code_symbol cs LEFT JOIN code_fact cf ON cf.symbol_id=cs.id
                GROUP BY cs.id ORDER BY cs.qualified_name"""
        )
        results = []
        for row in rows:
            haystack = f"{row['qualified_name']} {row['summary'] or ''}".lower()
            if any(term in haystack for term in terms):
                results.append(dict(row))
        return results[:limit]

    def get_repo_map(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT cf.path, count(DISTINCT cs.id) symbols, count(f.id) facts
                 FROM code_file cf LEFT JOIN code_symbol cs ON cs.file_id=cf.id
                 LEFT JOIN code_fact f ON f.symbol_id=cs.id GROUP BY cf.path ORDER BY cf.path"""
        )]

    def read_source(self, symbol_id: str) -> dict:
        row = self.db.execute(
            """SELECT cs.qualified_name, cs.line_start, cs.line_end, cf.path, r.root_path
                 FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                 JOIN repository r ON r.id=cf.repository_id WHERE cs.id=?""",
            (symbol_id,),
        ).fetchone()
        if not row:
            raise KeyError(symbol_id)
        from pathlib import Path
        lines = (Path(row["root_path"]) / row["path"]).read_text(encoding="utf-8").splitlines()
        return {**dict(row), "content": "\n".join(lines[row["line_start"] - 1:row["line_end"]])}

    def get_symbol_relations(self, symbol_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT fact_type, subject, target, evidence_id FROM code_fact WHERE symbol_id=? ORDER BY fact_type, subject",
            (symbol_id,),
        )]

    def has_direct_call(self, source_symbol_id: str, target_symbol_id: str) -> bool:
        """Return only a directly indexed CALL edge; absence is scoped to this index."""
        target = self.db.execute("SELECT name, qualified_name FROM code_symbol WHERE id=?", (target_symbol_id,)).fetchone()
        if not target:
            raise KeyError(target_symbol_id)
        row = self.db.execute(
            """SELECT 1 FROM code_fact WHERE symbol_id=? AND fact_type='CALL'
                 AND (lower(subject)=lower(?) OR lower(target)=lower(?) OR lower(target)=lower(?)) LIMIT 1""",
            (source_symbol_id, target["name"], target["name"], target["qualified_name"]),
        ).fetchone()
        return row is not None

    def validate_evidence_integrity(self, evidence_ids: list[str]) -> list[dict]:
        results = []
        for evidence_id in dict.fromkeys(evidence_ids):
            item = self.evidence(evidence_id)
            actual_hash = digest(item["excerpt"])
            live_valid, live_detail = self._validate_live_evidence(item)
            results.append({
                "evidence_id": evidence_id,
                "valid": actual_hash == item["content_hash"] and live_valid,
                "stored_hash_valid": actual_hash == item["content_hash"],
                "live_source_valid": live_valid,
                "live_source_detail": live_detail,
                "expected_hash": item["content_hash"],
                "actual_hash": actual_hash,
            })
        return results

    def _validate_live_evidence(self, item: dict) -> tuple[bool, str]:
        from pathlib import Path

        if item["source_type"] == "CODE":
            row = self.db.execute(
                """SELECT r.root_path, cf.path FROM code_file cf
                     JOIN repository r ON r.id=cf.repository_id WHERE cf.id=?""",
                (item["source_id"],),
            ).fetchone()
            if not row:
                return False, "code_file provenance missing"
            path = Path(row["root_path"]) / row["path"]
            if not path.is_file():
                return False, f"source file missing: {path}"
            lines = path.read_text(encoding="utf-8").splitlines()
            start = (item["line_start"] or 1) - 1
            end = item["line_end"] or item["line_start"] or 1
            live = "\n".join(lines[start:end]).strip()
            normalized_live = " ".join(live.split())
            normalized_excerpt = " ".join(item["excerpt"].split())
            return normalized_excerpt in normalized_live, str(path)
        if item["source_type"] == "REQUIREMENT":
            row = self.db.execute("SELECT source_path FROM requirement WHERE id=?", (item["source_id"],)).fetchone()
            if not row or not Path(row["source_path"]).is_file():
                return False, "requirement source missing"
            version = self.db.execute(
                "SELECT source_file,content_hash FROM requirement_version WHERE id=? AND requirement_id=?",
                (item["source_version"], item["source_id"]),
            ).fetchone()
            if version:
                try:
                    from .requirement.ingestion import RequirementDocumentParser
                    parsed = RequirementDocumentParser().parse(version["source_file"])
                except (OSError, ValueError):
                    return False, "versioned requirement source unreadable"
                source_valid = digest(parsed["original"]) == version["content_hash"]
                normalized_original = " ".join(parsed["original"].split())
                normalized_excerpt = " ".join(item["excerpt"].split())
                return source_valid and normalized_excerpt in normalized_original, version["source_file"]
            try:
                payload = json.loads(Path(row["source_path"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False, "requirement source unreadable"
            return item["excerpt"] in payload.get("original", ""), row["source_path"]
        if item["source_type"] == "MANUAL":
            path = Path(item["locator"])
            if not path.is_file():
                return False, f"business source missing: {path}"
            raw = path.read_text(encoding="utf-8")
            try:
                raw = json.loads(raw).get("statement", raw)
            except json.JSONDecodeError:
                pass
            return item["excerpt"] in raw, str(path)
        return False, f"unsupported source type: {item['source_type']}"

    def find_table(self, table: str, writes: bool = False) -> list[dict]:
        kinds = ("WRITE_TABLE", "WRITE_COLUMN") if writes else ("READ_TABLE", "READ_COLUMN")
        return self._find_subject(table, kinds)

    def find_table_reads(self, table_or_column: str) -> list[dict]:
        return self._find_subject(table_or_column, ("READ_TABLE", "READ_COLUMN"))

    def find_table_writes(self, table_or_column: str) -> list[dict]:
        return self._find_subject(table_or_column, ("WRITE_TABLE", "WRITE_COLUMN"))

    def _find_subject(self, subject: str, fact_types: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" for _ in fact_types)
        return [dict(row) for row in self.db.execute(
            f"""SELECT cf.*, cs.qualified_name FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                  WHERE lower(cf.subject)=lower(?) AND cf.fact_type IN ({marks})""",
            (subject, *fact_types),
        )]

    def get_requirement_digest(self, requirement_id: str) -> dict:
        row = self.db.execute("SELECT digest_json FROM requirement_digest WHERE requirement_id=?", (requirement_id,)).fetchone()
        if not row:
            raise KeyError(requirement_id)
        return json.loads(row[0])

    def read_requirement_chunk(self, requirement_id: str, chunk_id: str) -> dict:
        row = self.db.execute(
            "SELECT id, content, evidence_id FROM requirement_chunk WHERE requirement_id=? AND id=?",
            (requirement_id, chunk_id),
        ).fetchone()
        if not row:
            raise KeyError(chunk_id)
        return dict(row)

    def get_business_knowledge(self, knowledge_id: str) -> dict:
        from .business_tools import BusinessTools

        return BusinessTools(self.db).get_business_knowledge(knowledge_id)["knowledge"]

    def business_knowledge_by_evidence(self, evidence_id: str) -> dict:
        from .business_tools import BusinessTools

        tools = BusinessTools(self.db)
        for row in tools._search_published_functions(""):
            try:
                detail = tools.get_business_knowledge(row["id"])
            except KeyError:
                continue
            if evidence_id in {item.get("id") for item in detail.get("evidence", [])}:
                return detail["knowledge"]
        raise KeyError(evidence_id)

    def find_related_knowledge(self, source_id: str) -> list[dict]:
        from .business_tools import BusinessTools

        return BusinessTools(self.db).find_related_code(source_id)

    def search_requirements(self, query: str) -> list[dict]:
        terms = [term.lower() for term in tokens(query)]
        results = []
        for row in self.db.execute("SELECT r.id, r.title, d.digest_json FROM requirement r JOIN requirement_digest d ON d.requirement_id=r.id"):
            haystack = f"{row['title']} {row['digest_json']}".lower()
            if any(term in haystack for term in terms):
                results.append({"id": row["id"], "title": row["title"], "digest": json.loads(row["digest_json"])})
        return results

    def read_requirement_chunks(self, requirement_id: str, query: str) -> list[dict]:
        terms = list(dict.fromkeys(term.lower() for term in tokens(query)))
        rows = self.db.execute(
            "SELECT c.id, c.content, c.evidence_id FROM requirement_chunk c WHERE c.requirement_id=? ORDER BY c.ordinal",
            (requirement_id,),
        )
        scored = []
        for row in rows:
            content = row["content"].lower()
            score = sum(1 for term in terms if term in content)
            if score:
                scored.append((score, dict(row)))
        if not scored:
            return []
        best = max(score for score, _ in scored)
        return [row for score, row in scored if score == best]

    def search_business(self, query: str, include_suggested: bool = False) -> list[dict]:
        # ``include_suggested`` remains accepted for source compatibility; the
        # public query surface intentionally exposes published functions only.
        from .business_tools import BusinessTools

        return BusinessTools(self.db).search_business_knowledge(query)

    def evidence(self, evidence_id: str) -> dict:
        row = self.db.execute(
            """SELECT e.*, coalesce(el.status, 'ACTIVE') lifecycle_status,
                      el.superseded_at, el.trigger_type, el.trigger_id
                 FROM evidence e LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                WHERE e.id=?""",
            (evidence_id,),
        ).fetchone()
        if not row:
            raise KeyError(evidence_id)
        return dict(row)

    def evidence_history(self, source_type: str, source_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT e.*, coalesce(el.status, 'ACTIVE') lifecycle_status,
                      el.superseded_at, el.trigger_type, el.trigger_id
                 FROM evidence e LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                WHERE e.source_type=? AND e.source_id=?
                ORDER BY CASE coalesce(el.status, 'ACTIVE') WHEN 'ACTIVE' THEN 0 ELSE 1 END,
                         e.source_version DESC, e.line_start""",
            (source_type, source_id),
        )]

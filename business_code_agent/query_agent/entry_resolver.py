from __future__ import annotations

from pathlib import PurePosixPath
from sqlite3 import Connection


class EntryResolver:
    """Resolve a durable entry name against the current indexed code only."""

    def __init__(self, db: Connection):
        self.db = db

    def resolve(self, application_id: str, entry_name: str) -> dict:
        application_id = str(application_id or "").strip()
        entry_name = str(entry_name or "").strip()
        if not application_id or not entry_name:
            return self._result("NOT_FOUND", application_id, entry_name, [])
        rows = [dict(row) for row in self.db.execute(
            """SELECT DISTINCT s.id symbol_id,s.kind,s.name,s.qualified_name,
                              s.file_id,cf.path,cf.repository_id,
                              a.id application_id,a.name application_name,a.system_id,
                              ss.name system_name,
                              (SELECT cf2.evidence_id FROM code_fact cf2
                                WHERE cf2.symbol_id=s.id
                                ORDER BY CASE WHEN cf2.fact_type='CODE_DECLARATION' THEN 1 ELSE 0 END,
                                         cf2.evidence_id LIMIT 1) evidence_id
                              ,(SELECT cf2.fact_type FROM code_fact cf2
                                WHERE cf2.symbol_id=s.id
                                ORDER BY CASE WHEN cf2.fact_type='CODE_DECLARATION' THEN 1 ELSE 0 END,
                                         cf2.evidence_id LIMIT 1) fact_type
                 FROM code_symbol s
                 JOIN code_file cf ON cf.id=s.file_id
                 JOIN application_code_file acf ON acf.file_id=s.file_id
                 JOIN application a ON a.id=acf.application_id
                 LEFT JOIN software_system ss ON ss.id=a.system_id
                WHERE a.id=? AND a.status='ACTIVE'
                ORDER BY s.qualified_name,s.line_start""",
            (application_id,),
        )]
        exact_symbol = [row for row in rows if row["name"].casefold() == entry_name.casefold()]
        exact_file = [
            row for row in rows
            if PurePosixPath(str(row["path"]).replace("\\", "/")).name.casefold() == entry_name.casefold()
        ]
        qualified_suffix = [
            row for row in rows
            if str(row["qualified_name"]).casefold().endswith("." + entry_name.casefold())
        ]
        # Prefer a class/method symbol with an exact name, then a web module
        # whose file name is the authored Page/Component anchor. Deduplicate a
        # symbol that satisfies both tests.
        selected = _prefer_entry_symbols(exact_symbol or exact_file or qualified_suffix)
        status = "NOT_FOUND" if not selected else "RESOLVED" if len(selected) == 1 else "MULTIPLE"
        return self._result(status, application_id, entry_name, selected)

    def resolve_anchor(self, anchor: dict) -> dict:
        return self.resolve(anchor.get("applicationId", ""), anchor.get("entryName", ""))

    def _result(self, status: str, application_id: str, entry_name: str, rows: list[dict]) -> dict:
        return {
            "status": status,
            "applicationId": application_id,
            "entryName": entry_name,
            "symbolIds": [row["symbol_id"] for row in rows],
            "symbols": [
                {
                    "symbolId": row["symbol_id"], "kind": row["kind"],
                    "name": row["name"], "qualifiedName": row["qualified_name"],
                    "file": row["path"], "repositoryId": row["repository_id"],
                    "applicationName": row.get("application_name"),
                    "systemId": row.get("system_id"), "systemName": row.get("system_name"),
                    "evidenceId": row.get("evidence_id"),
                    "factType": row.get("fact_type"),
                }
                for row in rows
            ],
        }


def _unique_rows(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        symbol_id = str(row["symbol_id"])
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        result.append(row)
    return result


def _prefer_entry_symbols(rows: list[dict]) -> list[dict]:
    """Drop Java constructors/methods when a class or web module is exact.

    Indexers represent a Java constructor as a method with the same short
    name as its class, and a web file also owns both a PAGE module and handler
    methods.  An authored entry name means the enclosing entry object, so
    prefer those kinds while still returning MULTIPLE when two real entry
    objects remain in the same application.
    """
    unique = _unique_rows(rows)
    preferred_kinds = {"CLASS", "INTERFACE", "PAGE", "COMPONENT", "JOB", "CONSUMER", "ENTRY_CLASS"}
    preferred = [row for row in unique if str(row.get("kind") or "").upper() in preferred_kinds]
    return preferred or unique

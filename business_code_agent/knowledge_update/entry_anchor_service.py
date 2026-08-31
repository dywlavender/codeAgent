from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Mapping

from ..util import stable_id


ENTRY_BUSINESS_TYPES = {"FLOW", "CAPABILITY"}
ENTRY_TYPES = {"PAGE", "CONTROLLER", "JOB", "CONSUMER", "ENTRY_CLASS", "OTHER"}
ENTRY_SOURCE_TYPES = {"HUMAN", "AI_CANDIDATE"}
ENTRY_ACTIVE_STATUSES = {"ACTIVE", "VERIFIED"}


class EntryAnchorError(ValueError):
    """Invalid or ambiguous business entry anchor input."""


@dataclass(frozen=True)
class EntryAnchor:
    business_type: str
    business_id: str
    application_id: str
    entry_type: str
    entry_name: str
    source_type: str = "HUMAN"
    status: str = "ACTIVE"
    source_evidence_id: str | None = None


class EntryAnchorService:
    """CRUD and validation for durable business investigation entry hints.

    The service never resolves a name to a code symbol. Resolution belongs to
    query-time runtime code and therefore does not create a durable dependency
    on a file, method or symbol identifier.
    """

    def __init__(self, db: Connection):
        self.db = db

    def list_for_business(
        self, business_type: str, business_id: str, *, include_candidates: bool = True
    ) -> list[dict]:
        business_type = self._business_type(business_type)
        statuses = ("ACTIVE", "VERIFIED", "CANDIDATE") if include_candidates else tuple(ENTRY_ACTIVE_STATUSES)
        marks = ",".join("?" for _ in statuses)
        rows = self.db.execute(
            f"""SELECT ea.*,a.name application_name,ss.name system_name
                  FROM business_entry_anchor ea
                  JOIN application a ON a.id=ea.application_id
                  LEFT JOIN software_system ss ON ss.id=a.system_id
                 WHERE ea.business_type=? AND ea.business_id=?
                   AND ea.status IN ({marks})
                 ORDER BY a.name,ea.entry_type,ea.entry_name""",
            (business_type, business_id, *statuses),
        ).fetchall()
        return [self._dict(row) for row in rows]

    def get(self, anchor_id: str) -> dict:
        row = self.db.execute(
            """SELECT ea.*,a.name application_name,ss.name system_name
                 FROM business_entry_anchor ea
                 JOIN application a ON a.id=ea.application_id
                 LEFT JOIN software_system ss ON ss.id=a.system_id
                WHERE ea.id=?""",
            (anchor_id,),
        ).fetchone()
        if not row:
            raise KeyError(anchor_id)
        return self._dict(row)

    def list_all(self, *, include_candidates: bool = True) -> list[dict]:
        statuses = ("ACTIVE", "VERIFIED", "CANDIDATE") if include_candidates else tuple(ENTRY_ACTIVE_STATUSES)
        marks = ",".join("?" for _ in statuses)
        rows = self.db.execute(
            f"""SELECT ea.*,a.name application_name,ss.name system_name
                  FROM business_entry_anchor ea
                  JOIN application a ON a.id=ea.application_id
                  LEFT JOIN software_system ss ON ss.id=a.system_id
                 WHERE ea.status IN ({marks})
                 ORDER BY ea.updated_at DESC,ea.id""",
            statuses,
        ).fetchall()
        return [self._dict(row) for row in rows]

    def resolve_application(self, reference: str) -> dict | None:
        """Resolve a human application label to one configured application."""
        value = str(reference or "").strip()
        if not value:
            return None
        rows = self.db.execute(
            """SELECT a.id,a.name,a.system_id,ss.name system_name
                 FROM application a LEFT JOIN software_system ss ON ss.id=a.system_id
                WHERE a.status='ACTIVE'
                  AND (lower(a.id)=lower(?) OR lower(a.name)=lower(?))""",
            (value, value),
        ).fetchall()
        if len(rows) > 1:
            raise EntryAnchorError(f"应用入口名称匹配到多个 application: {value}")
        return dict(rows[0]) if rows else None

    def save(self, anchor: EntryAnchor, *, anchor_id: str | None = None) -> str:
        self.validate(anchor)
        now = datetime.now(timezone.utc).isoformat()
        anchor_id = anchor_id or stable_id(
            "BEA", anchor.business_type, anchor.business_id, anchor.application_id,
            anchor.entry_type, anchor.entry_name,
        )
        self.db.execute(
            """INSERT INTO business_entry_anchor(
                   id,business_type,business_id,application_id,entry_type,entry_name,
                   source_type,status,source_evidence_id,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(business_type,business_id,application_id,entry_type,entry_name)
               DO UPDATE SET source_type=excluded.source_type,status=excluded.status,
                   source_evidence_id=excluded.source_evidence_id,updated_at=excluded.updated_at""",
            (
                anchor_id, anchor.business_type, anchor.business_id, anchor.application_id,
                anchor.entry_type, anchor.entry_name, anchor.source_type, anchor.status,
                anchor.source_evidence_id, now, now,
            ),
        )
        return anchor_id

    def deprecate_for_business(self, business_id: str) -> None:
        self.db.execute(
            "UPDATE business_entry_anchor SET status='DEPRECATED',updated_at=? WHERE business_id=?",
            (datetime.now(timezone.utc).isoformat(), business_id),
        )

    @staticmethod
    def validate(anchor: EntryAnchor) -> None:
        business_type = str(anchor.business_type or "").strip().upper()
        if business_type not in ENTRY_BUSINESS_TYPES:
            raise EntryAnchorError("business entry anchor 只允许 FLOW 或 CAPABILITY")
        entry_type = str(anchor.entry_type or "").strip().upper()
        if entry_type not in ENTRY_TYPES:
            raise EntryAnchorError(f"entryType 必须是 {', '.join(sorted(ENTRY_TYPES))}")
        source_type = str(anchor.source_type or "").strip().upper()
        if source_type not in ENTRY_SOURCE_TYPES:
            raise EntryAnchorError("sourceType 必须是 HUMAN 或 AI_CANDIDATE")
        name = validate_entry_name(anchor.entry_name)
        if not str(anchor.business_id or "").strip() or not str(anchor.application_id or "").strip():
            raise EntryAnchorError("businessId 和 applicationId 不能为空")
        status = str(anchor.status or "").strip().upper()
        if source_type == "AI_CANDIDATE" and status in ENTRY_ACTIVE_STATUSES:
            raise EntryAnchorError("AI_CANDIDATE 入口不能直接生效")
        if not name:
            raise EntryAnchorError("entryName 不能为空")

    @staticmethod
    def _business_type(value: str) -> str:
        value = str(value or "").strip().upper()
        if value not in ENTRY_BUSINESS_TYPES:
            raise EntryAnchorError("businessType 只允许 FLOW 或 CAPABILITY")
        return value

    @staticmethod
    def _dict(row) -> dict:
        return {
            "id": row["id"],
            "businessType": row["business_type"],
            "businessId": row["business_id"],
            "applicationId": row["application_id"],
            "applicationName": row["application_name"],
            "systemName": row["system_name"],
            "entryType": row["entry_type"],
            "entryName": row["entry_name"],
            "sourceType": row["source_type"],
            "status": row["status"],
            "sourceEvidenceId": row["source_evidence_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


def validate_entry_name(value: object) -> str:
    """Allow a class/page/job name, never a package, method or file location."""
    name = str(value or "").strip()
    if not name or len(name) > 200:
        raise EntryAnchorError("entryName 不能为空且长度不能超过 200")
    if any(token in name for token in ("/", "\\", "::", "(", ")", ":", "\n", "\r")):
        raise EntryAnchorError(f"entryName 只能是入口名称，不能包含路径或方法签名: {name}")
    if "." in name and not name.lower().endswith((".vue", ".ts", ".tsx", ".js", ".jsx")):
        raise EntryAnchorError(f"entryName 不能保存限定类名或方法名: {name}")
    return name


def normalize_anchor_payload(raw: Mapping, *, default_source_type: str = "HUMAN") -> EntryAnchor:
    """Normalize model/deterministic payload keys without resolving code."""
    business_type = str(raw.get("businessType") or raw.get("business_type") or "").strip().upper()
    application_id = str(raw.get("applicationId") or raw.get("application_id") or raw.get("application") or "").strip()
    entry_type = str(raw.get("entryType") or raw.get("entry_type") or raw.get("type") or "").strip().upper()
    entry_name = str(raw.get("entryName") or raw.get("entry_name") or raw.get("name") or "").strip()
    source_type = str(raw.get("sourceType") or raw.get("source_type") or default_source_type).strip().upper()
    status = str(raw.get("status") or ("CANDIDATE" if source_type == "AI_CANDIDATE" else "ACTIVE")).strip().upper()
    return EntryAnchor(
        business_type, "", application_id, entry_type, validate_entry_name(entry_name),
        source_type, status, str(raw.get("sourceEvidenceId") or raw.get("source_evidence_id") or "") or None,
    )

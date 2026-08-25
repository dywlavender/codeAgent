from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from ..util import digest, dumps
from .models import FunctionSnapshot, FunctionStatus, ProposalAction, ProposalStatus


_REVIEW_TRANSITIONS = {
    "ACCEPT": ProposalStatus.APPROVED,
    "APPROVE": ProposalStatus.APPROVED,
    "REJECT": ProposalStatus.REJECTED,
    "DEFER": ProposalStatus.DEFERRED,
    "REQUEST_CHANGES": ProposalStatus.CHANGES_REQUESTED,
}


class KnowledgeGovernanceRepository:
    """Persists reviewed, versioned business-function knowledge.

    Code/document extraction may create proposals, but only an approved proposal
    can publish a new immutable function version.
    """

    def __init__(self, db):
        self.db = db

    def create_proposal(
        self,
        title: str,
        trigger_type: str,
        trigger_id: str,
        proposed_snapshot: FunctionSnapshot | dict[str, Any],
        created_by: str,
        *,
        target_function_id: str | None = None,
        action: str | ProposalAction = ProposalAction.CREATE,
        summary: str = "",
        base_version_id: str | None = None,
    ) -> dict:
        action = _enum_value(ProposalAction, action, "action")
        snapshot = _normalise_snapshot(proposed_snapshot)
        function_id = target_function_id or _new_id("BF")
        current = self.db.execute(
            "SELECT current_version_id FROM business_function WHERE id=?", (function_id,)
        ).fetchone()
        if action == ProposalAction.CREATE and current:
            raise ValueError(f"business function already exists: {function_id}")
        if action != ProposalAction.CREATE and not current:
            raise KeyError(function_id)
        if action != ProposalAction.CREATE:
            expected_base = current["current_version_id"]
            if base_version_id is None:
                base_version_id = expected_base
            if base_version_id != expected_base:
                raise ValueError("base version is not the current published version")
        proposal_id = _new_id("KUP")
        now = _now()
        self.db.execute(
            """INSERT INTO knowledge_update_proposal
               (id,title,summary,trigger_type,trigger_id,action,target_function_id,
                base_version_id,proposed_snapshot_json,status,created_by,created_at,updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal_id, title.strip(), summary.strip(), trigger_type, trigger_id,
             action, function_id, base_version_id, dumps(snapshot),
             ProposalStatus.DRAFT, created_by, now, now),
        )
        self.db.commit()
        return self.get_proposal(proposal_id)

    def add_proposal_item(
        self,
        proposal_id: str,
        item_type: str,
        target_type: str,
        *,
        before: Any = None,
        after: Any = None,
        target_id: str | None = None,
        rationale: str = "",
        confidence: float = 0.0,
        evidence_ids: Iterable[str] = (),
    ) -> dict:
        proposal = self._proposal_row(proposal_id)
        if proposal["status"] not in (ProposalStatus.DRAFT, ProposalStatus.CHANGES_REQUESTED):
            raise ValueError("proposal items can only change while draft or changes are requested")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        sequence = self.db.execute(
            "SELECT coalesce(max(sequence), 0) + 1 FROM knowledge_update_proposal_item WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()[0]
        item_id = _new_id("KUPI")
        self.db.execute(
            """INSERT INTO knowledge_update_proposal_item
               (id,proposal_id,sequence,item_type,target_type,target_id,before_json,after_json,rationale,confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, proposal_id, sequence, item_type, target_type, target_id,
             _optional_json(before), _optional_json(after), rationale, confidence),
        )
        for evidence_id in dict.fromkeys(evidence_ids):
            self.db.execute(
                "INSERT INTO proposal_item_evidence VALUES (?, ?)", (item_id, evidence_id)
            )
        self._touch(proposal_id)
        self.db.commit()
        return self._proposal_item(item_id)

    def replace_proposed_snapshot(
        self, proposal_id: str, snapshot: FunctionSnapshot | dict[str, Any]
    ) -> dict:
        proposal = self._proposal_row(proposal_id)
        if proposal["status"] not in (ProposalStatus.DRAFT, ProposalStatus.CHANGES_REQUESTED):
            raise ValueError("proposed snapshot can only change while editable")
        self.db.execute(
            "UPDATE knowledge_update_proposal SET proposed_snapshot_json=?, updated_at=? WHERE id=?",
            (dumps(_normalise_snapshot(snapshot)), _now(), proposal_id),
        )
        self.db.commit()
        return self.get_proposal(proposal_id)

    def submit_proposal(self, proposal_id: str) -> dict:
        proposal = self._proposal_row(proposal_id)
        if proposal["status"] not in (
            ProposalStatus.DRAFT, ProposalStatus.CHANGES_REQUESTED, ProposalStatus.DEFERRED
        ):
            raise ValueError("only editable proposals can be submitted")
        self.db.execute(
            "UPDATE knowledge_update_proposal SET status=?, updated_at=? WHERE id=?",
            (ProposalStatus.PENDING_REVIEW, _now(), proposal_id),
        )
        self.db.commit()
        return self.get_proposal(proposal_id)

    def review_proposal(
        self, proposal_id: str, decision: str, reviewer: str, comment: str = ""
    ) -> dict:
        proposal = self._proposal_row(proposal_id)
        if proposal["status"] != ProposalStatus.PENDING_REVIEW:
            raise ValueError("only pending proposals can be reviewed")
        try:
            new_status = _REVIEW_TRANSITIONS[decision]
        except KeyError as exc:
            raise ValueError(f"unsupported review decision: {decision}") from exc
        now = _now()
        self.db.execute(
            "INSERT INTO knowledge_proposal_review VALUES (?, ?, ?, ?, ?, ?)",
            (_new_id("KUR"), proposal_id, decision, reviewer, comment, now),
        )
        self.db.execute(
            """UPDATE knowledge_update_proposal
                  SET status=?, reviewed_by=?, reviewed_at=?, updated_at=? WHERE id=?""",
            (new_status, reviewer, now, now, proposal_id),
        )
        self.db.commit()
        return self.get_proposal(proposal_id)

    def publish_proposal(self, proposal_id: str, published_by: str) -> dict:
        proposal = self._proposal_row(proposal_id)
        if proposal["status"] != ProposalStatus.APPROVED:
            raise ValueError("only approved proposals can be published")
        function_id = proposal["target_function_id"]
        action = ProposalAction(proposal["action"])
        current = self.db.execute(
            "SELECT * FROM business_function WHERE id=?", (function_id,)
        ).fetchone()
        if action == ProposalAction.CREATE and current:
            raise ValueError(f"business function already exists: {function_id}")
        if action != ProposalAction.CREATE:
            if not current:
                raise KeyError(function_id)
            if current["current_version_id"] != proposal["base_version_id"]:
                raise ValueError("proposal base version is stale")

        snapshot = json.loads(proposal["proposed_snapshot_json"])
        now = _now()
        next_version = int(self.db.execute(
            "SELECT coalesce(max(version), 0) + 1 FROM business_function_version WHERE function_id=?",
            (function_id,),
        ).fetchone()[0])
        version_id = f"{function_id}-V{next_version}"
        function_status = FunctionStatus.RETIRED if action == ProposalAction.RETIRE else FunctionStatus.PUBLISHED

        try:
            self.db.execute("BEGIN")
            if current:
                self.db.execute(
                    """UPDATE business_function SET name=?,domain=?,summary=?,status=?,
                       current_version_id=?,updated_at=? WHERE id=?""",
                    (snapshot["name"], snapshot["domain"], snapshot["summary"],
                     function_status, version_id, now, function_id),
                )
            else:
                self.db.execute(
                    """INSERT INTO business_function
                       (id,name,domain,summary,status,current_version_id,created_by,created_at,updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (function_id, snapshot["name"], snapshot["domain"], snapshot["summary"],
                     function_status, version_id, proposal["created_by"], now, now),
                )
            self.db.execute(
                """INSERT INTO business_function_version
                   (id,function_id,version,status,snapshot_json,source_proposal_id,created_by,created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, function_id, next_version, FunctionStatus.PUBLISHED,
                 dumps(snapshot), proposal_id, published_by, now),
            )
            self._materialise_snapshot(version_id, function_id, snapshot)
            self.db.execute(
                """UPDATE knowledge_update_proposal SET status=?,published_by=?,published_at=?,updated_at=?
                   WHERE id=?""",
                (ProposalStatus.PUBLISHED, published_by, now, now, proposal_id),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get_function(function_id)

    def get_function(self, function_id: str, version: int | None = None) -> dict:
        function = self.db.execute("SELECT * FROM business_function WHERE id=?", (function_id,)).fetchone()
        if not function:
            raise KeyError(function_id)
        if version is None:
            version_row = self.db.execute(
                "SELECT * FROM business_function_version WHERE id=?", (function["current_version_id"],)
            ).fetchone()
        else:
            version_row = self.db.execute(
                "SELECT * FROM business_function_version WHERE function_id=? AND version=?",
                (function_id, version),
            ).fetchone()
        if not version_row:
            raise KeyError(f"{function_id} version {version or 'current'}")
        snapshot = json.loads(version_row["snapshot_json"])
        snapshot["evidence_ids"] = self._evidence_ids(version_row["id"], "FUNCTION", function_id)
        for collection, item_type in (("scenarios", "SCENARIO"), ("rules", "RULE"),
                                      ("entries", "ENTRY"), ("data_impacts", "DATA_IMPACT")):
            for item in snapshot[collection]:
                item["evidence_ids"] = self._evidence_ids(version_row["id"], item_type, item["id"])
        metadata = dict(version_row)
        metadata.pop("snapshot_json")
        return {"function": dict(function), "version": metadata, "snapshot": snapshot}

    def list_functions(self, status: str | None = FunctionStatus.PUBLISHED, limit: int = 50) -> list[dict]:
        if status is None:
            rows = self.db.execute(
                """SELECT f.*,v.snapshot_json FROM business_function f
                     LEFT JOIN business_function_version v ON v.id=f.current_version_id
                    ORDER BY f.updated_at DESC,f.id LIMIT ?""", (limit,)
            )
        else:
            rows = self.db.execute(
                """SELECT f.*,v.snapshot_json FROM business_function f
                     LEFT JOIN business_function_version v ON v.id=f.current_version_id
                    WHERE f.status=? ORDER BY f.updated_at DESC,f.id LIMIT ?""",
                (status, limit),
            )
        results = []
        for row in rows:
            item = dict(row)
            raw_snapshot = item.pop("snapshot_json", None)
            item["snapshot"] = json.loads(raw_snapshot) if raw_snapshot else None
            results.append(item)
        return results

    def get_proposal(self, proposal_id: str) -> dict:
        proposal = dict(self._proposal_row(proposal_id))
        proposal["proposed_snapshot"] = json.loads(proposal.pop("proposed_snapshot_json"))
        items = [self._proposal_item(row["id"]) for row in self.db.execute(
            "SELECT id FROM knowledge_update_proposal_item WHERE proposal_id=? ORDER BY sequence",
            (proposal_id,),
        )]
        reviews = [dict(row) for row in self.db.execute(
            "SELECT * FROM knowledge_proposal_review WHERE proposal_id=? ORDER BY reviewed_at,id",
            (proposal_id,),
        )]
        return {"proposal": proposal, "items": items, "reviews": reviews}

    def list_proposals(
        self, status: str | None = None, target_function_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        where, values = [], []
        if status is not None:
            where.append("status=?")
            values.append(status)
        if target_function_id is not None:
            where.append("target_function_id=?")
            values.append(target_function_id)
        sql = "SELECT * FROM knowledge_update_proposal"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC,id LIMIT ?"
        values.append(limit)
        results = []
        for row in self.db.execute(sql, values):
            item = dict(row)
            item["proposed_snapshot"] = json.loads(item.pop("proposed_snapshot_json"))
            results.append(item)
        return results

    def list_versions(self, function_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT id,function_id,version,status,source_proposal_id,created_by,created_at
                 FROM business_function_version WHERE function_id=? ORDER BY version""",
            (function_id,),
        )]

    def _materialise_snapshot(self, version_id: str, function_id: str, snapshot: dict) -> None:
        publication_evidence_id = _new_id("EV-BF")
        publication_text = _publication_text(snapshot)
        self.db.execute(
            """INSERT INTO evidence
               (id,source_type,source_id,source_version,locator,line_start,line_end,chunk_id,content_hash,excerpt)
               VALUES (?, 'MANUAL', ?, ?, ?, NULL, NULL, NULL, ?, ?)""",
            (publication_evidence_id, function_id, version_id, f"business-function:{function_id}",
             digest(publication_text), publication_text),
        )
        self.db.execute(
            "INSERT INTO evidence_lifecycle VALUES (?, 'ACTIVE', NULL, NULL, NULL)",
            (publication_evidence_id,),
        )
        for evidence_id in snapshot["evidence_ids"]:
            self.db.execute(
                "INSERT INTO function_item_evidence VALUES (?, 'FUNCTION', ?, ?)",
                (version_id, function_id, evidence_id),
            )
        tables = (
            ("scenarios", "function_scenario", "SCENARIO"),
            ("rules", "function_rule", "RULE"),
            ("entries", "function_entry", "ENTRY"),
            ("data_impacts", "function_data_impact", "DATA_IMPACT"),
        )
        for collection, table, item_type in tables:
            for item in snapshot[collection]:
                if table == "function_scenario":
                    values = (item["id"], version_id, item["name"], item["summary"], item["status"])
                elif table == "function_rule":
                    values = (item["id"], version_id, item["statement"], dumps(item["conditions"]), item["result"], item["status"])
                elif table == "function_entry":
                    values = (item["id"], version_id, item["entry_type"], item["label"], item["target_type"], item["target_id"], item["locator"], item["status"])
                else:
                    values = (item["id"], version_id, item["object_type"], item["object_name"], item["operation"], item["before_state"], item["after_state"], item["description"])
                placeholders = ",".join("?" for _ in values)
                self.db.execute(f"INSERT INTO {table} VALUES ({placeholders})", values)
                for evidence_id in item["evidence_ids"]:
                    self.db.execute(
                        "INSERT INTO function_item_evidence VALUES (?, ?, ?, ?)",
                        (version_id, item_type, item["id"], evidence_id),
                    )

    def _proposal_row(self, proposal_id: str):
        row = self.db.execute("SELECT * FROM knowledge_update_proposal WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return row

    def _proposal_item(self, item_id: str) -> dict:
        row = self.db.execute("SELECT * FROM knowledge_update_proposal_item WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        item = dict(row)
        for key in ("before_json", "after_json"):
            item[key.removesuffix("_json")] = json.loads(item.pop(key)) if item[key] is not None else None
        item["evidence_ids"] = [value[0] for value in self.db.execute(
            "SELECT evidence_id FROM proposal_item_evidence WHERE proposal_item_id=? ORDER BY evidence_id",
            (item_id,),
        )]
        return item

    def _evidence_ids(self, version_id: str, item_type: str, item_id: str) -> list[str]:
        return [row[0] for row in self.db.execute(
            """SELECT evidence_id FROM function_item_evidence
                WHERE function_version_id=? AND item_type=? AND item_id=? ORDER BY evidence_id""",
            (version_id, item_type, item_id),
        )]

    def _touch(self, proposal_id: str) -> None:
        self.db.execute("UPDATE knowledge_update_proposal SET updated_at=? WHERE id=?", (_now(), proposal_id))


def _normalise_snapshot(value: FunctionSnapshot | dict[str, Any]) -> dict[str, Any]:
    raw = value.to_dict() if isinstance(value, FunctionSnapshot) else dict(value)
    if not str(raw.get("name", "")).strip():
        raise ValueError("function snapshot requires a name")
    snapshot = {
        "name": str(raw["name"]).strip(),
        "domain": str(raw.get("domain", "")).strip(),
        "summary": str(raw.get("summary", "")).strip(),
        "evidence_ids": _strings(raw.get("evidence_ids", [])),
    }
    definitions = {
        "scenarios": ({"name": "", "summary": "", "status": "ACTIVE"}, "name"),
        "rules": ({"statement": "", "conditions": [], "result": "", "status": "ACTIVE"}, "statement"),
        "entries": ({"entry_type": "", "label": "", "target_type": "", "target_id": "", "locator": "", "status": "ACTIVE"}, "label"),
        "data_impacts": ({"object_type": "", "object_name": "", "operation": "", "before_state": "", "after_state": "", "description": ""}, "object_name"),
    }
    for collection, (defaults, required) in definitions.items():
        items, seen = [], set()
        for index, original in enumerate(raw.get(collection, []), 1):
            item = asdict(original) if hasattr(original, "__dataclass_fields__") else dict(original)
            normalised = {key: item.get(key, default) for key, default in defaults.items()}
            if not str(normalised[required]).strip():
                raise ValueError(f"{collection} item requires {required}")
            normalised["id"] = item.get("id") or f"{collection.rstrip('s').upper()}-{index:03d}"
            if normalised["id"] in seen:
                raise ValueError(f"duplicate {collection} id: {normalised['id']}")
            seen.add(normalised["id"])
            normalised["evidence_ids"] = _strings(item.get("evidence_ids", []))
            if collection == "rules":
                normalised["conditions"] = _strings(normalised["conditions"])
            items.append(normalised)
        snapshot[collection] = items
    return snapshot


def _publication_text(snapshot: dict[str, Any]) -> str:
    parts = [f"业务功能：{snapshot['name']}"]
    if snapshot.get("summary"):
        parts.append(snapshot["summary"])
    if snapshot.get("scenarios"):
        parts.append("业务场景：" + "；".join(item["name"] for item in snapshot["scenarios"]))
    if snapshot.get("rules"):
        parts.append("业务规则：" + "；".join(item["statement"] for item in snapshot["rules"]))
    return "\n".join(parts)


def _enum_value(enum_type, value, label: str) -> str:
    try:
        return enum_type(value).value
    except ValueError as exc:
        raise ValueError(f"unsupported {label}: {value}") from exc


def _optional_json(value: Any) -> str | None:
    return None if value is None else dumps(value)


def _strings(values: Iterable[Any]) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
def retain_active_evidence(db, snapshot: dict, additional=()) -> dict:
    """Remove superseded references from a proposed snapshot and add current source evidence."""
    import copy

    value = copy.deepcopy(snapshot)
    referenced: set[str] = set()

    def collect(node):
        if isinstance(node, dict):
            referenced.update(str(item) for item in node.get("evidence_ids", []) if item)
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(value)
    active: set[str] = set()
    if referenced:
        marks = ",".join("?" for _ in referenced)
        active = {row[0] for row in db.execute(
            f"""SELECT e.id FROM evidence e LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                  WHERE e.id IN ({marks}) AND coalesce(el.status,'ACTIVE')='ACTIVE'""",
            tuple(referenced),
        )}

    def filter_node(node):
        if isinstance(node, dict):
            if "evidence_ids" in node:
                node["evidence_ids"] = [item for item in node["evidence_ids"] if str(item) in active]
            for child in node.values():
                filter_node(child)
        elif isinstance(node, list):
            for child in node:
                filter_node(child)

    filter_node(value)
    value["evidence_ids"] = list(dict.fromkeys([*value.get("evidence_ids", []), *additional]))
    return value

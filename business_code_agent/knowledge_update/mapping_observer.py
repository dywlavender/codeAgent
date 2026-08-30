"""Turn query evidence into reviewable Business-Code mapping observations.

MVP2 deliberately keeps this separate from the authored business baseline.
The query agent can notice a useful relationship, but only an administrator
can promote it to the durable ``business_code_mapping`` table.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from ..util import stable_id


OBSERVATION_STATUSES = {"CANDIDATE", "ACCEPTED", "REJECTED"}


class MappingObservationService:
    """Persist and review Business-Code links discovered during a query."""

    def __init__(self, db, *, max_per_query: int = 8, min_confidence: float = 0.5):
        self.db = db
        self.max_per_query = max(1, min(int(max_per_query), 30))
        self.min_confidence = max(0.0, min(float(min_confidence), 1.0))

    def observe_query(self, run_id: str, question: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Create bounded candidate observations from the answer's own evidence.

        A candidate is emitted only when the answer has both a known business
        object and code evidence.  Search candidates alone are not enough;
        this keeps an unverified search hit from becoming durable knowledge.
        """
        run_id = str(run_id or "").strip()
        if not run_id:
            return []
        evidence_status = str(
            result.get("evidenceStatus") or result.get("evidence_status") or ""
        ).upper()
        if evidence_status and evidence_status != "SUFFICIENT":
            return self.list_for_run(run_id)

        evidence = [item for item in result.get("evidence", []) if isinstance(item, Mapping)]
        answer = result.get("answer") if isinstance(result.get("answer"), Mapping) else {}
        business_targets = self._business_targets(result, evidence, answer)
        code_targets = self._code_targets(result, evidence, answer)
        if not business_targets or not code_targets:
            return self.list_for_run(run_id)

        pairs: list[dict[str, Any]] = []
        for business in business_targets:
            for code in code_targets:
                existing = self._existing_mapping(business, code)
                if existing and str(existing["status"]).upper() == "VERIFIED":
                    continue
                score = self._score(business, code, str(question or ""), existing)
                overlap = set(business.get("terms", [])) & set(code.get("terms", []))
                if existing is None and not overlap and (len(business_targets) > 1 or len(code_targets) > 1):
                    # With several facts in one answer, an unanchored
                    # Cartesian pairing is too ambiguous to suggest.
                    continue
                if score < self.min_confidence:
                    continue
                pairs.append({
                    "business": business, "code": code, "existing": existing,
                    "confidence": score,
                })
        pairs.sort(key=lambda item: (-item["confidence"], item["business"]["name"], item["code"]["reference"]))
        for item in pairs[: self.max_per_query]:
            business = item["business"]
            code = item["code"]
            evidence_ids = _unique([
                *business.get("evidence_ids", []), *code.get("evidence_ids", []),
            ])
            reason = self._reason(business, code, item["existing"])
            observation_id = stable_id(
                "BMO", run_id, business["type"], business["id"],
                business["relation"], code["reference"],
            )
            now = _now()
            self.db.execute(
                """INSERT INTO business_code_mapping_observation
                   (id,run_id,question,business_type,business_id,relation_type,
                    code_symbol_id,code_reference,status,confidence,evidence_ids_json,
                    reason,created_at,reviewed_at,reviewer_note)
                   VALUES (?,?,?,?,?,?,?,?,'CANDIDATE',?,?,?, ?,NULL,'')
                   ON CONFLICT(run_id,business_type,business_id,relation_type,code_reference)
                   DO UPDATE SET code_symbol_id=excluded.code_symbol_id,
                     confidence=MAX(business_code_mapping_observation.confidence, excluded.confidence),
                     evidence_ids_json=excluded.evidence_ids_json, reason=excluded.reason,
                     status=CASE WHEN business_code_mapping_observation.status IN ('ACCEPTED','REJECTED')
                                 THEN business_code_mapping_observation.status ELSE 'CANDIDATE' END""",
                (
                    observation_id, run_id, str(question or "")[:2000], business["type"], business["id"],
                    business["relation"], code.get("symbol_id"), code["reference"],
                    item["confidence"], json.dumps(evidence_ids, ensure_ascii=False), reason, now,
                ),
            )
        self.db.commit()
        return self.list_for_run(run_id)

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        # The query response is a prompt for review, so only still-actionable
        # candidates are returned.  Historical ACCEPTED/REJECTED records stay
        # available through the general observation endpoint.
        return self.list_observations(run_id=run_id, status="CANDIDATE")

    def list_observations(
        self, *, status: str = "", business_id: str = "", run_id: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        normalized_status = str(status or "").strip().upper()
        if normalized_status:
            if normalized_status not in OBSERVATION_STATUSES:
                raise ValueError(f"unsupported mapping observation status: {status}")
            clauses.append("o.status=?")
            params.append(normalized_status)
        if business_id:
            clauses.append("o.business_id=?")
            params.append(str(business_id))
        if run_id:
            clauses.append("o.run_id=?")
            params.append(str(run_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(int(limit), 200))
        rows = self.db.execute(
            f"SELECT o.* FROM business_code_mapping_observation o{where} ORDER BY o.created_at DESC,o.id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._dict(row) for row in rows]

    def get(self, observation_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM business_code_mapping_observation WHERE id=?", (observation_id,)
        ).fetchone()
        if not row:
            raise KeyError(observation_id)
        value = self._dict(row)
        evidence_ids = value["evidenceIds"]
        if evidence_ids:
            marks = ",".join("?" for _ in evidence_ids)
            value["evidence"] = [dict(item) for item in self.db.execute(
                f"""SELECT id,source_type,source_id,source_version,locator,
                          line_start,line_end,excerpt
                     FROM evidence WHERE id IN ({marks}) ORDER BY id""",
                tuple(evidence_ids),
            )]
        else:
            value["evidence"] = []
        return value

    def accept(self, observation_id: str, reviewer_note: str = "") -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM business_code_mapping_observation WHERE id=?", (observation_id,)
        ).fetchone()
        if not row:
            raise KeyError(observation_id)
        if row["status"] == "REJECTED":
            raise ValueError("已拒绝的映射候选不能直接确认")
        if row["status"] == "ACCEPTED":
            return self.get(observation_id)
        self._assert_target(row)
        evidence_ids = _json_list(row["evidence_ids_json"])
        existing = self.db.execute(
            """SELECT * FROM business_code_mapping
                WHERE business_type=? AND business_id=? AND relation_type=? AND code_reference=?""",
            (row["business_type"], row["business_id"], row["relation_type"], row["code_reference"]),
        ).fetchone()
        now = _now()
        mapping_id = stable_id(
            "BCM", row["business_type"], row["business_id"], row["relation_type"], row["code_reference"],
        )
        if existing:
            merged_evidence = _unique([*_json_list(existing["evidence_ids_json"]), *evidence_ids])
            self.db.execute(
                """UPDATE business_code_mapping
                   SET code_symbol_id=COALESCE(?,code_symbol_id), status='VERIFIED',
                       confidence=MAX(confidence,?), evidence_ids_json=?,
                       message=?, source_type='QUERY_REVIEW', updated_at=?
                   WHERE id=?""",
                (
                    row["code_symbol_id"], float(row["confidence"]),
                    json.dumps(merged_evidence, ensure_ascii=False),
                    ("管理员确认：" + str(reviewer_note or ""))[:500], now, existing["id"],
                ),
            )
        else:
            self.db.execute(
                """INSERT INTO business_code_mapping
                   (id,business_type,business_id,relation_type,code_symbol_id,code_reference,
                    status,confidence,evidence_ids_json,search_terms_json,message,source_type,updated_at)
                   VALUES (?,?,?,?,? ,?,'VERIFIED',?,?, '[]',?,'QUERY_REVIEW',?)""",
                (
                    mapping_id, row["business_type"], row["business_id"], row["relation_type"],
                    row["code_symbol_id"], row["code_reference"], float(row["confidence"]),
                    json.dumps(evidence_ids, ensure_ascii=False),
                    ("管理员确认：" + str(reviewer_note or ""))[:500], now,
                ),
            )
        self.db.execute(
            """UPDATE business_code_mapping_observation
               SET status='ACCEPTED', reviewed_at=?, reviewer_note=? WHERE id=?""",
            (now, str(reviewer_note or "")[:1000], observation_id),
        )
        self.db.commit()
        return self.get(observation_id)

    def reject(self, observation_id: str, reviewer_note: str = "") -> dict[str, Any]:
        row = self.db.execute(
            "SELECT status FROM business_code_mapping_observation WHERE id=?", (observation_id,)
        ).fetchone()
        if not row:
            raise KeyError(observation_id)
        if row["status"] == "ACCEPTED":
            raise ValueError("已确认的映射不能直接拒绝")
        self.db.execute(
            """UPDATE business_code_mapping_observation
               SET status='REJECTED', reviewed_at=?, reviewer_note=? WHERE id=?""",
            (_now(), str(reviewer_note or "")[:1000], observation_id),
        )
        self.db.commit()
        return self.get(observation_id)

    def _business_targets(self, result, evidence, answer) -> list[dict[str, Any]]:
        ids: list[str] = []
        # A search hit is only a navigation hint.  It must not become a
        # mapping target merely because it was returned in
        # ``businessCandidates``.  Only business Evidence IDs referenced by a
        # final answer fact are eligible; this makes the observation boundary
        # match what the user actually saw and what the answer was based on.
        evidence_by_id = {
            str(item.get("evidenceId") or item.get("evidence_id") or item.get("id")): item
            for item in evidence
            if item.get("evidenceId") or item.get("evidence_id") or item.get("id")
        }
        for fact in _as_list(answer.get("facts")):
            if not isinstance(fact, Mapping) or str(fact.get("sourceType") or fact.get("source_type") or "").upper() != "BUSINESS":
                continue
            for evidence_id in _as_list(fact.get("evidenceIds") or fact.get("evidence_ids")):
                evidence_id = str(evidence_id)
                item = evidence_by_id.get(evidence_id)
                if item and str(item.get("sourceType") or item.get("source_type") or "").upper() != "BUSINESS":
                    continue
                row = self.db.execute(
                    "SELECT source_id FROM evidence WHERE id=? AND source_type IN ('BUSINESS','MANUAL')",
                    (evidence_id,),
                ).fetchone()
                if row:
                    ids.append(str(row["source_id"]))
        values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in ids:
            if value in seen:
                continue
            seen.add(value)
            entity = self.db.execute("SELECT * FROM business_entity WHERE id=?", (value,)).fetchone()
            if entity:
                attributes = _json_object(entity["attributes_json"])
                values.append({
                    "id": value, "type": "ENTITY", "name": entity["name"],
                    "relation": _entity_relation(entity["entity_type"]),
                    "terms": _terms([entity["name"], *_json_list(entity["aliases_json"]), entity["definition"],
                                      *_as_list(attributes.get("codeHints")), *_as_list(attributes.get("keywords"))]),
                    "evidence_ids": _unique([entity["source_evidence_id"]]),
                })
                continue
            relation = self.db.execute("SELECT * FROM business_relation_v2 WHERE id=?", (value,)).fetchone()
            if relation:
                values.append({
                    "id": value, "type": "RELATION", "name": f"{relation['from_label']} {relation['relation_type']} {relation['to_label']}",
                    "relation": "EVIDENCED_BY",
                    "terms": _terms([relation["from_label"], relation["to_label"], relation["relation_type"], relation["scope"]]),
                    "evidence_ids": _unique([relation["evidence_id"]]),
                })
        return values

    def _code_targets(self, result, evidence, answer) -> list[dict[str, Any]]:
        by_symbol: dict[str, dict[str, Any]] = {}
        answer_evidence = {
            str(evidence_id)
            for fact in _as_list(answer.get("facts"))
            if isinstance(fact, Mapping) and str(fact.get("sourceType") or fact.get("source_type") or "").upper() == "CODE"
            for evidence_id in _as_list(fact.get("evidenceIds") or fact.get("evidence_ids"))
        }
        evidence_by_id = {
            str(item.get("evidenceId") or item.get("evidence_id") or item.get("id")): item
            for item in evidence if item.get("evidenceId") or item.get("evidence_id") or item.get("id")
        }
        for evidence_id in answer_evidence:
            item = evidence_by_id.get(evidence_id)
            if not item or str(item.get("sourceType") or item.get("source_type") or "").upper() != "CODE":
                continue
            symbol_id = str(item.get("sourceId") or item.get("source_id") or "")
            symbol = self.db.execute(
                "SELECT id,qualified_name,name,kind FROM code_symbol WHERE id=?", (symbol_id,)
            ).fetchone()
            if not symbol:
                code_fact = self.db.execute(
                    "SELECT symbol_id FROM code_fact WHERE evidence_id=? LIMIT 1", (evidence_id,)
                ).fetchone()
                symbol = self.db.execute(
                    "SELECT id,qualified_name,name,kind FROM code_symbol WHERE id=?",
                    (code_fact["symbol_id"],),
                ).fetchone() if code_fact else None
            if not symbol:
                continue
            target = by_symbol.setdefault(symbol["id"], {
                "symbol_id": symbol["id"], "reference": symbol["qualified_name"],
                "terms": _terms([symbol["qualified_name"], symbol["name"], symbol["kind"]]),
                "evidence_ids": [],
            })
            target["evidence_ids"].append(evidence_id)
            target["terms"].extend(_terms(str(item.get("content") or "")))

        # Candidate projections can contain a symbol that did not appear in
        # the compact evidence list.  Only use it when one of its own code
        # evidence IDs was part of the answer.
        for item in [*(_as_list(result.get("codeCandidates"))), *(_as_list(result.get("code_candidates")))]:
            if not isinstance(item, Mapping):
                continue
            evidence_ids = [str(value) for value in _as_list(item.get("evidenceIds") or item.get("evidence_ids"))]
            evidence_ids = [value for value in evidence_ids if value in answer_evidence]
            symbol_id = _first(item, "symbolId", "symbol_id", "targetId", "target_id", "id")
            if not symbol_id or not evidence_ids:
                continue
            symbol = self.db.execute(
                "SELECT id,qualified_name,name,kind FROM code_symbol WHERE id=?", (str(symbol_id),)
            ).fetchone()
            if not symbol:
                continue
            target = by_symbol.setdefault(str(symbol["id"]), {
                "symbol_id": symbol["id"], "reference": symbol["qualified_name"],
                "terms": _terms([symbol["qualified_name"], symbol["name"], symbol["kind"]]),
                "evidence_ids": [],
            })
            target["evidence_ids"].extend(evidence_ids)
        for target in by_symbol.values():
            target["evidence_ids"] = _unique(target["evidence_ids"])
            target["terms"] = _unique(target["terms"])
        return list(by_symbol.values())

    def _existing_mapping(self, business: dict, code: dict):
        rows = self.db.execute(
            """SELECT * FROM business_code_mapping
                WHERE business_type=? AND business_id=?
                  AND (code_symbol_id=? OR code_reference=?)
                ORDER BY CASE status WHEN 'VERIFIED' THEN 0 WHEN 'CANDIDATE' THEN 1 ELSE 2 END""",
            (business["type"], business["id"], code.get("symbol_id"), code["reference"]),
        ).fetchall()
        return rows[0] if rows else None

    @staticmethod
    def _score(business: dict, code: dict, question: str, existing) -> float:
        overlap = set(business.get("terms", [])) & set(code.get("terms", []))
        question_terms = set(_terms(question))
        question_overlap = question_terms & (set(business.get("terms", [])) | set(code.get("terms", [])))
        score = 0.45
        if overlap:
            score += min(0.24, 0.08 * len(overlap))
        if question_overlap:
            score += min(0.12, 0.04 * len(question_overlap))
        if existing is not None:
            score += 0.16
        if business.get("id") and code.get("evidence_ids"):
            score += 0.06
        if len(business.get("evidence_ids", [])) and len(code.get("evidence_ids", [])):
            score += 0.08
        return min(0.97, round(score, 4))

    @staticmethod
    def _reason(business: dict, code: dict, existing) -> str:
        if existing is not None:
            return "问答证据命中了已有代码候选映射，建议管理员确认"
        overlap = sorted(set(business.get("terms", [])) & set(code.get("terms", [])))
        if overlap:
            return "问答中的业务知识与代码证据存在命名或别名交集：" + ", ".join(overlap[:6])
        return "同一回答同时引用了该业务知识和代码证据，建议人工确认是否为对应实现"

    def _assert_target(self, row) -> None:
        if row["business_type"] == "ENTITY":
            exists = self.db.execute(
                "SELECT 1 FROM business_entity WHERE id=? AND status!='DEPRECATED'", (row["business_id"],)
            ).fetchone()
        else:
            exists = self.db.execute(
                "SELECT 1 FROM business_relation_v2 WHERE id=? AND status!='DEPRECATED'", (row["business_id"],)
            ).fetchone()
        if not exists:
            raise ValueError("业务知识已不存在或已废弃，不能确认映射")
        if row["code_symbol_id"]:
            code_exists = self.db.execute(
                "SELECT 1 FROM code_symbol WHERE id=?", (row["code_symbol_id"],)
            ).fetchone()
            if not code_exists:
                raise ValueError("代码 Symbol 已不在当前索引中，不能确认映射")

    def _dict(self, row) -> dict[str, Any]:
        value = {
            "id": row["id"], "runId": row["run_id"], "question": row["question"],
            "businessType": row["business_type"], "businessId": row["business_id"],
            "relation": row["relation_type"], "relationType": row["relation_type"],
            "codeSymbolId": row["code_symbol_id"], "codeReference": row["code_reference"],
            "status": row["status"], "confidence": row["confidence"],
            "evidenceIds": _json_list(row["evidence_ids_json"]), "reason": row["reason"],
            "createdAt": row["created_at"], "reviewedAt": row["reviewed_at"],
            "reviewerNote": row["reviewer_note"],
            "sourceType": "QUERY", "origin": "QUERY_OBSERVATION",
        }
        if row["business_type"] == "ENTITY":
            target = row["business_id"]
            business = None
            # The lookup is intentionally best-effort here; the observation
            # itself remains readable even when its source entity was later
            # removed from the baseline.
            try:
                business = self.db.execute(
                    "SELECT name,entity_type FROM business_entity WHERE id=?", (target,)
                ).fetchone()
            except Exception:
                business = None
            if business:
                value["businessName"] = business["name"]
                value["businessEntityType"] = business["entity_type"]
        elif row["business_type"] == "RELATION":
            relation = self.db.execute(
                "SELECT from_label,relation_type,to_label FROM business_relation_v2 WHERE id=?",
                (row["business_id"],),
            ).fetchone()
            if relation:
                value["businessName"] = f"{relation['from_label']} {relation['relation_type']} {relation['to_label']}"
        if row["code_symbol_id"]:
            code = self.db.execute(
                """SELECT cs.kind,cs.qualified_name,cs.line_start,cs.line_end,cf.path
                     FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id WHERE cs.id=?""",
                (row["code_symbol_id"],),
            ).fetchone()
            if code:
                value["codeKind"] = code["kind"]
                value["codeLocation"] = {
                    "qualifiedName": code["qualified_name"], "file": code["path"],
                    "startLine": code["line_start"], "endLine": code["line_end"],
                }
        return value


def _entity_relation(entity_type: str) -> str:
    return {
        "SYSTEM": "OWNED_BY", "BUSINESS_TERM": "REPRESENTED_BY",
        "CAPABILITY": "IMPLEMENTED_BY", "FLOW": "IMPLEMENTED_BY",
        "RULE": "ENFORCED_BY",
    }.get(str(entity_type).upper(), "RELATED_TO")


def _first(value: Mapping[str, Any], *keys: str):
    for key in keys:
        if value.get(key) is not None and str(value.get(key)).strip():
            return value.get(key)
    return None


def _terms(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            result.extend(_terms(value))
            continue
        text = str(value or "").strip().casefold()
        if not text:
            continue
        result.append(_normalise(text))
        result.extend(_normalise(token) for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text))
        for run in re.findall(r"[\u4e00-\u9fff]+", text):
            if len(run) <= 4:
                result.append(_normalise(run))
            result.extend(_normalise(run[index:index + 2]) for index in range(len(run) - 1))
    return _unique([value for value in result if len(value) > 1])


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", value.casefold())


def _json_list(value) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _json_object(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    return list(value)


def _unique(values) -> list:
    return list(dict.fromkeys(value for value in values if value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from sqlite3 import Connection

from .util import stable_id


class IntegrationEdgeResolver:
    """Resolve HTTP and Feign edges only when both source and target facts exist."""

    def __init__(self, db: Connection):
        self.db = db

    def rebuild(self) -> dict[str, int]:
        self.db.execute("DELETE FROM cross_application_edge")
        facts = self._integration_facts()
        bases: dict[str, list[dict]] = {}
        services: dict[str, list[dict]] = {}
        endpoints: list[dict] = []
        calls: list[dict] = []
        for fact in facts:
            if fact["fact_type"] == "HTTP_BASE_PATH":
                bases.setdefault(fact["file_id"], []).append(fact)
            elif fact["fact_type"] == "RPC_SERVICE":
                services.setdefault(fact["file_id"], []).append(fact)
            elif fact["fact_type"] == "HTTP_ENDPOINT":
                endpoint = dict(fact)
                endpoint["resolved_path"] = _join_paths(
                    (bases.get(fact["file_id"]) or [{"target": "/"}])[0]["target"],
                    fact["target"],
                )
                endpoint["base_evidence_ids"] = [item["evidence_id"] for item in bases.get(fact["file_id"], [])]
                endpoints.append(endpoint)
            elif fact["fact_type"] in {"HTTP_CALL", "RPC_CALL"}:
                calls.append(dict(fact))

        created = verified = candidates = 0
        for call in calls:
            call_path = _join_paths(
                (bases.get(call["file_id"]) or [{"target": "/"}])[0]["target"],
                call["target"],
            ) if call["fact_type"] == "RPC_CALL" else call["target"]
            matches: list[tuple[dict, float, list[str], str]] = []
            for endpoint in endpoints:
                if endpoint["application_id"] == call["application_id"]:
                    continue
                if not _method_matches(call["subject"], endpoint["subject"]):
                    continue
                score = _path_match(call_path, endpoint["resolved_path"])
                if score <= 0:
                    continue
                extra_evidence = list(endpoint["base_evidence_ids"])
                protocol = "HTTP"
                if call["fact_type"] == "RPC_CALL":
                    service_rows = services.get(call["file_id"], [])
                    if not service_rows or not any(
                        _service_matches(item["subject"], endpoint["application_id"], endpoint["application_name"])
                        for item in service_rows
                    ):
                        continue
                    extra_evidence.extend(item["evidence_id"] for item in service_rows)
                    protocol = "FEIGN"
                matches.append((endpoint, score, extra_evidence, protocol))
            if matches:
                best_score = max(item[1] for item in matches)
                matches = [item for item in matches if item[1] == best_score]
            ambiguous = len(matches) > 1
            for endpoint, score, extra_evidence, protocol in matches:
                evidence_ids = list(dict.fromkeys([
                    call["evidence_id"], endpoint["evidence_id"], *extra_evidence,
                ]))
                edge_type = "RPC" if call["fact_type"] == "RPC_CALL" else "HTTP"
                edge_key = (
                    f"{services.get(call['file_id'], [{}])[0].get('subject', '')}|"
                    if edge_type == "RPC" else ""
                ) + f"{call['subject'].upper()} {call_path}"
                status = "CANDIDATE" if ambiguous else "VERIFIED"
                confidence = min(score, 0.75) if ambiguous else score
                edge_id = stable_id(
                    "XEDGE", call["symbol_id"], edge_type, endpoint["symbol_id"], edge_key,
                )
                self.db.execute(
                    """INSERT INTO cross_application_edge(
                         id,source_application_id,source_symbol_id,edge_type,
                         target_application_id,target_symbol_id,protocol,edge_key,
                         status,confidence,evidence_ids_json,resolved_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        edge_id, call["application_id"], call["symbol_id"], edge_type,
                        endpoint["application_id"], endpoint["symbol_id"], protocol,
                        edge_key, status, confidence, json.dumps(evidence_ids),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                created += 1
                if status == "VERIFIED":
                    verified += 1
                else:
                    candidates += 1
        self.db.commit()
        return {"edges": created, "verified": verified, "candidates": candidates}

    def _integration_facts(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            """SELECT f.fact_type,f.subject,f.target,f.evidence_id,
                      s.id symbol_id,s.qualified_name,s.file_id,
                      a.id application_id,a.name application_name
                 FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                 JOIN application_code_file acf ON acf.file_id=s.file_id
                 JOIN application a ON a.id=acf.application_id
                WHERE f.fact_type IN (
                  'HTTP_CALL','HTTP_ENDPOINT','HTTP_BASE_PATH','RPC_CALL','RPC_SERVICE'
                )
                ORDER BY a.id,s.file_id,s.line_start,f.fact_type"""
        )]


def _method_matches(source: str, target: str) -> bool:
    return source.upper() == target.upper() or "ANY" in {source.upper(), target.upper()}


def _join_paths(prefix: str, path: str) -> str:
    parts = [item.strip("/") for item in (prefix, path) if item and item != "/"]
    return "/" + "/".join(parts) if parts else "/"


def _path_match(source: str, target: str) -> float:
    source = _normalize_path(source)
    target = _normalize_path(target)
    if source == target:
        return 1.0
    source_variants = _path_variants(source)
    target_variants = _path_variants(target)
    if source_variants.intersection(target_variants):
        return 0.92
    return 0.0


def _path_variants(path: str) -> set[str]:
    result = {path}
    parts = [part for part in path.split("/") if part]
    if parts and parts[0].lower() in {"api", "gateway"}:
        result.add("/" + "/".join(parts[1:]))
    return result


def _normalize_path(value: str) -> str:
    path = re.sub(r"/+", "/", value.strip().split("?", 1)[0])
    return "/" + path.strip("/") if path.strip("/") else "/"


def _service_matches(service: str, application_id: str, application_name: str) -> bool:
    expected = _service_key(service)
    return expected in {_service_key(application_id), _service_key(application_name)}


def _service_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    return key.removesuffix("service")

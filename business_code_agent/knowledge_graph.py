from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .knowledge_update.repository import KnowledgeGovernanceRepository


class KnowledgeGraphService:
    """Read-only graph projection built from published knowledge and Evidence.

    The graph is deliberately a projection, not a second source of truth. Nodes
    point back to the published function snapshot, requirement version, code
    symbol, or Evidence record that created the relation.
    """

    NODE_TYPES = {"FUNCTION", "REQUIREMENT", "CODE", "TAG", "BUSINESS"}

    def __init__(self, db):
        self.db = db
        self.repository = KnowledgeGovernanceRepository(db)

    def search(self, query: str = "", node_type: str = "", limit: int = 120) -> dict[str, Any]:
        query = str(query or "").strip().casefold()
        node_type = str(node_type or "").strip().upper()
        if node_type and node_type not in self.NODE_TYPES:
            raise ValueError(f"unsupported graph node type: {node_type}")
        limit = max(20, min(int(limit), 240))

        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        def add_node(node_id: str, kind: str, label: str, **metadata: Any) -> str:
            kind = kind.upper()
            if not label:
                label = node_id
            item = nodes.get(node_id)
            if item is None:
                nodes[node_id] = {
                    "id": node_id,
                    "type": kind,
                    "typeLabel": _type_label(kind),
                    "label": str(label),
                    **{key: value for key, value in metadata.items() if value is not None},
                }
            else:
                for key, value in metadata.items():
                    if value not in (None, "", [], {}) and item.get(key) in (None, "", [], {}):
                        item[key] = value
            return node_id

        def add_edge(source: str, target: str, relation: str, *, status: str = "CONFIRMED", evidence_ids=()):
            edge_id = f"{source}:{relation}:{target}"
            item = edges.get(edge_id)
            if item is None:
                edges[edge_id] = {
                    "id": edge_id,
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "label": _relation_label(relation),
                    "status": status,
                    "evidenceIds": list(dict.fromkeys(str(value) for value in evidence_ids if value)),
                }
            else:
                item["evidenceIds"] = list(dict.fromkeys([*item["evidenceIds"], *(str(value) for value in evidence_ids if value)]))

        # Published function snapshots are the central business nodes.
        for summary in self.repository.list_functions(status="PUBLISHED", limit=100):
            function_id = str(summary["id"])
            detail = self.repository.get_function(function_id)
            snapshot = detail["snapshot"]
            evidence_ids = _snapshot_evidence(snapshot)
            function_node_id = add_node(
                f"FUNCTION:{function_id}",
                "FUNCTION",
                str(snapshot.get("name") or function_id),
                subtitle=str(snapshot.get("domain") or "业务功能"),
                description=str(snapshot.get("summary") or ""),
                status="PUBLISHED",
                statusLabel="已发布",
                version=detail["version"].get("version"),
                evidenceCount=len(evidence_ids),
                sourceId=function_id,
                tags=_normalise_tags(snapshot.get("tags") or snapshot.get("knowledge_tags") or snapshot.get("knowledgeTags")),
            )

            for tag in _normalise_tags(snapshot.get("tags") or snapshot.get("knowledge_tags") or snapshot.get("knowledgeTags")):
                tag_key = str(tag.get("key") or tag.get("canonicalKey") or tag.get("name") or "").strip()
                if not tag_key:
                    continue
                tag_node_id = add_node(
                    f"TAG:{tag_key.casefold()}",
                    "TAG",
                    str(tag.get("name") or tag_key),
                    subtitle=str(tag.get("typeLabel") or tag.get("type") or "知识标签"),
                    description=str(tag.get("alias") or ""),
                    statusLabel="已归一化",
                    sourceId=tag_key,
                    evidenceCount=len(_as_list(tag.get("evidence_ids") or tag.get("evidenceIds"))),
                )
                add_edge(
                    function_node_id,
                    tag_node_id,
                    "HAS_TAG",
                    evidence_ids=_as_list(tag.get("evidence_ids") or tag.get("evidenceIds")),
                )

            for entry in _as_list(snapshot.get("entries")):
                target_id = str(entry.get("target_id") or entry.get("targetId") or "").strip()
                if not target_id:
                    continue
                locator = str(entry.get("locator") or entry.get("label") or target_id)
                code_node_id = add_node(
                    f"CODE:{target_id}",
                    "CODE",
                    locator,
                    subtitle=str(entry.get("entry_type") or entry.get("entryType") or "代码入口"),
                    description=str(entry.get("label") or ""),
                    statusLabel="入口已关联",
                    sourceId=target_id,
                    evidenceCount=len(_as_list(entry.get("evidence_ids") or entry.get("evidenceIds"))),
                )
                add_edge(function_node_id, code_node_id, "IMPLEMENTED_BY", evidence_ids=_as_list(entry.get("evidence_ids") or entry.get("evidenceIds")))

            for evidence_id in evidence_ids:
                self._add_evidence_relation(evidence_id, function_node_id, add_node, add_edge)

        # Requirement relations contain stronger links than lexical co-occurrence.
        rows = self.db.execute(
            """SELECT rr.*,rv.requirement_id,rv.version requirement_version,r.title requirement_title
                 FROM requirement_relation rr
                 JOIN requirement_version rv ON rv.id=rr.requirement_version_id
                 JOIN requirement r ON r.id=rv.requirement_id
                WHERE rv.version=r.current_version"""
        ).fetchall()
        for row in rows:
            requirement_node_id = add_node(
                f"REQUIREMENT:{row['requirement_id']}",
                "REQUIREMENT",
                str(row["requirement_title"] or row["requirement_id"]),
                subtitle=f"{row['requirement_id']} V{row['requirement_version']}",
                description=str(row["reason"] or ""),
                statusLabel="需求依据",
                version=row["requirement_version"],
                sourceId=row["requirement_id"],
                evidenceCount=1 if row["requirement_evidence_id"] else 0,
            )
            target_type = str(row["target_type"] or "").upper()
            target_id = str(row["target_id"] or "")
            if target_type == "BUSINESS_FUNCTION":
                target_node_id = f"FUNCTION:{target_id}"
                if target_node_id not in nodes:
                    continue
            elif target_id:
                target_node_id = add_node(
                    f"CODE:{target_id}",
                    "CODE",
                    _code_label(target_id),
                    subtitle=target_type or "代码对象",
                    statusLabel=str(row["status"] or "建议关联"),
                    sourceId=target_id,
                    evidenceCount=1 if row["code_evidence_id"] else 0,
                )
            else:
                continue
            add_edge(
                requirement_node_id,
                target_node_id,
                "REQUIREMENT_LINK",
                status=str(row["status"] or "SUGGESTED"),
                evidence_ids=[row["requirement_evidence_id"], row["code_evidence_id"]],
            )

        # Keep current requirements discoverable even before they are enriched.
        for row in self.db.execute("SELECT id,title,current_version,status FROM requirement ORDER BY updated_at DESC LIMIT 100"):
            add_node(
                f"REQUIREMENT:{row['id']}",
                "REQUIREMENT",
                str(row["title"] or row["id"]),
                subtitle=f"{row['id']} V{row['current_version']}",
                statusLabel="需求依据",
                version=row["current_version"],
                sourceId=row["id"],
            )

        # Code facts remain queryable even when an older published snapshot did
        # not yet bind every symbol to a function entry. They are shown in the
        # dedicated Code filter or when a query matches a symbol/fact.
        if node_type == "CODE" or query:
            self._add_code_fact_nodes(add_node)

        visible_ids = set(nodes)
        if node_type:
            visible_ids = {node_id for node_id, node in nodes.items() if node["type"] == node_type}
        if query:
            matched = {
                node_id for node_id, node in nodes.items()
                if query in json.dumps(node, ensure_ascii=False, default=str).casefold()
            }
            related = {
                edge["source"] if edge["target"] in matched else edge["target"]
                for edge in edges.values()
                if edge["source"] in matched or edge["target"] in matched
            }
            visible_ids &= matched | related

        visible_nodes = [node for node_id, node in nodes.items() if node_id in visible_ids][:limit]
        visible_id_set = {node["id"] for node in visible_nodes}
        visible_edges = [edge for edge in edges.values() if edge["source"] in visible_id_set and edge["target"] in visible_id_set]
        counts = defaultdict(int)
        for node in visible_nodes:
            counts[_count_key(node["type"])] += 1
        all_counts = defaultdict(int)
        for node in nodes.values():
            all_counts[_count_key(node["type"])] += 1
        if "code" not in all_counts:
            all_counts["code"] = int(self.db.execute("SELECT count(DISTINCT symbol_id) FROM code_fact").fetchone()[0])
        return {
            "nodes": visible_nodes,
            "edges": visible_edges,
            "counts": dict(counts),
            "allCounts": dict(all_counts),
            "query": query,
            "nodeType": node_type or "ALL",
        }

    def _add_code_fact_nodes(self, add_node) -> None:
        rows = self.db.execute(
            """SELECT cs.id,cs.qualified_name,cs.kind,cfile.path,
                      count(cf.id) fact_count,
                      group_concat(cf.fact_type || ': ' || coalesce(cf.subject, '') || ' -> ' || coalesce(cf.target, ''), ' | ') facts
                 FROM code_symbol cs
                 JOIN code_file cfile ON cfile.id=cs.file_id
                 JOIN code_fact cf ON cf.symbol_id=cs.id
                GROUP BY cs.id,cs.qualified_name,cs.kind,cfile.path
                ORDER BY cs.qualified_name LIMIT 80"""
        ).fetchall()
        for row in rows:
            add_node(
                f"CODE:{row['id']}",
                "CODE",
                str(row["qualified_name"] or row["id"]),
                subtitle=f"{row['kind'] or '代码对象'} · {row['path'] or '未定位文件'}",
                description=str(row["facts"] or ""),
                statusLabel="代码事实",
                sourceId=row["id"],
                evidenceCount=int(row["fact_count"] or 0),
            )

    def _add_evidence_relation(self, evidence_id, function_node_id, add_node, add_edge):
        row = self.db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            return
        source_type = str(row["source_type"] or "").upper()
        if source_type == "REQUIREMENT":
            requirement = self.db.execute(
                "SELECT id,title,current_version FROM requirement WHERE id=?", (row["source_id"],)
            ).fetchone()
            label = str(requirement["title"] if requirement else row["source_id"])
            version = requirement["current_version"] if requirement else row["source_version"]
            node_id = add_node(
                f"REQUIREMENT:{row['source_id']}",
                "REQUIREMENT",
                label,
                subtitle=f"{row['source_id']} V{version}",
                statusLabel="需求依据",
                sourceId=row["source_id"],
                version=version,
                evidenceCount=1,
            )
            add_edge(function_node_id, node_id, "SUPPORTED_BY", evidence_ids=[evidence_id])
            return
        if source_type in {"CODE", "CODE_CHANGE"}:
            symbol_rows = self.db.execute(
                """SELECT cs.id,cs.qualified_name,cs.kind
                     FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                    WHERE cf.evidence_id=? ORDER BY cs.qualified_name LIMIT 5""",
                (evidence_id,),
            ).fetchall()
            for symbol in symbol_rows:
                node_id = add_node(
                    f"CODE:{symbol['id']}",
                    "CODE",
                    str(symbol["qualified_name"]),
                    subtitle=str(symbol["kind"] or "代码对象"),
                    statusLabel="代码证据",
                    sourceId=symbol["id"],
                    evidenceCount=1,
                )
                add_edge(function_node_id, node_id, "SUPPORTED_BY", evidence_ids=[evidence_id])
            return
        if source_type in {"MANUAL", "DOCUMENT", "USER_FEEDBACK"} and row["source_id"] != function_node_id.removeprefix("FUNCTION:"):
            node_id = add_node(
                f"BUSINESS:{row['source_id']}",
                "BUSINESS",
                str(row["source_id"]),
                subtitle=_type_label(source_type),
                description=str(row["excerpt"] or ""),
                statusLabel="人工依据",
                sourceId=row["source_id"],
                evidenceCount=1,
            )
            add_edge(function_node_id, node_id, "SUPPORTED_BY", evidence_ids=[evidence_id])


def _snapshot_evidence(snapshot: dict[str, Any]) -> list[str]:
    values = [*(_as_list(snapshot.get("evidence_ids") or snapshot.get("evidenceIds")))]
    for collection in ("tags", "knowledge_tags", "knowledgeTags", "scenarios", "rules", "entries", "data_impacts", "dataImpacts"):
        for item in _as_list(snapshot.get(collection)):
            if isinstance(item, dict):
                values.extend(_as_list(item.get("evidence_ids") or item.get("evidenceIds")))
    return list(dict.fromkeys(str(value) for value in values if value))


def _normalise_tags(value) -> list[dict[str, str]]:
    result = []
    for item in _as_list(value):
        if isinstance(item, str):
            result.append({"name": item, "key": item, "type": "TAG", "typeLabel": "知识标签"})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("label") or item.get("value") or "").strip()
        key = str(item.get("canonical_key") or item.get("canonicalKey") or item.get("key") or name).strip()
        if name:
            result.append({
                "name": name,
                "key": key,
                "type": str(item.get("type") or item.get("tag_type") or "TAG"),
                "typeLabel": str(item.get("typeLabel") or item.get("type_label") or item.get("type") or "知识标签"),
                "alias": str(item.get("alias") or ""),
            })
    return result


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _type_label(value: str) -> str:
    return {
        "FUNCTION": "功能",
        "REQUIREMENT": "需求",
        "CODE": "代码",
        "TAG": "知识标签",
        "BUSINESS": "业务补充",
        "MANUAL": "业务补充",
        "DOCUMENT": "文档",
        "USER_FEEDBACK": "用户反馈",
    }.get(str(value).upper(), str(value))


def _relation_label(value: str) -> str:
    return {
        "HAS_TAG": "包含标签",
        "IMPLEMENTED_BY": "代码实现",
        "SUPPORTED_BY": "证据支持",
        "REQUIREMENT_LINK": "需求关联",
    }.get(value, value)


def _count_key(value: str) -> str:
    return {"FUNCTION": "functions", "REQUIREMENT": "requirements", "CODE": "code", "TAG": "tags", "BUSINESS": "business"}.get(value, value.casefold())


def _code_label(target_id: str) -> str:
    return target_id

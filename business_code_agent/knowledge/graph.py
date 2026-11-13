from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


class KnowledgeGraphService:
    """Read-only graph projection built from published business knowledge.

    The graph is deliberately a projection, not a second source of truth.
    Nodes point back to the baseline entity, relation or entry anchor that
    produced them; code facts are investigated by Claude Code at runtime
    and are intentionally not part of this projection.
    """

    NODE_TYPES = {
        "SYSTEM", "BUSINESS_TERM", "CAPABILITY", "FLOW", "RULE",
        "PROJECT", "ENTRY_ANCHOR",
    }

    def __init__(self, db):
        self.db = db

    def search(self, query: str = "", node_type: str = "", limit: int = 120) -> dict[str, Any]:
        query = str(query or "").strip().casefold()
        node_type = str(node_type or "").strip().upper()
        if node_type and node_type not in self.NODE_TYPES:
            raise ValueError(f"unsupported graph node type: {node_type}")
        limit = max(20, min(int(limit), 240))
        return _baseline_graph(self.db, query, node_type, limit)


def _baseline_graph(db, query: str, node_type: str, limit: int) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def add_node(node_id: str, kind: str, label: str, **metadata):
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id, "type": kind, "typeLabel": _type_label(kind), "label": label,
                **{key: value for key, value in metadata.items() if value is not None},
            }
        return node_id

    def add_edge(source: str, target: str, relation: str, status="CONFIRMED", evidence_ids=()):
        edge_id = f"{source}:{relation}:{target}"
        edges[edge_id] = {
            "id": edge_id, "source": source, "target": target, "relation": relation,
            "label": _relation_label(relation), "status": status,
            "evidenceIds": list(dict.fromkeys(value for value in evidence_ids if value)),
        }

    for row in db.execute("SELECT * FROM business_entity WHERE status!='DEPRECATED' ORDER BY entity_type,name"):
        add_node(
            f"BUSINESS:{row['id']}", row["entity_type"], row["name"],
            subtitle=_type_label(row["entity_type"]), description=row["definition"],
            status=row["status"], statusLabel="人工业务基线" if row["source_type"] == "HUMAN" else row["source_type"],
            sourceId=row["id"], evidenceCount=1 if row["source_evidence_id"] else 0,
            aliases=json.loads(row["aliases_json"]),
        )
    for row in db.execute("SELECT * FROM business_relation_v2 WHERE status!='DEPRECATED'"):
        source = f"BUSINESS:{row['from_entity_id']}" if row['from_entity_id'] else f"LABEL:{row['from_label']}"
        target = f"BUSINESS:{row['to_entity_id']}" if row['to_entity_id'] else f"LABEL:{row['to_label']}"
        if source not in nodes:
            add_node(source, "BUSINESS_TERM", row["from_label"], subtitle="关系端点", statusLabel="人工业务基线")
        if target not in nodes:
            add_node(target, "BUSINESS_TERM", row["to_label"], subtitle="关系端点", statusLabel="人工业务基线")
        add_edge(source, target, row["relation_type"], row["status"], [row["evidence_id"]])
    # Entry anchors are the only durable business-to-code navigation hints.
    # They intentionally stop at an application/name pair; the current symbol
    # and all implementation facts are resolved by Claude Code at runtime.
    for row in db.execute(
        """SELECT ea.*,a.name application_name,ss.name system_name
             FROM business_entry_anchor ea
             JOIN application a ON a.id=ea.application_id
             LEFT JOIN software_system ss ON ss.id=a.system_id
            WHERE ea.status IN ('ACTIVE','VERIFIED','CANDIDATE')
            ORDER BY a.name,ea.entry_type,ea.entry_name"""
    ):
        business = f"BUSINESS:{row['business_id']}"
        if business not in nodes:
            continue
        application = add_node(
            f"APPLICATION:{row['application_id']}", "PROJECT", row["application_name"],
            subtitle="应用", statusLabel=row["system_name"] or "项目上下文",
            sourceId=row["application_id"],
        )
        entry = add_node(
            f"ENTRY_ANCHOR:{row['id']}", "ENTRY_ANCHOR", row["entry_name"],
            subtitle=row["entry_type"], statusLabel=row["status"],
            sourceId=row["id"], applicationId=row["application_id"],
        )
        add_edge(business, entry, "HAS_ENTRY", row["status"])
        add_edge(entry, application, "BELONGS_TO_APPLICATION", row["status"])

    visible_ids = set(nodes)
    if node_type:
        matched_type = {node_id for node_id, node in nodes.items() if node["type"] == node_type}
        neighbors = {
            edge["source"] if edge["target"] in matched_type else edge["target"]
            for edge in edges.values() if edge["source"] in matched_type or edge["target"] in matched_type
        }
        visible_ids = matched_type | neighbors
    if query:
        matched = {
            node_id for node_id, node in nodes.items()
            if query in json.dumps(node, ensure_ascii=False, default=str).casefold()
        }
        neighbors = {
            edge["source"] if edge["target"] in matched else edge["target"]
            for edge in edges.values() if edge["source"] in matched or edge["target"] in matched
        }
        visible_ids &= matched | neighbors
    visible_nodes = [node for node_id, node in nodes.items() if node_id in visible_ids][:limit]
    visible_set = {node["id"] for node in visible_nodes}
    visible_edges = [edge for edge in edges.values() if edge["source"] in visible_set and edge["target"] in visible_set]
    counts = defaultdict(int)
    all_counts = defaultdict(int)
    for node in visible_nodes: counts[_count_key(node["type"])] += 1
    for node in nodes.values(): all_counts[_count_key(node["type"])] += 1
    return {
        "nodes": visible_nodes, "edges": visible_edges, "counts": dict(counts),
        "allCounts": dict(all_counts), "query": query, "nodeType": node_type or "ALL",
    }


def _type_label(value: str) -> str:
    return {
        "PROJECT": "应用",
        "ENTRY_ANCHOR": "调查入口",
        "SYSTEM": "系统",
        "BUSINESS_TERM": "业务术语",
        "CAPABILITY": "业务能力",
        "FLOW": "业务流程",
        "RULE": "业务规则",
    }.get(str(value).upper(), str(value))


def _relation_label(value: str) -> str:
    return {
        "TRIGGERS": "触发",
        "PRODUCES": "产生",
        "BELONGS_TO": "属于",
        "DEPENDS_ON": "依赖",
        "HANDLED_BY": "由…处理",
        "HAS_ENTRY": "包含入口",
        "BELONGS_TO_APPLICATION": "归属应用",
    }.get(value, value)


def _count_key(value: str) -> str:
    return {
        "PROJECT": "projects",
        "ENTRY_ANCHOR": "entryAnchors",
        "SYSTEM": "systems", "BUSINESS_TERM": "terms", "CAPABILITY": "capabilities",
        "FLOW": "flows", "RULE": "rules",
    }.get(value, value.casefold())

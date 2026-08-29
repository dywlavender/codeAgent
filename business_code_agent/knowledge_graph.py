from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

class KnowledgeGraphService:
    """Read-only graph projection built from published knowledge and Evidence.

    The graph is deliberately a projection, not a second source of truth. Nodes
    point back to the published function snapshot, requirement version, code
    symbol, or Evidence record that created the relation.
    """

    NODE_TYPES = {
        "FUNCTION", "PROJECT", "CODE", "TABLE", "TAG",
        "SYSTEM", "BUSINESS_TERM", "CAPABILITY", "FLOW", "RULE",
    }

    def __init__(self, db):
        self.db = db

    def search(self, query: str = "", node_type: str = "", limit: int = 120) -> dict[str, Any]:
        query = str(query or "").strip().casefold()
        node_type = str(node_type or "").strip().upper()
        if node_type and node_type not in self.NODE_TYPES:
            raise ValueError(f"unsupported graph node type: {node_type}")
        limit = max(20, min(int(limit), 240))
        if self.db.execute("SELECT 1 FROM business_entity WHERE status!='DEPRECATED' LIMIT 1").fetchone():
            return _baseline_graph(self.db, query, node_type, limit)

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

        # Human function definitions are the centre; every other node is a
        # navigation projection that points back to current code evidence.
        for row in self.db.execute("SELECT * FROM functional_knowledge WHERE status='ACTIVE' ORDER BY name"):
            function_id = str(row["id"])
            tags = _as_list(json.loads(row["tags_json"]))
            evidence_count = self.db.execute(
                "SELECT count(DISTINCT evidence_id) FROM functional_retrieval_link WHERE function_id=? AND evidence_id IS NOT NULL",
                (function_id,),
            ).fetchone()[0]
            function_node_id = add_node(
                f"FUNCTION:{function_id}",
                "FUNCTION",
                str(row["name"]),
                subtitle="业务功能",
                description=str(row["summary"]),
                status="ACTIVE",
                statusLabel="功能文档",
                evidenceCount=evidence_count,
                sourceId=function_id,
                tags=[{"name": tag, "key": tag} for tag in tags],
            )
            for tag_key in tags:
                tag_node_id = add_node(
                    f"TAG:{tag_key.casefold()}",
                    "TAG",
                    tag_key,
                    subtitle="功能标签",
                    statusLabel="人工定义",
                    sourceId=tag_key,
                )
                add_edge(function_node_id, tag_node_id, "HAS_TAG")

            for entry in self.db.execute("SELECT * FROM functional_entry_anchor WHERE function_id=?", (function_id,)):
                project_node_id = add_node(
                    f"PROJECT:{entry['project_name']}", "PROJECT", entry["project_name"],
                    subtitle="工程或模块", statusLabel="人工登记", sourceId=entry["project_name"],
                )
                add_edge(function_node_id, project_node_id, "INVOLVES_PROJECT")
                if not entry["symbol_id"]:
                    continue
                symbol = self.db.execute(
                    "SELECT qualified_name FROM code_symbol WHERE id=?", (entry["symbol_id"],)
                ).fetchone()
                code_node_id = add_node(
                    f"CODE:{entry['symbol_id']}",
                    "CODE",
                    str(symbol["qualified_name"] if symbol else entry["class_name"]),
                    subtitle=f"{entry['entry_type']}入口",
                    statusLabel="已定位" if entry["resolution_status"] == "RESOLVED" else entry["resolution_status"],
                    sourceId=entry["symbol_id"],
                )
                add_edge(project_node_id, code_node_id, "HAS_ENTRY")
                add_edge(function_node_id, code_node_id, "ENTRY_POINT")

            for table in self.db.execute("SELECT * FROM functional_key_table WHERE function_id=?", (function_id,)):
                table_node_id = add_node(
                    f"TABLE:{table['table_name'].casefold()}", "TABLE", table["table_name"],
                    subtitle="关键表", description=table["purpose"], statusLabel="人工登记",
                    sourceId=table["table_name"],
                )
                add_edge(function_node_id, table_node_id, "KEY_TABLE")
                for link in self.db.execute(
                    """SELECT * FROM functional_retrieval_link
                        WHERE function_id=? AND source_type='TABLE' AND source_id=?""",
                    (function_id, table["table_name"]),
                ):
                    code_id = f"CODE:{link['target_id']}"
                    symbol = self.db.execute("SELECT qualified_name FROM code_symbol WHERE id=?", (link["target_id"],)).fetchone()
                    if not symbol:
                        continue
                    add_node(code_id, "CODE", symbol["qualified_name"], subtitle="数据访问", statusLabel="代码事实", sourceId=link["target_id"], evidenceCount=1)
                    add_edge(code_id, table_node_id, link["relation_type"], evidence_ids=[link["evidence_id"]])

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
        "PROJECT": "工程",
        "CODE": "代码",
        "TABLE": "数据表",
        "TAG": "知识标签",
        "SYSTEM": "系统",
        "BUSINESS_TERM": "业务术语",
        "CAPABILITY": "业务能力",
        "FLOW": "业务流程",
        "RULE": "业务规则",
    }.get(str(value).upper(), str(value))


def _relation_label(value: str) -> str:
    return {
        "HAS_TAG": "包含标签",
        "INVOLVES_PROJECT": "涉及工程",
        "HAS_ENTRY": "包含入口",
        "ENTRY_POINT": "功能入口",
        "KEY_TABLE": "关键数据",
        "READ_TABLE": "读取",
        "WRITE_TABLE": "写入",
        "TRIGGERS": "触发",
        "PRODUCES": "产生",
        "BELONGS_TO": "属于",
        "DEPENDS_ON": "依赖",
        "HANDLED_BY": "由…处理",
        "OWNED_BY": "归属代码",
        "REPRESENTED_BY": "代码表示",
        "IMPLEMENTED_BY": "代码实现",
        "ENFORCED_BY": "代码校验",
        "EVIDENCED_BY": "代码证据",
    }.get(value, value)


def _count_key(value: str) -> str:
    return {
        "FUNCTION": "functions", "PROJECT": "projects", "CODE": "code", "TABLE": "tables", "TAG": "tags",
        "SYSTEM": "systems", "BUSINESS_TERM": "terms", "CAPABILITY": "capabilities",
        "FLOW": "flows", "RULE": "rules",
    }.get(value, value.casefold())


def _code_label(target_id: str) -> str:
    return target_id


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
        source = f"BUSINESS:{row['from_entity_id']}" if row["from_entity_id"] else f"LABEL:{row['from_label']}"
        target = f"BUSINESS:{row['to_entity_id']}" if row["to_entity_id"] else f"LABEL:{row['to_label']}"
        if source not in nodes:
            add_node(source, "BUSINESS_TERM", row["from_label"], subtitle="关系端点", statusLabel="人工业务基线")
        if target not in nodes:
            add_node(target, "BUSINESS_TERM", row["to_label"], subtitle="关系端点", statusLabel="人工业务基线")
        add_edge(source, target, row["relation_type"], row["status"], [row["evidence_id"]])
    for row in db.execute(
        """SELECT bcm.*,cs.qualified_name,cs.kind
             FROM business_code_mapping bcm
             LEFT JOIN code_symbol cs ON cs.id=bcm.code_symbol_id
            WHERE bcm.status IN ('VERIFIED','CANDIDATE') AND bcm.code_symbol_id IS NOT NULL
            ORDER BY bcm.confidence DESC"""
    ):
        business = f"BUSINESS:{row['business_id']}"
        if business not in nodes:
            continue
        code = add_node(
            f"CODE:{row['code_symbol_id']}", "CODE", row["qualified_name"] or row["code_reference"],
            subtitle=row["kind"] or "代码对象", statusLabel="代码映射", sourceId=row["code_symbol_id"],
            evidenceCount=len(json.loads(row["evidence_ids_json"])),
        )
        add_edge(
            business, code, row["relation_type"], row["status"],
            json.loads(row["evidence_ids_json"]),
        )

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

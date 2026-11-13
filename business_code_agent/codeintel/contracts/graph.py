"""Read-only call-chain projection of a prepared project contract snapshot."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from .index import read_index
from .resolve import unique_refs


GAP_LABELS = {
    "http_base_url_unresolved": "调用地址未解析",
    "http_configuration_unresolved": "调用配置未解析",
    "http_target_ambiguous": "目标接口存在歧义",
    "http_target_not_indexed_or_matched": "目标接口未匹配",
    "dynamic_http_uri": "动态地址未解析",
    "call_target_unresolved": "方法目标未解析",
}


class ContractGraph:
    def __init__(self, root, project_id, repositories):
        self.root, self.project_id = Path(root), project_id
        self.repositories = {key: Path(value).resolve() for key, value in repositories.items()}
        self.snapshot = read_index(root, project_id) if (self.root / "contracts.sqlite").is_file() else None
        self.methods, self.types = {}, {}
        if self.snapshot:
            for item in self.snapshot["files"].values():
                self.methods.update((m["id"], m) for m in item["unit"].get("methods", []))
                self.types.update((t["id"], t) for t in item["unit"].get("types", []))
        self.clients = {edge["source"] for entry in (self.snapshot or {}).get("entries", {}).values()
                        for edge in entry["response"]["edges"] if edge["kind"] == "HTTP_RESPONSE"}
        self.checked = {}

    def _version(self, version=None):
        if not self.snapshot:
            raise ValueError("尚未准备调用链索引")
        if version and version != self.snapshot["version"]:
            raise ValueError("调用链索引已更新，请重新选择接口")

    def _file(self, path):
        parts = path.split("/", 2)
        if len(parts) != 3 or parts[0] != "repos" or parts[1] not in self.repositories:
            return None
        root = self.repositories[parts[1]]
        file = (root / parts[2]).resolve()
        if not file.is_relative_to(root):
            return None
        return file

    def _state(self, refs):
        states = []
        for ref in unique_refs(refs):
            path = ref["path"]
            if path not in self.checked:
                try:
                    file = self._file(path)
                    content = file.read_text(encoding="utf-8-sig") if file else None
                    self.checked[path] = "unavailable" if content is None else "current" if content == self.snapshot["files"][path]["content"] else "stale"
                except (OSError, KeyError):
                    self.checked[path] = "unavailable"
            states.append(self.checked[path])
        return "unavailable" if "unavailable" in states else "stale" if "stale" in states else "current"

    def entries(self, query=""):
        if not self.snapshot:
            return {"projectId": self.project_id, "status": "missing", "items": [], "message": "尚未准备调用链索引，请先显式准备工程契约索引。"}
        items = []
        for entry in self.snapshot["entries"].values():
            if not self._file(entry["ref"]["path"]):
                continue
            text = " ".join([entry["applicationName"], entry["symbol"], entry["route"]["path"], entry["searchText"]])
            if query.strip().casefold() not in text.casefold():
                continue
            items.append({"id": entry["id"], "application": entry["applicationName"],
                          "method": entry["route"]["method"], "path": entry["route"]["path"], "symbol": entry["symbol"],
                          "httpCount": len({(e["source"], e["target"]) for e in entry["response"]["edges"] if e["kind"] == "HTTP_RESPONSE"})})
        return {"projectId": self.project_id, "status": "ready", "indexVersion": self.snapshot["version"],
                "items": sorted(items, key=lambda e: (e["application"], e["path"], e["method"]))}

    def chain(self, contract_id, version=None):
        self._version(version)
        entry = self.snapshot["entries"].get(contract_id)
        if not entry or not self._file(entry["ref"]["path"]):
            raise ValueError("接口不属于当前工程索引")
        edges = {}
        for edge in entry["response"]["edges"]:
            if edge["kind"] not in {"RETURN_CALL", "HTTP_RESPONSE"}:
                continue
            key = (edge["source"], edge["target"], edge["kind"])
            edges.setdefault(key, {**edge, "refs": []})["refs"].extend(edge["refs"])
        gaps = list({(g["methodId"], g["reason"]): g for g in entry["response"]["gaps"] if g.get("methodId") in self.methods
                     and (g["reason"].startswith(("http_", "dynamic_http")) or g["reason"] == "call_target_unresolved")}.values())
        outgoing, incoming = defaultdict(list), defaultdict(list)
        for edge in edges.values():
            outgoing[edge["source"]].append(edge)
            incoming[edge["target"]].append(edge)
        root = entry["methodId"]
        reachable, queue, traversal = {root}, deque([root]), []
        while queue:
            method_id = queue.popleft()
            traversal.append(method_id)
            for edge in outgoing[method_id]:
                if edge["target"] not in reachable:
                    reachable.add(edge["target"])
                    queue.append(edge["target"])
        targets = {e["source"] for e in edges.values() if e["kind"] == "HTTP_RESPONSE" and e["source"] in reachable}
        targets.update(g["methodId"] for g in gaps if g["methodId"] in reachable)
        visible = {root}
        if targets:
            queue = deque(targets)
            visible.update(targets)
            while queue:
                for edge in incoming[queue.popleft()]:
                    if edge["source"] in reachable and edge["source"] not in visible:
                        visible.add(edge["source"])
                        queue.append(edge["source"])
            visible.update(e["target"] for e in edges.values() if e["kind"] == "HTTP_RESPONSE" and e["source"] in visible)
        else:
            visible.update(mid for mid in reachable if not self._getter(mid))
        truncated = len(visible) > 70
        if truncated:
            visible = set([mid for mid in traversal if mid in visible][:70])
        nodes = {mid: self._node(mid, root) for mid in sorted(visible)}
        nodes[root]["route"] = entry["route"]["method"] + " " + entry["route"]["path"]
        nodes[root]["contractId"] = contract_id
        links = []
        for edge in edges.values():
            if edge["source"] not in nodes or edge["target"] not in nodes:
                continue
            refs = unique_refs(edge["refs"])
            links.append({"id": self._edge_id(edge), "source": edge["source"], "target": edge["target"],
                          "kind": edge["kind"], "label": "HTTP 调用" if edge["kind"] == "HTTP_RESPONSE" else "方法调用",
                          "state": self._state([*refs, nodes[edge["target"]]["location"]]), "evidenceCount": len(refs)})
        for gap in gaps:
            if gap["methodId"] not in nodes:
                continue
            gap_id = "unresolved:" + gap["methodId"] + ":" + gap["reason"]
            nodes[gap_id] = {"id": gap_id, "type": "UNRESOLVED", "label": GAP_LABELS.get(gap["reason"], "连接未解析"),
                             "application": "", "state": "unresolved", "location": gap["ref"], "reason": gap["reason"]}
            links.append({"id": gap_id, "source": gap["methodId"], "target": gap_id,
                          "kind": "UNRESOLVED", "label": "待解析", "state": "unresolved", "evidenceCount": 1})
        return {"projectId": self.project_id, "indexVersion": self.snapshot["version"], "contractId": contract_id,
                "rootId": root, "nodes": list(nodes.values()), "edges": links, "truncated": truncated,
                "staticOnly": True, "coverage": "已索引的返回依赖调用；不代表完整执行轨迹"}

    def _getter(self, method_id):
        method = self.methods[method_id]
        owner = self.types[method["typeId"]]
        return len(method["returns"]) == 1 and method["returns"][0].get("kind") == "name" and method["returns"][0].get("name") in owner["fields"]

    def _node(self, method_id, root):
        method = self.methods[method_id]
        owner = self.types[method["typeId"]]
        app = next((a for a in self.snapshot["applications"] if a["id"] == owner["applicationId"]), {})
        endpoint = next((e for e in self.snapshot["entries"].values() if e["methodId"] == method_id), None)
        client = method_id in self.clients
        client = client or any(owner["imports"].get(value, value) == "org.springframework.web.client.RestClient" for value in owner["fields"].values())
        return {"id": method_id, "type": "ENDPOINT" if endpoint else "HTTP_CLIENT" if client else "METHOD",
                "label": owner["name"] + "." + method["name"], "application": app.get("name", owner["repositoryId"]),
                "route": endpoint["route"]["method"] + " " + endpoint["route"]["path"] if endpoint else "",
                "contractId": endpoint["id"] if endpoint else None, "isRoot": method_id == root,
                "location": method["ref"], "state": self._state([method["ref"]])}

    @staticmethod
    def _edge_id(edge):
        return edge["kind"] + ":" + edge["source"] + ":" + edge["target"]

    def evidence(self, contract_id, *, version, node_id=None, edge_id=None):
        if not version:
            raise ValueError("查看源码依据需要索引版本，请重新选择接口")
        chain = self.chain(contract_id, version)
        entry = self.snapshot["entries"][contract_id]
        refs = []
        if node_id:
            node = next((n for n in chain["nodes"] if n["id"] == node_id), None)
            if node:
                refs = [node["location"]]
        elif edge_id:
            edge = next((e for e in chain["edges"] if e["id"] == edge_id), None)
            if edge:
                refs = [ref for e in entry["response"]["edges"] if self._edge_id(e) == edge_id for ref in e["refs"]]
                if edge["kind"] == "UNRESOLVED":
                    refs = [n["location"] for n in chain["nodes"] if n["id"] == edge["target"]]
        if not refs:
            raise ValueError("请选择当前调用链中的节点或连线")
        blocks, lines, omitted = [], 0, 0
        for ref in unique_refs(refs):
            if lines >= 240 or not self._file(ref["path"]):
                omitted += 1
                continue
            original = self.snapshot["files"][ref["path"]]["content"].splitlines()
            end = min(ref["end"], ref["start"] + 240 - lines - 1)
            blocks.append({**ref, "end": end, "state": self._state([ref]),
                           "content": "\n".join(f"{n}: {original[n - 1]}" for n in range(ref["start"], end + 1)), "truncated": end < ref["end"]})
            lines += end - ref["start"] + 1
        return {"projectId": self.project_id, "indexVersion": self.snapshot["version"], "blocks": blocks,
                "omittedBlocks": omitted, "source": "indexed_snapshot"}

"""Read prepared contracts and deliver bounded, current, in-scope evidence."""

from __future__ import annotations

import json
from pathlib import Path

from business_code_agent.codeintel.retrieval import TextRetriever
from .index import read_index
from .resolve import unique_refs


def label(method_id):
    return method_id.split("::")[-1].split("@")[0]


class ContractIndex:
    def __init__(self, root, project_id, files):
        self.snapshot = read_index(root, project_id)
        self.files = files

    def find(self, question, limit=5):
        items = []
        for entry in self.snapshot["entries"].values():
            try:
                self.files.locate(entry["ref"]["path"])
            except ValueError:
                continue
            content = "\n".join([entry["applicationName"], entry["symbol"], entry["searchText"], *entry["response"]["fields"]])
            items.append({"id": entry["id"], "path": entry["route"]["path"], "content": content})
        ranked = TextRetriever(items).search(question, limit=min(8, max(1, limit)))
        return [{"contractId": item["id"], "score": round(item["score"], 4), **self.entry_header(item["id"])} for item in ranked]

    def entry_header(self, entry_id):
        entry = self.snapshot["entries"][entry_id]
        return {"application": entry["applicationName"], "method": entry["route"]["method"],
                "route": entry["route"]["path"], "symbol": entry["symbol"], "location": entry["ref"]}

    def bundle(self, entry_id, *, char_limit=16000, line_limit=260, view="response"):
        if entry_id not in self.snapshot["entries"]:
            raise ValueError("契约编号不在当前索引中，请先 FindCapability")
        entry = self.snapshot["entries"][entry_id]
        self.files.locate(entry["ref"]["path"])
        result = {"projectId": self.snapshot["projectId"], "indexVersion": self.snapshot["version"],
                  "contractId": entry_id, "entry": self.entry_header(entry_id), "fields": [], "mappings": [],
                  "connections": [], "gaps": [], "evidence": [], "evidenceLines": 0,
                  "businessRules": [],
                  "consumer": "unconfirmed", "semanticProof": False,
                  "coverage": {"repositories": self.snapshot["coveredRepositories"],
                               "parsers": ["spring-controller", "spring-restclient", "map-of", "record-constructor", "string-switch"]}}
        selected, checked = {}, {}
        size = lambda value: len(json.dumps(value, ensure_ascii=False))
        if size(result) + 250 > char_limit:
            raise ValueError("契约上下文预算过小")

        def add_gap(gap):
            if gap in result["gaps"]:
                return
            if len(result["gaps"]) < 8 and size(result) + size(gap) + 100 <= char_limit:
                result["gaps"].append(gap)

        def current(ref):
            path = ref["path"]
            if path not in checked:
                try:
                    file, _ = self.files.locate(path)
                    checked[path] = file.read_text(encoding="utf-8-sig") == self.snapshot["files"][path]["content"]
                except (OSError, ValueError, KeyError):
                    checked[path] = False
            if not checked[path]:
                add_gap({"reason": "source_out_of_scope_or_changed", "nextRead": {"path": path, "offset": ref["start"], "limit": min(100, ref["end"] - ref["start"] + 4)}})
            return checked[path]

        def admit(category, item, refs):
            ids, proposed = [], []
            for ref in unique_refs(refs):
                key = (ref["path"], ref["start"], ref["end"])
                if key in selected:
                    ids.append(selected[key])
                    continue
                covering = next((eid for (path, start, end), eid in selected.items()
                                 if path == key[0] and start <= key[1] and end >= key[2]), None)
                covering = covering or next((value["id"] for (path, start, end), value in proposed
                                            if path == key[0] and start <= key[1] and end >= key[2]), None)
                if covering:
                    ids.append(covering)
                    continue
                path = ref["path"]
                if not current(ref):
                    return
                lines = self.snapshot["files"][path]["content"].splitlines()
                if not (1 <= ref["start"] <= ref["end"] <= len(lines)):
                    add_gap({"reason": "source_span_invalid"})
                    return
                eid = ("U" if view == "business" else "E") + str(len(result["evidence"]) + len(proposed) + 1)
                content = "\n".join(f"{n}: {lines[n - 1]}" for n in range(ref["start"], ref["end"] + 1))
                proposed.append((key, {"id": eid, **ref, "content": content}))
                ids.append(eid)
            if not ids:
                return
            extra_lines = sum(key[2] - key[1] + 1 for key, _ in proposed)
            value = {**item, "evidence": list(dict.fromkeys(ids))}
            # Commit fact and all required originals together; never cut a proof.
            growth = size(value) + size([v for _, v in proposed]) + 50
            if result["evidenceLines"] + extra_lines > line_limit or size(result) + growth + 250 > char_limit:
                add_gap({"reason": "evidence_context_budget"})
                return
            for key, source in proposed:
                selected[key] = source["id"]
                result["evidence"].append(source)
            result["evidenceLines"] += extra_lines
            result[category].append(value)

        for path, fact in entry["response"]["fields"].items():
            if path and view != "business":
                admit("fields", {"path": path, "conditional": fact.get("conditional", False)}, fact["refs"])
        if view == "business":
            ordered = sorted(entry["businessRules"], key=lambda r: (0 if r["phase"] == "catch" else 1 if r["conditions"] else 2, r["ref"]["path"], r["ref"]["start"]))
            for rule in ordered:
                if not all(current(ref) for ref in rule["dependencies"]):
                    continue
                item = {key: rule[key] for key in ("id", "fields", "phase", "conditions", "assigned", "conditionsComplete", "executionProven")}
                item["conditions"] = [{"expression": c["expression"], "truth": c["truth"]} for c in rule["conditions"]]
                item["requiresResponseFields"] = rule["fields"]
                item["method"] = label(rule["methodId"])
                admit("businessRules", item, rule["refs"])
            return result
        for mapping in entry["response"]["mappings"]:
            inputs = [path for path, fact in entry["response"]["fields"].items() if fact.get("origin") in mapping.get("inputOrigins", [])]
            rules = [{"inputs": rule["inputs"], "default": rule["default"],
                      "output": rule["output"].get("value") if rule["output"]["kind"] == "literal" else "input" if rule["output"].get("name") == mapping.get("inputName") else "unresolved"} for rule in mapping["rules"]]
            # A mapping without a verified argument binding cannot explain a response field.
            if not inputs or any(r["output"] == "unresolved" for r in rules):
                continue
            output = mapping.get("outputField")
            delivered = {field["path"] for field in result["fields"]}
            if output not in delivered or not all(path in delivered for path in inputs):
                continue
            output_fact = entry["response"]["fields"][output]
            refs = [*mapping.get("refs", [mapping["ref"]]), *output_fact.get("valueRefs", output_fact["refs"]), *entry["route"]["refs"]]
            admit("mappings", {"outputField": output, "inputFields": inputs, "rules": rules}, refs)
        for edge in entry["response"]["edges"]:
            item = {"kind": edge["kind"], "from": label(edge["source"]), "to": label(edge["target"])}
            if any(all(existing.get(k) == v for k, v in item.items()) for existing in result["connections"]):
                continue
            admit("connections", item, edge["refs"])
        for gap in entry["response"]["gaps"]:
            add_gap(gap)
        return result

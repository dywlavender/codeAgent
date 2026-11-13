"""Evidence-driven retrieval scheduling around the existing model/tool loop.

Rules create retrieval obligations, not business conclusions. Search rank and
matching source locations are navigation; only contract bundles carry typed
structural facts. This module never parses code or prepares an index.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from langchain_core.messages import AIMessage, ToolMessage

from .business_answer import question_needs
from .material import BUSINESS_MOUNTS, MATERIAL_ROLES, MaterialInvestigation, material_question


def merge_line_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def request_needs(question):
    needs = question_needs(question)
    fields = bool(re.search(r"返回|字段|响应|透传|支持|response|payload|\bDTO\b", question, re.I))
    local = bool(re.search(r"函数|方法|function|method", question, re.I))
    relations = bool(re.search(r"跨|调用|链路|流程|来源|哪.*系统|下游|上游|调用方|call\s*chain", question, re.I))
    field_list = bool(re.search(r"(?:哪些|什么|列出).*字段|字段.*(?:清单|列表|类型)", question))
    business = needs["business"] and not (field_list and not re.search(r"为什么|为何|失败|异常|条件|区分|改", question))
    return {"relations": relations, "fields": fields, "business": business,
            "consumer": needs["consumer"], "changes": needs["changes"],
            "material": material_question(question) is not None,
            "semantics": bool(re.search(r"业务含义|术语|约定|需求|业务规则|业务边界", question)),
            "local": local and not (relations or fields or needs["consumer"])}


class RetrievalCoordinator:
    def __init__(self, question, files, navigation, tool_names, contracts=None, *, max_actions=None):
        self.question, self.files, self.navigation = question, files, navigation
        self.available = set(tool_names)
        self.contracts = contracts
        self.needs = request_needs(question)
        self.material = MaterialInvestigation(material_question(question), files, navigation) if self.needs["material"] else None
        self.max_actions = max_actions
        self.actions, self.done, self.processed = [], set(), set()
        self.tasks, self.candidates, self.read_spans, self.file_lines = {}, {}, [], {}
        self.notices, self.decisions = [], []
        self.bindings = set()
        self.automatic_count = 0
        self._seed()

    @staticmethod
    def key(name, args):
        return name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)

    def task(self, key, objective, **values):
        task = self.tasks.setdefault(key, {"id": key, "objective": objective, "status": "open",
                                          "source": "program", "evidence": [], "semanticProof": False})
        task.update(values)
        return task

    def enqueue(self, name, args, reason, task=None):
        key = self.key(name, args)
        if name not in self.available or key in self.done or any(a["key"] == key for a in self.actions):
            return
        if self.max_actions is not None and self.automatic_count + len(self.actions) >= self.max_actions:
            if "程序检索达到动作预算；剩余事项仍未确认" not in self.notices:
                self.notices.append("程序检索达到动作预算；剩余事项仍未确认")
            return
        action = {"key": key, "name": name, "args": args, "reason": reason, "task": task}
        self.actions.append(action)
        self.decisions.append({"action": "schedule", "label": reason, "tool": name, "input": args, "task": task})

    def _seed(self):
        self.task("entry", "定位并读取本题代码入口")
        for kind, objective in (("relations", "取得调用端、连接配置和目标端原文"),
                                ("fields", "取得返回对象及字段生产、透传原文"),
                                ("business", "取得相关条件、异常与提前返回原文")):
            if self.needs[kind]:
                self.task("need:" + kind, objective)
        literals = re.findall(r"[`“\"]([^`”\"\n]{1,120})[`”\"]", self.question)
        routes = re.findall(r"/[A-Za-z][\w/{}/.:-]*", self.question)
        remaining = self.question
        for value in [*literals, *routes]:
            remaining = remaining.replace(value, " ")
        identifiers = re.findall(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*\b", remaining)
        identifiers = [x for x in identifiers if x.upper() not in {"GET", "POST", "PUT", "DELETE", "HTTP", "API", "UI", "DTO"}]
        self.anchors = list(dict.fromkeys([*literals, *routes, *identifiers]))[:4]
        self.routes = {route.rstrip("/.") or "/" for route in routes}
        requested = {(verb.upper(), route.rstrip("/.") or "/") for verb, route in re.findall(
            r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z][\w/{}/.:-]*)", self.question, re.I)}
        exact = []
        if self.contracts and not self.needs["local"]:
            for cid, entry in self.contracts.index.snapshot["entries"].items():
                candidate = {"contractId": cid, **self.contracts.index.entry_header(cid)}
                if not self._explicit(candidate):
                    continue
                normalized_route = candidate["route"].rstrip("/.") or "/"
                if any(route == normalized_route for _, route in requested) and (candidate["method"], normalized_route) not in requested:
                    continue
                try:
                    self.files.locate(entry["ref"]["path"])
                except (OSError, ValueError):
                    continue
                exact.append(candidate)
        self.candidates.update({c["contractId"]: c for c in exact})
        for candidate in exact:
            if sum((c["method"], c["route"]) == (candidate["method"], candidate["route"]) for c in exact) != 1:
                continue
            self.decisions.append({"action": "entry", "label": "精确匹配本轮范围内的接口或符号，不经过相似度排名", "candidate": candidate})
            self.bind(candidate["contractId"], "按明确接口或符号核对契约原文")
        paths = re.findall(r"(?:[\w.-]+/)*[\w.-]+\.(?:java|py|tsx?|jsx?|vue|ya?ml|json|md)\b", self.question)
        if self.material:
            self.material.seed(self, locate_from_docs=not (paths or self.routes or self.bindings))
        for path in paths[:2]:
            try:
                file, mount = self.files.locate(path)
                if file.is_file():
                    self.enqueue("Read", {"path": mount}, "已给出文件，直接读取原文", "entry")
                    continue
            except (OSError, ValueError):
                pass
            self.enqueue("Glob", {"pattern": "**/" + path.split("/")[-1]}, "按文件名定位入口", "entry")
        if not paths and not self.bindings:
            for anchor in self.anchors[:2]:
                self.enqueue("Grep", {"pattern": anchor, "literal": True, "limit": 20}, "按明确标识精确定位", "entry")
            if not self.anchors:
                self.enqueue("SearchCode", {"question": self.question}, "按业务描述混合定位入口", "entry")
        if self.contracts and not self.needs["local"] and not self.bindings:
            self.enqueue("FindCapability", {"question": self.question}, "定位接口契约候选，候选本身不是连接证据", "entry")
        if self.needs["semantics"]:
            self.task("semantics", "核对业务术语、约定或需求原文")
        if self.needs["consumer"] and not self.material:
            self.task("consumer", "取得本题对应前端的请求和消费代码")
        self.decisions.insert(0, {"action": "plan", "label": "按问题目标建立检索事项", "needs": self.needs,
                                  "availableTools": sorted(self.available)})

    def next_batch(self):
        result, rest = [], []
        for action in self.actions:
            if action["key"] in self.done:
                continue
            if action["name"] == "Read" and self._already_read(action["args"]):
                self.done.add(action["key"])
                continue
            if len(result) < 3:
                result.append(action)
                self.automatic_count += 1
                self.done.add(action["key"])
            else:
                rest.append(action)
        self.actions = rest
        return result

    def _already_read(self, args):
        start = args.get("offset", 1)
        end = start + args.get("limit", 160) - 1
        return next(self._unread_ranges(args["path"], start, end), None) is None

    def _unread_ranges(self, path, start, end):
        end = min(end, self.file_lines.get(path, end))
        for low, high in merge_line_ranges((low, high) for p, low, high in self.read_spans if p == path):
            if high < start:
                continue
            if low > end:
                break
            if start < low:
                yield start, low - 1
            start = high + 1
        if start <= end:
            yield start, end

    def _schedule_grep_reads(self, hits):
        windows = defaultdict(list)
        for hit in hits:
            try:
                _, path = self.files.locate(hit["path"])
            except (OSError, ValueError):
                continue
            line = hit["line"]
            if self._already_read({"path": path, "offset": line, "limit": 1}):
                continue
            windows[path].append((max(1, line - 8), line + 35))
        for path, spans in windows.items():
            for start, end in merge_line_ranges(spans):
                for low, high in self._unread_ranges(path, start, end):
                    for offset in range(low, high + 1, 120):
                        args = {"path": path, "offset": offset, "limit": min(120, high - offset + 1)}
                        self.enqueue("Read", args, "核对同文件搜索命中的原文", "entry")

    def _read_location(self, path, start, end=None, reason="核对搜索命中的原文", task="entry"):
        try:
            _, path = self.files.locate(path)
        except (OSError, ValueError):
            return
        low = max(1, start - 8)
        high = min(low + 119, max(start + 35, end or start))
        if any(p == path and a <= start <= b and (end is None or b >= end) for p, a, b in self.read_spans):
            return
        self.enqueue("Read", {"path": path, "offset": low, "limit": high - low + 1}, reason, task)

    def observe(self, messages):
        start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
        current = messages[start:]
        calls = {c["id"]: c for m in current if isinstance(m, AIMessage) for c in m.tool_calls}
        grep_hits = []
        for message in current:
            if not isinstance(message, ToolMessage) or message.tool_call_id in self.processed:
                continue
            self.processed.add(message.tool_call_id)
            args = calls.get(message.tool_call_id, {}).get("args", {})
            self.done.add(self.key(message.name, args))
            try:
                data = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("skipped"):
                continue
            if data.get("error"):
                if message.name == "SearchCode":
                    self.decisions.append({"action": "fallback", "label": "混合检索不可用，保留缺口并转用文件工具", "error": data["error"]})
                    self.tasks["entry"]["note"] = "混合检索失败，尚需通过文件工具定位原文"
                continue
            if message.name == "FindCapability":
                self.candidates.update({c["contractId"]: c for c in data.get("candidates", [])})
                exact = [c for c in self.candidates.values() if self._explicit(c)]
                if len(exact) == 1:
                    self.bind(exact[0]["contractId"], "问题明确指定接口或符号，程序核对其契约")
                else:
                    self._bind_read_candidate()
            elif message.name == "Glob":
                if args.get("kind", "files") != "files":
                    continue
                if self.material and self.material.observe_glob(self, args, data):
                    continue
                paths = data.get("paths", [])
                if len(paths) <= 3 and not data.get("truncated"):
                    for path in paths:
                        self.enqueue("Read", {"path": path}, "读取文件名定位的候选", "entry")
            elif message.name == "Grep":
                if self.material and self.material.observe_grep(self, args, data):
                    continue
                grep_hits.extend(data.get("matches", [])[:3])
                if not data.get("matches") and not self.read_spans:
                    self.enqueue("SearchCode", {"question": self.question}, "精确搜索无命中，改用混合定位", "entry")
            elif message.name == "SearchCode":
                for hit in data.get("locations", [])[:3]:
                    if hit.get("nextLine"):
                        self._read_location(hit["path"], hit["nextLine"], hit["nextLine"] + 70, "续读被截断的候选原文")
                    else:
                        self.read_spans.append((hit["path"], hit["lineStart"], hit["lineEnd"]))
                        ref = {"path": hit["path"], "start": hit["lineStart"], "end": hit["lineEnd"]}
                        if not self.material:
                            self.task("entry", "定位并读取本题代码入口", status="source_read", evidence=[ref])
                        self._raw_evidence(data.get("content", ""), ref)
                self._bind_read_candidate()
                for path in data.get("staleLocations", [])[:2]:
                    source = next((s for s in self.files.roots if path.startswith(s[0] + "/")), None)
                    if source:
                        self.enqueue("Grep", {"pattern": self.anchors[0] if self.anchors else self.question,
                                              "path": path, "literal": True, "limit": 10}, "索引过期，重新定位当前源码", "entry")
            elif message.name == "Read" and data.get("path"):
                if "totalLines" in data:
                    self.file_lines[data["path"]] = data["totalLines"]
                self.read_spans.append((data["path"], data["lineStart"], data["lineEnd"]))
                ref = {"path": data["path"], "start": data["lineStart"], "end": data["lineEnd"]}
                if self.material:
                    self.material.observe_read(self, data)
                else:
                    self.task("entry", "定位并读取本题代码入口", status="source_read", evidence=[ref])
                self._raw_evidence(data.get("content", ""), ref)
                for key in ("consumer", "semantics"):
                    task = self.tasks.get(key)
                    if not task:
                        continue
                    matches = (key == "semantics" and any(
                        data["path"].startswith(mount + "/") for mount in BUSINESS_MOUNTS | {"requirements"}
                    )) or (
                        key == "consumer" and any(data["path"].startswith(a["mount"] + "/") for a in self._frontends()))
                    if matches:
                        task.update(status="source_read", evidence=task["evidence"] + [ref],
                                    note="原文已交付，是否支持业务解释或页面消费仍需判断")
                self._bind_read_candidate()
            elif message.name in {"TraceContract", "TraceResponseFields", "TraceBusinessRules"}:
                cid = data.get("contractId")
                if self.contracts and cid in self.contracts.selected:
                    self.bind(cid, "沿本轮选中的契约补齐相关证据")
                    self._accept_bundle(data, message.name)
        self._schedule_grep_reads(grep_hits)
        if self.contracts:
            for cid in self.contracts.selected:
                self.bind(cid, "沿本轮选中的契约补齐相关证据")

    def _explicit(self, candidate):
        route = candidate["route"]
        symbol = candidate["symbol"]
        return (route.rstrip("/.") or "/") in self.routes or symbol in self.anchors or any(
            anchor == symbol or ("." in anchor and symbol.endswith(anchor)) for anchor in self.anchors)

    def _raw_evidence(self, content, ref):
        if not ref["path"].startswith("repos/"):
            return
        patterns = {"fields": r"\breturn\b|=>|\bresponse\b", "business": r"\b(?:if|switch|catch|except|return)\b",
                    "relations": r"\b\w+\.\w+\s*\("}
        for kind, pattern in patterns.items():
            task = self.tasks.get("need:" + kind)
            if task and task["status"] != "evidence_ready" and re.search(pattern, content):
                task.update(status="partial" if kind == "relations" else "source_read",
                            evidence=(task["evidence"] + [ref])[-8:],
                            note="已取得相关源码线索；对象绑定、连接或完整执行条件仍需核对")

    def _bind_read_candidate(self):
        possible = [c for c in self.candidates.values() if any(path == c["location"]["path"]
                    and start <= c["location"]["start"] <= end for path, start, end in self.read_spans)]
        if len(possible) == 1:
            self.bind(possible[0]["contractId"], "已读原文定位到契约候选，继续核对连接和返回数据")

    def bind(self, cid, reason):
        if cid in self.bindings:
            return
        self.bindings.add(cid)
        # A located contract already supplies a route into code. Do not run a
        # queued broad semantic fallback caused by a split annotation URL.
        self.actions = [a for a in self.actions if not (a["name"] == "SearchCode" and a["args"] == {"question": self.question})]
        fields = self.needs["fields"] or self.needs["business"] or self.needs["consumer"] or self.needs["changes"]
        name = "TraceResponseFields" if fields else "TraceContract"
        self.task("contract:" + cid, "取得接口连接" + ("和返回字段依据" if fields else "依据"), contractId=cid)
        self.enqueue(name, {"contract_id": cid}, reason, "contract:" + cid)
        if self.needs["business"]:
            self.task("business:" + cid, "核对相关字段的写入条件、异常与提前返回", contractId=cid)
            self.enqueue("TraceBusinessRules", {"contract_id": cid}, "状态或改动问题需核对写入分支", "business:" + cid)
        if self.needs["consumer"] or (self.needs["changes"] and not self.needs["local"]):
            self.task("consumer", "取得本题对应前端的请求和消费代码")
            frontends = self._frontends(cid)
            if not frontends:
                self.tasks["consumer"].update(status="unavailable", note="本轮未登记对应系统的前端；页面是否使用仍未确认")
            else:
                route = self.contracts.index.snapshot["entries"][cid]["route"]["path"]
                prefix = route.split("{")[0].rstrip("/") or route
                for app in frontends[:2]:
                    self.enqueue("Grep", {"pattern": prefix, "path": app["mount"], "literal": True, "limit": 12},
                                 "按接口路径定位对应系统前端的请求代码", "consumer")

    def _frontends(self, cid=None):
        systems = set()
        if self.contracts:
            for selected in ([cid] if cid else self.bindings):
                app_id = self.contracts.index.snapshot["entries"][selected]["applicationId"]
                systems.update(a.get("systemId") for a in self.contracts.index.snapshot["applications"] if a["id"] == app_id)
        return [a for a in self.navigation["applications"] if a["type"] == "FRONTEND"
                and (not systems or a.get("systemId") in systems)]

    def _accept_bundle(self, bundle, name):
        cid = bundle["contractId"]
        if bundle["evidence"] and not self.material:
            self.task("entry", "定位并读取本题代码入口", status="evidence_ready", evidence=[{k: s[k] for k in ("path", "start", "end")} for s in bundle["evidence"]])
        key = ("business:" if name == "TraceBusinessRules" else "contract:") + cid
        task = self.tasks.get(key)
        if task:
            task.update(status="evidence_ready", evidence=[{k: s[k] for k in ("path", "start", "end")} for s in bundle["evidence"]],
                        indexVersion=bundle["indexVersion"], gaps=bundle.get("gaps", []),
                        note="已交付解析器支持的结构与原文；不表示全量字段、完整控制流或语义已证明")
            if not bundle["evidence"] or bundle.get("gaps"):
                task["status"] = "partial"
        self.read_spans.extend((s["path"], s["start"], s["end"]) for s in bundle["evidence"])
        for kind, present in (("relations", bool(bundle.get("connections"))),
                              ("fields", name == "TraceResponseFields" and bool(bundle.get("fields"))),
                              ("business", name == "TraceBusinessRules" and bool(bundle.get("businessRules")))):
            general = self.tasks.get("need:" + kind)
            if general and present:
                general.update(status="partial" if bundle.get("gaps") else "evidence_ready",
                               evidence=[{k: s[k] for k in ("path", "start", "end")} for s in bundle["evidence"]],
                               note="结构和原文已交付；仅限解析器支持范围，不是完整执行或业务语义证明")
        for gap in bundle.get("gaps", []):
            read = gap.get("nextRead")
            if read:
                self.enqueue("Read", read, "补查结构索引标出的具体源码缺口", key)

    def drain_decisions(self):
        values, self.decisions = self.decisions, []
        return values

    def snapshot(self):
        value = {"needs": self.needs, "tasks": list(self.tasks.values()), "candidates": list(self.candidates.values()),
                "pending": [{k: v for k, v in a.items() if k != "key"} for a in self.actions if a["key"] not in self.done],
                "attemptedActions": self.automatic_count, "actionBudget": self.max_actions,
                "notices": self.notices, "semanticProof": False}
        if self.material:
            value["material"] = self.material.snapshot()
        return value

    def reconcile_material(self, investigation):
        if self.material:
            self.material.reconcile(self, investigation)

    def missing_material_evidence(self):
        return self.material is not None and any(
            task["status"] in {"open", "partial", "unavailable"} or task.get("contextMissing")
            for key, task in self.tasks.items() if key == "entry" or key in MATERIAL_ROLES
        )

    def render(self):
        return "程序检索事项（source_read=原文已读，evidence_ready=结构证据已交付，均不证明业务语义；partial/open/unavailable须保留缺口）：\n" + json.dumps(self.snapshot(), ensure_ascii=False)

    def revalidate(self, catalog):
        lines = {}
        for source in catalog.sources:
            if source.kind == "navigation":
                continue
            for path in (source.path, "repos/" + source.path):
                lines.setdefault(path, set()).update(source.lines)
        for task in self.tasks.values():
            task["contextMissing"] = any(not set(range(ref["start"], ref["end"] + 1)) <= lines.get(ref["path"], set())
                                         for ref in task["evidence"])

    def qualify(self, answer):
        missing = [t["objective"] + ("（答题上下文未保留全部原文）" if t.get("contextMissing") else "")
                   + ("：" + t["note"] if t.get("note") else "") for t in self.tasks.values()
                   if t["status"] in {"open", "partial", "unavailable"} or t.get("contextMissing")]
        return answer + ("\n\n检索仍有缺口：" + "；".join(missing) + "。" if missing else "")

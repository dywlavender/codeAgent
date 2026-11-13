"""Typed contract navigation and a turn-local queue owned by the runtime."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from business_code_agent.codeintel.contracts.query import ContractIndex
from .investigation import PendingRead
from .business_answer import question_needs


class ContractState:
    def __init__(self, root, project_id, files, question="", navigation=None):
        self.index = ContractIndex(root, project_id, files)
        self.files = files
        self.selected = {}
        self.processed = set()
        self.attempted_reads = set()
        self.tasks = {}
        needs = question_needs(question)
        self.needs_business = needs["business"]
        self.business_traced = set()
        self.attempted_business = set()
        self.navigation = navigation or {"applications": []}
        self.needs_consumer = needs["consumer"]
        self.needs_change = needs["changes"]
        self.stalled_consumer_scope = None
        self.stalled_consumer_searches = 0
        self.finalization_reason = None

    def unrelated_frontend(self, path):
        systems = {a.get("systemId") for cid in self.selected for a in self.index.snapshot["applications"]
                   if a["id"] == self.index.snapshot["entries"][cid]["applicationId"] and a.get("systemId")}
        frontends = [a for a in self.navigation["applications"] if a.get("type") == "FRONTEND"]
        if not self.needs_consumer or not systems or any(a.get("systemId") in systems for a in frontends):
            return False
        return any(a.get("systemId") not in systems and (path == a["mount"] or path.startswith(a["mount"] + "/")) for a in frontends)

    def tools(self):
        @tool
        def FindCapability(question: str, limit: int = 5) -> str:
            """按业务描述、HTTP 路径或符号定位当前工程的契约候选。只作导航，选中的编号还需 TraceContract 或 TraceResponseFields 核实。"""
            return json.dumps({"candidates": self.index.find(question, limit)}, ensure_ascii=False)

        @tool
        def TraceContract(contract_id: str) -> str:
            """核对 FindCapability 中的契约编号，交付配置绑定的跨服务连接、原文和缺口；不以同名接口推断连接。需核对返回数据时再用 TraceResponseFields。"""
            try:
                bundle = self.index.bundle(contract_id)
                bundle["fields"], bundle["mappings"] = [], []
                bundle["view"] = "connections"
                return json.dumps(bundle, ensure_ascii=False)
            except (OSError, ValueError) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @tool
        def TraceResponseFields(contract_id: str) -> str:
            """核对契约的返回字段、对象透传、展示映射以及依赖连接，交付源文件行号与原文。未列出的字段只能视为未解析；不证明页面已消费。可直接使用此工具同时完成连接和字段核对。"""
            try:
                bundle = self.index.bundle(contract_id)
                bundle["view"] = "response"
                return json.dumps(bundle, ensure_ascii=False)
            except (OSError, ValueError) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @tool
        def TraceBusinessRules(contract_id: str) -> str:
            """查询已显式索引的返回成员写入点：实际 setter、局部分支、try/catch、写入表达式与原文。不是完整控制流或某次执行证明；业务解释还应核对提前返回、调用时机和资料语义。"""
            return json.dumps(self.trace_business(contract_id), ensure_ascii=False)

        return [FindCapability, TraceContract, TraceResponseFields, TraceBusinessRules]

    def trace_business(self, contract_id):
        try:
            bundle = self.index.bundle(contract_id, view="business")
            bundle["view"] = "business"
            return bundle
        except (ValueError, OSError) as exc:
            return {"error": str(exc)}

    def observe(self, messages):
        start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
        calls = {c["id"]: c for m in messages[start:] if isinstance(m, AIMessage) for c in m.tool_calls}
        for message in messages[start:]:
            if not isinstance(message, ToolMessage) or message.tool_call_id in self.processed:
                continue
            self.processed.add(message.tool_call_id)
            try:
                data = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("skipped"):
                continue
            if message.name == "Grep":
                path = calls.get(message.tool_call_id, {}).get("args", {}).get("path", "")
                try:
                    path = self.files.locate(path)[1]
                except (ValueError, OSError):
                    path = ""
                if data.get("matches") == [] and self.unrelated_frontend(path):
                    self.stalled_consumer_searches = self.stalled_consumer_searches + 1 if self.stalled_consumer_scope == path else 1
                    self.stalled_consumer_scope = path
                else:
                    self.stalled_consumer_scope, self.stalled_consumer_searches = None, 0
            elif message.name in {"Read", "TraceContract", "TraceResponseFields", "TraceBusinessRules"} and not data.get("error"):
                self.stalled_consumer_scope, self.stalled_consumer_searches = None, 0
            if message.name == "Read" and data.get("path"):
                for key, task in self.tasks.items():
                    pending = task["read"]
                    if data["path"] == pending["path"] and data.get("lineStart", 0) <= pending["offset"] <= data.get("lineEnd", 0):
                        task["status"] = "source_read"
                continue
            if message.name not in {"TraceContract", "TraceResponseFields", "TraceBusinessRules"} or data.get("error"):
                continue
            cid = data.get("contractId")
            if data.get("indexVersion") != self.index.snapshot["version"] or cid not in self.index.snapshot["entries"]:
                continue
            # Re-read from our own snapshot, not fields asserted in a tool message.
            bundle = self.index.bundle(cid)
            bundle["responseTraced"] = data.get("view") == "response" or self.selected.get(cid, {}).get("responseTraced", False)
            self.selected[cid] = bundle
            if data.get("view") == "business":
                self.business_traced.add(cid)
            for gap in data.get("gaps", []):
                pending = gap.get("nextRead")
                if not pending:
                    continue
                try:
                    pending = PendingRead.model_validate(pending).model_dump()
                    pending["path"] = self.files.locate(pending["path"])[1]
                except (ValueError, OSError):
                    continue
                key = (pending["path"], pending["offset"], pending["limit"])
                self.tasks.setdefault(key, {"reason": gap["reason"], "read": pending, "status": "open"})

    def next_business(self):
        if self.needs_business:
            for cid, bundle in self.selected.items():
                if bundle["responseTraced"] and cid not in self.business_traced and cid not in self.attempted_business:
                    self.attempted_business.add(cid)
                    return cid
        return None

    def next_read(self):
        for key, task in self.tasks.items():
            if task["status"] == "open" and key not in self.attempted_reads:
                self.attempted_reads.add(key)
                task["status"] = "attempted"
                return PendingRead.model_validate(task["read"])
        return None

    def bundles(self):
        # Validate freshness and scope again immediately before the answer call.
        result = []
        for cid, selected in self.selected.items():
            if not selected["responseTraced"]:
                continue
            bundle = self.index.bundle(cid)
            if cid in self.business_traced:
                business = self.index.bundle(cid, view="business")
                bundle["businessRules"] = business["businessRules"]
                bundle["evidence"].extend(business["evidence"])
                bundle["evidenceLines"] += business["evidenceLines"]
                bundle["gaps"].extend(g for g in business["gaps"] if g not in bundle["gaps"])
            result.append(bundle)
        return result

    def snapshot(self):
        obligations = []
        for cid, bundle in self.selected.items():
            record = {"contractId": cid, "response": "read" if bundle["responseTraced"] else "open"}
            if self.needs_business:
                record["businessConditions"] = "source_sites_read" if cid in self.business_traced else "open"
            if self.needs_consumer:
                entry = self.index.snapshot["entries"][cid]
                app = next((a for a in self.index.snapshot["applications"] if a["id"] == entry["applicationId"]), {})
                system = app.get("systemId")
                candidates = [a["mount"] for a in self.navigation["applications"]
                              if system and a.get("systemId") == system and a.get("type") == "FRONTEND"]
                record["consumer"] = {"status": "unconfirmed" if candidates or not system else "not_registered_in_scope", "candidates": candidates}
            if self.needs_change:
                record["changePreconditions"] = "需核对判别输入与实际消费端；无消费证据时不得给出单点收益保证或排序"
            obligations.append(record)
        return {"indexVersion": self.index.snapshot["version"], "needsBusinessConditions": self.needs_business, "selected": [
            {"contractId": cid, "responseTraced": b["responseTraced"], "fields": len(b["fields"]), "gaps": b["gaps"]}
            for cid, b in self.selected.items()], "businessTraced": sorted(self.business_traced),
            "obligations": obligations, "followups": list(self.tasks.values()),
            "consumerSearchStall": {"scope": self.stalled_consumer_scope, "count": self.stalled_consumer_searches},
            "finalizationReason": self.finalization_reason, "semanticProof": False}

    def render(self):
        return ("契约调查状态（程序维护；source_read 只表示补读，不会把未解析边改成已证明）：\n"
                + json.dumps(self.snapshot(), ensure_ascii=False))

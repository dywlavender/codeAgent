from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from business_code_agent.codeintel.contracts.index import prepare_index, read_index
from business_code_agent.codeintel.contracts.query import ContractIndex
from business_code_agent.query.langchain_agent.contracts import ContractState
from business_code_agent.query.langchain_agent.contract_answer import complete_response
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.context import answer_evidence
from business_code_agent.query.langchain_agent.contract_answer import request_messages
from business_code_agent.query.langchain_agent.verification import Ref
from business_code_agent.query.navigation import build_navigation, NAVIGATION_FILE


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


DEMO = Path(__file__).resolve().parents[1] / "examples/contract-flow"


class ContractIndexTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        for repository in ("channel", "middle"):
            shutil.copytree(DEMO / repository, self.workspace / "repos" / repository)
        (self.workspace / "CLAUDE.md").write_text("只读，核对源码原文和行号。")
        self.config = {"applications": [{"id": r, "name": r, "repositoryId": r, "sourceRoot": ".", "type": "BACKEND"} for r in ("channel", "middle")]}
        self.sources = [{"id": r, "kind": "source", "root": str(self.workspace / "repos" / r)} for r in ("channel", "middle")]
        self.index_root = self.root / "index"
        self.prepared = self.prepare()
        self.files = SourceFiles(self.workspace)
        self.cid = next(k for k, e in self.prepared["entries"].items() if e["symbol"] == "demo.channel.Channel.detail")

    def prepare(self, **kwargs):
        return prepare_index(self.index_root, self.sources, self.config, "A", **kwargs)

    def index(self, files=None):
        return ContractIndex(self.index_root, "A", files or self.files)

    def test_bound_http_object_passthrough_arrays_record_and_projection(self):
        bundle = self.index().bundle(self.cid)
        fields = {f["path"] for f in bundle["fields"]}
        self.assertTrue({"displayStatus", "detail", "detail.status", "detail.payments[].status", "detail.booking.status", "detail.booking.retryCount"} <= fields)
        self.assertFalse(bundle["gaps"])
        self.assertEqual("unconfirmed", bundle["consumer"])
        self.assertTrue(any(e["kind"] == "HTTP_RESPONSE" for e in bundle["connections"]))
        mapping = bundle["mappings"][0]
        self.assertEqual(["detail.status"], mapping["inputFields"])
        self.assertEqual("PROCESSING", mapping["rules"][-1]["output"])
        self.assertIn("http://localhost:8092", "\n".join(s["content"] for s in bundle["evidence"]))
        all_ids = {e["id"] for e in bundle["evidence"]}
        for item in [*bundle["fields"], *bundle["mappings"], *bundle["connections"]]:
            self.assertTrue(set(item["evidence"]) <= all_ids)
        candidate = self.index().find("渠道订单详情展示状态")
        self.assertEqual(self.cid, candidate[0]["contractId"])

    def test_same_path_without_service_binding_is_not_an_http_edge(self):
        p = self.workspace / "repos/channel/application.yml"
        p.write_text(p.read_text().replace("8092", "9999"))
        self.prepare()
        bundle = self.index().bundle(self.cid)
        self.assertEqual({"displayStatus", "detail"}, {f["path"] for f in bundle["fields"]})
        self.assertFalse(any(e["kind"] == "HTTP_RESPONSE" for e in bundle["connections"]))
        self.assertTrue(any(g["reason"] == "http_target_not_indexed_or_matched" for g in bundle["gaps"]))

    def test_ambiguous_routes_are_left_unresolved(self):
        p = self.workspace / "repos/middle/Other.java"
        p.write_text('package demo.middle; import java.util.Map; import org.springframework.web.bind.annotation.*; @RestController class Other { @GetMapping("/internal/orders/{no}") Map detail(String no) { return Map.of("wrong", true); } }')
        self.prepare()
        bundle = self.index().bundle(self.cid)
        self.assertTrue(any(g["reason"] == "http_target_ambiguous" for g in bundle["gaps"]))
        self.assertNotIn("detail.wrong", {f["path"] for f in bundle["fields"]})

    def test_partial_repository_scope_does_not_deliver_remote_originals(self):
        bundle = self.index(SourceFiles(self.workspace, {"channel"})).bundle(self.cid)
        self.assertEqual({"displayStatus", "detail"}, {f["path"] for f in bundle["fields"]})
        self.assertTrue(all(e["path"].startswith("repos/channel/") for e in bundle["evidence"]))
        self.assertFalse(bundle["mappings"])

    def test_changed_source_drops_dependent_facts_and_query_never_parses(self):
        p = self.workspace / "repos/middle/Middle.java"
        p.write_text(p.read_text().replace('"RETRY_NEEDED"', '"SUCCESS"'))
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("查询不应解析")):
            bundle = self.index().bundle(self.cid)
        self.assertNotIn("detail.booking.status", {f["path"] for f in bundle["fields"]})
        self.assertFalse(bundle["mappings"])
        self.assertTrue(any(g["reason"] == "source_out_of_scope_or_changed" for g in bundle["gaps"]))

    def test_context_budget_keeps_whole_proofs(self):
        bundle = self.index().bundle(self.cid, char_limit=2400, line_limit=15)
        self.assertLessEqual(len(json.dumps(bundle, ensure_ascii=False)), 2400)
        self.assertLessEqual(bundle["evidenceLines"], 15)
        ids = {e["id"] for e in bundle["evidence"]}
        for field in bundle["fields"]:
            self.assertTrue(set(field["evidence"]) <= ids)
        self.assertIn({"reason": "evidence_context_budget"}, bundle["gaps"])

    def test_diff_incremental_reuse_modify_and_delete(self):
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("不应重复解析")):
            same = self.prepare()
        self.assertEqual(self.prepared["version"], same["version"])
        self.assertEqual(0, same["update"]["parsedJavaFiles"])
        p = self.workspace / "repos/middle/Middle.java"
        p.write_text(p.read_text().replace("retryCount", "attemptCount"))
        changed = self.prepare()
        self.assertEqual(1, changed["update"]["parsedJavaFiles"])
        self.assertEqual(self.prepared["update"]["parsedJavaFiles"] - 1, changed["update"]["reusedJavaFiles"])
        self.assertTrue(changed["update"]["changes"][0]["ranges"])
        self.assertIn("detail.booking.attemptCount", {f["path"] for f in self.index().bundle(self.cid)["fields"]})
        p.unlink()
        deleted = self.prepare()
        self.assertTrue(any(c["kind"] == "deleted" for c in deleted["update"]["changes"]))
        self.assertNotIn("detail.booking.attemptCount", {f["path"] for f in self.index().bundle(self.cid)["fields"]})

    def test_project_identity_rejects_same_entry_ids_from_another_project(self):
        with self.assertRaisesRegex(ValueError, "当前工程"):
            read_index(self.index_root, "B")
        other = prepare_index(self.root / "B", self.sources, self.config, "B")
        self.assertIn(self.cid, other["entries"])
        with self.assertRaisesRegex(ValueError, "当前工程"):
            ContractIndex(self.root / "B", "A", self.files)

    def test_mutated_alias_is_not_treated_as_original_response(self):
        p = self.workspace / "repos/channel/Channel.java"
        p.write_text(p.read_text().replace('String status =', 'Map alias = data;\n        alias.remove("booking");\n        String status ='))
        self.prepare()
        bundle = self.index().bundle(self.cid)
        self.assertNotIn("detail.booking.status", {f["path"] for f in bundle["fields"]})
        self.assertTrue(any(g["reason"] == "local_object_modified" for g in bundle["gaps"]))

    def test_program_gap_read_is_created_without_an_investigation_command(self):
        p = self.workspace / "repos/middle/Middle.java"
        p.write_text(p.read_text() + "\n// changed\n")
        state = ContractState(self.index_root, "A", self.files)
        output = state.tools()[2].invoke({"contract_id": self.cid})
        messages = [HumanMessage(content="调查接口"), ToolMessage(content=output, name="TraceResponseFields", tool_call_id="t1")]
        state.observe(messages)
        pending = state.next_read()
        self.assertIsNotNone(pending)
        self.assertEqual("repos/middle/Middle.java", pending.path)
        body = self.files.read(**pending.model_dump())
        state.observe(messages + [ToolMessage(content=json.dumps(body), name="Read", tool_call_id="r1")])
        self.assertEqual("source_read", state.snapshot()["followups"][0]["status"])
        self.assertTrue(state.bundles()[0]["gaps"])

    def test_invalid_model_prose_cannot_replace_canonical_field_facts(self):
        bundle = self.index().bundle(self.cid)
        answer, report = complete_response(AIMessage(content="接口只返回 displayStatus，建议新增 booking.status"), [bundle], EvidenceCatalog("A", ()))
        self.assertIn("`detail.booking.status`", answer)
        self.assertNotIn("建议新增", answer)
        self.assertTrue(report["issues"])
        self.assertFalse(report["semanticProof"])

    def test_delivered_business_branch_is_kept_under_total_budget(self):
        original = "[源码] repos/middle/Business.java:1-55\n" + "\n".join(f"{n}: business line {n}" for n in range(1, 56))
        catalog = EvidenceCatalog.from_text("A", original)
        message = AIMessage(content=json.dumps({"contracts": [], "business_evidence": [{"source": "S1", "start": 1, "end": 55}]}))
        answer, audit = complete_response(message, [], catalog)
        self.assertIn("business line 55", answer)
        self.assertEqual(55, audit["businessEvidenceLines"])
        self.assertEqual(1, audit["businessEvidenceCount"])
        self.assertFalse(audit["issues"])

    def test_routes_with_literal_arrays_are_indexed_but_constants_are_not_guessed(self):
        p = self.workspace / "repos/channel/Channel.java"
        p.write_text(p.read_text().replace('@GetMapping("/{id}")', '@GetMapping({"/{id}", "/detail/{id}"})'))
        result = self.prepare()
        self.assertEqual(3, len(result["entries"]))
        p.write_text(p.read_text().replace('@GetMapping({"/{id}", "/detail/{id}"})', '@GetMapping(ROUTE)'))
        result = self.prepare()
        self.assertEqual(1, len(result["entries"]))
        self.assertEqual("dynamic_route_not_supported", result["issues"][0]["reason"])

    def test_agent_tools_handoff_and_final_constraints_use_one_shared_loop(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "trace-fields", "name": "TraceResponseFields", "args": {"contract_id": self.cid}}]),
            AIMessage(content="接口只返回 displayStatus。"),
            AIMessage(content=json.dumps({"contracts": [self.cid], "business_evidence": []})),
        ])
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("问答不生成索引")):
            result = LangChainRuntime(model=model, project_id="A", contract_root=self.index_root,
                                      answer_mode="contracts", max_calls=4).ask("渠道订单详情接口能区分业务状态吗", workspace=self.workspace)
        self.assertEqual(3, result.usage["modelCalls"])
        self.assertTrue(result.usage["separateFinalization"])
        self.assertNotIn("接口只返回", result.answer)
        self.assertIn("`detail.payments[].status`", result.answer)
        self.assertIn("`detail.booking.status`", result.answer)
        self.assertFalse(result.usage["verification"]["issues"])
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        self.assertIn("contracts", trace["calls"][-1]["investigation"])

    def test_missing_edge_triggers_runtime_read_without_model_plan(self):
        p = self.workspace / "repos/middle/Middle.java"
        p.write_text(p.read_text() + "\n// source changed\n")
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "trace-fields", "name": "TraceResponseFields", "args": {"contract_id": self.cid}}]),
            AIMessage(content="调查完成"),
            AIMessage(content=json.dumps({"contracts": [self.cid], "business_evidence": []})),
        ])
        result = LangChainRuntime(model=model, project_id="A", contract_root=self.index_root,
                                  answer_mode="contracts", max_calls=4).ask("接口返回什么", workspace=self.workspace)
        reads = [e["payload"] for e in result.events if e["eventType"] == "tool" and e["payload"].get("name") == "Read" and e["payload"]["status"] == "completed"]
        self.assertTrue(reads)
        self.assertEqual("workflow", reads[0]["origin"])
        self.assertNotIn("`detail.booking.status`", result.answer)
        self.assertTrue(result.usage["contracts"]["followups"])

    def business_evidence(self):
        state = ContractState(self.index_root, "A", self.files)
        response = state.tools()[2].invoke({"contract_id": self.cid})
        rules = state.tools()[3].invoke({"contract_id": self.cid})
        messages = [HumanMessage(content="状态区分"),
                    ToolMessage(content=response, name="TraceResponseFields", tool_call_id="r"),
                    ToolMessage(content=rules, name="TraceBusinessRules", tool_call_id="b")]
        state.observe(messages)
        original, _ = answer_evidence(messages, float("inf"))
        return state.bundles()[0], EvidenceCatalog.from_text("A", original).merged()

    def test_business_rules_bind_members_to_response_and_preserve_branches(self):
        bundle, catalog = self.business_evidence()
        rules = bundle["businessRules"]
        waiting = next(r for r in rules if r["assigned"].get("value") == "TO_BOOK")
        failed = next(r for r in rules if r["assigned"].get("value") == "RETRY_NEEDED")
        self.assertEqual(["detail.booking.status"], waiting["fields"])
        self.assertEqual("body", waiting["phase"])
        self.assertTrue(waiting["conditions"][0]["truth"])
        self.assertIn('"OK".equals(result)', waiting["conditions"][0]["expression"])
        self.assertEqual("catch", failed["phase"])
        self.assertFalse(failed["executionProven"])
        self.assertFalse(failed["conditionsComplete"])
        payload = {"contracts": [self.cid], "cases": [
            {"contract": self.cid, "rules": [waiting["id"]], "meaning": "已收到成功结果，准备记账；不能把这个准备状态当作已发生异常。"},
            {"contract": self.cid, "rules": [failed["id"]], "meaning": "记账调用异常后进入重试状态。"}],
            "changes": [{"contract": self.cid, "action": "adjust_projection", "fields": ["displayStatus"]}]}
        answer, audit = complete_response(AIMessage(content=json.dumps(payload)), [bundle], catalog, question="页面区分状态，只改一处哪里收益最大")
        self.assertEqual(2, len(audit["businessCases"]))
        self.assertFalse(audit["issues"])
        self.assertIn("不能保证区分这些字段的不同状态", answer)
        self.assertIn("消费端是否接受新值仍未核对", answer)

    def test_same_named_member_on_another_type_does_not_become_a_business_rule(self):
        p = self.workspace / "repos/middle/Other.java"
        p.write_text('package demo.middle; class Other { String bookingStatus; void setBookingStatus(String v) { this.bookingStatus=v; } void unrelated() { setBookingStatus("OTHER_ENTITY"); } }')
        self.prepare()
        rules = self.index().bundle(self.cid, view="business")["businessRules"]
        self.assertFalse(any(r["assigned"].get("value") == "OTHER_ENTITY" for r in rules))

    def test_unknown_business_reference_or_missing_original_is_not_accepted(self):
        bundle, catalog = self.business_evidence()
        actual = bundle["businessRules"][0]["id"]
        payload = {"contracts": [self.cid], "cases": [{"contract": self.cid, "rules": [actual], "meaning": "解释"}]}
        _, audit = complete_response(AIMessage(content=json.dumps(payload)), [bundle], EvidenceCatalog("A", ()))
        self.assertFalse(audit["businessCases"])
        self.assertIn("原文未完整交付", audit["issues"][0])
        payload["cases"][0]["rules"] = ["B9999"]
        _, audit = complete_response(AIMessage(content=json.dumps(payload)), [bundle], catalog)
        self.assertFalse(audit["businessCases"])

    def test_requested_existing_fields_are_reused_instead_of_added_again(self):
        bundle, catalog = self.business_evidence()
        payload = {"contracts": [self.cid], "changes": [{"contract": self.cid, "action": "add_fields", "fields": ["detail.booking.status"]}]}
        answer, audit = complete_response(AIMessage(content=json.dumps(payload)), [bundle], catalog)
        self.assertIn("无需再次新增这些字段", answer)
        self.assertEqual(1, len(audit["changeOptions"]))

    def test_business_followup_is_program_owned_and_not_required_for_field_only_query(self):
        response = ContractState(self.index_root, "A", self.files).tools()[2].invoke({"contract_id": self.cid})
        messages = [HumanMessage(content="问题"), ToolMessage(content=response, name="TraceResponseFields", tool_call_id="r")]
        business = ContractState(self.index_root, "A", self.files, "区分失败和等待状态")
        business.observe(messages)
        self.assertEqual(self.cid, business.next_business())
        self.assertIsNone(business.next_business())
        fields = ContractState(self.index_root, "A", self.files, "接口返回哪些字段")
        fields.observe(messages)
        self.assertIsNone(fields.next_business())

    def test_full_agent_automatically_reads_business_sites_without_spending_model_call(self):
        rules = self.index().bundle(self.cid, view="business")["businessRules"]
        rid = next(r["id"] for r in rules if r["phase"] == "catch")
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "fields", "name": "TraceResponseFields", "args": {"contract_id": self.cid}}]),
            AIMessage(content="调查结束"),
            AIMessage(content=json.dumps({"contracts": [self.cid], "cases": [{"contract": self.cid, "rules": [rid], "meaning": "异常后待重试。"}], "business_evidence": []})),
        ])
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("运行时不解析")):
            result = LangChainRuntime(model=model, project_id="A", contract_root=self.index_root, answer_mode="contracts", max_calls=4).ask("区分失败和等待状态", workspace=self.workspace)
        events = [e["payload"] for e in result.events if e["eventType"] == "tool" and e["payload"].get("name") == "TraceBusinessRules" and e["payload"]["status"] == "completed"]
        self.assertTrue(events)
        self.assertEqual("workflow", events[0]["origin"])
        self.assertEqual(3, result.usage["modelCalls"])
        self.assertEqual(1, len(result.usage["verification"]["businessCases"]))

    def test_final_context_keeps_each_original_line_once_and_missing_lines_unavailable(self):
        original = "[源码] repos/middle/F.java:1-3\n1: alpha\n2: beta\n3: gamma\n\n[源码] repos/middle/F.java:3-5\n3: gamma\n4: delta\n5: epsilon\n\n[源码] repos/middle/F.java:9-9\n9: later"
        catalog = EvidenceCatalog.from_text("A", original).merged()
        self.assertEqual(1, len(catalog.sources))
        self.assertEqual(1, catalog.render().count("3: gamma"))
        self.assertIsNone(catalog.resolve(Ref(source="S1", start=5, end=9)))
        bundle = self.index().bundle(self.cid)
        messages = request_messages("字段问题", [bundle], catalog)
        self.assertEqual(1, messages[-1].content.count("3: gamma"))
        self.assertNotIn(bundle["evidence"][0]["content"], messages[-1].content)

    def test_conflicting_delivered_originals_are_not_silently_merged(self):
        catalog = EvidenceCatalog.from_text("A", "[源码] repos/a/F.java:1-1\n1: old\n\n[源码] repos/a/F.java:1-1\n1: new").merged()
        self.assertFalse(catalog.sources)
        self.assertEqual(("repos/a/F.java",), catalog.conflicts)

    def test_catch_cannot_turn_earlier_writes_into_guaranteed_final_state(self):
        bundle, catalog = self.business_evidence()
        normal = next(r["id"] for r in bundle["businessRules"] if r["assigned"].get("value") == "BOOKING_PENDING")
        failure = next(r["id"] for r in bundle["businessRules"] if r["phase"] == "catch")
        payload = {"contracts": [self.cid], "cases": [{"contract": self.cid, "rules": [normal, failure], "meaning": "凡是异常，最终主状态一定是 BOOKING_PENDING。"}]}
        answer, audit = complete_response(AIMessage(content=json.dumps(payload)), [bundle], catalog)
        self.assertNotIn("凡是异常", answer)
        self.assertIn("其它成员的最终值还取决于异常发生位置", answer)
        self.assertEqual("catch_members_only", audit["businessCases"][0]["displayMode"])
        self.assertTrue(audit["interpretationLimits"])

    def test_repeated_foreign_consumer_search_does_not_force_finalization(self):
        ui = self.workspace / "repos/other-ui"
        ui.mkdir()
        (ui / "App.tsx").write_text("export const unrelated = 'foreign app';")
        for app in self.config["applications"]:
            app["systemId"] = "target"
        self.config["applications"].append({"id": "other-ui", "name": "Other UI", "repositoryId": "other-ui", "sourceRoot": ".", "type": "FRONTEND", "systemId": "other"})
        self.prepare()
        navigation = build_navigation(self.config, {r: "repos/" + r for r in ("channel", "middle", "other-ui")}, "A", "Demo")
        (self.workspace / NAVIGATION_FILE).write_text(json.dumps(navigation))
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "fields", "name": "TraceResponseFields", "args": {"contract_id": self.cid}}]),
            AIMessage(content="", tool_calls=[{"id": "empty-1", "name": "Grep", "args": {"pattern": "displayStatus", "path": "repos/other-ui"}}]),
            AIMessage(content="", tool_calls=[{"id": "empty-2", "name": "Grep", "args": {"pattern": "booking", "path": "repos/other-ui"}}]),
            AIMessage(content="", tool_calls=[{"id": "continue-read", "name": "Read", "args": {"path": "repos/other-ui/App.tsx"}}]),
            AIMessage(content=json.dumps({"contracts": [self.cid], "changes": [{"contract": self.cid, "action": "inspect_consumer"}]})),
            AIMessage(content=json.dumps({"contracts": [self.cid], "changes": [{"contract": self.cid, "action": "inspect_consumer"}]})),
        ])
        result = LangChainRuntime(model=model, project_id="A", contract_root=self.index_root, answer_mode="contracts", max_calls=8).ask("页面如何区分业务状态，如何修改", workspace=self.workspace)
        self.assertEqual(6, result.usage["modelCalls"])
        self.assertIsNone(result.usage["contracts"]["finalizationReason"])
        self.assertIn("当前没有核对页面消费端", result.answer)
        self.assertIn("foreign app", SourceFiles(self.workspace).read("repos/other-ui/App.tsx")["content"])


if __name__ == "__main__":
    unittest.main()

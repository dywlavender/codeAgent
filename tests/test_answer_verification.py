"""Observed response data, display mapping and UI use are different claims."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog
from business_code_agent.query.langchain_agent.verification import verify_response


class AnswerVerificationTest(unittest.TestCase):
    def setUp(self):
        # Deliberately unrelated to the real regression's domain and field names.
        self.catalog = EvidenceCatalog.from_text("demo", '''[源码] repos/api/Receipt.java:10-12
10: var body = records.lookup(key);
11: var badge = badgeOf(body.get("phase"));
12: return Map.of("badge", badge, "payload", body);

[源码] repos/store/Records.java:30-31
30: Map lookup(String key) {
31: return Map.of("receipt", Map.of("phase", receipt.phase())); }

[源码] repos/api/Receipt.java:40-42
40: String badgeOf(String phase) {
41: // WAITING and RECONCILING are both displayed as BUSY
42: return "BUSY"; }
''')
        self.packet = {
            "resources": [
                {"id": "api", "label": "返回数据", "scope": "api_data"},
                {"id": "badge", "label": "展示字段", "scope": "display"},
                {"id": "page", "label": "页面使用", "scope": "ui"},
            ],
            "facts": [
                {"id": "path", "resource": "api", "kind": "returned_field",
                 "field": "payload.receipt.phase", "refs": [
                     {"source": "S1", "start": 10, "end": 12},
                     {"source": "S2", "start": 30, "end": 31},
                 ]},
                {"id": "collapse", "resource": "badge", "kind": "mapping",
                 "field": "badge", "inputs": ["WAITING", "RECONCILING"], "output": "BUSY",
                 "refs": [{"source": "S1", "start": 11, "end": 12},
                          {"source": "S3", "start": 40, "end": 42}]},
            ],
            "answers": [
                {"resource": "api", "kind": "data_fields", "required_fields": ["payload.receipt.phase"],
                 "facts": ["collapse"], "verdict": "unsupported", "summary": "错误：接口不支持"},
                {"resource": "badge", "kind": "display", "facts": ["collapse"]},
                {"resource": "page", "kind": "ui", "facts": ["path"], "verdict": "supported"},
            ],
        }

    def run_packet(self, packet=None, catalog=None):
        return verify_response(json.dumps(packet or self.packet), catalog or self.catalog)

    def test_derive_availability_even_when_model_proposes_opposite_or_omits_fact(self):
        self.packet["resources"][0]["label"] = "错误：接口不支持"
        text, audit = self.run_packet()
        self.assertEqual("supported", audit["decisions"][0]["verdict"])
        self.assertEqual("unknown", audit["decisions"][2]["verdict"])
        self.assertIn("返回数据中已包含", text)
        self.assertIn("单看该展示字段不能细分", text)
        self.assertNotIn("错误：接口不支持", text)
        self.assertTrue(any(i["code"] == "availability_conflict" for i in audit["issues"]))
        self.assertFalse(audit["semanticProof"])

    def test_missing_observation_and_projection_do_not_prove_absence(self):
        packet = copy.deepcopy(self.packet)
        packet["answers"][0]["required_fields"].append("payload.receipt.reason")
        text, audit = self.run_packet(packet)
        self.assertEqual("unknown", audit["decisions"][0]["verdict"])
        self.assertEqual(["payload.receipt.reason"], audit["decisions"][0]["unconfirmed"])
        self.assertIn("未确认不能当作接口没有", text)

    def test_references_must_resolve_to_delivered_original_lines(self):
        for change in ({"source": "S99"}, {"end": 13}, {"quote": "return impossible;"}):
            packet = copy.deepcopy(self.packet)
            packet["facts"][0]["refs"][0].update(change)
            with self.subTest(change=change):
                _, audit = self.run_packet(packet)
                self.assertEqual("unknown", audit["decisions"][0]["verdict"])
                self.assertTrue(any(i["code"] == "source_quote" for i in audit["issues"]))

    def test_split_reads_of_same_file_share_actual_lines_not_other_files(self):
        from business_code_agent.query.langchain_agent.verification import Ref

        same_file = Ref(source="S1", start=40, end=42)
        self.assertIn('return "BUSY"', self.catalog.excerpt(same_file))
        self.assertIsNone(self.catalog.resolve(Ref(source="S1", start=30, end=31)))

    def test_requirements_and_navigation_are_not_current_implementation(self):
        for header in ("[需求原文] requirements/plan.md", "[业务补充资料] knowledge/generated-code-map/index.md"):
            catalog = EvidenceCatalog.from_text("demo", header + ':1-1\n1: payload receipt phase')
            packet = copy.deepcopy(self.packet)
            packet["facts"] = [packet["facts"][0]]
            packet["facts"][0]["refs"] = [{"source": "S1", "start": 1, "end": 1}]
            with self.subTest(header=header):
                _, audit = self.run_packet(packet, catalog)
                self.assertEqual("unknown", audit["decisions"][0]["verdict"])
                self.assertEqual("implementation_source", audit["issues"][0]["code"])

    def test_added_field_already_returned_is_not_presented_as_missing(self):
        packet = copy.deepcopy(self.packet)
        packet["recommendations"] = [{
            "resource": "api", "change": "新增返回 phase", "benefit": "弥补接口缺失",
            "facts": ["path"], "adds_fields": ["payload.receipt.phase"],
        }]
        text, audit = self.run_packet(packet)
        self.assertIn("建议先利用已返回", text)
        self.assertNotIn("弥补接口缺失", text)
        self.assertTrue(any(i["code"] == "field_already_returned" for i in audit["issues"]))

    def test_malformed_packet_does_not_pass_unchecked_prose_to_user(self):
        text, audit = verify_response("接口完全不支持，因为 BUSY", self.catalog)
        self.assertEqual("invalid", audit["status"])
        self.assertNotIn("完全不支持", text)

    def test_ui_tag_and_backend_reference_do_not_prove_page_consumption(self):
        packet = copy.deepcopy(self.packet)
        packet["facts"].append({"id": "not_ui", "resource": "page", "kind": "ui",
                                "statement": "页面已经显示 phase", "refs": [{"source": "S1", "start": 12, "end": 12}]})
        packet["answers"][2]["facts"] = ["not_ui"]
        text, audit = self.run_packet(packet)
        self.assertEqual("unknown", audit["decisions"][2]["verdict"])
        self.assertNotIn("页面已经显示 phase", text)
        self.assertTrue(any(i["code"] == "ui_usage_unverified" for i in audit["issues"]))

    def test_recommendation_cannot_silently_drop_its_unverified_basis(self):
        packet = copy.deepcopy(self.packet)
        packet["recommendations"] = [{"resource": "api", "change": "依赖未核实事实的改动",
                                      "benefit": "收益", "facts": ["path", "missing_fact"]}]
        text, audit = self.run_packet(packet)
        self.assertNotIn("依赖未核实事实的改动", text)
        self.assertTrue(any(i["code"] == "recommendation_basis" for i in audit["issues"]))

    def test_early_unverified_draft_cannot_bypass_final_contract(self):
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, *args, **kwargs):
                return self

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "repos/api").mkdir(parents=True)
            (root / "repos/api/Receipt.java").write_text('return Map.of("phase", phase);')
            (root / "CLAUDE.md").write_text("依据源码回答")
            packet = {
                "resources": [{"id": "api", "label": "返回数据", "scope": "api_data"}],
                "facts": [{"id": "F", "resource": "api", "kind": "returned_field", "field": "phase",
                           "refs": [{"source": "S1", "start": 1, "end": 1}]}],
                "answers": [{"resource": "api", "kind": "data_fields", "required_fields": ["phase"],
                             "facts": ["F"], "verdict": "unsupported"}],
            }
            model = ToolModel(responses=[
                AIMessage(content="", tool_calls=[{"id": "r", "name": "Read", "args": {"path": "repos/api/Receipt.java"}}]),
                AIMessage(content="接口缺少 phase，无法支持"),
                AIMessage(content=json.dumps(packet)),
            ])
            result = LangChainRuntime(model=model, max_calls=3, answer_mode="facts").ask("返回 phase 吗", workspace=root)
            self.assertIn("返回数据中已包含", result.answer)
            self.assertNotIn("接口缺少", result.answer)
            self.assertTrue(result.usage["discardedUnverifiedDraft"])
            self.assertEqual(3, result.usage["modelCalls"])
            self.assertTrue(any(e.get("payload", {}).get("phase") == "checking" for e in result.events))
            saved = json.loads(next((root / ".langchain-sessions").glob("*.json")).read_text())
            self.assertNotIn("接口缺少", json.dumps(saved, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

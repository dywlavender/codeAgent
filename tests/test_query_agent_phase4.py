from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from business_code_agent.cli import load_demo
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.query_agent.api import make_server
from business_code_agent.query_agent.models import EvidenceRole, SourceType, StructuredFact
from business_code_agent.query_agent.conflicts import ConflictDetector
from business_code_agent.query_agent.evidence import EvidenceAssembler, EvidenceBudget
from business_code_agent.query_agent.langchain_adapter import LangChainQueryAnalyzer, LangChainQueryComposer, QueryModelInvocationError
from business_code_agent.query_agent.models import QueryIntent, QuestionUnderstanding
from business_code_agent.query_agent.service import QueryService
from business_code_agent.query_agent.validation import run_validation


class QueryAgentPhase4Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "query.db"
        self.db = load_demo(str(self.db_path))
        self.agent = BusinessCodeQueryAgent(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_rule_reason_runs_three_source_evidence_loop(self):
        result = self.agent.run("提款的时候为什么要校验 repayType，这个字段在哪里生成？")
        self.assertEqual(("RULE_REASON", "SUFFICIENT"), (result["intent"], result["evidenceStatus"]))
        self.assertLessEqual(result["iterations"], 3)
        self.assertEqual({"CODE", "BUSINESS", "REQUIREMENT"}, set(result["metrics"]["sourceCoverage"]))
        self.assertFalse(result["answer"]["unknowns"])
        self.assertFalse(result["answer"]["conflicts"])
        self.assertTrue(all(fact["evidenceIds"] for fact in result["answer"]["facts"]))
        raw = json.dumps(result["answer"], ensure_ascii=False)
        self.assertNotIn("尚未定位", raw)

    def test_data_trace_finds_producer_and_consumer_without_call(self):
        result = self.agent.run("repayType 字段在哪里生成、读取和校验？")
        self.assertEqual(("DATA_TRACE", "SUFFICIENT"), (result["intent"], result["evidenceStatus"]))
        statements = " ".join(item["statement"] for item in result["answer"]["facts"])
        self.assertIn("ApplyService.generate", statements)
        self.assertIn("WithdrawService.validate", statements)
        self.assertNotIn("直接调用", result["answer"]["conclusion"])

    def test_cross_process_uses_confirmed_business_bridge(self):
        result = self.agent.run("申请阶段和提款阶段之间是什么关系，repayType 如何连接两个流程？")
        self.assertEqual("CROSS_PROCESS", result["intent"])
        self.assertEqual("SUFFICIENT", result["evidenceStatus"])
        self.assertIn("BUSINESS", result["metrics"]["sourceCoverage"])
        self.assertTrue(any(item["sourceType"] == "BUSINESS" for item in result["answer"]["facts"]))

    def test_unknown_stops_without_guessing(self):
        result = self.agent.run("missingFlag 字段在哪里生成和校验？")
        self.assertEqual("INSUFFICIENT", result["evidenceStatus"])
        self.assertEqual(3, result["iterations"])
        self.assertTrue(result["answer"]["unknowns"])
        self.assertNotIn("一定", result["answer"]["conclusion"])
        self.assertFalse(result["answer"]["facts"])

    def test_conflict_detector_does_not_choose_a_source(self):
        facts = [
            StructuredFact("提款阶段沿用申请阶段的 projectNo", SourceType.BUSINESS, ["EV-B"], EvidenceRole.RULE, "projectNo", "DATA_STRATEGY", "SNAPSHOT", confidence="CONFIRMED"),
            StructuredFact("提款阶段实时查询 projectNo", SourceType.REQUIREMENT, ["EV-R"], EvidenceRole.RULE, "projectNo", "DATA_STRATEGY", "REALTIME"),
            StructuredFact("当前代码读取已保存 projectNo", SourceType.CODE, ["EV-C"], EvidenceRole.BEHAVIOR, "projectNo", "DATA_STRATEGY", "SNAPSHOT"),
        ]
        conflicts = ConflictDetector().detect(facts)
        self.assertTrue(conflicts)
        self.assertEqual("CONFLICT", conflicts[0]["status"])
        self.assertTrue(all(conflicts[0][key] for key in ("business", "requirement", "code")))

    def test_candidates_cannot_become_answer_facts(self):
        from business_code_agent.query_agent.answer import AnswerBuilder
        answer = AnswerBuilder().build({
            "intent": "BUSINESS_LOGIC", "question": "x",
            "known_facts": [{"statement": "候选推送方法一定执行", "sourceType": "CODE", "candidate": True, "evidenceIds": ["EV-X"]}],
        })
        self.assertFalse(answer["facts"])
        self.assertIn("证据不足", answer["conclusion"])

    def test_budget_deduplicates_and_caps_raw_context(self):
        assembler = EvidenceAssembler(self.db, budget=EvidenceBudget(1, 1, 1, 20))
        items = [
            {"evidenceId": "1", "sourceType": "CODE", "sourceId": "S", "sourceVersion": "1", "location": {"line": 1}, "content": "a" * 30, "status": "DIRECT"},
            {"evidenceId": "2", "sourceType": "CODE", "sourceId": "S", "sourceVersion": "1", "location": {"line": 1}, "content": "a" * 30, "status": "DIRECT"},
        ]
        result = assembler.apply_budget(items)
        self.assertEqual(1, len(result))
        self.assertLessEqual(sum(len(item["content"]) for item in result), 20)

    def test_run_observability_and_checkpoints_contain_no_raw_source(self):
        result = self.agent.run("repayType 字段在哪里生成和校验？")
        detail = QueryService(self.db).get_run(result["runId"])
        self.assertTrue(detail["steps"])
        self.assertTrue(detail["toolCalls"])
        self.assertTrue(detail["checkpoints"])
        checkpoint_text = " ".join(item["state_json"] for item in detail["checkpoints"])
        self.assertNotIn("apply.setRepayType", checkpoint_text)
        self.assertNotIn("提款的时候", checkpoint_text)

    def test_every_answer_evidence_id_resolves_and_follow_up_keeps_context(self):
        service = QueryService(self.db)
        first = service.query("repayType 字段在哪里生成、读取和校验？", conversation_id="CONV-TEST")
        second = service.query("它在哪里校验？", conversation_id="CONV-TEST")
        self.assertEqual("CONV-TEST", second["conversationId"])
        evidence_ids = {item["evidenceId"] for item in second["evidence"]}
        answer_ids = {
            evidence_id
            for section in ("facts", "inferences")
            for item in second["answer"].get(section, [])
            for evidence_id in item.get("evidenceIds", [])
        }
        self.assertTrue(answer_ids <= evidence_ids)
        self.assertGreaterEqual(self.db.execute("SELECT count(*) FROM query_message WHERE conversation_id='CONV-TEST'").fetchone()[0], 4)
        self.assertIn("validate", " ".join(item["statement"] for item in second["answer"]["facts"]).lower())
        feedback = service.record_feedback(second["runId"], "HELPFUL", "回答解决了校验位置问题")
        self.assertEqual("HELPFUL", feedback["rating"])
        self.assertEqual(1, self.db.execute("SELECT count(*) FROM query_feedback WHERE run_id=?", (second["runId"],)).fetchone()[0])

    def test_http_query_and_run_detail(self):
        server = make_server(str(self.db_path), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(base + "/api/query", method="POST", data=json.dumps({"question": "repayType 字段在哪里生成和校验？"}).encode(), headers={"Content-Type": "application/json"})
            result = json.loads(urlopen(request).read())
            self.assertEqual("SUFFICIENT", result["evidenceStatus"])
            detail = json.loads(urlopen(base + f"/api/query/{result['runId']}").read())
            self.assertEqual(result["runId"], detail["id"])
            self.assertTrue(detail["toolCalls"])
            self.assertTrue(detail["evidence"])
            self.assertEqual(result["evidence"], detail["evidence"])
            code = next(item for item in detail["evidence"] if item["sourceType"] == "CODE")
            self.assertEqual(code["location"]["startLine"], code["location"]["endLine"])
            workspace = json.loads(urlopen(base + "/api/workspace").read())
            self.assertTrue(workspace["repositories"])
            self.assertNotIn("root_path", workspace["repositories"][0])
            history = json.loads(urlopen(base + "/api/runs?limit=5").read())
            self.assertTrue(history["items"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_representative_twenty_case_fixture_passes(self):
        cases = Path(__file__).resolve().parent.parent / "examples" / "query_validation" / "cases.json"
        report = run_validation(self.db, cases)
        self.assertEqual((20, 20, True), (report["totalCases"], report["passedCases"], report["passed"]))
        self.assertEqual("REPEATABLE_REPRESENTATIVE_NOT_PRODUCTION", report["fixtureType"])

    def test_optional_langgraph_adapter_is_explicit_and_safe(self):
        try:
            from business_code_agent.query_agent.langgraph import build_query_graph
            from langgraph.checkpoint.memory import MemorySaver
        except ImportError:
            self.skipTest("langgraph optional dependency is not installed")
        graph = build_query_graph(self.db, db_path=str(self.db_path), checkpointer=MemorySaver())
        result = graph.invoke(
            {"question": "repayType 字段在哪里生成和校验？", "node_trace": []},
            {"configurable": {"thread_id": "phase4-test"}},
        )
        self.assertEqual("SUFFICIENT", result["result"]["evidenceStatus"])
        self.assertNotIn("evidence", result["result"])
        self.assertIn("EXECUTE_EVIDENCE_LOOP", result["node_trace"])


class QueryLangChainAdapterTest(unittest.TestCase):
    def test_structured_understanding_uses_langchain_agent_contract(self):
        captured = {}

        class Structured:
            def model_dump(self, mode="python"):
                return {"intent": "DATA_TRACE", "business_objects": ["订单"], "processes": [], "systems": [],
                        "field_hints": ["repayType"], "table_hints": [], "code_hints": [], "search_terms": ["repayType"]}

        class Runnable:
            def invoke(self, value):
                captured["input"] = value
                return {"structured_response": Structured()}

        analyzer = LangChainQueryAnalyzer(object(), agent_factory=lambda **kwargs: (captured.update(kwargs) or Runnable()))
        value = analyzer.understand("这个字段在哪里校验？", [{"question": "repayType 字段是什么？"}])
        self.assertEqual(QueryIntent.DATA_TRACE, value.intent)
        self.assertEqual("repayType", value.field_hints[0])
        self.assertEqual([], captured["tools"])
        self.assertIn("recent_conversation", captured["input"]["messages"][0]["content"])

    def test_composer_rejects_unloaded_or_unrelated_claims(self):
        class Structured:
            def model_dump(self, mode="python"):
                return {"conclusion": "完全无关的结论", "claims": [{"statement": "完全无关", "evidence_ids": ["EV-X"]}]}

        class Runnable:
            def invoke(self, value):
                return {"structured_response": Structured()}

        composer = LangChainQueryComposer(object(), agent_factory=lambda **kwargs: Runnable())
        with self.assertRaises(QueryModelInvocationError):
            composer.compose("问题", evidence_status="SUFFICIENT",
                             facts=[{"statement": "repayType 在校验", "evidenceIds": ["EV-1"]}],
                             unknowns=[], conflicts=[])


if __name__ == "__main__":
    unittest.main()

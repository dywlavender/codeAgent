from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.cli import load_demo
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.query_agent.langchain_adapter import LangChainQueryComposer, QueryModelInvocationError
from business_code_agent.query_agent.service import QueryService
from business_code_agent.schema import connect
from business_code_agent.tools import EvidenceTools


class SourceInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "query.db"
        self.db = load_demo(str(self.db_path))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_navigation_tools_include_callers_and_source_reads(self):
        tools = EvidenceTools(self.db)
        symbol = self.db.execute(
            "SELECT id FROM code_symbol WHERE qualified_name LIKE '%WithdrawService.validate'"
        ).fetchone()[0]
        source = tools.read_source(symbol)
        self.assertIn("repaytype", source["content"].casefold())
        self.assertTrue(tools.find_references(symbol) or tools.follow_call(symbol) == [])
        self.assertIsInstance(tools.follow_integration(symbol), list)

    def test_model_investigator_reads_source_and_bypasses_evaluator(self):
        calls = []

        class Investigator:
            def investigate(self, question, *, context, tools, ledger, max_tool_calls):
                calls.append({"question": question, "context": context})
                search = next(item for item in tools if item.name == "search_code")
                rows = search.invoke({"query": "WithdrawService.validate"})
                method = next(item for item in rows if item["kind"] == "METHOD")
                read = next(item for item in tools if item.name == "read_source")
                source = read.invoke({"symbol_id": method["id"]})
                self.assert_source = source
                return {
                    "conclusion": "中台代码在 repayType 不是 A 时抛出异常。",
                    "claims": [{
                        "statement": "WithdrawService.validate 在 repayType 不等于 A 时抛出 IllegalArgumentException。",
                        "reference_ids": [source["referenceId"]],
                        "source_type": "CODE",
                    }],
                    "answer_type": "FULL",
                }

        agent = BusinessCodeQueryAgent(self.db, query_investigator=Investigator())
        with patch.object(agent.evaluator, "apply", side_effect=AssertionError("legacy evaluator must not run")):
            result = agent.run("中台发起提款前具体会校验哪些条件？")
        self.assertEqual("MODEL_AGENT", result["answerMode"])
        self.assertEqual("FULL", result["answerType"])
        self.assertEqual("SUFFICIENT", result["evidenceStatus"])
        self.assertTrue(result["answer"]["facts"][0]["evidenceIds"])
        self.assertEqual(1, len(result["sourceReferences"]))
        self.assertIn("IllegalArgumentException", result["evidence"][0]["content"])
        self.assertTrue(calls)
        stored = self.db.execute("SELECT answer_json,state_json FROM query_agent_run").fetchone()
        self.assertNotIn("throw new IllegalArgumentException", stored[0])
        self.assertNotIn("throw new IllegalArgumentException", stored[1])

    def test_query_service_rehydrates_source_reference_for_history(self):
        class Investigator:
            def investigate(self, question, *, context, tools, ledger, max_tool_calls):
                rows = next(item for item in tools if item.name == "search_code").invoke({"query": "WithdrawService.validate"})
                method = next(item for item in rows if item["kind"] == "METHOD")
                source = next(item for item in tools if item.name == "read_source").invoke({"symbol_id": method["id"]})
                return {
                    "conclusion": "当前校验会拒绝不符合条件的请求。",
                    "claims": [{"statement": "代码包含提款条件校验。", "reference_ids": [source["referenceId"]]}],
                    "answer_type": "FULL",
                }

        service = QueryService(self.db)
        service.agent.query_investigator = Investigator()
        service.agent.answer_composer = service.agent.query_investigator
        value = service.query("提款前会校验什么？")
        self.assertTrue(value["evidence"])
        detail = service.get_run(value["runId"])
        self.assertEqual(value["answer"]["sourceReferences"], detail["sourceReferences"])
        self.assertIn("repaytype", detail["evidence"][0]["content"].casefold())
        stored_answer = self.db.execute("SELECT answer_json FROM query_agent_run WHERE id=?", (value["runId"],)).fetchone()[0]
        self.assertNotIn("public void validate", stored_answer)


class SourceInvestigatorAdapterTest(unittest.TestCase):
    def test_citation_must_be_returned_by_read_source(self):
        class Structured:
            def model_dump(self, mode="python"):
                return {
                    "conclusion": "未读取的代码结论",
                    "claims": [{"statement": "未读取的代码结论", "reference_ids": ["SRC-999"], "source_type": "CODE"}],
                    "answer_type": "FULL",
                }

        class Runnable:
            def invoke(self, value, *args):
                return {"structured_response": Structured()}

        composer = LangChainQueryComposer(object(), agent_factory=lambda **kwargs: Runnable())
        with self.assertRaises(QueryModelInvocationError):
            composer.investigate("问题", context={}, tools=[], ledger=_EmptyLedger())


class _EmptyLedger:
    def metadata(self):
        return []

    @property
    def business_evidence_ids(self):
        return set()


if __name__ == "__main__":
    unittest.main()

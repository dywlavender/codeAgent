from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.code_intelligence import JavaIndexer
from business_code_agent.knowledge_update.functional_service import FunctionalKnowledgeService, parse_function_document
from business_code_agent.knowledge_graph import KnowledgeGraphService
from business_code_agent.schema import connect


ROOT = Path(__file__).resolve().parent.parent


class FunctionalKnowledgeTest(unittest.TestCase):
    def test_minimal_document_parser(self):
        document = parse_function_document(ROOT / "knowledge/functions/application-withdrawal.md")
        self.assertEqual("application-withdrawal", document.id)
        self.assertEqual(3, len(document.entries))
        self.assertEqual(("loan_application", "保存贷款申请及还款方式"), document.key_tables[0])

    def test_refresh_resolves_monorepo_modules_and_table_evidence(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            JavaIndexer(db).ingest(str(ROOT / "examples/validation-project"), "validation-project")
            service = FunctionalKnowledgeService(db, project_config=ROOT / "project.config.json")
            result = service.refresh(analyze=False)
            self.assertEqual(1, result["functionCount"])
            detail = service.get_function("application-withdrawal")
            self.assertEqual({"RESOLVED"}, {item["resolution_status"] for item in detail["entries"]})
            self.assertTrue(any(item["relation_type"] == "READ_TABLE" for item in detail["retrievalLinks"]))
            self.assertTrue(any(item["relation_type"] == "WRITE_TABLE" for item in detail["retrievalLinks"]))
            self.assertEqual("NOT_RUN", detail["analysis"]["status"])
            graph = KnowledgeGraphService(db).search()
            self.assertEqual({"functions": 1, "projects": 3, "tables": 1}, {
                key: graph["allCounts"][key] for key in ("functions", "projects", "tables")
            })
            self.assertIn("ENTRY_POINT", {edge["relation"] for edge in graph["edges"]})
            db.close()

    def test_analysis_without_model_keeps_index_and_does_not_invent_rules(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            JavaIndexer(db).ingest(str(ROOT / "examples/validation-project"), "validation-project")
            service = FunctionalKnowledgeService(db, project_config=ROOT / "project.config.json")
            service.refresh(analyze=False)
            with patch("business_code_agent.knowledge_update.functional_service.model_config_from_environment", return_value=None):
                detail = service.analyze("application-withdrawal")
            self.assertEqual("INDEXED", detail["analysis"]["status"])
            self.assertEqual([], detail["analysis"]["flow"])
            self.assertEqual([], detail["analysis"]["rules"])
            db.close()


if __name__ == "__main__":
    unittest.main()

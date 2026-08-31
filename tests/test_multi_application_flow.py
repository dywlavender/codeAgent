from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.project_sync import sync_project
from business_code_agent.knowledge_update.baseline_service import BaselineKnowledgeService
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.schema import connect
from business_code_agent.tools import EvidenceTools


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "examples" / "multi-application-flow"


class MultiApplicationFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "knowledge.db"
        self.result = sync_project(FIXTURE / "project.config.json", self.database, offline=True)
        self.db = connect(str(self.database))
        BaselineKnowledgeService(
            self.db, project_config=FIXTURE / "project.config.json"
        ).refresh(use_model=False)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_application_web_spring_feign_and_edges_are_indexed(self):
        self.assertEqual(3, self.result["topology"]["applications"])
        self.assertEqual(3, self.db.execute("SELECT count(*) FROM application").fetchone()[0])
        kinds = {row[0] for row in self.db.execute("SELECT DISTINCT kind FROM code_symbol")}
        self.assertIn("PAGE", kinds)
        fact_types = {row[0] for row in self.db.execute("SELECT DISTINCT fact_type FROM code_fact")}
        self.assertTrue({
            "UI_EVENT", "HTTP_CALL", "HTTP_BASE_PATH", "HTTP_ENDPOINT", "RPC_SERVICE", "RPC_CALL",
        } <= fact_types)

        edges = self.db.execute(
            "SELECT edge_type,protocol,edge_key,status,evidence_ids_json FROM cross_application_edge ORDER BY edge_type"
        ).fetchall()
        self.assertEqual(2, len(edges))
        self.assertEqual({"HTTP", "RPC"}, {row["edge_type"] for row in edges})
        self.assertTrue(all(row["status"] == "VERIFIED" for row in edges))
        self.assertTrue(all(len(json.loads(row["evidence_ids_json"])) >= 2 for row in edges))

        evidence_ids = [item for row in edges for item in json.loads(row["evidence_ids_json"])]
        self.assertTrue(all(item["valid"] for item in EvidenceTools(self.db).validate_evidence_integrity(evidence_ids)))

    def test_query_agent_follows_the_verified_application_flow(self):
        result = BusinessCodeQueryAgent(self.db).run(
            "H5 点击提款提交按钮以后，后端经过哪些应用，最终在哪里处理提款？"
        )
        facts = result["answer"]["facts"]
        statements = "\n".join(item["statement"] for item in facts)
        self.assertIn("提款 H5", statements)
        self.assertIn("渠道服务", statements)
        self.assertIn("贷款中台", statements)
        self.assertIn("HTTP", statements)
        self.assertIn("FEIGN", statements)
        integration = [item for item in facts if "通过 HTTP" in item["statement"] or "通过 FEIGN" in item["statement"]]
        self.assertEqual(2, len(integration))
        self.assertTrue(all(len(item["evidenceIds"]) >= 2 for item in integration))
        self.assertTrue(result["answer"]["businessFlow"])
        self.assertGreaterEqual(len(result["answer"]["technicalFlow"]), 2)
        self.assertTrue(all(item["evidenceIds"] for item in result["answer"]["technicalFlow"]))


if __name__ == "__main__":
    unittest.main()

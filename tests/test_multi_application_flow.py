from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.project_sync import sync_project
from business_code_agent.knowledge_update.baseline_service import BaselineKnowledgeService
from business_code_agent.schema import connect
from business_code_agent.tools import EvidenceTools


ROOT = Path(__file__).resolve().parent.parent
# Keep regression data under tests/fixtures so user-facing examples can be
# replaced without breaking the automated multi-application checks.
FIXTURE = ROOT / "tests" / "fixtures" / "multi_application_flow"


class MultiApplicationFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "knowledge.db"
        self.result = sync_project(FIXTURE / "project.config.json", self.database, offline=True)
        self.db = connect(str(self.database))
        BaselineKnowledgeService(
            self.db, project_config=FIXTURE / "project.config.json"
        ).refresh(parser="markdown")

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


if __name__ == "__main__":
    unittest.main()

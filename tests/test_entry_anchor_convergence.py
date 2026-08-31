from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import shutil

from business_code_agent.knowledge_update.baseline_service import BaselineKnowledgeService
from business_code_agent.project_sync import sync_project
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.query_agent.entry_resolver import EntryResolver
from business_code_agent.query_agent.retriever import QueryRetriever
from business_code_agent.schema import connect
from business_code_agent.business_tools import BusinessTools


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "examples" / "multi-application-flow"


class EntryAnchorConvergenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "knowledge.db"
        sync_project(FIXTURE / "project.config.json", self.database, offline=True)
        self.db = connect(str(self.database))
        self.refresh = BaselineKnowledgeService(
            self.db, project_config=FIXTURE / "project.config.json"
        ).refresh(map_code=False, use_model=False)
        self.flow_id = self.db.execute(
            "SELECT id FROM business_entity WHERE entity_type='FLOW' LIMIT 1"
        ).fetchone()["id"]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_refresh_keeps_only_durable_entry_anchors(self):
        self.assertEqual({"ACTIVE": 3, "CANDIDATE": 0, "UNRESOLVED": 0}, self.refresh["anchorCounts"])
        self.assertEqual(0, self.refresh["mappingCounts"]["VERIFIED"])
        self.assertFalse(self.refresh["legacyMappingRefresh"])
        self.assertEqual(0, self.db.execute("SELECT count(*) FROM business_code_mapping").fetchone()[0])
        self.assertEqual(0, self.db.execute("SELECT count(*) FROM business_code_mapping_observation").fetchone()[0])
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(business_entry_anchor)")}
        self.assertNotIn("symbol_id", columns)
        self.assertNotIn("file_id", columns)
        detail = BusinessTools(self.db).get_business_knowledge(self.flow_id)
        self.assertEqual([], detail.get("mappings", []))
        self.assertEqual(3, len(detail["entryAnchors"]))
        # A topology refresh must not delete anchors through the application
        # foreign key; code syncs happen before every baseline/query cycle.
        sync_project(FIXTURE / "project.config.json", self.database, offline=True)
        self.assertEqual(3, self.db.execute(
            "SELECT count(*) FROM business_entry_anchor WHERE status='ACTIVE'"
        ).fetchone()[0])

    def test_resolved_anchor_is_used_before_global_code_search(self):
        result = QueryRetriever(self.db).initial_search({"searchTerms": ["提款申请主流程"]})
        tools = [item["tool"] for item in result["tool_calls"]]
        self.assertIn("get_business_entry_anchors", tools)
        self.assertEqual(3, tools.count("resolve_entry_anchor"))
        self.assertNotIn("search_code", tools)
        self.assertEqual(3, len(result["code_candidates"]))
        self.assertTrue(all(item.get("entry_resolution") == "RESOLVED" for item in result["code_candidates"]))

    def test_stale_anchor_falls_back_without_rewriting_knowledge(self):
        anchor = self.db.execute(
            "SELECT id,application_id FROM business_entry_anchor ORDER BY id LIMIT 1"
        ).fetchone()
        self.db.execute(
            "UPDATE business_entry_anchor SET entry_name='OldWithdrawResultJob' WHERE id=?",
            (anchor["id"],),
        )
        self.db.commit()
        self.assertEqual(
            "NOT_FOUND",
            EntryResolver(self.db).resolve(anchor["application_id"], "OldWithdrawResultJob")["status"],
        )
        result = QueryRetriever(self.db).initial_search({"searchTerms": ["提款申请主流程"]})
        tools = [item["tool"] for item in result["tool_calls"]]
        self.assertIn("resolve_entry_anchor", tools)
        self.assertIn("search_code", tools)
        self.assertEqual("OldWithdrawResultJob", self.db.execute(
            "SELECT entry_name FROM business_entry_anchor WHERE id=?", (anchor["id"],)
        ).fetchone()["entry_name"])
        self.assertEqual(3, self.db.execute("SELECT count(*) FROM business_entry_anchor").fetchone()[0])

    def test_entry_resolution_is_scoped_to_application(self):
        resolver = EntryResolver(self.db)
        self.assertEqual("RESOLVED", resolver.resolve("channel-service", "ChannelWithdrawController")["status"])
        self.assertEqual("NOT_FOUND", resolver.resolve("loan-middle", "ChannelWithdrawController")["status"])
        self.assertEqual("RESOLVED", resolver.resolve("loan-middle", "WithdrawService")["status"])

    def test_code_refactor_rebuilds_runtime_index_without_changing_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            shutil.copytree(FIXTURE, project)
            database = Path(folder) / "knowledge.db"
            sync_project(project / "project.config.json", database, offline=True)
            db = connect(str(database))
            BaselineKnowledgeService(
                db, project_config=project / "project.config.json"
            ).refresh(map_code=False, use_model=False)
            before = db.execute(
                "SELECT id FROM business_entry_anchor ORDER BY id"
            ).fetchall()

            service_file = project / "middle/src/main/java/demo/middle/WithdrawService.java"
            service_file.rename(service_file.with_name("NewWithdrawApplicationService.java"))
            for path in (
                service_file.with_name("NewWithdrawApplicationService.java"),
                project / "middle/src/main/java/demo/middle/MiddleWithdrawController.java",
            ):
                path.write_text(
                    path.read_text(encoding="utf-8").replace("WithdrawService", "NewWithdrawApplicationService"),
                    encoding="utf-8",
                )
            sync_project(project / "project.config.json", database, offline=True)
            after = db.execute(
                "SELECT id FROM business_entry_anchor ORDER BY id"
            ).fetchall()
            self.assertEqual([row["id"] for row in before], [row["id"] for row in after])
            self.assertEqual(
                "RESOLVED",
                EntryResolver(db).resolve("loan-middle", "MiddleWithdrawController")["status"],
            )
            self.assertTrue(db.execute(
                "SELECT 1 FROM code_symbol WHERE name='NewWithdrawApplicationService'"
            ).fetchone())
            self.assertFalse(db.execute(
                "SELECT 1 FROM code_symbol WHERE name='WithdrawService'"
            ).fetchone())
            db.close()

    def test_query_does_not_promote_runtime_findings_to_business_knowledge(self):
        before = {
            table: self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "business_entity", "business_relation_v2", "business_entry_anchor",
                "business_code_mapping", "business_code_mapping_observation",
            )
        }
        BusinessCodeQueryAgent(self.db).run("H5 点击提款提交按钮以后，后端经过哪些应用，最终在哪里处理提款？")
        after = {
            table: self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in before
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

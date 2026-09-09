"""Focused regression checks for the manual-AST and three-mode flow."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.ast_service import AstService
from business_code_agent.evaluation.case_import import import_upload
from business_code_agent.evaluation.runner import freeze_batch, summarize_pairs
from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryService
from business_code_agent.schema import connect
from tests.test_evaluation_service import make_project, suite_payload, wait_for


class ThreeModeFlowTest(unittest.TestCase):
    def test_import_accepts_reference_answers_and_maps_nonstandard_columns(self):
        standard = json.dumps({"cases": [{"问题": "问题", "参考答案": "答案"}]}, ensure_ascii=False)
        result = import_upload("cases.json", base64.b64encode(standard.encode()).decode())
        self.assertEqual(1, result["importSummary"]["success"])

        csv_data = "问法,标准解\n问题,答案\n"
        mapped = import_upload("cases.csv", base64.b64encode(csv_data.encode()).decode())
        self.assertTrue(mapped["mappingRequired"])
        result = import_upload("cases.csv", base64.b64encode(csv_data.encode()).decode(),
                               mapping={"question": "问法", "referenceAnswer": "标准解"})
        self.assertEqual("答案", result["cases"][0]["referenceAnswer"])

    def test_ast_is_not_created_by_batch_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = make_project(root)
            ast = AstService(project_config=config, data_root=root / ".data" / "ast")
            self.assertEqual("missing", ast.status()["status"])
            with self.assertRaisesRegex(ValueError, "手动生成"):
                freeze_batch(root / "batch", suite=suite_payload(), cases=suite_payload()["cases"],
                             project_config=config, arms=["ast"], variants=None)
            self.assertEqual("missing", ast.status()["status"])

    def test_switching_mode_does_not_resume_previous_runtime_session(self):
        class Runtime:
            runtime_name = "FAKE"

            def __init__(self):
                self.sessions = []

            def ask(self, question, *, workspace, session_id=None, event_callback=None,
                    cancel_check=None, repositories=None):
                self.sessions.append(session_id)
                return RuntimeResult("回答", "session-from-runtime")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = make_project(root)
            db = connect(str(root / "db.sqlite"))
            runtime = Runtime()
            try:
                service = QueryService(db, db_path=str(root / "db.sqlite"),
                                       project_config=config, runtime=runtime)
                first = service.query("问题", mode="none")
                second = service.query("问题二", conversation_id=first["conversationId"], mode="backbone")
                self.assertEqual([None, None], runtime.sessions)
                self.assertEqual("none", first["mode"])
                self.assertEqual("backbone", second["mode"])
            finally:
                db.close()

    def test_ast_version_change_starts_new_session_and_is_persisted(self):
        class Runtime:
            runtime_name = "FAKE"

            def __init__(self):
                self.sessions = []

            def ask(self, question, *, workspace, session_id=None, event_callback=None,
                    cancel_check=None, repositories=None):
                self.sessions.append(session_id)
                return RuntimeResult("回答", f"session-{len(self.sessions)}")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = make_project(root)
            ast = AstService(project_config=config)
            ast.generate()
            self.assertTrue(wait_for(lambda: ast.status().get("status") == "available"))
            version_one = ast.status()["currentVersionId"]
            db = connect(str(root / "db.sqlite"))
            runtime = Runtime()
            try:
                service = QueryService(db, db_path=str(root / "db.sqlite"),
                                       project_config=config, runtime=runtime)
                first = service.query("第一版问题", mode="ast")
                self.assertEqual(version_one, first["astVersionId"])
                (root / "repo-a" / "src" / "Main.java").write_text("class Main { int v2; }\n", encoding="utf-8")
                ast.generate()
                self.assertTrue(wait_for(lambda: ast.status().get("status") == "available"))
                version_two = ast.status()["currentVersionId"]
                self.assertNotEqual(version_one, version_two)
                second = service.query("第二版问题", conversation_id=first["conversationId"], mode="ast")
                self.assertEqual([None, None], runtime.sessions)
                self.assertEqual(version_two, second["astVersionId"])
                self.assertEqual(version_one, service.get_run(first["runId"])["astVersionId"])
                self.assertEqual(version_two, service.get_run(second["runId"])["astVersionId"])
                self.assertEqual(version_two, service.list_runs()[0]["astVersionId"])
            finally:
                db.close()

    def test_pair_comparisons_keep_valid_pair_when_third_arm_fails(self):
        cases = {"case-1": {"id": "case-1", "checks": ["结论正确"]}}

        def result(arm, status, seconds):
            value = {"id": f"case-1-r1-{arm}", "questionId": "case-1", "repeat": 1,
                     "arm": arm, "status": status, "elapsedSeconds": seconds, "toolCalls": 1}
            if status == "completed":
                value.update({"answer": "候选答案", "review": {
                    "status": "completed", "checks": [{"met": True, "reason": "正确", "evidence": "src/Main.java:1"}],
                    "issues": [],
                }})
            return value

        summary = summarize_pairs(
            [result("code_only", "completed", 1), result("optional", "completed", 2),
             result("ast", "failed", None)], cases, ["code_only", "optional", "ast"])
        self.assertEqual(0, summary["qualityBlocks"])
        self.assertEqual(1, len([item for item in summary["pairs"]
                                 if item["controlArm"] == "code_only" and item["arm"] == "optional"]))
        self.assertEqual(0, len([item for item in summary["pairs"]
                                 if item["controlArm"] == "code_only" and item["arm"] == "ast"]))

    def test_five_arm_pairs_use_raw_control_independently(self):
        cases = {"case-1": {"id": "case-1", "checks": ["结论正确"]}}

        def result(arm, status):
            value = {"id": f"case-1-r1-{arm}", "questionId": "case-1", "repeat": 1,
                     "arm": arm, "status": status, "elapsedSeconds": 1, "toolCalls": 1}
            if status == "completed":
                value.update({"answer": "候选答案", "review": {
                    "status": "completed", "checks": [{"met": True, "reason": "正确", "evidence": "src/Main.java:1"}],
                    "issues": [],
                }})
            return value

        arms = ["raw", "old_baseline", "code_map", "business_context", "code_map_context"]
        rows = [result(arm, "failed" if arm == "code_map_context" else "completed") for arm in arms]
        summary = summarize_pairs(rows, cases, arms)
        self.assertEqual(3, len([item for item in summary["pairs"]
                                if item["controlArm"] == "raw" and item["arm"] in arms[1:4]]))
        self.assertEqual(0, len([item for item in summary["pairs"] if item["arm"] == "code_map_context"]))


if __name__ == "__main__":
    unittest.main()

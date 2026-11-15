from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from business_code_agent.query.agent.pi import PiAgentRuntime
from business_code_agent.query.profile import PI
from business_code_agent.query.service import (
    QueryConfigurationError,
    QueryRuntimeError,
    QueryService,
)
from business_code_agent.storage.schema import connect
from business_code_agent.query.workspace import _safe_name


FAKE_PI = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"


class FakeAst:
    def __init__(self, source: Path):
        self.source = source

    def status(self):
        return {"status": "available", "currentVersionId": "ast-v1"}

    def version(self, version_id):
        return version_id, {"generatedAt": "2026-11-15T00:00:00Z"}, self.source


class PiQueryServiceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("a", "b"):
            source = self.root / "sources" / repository_id
            source.mkdir(parents=True)
            (source / "Main.java").write_text(
                f"class Main {{ // {repository_id}\n}}\n", encoding="utf-8")
        (self.root / "requirements").mkdir()
        (self.root / "requirements" / "request.md").write_text("需求\n", encoding="utf-8")
        (self.root / "business").mkdir()
        (self.root / "business" / "terms.md").write_text("业务术语\n", encoding="utf-8")
        (self.root / "code-map").mkdir()
        (self.root / "code-map" / "index.md").write_text("导航\n", encoding="utf-8")
        (self.root / "ast").mkdir()
        (self.root / "ast" / "structure.md").write_text("结构\n", encoding="utf-8")
        self.config = self.root / "project.json"
        self.config.write_text(json.dumps({
            "project": {"id": "pi-service", "name": "Pi Service"},
            "knowledge": {
                "businessContextRoot": str(self.root / "business"),
                "codeMapRoot": str(self.root / "code-map"),
            },
            "repositories": [
                {"id": repository_id, "gitUrl": "unused",
                 "localPath": str(self.root / "sources" / repository_id)}
                for repository_id in ("a", "b")
            ],
            "systems": [{"id": "empty-system", "name": "空系统"}],
            "applications": [],
        }), encoding="utf-8")
        self.db_path = str(self.root / "knowledge.db")
        self.db = connect(self.db_path)
        self.addCleanup(self.db.close)

    def service(self, scenario="normal", *, runtime=None, ast=False):
        runtime = runtime or PiAgentRuntime(
            profile=PI, environment={"FAKE_PI_SCENARIO": scenario},
            command=[sys.executable, str(FAKE_PI)],
            session_root=self.root / "pi-sessions")
        return QueryService(
            self.db, db_path=self.db_path, project_config=self.config,
            project_id="pi-service", runtime=runtime,
            ast_material_factory=(lambda: FakeAst(self.root / "ast")) if ast else None)

    def test_query_persists_run_trace_and_pi_session(self):
        service = self.service()
        events = []
        result = service.query("hello?", mode="backbone", event_callback=events.append)
        self.assertEqual(("completed", "PI", "backbone"),
                         (result["status"], result["runtime"], result["mode"]))
        self.assertEqual("fake answer from pi", result["answer"])
        self.assertTrue(Path(result["sessionId"]).is_file())
        self.assertEqual("pi-v1", result["execution"]["profileId"])
        self.assertEqual("9.9.9", result["execution"]["settings"]["piVersion"])
        self.assertEqual({"a", "b"}, {
            source["repositoryId"] for source in result["execution"]["materials"]
            if source["kind"] == "source"})
        self.assertEqual({"source", "requirements", "business_context"},
                         {source["kind"] for source in result["execution"]["materials"]})
        trace = service.get_trace(result["runId"])
        self.assertEqual("completed", trace["status"])
        self.assertEqual(2, len(trace["calls"]))
        self.assertEqual(["investigation", "answer"],
                         [call["phase"] for call in trace["calls"]])
        self.assertTrue(any(event["eventType"] == "tool" for event in trace["events"]))
        run = service.get_run(result["runId"])
        self.assertEqual("completed", run["status"])
        row = self.db.execute(
            "SELECT runtime,runtime_session_id,status FROM query_run WHERE id=?",
            (result["runId"],)).fetchone()
        self.assertEqual(("PI", result["sessionId"], "completed"), tuple(row))

    def test_empty_system_scope_is_rejected_before_pi_starts(self):
        service = self.service()
        runs = []
        with self.assertRaises(QueryConfigurationError) as caught:
            service.query("empty scope", mode="none",
                          scope={"systemIds": ["empty-system"]},
                          run_callback=runs.append)
        self.assertIn("未包含任何代码仓库", str(caught.exception))
        self.assertEqual([], runs[0]["execution"]["repositoryIds"])
        self.assertEqual("failed", service.get_run(runs[0]["runId"])["status"])
        self.assertFalse((service.workspace_manager.root / "pi").exists())

    def test_final_answer_citations_are_checked_against_delivered_reads(self):
        service = self.service("citation")
        result = service.query("cite", mode="none")
        verification = result["usage"]["verification"]
        self.assertFalse(verification["semanticProof"])
        self.assertEqual(["repos/a/Main.java"], verification["readFiles"])
        references = verification["references"]
        self.assertEqual(
            [("repos/a/Main.java", 2, 2, True),
             ("repos/a/Main.java", 1, 2, True),
             ("repos/b/Main.java", 1, 1, False),
             ("missing/Other.java", 1, 1, False)],
            [(item["path"], item["line"], item["lineEnd"], item["confirmed"])
             for item in references],
        )
        self.assertEqual(["repos/b/Main.java:1", "missing/Other.java:1"],
                         verification["unconfirmed"])
        trace = service.get_trace(result["runId"])
        self.assertEqual(verification, trace["run"]["usage"]["verification"])

    def test_material_changes_start_a_new_pi_session(self):
        service = self.service()
        first = service.query("first", mode="backbone")
        second = service.query("same", conversation_id=first["conversationId"],
                               mode="backbone")
        self.assertEqual(first["sessionId"], second["sessionId"])

        (self.root / "business" / "terms.md").write_text(
            "业务术语更新\n更多内容\n", encoding="utf-8")
        third = service.query("business changed", conversation_id=first["conversationId"],
                              mode="backbone")
        self.assertNotEqual(second["sessionId"], third["sessionId"])
        versions = {source["kind"]: source["version"]
                    for source in third["execution"]["materials"]}
        self.assertTrue(versions["business_context"].startswith("sha256:"))
        self.assertTrue(versions["requirements"].startswith("sha256:"))

        (self.root / "sources" / "a" / "Main.java").write_text(
            "class Main { // changed\n}\n", encoding="utf-8")
        fourth = service.query("source changed", conversation_id=first["conversationId"],
                               mode="backbone")
        self.assertNotEqual(third["sessionId"], fourth["sessionId"])
        source_versions = {source["version"]
                           for source in fourth["execution"]["materials"]
                           if source["kind"] == "source"}
        self.assertTrue(all(version.startswith("meta-sha256:")
                            for version in source_versions))

    def test_model_catalog_and_selected_model_shape_the_run(self):
        runtime = PiAgentRuntime(
            profile=PI,
            environment={"FAKE_PI_SCENARIO": "normal", "FAKE_PI_ECHO_MODEL": "1"},
            command=[sys.executable, str(FAKE_PI)],
            session_root=self.root / "pi-sessions")
        service = self.service(runtime=runtime)
        catalog = service.list_pi_models()
        self.assertEqual("fake/fake-model", catalog["defaultModel"])
        self.assertIn("zcode/glm-5.3-flash",
                      [item["id"] for item in catalog["items"]])
        self.assertNotIn("baseUrl", json.dumps(catalog))

        first = service.query("selected", mode="none", model="zcode/glm-5.3-flash")
        self.assertEqual("zcode/glm-5.3-flash", first["usage"]["actualModel"])
        self.assertEqual("zcode/glm-5.3-flash",
                         first["execution"]["settings"]["requestedModel"])
        self.assertEqual("zcode/glm-5.3-flash", first["execution"]["model"])
        second = service.query("same model", conversation_id=first["conversationId"],
                               mode="none", model="zcode/glm-5.3-flash")
        self.assertEqual(first["sessionId"], second["sessionId"])

        third = service.query("other model", conversation_id=first["conversationId"],
                              mode="none", model="fake/fake-model")
        self.assertNotEqual(second["sessionId"], third["sessionId"])
        self.assertEqual("fake/fake-model", third["usage"]["actualModel"])
        fourth = service.query("default model", conversation_id=first["conversationId"],
                               mode="none")
        self.assertNotEqual(third["sessionId"], fourth["sessionId"])
        self.assertEqual("fake/fake-model", fourth["usage"]["actualModel"])

        with self.assertRaises(ValueError):
            service.query("bad", mode="none", model="bad model;rm -rf /")

    def test_follow_up_reuses_session_and_mode_change_starts_a_new_one(self):
        service = self.service()
        first = service.query("first", mode="backbone")
        second = service.query("second", conversation_id=first["conversationId"],
                               mode="backbone")
        self.assertEqual(first["sessionId"], second["sessionId"])
        third = service.query("third", conversation_id=first["conversationId"], mode="none")
        self.assertNotEqual(second["sessionId"], third["sessionId"])
        self.assertEqual({"source", "requirements"}, {
            source["kind"] for source in third["execution"]["materials"]})
        statuses = {tool["capability"]: tool for tool in third["execution"]["tools"]}
        self.assertEqual(("disabled", "disabled"),
                         (statuses["business_context"]["policy"],
                          statuses["business_context"]["status"]))

    def test_scope_change_starts_a_new_session_and_limits_the_workspace(self):
        service = self.service()
        first = service.query("a", mode="none", scope={"repositoryIds": ["a"]})
        second = service.query("b", conversation_id=first["conversationId"], mode="none",
                               scope={"repositoryIds": ["b"]})
        self.assertNotEqual(first["sessionId"], second["sessionId"])
        self.assertEqual(["b"], [source["repositoryId"]
                                 for source in second["execution"]["materials"]
                                 if source["kind"] == "source"])
        root = service.workspace_manager.root / "pi" / _safe_name(first["conversationId"])
        self.assertFalse((root / "repos" / "a").exists())
        self.assertTrue((root / "repos" / "b").is_dir())

    def test_combined_and_ast_modes_mount_the_selected_materials(self):
        service = self.service(ast=True)
        combined = service.query("combined", mode="combined")
        kinds = {source["kind"] for source in combined["execution"]["materials"]}
        self.assertEqual({"source", "requirements", "business_context", "code_map", "ast"},
                         kinds)
        statuses = {tool["capability"]: tool for tool in combined["execution"]["tools"]}
        self.assertEqual("ready", statuses["ast"]["status"])
        self.assertEqual("ready", statuses["code_map"]["status"])
        self.assertEqual("ast-v1", combined["astVersionId"])

        ast = service.query("ast", mode="ast")
        ast_kinds = {source["kind"] for source in ast["execution"]["materials"]}
        self.assertEqual({"source", "requirements", "ast"}, ast_kinds)
        statuses = {tool["capability"]: tool for tool in ast["execution"]["tools"]}
        self.assertEqual("ready", statuses["ast"]["status"])
        self.assertEqual("disabled", statuses["code_map"]["status"])
        root = service.workspace_manager.root / "pi" / _safe_name(ast["conversationId"])
        self.assertEqual((self.root / "ast").resolve(),
                         (root / "knowledge" / "generated-code-map").resolve())

    def test_runtime_failure_is_persisted_without_langchain_fallback(self):
        service = self.service("error")
        runs = []
        with self.assertRaises(QueryRuntimeError) as caught:
            service.query("fail", mode="none", run_callback=runs.append)
        self.assertIn("fake provider down", str(caught.exception))
        self.assertEqual("pi-v1", runs[0]["execution"]["profileId"])
        run = service.get_run(runs[0]["runId"])
        self.assertEqual("failed", run["status"])
        self.assertIn("fake provider down", run["error"])
        trace = service.get_trace(runs[0]["runId"])
        self.assertEqual("failed", trace["status"])
        self.assertEqual("PI", trace["run"]["runtime"])

    def test_configuration_failure_is_reported(self):
        runtime = PiAgentRuntime(profile=PI, environment={},
                                 command=["/nonexistent/pi-for-test"],
                                 session_root=self.root / "pi-sessions")
        service = self.service(runtime=runtime)
        runs = []
        with self.assertRaises(QueryConfigurationError):
            service.query("hello", mode="none", run_callback=runs.append)
        self.assertEqual("failed", service.get_run(runs[0]["runId"])["status"])

    def test_cancel_marks_the_run_cancelled(self):
        service = self.service("cancel")
        results = []
        errors = []
        started = threading.Event()

        def run():
            worker_db = connect(self.db_path)
            try:
                results.append(QueryService(
                    worker_db, db_path=self.db_path, project_config=self.config,
                    project_id="pi-service", runtime=PiAgentRuntime(
                        profile=PI, environment={"FAKE_PI_SCENARIO": "cancel"},
                        command=[sys.executable, str(FAKE_PI)],
                        session_root=self.root / "pi-sessions")).query(
                            "cancel", mode="none",
                            run_callback=lambda value: started.set()))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                worker_db.close()

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(started.wait(timeout=10), errors)
        run_id = None
        for _ in range(200):
            row = self.db.execute(
                "SELECT id FROM query_run WHERE status='running'").fetchone()
            if row:
                run_id = row["id"]
                break
            time.sleep(0.02)
        self.assertIsNotNone(run_id)
        time.sleep(0.3)
        service.cancel_run(run_id)
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("cancelled", results[0]["status"])
        self.assertEqual("已停止。", results[0]["answer"])
        self.assertEqual("cancelled", service.get_run(run_id)["status"])
        trace = service.get_trace(run_id)
        self.assertEqual("cancelled", trace["status"])
        self.assertEqual("cancelled", trace["calls"][-1]["status"])


if __name__ == "__main__":
    unittest.main()

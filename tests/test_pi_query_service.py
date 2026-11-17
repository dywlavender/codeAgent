from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.codeintel.ast.service import AstService
from business_code_agent.knowledge.business_context import BusinessContextFiles
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

    def service(self, scenario="normal", *, runtime=None):
        runtime = runtime or PiAgentRuntime(
            profile=PI, environment={"FAKE_PI_SCENARIO": scenario},
            command=[sys.executable, str(FAKE_PI)],
            session_root=self.root / "pi-sessions")
        return QueryService(
            self.db, db_path=self.db_path, project_config=self.config,
            project_id="pi-service", runtime=runtime)

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

        BusinessContextFiles(self.root / "business").save(
            "terms.md", previous="业务术语\n", content="业务术语更新\n更多内容\n")
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

    def test_combined_and_ast_modes_use_a_structured_version_without_markdown(self):
        state = self.generate_ast()
        service = self.service()
        combined = service.query("combined", mode="combined")
        self.assertEqual({"source", "requirements", "business_context", "code_map", "ast"},
                         {source["kind"] for source in combined["execution"]["materials"]})
        self.assertEqual(state["currentVersionId"], combined["astVersionId"])
        ast = service.query("ast", mode="ast")
        self.assertEqual({"source", "requirements", "ast"},
                         {source["kind"] for source in ast["execution"]["materials"]})
        statuses = {tool["capability"]: tool for tool in ast["execution"]["tools"]}
        self.assertEqual(("required", "ready"), (statuses["ast"]["policy"], statuses["ast"]["status"]))
        self.assertEqual("disabled", statuses["code_map"]["status"])
        root = service.workspace_manager.root / "pi" / _safe_name(ast["conversationId"])
        self.assertFalse((root / "knowledge" / "generated-code-map").exists())
        self.assertFalse((root / "knowledge" / "ast").exists())

    def generate_ast(self):
        ast = AstService(project_config=self.config)
        ast.generate()
        deadline = time.monotonic() + 5
        while ast.status()["status"] == "generating" and time.monotonic() < deadline:
            time.sleep(0.01)
        state = ast.status()
        self.assertEqual("available", state["status"], state)
        return state

    def test_pi_ast_tools_are_versioned_optional_and_respect_scope(self):
        state = self.generate_ast()
        service = self.service()
        scope = {"repositoryIds": ["a"]}

        def ast_tool(run):
            return next(item for item in run["execution"]["tools"] if item["capability"] == "ast")

        with patch.object(AstService, "generate", side_effect=AssertionError("must remain manual")):
            ready = service.query("查结构", mode="combined", scope=scope)
            self.assertEqual("ready", ast_tool(ready)["status"])
            self.assertEqual(state["currentVersionId"], ast_tool(ready)["version"])
            self.assertIn("find_code_symbol", ready["execution"]["settings"]["tools"])
            material = next(item for item in ready["execution"]["materials"] if item["kind"] == "ast")
            self.assertEqual(("tools", True, None), (material["delivery"], material["available"], material["directory"]))
            self.assertFalse(next(item["available"] for item in ready["execution"]["materials"]
                                  if item["kind"] == "code_map"))
            disabled = service.query("只看源码", mode="none", scope=scope)
            self.assertEqual("disabled", ast_tool(disabled)["status"])
            self.assertNotIn("find_code_symbol", disabled["execution"]["settings"]["tools"])

            (self.root / "sources" / "a" / "Main.java").write_text("class Main { int changed; }\n")
            stale = service.query("继续查源码", mode="combined", scope=scope,
                                  conversation_id=ready["conversationId"])
            self.assertEqual("stale", ast_tool(stale)["status"])
            self.assertEqual([], ast_tool(stale)["names"])
            self.assertIn("read", stale["execution"]["settings"]["tools"])
            self.assertEqual("completed", stale["status"])
            self.assertEqual("stale", service.get_trace(stale["runId"])["run"]["astStatus"])
            self.assertEqual(state["currentVersionId"], service.get_run(ready["runId"])["astVersionId"])
        new_state = self.generate_ast()
        updated = service.query("查更新后的结构", mode="ast", scope=scope,
                                conversation_id=ready["conversationId"])
        self.assertEqual(new_state["currentVersionId"], updated["astVersionId"])
        self.assertNotEqual(state["currentVersionId"], updated["astVersionId"])
        self.assertNotEqual(ready["sessionId"], updated["sessionId"])
        self.assertEqual(state["currentVersionId"], service.get_run(ready["runId"])["astVersionId"])


    def test_broken_ast_extension_falls_back_before_prompt_and_updates_run_metadata(self):
        state = self.generate_ast()
        ready = self.service().query("查结构", mode="combined")
        with patch.dict(os.environ, {"FAKE_PI_NO_AST_EXTENSION": "1"}):
            service = self.service()
        notifications = []
        result = service.query("回退到源码", mode="combined",
                               conversation_id=ready["conversationId"],
                               run_callback=notifications.append)

        def ast_status(execution):
            return next(tool for tool in execution["tools"] if tool["capability"] == "ast")

        self.assertEqual("completed", result["status"])
        self.assertNotEqual(ready["sessionId"], result["sessionId"])
        self.assertEqual(1, len(notifications))
        status = ast_status(result["execution"])
        self.assertEqual(("unavailable", [], state["currentVersionId"]),
                         (status["status"], status["names"], status["version"]))
        self.assertIn("未完成加载", status["reason"])
        self.assertEqual(status, ast_status(notifications[0]["execution"]))
        self.assertEqual(status, ast_status(service.get_run(result["runId"])["execution"]))
        self.assertNotIn("find_code_symbol", result["execution"]["settings"]["tools"])
        trace = service.get_trace(result["runId"])
        self.assertEqual(status, ast_status(trace["run"]["execution"]))
        phases = [event["payload"].get("phase") for event in trace["events"]]
        self.assertLess(phases.index("ast_unavailable"), phases.index("thinking"))
        self.assertTrue(any(event["eventType"] == "tool" and event["payload"]["name"] == "read"
                            for event in trace["events"]))

    def test_system_guides_follow_scope_and_only_relevant_edits_replace_the_session(self):
        config = json.loads(self.config.read_text())
        config.update(systems=[{"id": "middle", "name": "中台"}, {"id": "channel", "name": "渠道"}],
                      applications=[
                          {"id": "api", "systemId": "middle", "repositoryId": "a", "sourceRoot": "api"},
                          {"id": "job", "systemId": "middle", "repositoryId": "b"},
                          {"id": "entry", "systemId": "channel", "repositoryId": "a"},
                      ])
        self.config.write_text(json.dumps(config), encoding="utf-8")
        service = self.service()
        source_root = service.project_context.system_guides_root
        for filename, text in {
            "middle/SYSTEM.md": "从项目约定的入口定位",
            "middle/applications/job.md": "批任务从注册器定位",
            "channel/SYSTEM.md": "渠道从路由定位",
        }.items():
            path = source_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        full = service.query("全工程", mode="none")
        conversation_id = full["conversationId"]
        folder = service.workspace_manager.root / "pi" / _safe_name(conversation_id) / "knowledge/system-guides"
        self.assertTrue((folder / "channel/SYSTEM.md").is_file())
        scoped = service.query("只看中台", mode="none", scope={"systemIds": ["middle"]},
                               conversation_id=conversation_id)
        self.assertFalse((folder / "channel").exists())
        self.assertTrue((folder / "middle/applications/job.md").is_file())
        self.assertEqual(["a", "b"], scoped["execution"]["repositoryIds"])

        scope = {"repositoryIds": ["a"]}
        narrowed = service.query("只授权仓库 a", mode="none", scope=scope, conversation_id=conversation_id)
        self.assertFalse((folder / "middle/applications/job.md").exists())
        old = narrowed["execution"]["systemGuides"]
        self.assertEqual(["knowledge/system-guides/middle/SYSTEM.md", "knowledge/system-guides/channel/SYSTEM.md"],
                         old["documents"])

        (source_root / "middle/applications/job.md").write_text("更新未授权仓库规则", encoding="utf-8")
        unchanged = service.query("续问", mode="none", scope=scope, conversation_id=conversation_id)
        self.assertEqual(narrowed["sessionId"], unchanged["sessionId"])
        (source_root / "middle/SYSTEM.md").write_text("入口改从登记表定位", encoding="utf-8")
        refreshed = service.query("查新导航", mode="none", scope=scope, conversation_id=conversation_id)
        self.assertNotEqual(narrowed["sessionId"], refreshed["sessionId"])
        self.assertNotEqual(old["version"], refreshed["execution"]["systemGuides"]["version"])
        self.assertEqual("入口改从登记表定位", (folder / "middle/SYSTEM.md").read_text())
        self.assertEqual(old, service.get_trace(narrowed["runId"])["run"]["execution"]["systemGuides"])

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

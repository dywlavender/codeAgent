from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.query.agent.direct import DirectAgentRuntime
from business_code_agent.query.profile import CORE
from business_code_agent.query.runtime import RuntimeResult
from business_code_agent.query.service import (
    QueryConfigurationError,
    QueryService,
    _default_runtime,
)
from business_code_agent.query.workspace import WorkspaceManager
from business_code_agent.storage.schema import connect


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class ExplodingModel(ToolModel):
    called: ClassVar[bool] = False

    def _call(self, *args, **kwargs):
        type(self).called = True
        raise AssertionError("model must not be called")


def project_config(root: Path, repositories=("channel", "decision")) -> Path:
    config = root / "project.json"
    config.write_text(json.dumps({
        "project": {"id": "core-query", "name": "Core Query"},
        "repositories": [
            {"id": repository_id, "gitUrl": "unused", "localPath": f"repos/{repository_id}"}
            for repository_id in repositories
        ],
    }), encoding="utf-8")
    return config


class CoreQueryServiceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("channel", "decision"):
            source = self.root / "repos" / repository_id
            source.mkdir(parents=True)
            (source / f"{repository_id.title()}.java").write_text(
                "class Main {\n  void withdraw() {}\n}\n", encoding="utf-8")
        (self.root / "requirements").mkdir()
        (self.root / "requirements" / "request.md").write_text(
            "提款需要校验金额。\n", encoding="utf-8")
        self.config = project_config(self.root)
        self.db_path = str(self.root / "knowledge.db")
        self.db = connect(self.db_path)
        self.addCleanup(self.db.close)

    def service(self, responses, profile=CORE, config=None):
        model = ToolModel(responses=responses)
        runtime = DirectAgentRuntime(profile=profile, model=model, environment={})
        return QueryService(self.db, db_path=self.db_path,
                            project_config=config or self.config,
                            project_id="core-query", runtime=runtime), runtime

    def test_core_query_completes_with_source_evidence_and_run_trace(self):
        responses = [
            AIMessage(content="", tool_calls=[{
                "id": "read", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 5},
            }]),
            AIMessage(content="提款入口为 repos/channel/Channel.java:2，当前实现调用 withdraw。"),
        ]
        service, runtime = self.service(responses)
        events, runs = [], []
        result = service.query("提款入口在哪里？", mode="combined",
                               event_callback=events.append, run_callback=runs.append)
        self.assertEqual(("core", "零索引核心", "completed"),
                         (result["mode"], result["modeLabel"], result["status"]))
        self.assertEqual("LANGCHAIN_DIRECT", result["runtime"])
        self.assertTrue(result["sessionId"].startswith("DA-"))
        self.assertEqual("core-v1", result["execution"]["profileId"])
        self.assertEqual({"channel", "decision"},
                         {source["repositoryId"] for source in result["execution"]["materials"]
                          if source["kind"] == "source"})
        self.assertEqual({"source", "requirements"},
                         {source["kind"] for source in result["execution"]["materials"]})
        statuses = {tool["capability"]: tool for tool in result["execution"]["tools"]}
        self.assertEqual(("required", "ready"),
                         (statuses["file_tools"]["policy"], statuses["file_tools"]["status"]))
        for capability in ("search_code", "contract_trace", "business_context", "ast",
                           "code_map", "hybrid_search"):
            self.assertEqual(("disabled", "disabled"),
                             (statuses[capability]["policy"], statuses[capability]["status"]))
        self.assertTrue(any(event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                            for event in events))
        self.assertEqual(1, len(runs))
        self.assertEqual(result["runId"], runs[0]["runId"])
        self.assertEqual(result["execution"], runs[0]["execution"])
        trace = service.get_trace(result["runId"])
        self.assertEqual("completed", trace["status"])
        self.assertEqual(result["execution"], trace["run"]["execution"])
        self.assertTrue(any(event["eventType"] == "tool" for event in trace["events"]))
        self.assertTrue(result["usage"]["verification"]["references"][0]["confirmed"])
        self.assertFalse(result["usage"]["semanticProof"])
        run = service.get_run(result["runId"])
        self.assertEqual(result["execution"], run["execution"])
        self.assertEqual(1, self.db.execute(
            "SELECT count(*) FROM query_run WHERE status='completed'").fetchone()[0])
        workspace = Path(next(source["directory"] for source in result["execution"]["materials"]
                             if source["kind"] == "source")).parent.parent
        self.assertFalse((workspace / "knowledge").exists())
        self.assertFalse((self.root / ".data" / "ast").exists())
        self.assertFalse((self.root / "retrieval-index").exists())
        self.assertFalse((self.root / "contract-index").exists())

        with patch("business_code_agent.query.service.AstMaterial",
                   side_effect=AssertionError("core must not inspect AST")), \
                patch.object(WorkspaceManager, "ensure",
                             side_effect=AssertionError("core must not build combined")):
            summary = service.workspace_summary()
        self.assertEqual("core", summary["workspace"]["mode"])
        self.assertEqual({"repository", "requirements"},
                         {source["kind"] for source in summary["sources"]})

    def test_frontend_default_mode_maps_to_core_and_legacy_modes_are_rejected(self):
        service, _ = self.service([AIMessage(content="源码答案。"),
                                   AIMessage(content="第二次答案。")])
        first = service.query("问题", mode="combined")
        self.assertEqual("core", first["mode"])
        with self.assertRaisesRegex(ValueError, "不使用旧资料模式"):
            service.query("问题", mode="backbone")
        self.assertEqual(1, self.db.execute("SELECT count(*) FROM query_run").fetchone()[0])
        self.assertEqual(["user", "assistant"],
                         [row[0] for row in self.db.execute("SELECT role FROM query_message ORDER BY created_at,id")])

    def test_missing_configured_repository_fails_before_model_and_is_recorded(self):
        service, runtime = self.service(
            [AIMessage(content="不应执行")],
            config=project_config(self.root, ("channel", "middle", "decision")),
        )
        runtime.model = ExplodingModel(responses=[])
        runs = []
        with self.assertRaises(QueryConfigurationError) as raised:
            service.query("提款入口在哪里？", run_callback=runs.append)
        message = str(raised.exception)
        self.assertIn("middle", message)
        self.assertIn("授权仓库不可用", message)
        self.assertFalse(ExplodingModel.called)
        self.assertEqual(1, len(runs))
        run_id = runs[0]["runId"]
        run = service.get_run(run_id)
        self.assertEqual("failed", run["status"])
        self.assertIn("middle", run["error"])
        self.assertEqual({"channel", "middle", "decision"},
                         set(run["execution"]["repositoryIds"]))
        self.assertEqual("failed", run["execution"]["phase"])
        self.assertIn("middle", run["execution"]["error"])
        self.assertEqual(["error"], [event["eventType"] for event in run["events"]])
        self.assertEqual(["user"], [row[0] for row in self.db.execute("SELECT role FROM query_message")])
        self.assertFalse((service.workspace_manager.root / "core" / run_id).exists())
        trace_path = service.trace_root / f"{run_id}.json"
        self.assertTrue(trace_path.is_file())
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual("failed", trace["status"])
        self.assertEqual(run["execution"], trace["run"]["execution"])
        self.assertEqual("error", trace["events"][0]["eventType"])

    def test_changing_scope_or_profile_rebuilds_the_model_session(self):
        service, _ = self.service([AIMessage(content="一"), AIMessage(content="二"),
                                   AIMessage(content="三"), AIMessage(content="四")])
        first = service.query("问题一", scope={"repositoryIds": ["channel"]})
        same = service.query("问题二", conversation_id=first["conversationId"],
                             scope={"repositoryIds": ["channel"]})
        self.assertEqual(first["sessionId"], same["sessionId"])
        changed_scope = service.query("问题三", conversation_id=first["conversationId"],
                                      scope={"repositoryIds": ["decision"]})
        self.assertNotEqual(first["sessionId"], changed_scope["sessionId"])
        self.assertEqual(["decision"], changed_scope["scope"]["repositoryIds"])
        alt = DirectAgentRuntime(profile=replace(CORE, id="core-alt"),
                                 model=ToolModel(responses=[AIMessage(content="五")]),
                                 environment={})
        service.runtime = alt
        changed_profile = service.query("问题四", conversation_id=first["conversationId"],
                                        scope={"repositoryIds": ["decision"]})
        self.assertNotEqual(changed_scope["sessionId"], changed_profile["sessionId"])
        self.assertEqual("core-alt", changed_profile["execution"]["profileId"])
        self.assertEqual(4, self.db.execute(
            "SELECT count(*) FROM query_run WHERE status='completed'").fetchone()[0])

    def test_core_cancel_keeps_run_event_and_trace(self):
        ready = threading.Event()
        results, errors = [], []

        class WaitingDirect:
            runtime_name = "LANGCHAIN_DIRECT_WAIT"
            profile = CORE

            def execution_settings(self):
                return {"requestedModel": "wait-model"}

            def ask(self, question, *, context, prepared, session_id=None,
                    event_callback=None, cancel_check=None):
                ready.set()
                while not cancel_check():
                    time.sleep(0.01)
                return RuntimeResult("已停止。", session_id or "DA-wait", status="cancelled")

        def worker():
            worker_db = connect(self.db_path)
            try:
                results.append(QueryService(
                    worker_db, db_path=self.db_path, project_config=self.config,
                    project_id="core-query", runtime=WaitingDirect()).query("等待"))
            except Exception as exc:
                errors.append(exc)
            finally:
                worker_db.close()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            row = None
            for _ in range(100):
                row = self.db.execute(
                    "SELECT id FROM query_run WHERE status='running'").fetchone()
                if row:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(row)
            run_id = row["id"]
            self.assertEqual("cancelling", self.service_cancel(run_id)["status"])
        finally:
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("cancelled", results[0]["status"])
        run = self.db.execute("SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()
        self.assertEqual("cancelled", run["status"])
        trace_path = self.service_trace_root() / f"{run_id}.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual("cancelled", trace["status"])
        self.assertTrue(any(event["payload"].get("phase") == "cancelled"
                            for event in trace["events"]))

    def service_cancel(self, run_id):
        service = QueryService(self.db, db_path=self.db_path, project_config=self.config,
                               project_id="core-query", runtime=DirectAgentRuntime(
                                   profile=CORE, model=ToolModel(responses=[]),
                                   environment={}))
        return service.cancel_run(run_id)

    def service_trace_root(self):
        return Path(self.db_path).parent / "query-traces"


class CoreDefaultRuntimeTest(unittest.TestCase):
    def test_core_profile_selects_direct_runtime_without_legacy_index_probe(self):
        with patch.dict(os.environ, {"BUSINESS_CODE_AGENT_PROFILE": "core-v1"}, clear=True):
            runtime = _default_runtime(index_root=Path("/tmp/does-not-exist"), project_id="core")
        self.assertEqual("LANGCHAIN_DIRECT", runtime.runtime_name)
        self.assertEqual(("core-v1", "direct"), (runtime.profile.id, runtime.profile.investigation))


if __name__ == "__main__":
    unittest.main()

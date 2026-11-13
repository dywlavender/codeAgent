import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.api.server import make_server
from business_code_agent.platform.projects import ProjectRegistry
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.service import QueryRuntimeError, QueryService
from business_code_agent.storage.schema import connect


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class FailingModel(ToolModel):
    async def _agenerate(self, *args, **kwargs):
        raise RuntimeError("fixture model failure")


class QueryTraceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = ProjectRegistry(self.root / "platform")
        self.contexts = {}
        for project, value in (("alpha", 1), ("beta", 2)):
            source = self.root / project / "source"
            source.mkdir(parents=True)
            (source / "value.py").write_text(f"def value():\n    return {value}\n")
            config = self.root / project / "project.json"
            config.write_text(json.dumps({"project": {"id": project, "name": project},
                                           "repositories": [{"id": "shared", "localPath": str(source)}]}))
            self.contexts[project] = self.registry.register(config)

    def service(self, project="alpha", model=None):
        context = self.contexts[project]
        db = connect(str(context.db_path))
        self.addCleanup(db.close)
        runtime = LangChainRuntime(model=model or ToolModel(responses=[AIMessage(content="依据已读原文回答")]),
            environment={"BUSINESS_CODE_MODEL_API_KEY": "trace-secret-must-not-be-written"}, project_id=project)
        return QueryService(db, db_path=str(context.db_path), project_config=context.config_path,
            workspace_root=context.workspace_root, trace_root=context.data_root / "query-traces",
            runtime=runtime, project_id=project, project_scoped=True)

    def test_archive_keeps_full_requests_tools_and_decisions_after_workspace_removal(self):
        source = self.root / "alpha/source/value.py"
        original = "# " + "long source " * 1800 + "\ndef value():\n    return 1\n"
        source.write_text(original)
        service = self.service()
        result = service.query("解释 repos/shared/value.py 这个函数", mode="none")
        run_id = result["runId"]
        archived = Path(result["usage"]["traceArchive"])
        trace = service.get_trace(run_id)
        self.assertEqual("completed", trace["run"]["status"])
        self.assertEqual(1, len(trace["calls"]))
        self.assertTrue(trace["modelSnapshotAvailable"])
        tools = [e["payload"] for e in trace["runtimeEvents"] if e["eventType"] == "tool" and e["payload"]["status"] == "completed"]
        self.assertIn("long source " * 1800, tools[0]["output"])
        self.assertIn("long source " * 1800, str(trace["calls"][0]["messages"]))
        self.assertTrue(any(e["eventType"] == "retrieval" for e in trace["runtimeEvents"]))
        self.assertNotIn("trace-secret-must-not-be-written", archived.read_text())
        self.assertGreaterEqual(trace["calls"][0]["durationMs"], 0)
        shutil.rmtree(service.workspace_manager.root)
        self.assertEqual(trace["calls"], self.service().get_trace(run_id)["calls"])

    def test_failed_model_call_is_archived_with_request_and_error(self):
        service = self.service(model=FailingModel(responses=[]))
        with self.assertRaises(QueryRuntimeError):
            service.query("解释 repos/shared/value.py 函数", mode="none")
        run = service.list_runs()[0]
        trace = service.get_trace(run["id"])
        self.assertEqual("failed", trace["run"]["status"])
        self.assertEqual("failed", trace["calls"][0]["status"])
        self.assertEqual("fixture model failure", trace["calls"][0]["error"])
        self.assertIn("return 1", str(trace["calls"][0]["messages"]))
        self.assertTrue(any(e["eventType"] == "error" for e in trace["events"]))

    def test_zero_call_cancellation_still_has_a_trace(self):
        runtime = LangChainRuntime(model=ToolModel(responses=[]), environment={}, project_id="alpha")
        workspace = self.root / "cancel-workspace"
        workspace.mkdir()
        (workspace / "CLAUDE.md").write_text("只读")
        result = runtime.ask("停止", workspace=workspace, cancel_check=lambda: True)
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        self.assertEqual("cancelled", trace["status"])
        self.assertEqual([], trace["calls"])
        self.assertTrue(trace["events"])

    def test_project_cannot_read_another_projects_run_trace(self):
        first = self.service("alpha").query("解释 repos/shared/value.py 函数", mode="none")
        second = self.service("beta").query("解释 repos/shared/value.py 函数", mode="none")
        with self.assertRaises(ValueError):
            self.service("beta").get_trace(first["runId"])
        trace = self.service("beta").get_trace(second["runId"])
        self.assertIn("return 2", str(trace["calls"][0]["messages"]))
        self.assertNotIn("return 1", str(trace["calls"][0]["messages"]))

    def test_trace_api_is_project_scoped_and_historical_events_remain_available(self):
        def factory(db, **kwargs):
            return QueryService(db, runtime=LangChainRuntime(model=ToolModel(responses=[AIMessage(content="测试回答")]),
                                environment={}, project_id=kwargs.get("project_id")), **kwargs)
        with patch("business_code_agent.api.server.QueryService", factory):
            server = make_server(str(self.root / "unused.db"), port=0, project_registry=self.registry)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                payload = json.dumps({"question": "解释 repos/shared/value.py 函数", "mode": "none"}).encode()
                with urlopen(Request(base + "/api/projects/alpha/query", data=payload, headers={"Content-Type": "application/json"})) as response:
                    result = json.load(response)
                path = "/query/" + result["runId"] + "/trace"
                with urlopen(base + "/api/projects/alpha" + path) as response:
                    trace = json.load(response)
                self.assertEqual("alpha", trace["projectId"])
                self.assertEqual(1, len(trace["calls"]))
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(base + "/api/projects/beta" + path)
                self.assertIn(rejected.exception.code, (400, 404))
                rejected.exception.close()
                Path(result["usage"]["traceArchive"]).unlink()
                with urlopen(base + "/api/projects/alpha" + path) as response:
                    historical = json.load(response)
                self.assertFalse(historical["historical"])
                self.assertTrue(historical["calls"])
                self.assertTrue(historical["events"])
                Path(result["usage"]["tracePath"]).unlink()
                with urlopen(base + "/api/projects/alpha" + path) as response:
                    events_only = json.load(response)
                self.assertTrue(events_only["historical"])
                self.assertFalse(events_only["modelSnapshotAvailable"])
                self.assertTrue(events_only["events"])
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

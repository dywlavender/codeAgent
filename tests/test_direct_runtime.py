from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.agent.direct import DirectAgentRuntime, verify_answer
from business_code_agent.query.agent.session import DirectSessionStore, retire_changed_sources
from business_code_agent.query.capabilities import FileCapability, prepare_capabilities
from business_code_agent.query.context import RunContext
from business_code_agent.query.materials import MaterialManifest
from business_code_agent.query.profile import CORE, ToolStatus
from business_code_agent.query.workspace import WorkspaceManager


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class ExplodingModel(ToolModel):
    def _call(self, *args, **kwargs):
        raise AssertionError("model must not be called")


class ExtraProvider:
    id = "extra"

    def status(self, context):
        return ToolStatus("extra", "optional", "ready", ("Ping",))

    def tools(self, context):
        @tool
        def Ping(value: str = "pong") -> str:
            """返回固定测试值。"""
            return json.dumps({"value": value})

        return (Ping,)

    def instructions(self, context):
        return "Ping 只用于验证工具注册。"


class DirectRuntimeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("channel", "decision"):
            source = self.root / "sources" / repository_id
            source.mkdir(parents=True)
            (source / f"{repository_id.title()}.java").write_text(
                "class Main {\n  // withdraw\n  void applyWithdraw() {}\n}\n", encoding="utf-8")
        config = self.root / "project.json"
        config.write_text(json.dumps({
            "project": {"id": "direct-test", "name": "Direct Test"},
            "repositories": [
                {"id": repository_id, "gitUrl": "unused",
                 "localPath": str(self.root / "sources" / repository_id)}
                for repository_id in ("channel", "decision")
            ],
        }), encoding="utf-8")
        manager = WorkspaceManager(project_config=config, workspace_root=self.root / "workspaces")
        self.workspace = manager.prepare_core(run_id="RUN-direct",
                                              repository_ids=["channel", "decision"])
        self.project = ProjectContext.legacy(self.root / "db.sqlite", config)
        manifest = MaterialManifest.for_core(self.workspace.path, self.workspace.repository_mounts)
        self.context = RunContext(
            run_id="RUN-direct", project=self.project, profile=CORE,
            repository_ids=("channel", "decision"), workspace=self.workspace.path,
            materials=manifest.sources, session_id=None,
        )
        self.prepared = prepare_capabilities(self.context)

    def runtime(self, responses, **kwargs):
        return DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                  environment={}, **kwargs)

    def test_model_autonomously_searches_reads_and_answers_across_repositories(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[
                {"id": "dirs", "name": "Glob",
                 "args": {"pattern": "repos/*", "kind": "directories"}},
                {"id": "grep", "name": "Grep", "args": {"pattern": "withdraw"}},
            ]),
            AIMessage(content="", tool_calls=[
                {"id": "read", "name": "Read",
                 "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 10}},
            ]),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3，调用 applyWithdraw；"
                              "未确认 decision 仓库中的后续处理。"),
        ])
        runtime = DirectAgentRuntime(profile=CORE, model=model, environment={})
        result = runtime.ask("提款入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertTrue(result.runtime_session_id.startswith("DA-"))
        self.assertEqual(3, result.usage["modelCalls"])
        self.assertEqual(3, result.usage["toolCalls"])
        self.assertFalse(result.usage["semanticProof"])
        tool_events = [event for event in result.events if event["eventType"] == "tool"]
        self.assertEqual(
            [("Glob", "completed"), ("Grep", "completed"), ("Read", "completed")],
            [(event["payload"]["name"], event["payload"]["status"])
             for event in tool_events if event["payload"]["status"] != "running"],
        )
        references = result.usage["verification"]["references"]
        self.assertEqual(1, len(references))
        self.assertTrue(references[0]["confirmed"])
        self.assertEqual("repos/channel/Channel.java", references[0]["matchedPath"])
        self.assertIn("repos/channel/Channel.java:3", result.answer)
        session_path = (self.project.data_root / "query-sessions" / "core"
                        / f"{result.runtime_session_id}.json")
        self.assertTrue(session_path.is_file())
        session = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(self.context.project.project_id, session["scope"]["projectId"])
        self.assertEqual(1, len([message for message in session["messages"]
                                if message["type"] == "human"]))

    def test_unregistered_tool_returns_error_to_model_without_extra_loop(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "write", "name": "Write",
                                               "args": {"path": "x", "content": "y"}}]),
            AIMessage(content="没有写入工具，无法修改文件；已确认只读范围。"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "帮我改代码", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        errors = [event["payload"] for event in result.events
                  if event["eventType"] == "tool" and event["payload"]["status"] == "error"]
        self.assertEqual(1, len(errors))
        self.assertIn("未注册工具", errors[0]["output"])
        self.assertEqual({"Read", "Glob", "Grep"}, {tool.name for tool in self.prepared.tools})

    def test_cancellation_prevents_model_and_tool_execution(self):
        result = DirectAgentRuntime(profile=CORE, model=ExplodingModel(responses=[]),
                                    environment={}).ask(
            "停止", context=self.context, prepared=self.prepared, cancel_check=lambda: True)
        self.assertEqual("cancelled", result.status)
        self.assertEqual(0, result.usage["modelCalls"])
        self.assertEqual("已停止。", result.answer)
        self.assertEqual("cancelled", result.events[-1]["payload"]["phase"])

    def test_session_scope_and_changed_source_invalidate_history(self):
        store = DirectSessionStore(self.root / "sessions")
        read = {
            "path": "repos/channel/Channel.java", "lineStart": 1, "lineEnd": 4,
            "content": "1: class Main {", "sourceMtimeNs": 123,
        }
        messages = [HumanMessage("旧问题"), ToolMessage(
            content=json.dumps(read), name="Read", tool_call_id="read",
            status="success")]
        store.save("DA-abc", {"project": "A"}, messages)
        self.assertEqual(2, len(store.load("DA-abc", {"project": "A"})))
        self.assertEqual([], store.load("DA-abc", {"project": "B"}))
        self.assertEqual([], store.load("LC-abc", {"project": "A"}))
        retired = retire_changed_sources(messages, self.prepared.files)
        self.assertIn("已废弃", retired[1].content)

    def test_extra_provider_registers_tool_without_changing_direct_loop(self):
        prepared = prepare_capabilities(self.context, providers=[FileCapability(), ExtraProvider()])
        self.assertIn("Ping", {tool.name for tool in prepared.tools})
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "ping", "name": "Ping", "args": {"value": "ok"}}]),
            AIMessage(content="额外只读工具已返回 pong。"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "调用额外工具", context=self.context, prepared=prepared)
        self.assertEqual("completed", result.status)
        outputs = [event["payload"]["output"] for event in result.events
                   if event["eventType"] == "tool" and event["payload"]["status"] == "completed"]
        self.assertIn('"value": "ok"', outputs[0])

    def test_verify_answer_requires_fully_delivered_line_range(self):
        def read(path, start, end):
            return ToolMessage(content=json.dumps({
                "path": path, "lineStart": start, "lineEnd": end, "content": "源码"}),
                name="Read", tool_call_id=f"read-{start}")

        messages = [read("repos/channel/Channel.java", 10, 14),
                    read("repos/channel/Channel.java", 15, 20)]
        result = verify_answer(
            "单行 repos/channel/Channel.java:12；"
            "完整 repos/channel/Channel.java:10-20；"
            "越界 repos/channel/Channel.java:15-25；"
            "仅文件 repos/channel/Channel.java。", messages)
        references = result["references"]
        self.assertEqual([True, True, False, False],
                         [reference["confirmed"] for reference in references])
        self.assertEqual((10, 20), (references[1]["line"], references[1]["lineEnd"]))
        self.assertTrue(references[3]["fileRead"])
        self.assertEqual(["repos/channel/Channel.java:15-25", "repos/channel/Channel.java"],
                         result["unconfirmed"])

    def test_failed_model_call_keeps_request_and_tool_definitions(self):
        calls = []
        runtime = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=[]), environment={})
        with self.assertRaises(IndexError):
            runtime.ask("问题", context=self.context, prepared=self.prepared,
                        call_callback=calls.append)
        self.assertEqual(["running", "failed"], [call["status"] for call in calls])
        call = calls[-1]
        self.assertEqual((1, "IndexError"), (call["attempt"], call["errorType"]))
        self.assertEqual(["system", "human"], [message["role"] for message in call["messages"]])
        self.assertEqual({"Read", "Glob", "Grep"},
                         {definition["function"]["name"] for definition in call["tools"]})

    def test_cancellation_after_tool_keeps_completed_model_call(self):
        cancelled = {"value": False}
        calls = []

        class CancelProvider:
            id = "cancel"

            def status(self, context):
                return ToolStatus("cancel", "optional", "ready", ("Stop",))

            def tools(self, context):
                @tool
                def Stop() -> str:
                    """请求停止本轮调查。"""
                    cancelled["value"] = True
                    return json.dumps({"stopped": True})

                return (Stop,)

            def instructions(self, context):
                return ""

        prepared = prepare_capabilities(self.context,
                                        providers=[FileCapability(), CancelProvider()])
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "stop", "name": "Stop", "args": {}}]),
            AIMessage(content="不应执行"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "停止", context=self.context, prepared=prepared,
            cancel_check=lambda: cancelled["value"], call_callback=calls.append)
        self.assertEqual("cancelled", result.status)
        self.assertEqual(1, result.usage["modelCalls"])
        self.assertEqual(1, calls[-1]["attempt"])
        self.assertEqual("completed", calls[-1]["status"])
        self.assertEqual(1, len(calls[-1]["responses"]))

    def test_direct_module_does_not_import_legacy_agent_or_codeintel(self):
        code = (
            "import sys\n"
            "import business_code_agent.query.agent.direct\n"
            "blocked = [name for name in sys.modules if name.startswith("
            "'business_code_agent.query.langchain_agent') or name.startswith("
            "'business_code_agent.codeintel')]\n"
            "print(','.join(blocked))\n"
            "raise SystemExit(bool(blocked))\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()

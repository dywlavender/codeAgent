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
from business_code_agent.query.agent.session import (
    DirectSessionStore,
    ReadLedger,
    compact_messages,
    retire_changed_sources,
)
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
        recovered = ToolMessage(
            content=json.dumps({**read, "cached": True, "recovered": True}),
            name="Read", tool_call_id="recovered", status="success")
        self.assertIn("已废弃", retire_changed_sources(
            [HumanMessage("旧问题"), recovered], self.prepared.files)[1].content)

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

    def test_citation_resolves_unambiguous_truncated_paths(self):
        def read(path, start, end):
            return ToolMessage(content=json.dumps({
                "path": path, "lineStart": start, "lineEnd": end, "content": "源码"}),
                name="Read", tool_call_id=f"read-{path}-{start}")

        actual = ("repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/"
                  "portfolio/loanaccount/domain/LoanStatus.java")
        processor = ("repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/"
                     "portfolio/loanaccount/domain/transactionprocessor/"
                     "AbstractLoanRepaymentScheduleTransactionProcessor.java")
        messages = [read(actual, 20, 40),
                    read(processor, 80, 95),
                    read("repos/a/domain/Foo.java", 1, 5),
                    read("repos/b/domain/Foo.java", 1, 5)]
        result = verify_answer(
            "见 `fineract-core/.../loanaccount/domain/LoanStatus.java:24-37`；"
            "另见 `.../domain/LoanStatus.java:24`；"
            "文件名 `Abstract...Processor.java:85`；"
            "歧义 `.../domain/Foo.java:1`。", messages)
        references = result["references"]
        self.assertEqual(actual, references[0]["matchedPath"])
        self.assertTrue(references[0]["confirmed"])
        self.assertEqual(actual, references[1]["matchedPath"])
        self.assertTrue(references[1]["confirmed"])
        self.assertEqual(processor, references[2]["matchedPath"])
        self.assertTrue(references[2]["confirmed"])
        self.assertIsNone(references[3]["matchedPath"])
        self.assertFalse(references[3]["fileRead"])

    def test_citation_parser_respects_extension_boundaries(self):
        result = verify_answer(
            "`repos/channel/Channel.class` `repos/channel/Channel.json` "
            "`repos/channel/Channel.tsx` `repos/channel/Channel.cs` "
            "`repos/channel/Channel.c` `repos/channel/Channel.js` "
            "`repos/channel/Channel.ts`", [])
        self.assertEqual([
            "repos/channel/Channel.json",
            "repos/channel/Channel.tsx",
            "repos/channel/Channel.cs",
            "repos/channel/Channel.c",
            "repos/channel/Channel.js",
            "repos/channel/Channel.ts",
        ], [reference["path"] for reference in result["references"]])

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

    def test_repeated_identical_read_is_cached_without_reopening_content(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "read-1", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
            }]),
            AIMessage(content="", tool_calls=[{
                "id": "read-2", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
            }]),
            AIMessage(content="重复读取已阻止，入口为 repos/channel/Channel.java:3。"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["cachedReads"])
        reads = [event["payload"] for event in result.events
                 if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                 and event["payload"]["status"] == "completed"]
        self.assertEqual(2, len(reads))
        self.assertIn("applyWithdraw", reads[0]["output"])
        cached = json.loads(reads[1]["output"])
        self.assertTrue(cached["cached"])
        self.assertIn("不要重复读取", cached["note"])
        self.assertNotIn("applyWithdraw", reads[1]["output"])
        self.assertTrue(result.usage["verification"]["references"][0]["confirmed"])

    def test_overlapping_read_is_cached_when_the_range_is_fully_covered(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "read-1", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 4},
            }]),
            AIMessage(content="", tool_calls=[{
                "id": "read-2", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 2, "limit": 2},
            }]),
            AIMessage(content="重复区间已缓存，入口为 repos/channel/Channel.java:2。"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["cachedReads"])
        second = [event["payload"] for event in result.events
                  if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                  and event["payload"]["status"] == "completed"][-1]
        self.assertTrue(json.loads(second["output"])["cached"])

    def test_new_read_range_is_not_blocked_by_the_cache(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "read-1", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 2},
            }]),
            AIMessage(content="", tool_calls=[{
                "id": "read-2", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 3, "limit": 2},
            }]),
            AIMessage(content="新读取区间包含 repos/channel/Channel.java:3。"),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model, environment={}).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(0, result.usage["cachedReads"])
        second = [event["payload"] for event in result.events
                  if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                  and event["payload"]["status"] == "completed"][-1]
        self.assertIn("applyWithdraw", second["output"])

    def test_compaction_retires_reads_with_path_and_range_metadata(self):
        messages = [HumanMessage("问题")]
        for index in range(4):
            messages.append(AIMessage(content="", tool_calls=[{
                "id": f"read-{index}", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
            }]))
            messages.append(ToolMessage(
                content=json.dumps({
                    "path": "repos/channel/Channel.java", "lineStart": 1, "lineEnd": 3,
                    "totalLines": 4, "content": "x" * 1000,
                }), name="Read", tool_call_id=f"read-{index}", status="success"))
        compacted = compact_messages(messages, 500)
        retired = [message for message in compacted
                   if isinstance(message, ToolMessage)
                   and str(message.content).startswith("[历史读取已移除]")]
        self.assertTrue(retired)
        self.assertIn("Channel.java:1-3", retired[0].content)
        self.assertLess(len(retired[0].content), 200)

    def test_cached_read_recovers_compacted_evidence_then_reports_a_gap(self):
        output = json.dumps({
            "path": "repos/channel/Channel.java", "lineStart": 1, "lineEnd": 3,
            "totalLines": 4, "content": "1: class Main {\n2: void applyWithdraw() {}\n3: }",
        })
        ledger = ReadLedger()
        ledger.record(output)
        self.assertIn("repos/channel/Channel.java: 1-3", ledger.index())
        visible = ToolMessage(content=output, name="Read", tool_call_id="read", status="success")
        ledger.observe([visible])
        visible_cached = ledger.cached({"path": "repos/channel/Channel.java", "offset": 1, "limit": 3})
        self.assertTrue(visible_cached["contentInContext"])
        self.assertNotIn("content", visible_cached)

        ledger.observe([])
        recovered = ledger.cached({"path": "repos/channel/Channel.java", "offset": 1, "limit": 3})
        self.assertTrue(recovered["recovered"])
        self.assertIn("applyWithdraw", recovered["content"])

        ledger.observe([])
        recovered_again = ledger.cached({"path": "repos/channel/Channel.java", "offset": 1, "limit": 3})
        self.assertTrue(recovered_again["recovered"])

        ledger.observe([])
        gap = ledger.cached({"path": "repos/channel/Channel.java", "offset": 1, "limit": 3})
        self.assertTrue(gap["evidenceGap"])
        self.assertNotIn("content", gap)
        self.assertIn("无法复核", gap["note"])

    def test_recovery_is_capped_across_files(self):
        ledger = ReadLedger()
        results = []
        for index in range(9):
            path = f"repos/channel/File{index}.java"
            ledger.record(json.dumps({"path": path, "lineStart": 1, "lineEnd": 1,
                                      "content": "1: x"}))
            ledger.observe([])
            results.append(ledger.cached({"path": path, "offset": 1, "limit": 1}))
        self.assertEqual(8, sum(1 for result in results if result.get("recovered")))
        self.assertTrue(results[-1]["evidenceGap"])

    def test_runtime_recovers_compacted_read_evidence(self):
        marker = "RETENTION_RULE"
        for name in ("Important", "Big0", "Big1", "Big2"):
            lines = ["x" * 250 for _ in range(14)]
            if name == "Important":
                lines[2] = f"{marker} " + "y" * 200
            (self.root / "sources" / "channel" / f"{name}.java").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
        responses = [AIMessage(content="", tool_calls=[{
            "id": f"read-{name}", "name": "Read",
            "args": {"path": f"repos/channel/{name}.java"},
        }]) for name in ("Important", "Big0", "Big1", "Big2")]
        responses.append(AIMessage(content="", tool_calls=[{
            "id": "re-read", "name": "Read",
            "args": {"path": "repos/channel/Important.java"},
        }]))
        responses.append(AIMessage(content="额度恢复规则见 repos/channel/Important.java:3。"))
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CONTEXT_CHARS": "14000"})
        result = runtime.ask("读取多个文件后回答", context=self.context,
                             prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["recoveredReads"])
        reads = [json.loads(event["payload"]["output"]) for event in result.events
                 if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                 and event["payload"]["status"] == "completed"]
        self.assertEqual(5, len(reads))
        self.assertFalse(reads[0].get("recovered"))
        self.assertTrue(reads[-1]["recovered"])
        self.assertIn(marker, reads[-1]["content"])
        self.assertTrue(result.usage["verification"]["references"][-1]["confirmed"])

    def test_investigation_limit_returns_incomplete_instead_of_completed(self):
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "read", "name": "Read",
                "args": {"path": "repos/channel/Channel.java"},
            }]),
            AIMessage(content="不应执行"),
        ])
        runtime = DirectAgentRuntime(profile=CORE, model=model, environment={}, max_calls=1)
        result = runtime.ask("读取", context=self.context, prepared=self.prepared)
        self.assertEqual("incomplete", result.status)
        self.assertEqual("budget", result.usage["stopReason"])
        self.assertEqual(1, result.usage["modelCalls"])
        self.assertEqual("incomplete", result.events[-1]["payload"]["phase"])
        self.assertIn("调查未完成", result.answer)

    def test_time_limit_marks_the_inflight_call_incomplete(self):
        calls = []
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=[AIMessage(content="慢答案。")], sleep=1),
            environment={}, max_seconds=0.2)
        result = runtime.ask("等待", context=self.context, prepared=self.prepared,
                             call_callback=calls.append)
        self.assertEqual("incomplete", result.status)
        self.assertEqual("budget", result.usage["stopReason"])
        self.assertEqual("incomplete", calls[-1]["status"])

    def test_stalled_repeated_reads_trigger_a_final_answer(self):
        repeated = {
            "id": "read", "name": "Read",
            "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
        }
        responses = [AIMessage(content="", tool_calls=[{**repeated, "id": f"read-{index}"}])
                     for index in range(11)]
        responses.append(AIMessage(content="提款入口见 repos/channel/Channel.java:2。部分答案。"))
        calls = []
        result = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                    environment={}).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared,
            call_callback=calls.append)
        self.assertEqual("incomplete", result.status)
        self.assertEqual("no_progress", result.usage["stopReason"])
        self.assertEqual(12, result.usage["modelCalls"])
        self.assertEqual(10, result.usage["stalledReads"])
        self.assertIn("连续无新增证据", result.answer)
        self.assertIn("部分答案", result.answer)
        self.assertTrue(result.usage["verification"]["references"][0]["confirmed"])
        self.assertEqual([], calls[-1]["tools"])
        self.assertEqual("answer", calls[-1]["phase"])
        self.assertIn("已读证据索引", calls[-1]["messages"][0]["content"])
        self.assertIn("repos/channel/Channel.java", calls[-1]["messages"][0]["content"])
        self.assertEqual("human", calls[-1]["messages"][-1]["role"])
        self.assertIn("立即输出最终答案", calls[-1]["messages"][-1]["content"])

    def test_stall_warning_does_not_force_an_incomplete_answer(self):
        repeated = {
            "id": "read", "name": "Read",
            "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
        }
        responses = [AIMessage(content="", tool_calls=[{**repeated, "id": f"read-{index}"}])
                     for index in range(7)]
        responses.append(AIMessage(content="已确认入口为 repos/channel/Channel.java:2。"))
        calls = []
        result = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                    environment={}).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared,
            call_callback=calls.append)
        self.assertEqual("completed", result.status)
        self.assertIsNone(result.usage["stopReason"])
        self.assertIn("调查提醒", calls[-1]["messages"][0]["content"])
        self.assertNotEqual([], calls[-1]["tools"])

    def test_budget_finalization_reserves_a_tool_free_answer_call(self):
        calls = []
        responses = [
            AIMessage(content="", tool_calls=[{
                "id": "read", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
            }]),
            AIMessage(content="预算内答案：repos/channel/Channel.java:2。"),
        ]
        result = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                    environment={}, max_calls=3).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared,
            call_callback=calls.append)
        self.assertEqual("incomplete", result.status)
        self.assertEqual("budget", result.usage["stopReason"])
        self.assertEqual(2, result.usage["modelCalls"])
        self.assertIn("预算用尽", result.answer)
        self.assertEqual([], calls[-1]["tools"])
        self.assertTrue(result.usage["verification"]["references"][0]["confirmed"])

    def test_empty_finalization_falls_back_to_the_incomplete_notice(self):
        responses = [
            AIMessage(content="", tool_calls=[{
                "id": "read", "name": "Read",
                "args": {"path": "repos/channel/Channel.java", "offset": 1, "limit": 3},
            }]),
            AIMessage(content=""),
        ]
        result = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                    environment={}, max_calls=3).ask(
            "入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("incomplete", result.status)
        self.assertEqual("budget", result.usage["stopReason"])
        self.assertIn("调查未完成", result.answer)

    def test_context_budget_accounts_for_the_system_prompt(self):
        for index in range(4):
            (self.root / "sources" / "channel" / f"Big{index}.java").write_text(
                ("x" * 200 + "\n") * 6, encoding="utf-8")
        responses = [
            AIMessage(content="", tool_calls=[{
                "id": f"read-{index}", "name": "Read",
                "args": {"path": f"repos/channel/Big{index}.java"},
            }])
            for index in range(4)
        ] + [AIMessage(content="已读取足够文件，给出结论。")]
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CONTEXT_CHARS": "5000"})
        result = runtime.ask("读取多个文件后回答", context=self.context,
                             prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(5, result.usage["modelCalls"])

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

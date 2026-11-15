from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.agent.direct import (
    DirectAgentRuntime,
    CHECKPOINT_SYSTEM,
    _checkpoint_due,
    _checkpoint_progress,
    _parse_checkpoint,
    verify_answer,
)
from business_code_agent.query.agent.session import (
    CHECKPOINT_RUNTIME_ONLY,
    DirectSessionStore,
    ReadLedger,
    compact_checkpoint_messages,
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

    def test_closure_accepts_explicit_connection_read_in_normal_loop(self):
        entry = "repos/channel/Channel.java"
        route = "repos/decision/Route.java"
        (self.root / "sources/decision/Route.java").write_text(
            'routes.put("approve", approvalHandler);\n', encoding="utf-8")
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "Read", "id": "entry", "args": {"path": entry}}]),
            AIMessage(content="", tool_calls=[
                {"name": "RecordFindings", "id": "needed", "args": {"relations": [{
                    "objective_id": "Q0", "relationship": "命令路由", "basis": f"{entry}:3"}]}},
                {"name": "Read", "id": "route", "args": {"path": route}},
            ]),
            AIMessage(content="", tool_calls=[{"name": "RecordFindings", "id": "answer", "args": {
                "relations": [{"objective_id": "Q0", "relation_id": "R1", "connection": [f"{route}:1"]}],
                "findings": [{"objective_id": "Q0", "claim": "approve 映射到 approvalHandler。",
                              "references": [f"{route}:1"]}],
                "submit": True,
            }}]),
            AIMessage(content="", tool_calls=[{"name": "RecordFindings", "id": "followup", "args": {
                "findings": [{"objective_id": "Q0", "claim": "直接沿用上一轮的证据。",
                              "references": [f"{route}:1"]}],
                "submit": True,
            }}]),
        ])
        runtime = DirectAgentRuntime(profile=CORE, model=model,
                                    environment={"BUSINESS_CODE_AGENT_ANSWER_CLOSURE_ENABLED": "true"})
        first = runtime.ask("命令如何路由？", context=self.context, prepared=self.prepared)
        closure = first.usage["answerClosure"]
        self.assertEqual(3, first.usage["modelCalls"])
        self.assertEqual("supported", closure["registeredCoverage"])
        self.assertEqual("unknown", closure["questionCoverage"])
        self.assertFalse(closure["semanticProof"])
        self.assertIn(f"命令路由的连接依据 `{route}:1`", first.answer)
        self.assertEqual(["route"], list(closure["evidenceLedger"]["evidence"][1]["read_call_ids"]))
        followup = runtime.ask("命令如何路由？", context=self.context, prepared=self.prepared,
                               session_id=first.runtime_session_id)
        self.assertEqual("unknown", followup.usage["answerClosure"]["registeredCoverage"])
        self.assertNotIn("直接沿用上一轮的证据", followup.answer)

    def test_closure_cannot_cite_read_from_same_tool_batch(self):
        path = "repos/channel/Channel.java"
        runtime = DirectAgentRuntime(profile=CORE, environment={"BUSINESS_CODE_AGENT_ANSWER_CLOSURE_ENABLED": "true"},
                                    model=ToolModel(responses=[AIMessage(content="", tool_calls=[
            {"name": "Read", "id": "not-delivered", "args": {"path": path}},
            {"name": "RecordFindings", "id": "finish", "args": {"submit": True, "findings": [{
                "objective_id": "Q0", "claim": "尚未收到文件却确认实现。", "references": [f"{path}:3"]}]}},
        ])]))
        result = runtime.ask("源码实现是什么？", context=self.context, prepared=self.prepared)
        self.assertEqual("unknown", result.usage["answerClosure"]["registeredCoverage"])
        self.assertEqual([], result.usage["answerClosure"]["evidenceLedger"]["evidence"])
        self.assertNotIn("尚未收到文件却确认实现", result.answer)

    def test_closure_allows_one_revision_when_valid_and_unsupported_steps_are_combined(self):
        path = "repos/channel/Channel.java"
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "Read", "id": "read", "args": {"path": path}}]),
            AIMessage(content="", tool_calls=[{"name": "RecordFindings", "id": "draft", "args": {
                "submit": True, "findings": [{"objective_id": "Q0", "claim": "入口和下游已经核实。",
                                               "references": [f"{path}:3", f"{path}:100"]}],
            }}]),
            AIMessage(content="", tool_calls=[{"name": "RecordFindings", "id": "revised", "args": {
                "submit": True, "findings": [{"objective_id": "Q0", "claim": "入口方法是 applyWithdraw。",
                                               "references": [f"{path}:3"], "gap": "下游尚未核实。"}],
            }}]),
        ])
        result = DirectAgentRuntime(profile=CORE, model=model,
                                    environment={"BUSINESS_CODE_AGENT_ANSWER_CLOSURE_ENABLED": "true"}).ask(
            "入口与下游怎么连接？", context=self.context, prepared=self.prepared)
        self.assertEqual(3, result.usage["modelCalls"])
        self.assertTrue(result.usage["answerClosure"]["revisionRequested"])
        draft = next(event["payload"] for event in result.events if event["eventType"] == "tool"
                     and event["payload"]["id"] == "draft" and event["payload"]["status"] == "completed")
        self.assertEqual("revise", json.loads(draft["output"])["submission"])
        self.assertIn("入口方法是 applyWithdraw", result.answer)
        self.assertIn("下游尚未核实", result.answer)
        self.assertNotIn("入口和下游已经核实", result.answer)

    def test_cancellation_prevents_model_and_tool_execution(self):
        result = DirectAgentRuntime(profile=CORE, model=ExplodingModel(responses=[]),
                                    environment={}).ask(
            "停止", context=self.context, prepared=self.prepared, cancel_check=lambda: True)
        self.assertEqual("cancelled", result.status)
        self.assertEqual(0, result.usage["modelCalls"])
        self.assertEqual("已停止。", result.answer)
        self.assertEqual("cancelled", result.events[-1]["payload"]["phase"])

    def test_investigation_guidance_and_prompt_version_are_delivered(self):
        runtime = self.runtime([])
        prompt = runtime._system_prompt(self.context, self.prepared)
        for text in (
            "调查策略：", "并列出问题明确要求覆盖的仓库、系统或职责环节",
            "Grep 的 files 模式", "缩小 path 用 matches",
            "Grep 命中（含 context）只是定位线索，不能作为引用依据",
            "不因问题提到某系统就禁止调查其他授权系统",
            "也不因一个仓库已能解释部分链路就跳过问题要求的其他系统",
            "问题要求的关键对象和环节均已有源码依据时直接回答",
            "工具选择可根据问题灵活调整",
            "业务知识用于理解术语和业务约定",
        ):
            self.assertIn(text, prompt)
        guidance = dict(self.prepared.instructions)["file"]
        self.assertIn("Grep(output_mode='files')", guidance)
        self.assertIn("Grep(output_mode='matches', context=2)", guidance)
        self.assertIn("Grep 仅支持 matches 与 files 两种 output_mode", guidance)
        self.assertIn("不支持 {a,b} 花括号", guidance)
        self.assertIn("不要求每道题依次调用所有工具", guidance)
        self.assertNotIn("业务补充知识位于", prompt)

    def test_prompt_version_change_invalidates_prompt_scoped_session(self):
        runtime = self.runtime([])
        scope = runtime._session_scope(self.context, self.prepared)
        self.assertEqual(2, scope["promptVersion"])
        self.assertEqual("core-direct-2", runtime.execution_settings()["runtimeRevision"])
        self.assertFalse(scope["checkpoint"])
        self.assertNotIn("checkpointTimeoutSeconds", scope)
        store = DirectSessionStore(self.root / "prompt-sessions")
        store.save("DA-prompt", scope, [HumanMessage("旧策略问题")])
        self.assertEqual(1, len(store.load("DA-prompt", scope)))
        enabled = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=[]),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"})
        checkpoint_scope = enabled._session_scope(self.context, self.prepared)
        self.assertTrue(checkpoint_scope["checkpoint"])
        self.assertEqual(2, checkpoint_scope["checkpointVersion"])
        self.assertEqual("45.0", checkpoint_scope["checkpointTimeoutSeconds"])
        self.assertEqual("1024", checkpoint_scope["checkpointOutputTokens"])
        self.assertEqual("20000", checkpoint_scope["checkpointContextChars"])
        self.assertEqual("core-direct-2-checkpoint-2",
                         enabled.execution_settings()["runtimeRevision"])
        self.assertNotEqual(scope, checkpoint_scope)
        with patch("business_code_agent.query.agent.direct.PROMPT_VERSION", 1):
            old_scope = runtime._session_scope(self.context, self.prepared)
            old_revision = runtime.execution_settings()["runtimeRevision"]
        self.assertEqual(1, old_scope["promptVersion"])
        self.assertEqual("core-direct-1", old_revision)
        self.assertNotEqual(scope, old_scope)
        self.assertEqual([], store.load("DA-prompt", old_scope))

    def test_checkpoint_trigger_uses_half_of_the_normal_budget(self):
        self.assertFalse(_checkpoint_due(6, None, 1, 0))
        self.assertTrue(_checkpoint_due(6, None, 2, 0))
        self.assertFalse(_checkpoint_due(40, 300, 5, 89))
        self.assertTrue(_checkpoint_due(40, 300, 5, 90))
        self.assertFalse(_checkpoint_due(None, None, 9, 119))
        self.assertTrue(_checkpoint_due(None, None, 10, 0))

    def test_checkpoint_has_a_short_output_and_time_budget(self):
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=[]), environment={}, max_seconds=300)
        self.assertEqual((45.0, 1024), runtime.checkpoint_limits(90))
        self.assertEqual(1.0, runtime.checkpoint_limits(260)[0])
        overridden = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=[]),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_TIMEOUT": "12",
                         "BUSINESS_CODE_AGENT_CHECKPOINT_OUTPUT_TOKENS": "512"},
            max_seconds=300)
        self.assertEqual((12.0, 512), overridden.checkpoint_limits(90))

    def test_checkpoint_system_prompt_stays_short(self):
        self.assertLess(len(CHECKPOINT_SYSTEM), 1200)
        self.assertIn("只输出一个 JSON", CHECKPOINT_SYSTEM)
        self.assertIn("resolvable", CHECKPOINT_SYSTEM)

    def test_checkpoint_parser_handles_code_fences_and_optional_gaps_stay_out_of_progress(self):
        parsed = _parse_checkpoint("```json\n" + json.dumps({
            "draftAnswer": "草稿",
            "aspects": [{"id": "A1", "question": "Q", "required": True,
                         "status": "confirmed", "claim": "C", "evidence": ["A.java:1"]}],
            "gaps": [{"aspectId": "A1", "question": "OPTIONAL_SECRET",
                      "importance": "optional", "resolvable": True,
                      "reason": "R", "nextAction": "N"}],
        }, ensure_ascii=False) + "\n```")
        self.assertIsNotNone(parsed)
        self.assertEqual("草稿", parsed["draftAnswer"])
        self.assertEqual("optional", parsed["gaps"][0]["importance"])
        progress = _checkpoint_progress(parsed)
        self.assertIn("已确认：", progress)
        self.assertIn("1 个可选缺口", progress)
        self.assertNotIn("OPTIONAL_SECRET", progress)

    def test_checkpoint_requires_linked_gaps_and_promotes_required_missing_aspects(self):
        missing = {
            "draftAnswer": "命令路由未确认。",
            "aspects": [{"id": "A1", "question": "命令路由如何进入审批 Handler",
                         "required": True, "status": "missing", "claim": "尚未确认"}],
            "gaps": [{"aspectId": "A1", "question": "命令路由",
                      "importance": "optional", "resolvable": True,
                      "reason": "缺少 Handler 证据", "nextAction": "Read Handler"}],
        }
        parsed = _parse_checkpoint(json.dumps(missing, ensure_ascii=False))
        self.assertIsNotNone(parsed)
        self.assertEqual("required", parsed["gaps"][0]["importance"])
        self.assertTrue(parsed["gaps"][0]["importanceAdjusted"])
        self.assertIn("关键缺口", _checkpoint_progress(parsed))

        unlinked = {**missing, "gaps": [{**missing["gaps"][0], "aspectId": "A9"}]}
        self.assertIsNone(_parse_checkpoint(json.dumps(unlinked, ensure_ascii=False)))
        no_gap = {**missing, "gaps": []}
        self.assertIsNone(_parse_checkpoint(json.dumps(no_gap, ensure_ascii=False)))

    def test_checkpoint_marks_unresolvable_gaps_as_answer_limitations(self):
        parsed = _parse_checkpoint(json.dumps({
            "draftAnswer": "产品配置不在授权资料中。",
            "aspects": [{"id": "A1", "question": "产品实际策略配置",
                         "required": True, "status": "missing", "claim": "无法确认"}],
            "gaps": [{"aspectId": "A1", "question": "产品策略配置",
                      "importance": "required", "resolvable": False,
                      "reason": "授权仓库中没有产品创建代码", "nextAction": "无"}],
        }, ensure_ascii=False))
        self.assertIsNotNone(parsed)
        progress = _checkpoint_progress(parsed)
        self.assertIn("无法继续核实", progress)
        self.assertNotIn("下一步只补查这些", progress)

    def test_checkpoint_rejects_invalid_structures_without_guidance(self):
        for value in (
            {"aspects": {}, "gaps": []},
            {"aspects": [], "gaps": []},
            {"aspects": [{"id": "A1", "question": "Q", "required": "yes",
                          "status": "missing", "claim": "C"}], "gaps": []},
            {"aspects": [{"id": "A1", "question": "Q", "required": True,
                          "status": "unknown", "claim": "C"}], "gaps": []},
            {"aspects": [{"id": "A1", "question": "Q", "required": False,
                          "status": "confirmed", "claim": "C", "evidence": []}], "gaps": []},
        ):
            self.assertIsNone(_parse_checkpoint(json.dumps(value, ensure_ascii=False)), value)

    def test_checkpoint_is_disabled_by_default(self):
        responses = [
            AIMessage(content="", tool_calls=[{"id": "read", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        result = self.runtime(responses, max_calls=8).ask(
            "提款入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(2, result.usage["modelCalls"])
        self.assertEqual(0, result.usage["checkpointCalls"])
        self.assertIsNone(result.usage["checkpoint"])

    def test_checkpoint_verifies_evidence_and_injects_progress_at_half_budget(self):
        checkpoint = {
            "draftAnswer": "提款入口在渠道，调用 applyWithdraw。",
            "aspects": [
                {"id": "A1", "question": "渠道入口", "required": True,
                 "status": "confirmed", "claim": "Channel.applyWithdraw 是入口",
                 "evidence": ["repos/channel/Channel.java:1-4"]},
                {"id": "A2", "question": "决策系统后续处理", "required": True,
                 "status": "partial", "claim": "尚未确认决策仓库的调用", "evidence": []},
            ],
            "gaps": [
                {"aspectId": "A2", "question": "决策仓库是否被调用",
                 "importance": "required", "resolvable": True,
                 "reason": "影响调用链完整性", "nextAction": "Read Decision.java"},
            ],
        }
        responses = [
            AIMessage(content="", tool_calls=[{"id": "r1", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="", tool_calls=[{"id": "r2", "name": "Read",
                                               "args": {"path": "repos/decision/Decision.java"}}]),
            AIMessage(content=json.dumps(checkpoint, ensure_ascii=False)),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3；决策仓库未读到调用。"),
        ]
        entries = []
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"},
            max_calls=6)
        result = runtime.ask("提款入口在哪里？", context=self.context,
                             prepared=self.prepared, call_callback=entries.append)
        self.assertEqual("completed", result.status)
        self.assertEqual(4, result.usage["modelCalls"])
        self.assertEqual(1, result.usage["checkpointCalls"])
        summary = result.usage["checkpoint"]
        self.assertEqual(("completed", 1, 1, 1, 1),
                         (summary["status"], summary["confirmedCount"],
                          summary["partialCount"], summary["gapCount"],
                          summary["requiredGapCount"]))
        self.assertEqual(0, summary["requiredUnresolvableCount"])
        self.assertEqual(0, summary["adjustedGapCount"])
        self.assertEqual([1, 2, 3], [entry["attempt"] for entry in entries
                                     if entry.get("status") == "completed"][:3])
        checkpoint_entry = [entry for entry in entries
                            if entry.get("phase") == "checkpoint"
                            and entry.get("status") == "completed"][-1]
        self.assertEqual([], checkpoint_entry["tools"])
        self.assertEqual(1, checkpoint_entry["checkpoint"]["confirmedCount"])
        final_entry = next(entry for entry in entries if entry.get("attempt") == 4
                           and entry.get("status") == "completed")
        humans = [message["content"] for message in final_entry["messages"]
                  if message.get("role") == "human"]
        self.assertTrue(any("[调查进展检查点]" in content for content in humans))
        self.assertTrue(any("已确认：" in content for content in humans))
        checkpoint_events = [event["payload"] for event in result.events
                             if event["eventType"] == "checkpoint"]
        self.assertEqual("completed", checkpoint_events[-1]["status"])
        self.assertEqual(1, checkpoint_events[-1]["confirmedCount"])
        store = DirectSessionStore(self.project.data_root / "query-sessions" / "core")
        saved = store.load(result.runtime_session_id,
                           runtime._session_scope(self.context, self.prepared))
        self.assertFalse(any("[调查进展检查点]" in str(message.content)
                             for message in saved))

    def test_checkpoint_downgrades_unverified_claims(self):
        unverified = {
            "draftAnswer": "渠道入口已确认。",
            "aspects": [{"id": "A1", "question": "渠道入口", "required": True,
                         "status": "confirmed", "claim": "Channel.applyWithdraw 是入口",
                         "evidence": ["repos/channel/Channel.java:99-120"]}],
            "gaps": [],
        }
        responses = [
            AIMessage(content="", tool_calls=[{"id": "r1", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="", tool_calls=[{"id": "r2", "name": "Read",
                                               "args": {"path": "repos/decision/Decision.java"}}]),
            AIMessage(content=json.dumps(unverified, ensure_ascii=False)),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        entries = []
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"},
            max_calls=6)
        result = runtime.ask("提款入口在哪里？", context=self.context,
                             prepared=self.prepared, call_callback=entries.append)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["checkpointCalls"])
        summary = result.usage["checkpoint"]
        self.assertEqual(("completed", 0, 1),
                         (summary["status"], summary["confirmedCount"],
                          summary["partialCount"]))
        final_entry = next(entry for entry in entries if entry.get("attempt") == 4
                           and entry.get("status") == "completed")
        humans = [message["content"] for message in final_entry["messages"]
                  if message.get("role") == "human"]
        self.assertTrue(any("[调查进展检查点]" in content for content in humans))
        self.assertTrue(any("部分确认" in content for content in humans))
        self.assertFalse(any("已确认：" in content for content in humans))

    def test_checkpoint_parse_failure_falls_back_to_normal_investigation(self):
        responses = [
            AIMessage(content="", tool_calls=[{"id": "r1", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="", tool_calls=[{"id": "r2", "name": "Read",
                                               "args": {"path": "repos/decision/Decision.java"}}]),
            AIMessage(content="不是 JSON"),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        entries = []
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"},
            max_calls=6)
        result = runtime.ask("提款入口在哪里？", context=self.context,
                             prepared=self.prepared, call_callback=entries.append)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["checkpointCalls"])
        summary = result.usage["checkpoint"]
        self.assertEqual(("failed", "parse"), (summary["status"], summary["reason"]))
        final_entry = next(entry for entry in entries if entry.get("attempt") == 4
                           and entry.get("status") == "completed")
        humans = [message["content"] for message in final_entry["messages"]
                  if message.get("role") == "human"]
        self.assertFalse(any("[调查进展检查点]" in content for content in humans))

    def test_checkpoint_structure_error_falls_back_without_guidance(self):
        responses = [
            AIMessage(content="", tool_calls=[{"id": "r1", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="", tool_calls=[{"id": "r2", "name": "Read",
                                               "args": {"path": "repos/decision/Decision.java"}}]),
            AIMessage(content=json.dumps({"draftAnswer": "草稿", "aspects": {}, "gaps": []})),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        entries = []
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"},
            max_calls=6)
        result = runtime.ask("提款入口在哪里？", context=self.context,
                             prepared=self.prepared, call_callback=entries.append)
        self.assertEqual("completed", result.status)
        self.assertEqual(1, result.usage["checkpointCalls"])
        summary = result.usage["checkpoint"]
        self.assertEqual(("failed", "structure"), (summary["status"], summary["reason"]))
        final_entry = next(entry for entry in entries if entry.get("attempt") == 4
                           and entry.get("status") == "completed")
        humans = [message["content"] for message in final_entry["messages"]
                  if message.get("role") == "human"]
        self.assertFalse(any("[调查进展检查点]" in content for content in humans))

    def test_checkpoint_invalid_budget_config_falls_back(self):
        responses = [
            AIMessage(content="", tool_calls=[{"id": "r1", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="", tool_calls=[{"id": "r2", "name": "Read",
                                               "args": {"path": "repos/decision/Decision.java"}}]),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true",
                         "BUSINESS_CODE_AGENT_CHECKPOINT_TIMEOUT": "not-a-number"},
            max_calls=6)
        result = runtime.ask("提款入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual("completed", result.status)
        self.assertEqual(3, result.usage["modelCalls"])
        self.assertEqual(0, result.usage["checkpointCalls"])
        self.assertEqual(("failed", "config"),
                         (result.usage["checkpoint"]["status"],
                          result.usage["checkpoint"]["reason"]))

    def test_checkpoint_is_skipped_when_finalization_is_due(self):
        responses = [
            AIMessage(content="", tool_calls=[{"id": "read", "name": "Read",
                                               "args": {"path": "repos/channel/Channel.java"}}]),
            AIMessage(content="提款入口为 repos/channel/Channel.java:3。"),
        ]
        runtime = DirectAgentRuntime(
            profile=CORE, model=ToolModel(responses=responses),
            environment={"BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED": "true"},
            max_calls=3)
        result = runtime.ask("提款入口在哪里？", context=self.context, prepared=self.prepared)
        self.assertEqual(2, result.usage["modelCalls"])
        self.assertEqual(0, result.usage["checkpointCalls"])

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

    def test_checkpoint_guidance_is_runtime_only_and_not_saved(self):
        question = "原问题" + "x" * 200
        messages = [
            HumanMessage(question),
            HumanMessage("[调查进展检查点] 只在本轮有效。",
                         additional_kwargs={"runtimeOnly": CHECKPOINT_RUNTIME_ONLY}),
        ]
        bounded = compact_messages(messages, 50)
        self.assertTrue(any(question in str(message.content) for message in bounded))
        store = DirectSessionStore(self.root / "runtime-sessions")
        store.save("DA-runtime", {"project": "A"}, messages)
        self.assertEqual([question], [str(message.content) for message in
                                      store.load("DA-runtime", {"project": "A"})])

    def test_checkpoint_context_replaces_tool_bodies_with_read_index(self):
        read = ToolMessage(content=json.dumps({
            "path": "repos/channel/Channel.java", "lineStart": 1, "lineEnd": 4000,
            "content": "x" * 50000,
        }), name="Read", tool_call_id="read", status="success")
        messages = [HumanMessage("问题"), AIMessage(content="", tool_calls=[{
            "id": "read", "name": "Read", "args": {"path": "repos/channel/Channel.java"},
        }]), read]
        bounded = compact_checkpoint_messages(messages, 20000)
        text = "\n".join(str(message.content) for message in bounded)
        self.assertIn("[检查点省略读取原文] repos/channel/Channel.java:1-4000", text)
        self.assertNotIn("x" * 1000, text)
        self.assertLess(len(text), 2000)

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

    def test_token_compaction_records_evidence_hints_and_requires_original_for_citation(self):
        important = "repos/channel/Important.java"
        recent = "repos/channel/Recent.java"
        for name, count in (("Important", 6), ("Middle", 20), ("Recent", 6)):
            lines = ["x" * 240] * count
            lines[0] = f"{name} content " + lines[0][20:]
            if name == "Important":
                lines[:2] = ["void approve() { router.dispatch(APPROVE); }",
                             "routes.put(APPROVE, new ApprovalHandler());"]
            (self.root / "sources/channel" / f"{name}.java").write_text("\n".join(lines), encoding="utf-8")
        question = "审批命令怎样路由？"
        claim = "APPROVE 命令经路由表交给 ApprovalHandler"
        environment = {"BUSINESS_CODE_AGENT_CONTEXT_TOKENS": "4500", "BUSINESS_CODE_AGENT_OUTPUT_TOKENS": "512",
                       "BUSINESS_CODE_AGENT_FINAL_OUTPUT_TOKENS": "512"}
        for recover in (False, True):
            with self.subTest(recover=recover):
                reads = [AIMessage(content="", tool_calls=[{"name": "Read", "id": name,
                         "args": {"path": f"repos/channel/{name}.java"}}])
                         for name in ("Important", "Middle", "Recent")]
                summary = AIMessage(content=json.dumps({"confirmedFindings": [
                    {"claim": claim, "evidence": [f"{important}:1-2"]},
                    {"claim": "未见原文也声称验证", "evidence": ["repos/channel/Unseen.java:1"]},
                ], "pendingQuestions": ["命令如何到达审批处理器？"], "nextFocus": "沿命令注册点调查"}, ensure_ascii=False),
                    usage_metadata={"input_tokens": 500, "output_tokens": 80, "total_tokens": 580})
                response = AIMessage(content=f"{claim}，见 {important}:1-2。")
                resumed = [AIMessage(content="", tool_calls=[{"name": "Read", "id": "again",
                            "args": {"path": important, "limit": 2}}])] if recover else []
                model = ToolModel(responses=[*reads, summary, *resumed, response])
                runtime = DirectAgentRuntime(profile=CORE, model=model, environment=environment)
                calls = []
                result = runtime.ask(question, context=self.context, prepared=self.prepared, call_callback=calls.append)
                manager = result.usage["contextManager"]
                self.assertEqual("completed", result.status)
                self.assertEqual(1, manager["summaryCalls"])
                self.assertEqual(500, result.usage["inputTokens"])
                self.assertEqual(int(recover), result.usage["recoveredReads"])
                self.assertEqual(recover, result.usage["verification"]["references"][0]["confirmed"])
                self.assertEqual([{"claim": claim, "evidence": [f"{important}:1-2"]}],
                                 manager["memory"]["confirmedFindings"])
                compacted = [call for call in calls if call["phase"] == "compaction" and call["status"] == "completed"]
                self.assertEqual(1, len(compacted))
                self.assertEqual([], compacted[0]["tools"])
                self.assertIn("routes.put(APPROVE, new ApprovalHandler())", compacted[0]["messages"][1]["content"])
                self.assertTrue(all(call["contextBudget"]["estimatedInputTokens"] +
                                    call["contextBudget"]["reservedOutputTokens"] <= 4500 for call in calls))
                final_request = calls[-1]["messages"]
                self.assertIn(question, [m["content"] for m in final_request if m["role"] == "human"])
                self.assertIn("命令如何到达审批处理器", final_request[0]["content"])
                self.assertTrue(any(recent in str(m["content"]) for m in final_request if m["role"] == "tool"))

        store = DirectSessionStore(self.project.data_root / "query-sessions" / "core")
        scope = runtime._session_scope(self.context, self.prepared)
        history, memory = store.load_context(result.runtime_session_id, scope)
        self.assertEqual(manager["memory"], memory)
        self.assertTrue(history)
        self.assertEqual(([], {}), store.load_context(result.runtime_session_id, {**scope, "contextTokens": 4600}))

        followup = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=[AIMessage(content="继续调查。")]),
                                     environment=environment)
        (self.root / "sources/channel/Important.java").write_text("changed source\n", encoding="utf-8")
        followup_calls = []
        next_turn = followup.ask("继续核对入口", context=self.context, prepared=self.prepared,
                                 session_id=result.runtime_session_id, call_callback=followup_calls.append)
        self.assertEqual([], next_turn.usage["contextManager"]["memory"]["confirmedFindings"])
        self.assertNotIn(claim, followup_calls[0]["messages"][0]["content"])

    def test_token_context_trims_older_search_before_discarding_read_evidence(self):
        repository = self.root / "sources/channel"
        (repository / "Search.java").write_text(("match " + "x" * 430 + "\n") * 18, encoding="utf-8")
        (repository / "Recent.java").write_text(("y" * 200 + "\n") * 20, encoding="utf-8")
        source = "repos/channel/Channel.java"
        responses = [AIMessage(content="", tool_calls=[{
            "id": key, "name": name, "args": arguments,
        }]) for key, name, arguments in (
            ("source", "Read", {"path": source}),
            ("search", "Grep", {"pattern": "match", "path": "repos/channel/Search.java"}),
            ("recent", "Read", {"path": "repos/channel/Recent.java"}),
        )]
        responses.append(AIMessage(content=f"提款入口见 {source}:3。"))
        runtime = DirectAgentRuntime(profile=CORE, model=ToolModel(responses=responses),
                                     environment={"BUSINESS_CODE_AGENT_CONTEXT_TOKENS": "12000",
                                                  "BUSINESS_CODE_AGENT_CONTEXT_CHARS": "13500"})
        calls = []
        result = runtime.ask("核对提款入口", context=self.context, prepared=self.prepared, call_callback=calls.append)
        self.assertEqual(4, result.usage["modelCalls"])
        self.assertTrue(result.usage["references"][0]["confirmed"])
        compaction = next(e["payload"] for e in result.events if e["eventType"] == "compaction")
        self.assertEqual(("pruned", 1, 0), (compaction["status"], compaction["trimmedSearchResults"],
                                           compaction["removedMessages"]))
        final_tools = {m["name"]: m["content"] for m in calls[-1]["messages"] if m["role"] == "tool"}
        self.assertIn("搜索结果已省略", final_tools["Grep"])
        full_search = next(e["payload"]["output"] for e in result.events if e["eventType"] == "tool"
                           and e["payload"]["id"] == "search" and e["payload"]["status"] == "completed")
        self.assertEqual(18, len(json.loads(full_search)["matches"]))

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
            environment={"BUSINESS_CODE_AGENT_CONTEXT_CHARS": "5600"})
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

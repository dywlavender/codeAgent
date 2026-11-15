from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.langchain_agent.context import compact
from business_code_agent.query.langchain_agent.context import retire_changed_sources


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class AgentTest(unittest.TestCase):
    def test_online_runtime_is_pi_even_with_legacy_configuration(self):
        import os
        from unittest.mock import patch
        from business_code_agent.query.service import _default_runtime

        with patch.dict(
            os.environ,
            {
                "BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                "BUSINESS_CODE_AGENT_PROFILE": "core-v1",
                "BUSINESS_CODE_AGENT_RETRIEVAL": "hybrid",
            },
        ):
            runtime = _default_runtime(index_root="/tmp/index", project_id="A")
        self.assertEqual("PI", runtime.runtime_name)
        self.assertEqual("pi-v1", runtime.profile.id)

    def workspace(self, root):
        root = Path(root)
        (root / "repos/a").mkdir(parents=True)
        (root / "repos/b").mkdir(parents=True)
        (root / "CLAUDE.md").write_text("只读源码，引用行号")
        (root / "repos/a/Ledger.java").write_text(
            "class Ledger {\n int total = 1;\n}\n"
        )
        (root / "repos/b/Other.java").write_text("class Other {}")
        return root

    def test_real_agent_tool_loop_and_persistent_followup(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            model = ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "Read",
                                "args": {"path": "repos/a/Ledger.java"},
                                "id": "read-1",
                            }
                        ],
                    ),
                    AIMessage(content="total 初始为 1，见 repos/a/Ledger.java:2"),
                    AIMessage(content="追问答复"),
                ]
            )
            runtime = LangChainRuntime(model=model, answer_mode="plain")
            first = runtime.ask("total 是多少", workspace=root, repositories={"a"})
            self.assertIn("初始为 1", first.answer)
            completed = [
                e
                for e in first.events
                if e["eventType"] == "tool" and e["payload"]["status"] == "completed"
            ]
            self.assertTrue(any("2:  int total = 1;" in e["payload"]["output"] for e in completed if e["payload"]["name"] == "Read"))
            second = runtime.ask(
                "继续",
                workspace=root,
                repositories={"a"},
                session_id=first.runtime_session_id,
            )
            self.assertEqual(first.runtime_session_id, second.runtime_session_id)
            saved = json.loads(
                next((root / ".langchain-sessions").glob("*.json")).read_text()
            )
            self.assertEqual(2, sum(m["type"] == "human" for m in saved["messages"]))

    def test_tools_do_not_escape_repository_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            files = SourceFiles(root, {"a"})
            self.assertEqual(["repos/a/Ledger.java"], files.glob("**/*.java")["paths"])
            with self.assertRaises(ValueError):
                files.read("repos/b/Other.java")
            with self.assertRaises(ValueError):
                files.read("/etc/hosts")

    def test_read_has_hard_line_cap_and_continuation(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            value = SourceFiles(root).read("repos/a/Ledger.java", 1, 1)
            self.assertTrue(value["truncated"])
            self.assertEqual(2, value["nextLine"])

    def test_explicit_evaluation_limit_and_manual_cancel(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            model = ToolModel(
                responses=[AIMessage(content="按显式评测限制整理现有证据")]
            )
            r = LangChainRuntime(model=model, max_calls=1, answer_mode="plain").ask(
                "调查", workspace=root
            )
            self.assertIsNone(r.usage["stopReason"])
            self.assertEqual(1, r.usage["modelCalls"])
            self.assertTrue(r.usage["separateFinalization"])
            cancelled = LangChainRuntime(model=model).ask(
                "停止", workspace=root, cancel_check=lambda: True
            )
            self.assertEqual("cancelled", cancelled.status)
            self.assertEqual(0, cancelled.usage["modelCalls"])

    def test_production_has_no_call_or_elapsed_time_limit_even_with_legacy_environment(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        seconds = [0]

        class LongInvestigation(ToolModel):
            async def _agenerate(self, *args, **kwargs):
                seconds[0] += 301
                return await super()._agenerate(*args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            responses = [AIMessage(content="", tool_calls=[{
                "id": f"search-{i}", "name": "Glob", "args": {"pattern": f"**/candidate-{i}.java"},
            }]) for i in range(15)] + [AIMessage(content="调查完成")]
            runtime = LangChainRuntime(model=LongInvestigation(responses=responses), environment={
                "BUSINESS_CODE_AGENT_MAX_CALLS": "2", "BUSINESS_CODE_AGENT_TIMEOUT": "1",
            })
            with patch("business_code_agent.query.langchain_agent.runtime.time", SimpleNamespace(monotonic=lambda: seconds[0])):
                result = runtime.ask("定位源码", workspace=root)
            self.assertEqual(16, result.usage["modelCalls"])
            self.assertIsNone(result.usage["stopReason"])
            self.assertEqual("调查完成", result.answer.split("\n")[0])
            trace = json.loads(Path(result.usage["tracePath"]).read_text())
            self.assertIsNone(trace["settings"]["maxCalls"])
            self.assertIsNone(trace["settings"]["maxSeconds"])
            self.assertEqual(16, len(trace["calls"]))

    def test_explicit_evaluation_time_limit_remains_opt_in(self):
        import asyncio

        class SlowModel(ToolModel):
            async def _agenerate(self, *args, **kwargs):
                await asyncio.sleep(2)
                return await super()._agenerate(*args, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            result = LangChainRuntime(model=SlowModel(responses=[AIMessage(content="late")]), max_seconds=0.01).ask(
                "调查", workspace=self.workspace(d))
            self.assertEqual("budget", result.usage["stopReason"])
            self.assertIn("显式指定", result.answer)

    def test_context_retires_old_tool_bodies_without_breaking_pairs(self):
        values = [HumanMessage(content="问题")]
        for i in range(5):
            values.extend(
                [
                    AIMessage(
                        content="",
                        tool_calls=[{"id": str(i), "name": "Read", "args": {}}],
                    ),
                    ToolMessage(content="原文" * 100, tool_call_id=str(i)),
                ]
            )
        result = compact(values, 800)
        self.assertIn("已废弃", result[2].content)
        self.assertEqual(
            [m.tool_call_id for m in values if isinstance(m, ToolMessage)],
            [m.tool_call_id for m in result if isinstance(m, ToolMessage)],
        )

    def test_changed_source_is_retired_before_followup(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            message = ToolMessage(
                name="Read",
                tool_call_id="r",
                content=json.dumps({"path": "repos/a/Ledger.java", "sourceMtimeNs": 0}),
            )
            result = retire_changed_sources([message], SourceFiles(root))
            self.assertIn("已废弃", result[0].content)

    def test_cancel_interrupts_an_inflight_model_call(self):
        import asyncio, time
        from langchain_core.outputs import ChatResult, ChatGeneration

        class SlowModel(ToolModel):
            async def _agenerate(self, *args, **kwargs):
                await asyncio.sleep(5)
                return ChatResult(
                    generations=[ChatGeneration(message=AIMessage(content="late"))]
                )

        with tempfile.TemporaryDirectory() as d:
            root = self.workspace(d)
            started = time.monotonic()
            result = LangChainRuntime(model=SlowModel(responses=[])).ask(
                "问题",
                workspace=root,
                cancel_check=lambda: time.monotonic() - started > 0.1,
            )
            self.assertEqual("cancelled", result.status)
            self.assertLess(time.monotonic() - started, 1)
            trace = json.loads(Path(result.usage["tracePath"]).read_text())
            self.assertEqual("cancelled", trace["status"])
            self.assertEqual("cancelled", trace["calls"][0]["status"])

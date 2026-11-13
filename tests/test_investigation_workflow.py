"""Regress actual trace failures: navigation, tool semantics and handoff."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.langchain_agent.investigation import InvestigationState
from business_code_agent.query.navigation import build_navigation, read_navigation, render_navigation
from business_code_agent.query.workspace import WorkspaceManager


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        if not self.responses:
            raise AssertionError("发起了意外的额外模型请求")
        result = super()._generate(*args, **kwargs)
        self.responses.pop(0)
        self.i = 0
        return result


class InvestigationWorkflowTest(unittest.TestCase):
    def workspace(self, directory):
        root = Path(directory)
        (root / "repos/api").mkdir(parents=True)
        (root / "repos/core").mkdir(parents=True)
        (root / "repos/web").mkdir(parents=True)
        (root / "knowledge/business-context/nested").mkdir(parents=True)
        (root / "knowledge/business-context/terminology.md").write_text("业务术语")
        (root / "knowledge/business-context/nested/notes.md").write_text("业务例外")
        (root / "repos/api/Gateway.java").write_text('class Gateway { Object fetch() { return remote.get(); } }')
        (root / "repos/core/Handler.java").write_text('class Handler { Object get() { return Map.of("phase", "WAITING"); } }')
        (root / "repos/web/page.ts").write_text('const phase = response.phase;')
        (root / "CLAUDE.md").write_text("依据原文回答")
        return root

    def navigation(self):
        return build_navigation({
            "systems": [{"id": "business", "name": "业务系统"}, {"id": "admin", "name": "管理系统"}],
            "applications": [
                {"id": "api", "repositoryId": "api", "systemId": "business", "type": "BACKEND"},
                {"id": "core", "repositoryId": "core", "systemId": "business", "type": "BACKEND"},
                {"id": "web", "repositoryId": "web", "systemId": "admin", "type": "FRONTEND"},
            ],
        }, {name: "repos/" + name for name in ("api", "core", "web")}, "A", "演示工程")

    def pair(self, name, args, output, call_id):
        return [
            AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}]),
            ToolMessage(content=json.dumps(output), name=name, tool_call_id=call_id),
        ]

    def test_recursive_glob_direct_children_and_directory_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            files = SourceFiles(self.workspace(directory))
            self.assertEqual([
                "knowledge/business-context/nested/notes.md",
                "knowledge/business-context/terminology.md",
            ], files.glob("knowledge/business-context/**/*", kind="files")["paths"])
            self.assertEqual(["repos/api", "repos/core", "repos/web"], files.glob("repos/*", kind="directories")["paths"])
            self.assertEqual(["repos/api/Gateway.java"], files.glob("repos/api/**/*.java")["paths"])

    def test_grep_distinguishes_enum_case_and_whole_word(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            (root / "repos/api/status.ts").write_text('"PROCESSING"\n"PROCESSING_DONE"\n"Data processing"')
            (root / "repos/api-archive").mkdir()
            (root / "repos/api-archive/status.ts").write_text('"PROCESSING"')
            files = SourceFiles(root)
            result = files.grep("PROCESSING", path="repos/api", whole_word=True)
            self.assertEqual([1], [m["line"] for m in result["matches"]])
            result = files.grep("PROCESSING", path="repos/api", whole_word=True, case_sensitive=False)
            self.assertEqual([1, 3], [m["line"] for m in result["matches"]])

    def test_workspace_navigation_uses_config_and_filters_repository_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("api", "web"):
                (root / name).mkdir()
                (root / name / "sample.java").write_text("class Example {}")
            config = self.navigation()
            config["project"] = {"id": "A"}
            config["repositories"] = [{"id": name, "localPath": name} for name in ("api", "web")]
            for app in config["applications"]:
                app.pop("mount")
            config_file = root / "project.json"
            config_file.write_text(json.dumps(config))
            workspace = WorkspaceManager(project_config=config_file).ensure()
            files = SourceFiles(workspace.path, {"api"})
            nav = read_navigation(workspace.path, files.roots, "A")
            self.assertEqual(["api"], [a["id"] for a in nav["applications"]])
            self.assertNotIn("repos/web", render_navigation(nav))
            self.assertIn("未登记前端应用", render_navigation(nav))
            with self.assertRaises(ValueError):
                read_navigation(workspace.path, files.roots, "B")

    def test_connections_need_delivered_endpoints_and_backend_does_not_prove_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            files = SourceFiles(self.workspace(directory))
            state = InvestigationState("A", files, self.navigation())
            refs = [
                {"path": "repos/api/Gateway.java", "start": 1, "end": 1, "symbol": "fetch"},
                {"path": "repos/core/Handler.java", "start": 1, "end": 1, "symbol": "get"},
            ]
            task = {"id": "return", "objective": "核对返回对象连接", "kind": "relation", "status": "supported", "refs": refs,
                    "paths": ["repos/api/", "repos/core/"]}
            messages = [HumanMessage(content="调查返回对象")]
            messages += self.pair("Read", {"path": refs[0]["path"]}, files.read(refs[0]["path"]), "read-a")
            messages += self.pair("Investigation", {}, {"tasks": [task]}, "plan-1")
            state.observe(messages)
            self.assertEqual("open", state.tasks["return"].status)
            messages += self.pair("Read", {"path": refs[1]["path"]}, files.read(refs[1]["path"]), "read-b")
            messages += self.pair("Investigation", {}, {"tasks": [{"id": "return", "status": "supported", "refs": refs}]}, "plan-2")
            state.observe(messages)
            self.assertEqual("supported", state.tasks["return"].status)
            self.assertEqual(["repos/api", "repos/core"], state.tasks["return"].paths)
            self.assertEqual("relation", state.tasks["return"].kind)
            ui = {"id": "ui", "objective": "核对页面实际使用", "kind": "consumer", "status": "supported", "refs": [refs[0]]}
            messages += self.pair("Investigation", {}, {"tasks": [ui]}, "plan-ui")
            state.observe(messages)
            self.assertEqual("open", state.tasks["ui"].status)
            self.assertIn("尚无已登记前端", state.tasks["ui"].note)
            messages += self.pair("Read", {"path": "repos/web/page.ts"}, files.read("repos/web/page.ts"), "read-ui")
            messages += self.pair("Investigation", {}, {"tasks": [{"id": "ui", "status": "supported", "refs": [
                {"path": "repos/web/page.ts", "start": 1, "end": 1, "symbol": "response.phase"},
            ]}]}, "plan-ui-source")
            state.observe(messages)
            self.assertEqual("supported", state.tasks["ui"].status)
            self.assertFalse(state.snapshot()["semanticProof"])

    def plan_call(self):
        return AIMessage(content="", tool_calls=[
            {"id": "plan", "name": "Investigation", "args": {"tasks": [{
                "id": "link", "objective": "核对返回对象连接", "kind": "relation",
                "paths": ["repos/api/Gateway.java", "repos/core/Handler.java"],
                "next_read": {"path": "repos/api/Gateway.java", "offset": 1, "limit": 20},
            }]}},
            {"id": "read-core", "name": "Read", "args": {"path": "repos/core/Handler.java"}},
        ])

    def test_investigation_can_record_more_than_eight_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            state = InvestigationState("A", SourceFiles(self.workspace(directory)), self.navigation())
            tasks = [{"id": f"task-{i}", "objective": f"核对业务对象 {i}"} for i in range(12)]
            state.observe([HumanMessage(content="完整调查"), *self.pair("Investigation", {}, {"tasks": tasks}, "plan")])
            self.assertEqual(12, len(state.tasks))
            self.assertFalse(state.issues)

    def test_program_reads_known_gap_then_finalizes_when_no_followup_remains(self):
        for workflow in ("coordinated", "tracked"):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as directory:
                root = self.workspace(directory)
                model = ToolModel(responses=[
                    self.plan_call(),
                    AIMessage(content="未经核查的提前结论"),
                    AIMessage(content="已读取两个端点，连接仍待确认"),
                ])
                result = LangChainRuntime(model=model, environment={}, workflow_mode=workflow).ask(
                    "调查返回关系", workspace=root)
                self.assertEqual(3, result.usage["modelCalls"])
                self.assertEqual(0, result.usage["investigation"]["reopenedDrafts"])
                self.assertNotIn("提前结论", result.answer)
                self.assertIn("核对返回对象连接", result.answer)
                trace = json.loads(Path(result.usage["tracePath"]).read_text())
                self.assertIsNone(trace["settings"]["maxCalls"])
                self.assertEqual(["investigation", "investigation", "answer"], [call["phase"] for call in trace["calls"]])
                self.assertEqual([], trace["calls"][-1]["tools"])
                self.assertIn("未经核查的提前结论", trace["calls"][1]["responses"][0]["content"])
                self.assertTrue(any(m.get("name") == "Read" and "Gateway.java" in m["content"]
                                    for m in trace["calls"][1]["messages"]))
                self.assertIn("return remote.get()", trace["calls"][-1]["messages"][-1]["content"])

    def test_duplicate_search_is_skipped_and_duplicate_only_batch_finalizes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            args = {"pattern": "NOT_A_REAL_FIELD", "path": "repos/web"}
            model = ToolModel(responses=[
                AIMessage(content="", tool_calls=[{"id": "first", "name": "Grep", "args": args}]),
                AIMessage(content="", tool_calls=[
                    {"id": "repeat-with-default", "name": "Grep", "args": {**args, "limit": 60}},
                    {"id": "new-scope", "name": "Glob", "args": {"pattern": "**/MissingPage.ts"}},
                ]),
                AIMessage(content="", tool_calls=[{"id": "repeat", "name": "Grep", "args": args}]),
                AIMessage(content="本轮尚未定位页面消费代码"),
            ])
            with patch.object(SourceFiles, "grep", return_value={"matches": [], "truncated": False}) as grep:
                result = LangChainRuntime(model=model, environment={}).ask("页面实际读取哪个字段", workspace=root)
            grep.assert_called_once()
            trace = json.loads(Path(result.usage["tracePath"]).read_text())
            self.assertEqual(["investigation", "investigation", "investigation", "answer"],
                             [call["phase"] for call in trace["calls"]])
            self.assertEqual([], trace["calls"][-1]["tools"])
            self.assertEqual({"repeat-with-default", "repeat"}, {
                e["payload"]["id"] for e in result.events if e["eventType"] == "tool"
                and e["payload"]["status"] == "skipped"
            })
            self.assertTrue(any(e["eventType"] == "tool" and e["payload"]["id"] == "new-scope"
                                and e["payload"]["status"] == "completed" for e in result.events))
            self.assertIn("no_matches", trace["calls"][-1]["messages"][-1]["content"])

    def test_budget_handoff_contains_unfinished_tasks_and_search_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.workspace(directory)
            model = ToolModel(responses=[
                AIMessage(content="", tool_calls=[
                    {"id": "plan", "name": "Investigation", "args": {"tasks": [{"id": "ui", "objective": "确认本题页面是否消费返回字段", "kind": "consumer"}]}},
                    {"id": "search", "name": "Grep", "args": {"pattern": "NOT_A_REAL_FIELD", "path": "repos/web"}},
                ]),
                AIMessage(content="页面使用关系尚未确认"),
            ])
            result = LangChainRuntime(model=model, max_calls=2, workflow_mode="tracked").ask("页面能否使用", workspace=root)
            self.assertEqual(2, result.usage["modelCalls"])
            self.assertIn("本轮仍未确认", result.answer)
            trace = json.loads(Path(result.usage["tracePath"]).read_text())
            handoff = trace["calls"][-1]
            self.assertEqual("answer", handoff["phase"])
            self.assertEqual([], handoff["tools"])
            text = handoff["messages"][-1]["content"]
            self.assertIn("确认本题页面是否消费返回字段", text)
            self.assertIn("no_matches", text)
            self.assertNotIn("源码已证明不存在", text)


if __name__ == "__main__":
    unittest.main()

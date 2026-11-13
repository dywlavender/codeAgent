"""Production combines available materials; experiments retain isolated sources."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from business_code_agent.evaluation.ast_service import AstService
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.runtime import RuntimeResult
from business_code_agent.query.service import QueryService
from business_code_agent.query.workspace import workspace_sources
from business_code_agent.storage.schema import connect
from tests.test_langchain_agent import ToolModel


class RecordingRuntime:
    runtime_name = "LOCAL_MATERIAL_TEST"

    def __init__(self):
        self.calls = []

    def ask(self, question, *, workspace, session_id=None, **kwargs):
        workspace = Path(workspace)
        self.calls.append({"workspace": workspace, "session": session_id,
                           "sources": dict(workspace_sources(workspace))})
        return RuntimeResult("本地资料检查完成", session_id or f"session-{len(self.calls)}")


class QueryMaterialsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        documents = {
            "knowledge/business-context/rules.md": "渠道处理中是业务展示术语。\n",
            "knowledge/generated-code-map/project-index.md": "代码地图入口：core/src/Main.java\n",
            "requirements/goal.md": "需求目标：展示状态原因。\n",
            "repo/src/Main.java": "class Main { int phase = 1; }\n",
        }
        for name, body in documents.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        self.config = self.root / "project.config.json"
        self.config.write_text(json.dumps({
            "project": {"id": "material-test", "name": "资料组合"},
            "knowledge": {"businessContextRoot": "knowledge/business-context"},
            "repositories": [{"id": "core", "localPath": "repo"}],
        }), encoding="utf-8")
        self.ast_root = self.root / "prepared-ast"
        self.db_path = self.root / "db.sqlite"
        self.db = connect(str(self.db_path))
        self.addCleanup(self.db.close)

    def prepare_ast(self, version="ast-v1"):
        """Seed an already prepared version, without invoking a parser."""
        ast = AstService(project_config=self.config, data_root=self.ast_root)
        version_root = self.ast_root / "versions" / version
        documents = version_root / "documents"
        documents.mkdir(parents=True)
        (documents / "project-index.md").write_text("AST：core 的 Main 类型。\n", encoding="utf-8")
        (version_root / "metadata.json").write_text(json.dumps({
            "projectId": "material-test", "versionId": version,
            "sourceStamp": ast._current_source_stamp(), "generatedAt": "2026-11-02T12:00:00Z",
        }), encoding="utf-8")
        (self.ast_root / "index.json").write_text(json.dumps({
            "status": "available", "currentVersionId": version,
        }), encoding="utf-8")

    def service(self, runtime=None):
        return QueryService(self.db, db_path=str(self.db_path), project_config=self.config,
                            ast_data_root=self.ast_root, runtime=runtime or RecordingRuntime())

    def test_same_agent_turn_reads_all_material_types_and_archives_ast_version(self):
        self.prepare_ast()
        paths = ["repos/core/src/Main.java", "requirements/goal.md", "knowledge/business-context/rules.md",
                 "knowledge/generated-code-map/project-index.md", "knowledge/ast/project-index.md"]
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "Read", "id": f"read-{i}", "args": {"path": path}}
                                               for i, path in enumerate(paths)]),
            AIMessage(content="已结合当前源码、需求和业务约定回答；AST 用于定位。"),
        ])
        runtime = LangChainRuntime(model=model, workflow_mode="basic", answer_mode="plain", project_id="material-test")
        service = self.service(runtime)
        with patch.object(AstService, "generate", side_effect=AssertionError("must remain manual")):
            result = service.query("解释这个项目的术语和实现")
        self.assertEqual("combined", result["mode"])
        self.assertEqual("ast-v1", result["astVersionId"])
        trace = service.get_trace(result["runId"])
        self.assertEqual("ast-v1", trace["run"]["astVersionId"])
        material_labels = {item["label"] for item in trace["run"]["materials"]}
        self.assertTrue({"业务补充知识", "AST 结构资料", "代码仓库 core"}.issubset(material_labels))
        self.assertEqual(2, len(trace["calls"]))
        delivered = [json.loads(e["payload"]["output"]) for e in trace["runtimeEvents"]
                     if e["eventType"] == "tool" and e["payload"].get("status") == "completed"]
        self.assertEqual(set(paths), {item["path"] for item in delivered})
        actual_inputs = [json.loads(message["content"]) for message in trace["calls"][1]["messages"]
                         if message["role"] == "tool"]
        self.assertEqual({item["path"]: item["content"] for item in delivered},
                         {item["path"]: item["content"] for item in actual_inputs})
        catalog = EvidenceCatalog.from_text("material-test", "\n\n".join(
            f"[Read] {item['path']}:{item['lineStart']}-{item['lineEnd']}\n{item['content']}" for item in delivered))
        kinds = {source.path: source.kind for source in catalog.sources}
        self.assertEqual("navigation", kinds["knowledge/ast/project-index.md"])
        self.assertEqual("navigation", kinds["knowledge/generated-code-map/project-index.md"])
        self.assertEqual("business_context", kinds["knowledge/business-context/rules.md"])
        self.assertEqual("code", kinds["repos/core/src/Main.java"])

    def test_missing_ast_keeps_business_and_code_map_and_never_generates(self):
        runtime = RecordingRuntime()
        service = self.service(runtime)
        with patch.object(AstService, "generate", side_effect=AssertionError("must remain manual")):
            result = service.query("没有 AST 也应正常调查")
            summary = service.workspace_summary()
        self.assertEqual("combined", result["mode"])
        self.assertIsNone(result["astVersionId"])
        self.assertIn("业务补充知识", runtime.calls[0]["sources"])
        self.assertIn("自动代码地图", runtime.calls[0]["sources"])
        self.assertNotIn("AST 结构资料", runtime.calls[0]["sources"])
        ast = next(item for item in summary["sources"] if item["kind"] == "ast")
        self.assertEqual("missing", ast["availability"])
        self.assertFalse(ast["authorizationConfigured"])
        self.assertEqual([], list((self.ast_root / "versions").iterdir()))

    def test_stale_ast_is_unmounted_and_returns_to_combined_after_manual_update(self):
        self.prepare_ast()
        runtime = RecordingRuntime()
        service = self.service(runtime)
        first = service.query("第一问")
        same = service.query("继续", conversation_id=first["conversationId"])
        self.assertEqual("session-1", runtime.calls[1]["session"])
        (self.root / "repo/src/Main.java").write_text("class Main { int changed = 2; }\n", encoding="utf-8")
        stale = service.query("源码改了", conversation_id=same["conversationId"])
        self.assertIsNone(stale["astVersionId"])
        self.assertIsNone(runtime.calls[2]["session"])
        self.assertNotIn("AST 结构资料", runtime.calls[2]["sources"])
        self.assertFalse((runtime.calls[2]["workspace"] / "knowledge/ast").exists())
        summary = service.workspace_summary()
        self.assertEqual("stale", summary["workspace"]["astStatus"])
        self.prepare_ast("ast-v2")
        updated = service.query("新结构", conversation_id=stale["conversationId"])
        self.assertEqual("ast-v2", updated["astVersionId"])
        self.assertIsNone(runtime.calls[3]["session"])
        self.assertEqual("ast-v1", service.get_run(first["runId"])["astVersionId"])

    def test_historical_modes_stay_isolated_and_new_followup_uses_combined(self):
        self.prepare_ast()
        runtime = RecordingRuntime()
        service = self.service(runtime)
        sources = {}
        for mode in ("none", "backbone", "ast"):
            row = service.query("显式对照", mode=mode)
            sources[mode] = set(runtime.calls[-1]["sources"])
        self.assertNotIn("业务补充知识", sources["none"])
        self.assertNotIn("自动代码地图", sources["backbone"])
        self.assertNotIn("业务补充知识", sources["ast"])
        current = service.query("从旧记录追问", conversation_id=row["conversationId"])
        self.assertEqual("combined", current["mode"])
        self.assertEqual("ast", service.get_run(row["runId"])["mode"])
        self.assertIsNone(runtime.calls[-1]["session"])
        self.assertIn("业务补充知识", runtime.calls[-1]["sources"])
        self.assertIn("AST 结构资料", runtime.calls[-1]["sources"])

    def test_combined_sources_respect_repository_scope(self):
        self.prepare_ast()
        workspace = self.service()._workspace_for_mode("combined")
        files = SourceFiles(workspace.path, repositories=set())
        self.assertEqual("knowledge/ast/project-index.md", files.read("knowledge/ast/project-index.md")["path"])
        self.assertIn("业务展示术语", files.read("knowledge/business-context/rules.md")["content"])
        with self.assertRaises(ValueError):
            files.read("repos/core/src/Main.java")

    def test_combined_followup_does_not_assign_ast_to_an_old_business_answer(self):
        self.prepare_ast()
        service = self.service()
        old = service.query("旧业务资料问答", mode="backbone")
        combined = service.query("组合资料追问", conversation_id=old["conversationId"])
        self.assertEqual("ast-v1", combined["astVersionId"])
        self.assertIsNone(service.get_run(old["runId"])["astVersionId"])
        self.assertIsNone(service.get_trace(old["runId"])["run"]["astVersionId"])
        self.assertEqual("backbone", service.get_conversation(old["conversationId"])["items"][0]["mode"])


if __name__ == "__main__":
    unittest.main()

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

    def service(self, runtime=None):
        return QueryService(self.db, db_path=str(self.db_path), project_config=self.config,
                            ast_data_root=self.ast_root, runtime=runtime or RecordingRuntime())

    def test_legacy_agent_reads_combined_file_materials(self):
        paths = ["repos/core/src/Main.java", "requirements/goal.md", "knowledge/business-context/rules.md",
                 "knowledge/generated-code-map/project-index.md"]
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "Read", "id": f"read-{i}", "args": {"path": path}}
                                               for i, path in enumerate(paths)]),
            AIMessage(content="已结合当前源码、需求和业务约定回答。"),
        ])
        runtime = LangChainRuntime(model=model, workflow_mode="basic", answer_mode="plain", project_id="material-test")
        service = self.service(runtime)
        with patch.object(AstService, "generate", side_effect=AssertionError("must remain manual")):
            result = service.query("解释这个项目的术语和实现")
        self.assertEqual("combined", result["mode"])
        self.assertIsNone(result["astVersionId"])
        trace = service.get_trace(result["runId"])
        self.assertIsNone(trace["run"]["astVersionId"])
        material_labels = {item["label"] for item in trace["run"]["materials"]}
        self.assertTrue({"业务补充知识", "自动代码地图", "代码仓库 core"}.issubset(material_labels))
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
        self.assertFalse(any(item["kind"] == "ast" for item in summary["sources"]))
        self.assertFalse(self.ast_root.exists())

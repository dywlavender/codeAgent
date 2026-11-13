from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.query.agent.direct import DirectAgentRuntime
from business_code_agent.query.profile import CORE, CORE_BUSINESS
from business_code_agent.query.service import QueryService
from business_code_agent.storage.schema import connect


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class CoreBusinessQueryTest(unittest.TestCase):
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
        self.documents = self.root / "knowledge" / "business-context"
        self.documents.mkdir(parents=True)
        (self.documents / "README.md").write_text(
            "业务补充知识说明，按需读取。\n", encoding="utf-8")
        (self.documents / "cross-system.md").write_text(
            "外部付款成功不等于提款业务最终成功；中台仍需完成借据登记和业务台账收敛。\n",
            encoding="utf-8")
        self.config = self.root / "project.json"
        self.config.write_text(json.dumps({
            "project": {"id": "core-business", "name": "Core Business"},
            "knowledge": {"businessContextRoot": "knowledge/business-context"},
            "repositories": [
                {"id": repository_id, "gitUrl": "unused", "localPath": f"repos/{repository_id}"}
                for repository_id in ("channel", "decision")
            ],
        }), encoding="utf-8")
        self.db_path = str(self.root / "knowledge.db")
        self.db = connect(self.db_path)
        self.addCleanup(self.db.close)

    def service(self, responses, profile=CORE_BUSINESS):
        runtime = DirectAgentRuntime(
            profile=profile, model=ToolModel(responses=responses), environment={})
        return QueryService(self.db, db_path=self.db_path, project_config=self.config,
                            project_id="core-business", runtime=runtime), runtime

    @staticmethod
    def read(path, offset=1, limit=20):
        return {"path": path, "offset": offset, "limit": limit}

    def test_business_profile_reads_context_and_trace_records_it(self):
        responses = [
            AIMessage(content="", tool_calls=[
                {"id": "glob", "name": "Glob",
                 "args": {"pattern": "knowledge/business-context/*"}},
                {"id": "grep", "name": "Grep",
                 "args": {"pattern": "付款成功", "path": "knowledge/business-context"}}]),
            AIMessage(content="", tool_calls=[
                {"id": "doc", "name": "Read",
                 "args": self.read("knowledge/business-context/cross-system.md")},
                {"id": "code", "name": "Read",
                 "args": self.read("repos/channel/Channel.java")}]),
            AIMessage(content="业务补充知识（knowledge/business-context/cross-system.md:1）"
                              "说明外部付款成功不等于提款最终成功；"
                              "源码确认入口为 repos/channel/Channel.java:2。"),
        ]
        service, runtime = self.service(responses)
        events = []
        result = service.query("外部付款成功后提款业务完成了吗？", event_callback=events.append)
        self.assertEqual(("core-business-v1", "completed"),
                         (result["execution"]["profileId"], result["status"]))
        materials = {source["kind"]: source for source in result["execution"]["materials"]}
        self.assertEqual({"source", "requirements", "business_context"}, set(materials))
        self.assertEqual(("available", "knowledge/business-context"),
                         (materials["business_context"]["status"],
                          materials["business_context"]["path"]))
        self.assertTrue(materials["business_context"]["version"].startswith("sha256:"))
        statuses = {tool["capability"]: tool for tool in result["execution"]["tools"]}
        self.assertEqual(("optional", "ready"),
                         (statuses["business_context"]["policy"],
                          statuses["business_context"]["status"]))
        for capability in ("search_code", "contract_trace", "ast", "code_map", "hybrid_search"):
            self.assertEqual(("disabled", "disabled"),
                             (statuses[capability]["policy"], statuses[capability]["status"]))
        self.assertEqual(3, result["usage"]["modelCalls"])
        self.assertFalse(result["usage"]["semanticProof"])
        references = result["usage"]["verification"]["references"]
        self.assertEqual(["business_context", "source"],
                         [reference["kind"] for reference in references])
        self.assertTrue(all(reference["confirmed"] for reference in references))
        read_paths = [event["payload"]["input"]["path"] for event in events
                      if event["eventType"] == "tool"
                      and event["payload"]["name"] == "Read"
                      and event["payload"]["status"] == "completed"]
        self.assertEqual(["knowledge/business-context/cross-system.md",
                          "repos/channel/Channel.java"], read_paths)

        trace = service.get_trace(result["runId"])
        self.assertEqual(3, len(trace["calls"]))
        self.assertEqual("core-business-v1", trace["run"]["execution"]["profileId"])
        self.assertIn("knowledge/business-context/cross-system.md",
                      trace["run"]["usage"]["verification"]["readFiles"])
        self.assertTrue(any(event["eventType"] == "tool"
                            and event["payload"].get("input", {}).get("path")
                            == "knowledge/business-context/cross-system.md"
                            for event in trace["events"]))
        workspace_path = Path(trace["run"]["workspace"])
        self.assertFalse((workspace_path / "knowledge/generated-code-map").exists())
        self.assertFalse((workspace_path / "knowledge/ast").exists())
        self.assertFalse((workspace_path / ".data").exists())
        summary = service.workspace_summary()
        business = next(source for source in summary["sources"]
                        if source["kind"] == "business_context")
        self.assertEqual(("READABLE", True),
                         (business["status"], business["authorizationConfigured"]))
        self.assertEqual("direct", runtime.profile.investigation)

    def test_core_profile_cannot_read_business_documents(self):
        responses = [
            AIMessage(content="", tool_calls=[
                {"id": "doc", "name": "Read",
                 "args": self.read("knowledge/business-context/cross-system.md")},
                {"id": "code", "name": "Read",
                 "args": self.read("repos/channel/Channel.java")}]),
            AIMessage(content="无法读取业务资料；源码入口为 repos/channel/Channel.java:2。"),
        ]
        service, _ = self.service(responses, profile=CORE)
        result = service.query("提款入口在哪里？")
        self.assertEqual("core-v1", result["execution"]["profileId"])
        materials = {source["kind"] for source in result["execution"]["materials"]}
        self.assertNotIn("business_context", materials)
        statuses = {tool["capability"]: tool for tool in result["execution"]["tools"]}
        self.assertEqual("disabled", statuses["business_context"]["status"])
        denied = next(event["payload"] for event in result["events"]
                      if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
                      and event["payload"]["status"] == "error")
        self.assertIn("不在本轮授权资料范围内", denied["output"])
        completed_reads = [event["payload"]["input"]["path"] for event in result["events"]
                           if event["eventType"] == "tool"
                           and event["payload"]["name"] == "Read"
                           and event["payload"]["status"] == "completed"]
        self.assertEqual(["repos/channel/Channel.java"], completed_reads)
        summary = service.workspace_summary()
        self.assertFalse(any(source["kind"] == "business_context"
                             for source in summary["sources"]))

    def test_missing_business_context_continues_source_qa(self):
        shutil.rmtree(self.documents)
        service, _ = self.service([AIMessage(content="业务资料缺失，源码入口为 "
                                                      "repos/channel/Channel.java:1。")])
        result = service.query("提款入口在哪里？")
        self.assertEqual("completed", result["status"])
        materials = {source["kind"]: source for source in result["execution"]["materials"]}
        self.assertEqual("missing", materials["business_context"]["status"])
        self.assertIsNone(materials["business_context"]["version"])
        statuses = {tool["capability"]: tool for tool in result["execution"]["tools"]}
        self.assertEqual(("optional", "missing"),
                         (statuses["business_context"]["policy"],
                          statuses["business_context"]["status"]))
        self.assertEqual(("required", "ready"),
                         (statuses["file_tools"]["policy"], statuses["file_tools"]["status"]))
        trace = service.get_trace(result["runId"])
        system_prompt = trace["calls"][0]["messages"][0]["content"]
        self.assertNotIn("业务补充知识位于", system_prompt)
        summary = service.workspace_summary()
        business = next(source for source in summary["sources"]
                        if source["kind"] == "business_context")
        self.assertEqual("MISSING", business["status"])

    def test_business_document_revision_rebuilds_the_model_session(self):
        service, _ = self.service([AIMessage(content="第一轮"), AIMessage(content="第二轮"),
                                   AIMessage(content="第三轮")])
        first = service.query("第一问")
        original_version = next(
            source["version"] for source in first["execution"]["materials"]
            if source["kind"] == "business_context")
        self.assertEqual(1, service.db.execute(
            "SELECT count(*) FROM query_run WHERE status='completed'").fetchone()[0])
        (self.documents / "cross-system.md").write_text(
            "外部付款成功后仍需完成业务台账收敛，且状态回写可能延迟。\n", encoding="utf-8")
        second = service.query("第二问", conversation_id=first["conversationId"])
        changed_version = next(
            source["version"] for source in second["execution"]["materials"]
            if source["kind"] == "business_context")
        self.assertNotEqual(original_version, changed_version)
        self.assertNotEqual(first["sessionId"], second["sessionId"])
        third = service.query("第三问", conversation_id=first["conversationId"])
        self.assertEqual(second["sessionId"], third["sessionId"])
        self.assertEqual(changed_version, next(
            source["version"] for source in third["execution"]["materials"]
            if source["kind"] == "business_context"))

    def test_business_documents_do_not_expand_repository_scope(self):
        responses = [
            AIMessage(content="", tool_calls=[
                {"id": "doc", "name": "Read",
                 "args": self.read("knowledge/business-context/cross-system.md")},
                {"id": "other", "name": "Read",
                 "args": self.read("repos/decision/Decision.java")},
                {"id": "code", "name": "Read",
                 "args": self.read("repos/channel/Channel.java")}]),
            AIMessage(content="业务资料提到中台登记；本轮只确认 "
                              "repos/channel/Channel.java:2，decision 源码未授权。"),
        ]
        service, _ = self.service(responses)
        result = service.query("外部付款后中台做了什么？",
                               scope={"repositoryIds": ["channel"]})
        self.assertEqual(["channel"], result["execution"]["repositoryIds"])
        denied = next(event["payload"] for event in result["events"]
                      if event["eventType"] == "tool"
                      and event["payload"]["status"] == "error")
        self.assertEqual("Read", denied["name"])
        self.assertIn("decision", denied["input"]["path"])
        self.assertTrue(any(event["eventType"] == "tool"
                            and event["payload"]["status"] == "completed"
                            and event["payload"].get("input", {}).get("path")
                            == "knowledge/business-context/cross-system.md"
                            for event in result["events"]))
        self.assertTrue(result["usage"]["verification"]["references"][0]["confirmed"])


if __name__ == "__main__":
    unittest.main()

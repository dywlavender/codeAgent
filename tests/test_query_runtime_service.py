from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from business_code_agent.query.claude_runtime import ClaudeCodeRuntime
from business_code_agent.query.profile import ToolStatus, resolve_profile
from business_code_agent.query.runtime import RunPreparation, RuntimeResult
from business_code_agent.query.service import QueryBusyError, QueryConfigurationError, QueryService
from business_code_agent.storage.schema import connect


class _FakeRuntime:
    runtime_name = "FAKE_RUNTIME"

    def __init__(self):
        self.calls = []

    def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None, repositories=None):
        self.calls.append({
            "question": question,
            "workspace": workspace,
            "session_id": session_id,
            "repositories": set(repositories) if repositories is not None else None,
        })
        event = {"sequence": 1, "eventType": "tool_use", "payload": {"name": "Read", "path": "CLAUDE.md"}}
        if event_callback:
            event_callback(event)
        return RuntimeResult(
            answer=f"回答：{question}",
            runtime_session_id=session_id or "session-1",
            events=[],
            usage={"input_tokens": 3},
        )


class _PreparedRuntime(_FakeRuntime):
    def __init__(self):
        super().__init__()
        self.profile = resolve_profile({})
        self.tool_statuses = (ToolStatus("file_tools", "required", "ready", ("Read", "Glob", "Grep")),)
        self.settings = {"requestedModel": "fake-model", "agentVersion": 1}
        self.preparations = []

    def prepare(self, *, workspace, materials, project_id=None):
        return RunPreparation(materials, self.tool_statuses)

    def execution_settings(self):
        return dict(self.settings)

    def ask_prepared(self, question, *, preparation, **kwargs):
        self.preparations.append(preparation)
        result = super().ask(question, **kwargs)
        return replace(result, runtime_session_id=kwargs["session_id"] or f"session-{len(self.calls)}")


class QueryRuntimeServiceTest(unittest.TestCase):
    def test_running_conversation_rejects_second_request_until_cancelled(self):
        ready, release = threading.Event(), threading.Event()
        results, errors = [], []
        class WaitingRuntime:
            runtime_name = "WAITING"
            def ask(self, question, *, cancel_check=None, **kwargs):
                ready.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release runtime")
                return RuntimeResult("partial", "session", status="cancelled" if cancel_check() else "completed")
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "db.sqlite")
            db = connect(path)
            def first_request():
                worker_db = connect(path)
                try:
                    results.append(QueryService(worker_db, db_path=path, runtime=WaitingRuntime()).query("first", conversation_id="same"))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    worker_db.close()
            worker = threading.Thread(target=first_request)
            worker.start()
            try:
                self.assertTrue(ready.wait(3))
                runtime = _FakeRuntime()
                service = QueryService(db, db_path=path, runtime=runtime)
                for status in ("running", "cancelling"):
                    with self.assertRaises(QueryBusyError) as raised:
                        service.query("second", conversation_id="same")
                    self.assertEqual(status, service.get_run(raised.exception.run_id)["status"])
                    service.cancel_run(raised.exception.run_id)
                self.assertEqual([], runtime.calls)
                self.assertEqual(1, db.execute("SELECT count(*) FROM query_run").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT count(*) FROM query_message").fetchone()[0])
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual([], errors)
            self.assertEqual("cancelled", results[0]["status"])
            self.assertEqual("completed", service.query("next", conversation_id="same")["status"])
            db.close()

    def test_conversation_pagination_is_not_limited_by_run_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "db.sqlite")
            db = connect(path)
            service = QueryService(db, db_path=path, runtime=_FakeRuntime())
            service.query("old", conversation_id="old")
            service.query("middle", conversation_id="middle")
            for index in range(31):
                service.query(f"question {index}", conversation_id="long")
            cursor = None
            ids = []
            while True:
                page = service.list_conversations(limit=1, cursor=cursor)
                ids.extend(item["conversationId"] for item in page["items"])
                cursor = page["nextCursor"]
                if not cursor:
                    break
            self.assertEqual(["long", "middle", "old"], ids)
            self.assertEqual(3, len(service.list_conversations()["items"]))
            db.close()

    def test_conversation_session_messages_runs_and_events_are_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = root / "repo"
            repository.mkdir()
            (repository / "Example.java").write_text("class Example {}", encoding="utf-8")
            baseline = root / "knowledge" / "baseline"
            baseline.mkdir(parents=True)
            (baseline / "withdraw.md").write_text("提款业务。", encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "service-test", "name": "Service Test"},
                "knowledge": {"baselineRoot": "knowledge/baseline"},
                "repositories": [{"id": "repo", "gitUrl": "unused", "localPath": "repo"}],
            }), encoding="utf-8")
            db_path = root / "knowledge.db"
            db = connect(str(db_path))
            runtime = _FakeRuntime()
            service = QueryService(db, db_path=str(db_path), project_config=config, runtime=runtime)
            first = service.query("发起提款有哪些校验？", history=[{"role": "user", "content": "不应发送"}])
            second = service.query("那银行卡为什么必须存在？", conversation_id=first["conversationId"])

            self.assertEqual("FAKE_RUNTIME", first["runtime"])
            self.assertEqual("session-1", first["sessionId"])
            self.assertEqual(first["conversationId"], second["conversationId"])
            self.assertEqual("session-1", runtime.calls[1]["session_id"])
            self.assertEqual(2, len(runtime.calls))
            self.assertNotIn("不应发送", runtime.calls[0]["question"])
            self.assertTrue(Path(runtime.calls[0]["workspace"]).is_dir())

            rows = db.execute("SELECT role,content FROM query_message ORDER BY created_at,id").fetchall()
            self.assertEqual(["user", "assistant", "user", "assistant"], [row[0] for row in rows])
            self.assertEqual(2, db.execute("SELECT count(*) FROM query_run WHERE status='completed'").fetchone()[0])
            self.assertEqual(2, db.execute("SELECT count(*) FROM query_event").fetchone()[0])
            conversation = db.execute("SELECT runtime,runtime_session_id,workspace_id FROM query_conversation").fetchone()
            self.assertEqual(("FAKE_RUNTIME", "session-1", "service-test"), tuple(conversation))

            detail = service.get_run(second["runId"])
            self.assertEqual(second["answer"], detail["answer"])
            self.assertEqual("tool_use", detail["events"][0]["eventType"])
            self.assertEqual(2, len(service.list_runs()))
            history = service.get_conversation(first["conversationId"])
            self.assertEqual([first["runId"], second["runId"]], [item["runId"] for item in history["items"]])
            self.assertEqual(second["answer"], history["items"][-1]["answer"])
            with self.assertRaises(KeyError):
                service.get_conversation("missing")
            db.close()

    def test_runtime_failure_is_recorded_and_does_not_create_assistant_message(self):
        class BrokenRuntime:
            runtime_name = "BROKEN"

            def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None, repositories=None):
                raise RuntimeError("runtime unavailable")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "db.sqlite"
            db = connect(str(db_path))
            service = QueryService(db, db_path=str(db_path), runtime=BrokenRuntime())
            with self.assertRaises(Exception):
                service.query("q")
            row = db.execute("SELECT status,error FROM query_run").fetchone()
            self.assertEqual("failed", row[0])
            self.assertIn("runtime unavailable", row[1])
            self.assertEqual(["user"], [item[0] for item in db.execute("SELECT role FROM query_message")])
            db.close()

    def test_list_conversations_returns_workspace_dimension(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "dim-test", "name": "Dim Test"},
                "knowledge": {"baselineRoot": "knowledge/baseline"},
                "repositories": [],
            }), encoding="utf-8")
            db_path = root / "db.sqlite"
            db = connect(str(db_path))
            service = QueryService(db, db_path=str(db_path), project_config=config, runtime=_FakeRuntime())
            result = service.query("问题")
            page = service.list_conversations()
            self.assertEqual([result["conversationId"]], [item["conversationId"] for item in page["items"]])
            self.assertEqual("dim-test", page["items"][0]["workspaceId"])
            db.close()

    def test_delete_conversation_removes_records_and_rejects_active_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "db.sqlite")
            db = connect(path)
            service = QueryService(db, db_path=path, runtime=_FakeRuntime())
            target = service.query("要删除的会话")
            keep = service.query("要保留的会话")

            running_id = "RUN-running"
            db.execute(
                "INSERT INTO query_run(id,conversation_id,runtime,question,status,started_at) VALUES (?,?,?,?,?,?)",
                (running_id, target["conversationId"], "FAKE_RUNTIME", "还在跑", "running", "2026-01-01T00:00:00+00:00"),
            )
            with self.assertRaises(QueryBusyError):
                service.delete_conversation(target["conversationId"])
            db.execute("UPDATE query_run SET status='completed' WHERE id=?", (running_id,))

            result = service.delete_conversation(target["conversationId"])
            self.assertTrue(result["deleted"])
            self.assertEqual([keep["conversationId"]], [item["conversationId"] for item in service.list_conversations()["items"]])
            self.assertEqual(0, db.execute("SELECT count(*) FROM query_run WHERE conversation_id=?", (target["conversationId"],)).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM query_message WHERE conversation_id=?", (target["conversationId"],)).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM query_conversation WHERE id=?", (target["conversationId"],)).fetchone()[0])
            with self.assertRaises(KeyError):
                service.delete_conversation(target["conversationId"])
            with self.assertRaises(KeyError):
                service.get_conversation(target["conversationId"])
            db.close()

    @staticmethod
    def _scope_project_config(root: Path) -> Path:
        config = root / "project.json"
        config.write_text(json.dumps({
            "project": {"id": "scope-test", "name": "Scope Test"},
            "knowledge": {"baselineRoot": "knowledge/baseline"},
            "systems": [{"id": "s-channel", "name": "渠道系统"}, {"id": "s-middle", "name": "贷款中台系统"}],
            "repositories": [{"id": "r-h5", "gitUrl": "unused", "localPath": "repos/h5"},
                             {"id": "r-middle", "gitUrl": "unused", "localPath": "repos/middle"}],
            "applications": [
                {"id": "a-h5", "name": "渠道H5", "systemId": "s-channel", "repositoryId": "r-h5",
                 "sourceRoot": ".", "type": "FRONTEND"},
                {"id": "a-middle", "name": "中台", "systemId": "s-middle", "repositoryId": "r-middle",
                 "sourceRoot": ".", "type": "BACKEND"},
            ],
        }), encoding="utf-8")
        return config

    def test_scope_narrows_runtime_persists_and_resets_session_on_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "db.sqlite"
            db = connect(str(db_path))
            runtime = _FakeRuntime()
            service = QueryService(db, db_path=str(db_path), project_config=self._scope_project_config(root), runtime=runtime)
            first = service.query("跨系统怎么调用？", scope={"systemIds": ["s-channel"], "repositoryIds": []})
            self.assertEqual({"r-h5"}, runtime.calls[0]["repositories"])
            self.assertIsNone(runtime.calls[0]["session_id"])
            self.assertEqual({"systemIds": ["s-channel"], "repositoryIds": ["r-h5"]}, first["scope"])

            service.query("继续追问", conversation_id=first["conversationId"], scope={"systemIds": ["s-channel"], "repositoryIds": []})
            self.assertEqual("session-1", runtime.calls[1]["session_id"])

            third = service.query("换成中台", conversation_id=first["conversationId"], scope={"systemIds": ["s-middle"], "repositoryIds": []})
            self.assertIsNone(runtime.calls[2]["session_id"])
            self.assertEqual({"r-middle"}, runtime.calls[2]["repositories"])
            self.assertEqual({"systemIds": ["s-middle"], "repositoryIds": ["r-middle"]}, third["scope"])

            detail = service.get_run(third["runId"])
            self.assertEqual({"systemIds": ["s-middle"], "repositoryIds": ["r-middle"]}, detail["scope"])
            page = service.list_conversations()
            self.assertEqual({"systemIds": ["s-middle"], "repositoryIds": ["r-middle"]}, page["items"][0]["scope"])
            with self.assertRaises(ValueError):
                service.query("未知范围", conversation_id=first["conversationId"], scope={"systemIds": ["s-nope"], "repositoryIds": []})
            db.close()

    def test_claude_command_scope_limits_directories_and_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "repos" / "alpha").mkdir(parents=True)
            (root / "repos" / "beta").mkdir()
            (root / "knowledge" / "baseline").mkdir(parents=True)
            runtime = ClaudeCodeRuntime()

            command = runtime.build_command("问题", workspace=root, repositories={"alpha"})
            prompt = command[command.index("--append-system-prompt") + 1]
            self.assertIn("代码仓库 alpha", prompt)
            self.assertNotIn("代码仓库 beta", prompt)
            self.assertIn("本轮调查已限定范围", prompt)
            directories = command[command.index("--add-dir") + 1: command.index("-p")]
            joined = "\n".join(directories)
            self.assertIn(str((root / "repos" / "alpha").resolve()), joined)
            self.assertNotIn(str((root / "repos" / "beta").resolve()), joined)

            unscoped = runtime.build_command("问题", workspace=root)
            prompt_all = unscoped[unscoped.index("--append-system-prompt") + 1]
            self.assertIn("代码仓库 beta", prompt_all)
            self.assertNotIn("本轮调查已限定范围", prompt_all)


class QueryPreparedServiceTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        repository = self.root / "repo"
        repository.mkdir()
        (repository / "math.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        requirements = self.root / "requirements"
        requirements.mkdir()
        (requirements / "request.md").write_text("返回两数之和。", encoding="utf-8")
        config = self.root / "project.json"
        config.write_text(json.dumps({
            "project": {"id": "prepared-test", "name": "Prepared Test"},
            "knowledge": {"requirementsRoot": "requirements"},
            "repositories": [{"id": "repo", "gitUrl": "unused", "localPath": "repo"}],
        }), encoding="utf-8")
        db_path = self.root / "db.sqlite"
        self.db = connect(str(db_path))
        self.addCleanup(self.db.close)
        self.runtime = _PreparedRuntime()
        self.service = QueryService(self.db, db_path=str(db_path), project_config=config,
                                    project_id="prepared-test", runtime=self.runtime)

    def session_config(self, conversation_id):
        row = self.db.execute("SELECT session_config_json FROM query_conversation WHERE id=?",
                              (conversation_id,)).fetchone()
        return json.loads(row[0])

    def test_profile_change_resets_session_and_keeps_historical_execution(self):
        first = self.service.query("first", mode="none")
        same = self.service.query("same", conversation_id=first["conversationId"], mode="none")
        self.assertEqual(first["sessionId"], self.runtime.calls[-1]["session_id"])
        self.assertEqual(first["sessionId"], same["sessionId"])
        self.assertEqual(self.runtime.tool_statuses, self.runtime.preparations[0].tool_statuses)
        old_config = self.session_config(first["conversationId"])
        self.runtime.profile = resolve_profile({"BUSINESS_CODE_AGENT_WORKFLOW": "tracked"})
        changed = self.service.query("changed", conversation_id=first["conversationId"], mode="none")
        self.assertIsNone(self.runtime.calls[-1]["session_id"])
        self.assertNotEqual(first["sessionId"], changed["sessionId"])
        self.assertEqual("tracked-legacy-v1", self.session_config(first["conversationId"])["profileId"])
        self.assertEqual("coordinated-legacy-v1", old_config["profileId"])
        self.assertEqual(first["execution"], self.service.get_run(first["runId"])["execution"])
        self.assertEqual(first["execution"], self.service.get_trace(first["runId"])["run"]["execution"])

    def test_tool_set_policy_status_and_version_changes_reset_session(self):
        baseline = ToolStatus("search_code", "optional", "ready", ("SearchCode",), "index-1")
        variants = {
            "names": replace(baseline, names=("SearchCode", "SearchSymbols")),
            "policy": replace(baseline, policy="required"),
            "status": replace(baseline, status="unavailable", names=()),
            "version": replace(baseline, version="index-2"),
            "removed": None,
        }
        file_status = self.runtime.tool_statuses[0]
        for label, changed_status in variants.items():
            with self.subTest(change=label):
                self.runtime.tool_statuses = (file_status, baseline)
                first = self.service.query("first", mode="none")
                before = self.session_config(first["conversationId"])
                self.runtime.tool_statuses = (file_status,) + ((changed_status,) if changed_status else ())
                changed = self.service.query("changed", conversation_id=first["conversationId"], mode="none")
                self.assertIsNone(self.runtime.calls[-1]["session_id"])
                self.assertNotEqual(first["sessionId"], changed["sessionId"])
                after = self.session_config(first["conversationId"])
                self.assertEqual(before["profileId"], after["profileId"])
                self.assertNotEqual(before["tools"], after["tools"])
                self.assertEqual(first["execution"], self.service.get_run(first["runId"])["execution"])

    def test_diagnostic_reason_alone_does_not_reset_session(self):
        status = ToolStatus("search_code", "optional", "unavailable", reason="index missing")
        self.runtime.tool_statuses += (status,)
        first = self.service.query("first", mode="none")
        before = self.session_config(first["conversationId"])
        self.runtime.tool_statuses = (self.runtime.tool_statuses[0], replace(status, reason="still missing"))
        second = self.service.query("same", conversation_id=first["conversationId"], mode="none")
        self.assertEqual(first["sessionId"], self.runtime.calls[-1]["session_id"])
        self.assertEqual(before, self.session_config(first["conversationId"]))
        self.assertEqual("still missing", second["execution"]["tools"][-1]["reason"])
        self.assertEqual("index missing", self.service.get_run(first["runId"])["execution"]["tools"][-1]["reason"])

    def test_model_settings_change_resets_session_and_preserves_old_snapshot(self):
        first = self.service.query("first", mode="none")
        self.runtime.settings["requestedModel"] = "another-model"
        second = self.service.query("changed", conversation_id=first["conversationId"], mode="none")
        self.assertIsNone(self.runtime.calls[-1]["session_id"])
        self.assertNotEqual(first["sessionId"], second["sessionId"])
        self.assertEqual("another-model", self.session_config(first["conversationId"])["model"])
        self.assertEqual("fake-model", self.service.get_run(first["runId"])["execution"]["model"])

    def test_material_set_change_resets_session_without_changing_profile_id(self):
        first = self.service.query("first", mode="none")
        self.assertIn("requirements", {item[0] for item in self.session_config(first["conversationId"])["materials"]})
        self.runtime.profile = replace(self.runtime.profile, materials=("source",))
        second = self.service.query("changed", conversation_id=first["conversationId"], mode="none")
        self.assertEqual(first["execution"]["profileId"], second["execution"]["profileId"])
        self.assertIsNone(self.runtime.calls[-1]["session_id"])
        self.assertNotEqual(first["sessionId"], second["sessionId"])
        self.assertEqual({"source"}, {item[0] for item in self.session_config(first["conversationId"])["materials"]})
        self.assertEqual(first["execution"], self.service.get_run(first["runId"])["execution"])

    def test_required_preflight_failure_persists_failed_run_execution_and_trace(self):
        from business_code_agent.query.langchain_agent import LangChainRuntime

        runtime = LangChainRuntime(environment={}, profile=resolve_profile({
            "BUSINESS_CODE_AGENT_RETRIEVAL": "hybrid", "BUSINESS_CODE_AGENT_CONTRACTS": "off",
        }), index_root=self.root / "missing-index", project_id="prepared-test")
        self.service.runtime = runtime
        started = []
        with patch.object(runtime, "chat_model", side_effect=AssertionError("model must not start")) as model, \
             self.assertLogs("business_code_agent.query.service", level="ERROR"), \
             self.assertRaisesRegex(QueryConfigurationError, "search_code"):
            self.service.query("question", mode="none", run_callback=started.append)
        model.assert_not_called()
        self.assertEqual(1, len(started))
        run = self.service.get_run(started[0]["runId"])
        self.assertEqual("failed", run["status"])
        self.assertIsNotNone(run["completedAt"])
        self.assertEqual(started[0]["execution"], run["execution"])
        search = next(tool for tool in run["execution"]["tools"] if tool["capability"] == "search_code")
        self.assertEqual(("required", "unavailable", []), (search["policy"], search["status"], search["names"]))
        self.assertIn(search["reason"], run["error"])
        self.assertEqual(["user"], [row[0] for row in self.db.execute("SELECT role FROM query_message")])
        self.assertEqual("error", run["events"][0]["eventType"])
        self.assertEqual(run["error"], run["events"][0]["payload"]["error"])
        trace = json.loads((self.service.trace_root / f"{run['runId']}.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", trace["status"])
        self.assertEqual(run["execution"], trace["run"]["execution"])
        self.assertEqual([], trace["calls"])
        self.assertEqual("error", trace["events"][0]["eventType"])
        self.assertEqual("RequiredToolUnavailable", trace["events"][0]["subtype"])
        self.assertFalse((self.root / "missing-index").exists())

    def test_optional_missing_indexes_keep_coordinated_file_query_behavior(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from business_code_agent.query.langchain_agent import LangChainRuntime

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, *args, **kwargs):
                return self

        runtime = LangChainRuntime(environment={}, profile=resolve_profile({}), project_id="prepared-test",
                                   index_root=self.root / "missing-index", contract_root=self.root / "missing-contract",
                                   model=ToolModel(responses=[AIMessage(content="返回两数之和。")]))
        self.service.runtime = runtime
        result = self.service.query("解释 repos/repo/math.py 这个函数")
        self.assertEqual("completed", result["status"])
        self.assertEqual("combined", result["mode"])
        self.assertEqual("coordinated-legacy-v1", result["execution"]["profileId"])
        self.assertEqual("coordinated", result["execution"]["investigation"])
        self.assertEqual("plain", result["execution"]["answerMode"])
        tools = {tool["capability"]: tool for tool in result["execution"]["tools"]}
        for name in ("search_code", "contract_trace"):
            self.assertEqual(("optional", "unavailable", []),
                             (tools[name]["policy"], tools[name]["status"], tools[name]["names"]))
        self.assertEqual(["Read", "Glob", "Grep"], tools["file_tools"]["names"])
        self.assertEqual(["Investigation"], tools["investigation"]["names"])
        reads = [event["payload"] for event in result["events"]
                 if event["eventType"] == "tool" and event["payload"]["status"] == "completed"]
        self.assertEqual(["Read"], [tool["name"] for tool in reads])
        self.assertEqual("workflow", reads[0]["origin"])
        trace = self.service.get_trace(result["runId"])
        self.assertIn("return a + b", str(trace["calls"][0]["messages"]))
        self.assertEqual(1, result["usage"]["modelCalls"])
        self.assertFalse(result["usage"]["separateFinalization"])
        self.assertEqual(result["execution"], self.service.get_run(result["runId"])["execution"])
        self.assertFalse((self.root / "missing-index").exists())
        self.assertFalse((self.root / "missing-contract").exists())


if __name__ == "__main__":
    unittest.main()

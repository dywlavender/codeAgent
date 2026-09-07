from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from business_code_agent.query_agent.claude_runtime import ClaudeCodeRuntime
from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryBusyError, QueryService
from business_code_agent.schema import connect


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


if __name__ == "__main__":
    unittest.main()

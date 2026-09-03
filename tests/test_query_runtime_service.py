from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryService
from business_code_agent.schema import connect


class _FakeRuntime:
    runtime_name = "FAKE_RUNTIME"

    def __init__(self):
        self.calls = []

    def ask(self, question, *, workspace, session_id=None, event_callback=None):
        self.calls.append({"question": question, "workspace": workspace, "session_id": session_id})
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
            db.close()

    def test_runtime_failure_is_recorded_and_does_not_create_assistant_message(self):
        class BrokenRuntime:
            runtime_name = "BROKEN"

            def ask(self, question, *, workspace, session_id=None, event_callback=None):
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


if __name__ == "__main__":
    unittest.main()

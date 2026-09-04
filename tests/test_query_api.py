from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from business_code_agent.query_agent.api import make_server
from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryService as RealQueryService
from business_code_agent.schema import connect


class _Runtime:
    runtime_name = "FAKE_RUNTIME"

    def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None):
        if event_callback:
            event_callback({"sequence": 1, "eventType": "tool_use", "payload": {"name": "Read"}})
        return RuntimeResult("答案：" + question, session_id or "session-1")


class QueryApiTest(unittest.TestCase):
    def test_cancel_endpoint_stops_stream_and_persists_partial_history(self):
        class WaitingRuntime:
            runtime_name = "WAITING_TEST"

            def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None):
                event_callback({"sequence": 1, "eventType": "text", "payload": {
                    "id": "m", "mode": "append", "text": "部分回答",
                }})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if cancel_check():
                        return RuntimeResult("部分回答", "cancel-session", status="cancelled")
                    threading.Event().wait(0.01)
                raise RuntimeError("cancel signal not received")

        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "cancel.db"
            connect(str(db_path)).close()
            def service_factory(db, **kwargs):
                return RealQueryService(db, runtime=WaitingRuntime(), **kwargs)
            with patch("business_code_agent.query_agent.api.QueryService", service_factory):
                server = make_server(str(db_path), port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    query = Request(base + "/api/query/stream", method="POST",
                                    data=b'{"question":"q"}', headers={"Content-Type": "application/json"})
                    with urlopen(query, timeout=8) as response:
                        self.assertEqual(b"event: run\n", response.readline())
                        metadata = json.loads(response.readline().decode().removeprefix("data: "))
                        response.readline()
                        running = json.loads(urlopen(base + "/api/conversations/" + metadata["conversationId"]).read())
                        self.assertEqual("running", running["items"][-1]["status"])
                        duplicate = Request(base + "/api/query", method="POST", data=json.dumps({
                            "question": "duplicate", "conversationId": metadata["conversationId"],
                        }).encode(), headers={"Content-Type": "application/json"})
                        with self.assertRaises(HTTPError) as raised:
                            urlopen(duplicate)
                        self.assertEqual(409, raised.exception.code)
                        self.assertEqual("CONVERSATION_BUSY", json.loads(raised.exception.read())["code"])
                        raised.exception.close()
                        cancel_url = base + "/api/query/" + metadata["runId"] + "/cancel"
                        cancelled = json.loads(urlopen(Request(cancel_url, method="POST", data=b"{}")).read())
                        self.assertIn(cancelled["status"], {"cancelling", "cancelled"})
                        stream = response.read().decode()
                        self.assertIn('"status":"cancelled"', stream)
                        self.assertNotIn("event: error", stream)
                    detail = json.loads(urlopen(base + "/api/query/" + metadata["runId"]).read())
                    self.assertEqual("cancelled", detail["status"])
                    self.assertEqual("部分回答", detail["answer"])
                    self.assertEqual("cancel-session", detail["sessionId"])
                    self.assertEqual("cancelled", detail["events"][-1]["payload"]["phase"])
                    again = json.loads(urlopen(Request(cancel_url, method="POST", data=b"{}")).read())
                    self.assertEqual("cancelled", again["status"])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_json_and_sse_query_endpoints_use_runtime_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "query.db"
            config = root / "project.json"
            config.write_text(json.dumps({"project": {"id": "api-test", "name": "API Test"}}), encoding="utf-8")
            connect(str(db_path)).close()

            def service_factory(db, **kwargs):
                return RealQueryService(db, runtime=_Runtime(), **kwargs)

            with patch("business_code_agent.query_agent.api.QueryService", service_factory):
                server = make_server(str(db_path), port=0, project_config=str(config))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    request = Request(
                        base + "/api/query",
                        method="POST",
                        data=json.dumps({"question": "第一问"}).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    first = json.loads(urlopen(request).read())
                    self.assertEqual("答案：第一问", first["answer"])
                    self.assertEqual("FAKE_RUNTIME", first["runtime"])
                    conversations = json.loads(urlopen(base + "/api/conversations?limit=1").read())
                    self.assertEqual(first["conversationId"], conversations["items"][0]["conversationId"])
                    completed = json.loads(urlopen(Request(
                        base + "/api/query/" + first["runId"] + "/cancel", method="POST", data=b"{}",
                    )).read())
                    self.assertEqual("completed", completed["status"])

                    stream_request = Request(
                        base + "/api/query/stream",
                        method="POST",
                        data=json.dumps({"question": "第二问", "conversationId": first["conversationId"]}).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    stream = urlopen(stream_request).read().decode("utf-8")
                    self.assertIn("event: event", stream)
                    self.assertIn("event: result", stream)
                    self.assertIn("答案：第二问", stream)

                    detail = json.loads(urlopen(base + "/api/query/" + first["runId"]).read())
                    self.assertEqual("答案：第一问", detail["answer"])
                    self.assertEqual("tool_use", detail["events"][0]["eventType"])
                    history = json.loads(urlopen(base + "/api/conversations/" + first["conversationId"]).read())
                    self.assertEqual(["第一问", "第二问"], [item["question"] for item in history["items"]])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

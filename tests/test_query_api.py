from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from business_code_agent.query_agent.api import make_server
from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryService as RealQueryService
from business_code_agent.schema import connect


class _Runtime:
    runtime_name = "FAKE_RUNTIME"

    def ask(self, question, *, workspace, session_id=None, event_callback=None):
        if event_callback:
            event_callback({"sequence": 1, "eventType": "tool_use", "payload": {"name": "Read"}})
        return RuntimeResult("答案：" + question, session_id or "session-1")


class QueryApiTest(unittest.TestCase):
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
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

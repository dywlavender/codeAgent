from __future__ import annotations

import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from business_code_agent.query_agent.api import make_server
from business_code_agent.schema import connect


class KnowledgeAdminApiTest(unittest.TestCase):
    def test_generate_review_publish_http_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "api.db"
            connect(str(db_path)).close()
            server = make_server(str(db_path), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                created = _json_request(base + "/api/knowledge-admin/proposals/generate", {
                    "sourceType": "ADMIN_NOTE",
                    "sourceId": "note-http-1",
                    "content": "取消订单功能负责关闭未完成订单并释放库存。",
                    "functionName": "取消订单",
                })
                self.assertEqual("PENDING_REVIEW", created["status"])
                pending = json.loads(urlopen(base + "/api/knowledge-admin/pending").read())
                self.assertEqual(created["id"], pending["items"][0]["id"])

                reviewed = _json_request(
                    base + f"/api/knowledge-admin/proposals/{created['id']}/review",
                    {"action": "ACCEPT", "reviewer": "http-admin", "comment": "确认"},
                )
                self.assertEqual("PUBLISHED", reviewed["proposal"]["status"])
                functions = json.loads(urlopen(base + "/api/knowledge-admin/functions").read())
                self.assertEqual("取消订单", functions["items"][0]["name"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_configured_admin_token_protects_governance_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "protected.db"
            config = Path(folder) / "agent.json"
            config.write_text(json.dumps({
                "admin": {"name": "reviewer-a", "apiTokenEnv": "TEST_ADMIN_TOKEN"}
            }), encoding="utf-8")
            connect(str(db_path)).close()
            with patch.dict("os.environ", {"TEST_ADMIN_TOKEN": "test-admin-token"}):
                server = make_server(str(db_path), port=0, project_config=str(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(base + "/api/knowledge-admin/pending")
                self.assertEqual(401, denied.exception.code)
                denied.exception.close()
                request = Request(
                    base + "/api/knowledge-admin/pending",
                    headers={"Authorization": "Bearer test-admin-token"},
                )
                self.assertEqual([], json.loads(urlopen(request).read())["items"])
                workspace = json.loads(urlopen(base + "/api/workspace").read())
                self.assertTrue(workspace["adminAuthRequired"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


def _json_request(url: str, body: dict) -> dict:
    request = Request(
        url, method="POST", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urlopen(request).read())


if __name__ == "__main__":
    unittest.main()

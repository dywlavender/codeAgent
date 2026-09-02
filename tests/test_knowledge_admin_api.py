from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from business_code_agent.query_agent.api import _user_facing_internal_error, make_server
from business_code_agent.schema import connect


class KnowledgeAdminApiTest(unittest.TestCase):
    def test_model_quota_error_is_actionable_without_exposing_provider_response(self):
        message = _user_facing_internal_error(
            RuntimeError("Error code: 403 insufficient_quota: Free quota exhausted")
        )
        self.assertIn("额度已用尽", message)
        self.assertNotIn("insufficient_quota", message)

    def test_wrapped_model_access_error_is_actionable(self):
        cause = PermissionError("AccessDenied.Unpurchased")
        error = RuntimeError("query understanding failed")
        error.__cause__ = cause
        message = _user_facing_internal_error(error)
        self.assertIn("未开通所选模型", message)
        self.assertNotIn("AccessDenied.Unpurchased", message)

    def test_wrapped_deepseek_thinking_tool_choice_error_is_actionable(self):
        cause = RuntimeError("Thinking mode does not support this tool_choice")
        error = RuntimeError("query understanding failed")
        error.__cause__ = cause
        message = _user_facing_internal_error(error)
        self.assertIn("思考模式不支持", message)
        self.assertIn("BUSINESS_CODE_MODEL_THINKING=disabled", message)
        self.assertNotIn("tool_choice", message)

    def test_wrapped_sqlite_thread_error_is_actionable(self):
        cause = RuntimeError("SQLite objects created in a thread can only be used in that same thread")
        error = RuntimeError("query understanding failed")
        error.__cause__ = cause
        message = _user_facing_internal_error(error)
        self.assertIn("数据库连接发生跨线程使用", message)

    def test_natural_baseline_refresh_and_entity_read_http_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "baseline.db"
            knowledge = root / "baseline"
            knowledge.mkdir()
            (knowledge / "business.md").write_text(
                "# 业务基线\n\n## 极优\n\n极优是再担保类型产品，代码中一般用 JY 表示。\n",
                encoding="utf-8",
            )
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            connect(str(db_path)).close()
            server = make_server(str(db_path), port=0, project_config=str(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                refreshed = _json_request(
                    base + "/api/knowledge/baselines/refresh",
                    {"parser": "markdown"},
                )
                self.assertEqual(1, refreshed["sourceCount"])
                payload = json.loads(urlopen(base + "/api/knowledge/entities").read())
                self.assertEqual("极优", payload["items"][0]["name"])
                self.assertEqual("BUSINESS_TERM", payload["items"][0]["type"])
                self.assertNotIn("mappings", payload["items"][0])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_configured_admin_token_protects_refresh_but_not_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "protected.db"
            knowledge = root / "baseline"
            knowledge.mkdir()
            (knowledge / "business.md").write_text(
                "# 业务基线\n\n## 订单取消\n\n用户取消订单后，系统关闭未完成订单。\n",
                encoding="utf-8",
            )
            config = root / "project.json"
            config.write_text(json.dumps({
                "knowledge": {"baselineRoot": "baseline"},
                "admin": {"name": "knowledge-admin", "apiTokenEnv": "TEST_ADMIN_TOKEN"},
            }), encoding="utf-8")
            connect(str(db_path)).close()
            with patch.dict("os.environ", {"TEST_ADMIN_TOKEN": "test-admin-token"}):
                server = make_server(str(db_path), port=0, project_config=str(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with self.assertRaises(HTTPError) as denied:
                    _json_request(base + "/api/knowledge/baselines/refresh", {"parser": "markdown"})
                self.assertEqual(401, denied.exception.code)
                denied.exception.close()
                refreshed = _json_request(base + "/api/knowledge/baselines/refresh", {"parser": "markdown"}, "test-admin-token")
                self.assertEqual(1, refreshed["sourceCount"])
                self.assertEqual(1, len(json.loads(urlopen(base + "/api/knowledge/entities").read())["items"]))
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


def _json_request(url: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, method="POST", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    return json.loads(urlopen(request).read())


if __name__ == "__main__":
    unittest.main()

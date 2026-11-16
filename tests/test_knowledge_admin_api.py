from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from business_code_agent.api.server import _user_facing_internal_error, make_server
from business_code_agent.platform.projects import ProjectRegistry
from business_code_agent.storage.schema import connect


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

    def test_claude_auth_error_is_actionable(self):
        cause = RuntimeError("Claude Code authentication failed")
        error = RuntimeError("query understanding failed")
        error.__cause__ = cause
        message = _user_facing_internal_error(error)
        self.assertIn("Claude Code 未通过认证", message)
        self.assertNotIn("authentication", message)

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

    def test_context_editor_uses_project_markdown_and_rejects_stale_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = ProjectRegistry(root / "platform")
            name = "rules/额度.md"
            for project_id in ("alpha", "beta"):
                config = root / f"{project_id}.json"
                config.write_text(json.dumps({
                    "project": {"id": project_id},
                    "admin": {"apiTokenEnv": "TEST_ADMIN_TOKEN"},
                }), encoding="utf-8")
                context = registry.register(config)
                source = context.business_context_root / name
                source.parent.mkdir()
                source.write_bytes(f"# {project_id}\r\n".encode("utf-8"))
            with patch.dict("os.environ", {"TEST_ADMIN_TOKEN": "test-admin-token"}):
                server = make_server(str(root / "legacy.db"), port=0, project_registry=registry)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}/api/projects"
                    files = f"{base}/alpha/knowledge/context/files"
                    document = f"{files}/{quote(name, safe='')}"
                    with self.assertRaises(HTTPError) as denied:
                        _json_request(document, method="GET")
                    self.assertEqual(401, denied.exception.code)
                    denied.exception.close()
                    token = "test-admin-token"
                    self.assertEqual({"items": [name], "editable": True},
                                     _json_request(files, token=token, method="GET"))
                    original = _json_request(document, token=token, method="GET")
                    self.assertEqual("# alpha\r\n", original["content"])
                    edited = {"previousContent": original["content"], "content": "# 新约定\n"}
                    self.assertEqual(edited["content"], _json_request(
                        document, edited, token, method="PUT")["content"])
                    with self.assertRaises(HTTPError) as stale:
                        _json_request(document, edited, token, method="PUT")
                    self.assertEqual(409, stale.exception.code)
                    stale.exception.close()
                    _json_request(f"{files}/new.md", {"previousContent": None, "content": "# 新文档\n"},
                                  token, method="PUT")
                    self.assertEqual(edited["content"],
                                     (registry.get("alpha").business_context_root / name).read_text())
                    self.assertEqual("# beta\r\n",
                                     (registry.get("beta").business_context_root / name).read_bytes().decode())
                    self.assertFalse((registry.get("beta").business_context_root / "new.md").exists())
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=2)


def _json_request(url: str, body: dict | None = None, token: str | None = None, *,
                  method: str = "POST") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(url, method=method, data=data, headers=headers)
    with urlopen(request) as response:
        return json.load(response)


if __name__ == "__main__":
    unittest.main()

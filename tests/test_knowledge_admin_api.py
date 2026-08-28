from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from business_code_agent.query_agent.api import make_server
from business_code_agent.schema import connect


DOCUMENT = """---
id: cancel-order
name: 取消订单
tags:
  - 订单
---

# 功能说明

关闭未完成订单。

## 业务场景

- 用户取消订单

## 工程与入口

| 工程 | 类型 | 入口类 |
|---|---|---|
| order-service | 服务 | OrderController |

## 关键表

| 表名 | 数据作用 |
|---|---|
| orders | 保存订单状态 |
"""


class KnowledgeAdminApiTest(unittest.TestCase):
    def test_refresh_and_read_function_http_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "api.db"
            knowledge = root / "knowledge"
            knowledge.mkdir()
            (knowledge / "cancel-order.md").write_text(DOCUMENT, encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"root": "knowledge"}}), encoding="utf-8")
            connect(str(db_path)).close()
            server = make_server(str(db_path), port=0, project_config=str(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                refreshed = _json_request(base + "/api/knowledge/refresh", {"analyze": False})
                self.assertEqual(1, refreshed["functionCount"])
                functions = json.loads(urlopen(base + "/api/knowledge/functions").read())
                self.assertEqual("取消订单", functions["items"][0]["name"])
                detail = json.loads(urlopen(base + "/api/knowledge/functions/cancel-order").read())
                self.assertEqual("NOT_FOUND", detail["entries"][0]["resolution_status"])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_configured_admin_token_protects_refresh_but_not_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "protected.db"
            knowledge = root / "knowledge"
            knowledge.mkdir()
            (knowledge / "cancel-order.md").write_text(DOCUMENT, encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({
                "knowledge": {"root": "knowledge"},
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
                    _json_request(base + "/api/knowledge/refresh", {"analyze": False})
                self.assertEqual(401, denied.exception.code)
                denied.exception.close()
                refreshed = _json_request(base + "/api/knowledge/refresh", {"analyze": False}, "test-admin-token")
                self.assertEqual(1, refreshed["functionCount"])
                self.assertEqual(1, len(json.loads(urlopen(base + "/api/knowledge/functions").read())["items"]))
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

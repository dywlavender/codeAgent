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
                    projection = f"{base}/alpha/materials/projection"
                    self.assertEqual("not_imported", _json_request(projection, method="GET")["status"])
                    _json_request(f"{base}/alpha/knowledge/baselines/refresh", {"parser": "markdown"}, token)
                    self.assertEqual("synced", _json_request(projection, method="GET")["status"])
                    edited = {"previousContent": original["content"], "content": "# 新约定\n"}
                    self.assertEqual(edited["content"], _json_request(
                        document, edited, token, method="PUT")["content"])
                    self.assertEqual("stale", _json_request(projection, method="GET")["status"])
                    self.assertEqual("not_imported", _json_request(
                        f"{base}/beta/materials/projection", method="GET")["status"])
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

    def test_suggestions_use_only_the_selected_section_until_confirmed_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            knowledge = root / "business"
            knowledge.mkdir()
            document = knowledge / "rules.md"
            original = "# 提款规则\n\n## 审核\n\n审核范围。\n\n## 例外\n\n原例外。\n"
            document.write_text(original, encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"businessContextRoot": "business"}}))
            server = make_server(str(root / "knowledge.db"), port=0, project_config=str(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/knowledge/context"
            try:
                payload = {"name": "rules.md", "previousContent": original,
                           "note": "已确认审核适用范围更新。", "sectionStart": 3}
                suggestion = {
                    "action": "revise", "reason": "业务负责人已确认。",
                    "replacement": "## 审核\n\n已确认的新范围。",
                    "basis": ["已确认审核适用范围更新。"], "pendingQuestions": [],
                }
                response = "\u200b建议如下\n```json\n" + json.dumps(suggestion, ensure_ascii=False) + "\n```"
                with patch("business_code_agent.api.server.complete_text",
                           return_value=response) as complete:
                    result = _json_request(f"{base}/suggest", payload)
                    self.assertEqual("## 审核\n\n审核范围。\n\n", result["original"])
                    model_input = json.loads(complete.call_args.args[1])
                    self.assertEqual({"original": result["original"],
                                      "additionalMaterial": payload["note"]}, model_input)
                    self.assertEqual(original, document.read_text())
                    approved = "# 提款规则\n\n## 审核\n\n已确认的新范围。\n\n## 例外\n\n原例外。\n"
                    self.assertEqual(approved, result["proposalContent"])
                    _json_request(f"{base}/files/rules.md",
                                  {"previousContent": original, "content": approved}, method="PUT")
                    with self.assertRaises(HTTPError) as stale:
                        _json_request(f"{base}/suggest", payload)
                    self.assertEqual(409, stale.exception.code)
                    stale.exception.close()
                    self.assertEqual(1, complete.call_count)
                uncertain = {**suggestion, "action": "needs_confirmation", "pendingQuestions": ["业务口径？"]}
                with patch("business_code_agent.api.server.complete_text",
                           return_value=json.dumps(uncertain, ensure_ascii=False)):
                    result = _json_request(f"{base}/suggest", {
                        **payload, "previousContent": approved, "note": "代码删去了审核分支。"})
                    self.assertEqual("needs_confirmation", result["action"])
                    self.assertIsNone(result["proposalContent"])
                    self.assertEqual(approved, document.read_text())
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_system_guide_draft_requires_source_read_and_explicit_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "guides"},
                "systems": [{"id": "core"}, {"id": "ops"}],
                "repositories": [{"id": "loan", "localPath": "loan"},
                                 {"id": "reports", "localPath": "reports"}],
                "applications": [{"id": "api", "systemId": "core", "repositoryId": "loan", "sourceRoot": "src"},
                                 {"id": "audit", "systemId": "core", "repositoryId": "loan", "sourceRoot": "audit"},
                                 {"id": "job", "systemId": "ops", "repositoryId": "loan", "sourceRoot": "job"},
                                 {"id": "export", "systemId": "ops", "repositoryId": "reports", "sourceRoot": "src"}],
            }))
            registry = ProjectRegistry(root / "platform")
            context = registry.register(config)
            for name, text in {
                "core/SYSTEM.md": "核心系统通用规则",
                "core/applications/audit.md": "审计应用特殊规则",
                "ops/SYSTEM.md": "运营系统通用规则",
                "ops/applications/job.md": "运营任务特殊规则",
                "ops/applications/export.md": "报表应用特殊规则",
            }.items():
                guide = context.system_guides_root / name
                guide.parent.mkdir(parents=True, exist_ok=True)
                guide.write_text(text, encoding="utf-8")
            server = make_server(str(root / "legacy.db"), port=0, project_registry=registry)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/projects/guides/knowledge/system-guides"
            try:
                target = {"systemId": "core", "applicationId": "api"}
                answer = "# 核心应用导航\n\n## 导航摘要\n\n在本应用中定位接口。"
                reply = {"status": "completed", "answer": answer, "runId": "draft",
                         "usage": {"verification": {"readFiles": ["repos/loan/README.md", "repos/loan/job/Job.java"]}}}
                with patch("business_code_agent.api.server.QueryService.query", return_value=reply) as query:
                    with self.assertRaises(HTTPError) as insufficient:
                        _json_request(base + "/draft", target)
                    self.assertEqual(422, insufficient.exception.code)
                    insufficient.exception.close()
                    reply["usage"]["verification"]["readFiles"].append("repos/loan/src/Api.java")
                    result = _json_request(base + "/draft", target)
                    self.assertEqual(["repos/loan/src/Api.java"], result["sourceFiles"])
                    self.assertEqual({"repositoryIds": ["loan"]}, query.call_args.kwargs["scope"])
                file = base + "/file?systemId=core&applicationId=api"
                self.assertIsNone(_json_request(file, method="GET")["content"])
                _json_request(base + "/file", {**target, "content": result["content"], "previousContent": None}, method="PUT")
                system = _json_request(base + "/preview?systemId=core", method="GET")
                self.assertEqual({"systemIds": ["core"]}, system["scope"])
                self.assertEqual(["api", "audit"], [app["id"] for app in system["applications"]])
                self.assertIn("核心系统通用规则", system["guidance"])
                self.assertIn("审计应用特殊规则", system["guidance"])
                self.assertNotIn("运营系统通用规则", system["guidance"])

                application = _json_request(base + "/preview?systemId=core&applicationId=api", method="GET")
                self.assertEqual({"systemIds": ["core"], "applicationIds": ["api"]}, application["scope"])
                self.assertEqual(["api"], [app["id"] for app in application["applications"]])
                self.assertEqual(["knowledge/system-guides/core/SYSTEM.md",
                                  "knowledge/system-guides/core/applications/api.md"], application["documents"])
                self.assertIn("核心系统通用规则", application["guidance"])
                self.assertIn("在本应用中定位接口", application["guidance"])
                self.assertNotIn("审计应用特殊规则", application["guidance"])
                self.assertNotIn("运营任务特殊规则", application["guidance"])

                repository = _json_request(base + "/preview?systemId=core&applicationId=api&repositoryId=loan",
                                           method="GET")
                self.assertEqual({"repositoryIds": ["loan"]}, repository["scope"])
                self.assertEqual(["api", "audit", "job"], [app["id"] for app in repository["applications"]])
                self.assertIn("运营系统通用规则", repository["guidance"])
                self.assertIn("运营任务特殊规则", repository["guidance"])
                self.assertNotIn("报表应用特殊规则", repository["guidance"])
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

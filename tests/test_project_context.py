from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from business_code_agent.project_context import ProjectRegistry
from business_code_agent.evaluation.ast_service import AstService
from business_code_agent.query_agent.api import make_server
from business_code_agent.query_agent.runtime import RuntimeResult
from business_code_agent.query_agent.service import QueryService as RealQueryService
from business_code_agent.schema import connect


class _Runtime:
    runtime_name = "PROJECT_TEST_RUNTIME"

    def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None, repositories=None):
        return RuntimeResult(f"答案：{question}", session_id or "project-session")


class ProjectContextTest(unittest.TestCase):
    def _config(self, root: Path, project_id: str, source: Path) -> Path:
        config = root / f"{project_id}.json"
        baseline = root / f"{project_id}-baseline"
        requirements = root / f"{project_id}-requirements"
        baseline.mkdir(parents=True, exist_ok=True)
        requirements.mkdir(parents=True, exist_ok=True)
        (baseline / "project-overview.md").write_text(f"{project_id} overview", encoding="utf-8")
        (requirements / "requirement.md").write_text(f"{project_id} requirement", encoding="utf-8")
        config.write_text(json.dumps({
            "project": {"id": project_id, "name": project_id.upper()},
            "repositories": [{"id": "core", "gitUrl": "unused", "localPath": str(source)}],
            "knowledge": {"baselineRoot": str(baseline)},
            "requirements": {"root": str(requirements)},
        }), encoding="utf-8")
        return config

    def _projects(self, root: Path):
        registry = ProjectRegistry(root / "platform")
        configs = {}
        for project_id in ("alpha", "beta"):
            source = root / project_id / "source"
            source.mkdir(parents=True)
            (source / "README.md").write_text(project_id, encoding="utf-8")
            configs[project_id] = self._config(root, project_id, source)
            registry.register(configs[project_id])
        return registry, configs

    def test_registered_projects_have_independent_storage_and_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, configs = self._projects(root)
            alpha = registry.get("alpha")
            beta = registry.get("beta")

            self.assertNotEqual(alpha.data_root, beta.data_root)
            self.assertNotEqual(alpha.db_path, beta.db_path)
            self.assertNotEqual(alpha.ast_root, beta.ast_root)
            self.assertNotEqual(alpha.evaluations_root, beta.evaluations_root)
            self.assertEqual({"alpha", "beta"}, {item["id"] for item in registry.list()})
            self.assertTrue((alpha.data_root / "project.json").is_file())
            self.assertFalse((alpha.knowledge_root / "baseline").exists())
            self.assertEqual([], list(alpha.requirements_root.iterdir()))
            self.assertEqual("missing", AstService(project_config=configs["alpha"], data_root=alpha.ast_root).status()["status"])
            self.assertEqual("missing", AstService(project_config=configs["beta"], data_root=beta.ast_root).status()["status"])

            alpha_db = connect(str(alpha.db_path))
            alpha_service = RealQueryService(
                alpha_db, db_path=str(alpha.db_path), project_config=str(configs["alpha"]),
                runtime=_Runtime(), workspace_root=alpha.workspace_root,
                ast_data_root=alpha.ast_root, baseline_root=alpha.knowledge_root / "baseline",
                requirements_root=alpha.requirements_root, project_id=alpha.project_id, project_scoped=True,
            )
            first = alpha_service.query("alpha question")
            alpha_db.close()

            beta_db = connect(str(beta.db_path))
            beta_service = RealQueryService(
                beta_db, db_path=str(beta.db_path), project_config=str(configs["beta"]),
                runtime=_Runtime(), workspace_root=beta.workspace_root,
                ast_data_root=beta.ast_root, baseline_root=beta.knowledge_root / "baseline",
                requirements_root=beta.requirements_root, project_id=beta.project_id, project_scoped=True,
            )
            self.assertEqual([], beta_service.list_conversations()["items"])
            with self.assertRaises(ValueError):
                beta_service.query("must reject", conversation_id=first["conversationId"])
            beta_db.close()

    def test_material_import_is_explicit_after_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, _ = self._projects(root)
            alpha = registry.get("alpha")
            result = registry.import_materials(
                "alpha",
                baseline_root=root / "alpha-baseline",
                requirements_root=root / "alpha-requirements",
            )
            self.assertEqual({"businessContext", "requirements"}, set(result["copied"]))
            self.assertEqual("alpha overview", (alpha.business_context_root / "project-overview.md").read_text(encoding="utf-8"))
            self.assertEqual("alpha requirement", (alpha.requirements_root / "requirement.md").read_text(encoding="utf-8"))

    def test_http_routes_and_rejects_cross_project_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, _ = self._projects(root)

            def service_factory(db, **kwargs):
                return RealQueryService(db, runtime=_Runtime(), **kwargs)

            with patch("business_code_agent.query_agent.api.QueryService", service_factory):
                server = make_server(str(root / "unused.db"), port=0, project_registry=registry)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    projects = json.loads(urlopen(base + "/api/projects").read())["items"]
                    self.assertEqual({"alpha", "beta"}, {item["id"] for item in projects})

                    workspace = json.loads(urlopen(base + "/api/projects/alpha/workspace").read())
                    self.assertEqual("alpha", workspace["projectId"])
                    with self.assertRaises(HTTPError) as missing_context:
                        urlopen(base + "/api/workspace")
                    self.assertEqual(400, missing_context.exception.code)

                    first_request = Request(
                        base + "/api/projects/alpha/api/query", method="POST",
                        data=b'{"question":"alpha question"}',
                        headers={"Content-Type": "application/json"},
                    )
                    first = json.loads(urlopen(first_request).read())
                    self.assertTrue(first["conversationId"].startswith("CONV-alpha-"))

                    beta_history = json.loads(urlopen(base + "/api/projects/beta/api/conversations").read())
                    self.assertEqual([], beta_history["items"])
                    cross_request = Request(
                        base + "/api/projects/beta/api/query", method="POST",
                        data=json.dumps({"question": "cross", "conversationId": first["conversationId"]}).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(cross_request)
                    self.assertEqual(400, raised.exception.code)
                    self.assertIn("不属于当前工程", raised.exception.read().decode())
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_legacy_server_accepts_explicit_project_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, configs = self._projects(root)
            server = make_server(str(root / "legacy.db"), port=0, project_config=str(configs["alpha"]))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                direct = json.loads(urlopen(base + "/api/workspace").read())
                prefixed = json.loads(urlopen(base + "/api/projects/alpha/api/workspace").read())
                normalized = json.loads(urlopen(base + "/api/projects/alpha/workspace").read())
                self.assertEqual(direct["projectId"], prefixed["projectId"])
                self.assertEqual(direct["projectId"], normalized["projectId"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

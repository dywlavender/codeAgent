"""HTTP surface for the effect-verification page."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from business_code_agent.evaluation.service import EvaluationService
from business_code_agent.query_agent.api import make_server
from business_code_agent.schema import connect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_evaluation_service import ScriptedRuntime, make_project, suite_payload, wait_for


class EvaluationApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = make_project(self.root)
        self.db_path = self.root / "knowledge.db"
        connect(str(self.db_path)).close()

    def tearDown(self):
        self._tmp.cleanup()

    def serve(self, service, project_config=None):
        server = make_server(str(self.db_path), port=0,
                             project_config=str(project_config or self.config),
                             evaluation_service=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._drain, service)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        return f"http://127.0.0.0:{server.server_port}".replace("127.0.0.0", "127.0.0.1")

    @staticmethod
    def _drain(service):
        """批次线程写完收尾文件后再清理临时目录。"""
        for handle in list(service._active.values()):
            handle["thread"].join(timeout=15)

    def get(self, base, path):
        with urlopen(base + path, timeout=8) as response:
            return json.loads(response.read())

    def send(self, base, path, method, payload=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(base + path, method=method,
                          data=json.dumps(payload or {}).encode(), headers=headers)
        with urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read())

    def test_full_batch_flow_over_http(self):
        service = EvaluationService(project_config=str(self.config),
                                    data_root=str(self.root / ".data" / "evaluations"),
                                    timeout_seconds=5,
                                    runtime_factory=lambda job=None: ScriptedRuntime())
        service.ast_service.generate()
        self.assertTrue(wait_for(lambda: service.ast_service.status().get("status") == "available"))
        base = self.serve(service)
        setup = self.get(base, "/api/evaluations/setup")
        self.assertEqual("演示项目", setup["project"]["name"])
        self.assertTrue(setup["baseline"]["hasOverview"])

        status, suite = self.send(base, "/api/evaluation-suites", "POST", suite_payload())
        self.assertEqual(201, status)
        status, started = self.send(base, "/api/evaluations", "POST",
                                    {"suiteId": suite["id"], "repeats": 1})
        self.assertEqual(202, status)
        eval_id = started["evaluationId"]
        self.assertTrue(wait_for(lambda: self.get(base, f"/api/evaluations/{eval_id}")["status"]
                                 in ("completed", "cancelled", "failed", "interrupted")))
        state = self.get(base, f"/api/evaluations/{eval_id}")
        self.assertEqual("completed", state["status"])
        self.assertEqual(6, state["progress"]["answers"]["total"])
        self.assertTrue(state["summaries"]["model"]["aggregates"]["optional"]["score"] is not None)
        self.assertTrue(state["summaries"]["model"]["aggregates"]["ast"]["score"] is not None)

        detail = self.get(base, f"/api/evaluations/{eval_id}/cases/flow/repeats/1")
        answer_id = detail["runs"]["code_only"]["answerId"]
        self.assertIn("答案正文", detail["runs"]["code_only"]["answer"])

        status, saved = self.send(base, f"/api/evaluations/{eval_id}/reviews/{answer_id}", "PUT",
                                  {"checks": [1, 1], "issues": []})
        self.assertEqual(200, status)
        self.assertEqual("reviewed", saved["reviewStatus"])
        review_state = self.get(base, f"/api/evaluations/{eval_id}")
        self.assertIn("review", review_state["summaries"])

        report = self.get(base, f"/api/evaluations/{eval_id}/report?source=review")
        self.assertIn("复核记录视图", report["markdown"])
        model_report = self.get(base, f"/api/evaluations/{eval_id}/report?source=model")
        self.assertIn("模型初评视图", model_report["markdown"])

        source = self.get(base, f"/api/evaluations/{eval_id}/sources/repo-0?path=src/Main.java")
        self.assertEqual(1, source["totalLines"])
        self.assertEqual("class Main {}", source["lines"][0]["text"])

        status, after = self.send(base, f"/api/evaluations/{eval_id}/cancel", "POST")
        self.assertEqual(202, status)
        self.assertEqual("completed", after["status"])

        history = self.get(base, "/api/evaluations")
        self.assertEqual(1, len(history["items"]))
        self.assertIn("score", history["items"][0]["summaries"]["review"])

    def test_missing_resources_return_client_errors(self):
        service = EvaluationService(project_config=str(self.config),
                                    data_root=str(self.root / ".data" / "evaluations"),
                                    timeout_seconds=5,
                                    runtime_factory=lambda job=None: ScriptedRuntime())
        base = self.serve(service)
        with self.assertRaises(HTTPError) as raised:
            self.get(base, "/api/evaluations/eval-unknown")
        self.assertEqual(404, raised.exception.code)
        raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            self.send(base, "/api/evaluations", "POST", {"suiteId": "suite-missing"})
        self.assertEqual(400, raised.exception.code)
        raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            self.send(base, "/api/evaluations/eval-x/reviews/flow-r1-code_only", "PUT", {"checks": [1]})
        self.assertIn(raised.exception.code, (400, 404))
        raised.exception.close()

    def test_admin_token_gates_mutations(self):
        config = self.root / "admin-project.json"
        payload = json.loads(self.config.read_text(encoding="utf-8"))
        payload["admin"] = {"apiTokenEnv": "EVAL_TEST_TOKEN", "name": "admin"}
        config.write_text(json.dumps(payload), encoding="utf-8")
        service = EvaluationService(project_config=str(config),
                                    data_root=str(self.root / ".data" / "evaluations"),
                                    timeout_seconds=5,
                                    runtime_factory=lambda job=None: ScriptedRuntime())
        service.ast_service.generate()
        self.assertTrue(wait_for(lambda: service.ast_service.status().get("status") == "available"))
        with patch.dict(os.environ, {"EVAL_TEST_TOKEN": "secret-1"}):
            base = self.serve(service, project_config=config)
            suite_id = service.create_suite(suite_payload())["id"]
            with self.assertRaises(HTTPError) as raised:
                self.send(base, "/api/evaluations", "POST", {"suiteId": suite_id, "repeats": 1})
            self.assertEqual(401, raised.exception.code)
            raised.exception.close()
            with self.assertRaises(HTTPError) as raised:
                self.send(base, "/api/evaluation-suites", "POST", suite_payload())
            self.assertEqual(401, raised.exception.code)
            raised.exception.close()
            setup = self.get(base, "/api/evaluations/setup")
            self.assertTrue(setup["adminAuthRequired"])
            status, started = self.send(base, "/api/evaluations", "POST",
                                        {"suiteId": suite_id, "repeats": 1}, token="secret-1")
            self.assertEqual(202, status)
            self.assertTrue(wait_for(lambda: self.get(base, f"/api/evaluations/{started['evaluationId']}")["status"]
                                     in ("completed", "cancelled", "failed", "interrupted")))
            with self.assertRaises(HTTPError) as raised:
                self.send(base, f"/api/evaluations/{started['evaluationId']}/reviews/flow-r1-code_only", "PUT",
                          {"checks": [1, 1]}, token="wrong-token")
            self.assertEqual(401, raised.exception.code)
            raised.exception.close()


if __name__ == "__main__":
    unittest.main()

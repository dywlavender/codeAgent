"""Canonical project routes, admin writes, and cross-project retrieval isolation."""
import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from business_code_agent.api.server import make_server
from business_code_agent.platform.projects import ProjectRegistry
from business_code_agent.storage.schema import connect
from tests.test_retrieval_evaluation import make_context, suite


class RetrievalApiTest(unittest.TestCase):
    def test_project_routes_admin_and_same_case_ids(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            a, b = make_context(root / "a", "a"), make_context(root / "b", "b")
            for context in (a, b):
                data = json.loads(context.config_path.read_text())
                data["admin"] = {"apiTokenEnv": "RETRIEVAL_TEST_TOKEN"}
                context.config_path.write_text(json.dumps(data))
            registry = ProjectRegistry(root / "platform")
            for context in (a, b):
                registered = registry.register(context.config_path)
                connect(str(registered.db_path)).close()
            with patch.dict(os.environ, {"RETRIEVAL_TEST_TOKEN": "test-token"}):
                server = make_server(str(a.db_path), port=0, project_registry=registry, project_id="a")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"

                    def send(project, path, body=None, token=True):
                        headers = {"Content-Type": "application/json"}
                        if token:
                            headers["Authorization"] = "Bearer test-token"
                        req = Request(f"{base}/api/projects/{project}{path}", headers=headers,
                                      data=json.dumps(body).encode() if body is not None else None)
                        with urlopen(req, timeout=10) as response:
                            return json.loads(response.read())

                    with self.assertRaises(HTTPError) as denied:
                        send("a", "/retrieval-suites", suite("a"), token=False)
                    self.assertEqual(401, denied.exception.code)
                    denied.exception.close()
                    send("a", "/retrieval-suites", suite("a"))
                    send("b", "/retrieval-suites", suite("b"))
                    self.assertEqual(1, len(send("a", "/retrieval-suites")["items"]))
                    started = send("a", "/retrieval-evaluations", {"suiteId": "evidence-v1", "budgets": [500]})
                    import time
                    deadline = time.monotonic() + 10
                    detail = None
                    while time.monotonic() < deadline:
                        detail = send("a", f"/retrieval-evaluations/{started['id']}")
                        if detail["status"] != "running":
                            break
                        time.sleep(.02)
                    self.assertEqual("completed", detail["status"], detail)
                    selected = send("a", f"/retrieval-evaluations/{started['id']}/cases/settle?budget=500")
                    self.assertIn("if order.done", selected["context"])
                    comparison_path = f"/retrieval-evaluations/{started['id']}/context-comparisons"
                    with self.assertRaises(HTTPError) as denied_comparison:
                        send("a", comparison_path, {"budgets": [500]}, token=False)
                    self.assertEqual(401, denied_comparison.exception.code)
                    denied_comparison.exception.close()
                    compared = send("a", comparison_path, {"budgets": [500], "contextPolicy": "focused"})
                    self.assertTrue(compared["items"][0]["comparison"]["candidatesUnchanged"])
                    self.assertEqual("lexical", compared["retrievalStrategy"])
                    structure_path = f"/retrieval-evaluations/{started['id']}/structure-snapshots"
                    with self.assertRaises(HTTPError) as denied_structure:
                        send("a", structure_path, {}, token=False)
                    self.assertEqual(401, denied_structure.exception.code)
                    denied_structure.exception.close()
                    with self.assertRaises(HTTPError) as missing_structure:
                        send("a", f"/retrieval-evaluations/{started['id']}/method-comparisons", {"budgets": [500]})
                    self.assertEqual(400, missing_structure.exception.code)
                    missing_structure.exception.close()
                    self.assertEqual([], send("a", f"/retrieval-evaluations/{started['id']}")["structures"])
                    with self.assertRaises(HTTPError) as cross_structure:
                        send("b", structure_path, {})
                    self.assertEqual(404, cross_structure.exception.code)
                    cross_structure.exception.close()
                    with self.assertRaises(HTTPError) as cross_comparison:
                        send("b", comparison_path, {"budgets": [500]})
                    self.assertEqual(404, cross_comparison.exception.code)
                    cross_comparison.exception.close()
                    self.assertEqual([], send("b", "/retrieval-evaluations")["items"])
                    with self.assertRaises(HTTPError) as cross:
                        send("b", f"/retrieval-evaluations/{started['id']}")
                    self.assertEqual(404, cross.exception.code)
                    cross.exception.close()
                    with self.assertRaises(HTTPError) as invalid:
                        send("b", "/retrieval-suites", {**suite("a"), "id": "another"})
                    self.assertEqual(400, invalid.exception.code)
                    invalid.exception.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(2)


if __name__ == "__main__":
    unittest.main()

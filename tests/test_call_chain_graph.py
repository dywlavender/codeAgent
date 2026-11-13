import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

from business_code_agent.api.server import make_server
from business_code_agent.codeintel.contracts.graph import ContractGraph
from business_code_agent.codeintel.contracts.index import prepare_index
from business_code_agent.platform.projects import ProjectRegistry


DEMO = Path(__file__).resolve().parents[1] / "examples/contract-flow"


class CallChainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = ProjectRegistry(self.root / "platform")
        self.contexts, self.roots, self.configs, self.sources = {}, {}, {}, {}
        for project in ("alpha", "beta"):
            roots = {}
            for repository in ("channel", "middle"):
                path = self.root / project / repository
                shutil.copytree(DEMO / repository, path)
                roots[repository] = path
            config = {"project": {"id": project, "name": project},
                      "repositories": [{"id": key, "localPath": str(value)} for key, value in roots.items()],
                      "applications": [{"id": key, "repositoryId": key, "name": project + " " + key, "sourceRoot": "."} for key in roots]}
            config_path = self.root / f"{project}.json"
            config_path.write_text(json.dumps(config))
            context = self.registry.register(config_path)
            sources = [{"id": key, "kind": "source", "root": str(value)} for key, value in roots.items()]
            snapshot = prepare_index(context.data_root / "contract-index", sources, config, project)
            self.contexts[project], self.roots[project], self.configs[project], self.sources[project] = context, roots, config, sources
            if project == "alpha":
                self.cid = next(key for key, entry in snapshot["entries"].items() if entry["symbol"] == "demo.channel.Channel.detail")

    def graph(self, project="alpha"):
        context = self.contexts[project]
        return ContractGraph(context.data_root / "contract-index", project, self.roots[project])

    def test_call_spine_keeps_service_client_endpoint_and_hides_field_getters(self):
        chain = self.graph().chain(self.cid)
        self.assertEqual(3, len(chain["nodes"]))  # This demo Controller contains its service implementation.
        by_name = {n["label"]: n for n in chain["nodes"]}
        self.assertEqual({"Channel.detail", "MiddleClient.detail", "Middle.detail"}, set(by_name))
        self.assertEqual("HTTP_CLIENT", by_name["MiddleClient.detail"]["type"])
        self.assertEqual("ENDPOINT", by_name["Middle.detail"]["type"])
        self.assertEqual(2, len(chain["edges"]))
        self.assertEqual(1, sum(e["kind"] == "HTTP_RESPONSE" for e in chain["edges"]))
        self.assertTrue(chain["staticOnly"])

    def test_http_edge_evidence_has_client_configuration_and_target_route(self):
        graph = self.graph()
        chain = graph.chain(self.cid)
        edge = next(e for e in chain["edges"] if e["kind"] == "HTTP_RESPONSE")
        result = graph.evidence(self.cid, version=chain["indexVersion"], edge_id=edge["id"])
        text = "\n".join(b["content"] for b in result["blocks"])
        self.assertIn("http://localhost:8092", text)
        self.assertIn("GetMapping", text)
        self.assertIn(".uri", text)
        self.assertEqual("indexed_snapshot", result["source"])
        self.assertLessEqual(sum(b["end"] - b["start"] + 1 for b in result["blocks"]), 240)

    def test_stale_connection_stays_visibly_stale_and_original_is_not_relabelled(self):
        path = self.roots["alpha"]["channel"] / "application.yml"
        path.write_text(path.read_text().replace("8092", "9999"))
        graph = self.graph()
        chain = graph.chain(self.cid)
        edge = next(e for e in chain["edges"] if e["kind"] == "HTTP_RESPONSE")
        self.assertEqual("stale", edge["state"])
        result = graph.evidence(self.cid, version=chain["indexVersion"], edge_id=edge["id"])
        config = next(b for b in result["blocks"] if b["path"].endswith("channel/application.yml"))
        self.assertEqual("stale", config["state"])
        self.assertIn("8092", config["content"])

    def test_unresolved_http_target_is_a_gap_not_a_guessed_endpoint(self):
        path = self.roots["alpha"]["channel"] / "application.yml"
        path.write_text(path.read_text().replace("8092", "9999"))
        context = self.contexts["alpha"]
        prepare_index(context.data_root / "contract-index", self.sources["alpha"], self.configs["alpha"], "alpha")
        chain = self.graph().chain(self.cid)
        self.assertFalse(any(e["kind"] == "HTTP_RESPONSE" for e in chain["edges"]))
        self.assertTrue(any(n["type"] == "UNRESOLVED" for n in chain["nodes"]))
        self.assertEqual("HTTP_CLIENT", next(n["type"] for n in chain["nodes"] if n["label"] == "MiddleClient.detail"))
        self.assertFalse(any(n["label"] == "Middle.detail" for n in chain["nodes"]))

    def test_missing_index_does_not_create_any_structure_or_directory(self):
        missing = self.root / "not-prepared"
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("no parsing")):
            self.assertEqual("missing", ContractGraph(missing, "empty", {}).entries()["status"])
        self.assertFalse(missing.exists())

    def test_version_and_chain_membership_are_required_for_evidence(self):
        graph = self.graph()
        version = graph.chain(self.cid)["indexVersion"]
        with self.assertRaisesRegex(ValueError, "已更新"):
            graph.chain(self.cid, "old-version")
        with self.assertRaises(ValueError):
            graph.evidence(self.cid, version="", node_id="any")
        with self.assertRaises(ValueError):
            graph.evidence(self.cid, version=version, node_id="/etc/passwd")

    def test_project_identity_cannot_be_swapped_with_same_contract_ids(self):
        self.assertEqual(self.cid, self.graph("beta").chain(self.cid)["contractId"])
        with self.assertRaisesRegex(ValueError, "当前工程"):
            ContractGraph(self.contexts["alpha"].data_root / "contract-index", "beta", self.roots["beta"])

    def test_project_scoped_http_projection_and_evidence_never_parse_or_call_model(self):
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("no parsing")):
            server = make_server(str(self.root / "unused.db"), port=0, project_registry=self.registry)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                catalogs = {project: json.loads(urlopen(base + f"/api/projects/{project}/call-chains").read()) for project in ("alpha", "beta")}
                self.assertTrue(all(i["application"].startswith("alpha") for i in catalogs["alpha"]["items"]))
                self.assertTrue(all(i["application"].startswith("beta") for i in catalogs["beta"]["items"]))
                chain = json.loads(urlopen(base + f"/api/projects/alpha/call-chains/{self.cid}").read())
                node = chain["nodes"][0]
                params = urlencode({"version": chain["indexVersion"], "node": node["id"]})
                evidence = json.loads(urlopen(base + f"/api/projects/alpha/call-chains/{self.cid}/evidence?{params}").read())
                self.assertTrue(evidence["blocks"])
                with self.assertRaises(HTTPError) as mismatch:
                    urlopen(base + f"/api/projects/beta/call-chains/{self.cid}?version={chain['indexVersion']}")
                mismatch.exception.close()
            finally:
                server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

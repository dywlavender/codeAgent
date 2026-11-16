import json
import tempfile
import time
import unittest
from pathlib import Path

from business_code_agent.codeintel.ast.reader import AstQueryError, AstReader, AstRepository
from business_code_agent.codeintel.ast.service import AstService


class AstReaderTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        loan, other = root / ".data" / "repositories" / "loan", root / "other"
        loan.mkdir(parents=True)
        other.mkdir()
        self.loan = loan
        self.other = other
        self.source = loan / "Loan.java"
        self.source.write_text(
            "package demo;\n"
            "class Base { Loan loan; }\n"
            "interface Action { void submit(); }\n"
            "class Loan extends Base implements Action {\n"
            "    public void submit() { approve(); price(1); client.send(); }\n"
            "    public void approve() {}\n"
            "    public void price(int amount) {}\n"
            "    public void price(String amount) {}\n"
            "}\n",
            encoding="utf-8",
        )
        (other / "Loan.java").write_text(
            "package demo;\nclass Loan { void approve() {} void onlyOther() { approve(); } }\n",
            encoding="utf-8",
        )
        (other / "repay.ts").write_text("export function repay(amount: number) { return amount; }\n",
                                        encoding="utf-8")
        config = root / "project.json"
        config.write_text(json.dumps({
            "project": {"id": "ast-reader"},
            "repositories": [{"id": "z-loan", "localPath": ".data/repositories/loan"},
                             {"id": "a-other", "localPath": "other"}],
        }), encoding="utf-8")
        self.material = AstService(project_config=config, data_root=root / "ast")
        self.material.generate()
        deadline = time.monotonic() + 5
        while self.material.status()["status"] == "generating" and time.monotonic() < deadline:
            time.sleep(0.01)
        state = self.material.status()
        self.assertEqual("available", state["status"], state)
        self.version = state["currentVersionId"]
        self.version_root = self.material.versions_root / self.version
        self.repos = (AstRepository("z-loan", loan, "repos/z-loan"),
                      AstRepository("a-other", other, "repos/a-other"))
        self.reader = AstReader(self.material, self.version, self.repos)

    def match(self, query, repository="z-loan"):
        return self.reader.find_symbol(query, repository)["symbols"][0]

    def test_java_typescript_and_same_name_candidates_are_readable_locations(self):
        self.assertEqual("CLASS", self.match("Loan")["kind"])
        symbol = self.match("demo.Loan.approve()")
        self.assertEqual(("METHOD", "repos/z-loan/Loan.java", 6),
                         (symbol["kind"], symbol["path"], symbol["lineStart"]))
        self.assertEqual(symbol, self.reader.get_symbol(symbol["symbolId"])["symbol"])
        web = self.match("repay", "a-other")
        functions = self.reader.find_symbol("repay", "a-other")["symbols"]
        self.assertEqual("repos/a-other/repay.ts", web["path"])
        self.assertTrue(any(s["kind"] == "METHOD" and s["lineStart"] == 1 for s in functions))
        self.assertEqual({"a-other", "z-loan"}, {
            s["repositoryId"] for s in self.reader.find_symbol("demo.Loan.approve")["symbols"]
        })

    def test_relations_keep_reverse_edges_sites_and_ambiguous_targets_separate(self):
        owner = self.match("demo.Loan")
        types = self.reader.find_relations(owner["symbolId"])["relations"]
        self.assertEqual({("INHERITS", "demo.Base"), ("IMPLEMENTS", "demo.Action")},
                         {(edge["relationType"], edge["to"]["qualifiedName"]) for edge in types})
        implementation = self.reader.find_relations(
            self.match("demo.Action")["symbolId"], "IMPLEMENTS", "incoming",
        )["relations"]
        self.assertEqual([owner["symbolId"]], [edge["from"]["symbolId"] for edge in implementation])
        submit = self.match("demo.Loan.submit")
        calls = self.reader.find_relations(submit["symbolId"], "CALLS")
        self.assertEqual([("demo.Loan.approve", 5)], [
            (edge["to"]["qualifiedName"], edge["location"]["lineStart"]) for edge in calls["relations"]
        ])
        self.assertEqual([("demo.Loan.price", "ambiguous")], [
            (edge["targetName"], edge["status"]) for edge in calls["unresolved"]
        ])
        self.assertTrue(any(edge["targetName"].removesuffix("()") == "client.send" and edge["status"] == "raw"
                            for edge in calls["rawCalls"]))
        approve = self.match("demo.Loan.approve")
        incoming = self.reader.find_relations(approve["symbolId"], "CALLS", "incoming")
        self.assertEqual([submit["symbolId"]], [edge["from"]["symbolId"] for edge in incoming["relations"]])

    def test_scope_applies_to_repository_filter_and_explicit_symbol_ids(self):
        outside = self.match("demo.Loan.approve", "a-other")["symbolId"]
        scoped = AstReader(self.material, self.version, self.repos[:1])
        self.assertEqual({"z-loan"}, {
            s["repositoryId"] for s in scoped.find_symbol("approve")["symbols"]
        })
        with self.assertRaises(AstQueryError) as denied:
            scoped.find_symbol("approve", "a-other")
        self.assertEqual("denied", denied.exception.status)
        with self.assertRaises(AstQueryError) as missing:
            scoped.find_relations(outside)
        self.assertEqual("not_found", missing.exception.status)

    def test_changed_source_retires_live_query_without_rewriting_snapshot(self):
        database = self.version_root / "index.db"
        stamp = database.stat().st_mtime_ns
        self.reader.find_symbol("approve")
        self.assertEqual(stamp, database.stat().st_mtime_ns)
        self.source.write_text(self.source.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
        with self.assertRaises(AstQueryError) as stale:
            self.reader.find_symbol("approve")
        self.assertEqual("stale", stale.exception.status)
        self.assertTrue(self.material.list_documents(self.version)["items"])
        self.assertEqual(stamp, database.stat().st_mtime_ns)

    def test_legacy_version_mapping_uses_recorded_order_and_requires_an_index(self):
        path = self.version_root / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata.pop("repositoryMap")
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assertEqual("repos/z-loan/Loan.java", self.match("approve")["path"])
        (self.version_root / "index.db").unlink()
        with self.assertRaises(AstQueryError) as missing:
            self.reader.check()
        self.assertEqual("missing", missing.exception.status)

"""FTS5 symbol search over persisted code indexes and the SearchSymbols tool."""
import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.codeintel.analysis import JavaIndexer
from business_code_agent.codeintel.code_search import search_symbols
from business_code_agent.query.langchain_agent.code_search import search_symbols_tool
from business_code_agent.query.langchain_agent.runtime import LangChainRuntime
from business_code_agent.storage.schema import connect


class CodeSearchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        src = self.root / "src/main/java/demo"
        src.mkdir(parents=True)
        (src / "Pay.java").write_text('''package demo;
public class PayService {
    public void charge(int amount) {}
    public void refund(int amount) {}
}
''', encoding="utf-8")
        self.db_path = self.root / "index.db"
        self.db = connect(str(self.db_path))
        self.addCleanup(self.db.close)
        # Ingest populates the FTS table through schema triggers.
        JavaIndexer(self.db).ingest(str(src), "r1")

    def test_exact_fqn_query(self):
        rows = search_symbols(self.db, "demo.PayService.charge")
        self.assertEqual(1, len(rows))
        self.assertEqual("demo.PayService.charge", rows[0]["fqn"])
        self.assertTrue(rows[0]["exact"])
        self.assertEqual("charge", rows[0]["name"])
        self.assertIn("Pay.java", rows[0]["path"])

    def test_simple_name_and_case_insensitive(self):
        rows = search_symbols(self.db, "payservice")
        # The class and its members all contain the token; the class itself
        # is among them and every hit is a real containing symbol.
        self.assertIn("demo.PayService", {row["fqn"] for row in rows})
        self.assertTrue(all("payservice" in row["fqn"].casefold() for row in rows))
        rows = search_symbols(self.db, "REFUND")
        self.assertEqual("demo.PayService.refund", rows[0]["fqn"])
        self.assertTrue(rows[0]["exact"])

    def test_empty_query_returns_nothing(self):
        self.assertEqual([], search_symbols(self.db, "   "))

    def test_unknown_symbol_returns_empty_not_guesses(self):
        self.assertEqual([], search_symbols(self.db, "NotThere at all"))

    def test_top_k_caps_results(self):
        rows = search_symbols(self.db, "demo.PayService", top_k=1)
        self.assertEqual(1, len(rows))

    def test_ingest_delete_keeps_fts_in_sync(self):
        JavaIndexer(self.db).ingest(str(self.root / "src"), "r1")  # unchanged: no dup
        self.assertEqual(3, len(search_symbols(self.db, "demo", top_k=50)))
        (self.root / "src/main/java/demo/Pay.java").write_text(
            "package demo;\npublic class PayService {\n    public void charge(int a) {}\n}\n",
            encoding="utf-8")
        JavaIndexer(self.db).ingest(str(self.root / "src"), "r1")
        self.assertEqual([], search_symbols(self.db, "refund"))

    def test_tool_returns_matches_json(self):
        tool = search_symbols_tool(self.db_path)
        payload = json.loads(tool.invoke({"query": "demo.PayService.charge"}))
        self.assertEqual("demo.PayService.charge", payload["matches"][0]["fqn"])

    def test_runtime_rejects_missing_code_index_db(self):
        with self.assertRaises(Exception):
            LangChainRuntime(code_search_db=self.root / "missing.db")


if __name__ == "__main__":
    unittest.main()

"""Deterministic CALLS/INHERITS edge resolution and AST document anchors."""
import tempfile
import unittest
from pathlib import Path

from business_code_agent.codeintel.analysis import JavaIndexer
from business_code_agent.evaluation.ast_docs import generate_ast_documents
from business_code_agent.storage.schema import connect


BASE_JAVA = '''package demo;
public class Base {
    public void run() {}
    protected void helper() {}
}
'''

PAY_JAVA = '''package demo;
public class Pay extends Base implements Work, Comparable<Pay> {
    public void submit() { validate(); recalc(); base.run(); Helper.work(); }
    private void validate() {}
    private void recalc() {}
    private Base base = new Base();
}
'''

WORK_JAVA = '''package demo;
public interface Work {
    void submit();
}
'''

HELPER_JAVA = '''package demo;
public class Helper {
    public static void work() {}
}
'''


def _facts(db, fact_type):
    return {(row["subject"], row["target"]) for row in db.execute(
        "SELECT subject, target FROM code_fact WHERE fact_type=?", (fact_type,)).fetchall()}


class CodeEdgeResolutionTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.write("Base.java", BASE_JAVA)
        self.write("Pay.java", PAY_JAVA)
        self.write("Work.java", WORK_JAVA)
        self.write("Helper.java", HELPER_JAVA)
        self.db = connect(str(self.root / "t.db"))
        self.addCleanup(self.db.close)
        JavaIndexer(self.db).ingest(str(self.root), "r1")

    def write(self, name, content):
        (self.root / name).write_text(content, encoding="utf-8")

    def test_inherits_and_implements_resolve_only_unique_same_repository_parent(self):
        self.assertEqual({("demo.Pay", "demo.Base")}, _facts(self.db, "INHERITS"))
        self.assertEqual({("demo.Pay", "demo.Work")}, _facts(self.db, "IMPLEMENTS"))
        # Comparable is not indexed, so nothing is guessed for it.
        declared = _facts(self.db, "IMPLEMENTS_DECL")
        self.assertIn(("demo.Pay", "Comparable"), declared)
        self.assertIn(("demo.Pay", "Work"), declared)

    def test_calls_resolve_same_class_and_indexed_static_receiver(self):
        resolved = _facts(self.db, "CALLS")
        self.assertIn(("demo.Pay.submit", "demo.Pay.validate"), resolved)
        self.assertIn(("demo.Pay.submit", "demo.Pay.recalc"), resolved)
        self.assertIn(("demo.Pay.submit", "demo.Helper.work"), resolved)
        # The receiver "base" is a field, not an indexed type: omitted, not guessed.
        self.assertNotIn(("demo.Pay.submit", "demo.Base.run"), resolved)

    def test_reingest_is_idempotent(self):
        JavaIndexer(self.db).ingest(str(self.root), "r1")
        self.assertEqual(1, len(_facts(self.db, "INHERITS")))
        self.assertEqual(1, len(_facts(self.db, "IMPLEMENTS")))
        self.assertEqual(3, len(_facts(self.db, "CALLS")))

    def test_stale_edge_removed_when_target_disappears(self):
        self.write("Helper.java", "package demo;\npublic class Helper {}\n")
        JavaIndexer(self.db).ingest(str(self.root), "r1")
        resolved = _facts(self.db, "CALLS")
        self.assertNotIn(("demo.Pay.submit", "demo.Helper.work"), resolved)
        self.assertIn(("demo.Pay.submit", "demo.Pay.validate"), resolved)

    def test_ambiguous_type_name_is_not_resolved(self):
        (self.root / "demo").mkdir()
        self.write("demo/Helper.java", "package other;\npublic class Helper {}\n")
        JavaIndexer(self.db).ingest(str(self.root), "r1")
        resolved = _facts(self.db, "CALLS")
        self.assertNotIn(("demo.Pay.submit", "demo.Helper.work"), resolved)
        self.assertNotIn(("demo.Pay.submit", "other.Helper.work"), resolved)
        self.assertIn(("demo.Pay.submit", "demo.Pay.validate"), resolved)


class AstDocumentAnchorTest(unittest.TestCase):
    def test_documents_render_qualified_names_and_resolved_parents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "repo-0/src/main/java/demo/Pay.java"
            source.parent.mkdir(parents=True)
            source.write_text('''package demo;
public class Pay extends Base {
    public void submit() { validate(); }
    private void validate() {}
}
''', encoding="utf-8")
            parent = root / "repo-0/src/main/java/demo/Base.java"
            parent.write_text("package demo;\npublic class Base {}\n", encoding="utf-8")
            db_path = root / "out" / "index.db"
            docs = generate_ast_documents(root, [("payment-repo", "repo-0")], db_path=db_path)
            content = "\n".join(docs.values())
            self.assertIn("demo.Pay — CLASS", content)
            self.assertIn("继承: demo.Base", content)
            self.assertIn("demo.Pay.submit", content)
            # The persisted index keeps the resolved edges for later consumers.
            self.assertTrue(db_path.is_file())
            persisted = _facts(connect(str(db_path)), "CALLS")
            self.assertIn(("demo.Pay.submit", "demo.Pay.validate"), persisted)


if __name__ == "__main__":
    unittest.main()

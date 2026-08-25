from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from business_code_agent.schema import connect


class RetiredBusinessSchemaTest(unittest.TestCase):
    def test_new_database_has_only_canonical_function_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            db = connect(path)
            names = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            db.close()
        self.assertIn("business_function", names)
        self.assertNotIn("business_knowledge", names)
        self.assertNotIn("knowledge_relation", names)
        self.assertNotIn("knowledge_change", names)

    def test_existing_retired_rows_are_removed_on_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            db = connect(path)
            db.execute("CREATE TABLE business_knowledge (id TEXT PRIMARY KEY, evidence_id TEXT)")
            db.execute("INSERT INTO business_knowledge VALUES ('OLD-1', 'OLD-EV')")
            db.execute(
                "INSERT INTO evidence VALUES ('OLD-EV','MANUAL','OLD-1','1','old.txt',NULL,NULL,NULL,'x','old')"
            )
            db.execute("INSERT INTO evidence_lifecycle VALUES ('OLD-EV','ACTIVE',NULL,NULL,NULL)")
            db.commit()
            db.close()
            db = connect(path)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='business_knowledge'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM evidence WHERE id='OLD-EV'").fetchone())
            db.close()


if __name__ == "__main__":
    unittest.main()

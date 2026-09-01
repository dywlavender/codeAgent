from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from business_code_agent.schema import connect


class RetiredBusinessSchemaTest(unittest.TestCase):
    def test_new_database_has_canonical_baseline_without_retired_mapping_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            db = connect(path)
            names = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            db.close()
        self.assertIn("business_baseline_source", names)
        self.assertIn("business_entity", names)
        self.assertIn("business_relation_v2", names)
        self.assertNotIn("business_code_mapping", names)
        self.assertNotIn("business_code_mapping_observation", names)
        self.assertNotIn("business_function", names)
        self.assertNotIn("business_function_version", names)
        self.assertNotIn("knowledge_update_proposal", names)
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

    def test_existing_proposal_schema_is_removed_on_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            db = connect(path)
            db.execute("CREATE TABLE business_function (id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE business_function_version (id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE knowledge_update_proposal (id TEXT PRIMARY KEY)")
            db.commit()
            db.close()
            db = connect(path)
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("business_function", names)
            self.assertNotIn("business_function_version", names)
            self.assertNotIn("knowledge_update_proposal", names)
            db.close()

    def test_existing_mapping_schema_is_removed_on_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            raw = sqlite3.connect(path)
            raw.executescript(
                """
                CREATE TABLE business_code_mapping (id TEXT PRIMARY KEY);
                CREATE TABLE business_code_mapping_observation (id TEXT PRIMARY KEY);
                INSERT INTO business_code_mapping VALUES ('OLD-MAPPING');
                INSERT INTO business_code_mapping_observation VALUES ('OLD-OBSERVATION');
                """
            )
            raw.commit()
            raw.close()

            db = connect(path)
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("business_code_mapping", names)
            self.assertNotIn("business_code_mapping_observation", names)
            db.close()

    def test_existing_function_knowledge_schema_is_removed_on_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "knowledge.db")
            raw = sqlite3.connect(path)
            raw.executescript(
                """
                CREATE TABLE functional_knowledge (id TEXT PRIMARY KEY);
                CREATE TABLE functional_entry_anchor (id TEXT PRIMARY KEY);
                CREATE TABLE functional_key_table (id TEXT PRIMARY KEY);
                CREATE TABLE functional_retrieval_link (id TEXT PRIMARY KEY);
                CREATE TABLE functional_analysis (function_id TEXT PRIMARY KEY);
                INSERT INTO functional_knowledge VALUES ('OLD-FUNCTION');
                """
            )
            raw.commit()
            raw.close()

            db = connect(path)
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in (
                "functional_knowledge", "functional_entry_anchor", "functional_key_table",
                "functional_retrieval_link", "functional_analysis",
            ):
                self.assertNotIn(table, names)
            db.close()


if __name__ == "__main__":
    unittest.main()

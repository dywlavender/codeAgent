from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from business_code_agent.project_sync import sync_project
from business_code_agent.schema import connect


@unittest.skipUnless(shutil.which("git"), "Git is required")
class ProjectSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "remote source"
        self.source.mkdir()
        self._git(self.source, "init", "-b", "main")
        self._git(self.source, "config", "user.name", "Test User")
        self._git(self.source, "config", "user.email", "test@example.com")
        self._write_java('package demo; public class Entry { public void run() { value.setStatus("NEW"); } }')
        self._commit("initial")
        self.config = self.root / "project config.json"
        self.config.write_text(json.dumps({
            "project": {"id": "legacy-system", "name": "Legacy System"},
            "repositoryRoot": "managed repositories",
            "repositories": [{
                "id": "legacy-core",
                "gitUrl": str(self.source),
                "branch": "main",
            }],
        }), encoding="utf-8")
        self.db_path = self.root / "knowledge.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_clone_skip_update_and_fast_forward_before_incremental_index(self):
        first = sync_project(self.config, self.db_path)
        self.assertEqual("CLONED", first["repositories"][0]["syncStatus"])
        checkout = self.root / "managed repositories" / "legacy-core"
        self.assertTrue((checkout / ".git").is_dir())

        second = sync_project(self.config, self.db_path)
        self.assertEqual("UP_TO_DATE", second["repositories"][0]["syncStatus"])
        self.assertEqual(0, second["repositories"][0]["indexed"]["files"])

        self._write_java('package demo; public class Entry { public void run() { value.setStatus("DONE"); } }')
        self._commit("update")
        third = sync_project(self.config, self.db_path)
        self.assertEqual("UPDATED", third["repositories"][0]["syncStatus"])
        self.assertEqual(1, third["repositories"][0]["indexed"]["files"])
        self.assertIn('"DONE"', (checkout / "src" / "Entry.java").read_text(encoding="utf-8"))

        db = connect(str(self.db_path))
        try:
            self.assertEqual(1, db.execute("SELECT count(*) FROM repository WHERE id='legacy-core'").fetchone()[0])
        finally:
            db.close()

    def test_relative_local_git_source_resolves_against_config_and_reuses_clone(self):
        relative_config = self.root / "relative-config.json"
        relative_config.write_text(json.dumps({
            "project": {"id": "relative-system", "name": "Relative System"},
            "repositoryRoot": "managed-relative",
            "repositories": [{
                "id": "relative-core",
                "gitUrl": "remote source",
                "branch": "main",
            }],
        }), encoding="utf-8")
        db_path = self.root / "relative.db"

        first = sync_project(relative_config, db_path)
        self.assertEqual("CLONED", first["repositories"][0]["syncStatus"])
        second = sync_project(relative_config, db_path)
        self.assertEqual("UP_TO_DATE", second["repositories"][0]["syncStatus"])
        self.assertEqual(0, second["repositories"][0]["indexed"]["files"])

    def _write_java(self, content: str):
        path = self.source / "src" / "Entry.java"
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, message: str):
        self._git(self.source, "add", ".")
        self._git(self.source, "commit", "-m", message)

    @staticmethod
    def _git(folder: Path, *arguments: str):
        subprocess.run(
            ["git", "-C", str(folder), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()

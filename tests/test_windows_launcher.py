from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from business_code_agent.schema import connect


ROOT = Path(__file__).resolve().parent.parent


class WindowsLauncherTest(unittest.TestCase):
    def test_init_db_and_demo_are_idempotent_product_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "empty knowledge.db"
            result = self._cli("init-db", "--db", str(empty))
            self.assertEqual(("EMPTY", True), (result["mode"], result["initialized"]))
            db = connect(str(empty))
            self.assertEqual(0, db.execute("SELECT count(*) FROM repository").fetchone()[0])
            db.close()

            demo = Path(folder) / "demo knowledge.db"
            first = self._cli("init-demo", "--db", str(demo))
            second = self._cli("init-demo", "--db", str(demo))
            self.assertEqual(first["symbols"], second["symbols"])
            self.assertEqual(1, second["repositories"])
            db = connect(str(demo))
            self.assertEqual(1, db.execute("SELECT count(*) FROM requirement").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM business_function WHERE status='PUBLISHED'").fetchone()[0])
            db.close()

    def test_windows_entrypoints_have_safe_defaults_and_no_destructive_commands(self):
        batch = (ROOT / "start-windows.bat").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "start-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("ExecutionPolicy Bypass", batch)
        self.assertIn('ValidateSet("Demo", "Empty", "Repository")', script)
        self.assertIn('$HostAddress = "127.0.0.1"', script)
        self.assertIn("npm.cmd", script)
        self.assertIn("init-demo", script)
        self.assertIn("init-db", script)
        self.assertIn("sync-project", script)
        self.assertIn("ProjectConfig", script)
        self.assertIn("startup.database", script)
        self.assertIn("startup.port", script)
        self.assertIn('Join-Path $ProjectRoot "project.config.json"', script)
        self.assertIn("Wait-Process", script)
        self.assertNotIn("Remove-Item -Recurse", script)
        self.assertNotIn("ExecutionPolicy Unrestricted", script)
        self.assertNotIn("Set-ExecutionPolicy", script)

    def test_validation_project_config_supplies_startup_defaults(self):
        config = json.loads((ROOT / "project.config.json").read_text(encoding="utf-8"))
        self.assertEqual(".data/validation-project.db", config["startup"]["database"])
        self.assertEqual(8083, config["startup"]["port"])
        self.assertEqual("validation-project", config["project"]["id"])

    @staticmethod
    def _cli(*arguments):
        completed = subprocess.run(
            [sys.executable, "-m", "business_code_agent.cli", *arguments],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()

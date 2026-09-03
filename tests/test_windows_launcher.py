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
    def test_init_db_is_idempotent_product_command(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "empty knowledge.db"
            first = self._cli("init-db", "--db", str(empty))
            second = self._cli("init-db", "--db", str(empty))
            self.assertEqual(("EMPTY", True), (first["mode"], first["initialized"]))
            self.assertEqual(first, second)
            db = connect(str(empty))
            self.assertEqual(0, db.execute("SELECT count(*) FROM repository").fetchone()[0])
            db.close()

    def test_windows_entrypoints_have_safe_defaults_and_no_destructive_commands(self):
        batch = (ROOT / "start-windows.bat").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "start-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("ExecutionPolicy Bypass", batch)
        self.assertIn('ValidateSet("Empty", "Repository")', script)
        self.assertIn('$HostAddress = "127.0.0.1"', script)
        self.assertIn("npm.cmd", script)
        self.assertNotIn("init-demo", script)
        self.assertIn("init-db", script)
        self.assertIn("sync-project", script)
        self.assertIn("ProjectConfig", script)
        self.assertIn("startup.database", script)
        self.assertIn("startup.port", script)
        self.assertIn('Join-Path $ProjectRoot "project.config.json"', script)
        self.assertIn("Wait-Process", script)
        self.assertIn("Test-PortOpen", script)
        self.assertIn("Stop-PortProcesses", script)
        self.assertNotIn("Remove-Item -Recurse", script)
        self.assertNotIn("ExecutionPolicy Unrestricted", script)
        self.assertNotIn("Set-ExecutionPolicy", script)

    def test_validation_project_config_supplies_startup_defaults(self):
        config = json.loads(
            (ROOT / "tests" / "fixtures" / "windows" / "project.config.json").read_text(encoding="utf-8")
        )
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

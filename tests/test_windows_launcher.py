from __future__ import annotations

import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from business_code_agent.storage.schema import connect


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

    def test_launcher_and_sync_read_utf8_project_config_with_bom(self):
        launcher = runpy.run_path(str(ROOT / "scripts" / "launcher" / "start.py"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "本地 快照"
            source.mkdir()
            (source / "Sample.java").write_text("class Sample {}", encoding="utf-8")
            config = root / "project.config.json"
            startup = {"database": "运行 数据/知识库.db", "port": 8083, "python": "已有 环境/Scripts/python.exe"}
            payload = {
                "project": {"id": "validation-project", "name": "中文工程"},
                "startup": startup,
                "repositories": [{"id": "sample", "gitUrl": "unused", "localPath": source.name}],
            }
            config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

            self.assertEqual(startup, launcher["_project_startup"](config))
            database = root / startup["database"]
            database.parent.mkdir()
            self._cli("init-db", "--db", str(database))
            result = self._cli("sync-project", "--config", str(config), "--db", str(database))
            self.assertEqual("中文工程", result["projectName"])
            self.assertEqual(source.resolve(), Path(result["repositories"][0]["path"]).resolve())

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
            [sys.executable, "-X", "utf8", "-m", "business_code_agent.cli", *arguments],
            cwd=ROOT, check=True, capture_output=True, encoding="utf-8",
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()

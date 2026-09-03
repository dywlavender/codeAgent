from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_builder_module():
    path = ROOT / "scripts" / "build_offline_bundle.py"
    spec = importlib.util.spec_from_file_location("offline_bundle_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OfflineDeploymentTest(unittest.TestCase):
    def test_intranet_git_config_is_portable_and_does_not_bundle_repository(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "internal"},
                "repositoryRoot": "D:/build-machine/checkouts",
                "repositories": [{
                    "id": "core",
                    "gitUrl": "ssh://git@git.company.local/team/core.git",
                    "branch": "main",
                    "localPath": "D:/build-machine/checkouts/core",
                }],
            }), encoding="utf-8")
            staging = root / "staging"
            staging.mkdir()

            count, mode = builder._prepare_project_config(
                config, staging, bundle_repositories=False,
            )

            packaged = json.loads((staging / "project.config.json").read_text(encoding="utf-8"))
            self.assertEqual((1, "intranet-git"), (count, mode))
            self.assertEqual(".data/repositories", packaged["repositoryRoot"])
            self.assertNotIn("localPath", packaged["repositories"][0])
            self.assertEqual(
                "ssh://git@git.company.local/team/core.git",
                packaged["repositories"][0]["gitUrl"],
            )
            self.assertFalse((staging / "offline-repositories").exists())

    def test_local_git_source_automatically_becomes_bundled_snapshot(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = root / "sample-repository"
            source = repository / "src" / "Entry.java"
            source.parent.mkdir(parents=True)
            source.write_text("package sample; public class Entry {}", encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "sample"},
                "repositories": [{"id": "sample", "gitUrl": "sample-repository"}],
            }), encoding="utf-8")
            staging = root / "staging"
            staging.mkdir()

            count, mode = builder._prepare_project_config(
                config, staging, bundle_repositories=False,
            )

            packaged = json.loads((staging / "project.config.json").read_text(encoding="utf-8"))
            self.assertEqual((1, "bundled-snapshot"), (count, mode))
            self.assertEqual("offline-repositories/sample", packaged["repositories"][0]["localPath"])
            self.assertTrue((staging / "offline-repositories" / "sample" / "src" / "Entry.java").is_file())

    def test_launchers_support_download_and_strict_offline_dependency_modes(self):
        linux = (ROOT / "start-offline-linux.sh").read_text(encoding="utf-8")
        windows = (ROOT / "scripts" / "start-offline-windows.ps1").read_text(encoding="utf-8")
        for script in (linux, windows):
            self.assertIn("--no-index", script)
            self.assertIn("wheelhouse", script)
            self.assertIn("dependencyMode", script.replace("DEPENDENCY_MODE", "dependencyMode"))
            self.assertIn("sync-project", script)
            self.assertNotIn("init-demo", script)
            self.assertNotIn("-Demo", script)
            self.assertNotIn("npm ci", script)
            self.assertNotIn("npm run", script)
        self.assertIn("port_is_open", linux)
        self.assertIn("stop_port_processes", linux)
        self.assertIn("Test-PortOpen", windows)
        self.assertIn("Stop-PortProcesses", windows)
        self.assertIn("ExecutionPolicy Bypass", (ROOT / "start-offline-windows.bat").read_text(encoding="utf-8"))

    def test_canonical_build_entrypoints_default_to_all_targets(self):
        shell = (ROOT / "build-offline.sh").read_text(encoding="utf-8")
        batch = (ROOT / "build-offline.bat").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts" / "build-offline-windows.ps1").read_text(encoding="utf-8")
        builder = (ROOT / "scripts" / "build_offline_bundle.py").read_text(encoding="utf-8")
        self.assertIn("build_offline_bundle.py", shell)
        self.assertIn("build-offline-windows.ps1", batch)
        self.assertNotIn("--target windows", powershell)
        self.assertIn('default="all"', builder)


if __name__ == "__main__":
    unittest.main()

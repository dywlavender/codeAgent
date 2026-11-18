from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_builder_module():
    path = ROOT / "scripts" / "offline" / "build_offline_bundle.py"
    spec = importlib.util.spec_from_file_location("project_package_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProjectDeploymentTest(unittest.TestCase):
    def test_windows_package_contains_source_and_normal_launcher(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as folder:
            staging = Path(folder) / "package"
            staging.mkdir()
            builder._copy_project(staging, target="windows")

            self.assertTrue((staging / "business_code_agent" / "cli.py").is_file())
            self.assertTrue((staging / "frontend" / "package.json").is_file())
            self.assertTrue((staging / "frontend" / "package-lock.json").is_file())
            self.assertTrue((staging / "frontend" / "index.html").is_file())
            self.assertTrue((staging / "frontend" / "vite.config.js").is_file())
            self.assertTrue((staging / "frontend" / "src" / "main.jsx").is_file())
            self.assertTrue((staging / "start.sh").is_file())
            self.assertTrue((staging / "scripts" / "launcher" / "start.sh").is_file())
            self.assertTrue((staging / "scripts" / "launcher" / "start.py").is_file())
            self.assertTrue((staging / "pyproject.toml").is_file())
            self.assertTrue((staging / "project.config.example.json").is_file())
            self.assertEqual(
                {
                    "business_code_agent",
                    "frontend",
                    "pyproject.toml",
                    "project.config.example.json",
                    ".env.example",
                    "README.md",
                    "docs",
                    "start.sh",
                    "scripts",
                },
                {path.name for path in staging.iterdir()},
            )

            self.assertFalse((staging / "frontend" / "dist").exists())
            self.assertFalse((staging / "frontend" / "node_modules").exists())
            self.assertFalse((staging / "frontend" / "tests").exists())
            self.assertFalse((staging / ".data").exists())
            self.assertFalse((staging / "project.config.json").exists())
            self.assertFalse((staging / ".env").exists())
            self.assertFalse((staging / "examples").exists())
            self.assertFalse((staging / "knowledge").exists())
            self.assertFalse((staging / "tests").exists())

    def test_builder_creates_a_project_source_zip(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as folder:
            archive = builder._build_package("windows", Path(folder))
            self.assertEqual("business-code-agent-windows-project.zip", archive.name)
            package_root = "business-code-agent-windows-project/"
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                readme = package.read(package_root + "README.md").decode("utf-8")
                self.assertIn("bash ./start.sh", readme)
                for script in ("start.sh", "scripts/launcher/start.sh"):
                    self.assertNotIn(b"\r\n", package.read(package_root + script))
                for manual in ("docs/spec/offline-deployment.md", "docs/spec/windows-usage.md"):
                    self.assertIn(f"]({manual})", readme)
                    self.assertIn(package_root + manual, names)
                self.assertIn(package_root + ".env.example", names)
                self.assertNotIn(package_root + ".env", names)

            self.assertFalse(any(name.endswith((".bat", ".ps1")) for name in names))
            self.assertIn(package_root + "scripts/launcher/start.py", names)
            self.assertIn(
                "business-code-agent-windows-project/frontend/package.json",
                names,
            )
            self.assertIn(
                "business-code-agent-windows-project/frontend/src/main.jsx",
                names,
            )
            self.assertIn(
                "business-code-agent-windows-project/business_code_agent/cli.py",
                names,
            )
            self.assertNotIn(
                "business-code-agent-windows-project/frontend/dist/index.html",
                names,
            )
            for directory in ("examples", "knowledge", "tests"):
                self.assertFalse(any(name.startswith(package_root + directory + "/") for name in names))
            self.assertFalse(any("/records/" in name for name in names))
            self.assertFalse(any("wheelhouse" in name for name in names))
            self.assertFalse(any("offline-repositories" in name for name in names))

    def test_linux_package_contains_install_manual_and_launcher(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as folder:
            archive = builder._build_package("linux", Path(folder))
            self.assertEqual("business-code-agent-linux-project.zip", archive.name)
            package_root = "business-code-agent-linux-project/"
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                readme = package.read(package_root + "README.md").decode("utf-8")
                self.assertIn("bash ./start.sh", readme)
                manual = "docs/spec/offline-deployment-mac-linux.md"
                self.assertIn(f"]({manual})", readme)
                self.assertIn(package_root + manual, names)
                self.assertIn(package_root + ".env.example", names)
                self.assertIn(package_root + "start-mac.sh", names)
                self.assertNotIn(package_root + ".env", names)
                for script in ("start.sh", "start-mac.sh", "scripts/launcher/start.sh"):
                    self.assertNotIn(b"\r\n", package.read(package_root + script))
            self.assertFalse(any(name.endswith((".bat", ".ps1")) for name in names))
            self.assertFalse(any("/records/" in name for name in names))

    def test_builder_is_source_packager_not_dependency_bundler(self):
        builder_source = (ROOT / "scripts" / "offline" / "build_offline_bundle.py").read_text(
            encoding="utf-8",
        )
        self.assertIn('default="windows"', builder_source)
        self.assertNotIn("bundle-python-wheels", builder_source)
        self.assertNotIn("bundle-repositories", builder_source)
        self.assertNotIn("wheelhouse", builder_source)

    def test_shell_build_entrypoint_uses_the_project_packager(self):
        shell = (ROOT / "scripts" / "offline" / "build-offline.sh").read_text(encoding="utf-8")
        self.assertIn("build_offline_bundle.py", shell)

    def test_shared_launcher_installs_current_project_dependencies(self):
        launcher = (ROOT / "scripts" / "launcher" / "start.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("Python 3.11 或更高版本", launcher)
        self.assertIn("Node.js 20 or later", launcher)
        self.assertIn('"npm.cmd"', launcher)
        self.assertIn('"ci", "--no-audit", "--no-fund"', launcher)
        self.assertIn('"run", "build"', launcher)
        self.assertIn('[*install, ".[tree-sitter]"]', launcher)
        self.assertIn('[*install, "."]', launcher)
        self.assertIn("init-db", launcher)
        self.assertIn("sync-project", launcher)
        self.assertNotIn("init-demo", launcher)
        self.assertNotIn("claude", launcher.lower())


if __name__ == "__main__":
    unittest.main()

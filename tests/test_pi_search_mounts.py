from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


NODE = shutil.which("node")
MODULE = (Path(__file__).parent.parent / "business_code_agent" / "query" / "agent"
          / "pi" / "extension" / "search_mounts.js")


@unittest.skipUnless(NODE, "node is required for the Pi extension helper tests")
class PiSearchMountsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.workspace = (self.root / "workspace").resolve()
        self.workspace.mkdir()
        self.repo = (self.root / "sources" / "a").resolve()
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "Main.java").write_text("class Main {}\n", encoding="utf-8")
        self.outside = (self.root / "outside").resolve()
        self.outside.mkdir()
        self.mounts = {"repos/a": str(self.repo)}

    def run_js(self, body: str):
        script = (
            f"const m = await import({json.dumps(MODULE.as_uri())});\n"
            f"const workspace = {json.dumps(str(self.workspace))};\n"
            f"const repo = {json.dumps(str(self.repo))};\n"
            f"const outside = {json.dumps(str(self.outside))};\n"
            f"{body}"
        )
        result = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_parse_mounts_drops_invalid_and_outside_roots(self):
        raw = json.dumps({**self.mounts, "repos/outside": str(self.outside),
                          "bad": 3, "../escape": str(self.repo)})
        value = self.run_js(
            f"console.log(JSON.stringify(m.parseMounts({json.dumps(raw)}, "
            f"[workspace, repo])));")
        self.assertEqual([{"prefix": "repos/a", "real": str(self.repo.resolve())}], value)

    def test_workspace_root_searches_workspace_and_each_mount_without_follow(self):
        value = self.run_js(
            f"console.log(JSON.stringify(m.resolveSearchTargets(workspace, "
            f"m.parseMounts({json.dumps(json.dumps(self.mounts))}, [workspace, repo]), '.')));")
        self.assertEqual(
            [{"cwd": str(self.workspace.resolve()), "target": ".", "prefix": ""},
             {"cwd": str(self.repo.resolve()), "target": ".", "prefix": "repos/a"}],
            value["targets"],
        )

    def test_repository_subpath_maps_to_the_real_root(self):
        value = self.run_js(
            f"console.log(JSON.stringify(m.resolveSearchTargets(workspace, "
            f"m.parseMounts({json.dumps(json.dumps(self.mounts))}, [workspace, repo]), "
            f"'repos/a/src')));")
        self.assertEqual([{"cwd": str(self.repo.resolve()), "target": "src",
                           "prefix": "repos/a"}], value["targets"])

    def test_parent_directory_still_searches_child_mounts(self):
        value = self.run_js(
            f"console.log(JSON.stringify(m.resolveSearchTargets(workspace, "
            f"m.parseMounts({json.dumps(json.dumps(self.mounts))}, [workspace, repo]), "
            f"'repos')));")
        self.assertEqual(
            [{"cwd": str(self.workspace.resolve()), "target": "repos", "prefix": ""},
             {"cwd": str(self.repo.resolve()), "target": ".", "prefix": "repos/a"}],
            value["targets"],
        )

    def test_absolute_real_path_maps_back(self):
        value = self.run_js(
            f"console.log(JSON.stringify(m.resolveSearchTargets(workspace, "
            f"m.parseMounts({json.dumps(json.dumps(self.mounts))}, [workspace, repo]), "
            f"{json.dumps(str(self.repo / 'src' / 'Main.java'))})));")
        self.assertEqual([{"cwd": str(self.repo.resolve()), "target": "src/Main.java",
                           "prefix": "repos/a"}], value["targets"])

    def test_unauthorized_path_is_rejected(self):
        value = self.run_js(
            f"console.log(JSON.stringify(m.resolveSearchTargets(workspace, [], "
            f"{json.dumps(str(self.outside))})));")
        self.assertEqual([], value["targets"])
        self.assertIn("不在本轮授权范围", value["reason"])

    def test_result_lines_are_mapped_back_to_workspace_paths(self):
        value = self.run_js(
            "console.log(JSON.stringify([\n"
            "  m.mapResultLine('src/Main.java:3:class Main {}', 'repos/a'),\n"
            "  m.mapResultLine('./src/Main.java', 'repos/a'),\n"
            "  m.mapResultLine('--', 'repos/a'),\n"
            "  m.mapResultLine('README.md', ''),\n"
            "]));")
        self.assertEqual([
            "repos/a/src/Main.java:3:class Main {}",
            "repos/a/src/Main.java",
            "--",
            "README.md",
        ], value)


if __name__ == "__main__":
    unittest.main()

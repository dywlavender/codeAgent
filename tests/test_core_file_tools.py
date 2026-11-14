from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.capabilities import FileCapability, prepare_capabilities
from business_code_agent.query.context import RunContext
from business_code_agent.query.materials import MaterialManifest
from business_code_agent.query.profile import CORE
from business_code_agent.query.workspace import WorkspaceManager


class CoreFileToolsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.sources = {}
        for repository_id in ("channel", "decision"):
            source = self.root / "sources" / repository_id
            source.mkdir(parents=True)
            self.sources[repository_id] = source
        (self.sources["channel"] / "src/main/java").mkdir(parents=True)
        (self.sources["channel"] / "src/main/java/ChannelApplication.java").write_text(
            "class ChannelApplication {\n"
            "  void applyWithdraw() {\n"
            "    checkAmount();\n"
            "  }\n"
            "}\n", encoding="utf-8")
        (self.sources["channel"] / "src/main/resources").mkdir(parents=True)
        (self.sources["channel"] / "src/main/resources/application.yml").write_text(
            "withdraw:\n  enabled: true\n", encoding="utf-8")
        (self.sources["channel"] / "src/main/resources/mapper").mkdir()
        (self.sources["channel"] / "src/main/resources/mapper/WithdrawMapper.xml").write_text(
            "<mapper><update id=\"withdraw\">update withdraw set state = 2</update></mapper>\n",
            encoding="utf-8")
        (self.sources["channel"] / "src/main/resources/withdraw.sql").write_text(
            "update withdraw set state = 2 where id = #{id};\n", encoding="utf-8")
        (self.sources["channel"] / "src/main/resources/app.properties").write_text(
            "withdraw.retry=3\n", encoding="utf-8")
        (self.sources["channel"] / "web").mkdir()
        (self.sources["channel"] / "web/WithdrawApply.vue").write_text(
            "<template>withdraw</template>\n", encoding="utf-8")
        (self.sources["channel"] / "web/withdraw.ts").write_text(
            "export const withdraw = true;\n", encoding="utf-8")
        (self.sources["channel"] / "service.py").write_text(
            "def withdraw():\n    return True\n", encoding="utf-8")
        (self.sources["channel"] / ".git").mkdir()
        (self.sources["channel"] / ".git/config").write_text("[core]\n", encoding="utf-8")
        (self.sources["decision"] / "Decision.java").write_text(
            "class Decision {\n  // withdraw decision\n  boolean allowed = true;\n}\n",
            encoding="utf-8")
        (self.root / "requirements").mkdir()
        (self.root / "requirements/request.md").write_text(
            "提款需要校验金额。\n", encoding="utf-8")
        config = self.root / "project.json"
        config.write_text(json.dumps({
            "project": {"id": "core-files", "name": "Core Files"},
            "repositories": [
                {"id": repository_id, "gitUrl": "unused", "localPath": str(source)}
                for repository_id, source in self.sources.items()
            ],
        }), encoding="utf-8")
        manager = WorkspaceManager(project_config=config, workspace_root=self.root / "workspaces")
        self.workspace = manager.prepare_core(run_id="RUN-files",
                                              repository_ids=["channel", "decision"])
        self.project = ProjectContext.legacy(self.root / "db.sqlite", config)
        manifest = MaterialManifest.for_core(self.workspace.path, self.workspace.repository_mounts)
        self.context = RunContext(
            run_id="RUN-files", project=self.project, profile=CORE,
            repository_ids=("channel", "decision"), workspace=self.workspace.path,
            materials=manifest.sources, session_id=None,
        )
        self.prepared = prepare_capabilities(self.context)
        self.tools = {tool.name: tool for tool in self.prepared.tools}

    def invoke(self, name, **arguments):
        return json.loads(self.tools[name].invoke(arguments))

    def test_capability_registers_only_file_tools_and_marks_enhancements_disabled(self):
        self.assertEqual(["Read", "Glob", "Grep"], list(self.tools))
        statuses = {status.capability: status for status in self.prepared.statuses}
        self.assertEqual(("required", "ready", ("Read", "Glob", "Grep")),
                         (statuses["file_tools"].policy, statuses["file_tools"].status,
                          statuses["file_tools"].names))
        for capability in ("search_code", "contract_trace", "business_context", "ast",
                           "code_map", "hybrid_search"):
            self.assertEqual(("disabled", "disabled"),
                             (statuses[capability].policy, statuses[capability].status))

    def test_glob_lists_authorized_repositories_and_common_source_types(self):
        directories = self.invoke("Glob", pattern="repos/*", kind="directories")
        self.assertEqual(["repos/channel", "repos/decision"], directories["paths"])
        all_files = self.invoke("Glob", pattern="**/*", limit=200)
        paths = all_files["paths"]
        for expected in (
            "repos/channel/src/main/java/ChannelApplication.java",
            "repos/channel/src/main/resources/application.yml",
            "repos/channel/src/main/resources/mapper/WithdrawMapper.xml",
            "repos/channel/src/main/resources/withdraw.sql",
            "repos/channel/src/main/resources/app.properties",
            "repos/channel/web/WithdrawApply.vue",
            "repos/channel/web/withdraw.ts",
            "repos/channel/service.py",
            "repos/decision/Decision.java",
            "requirements/request.md",
        ):
            self.assertIn(expected, paths)
        self.assertNotIn("repos/channel/.git/config", paths)

    def test_glob_pagination_is_stable(self):
        first = self.invoke("Glob", pattern="**/*.java", limit=1)
        self.assertEqual(1, len(first["paths"]))
        self.assertTrue(first["truncated"])
        self.assertEqual(1, first["nextOffset"])
        second = self.invoke("Glob", pattern="**/*.java", limit=1, offset=1)
        self.assertNotEqual(first["paths"], second["paths"])

    def test_grep_scopes_to_repository_and_supports_pagination(self):
        matches = self.invoke("Grep", pattern="withdraw", limit=2)
        self.assertEqual(2, len(matches["matches"]))
        self.assertTrue(matches["truncated"])
        self.assertEqual(2, matches["nextOffset"])
        scoped = self.invoke("Grep", pattern="withdraw", path="repos/channel")
        self.assertTrue(scoped["matches"])
        self.assertFalse(any(item["path"].startswith("repos/decision/") for item in scoped["matches"]))
        case_sensitive = self.invoke("Grep", pattern="WITHDRAW", path="repos/channel")
        self.assertEqual([], case_sensitive["matches"])
        insensitive = self.invoke("Grep", pattern="WITHDRAW", path="repos/channel",
                                  case_sensitive=False)
        self.assertTrue(insensitive["matches"])

    def test_grep_files_mode_aggregates_and_paginates(self):
        hot = self.workspace.path / "repos/channel/src/main/java/Hot.java"
        hot.write_text("\n".join(["withdraw"] * 9) + "\n", encoding="utf-8")
        full = self.invoke("Grep", pattern="withdraw", output_mode="files", limit=120)
        self.assertTrue(full["files"])
        self.assertFalse(full["truncated"])
        paths = [item["path"] for item in full["files"]]
        self.assertEqual(sorted(paths), paths)
        hot_entry = next(item for item in full["files"] if item["path"].endswith("/Hot.java"))
        self.assertEqual(9, hot_entry["matches"])
        self.assertEqual(5, len(hot_entry["sampleLines"]))
        first = self.invoke("Grep", pattern="withdraw", output_mode="files", limit=2)
        self.assertEqual(2, len(first["files"]))
        self.assertTrue(first["truncated"])
        self.assertEqual(2, first["nextOffset"])
        seen = [item["path"] for item in first["files"]]
        offset = first["nextOffset"]
        while offset is not None:
            page = self.invoke("Grep", pattern="withdraw", output_mode="files",
                               limit=2, offset=offset)
            seen.extend(item["path"] for item in page["files"])
            offset = page["nextOffset"]
        self.assertEqual(paths, seen)
        self.assertEqual(len(seen), len(set(seen)))

    def test_grep_scope_starts_at_subdirectory_and_preserves_authorization(self):
        scope = "repos/channel/src/main/resources"
        files = self.invoke("Grep", pattern="withdraw", path=scope, output_mode="files")
        self.assertTrue(files["files"])
        self.assertTrue(all(item["path"].startswith(scope + "/") for item in files["files"]))
        single = self.invoke("Grep", pattern="withdraw", path="repos/channel/web/withdraw.ts")
        self.assertEqual(["repos/channel/web/withdraw.ts"],
                         [item["path"] for item in single["matches"]])
        for path, message in (
            ("repos/middle", "不在本轮授权"),
            ("repos/../requirements", ".."),
            ("/etc/passwd", "绝对路径"),
        ):
            with self.subTest(path=path):
                result = self.invoke("Grep", pattern="withdraw", path=path)
                self.assertIn(message, result["error"])
        with patch("business_code_agent.query.tools.files.os.walk", wraps=os.walk) as walk:
            self.invoke("Grep", pattern="withdraw", path=scope)
        starts = [str(call.args[0]) for call in walk.call_args_list if call.args]
        self.assertTrue(any(value.endswith("src/main/resources") for value in starts))

    def test_grep_matches_mode_adds_bounded_context(self):
        path = "repos/channel/src/main/java/ChannelApplication.java"
        default = self.invoke("Grep", pattern="checkAmount", path=path)
        self.assertNotIn("before", default["matches"][0])
        expanded = self.invoke("Grep", pattern="checkAmount", path=path, context=2)
        item = expanded["matches"][0]
        self.assertEqual(3, item["line"])
        self.assertEqual([1, 2], [row["line"] for row in item["before"]])
        self.assertEqual([4, 5], [row["line"] for row in item["after"]])
        self.assertIn("void applyWithdraw", item["before"][1]["text"])
        oversized = self.invoke("Grep", pattern="checkAmount", path=path, context=99)
        self.assertEqual(2, len(oversized["matches"][0]["before"]))
        self.assertEqual(2, len(oversized["matches"][0]["after"]))

    def test_grep_rejects_unknown_output_mode(self):
        result = self.invoke("Grep", pattern="withdraw", output_mode="content")
        self.assertIn("output_mode", result["error"])

    def test_read_returns_real_content_lines_and_requirements(self):
        read = self.invoke("Read", path="repos/channel/src/main/java/ChannelApplication.java",
                           offset=2, limit=2)
        self.assertEqual(("repos/channel/src/main/java/ChannelApplication.java", 2, 3),
                         (read["path"], read["lineStart"], read["lineEnd"]))
        self.assertEqual("2:   void applyWithdraw() {\n3:     checkAmount();", read["content"])
        self.assertTrue(read["truncated"])
        self.assertEqual(4, read["nextLine"])
        requirement = self.invoke("Read", path="requirements/request.md")
        self.assertEqual("1: 提款需要校验金额。", requirement["content"])

    def test_core_tools_reject_unauthorized_and_absolute_paths(self):
        for path, message in (
            ("/etc/passwd", "绝对路径"),
            ("repos/../requirements/request.md", ".."),
            ("repos/middle/Main.java", "不在本轮授权"),
            ("knowledge/business-context/withdraw.md", "不在本轮授权"),
            ("repos/channel/.git/config", "不在本轮授权"),
        ):
            with self.subTest(path=path):
                result = self.invoke("Read", path=path)
                self.assertIn(message, result["error"])
        self.assertFalse((self.workspace.path / "knowledge").exists())

    def test_failed_capability_does_not_register_tools(self):
        missing = RunContext(
            run_id="RUN-missing", project=self.project, profile=CORE,
            repository_ids=("middle",), workspace=self.workspace.path,
            materials=tuple(source for source in self.context.materials
                            if source.repository_id != "channel") + tuple(
                type(source)("source", "代码仓库 middle", "repos/middle", None, "missing",
                             repository_id="middle") for source in self.context.materials[:1]),
            session_id=None,
        )
        prepared = prepare_capabilities(missing)
        self.assertEqual((), prepared.tools)
        self.assertEqual("unavailable", prepared.statuses[0].status)
        self.assertIn("middle", prepared.statuses[0].reason)

    def test_core_tool_modules_do_not_import_retrieval_or_legacy_agent(self):
        code = (
            "import sys\n"
            "import business_code_agent.query.tools.files\n"
            "import business_code_agent.query.capabilities\n"
            "blocked = [name for name in sys.modules if name.startswith("
            "'business_code_agent.codeintel') or name.startswith("
            "'business_code_agent.query.langchain_agent')]\n"
            "print(','.join(blocked))\n"
            "raise SystemExit(bool(blocked))\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()

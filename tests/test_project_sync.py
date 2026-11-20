from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from business_code_agent.codeintel.sync import (
    ProjectSyncError,
    _is_allowed_aoci_asset,
    _uncommitted_changes,
    sync_project,
)
from business_code_agent.storage.schema import connect


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

    def test_unused_git_url_uses_existing_local_snapshot_without_cloning(self):
        source = self.root / "local snapshot"
        source.mkdir()
        (source / "Entry.java").write_text(
            "package demo; public class Entry { public void run() {} }",
            encoding="utf-8",
        )
        config = self.root / "local-config.json"
        config.write_text(json.dumps({
            "project": {"id": "local-system", "name": "Local System"},
            "repositories": [{
                "id": "local-core", "gitUrl": "unused", "localPath": "local snapshot",
            }],
        }), encoding="utf-8")

        result = sync_project(config, self.root / "local.db")

        self.assertEqual("LOCAL_SNAPSHOT", result["repositories"][0]["syncStatus"])
        self.assertGreater(result["repositories"][0]["indexed"]["symbols"], 0)

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


@unittest.skipUnless(shutil.which("git"), "Git is required")
class AociAwareUncommittedCheckTest(unittest.TestCase):
    """AOCI 资产不再永久阻塞同步，但真实本地改动仍然拒绝。

    仓库搭建方式沿用 ``ProjectSyncTest``：临时目录里建一个 bare 之外的普通
    仓库，再用 ``sync_project`` 克隆到工程目录下检查行为。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "aoci source"
        self.source.mkdir()
        self._git(self.source, "init", "-b", "main")
        self._git(self.source, "config", "user.name", "Test User")
        self._git(self.source, "config", "user.email", "test@example.com")
        entry = self.source / "src" / "Entry.java"
        entry.parent.mkdir()
        entry.write_text(
            'package demo; public class Entry { public void run() {} }', encoding="utf-8",
        )
        self._commit("initial")
        self.config = self.root / "aoci project config.json"
        self.config.write_text(json.dumps({
            "project": {"id": "aoci-system", "name": "AOCI System"},
            "repositoryRoot": "managed aoci repositories",
            "repositories": [{
                "id": "aoci-core",
                "gitUrl": str(self.source),
                "branch": "main",
            }],
        }), encoding="utf-8")
        self.db_path = self.root / "aoci.db"
        self.checkout = self.root / "managed aoci repositories" / "aoci-core"
        # 第一次同步只负责把仓库克隆下来，后续断言都在 clone 上做。
        self.assertEqual("CLONED", sync_project(self.config, self.db_path)["repositories"][0]["syncStatus"])

    def tearDown(self):
        self.temp.cleanup()

    def _write_aoci_assets(self) -> None:
        """按 ``aoci init`` 的真实产物写入未提交资产。"""
        (self.checkout / "aoci.txt").write_text("#AOCI-ROOT-MANIFEST: 1\n", encoding="utf-8")
        (self.checkout / "aoci.meta.txt").write_text("#Format-Version: meta-v1\n", encoding="utf-8")
        (self.checkout / "aoci.code.txt").write_text("#Format-Version: object-fras-v2\n", encoding="utf-8")
        (self.checkout / "aoci.database.txt").write_text("#Format-Version: database-v1\n", encoding="utf-8")
        (self.checkout / "AGENTS.md").write_text("# 协作指引\n", encoding="utf-8")
        (self.checkout / ".gitattributes").write_text("*.sh text eol=lf\n", encoding="utf-8")
        aoci_directory = self.checkout / ".aoci"
        aoci_directory.mkdir(exist_ok=True)
        (aoci_directory / "config.json").write_text("{}\n", encoding="utf-8")

    def test_aoci_assets_alone_still_sync(self):
        self._write_aoci_assets()

        result = sync_project(self.config, self.db_path)

        self.assertEqual("UP_TO_DATE", result["repositories"][0]["syncStatus"])

    def test_business_source_modification_is_still_rejected(self):
        self._write_aoci_assets()
        (self.checkout / "src" / "Entry.java").write_text(
            'package demo; public class Entry { public void run() { int local = 1; } }',
            encoding="utf-8",
        )

        with self.assertRaises(ProjectSyncError) as caught:
            sync_project(self.config, self.db_path)

        message = str(caught.exception)
        self.assertIn("src/Entry.java", message)
        self.assertIn("aoci-core", message)
        # AOCI 资产被放行，错误里不应再把它们当成问题路径列出来。
        self.assertNotIn("aoci.meta.txt", message)

    def test_rename_is_still_rejected_in_aoci_repository(self):
        self._write_aoci_assets()
        self._git(self.checkout, "mv", "src/Entry.java", "src/Renamed.java")

        with self.assertRaises(ProjectSyncError) as caught:
            sync_project(self.config, self.db_path)

        message = str(caught.exception)
        self.assertIn("src/Renamed.java", message)
        # 改名两端都要出现在拒绝列表里，方便用户判断。
        self.assertIn("src/Entry.java", message)

    def test_tracked_aoci_file_modification_is_allowed(self):
        # AOCI 资产先随初始提交进入仓库，再被修改：验证 `` M aoci.txt`` 也放行。
        # 资产写到来源仓库并推送，这样 checkout 里它们是已跟踪文件。
        (self.source / "aoci.txt").write_text("#AOCI-ROOT-MANIFEST: 1\n", encoding="utf-8")
        (self.source / "AGENTS.md").write_text("# 协作指引\n", encoding="utf-8")
        self._commit("track aoci assets")
        sync_project(self.config, self.db_path)  # 先快进到含资产的提交
        (self.checkout / "aoci.txt").write_text("#AOCI-ROOT-MANIFEST: 2\n", encoding="utf-8")

        result = sync_project(self.config, self.db_path)

        self.assertEqual("UP_TO_DATE", result["repositories"][0]["syncStatus"])

    def test_rename_source_into_aoci_asset_name_is_rejected(self):
        # 只按「改名目标」判白名单会放过这一条：新侧叫 AGENTS.md（白名单），
        # 但旧侧是真实源码 src/Entry.java，等于把源码挪进了资产名。
        # 两端都必须是 AOCI 资产才允许，所以这里必须拒绝。
        self._write_aoci_assets()
        (self.checkout / "AGENTS.md").unlink()
        self._git(self.checkout, "mv", "src/Entry.java", "AGENTS.md")

        with self.assertRaises(ProjectSyncError) as caught:
            sync_project(self.config, self.db_path)

        message = str(caught.exception)
        self.assertIn("src/Entry.java", message)

    def test_standalone_aoci_directory_and_gitattributes_are_allowed(self):
        # 已跟踪的 .aoci/.gitignore 会把 config.json 变成单条 ``?? .aoci/config.json``，
        # 而不是折叠成 ``?? .aoci/``；两种形态都必须放行。
        (self.source / "aoci.txt").write_text("#AOCI-ROOT-MANIFEST: 1\n", encoding="utf-8")
        (self.source / ".aoci").mkdir()
        (self.source / ".aoci" / ".gitignore").write_text(
            "*\n!.gitignore\n!config.json\n", encoding="utf-8",
        )
        self._commit("track aoci boundary")
        sync_project(self.config, self.db_path)  # 快进到已跟踪 .aoci/.gitignore 的提交
        (self.checkout / ".aoci" / "config.json").write_text("{}\n", encoding="utf-8")
        (self.checkout / ".gitattributes").write_text("*.sh text eol=lf\n", encoding="utf-8")

        # 先确认 Git 确实分别列出了这两条，避免测试因目录折叠而空转。
        records = {(change.status, change.paths) for change in _uncommitted_changes(self.checkout)}
        self.assertIn(("??", (".aoci/config.json",)), records)
        self.assertIn(("??", (".gitattributes",)), records)

        result = sync_project(self.config, self.db_path)

        self.assertEqual("UP_TO_DATE", result["repositories"][0]["syncStatus"])

    def test_bare_aoci_directory_without_cognition_is_not_whitelisted(self):
        # 只有一个 .aoci/ 目录（如本地运行时残留）不算 AOCI 仓库，
        # 否则任意仓库放个 .aoci/ 就能绕过未提交检查。
        (self.checkout / ".aoci").mkdir()
        (self.checkout / ".aoci" / "config.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaises(ProjectSyncError) as caught:
            sync_project(self.config, self.db_path)

        self.assertIn(".aoci/", str(caught.exception))

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


@unittest.skipUnless(shutil.which("git"), "Git is required")
class GitStatusPorcelainParsingTest(unittest.TestCase):
    """直接验证解析：两列状态码、改名、引号路径与目录折叠。"""

    @staticmethod
    def _git(folder: Path, *arguments: str):
        subprocess.run(
            ["git", "-C", str(folder), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "parsing"
        self.repo.mkdir()
        self._git(self.repo, "init", "-b", "main")
        self._git(self.repo, "config", "user.name", "Test User")
        self._git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "First.java").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (self.repo / "src" / "Second.java").write_text("second\n", encoding="utf-8")
        self._git(self.repo, "add", ".")
        self._git(self.repo, "commit", "-m", "initial")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _changes(folder: Path):
        return {(change.status, change.paths) for change in _uncommitted_changes(folder)}

    def test_leading_space_status_keeps_path_intact(self):
        # 仅工作区修改时 Git 输出 `` M src/First.java``，状态码以空格开头。
        # 若整段输出被 strip，路径会错位成 ``rc/First.java``。
        (self.repo / "src" / "First.java").write_text("one\nTWO\nthree\n", encoding="utf-8")

        self.assertIn((" M", ("src/First.java",)), self._changes(self.repo))

    def test_staged_modification_uses_second_column(self):
        (self.repo / "src" / "Second.java").write_text("second changed\n", encoding="utf-8")
        self._git(self.repo, "add", "src/Second.java")

        self.assertIn(("M ", ("src/Second.java",)), self._changes(self.repo))

    def test_rename_reports_both_sides(self):
        self._git(self.repo, "mv", "src/Second.java", "src/Renamed.java")

        statuses = self._changes(self.repo)
        status, paths = next(item for item in statuses if item[0][:1] in {"R", "C"})
        self.assertEqual(("src/Second.java", "src/Renamed.java"), paths)

    def test_untracked_directory_is_a_prefix_record(self):
        nested = self.repo / "brand new" / "inner"
        nested.mkdir(parents=True)
        (nested / "File.java").write_text("x\n", encoding="utf-8")

        self.assertIn(("??", ("brand new/",)), self._changes(self.repo))

    def test_rename_with_arrow_in_filename_reports_exact_paths(self):
        # 文件名本身含 `` -> `` 时 Git 会给整段加引号；只有引号外才是真正的分隔符。
        (self.repo / "a -> b.java").write_text("x\n", encoding="utf-8")
        self._git(self.repo, "add", "a -> b.java")
        self._git(self.repo, "commit", "-m", "arrow name")
        self._git(self.repo, "mv", "a -> b.java", "c -> d.java")

        statuses = self._changes(self.repo)
        status, paths = next(item for item in statuses if item[0][:1] in {"R", "C"})
        self.assertEqual(("a -> b.java", "c -> d.java"), paths)

    def test_quoted_non_ascii_path_is_decoded(self):
        (self.repo / "src" / "中文 空格.java").write_text("x\n", encoding="utf-8")

        self.assertIn(("??", ("src/中文 空格.java",)), self._changes(self.repo))

    def test_nested_aoci_named_path_is_not_whitelisted(self):
        # 白名单只认仓库根的 AOCI 资产；任意目录里叫 .aoci 或 aoci.txt
        # 都是真实内容，不能因为名字像就放行。
        (self.repo / "vendor").mkdir()
        (self.repo / "vendor" / ".aoci").mkdir()
        (self.repo / "vendor" / ".aoci" / "config.json").write_text("{}\n", encoding="utf-8")
        (self.repo / "vendor" / "aoci.txt").write_text("x\n", encoding="utf-8")

        self.assertFalse(_is_allowed_aoci_asset("vendor/.aoci/config.json"))
        self.assertFalse(_is_allowed_aoci_asset("vendor/aoci.txt"))
        self.assertFalse(_is_allowed_aoci_asset("docs/AGENTS.md"))
        # 仓库根的同名资产仍然放行。
        self.assertTrue(_is_allowed_aoci_asset(".aoci/config.json"))
        self.assertTrue(_is_allowed_aoci_asset("aoci.txt"))


@unittest.skipUnless(shutil.which("git"), "Git is required")
class NonAociRejectTest(unittest.TestCase):
    """白名单不能泄漏到普通仓库：非 AOCI 仓库任何未提交改动照旧拒绝。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "plain source"
        self.source.mkdir()
        self._git(self.source, "init", "-b", "main")
        self._git(self.source, "config", "user.name", "Test User")
        self._git(self.source, "config", "user.email", "test@example.com")
        (self.source / "src").mkdir()
        (self.source / "src" / "Entry.java").write_text(
            "package demo; public class Entry { public void run() {} }", encoding="utf-8",
        )
        self._git(self.source, "add", ".")
        self._git(self.source, "commit", "-m", "initial")
        self.config = self.root / "plain config.json"
        self.config.write_text(json.dumps({
            "project": {"id": "plain-system", "name": "Plain System"},
            "repositoryRoot": "managed plain repositories",
            "repositories": [{"id": "plain-core", "gitUrl": str(self.source), "branch": "main"}],
        }), encoding="utf-8")
        self.db_path = self.root / "plain.db"
        self.checkout = self.root / "managed plain repositories" / "plain-core"
        sync_project(self.config, self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_untracked_agents_md_is_rejected_without_aoci(self):
        (self.checkout / "AGENTS.md").write_text("# 临时说明\n", encoding="utf-8")

        with self.assertRaises(ProjectSyncError) as caught:
            sync_project(self.config, self.db_path)

        message = str(caught.exception)
        self.assertIn("AGENTS.md", message)
        self.assertIn("未提交修改", message)

    @staticmethod
    def _git(folder: Path, *arguments: str):
        subprocess.run(
            ["git", "-C", str(folder), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )


class OfflineProjectSyncTest(unittest.TestCase):
    def test_bundled_repository_is_indexed_without_git_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = root / "offline-repositories" / "core"
            java = repository / "src" / "Entry.java"
            java.parent.mkdir(parents=True)
            java.write_text("package demo; public class Entry { public void run() {} }", encoding="utf-8")
            config = root / "project.config.json"
            config.write_text(json.dumps({
                "project": {"id": "offline", "name": "Offline"},
                "repositoryRoot": "offline-repositories",
                "repositories": [{
                    "id": "core",
                    "gitUrl": "ssh://unreachable.invalid/core.git",
                    "localPath": "offline-repositories/core",
                }],
            }), encoding="utf-8")
            database = root / "knowledge.db"

            code_map = root / "generated-code-map"
            result = sync_project(config, database, offline=True, code_map_root=code_map)

            self.assertEqual("OFFLINE_BUNDLED", result["repositories"][0]["syncStatus"])
            self.assertGreater(result["repositories"][0]["indexed"]["symbols"], 0)
            self.assertFalse((repository / ".git").exists())
            self.assertEqual(1, result["codeMap"]["repositories"])
            self.assertEqual(1, result["codeMap"]["applications"])
            self.assertTrue((code_map / "project-index.md").is_file())
            self.assertTrue((code_map / "repositories" / "core.md").is_file())
            self.assertTrue((code_map / "repositories.md").is_file())
            self.assertTrue((code_map / "applications.md").is_file())
            repository_map = (code_map / "repositories" / "core.md").read_text(encoding="utf-8")
            repositories_map = (code_map / "repositories.md").read_text(encoding="utf-8")
            self.assertIn("仓库标识：`core`", repository_map)
            self.assertNotIn(str(repository), repository_map)
            self.assertNotIn(str(repository), repositories_map)


if __name__ == "__main__":
    unittest.main()

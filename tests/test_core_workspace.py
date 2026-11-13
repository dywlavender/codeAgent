from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.query.workspace import WorkspaceError, WorkspaceManager


def _project(root: Path, repositories=("channel", "middle", "decision")) -> Path:
    config = root / "project.json"
    config.write_text(json.dumps({
        "project": {"id": "core-workspace", "name": "Core Workspace"},
        "systems": [{"id": "s-core", "name": "核心系统"}],
        "repositories": [
            {"id": repository_id, "gitUrl": "unused", "localPath": f"repos/{repository_id}"}
            for repository_id in repositories
        ],
        "applications": [
            {"id": f"a-{repository_id}", "name": repository_id, "systemId": "s-core",
             "repositoryId": repository_id, "sourceRoot": ".", "type": "BACKEND"}
            for repository_id in repositories
        ],
    }), encoding="utf-8")
    return config


class CoreWorkspaceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "requirements").mkdir()
        (self.root / "requirements" / "request.md").write_text("提款申请。", encoding="utf-8")
        for repository_id in ("channel", "decision"):
            (self.root / "repos" / repository_id).mkdir(parents=True)
            (self.root / "repos" / repository_id / "Main.java").write_text(
                f"class {repository_id.title()} {{}}\n", encoding="utf-8")
        self.manager = WorkspaceManager(project_config=_project(self.root),
                                        workspace_root=self.root / "workspaces")

    def test_core_workspace_mounts_only_selected_sources_and_requirements(self):
        (self.root / "repos/unselected").mkdir()
        with patch.object(WorkspaceManager, "ensure", side_effect=AssertionError("must not build combined")), \
                patch("business_code_agent.query.workspace.build_navigation",
                      wraps=__import__("business_code_agent.query.navigation", fromlist=["build_navigation"]).build_navigation):
            workspace = self.manager.prepare_core(run_id="RUN-a", repository_ids=["channel", "decision"])
        self.assertEqual("core", workspace.mode)
        self.assertEqual(("channel", "decision"), workspace.repository_ids)
        self.assertEqual(("channel", "repos/channel"), workspace.repository_mounts[0])
        self.assertEqual({"channel", "decision"},
                         {path.name for path in workspace.repositories_path.iterdir()})
        self.assertTrue((workspace.path / "repos/channel/Main.java").is_file())
        self.assertTrue((workspace.path / "requirements/request.md").is_file())
        self.assertFalse((workspace.path / "knowledge").exists())
        navigation = json.loads((workspace.path / ".codeagent-project-navigation.json").read_text(encoding="utf-8"))
        self.assertEqual(["channel", "decision"],
                         [repository["id"] for repository in navigation["repositories"]])
        self.assertIn("只授权源码仓库", (workspace.path / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_missing_repository_is_reported_by_id_before_creating_a_workspace(self):
        with self.assertRaises(WorkspaceError) as raised:
            self.manager.prepare_core(run_id="RUN-missing")
        self.assertIn("middle", str(raised.exception))
        self.assertIn("目录不存在", str(raised.exception))
        self.assertFalse((self.manager.root / "core" / "RUN-missing").exists())

    def test_unreadable_repository_is_reported_without_starting_work(self):
        with patch("business_code_agent.query.workspace.os.scandir",
                   side_effect=PermissionError("denied")):
            with self.assertRaises(WorkspaceError) as raised:
                self.manager.prepare_core(run_id="RUN-unreadable",
                                          repository_ids=["channel", "decision"])
        message = str(raised.exception)
        for repository_id in ("channel", "decision"):
            self.assertIn(repository_id, message)
        self.assertIn("无法读取", message)
        self.assertFalse((self.manager.root / "core" / "RUN-unreadable").exists())

    def test_unregistered_scope_id_is_rejected(self):
        with self.assertRaisesRegex(WorkspaceError, "未在项目配置中登记"):
            self.manager.prepare_core(run_id="RUN-unknown", repository_ids=["missing"])

    def test_selected_scope_can_run_when_another_configured_repository_is_absent(self):
        workspace = self.manager.prepare_core(run_id="RUN-scoped",
                                              repository_ids=["channel"])
        self.assertEqual(("channel",), workspace.repository_ids)
        self.assertEqual({"channel"}, {path.name for path in workspace.repositories_path.iterdir()})

    def test_runs_with_different_scopes_do_not_share_or_remount_workspaces(self):
        first = self.manager.prepare_core(run_id="RUN-one", repository_ids=["channel"])
        second = self.manager.prepare_core(run_id="RUN-two", repository_ids=["decision"])
        self.assertNotEqual(first.path, second.path)
        self.assertEqual({"channel"}, {path.name for path in first.repositories_path.iterdir()})
        self.assertEqual({"decision"}, {path.name for path in second.repositories_path.iterdir()})
        self.assertTrue((first.path / "repos/channel/Main.java").is_file())
        self.assertTrue((second.path / "repos/decision/Main.java").is_file())
        self.assertFalse((first.path / "repos/decision").exists())
        self.assertFalse((second.path / "repos/channel").exists())


if __name__ == "__main__":
    unittest.main()

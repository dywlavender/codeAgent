from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.query_agent.workspace import WorkspaceManager


class AgentWorkspaceTest(unittest.TestCase):
    def test_workspace_exposes_live_knowledge_requirements_and_repositories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = root / "channel-service"
            repository.mkdir()
            source = repository / "WithdrawService.java"
            source.write_text("class WithdrawService { boolean validate() { return true; } }", encoding="utf-8")
            baseline = root / "knowledge" / "baseline"
            baseline.mkdir(parents=True)
            baseline_file = baseline / "withdraw.md"
            baseline_file.write_text("# 提款\n\n银行卡必须存在。", encoding="utf-8")
            requirements = root / "requirements"
            requirements.mkdir()
            requirement_file = requirements / "withdraw.md"
            requirement_file.write_text("提款前完成签约。", encoding="utf-8")
            config = root / "project.config.json"
            config.write_text(json.dumps({
                "project": {"id": "loan-withdraw", "name": "贷款提款"},
                "knowledge": {"baselineRoot": "knowledge/baseline"},
                "requirementsRoot": "requirements",
                "repositories": [{"id": "channel-service", "localPath": "channel-service", "gitUrl": "unused"}],
            }), encoding="utf-8")

            workspace = WorkspaceManager(project_config=config).ensure()
            self.assertEqual("loan-withdraw", workspace.id)
            self.assertTrue(workspace.claude_file.is_file())
            self.assertTrue((workspace.path / "knowledge" / "baseline" / "withdraw.md").is_file())
            self.assertTrue((workspace.path / "requirements" / "withdraw.md").is_file())
            exposed = workspace.path / "repos" / "channel-service" / "WithdrawService.java"
            self.assertTrue(exposed.is_file())
            self.assertIn("Entry Anchor", workspace.claude_file.read_text(encoding="utf-8"))

            # The workspace is a view, not a copied checkout. A source update
            # is visible after refresh and the source contents are untouched.
            source.write_text("class WithdrawService { boolean validate() { return false; } }", encoding="utf-8")
            WorkspaceManager(project_config=config).refresh()
            self.assertIn("return false", exposed.read_text(encoding="utf-8"))
            self.assertIn("return false", source.read_text(encoding="utf-8"))

    def test_missing_sources_are_linked_without_copying_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "empty"},
                "repositories": [{"id": "repo", "gitUrl": "unused", "localPath": "not-yet-synced"}],
            }), encoding="utf-8")
            workspace = WorkspaceManager(project_config=config).ensure()
            self.assertTrue((workspace.path / "repos" / "repo").is_symlink())
            self.assertTrue((workspace.path / "CLAUDE.md").is_file())


if __name__ == "__main__":
    unittest.main()

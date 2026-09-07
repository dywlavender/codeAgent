from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.query_agent.workspace import WorkspaceManager
from business_code_agent.query_agent.claude_runtime import ClaudeCodeRuntime


class AgentWorkspaceTest(unittest.TestCase):
    def test_overview_is_current_on_resume_while_flow_stays_on_demand(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = root / "baseline"
            baseline.mkdir()
            overview = baseline / "project-overview.md"
            overview.write_text("# 项目地图\n渠道负责接入，业务流程见 withdraw.md。", encoding="utf-8")
            (baseline / "withdraw.md").write_text("按需读取的流程详情", encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            workspace = WorkspaceManager(project_config=config).ensure()
            runtime = ClaudeCodeRuntime()
            command = runtime.build_command("提款流程", workspace=workspace.path)
            prompt = command[command.index("--append-system-prompt") + 1]
            self.assertIn("渠道负责接入", prompt)
            self.assertIn(str(overview.resolve()), prompt)
            self.assertNotIn("按需读取的流程详情", prompt)
            self.assertNotIn("渠道负责接入", workspace.claude_file.read_text())
            overview.write_text("# 项目地图\n更新后的系统职责", encoding="utf-8")
            command = runtime.build_command("继续", workspace=workspace.path, session_id="existing")
            prompt = command[command.index("--append-system-prompt") + 1]
            self.assertIn("更新后的系统职责", prompt)
            self.assertNotIn("渠道负责接入", prompt)
            overview.unlink()
            command = runtime.build_command("继续", workspace=workspace.path, session_id="existing")
            self.assertNotIn("本轮项目总览", command[command.index("--append-system-prompt") + 1])

    def test_source_summary_distinguishes_readable_empty_missing_and_mounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo = root / "repo"
            repo.mkdir()
            (repo / "main.java").write_text("class Main {}", encoding="utf-8")
            (root / "baseline").mkdir()
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"},
                "repositories": [{"id": "repo", "localPath": "repo"}]}), encoding="utf-8")
            manager = WorkspaceManager(project_config=config)
            self.assertFalse(any(item["authorizationConfigured"] for item in manager.source_summary()))
            manager.ensure()
            sources = {item["kind"]: item for item in manager.source_summary()}
            self.assertEqual("READABLE", sources["repository"]["status"])
            self.assertEqual(str(repo.resolve()), sources["repository"]["path"])
            self.assertTrue(sources["repository"]["authorizationConfigured"])
            self.assertEqual("EMPTY", sources["baseline"]["status"])
            self.assertEqual("MISSING", sources["requirements"]["status"])
            self.assertFalse(sources["requirements"]["authorizationConfigured"])

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
            instructions = workspace.claude_file.read_text(encoding="utf-8")
            for directory in (repository, baseline, requirements):
                self.assertIn(str(directory.resolve()), instructions)
            # Real targets must be authorised for both new and resumed turns,
            # not merely their links under the generated workspace.
            runtime = ClaudeCodeRuntime()
            for session in (None, "existing-session"):
                command = runtime.build_command("继续查调用链", workspace=workspace.path, session_id=session)
                end = command.index("--resume") if session else command.index("-p")
                directories = command[command.index("--add-dir") + 1:end]
                self.assertEqual({str(path.resolve()) for path in
                                  (workspace.path, repository, baseline, requirements)}, set(directories))
                self.assertIn("dontAsk", command)
                self.assertEqual("Read,Glob,Grep", command[command.index("--tools") + 1])
                prompt = command[command.index("--append-system-prompt") + 1]
                self.assertIn(str(repository.resolve()), prompt)
                self.assertIn("不要合并成它们的共同上级目录", prompt)
                self.assertIn("不要换工具绕过权限", prompt)
                self.assertNotIn(str(root.resolve()), directories)

            # The workspace is a view, not a copied checkout. A source update
            # is visible after refresh and the source contents are untouched.
            source.write_text("class WithdrawService { boolean validate() { return false; } }", encoding="utf-8")
            WorkspaceManager(project_config=config).refresh()
            self.assertIn("return false", exposed.read_text(encoding="utf-8"))
            self.assertIn("return false", source.read_text(encoding="utf-8"))

    def test_missing_sources_are_marked_unavailable_without_dangling_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "empty"},
                "repositories": [{"id": "repo", "gitUrl": "unused", "localPath": "not-yet-synced"}],
            }), encoding="utf-8")
            workspace = WorkspaceManager(project_config=config).ensure()
            self.assertFalse((workspace.path / "repos" / "repo").is_symlink())
            self.assertFalse(workspace.requirements_path.is_symlink())
            self.assertTrue((workspace.path / "CLAUDE.md").is_file())
            instructions = workspace.claude_file.read_text(encoding="utf-8")
            self.assertIn("需求原文：不可用", instructions)
            self.assertIn("代码仓库 repo：不可用", instructions)
            command = ClaudeCodeRuntime().build_command("q", workspace=workspace.path)
            self.assertEqual([str(workspace.path.resolve())],
                             command[command.index("--add-dir") + 1:command.index("-p")])
            workspace.requirements_path.symlink_to(root / "missing", target_is_directory=True)
            WorkspaceManager(project_config=config).refresh()
            self.assertFalse(workspace.requirements_path.is_symlink())

    def test_resumed_prompt_uses_current_roots_without_widening_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "旧仓库"
            new = root / "新仓库 with spaces"
            old.mkdir()
            new.mkdir()
            config = root / "project.json"
            def configure(path):
                config.write_text(json.dumps({"repositories": [{"id": "repo", "localPath": str(path)}]}), encoding="utf-8")
                return WorkspaceManager(project_config=config).ensure()
            configure(old)
            workspace = configure(new)
            command = ClaudeCodeRuntime().build_command("继续", workspace=workspace.path, session_id="previous")
            prompt = command[command.index("--append-system-prompt") + 1]
            self.assertIn(str(new.resolve()), prompt)
            self.assertNotIn(str(old.resolve()), prompt)
            self.assertEqual("previous", command[command.index("--resume") + 1])
            directories = command[command.index("--add-dir") + 1:command.index("--resume")]
            self.assertNotIn(str(root.resolve()), directories)
            self.assertNotIn(str(old.resolve()), directories)


if __name__ == "__main__":
    unittest.main()

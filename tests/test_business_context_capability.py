from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.capabilities import (
    BusinessContextCapability,
    prepare_capabilities,
)
from business_code_agent.query.context import RunContext
from business_code_agent.query.materials import BUSINESS_CONTEXT_MOUNT, MaterialManifest
from business_code_agent.query.profile import CORE, CORE_BUSINESS, resolve_profile
from business_code_agent.query.tools.files import CoreFiles
from business_code_agent.query.workspace import WorkspaceManager


class BusinessContextCapabilityTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("channel", "decision"):
            source = self.root / "repos" / repository_id
            source.mkdir(parents=True)
            (source / f"{repository_id.title()}.java").write_text(
                "class Main {\n  void withdraw() {}\n}\n", encoding="utf-8")
        (self.root / "requirements").mkdir()
        (self.root / "requirements" / "request.md").write_text(
            "提款需要校验金额。\n", encoding="utf-8")
        self.documents = self.root / "knowledge" / "business-context"
        self.documents.mkdir(parents=True)
        (self.documents / "README.md").write_text("业务补充知识说明。\n", encoding="utf-8")
        (self.documents / "cross-system.md").write_text(
            "外部付款成功不等于提款业务最终成功；中台仍需完成借据登记。\n", encoding="utf-8")
        self.config = self.root / "project.json"
        self.config.write_text(json.dumps({
            "project": {"id": "business-context", "name": "Business Context"},
            "knowledge": {"businessContextRoot": "knowledge/business-context"},
            "repositories": [
                {"id": repository_id, "gitUrl": "unused", "localPath": f"repos/{repository_id}"}
                for repository_id in ("channel", "decision")
            ],
        }), encoding="utf-8")
        self.manager = WorkspaceManager(project_config=self.config,
                                        workspace_root=self.root / "workspaces")
        self.project = ProjectContext.legacy(self.root / "db.sqlite", self.config)
        self.runs = 0

    def context(self, profile, repository_ids=None):
        self.runs += 1
        include = profile.business_context != "disabled"
        workspace = self.manager.prepare_core(
            run_id=f"RUN-capability-{self.runs}", repository_ids=repository_ids,
            include_business_context=include)
        manifest = MaterialManifest.for_core(
            workspace.path, workspace.repository_mounts,
            include_requirements=True, include_business_context=include)
        return RunContext(
            run_id=f"RUN-capability-{self.runs}", project=self.project, profile=profile,
            repository_ids=workspace.repository_ids, workspace=workspace.path,
            materials=manifest.sources, session_id=None,
        ), workspace, manifest

    def test_business_profile_is_explicit_and_keeps_indexes_disabled(self):
        profile = resolve_profile({"BUSINESS_CODE_AGENT_PROFILE": "core-business-v1"})
        self.assertEqual(CORE_BUSINESS, profile)
        self.assertEqual(("core-business-v1", "direct", "optional"),
                         (profile.id, profile.investigation, profile.business_context))
        self.assertEqual(("disabled", "disabled"),
                         (profile.search_code, profile.contract_trace))
        self.assertEqual("disabled", CORE.business_context)
        self.assertEqual(("source", "requirements"), CORE.materials)

    def test_manifest_marks_missing_or_changed_business_context(self):
        context, _, manifest = self.context(CORE_BUSINESS)
        business = next(source for source in manifest.sources
                        if source.kind == "business_context")
        self.assertEqual(("available", BUSINESS_CONTEXT_MOUNT),
                         (business.status, business.path))
        self.assertTrue(business.version.startswith("sha256:"))
        original = business.version
        (self.documents / "cross-system.md").write_text(
            "外部付款成功后仍需完成业务台账收敛。\n", encoding="utf-8")
        _, _, changed = self.context(CORE_BUSINESS)
        self.assertNotEqual(original, next(source for source in changed.sources
                                           if source.kind == "business_context").version)
        for path in self.documents.iterdir():
            path.unlink()
        _, _, empty = self.context(CORE_BUSINESS)
        missing = next(source for source in empty.sources if source.kind == "business_context")
        self.assertEqual(("missing", None, None),
                         (missing.status, missing.directory, missing.version))
        self.assertTrue(all(source.available for source in empty.sources
                            if source.kind == "source"))
        _, _, core_manifest = self.context(CORE)
        self.assertFalse(any(source.kind == "business_context"
                             for source in core_manifest.sources))

    def test_capability_adds_guidance_without_new_tools(self):
        context, _, _ = self.context(CORE_BUSINESS)
        prepared = prepare_capabilities(context)
        self.assertEqual({"Read", "Glob", "Grep"}, {tool.name for tool in prepared.tools})
        statuses = {status.capability: status for status in prepared.statuses}
        self.assertEqual(("optional", "ready"),
                         (statuses["business_context"].policy,
                          statuses["business_context"].status))
        for capability in ("search_code", "contract_trace", "ast", "code_map", "hybrid_search"):
            self.assertEqual(("disabled", "disabled"),
                             (statuses[capability].policy, statuses[capability].status))
        guidance = dict(prepared.instructions)["business_context"]
        self.assertIn(BUSINESS_CONTEXT_MOUNT, guidance)
        self.assertIn("不能当作源码已经实现", guidance)
        self.assertEqual([], list(BusinessContextCapability().tools(context)))

        core_context, _, _ = self.context(CORE)
        core_prepared = prepare_capabilities(core_context)
        core_statuses = {status.capability: status for status in core_prepared.statuses}
        self.assertEqual("disabled", core_statuses["business_context"].status)
        self.assertNotIn("business_context", dict(core_prepared.instructions))

    def test_file_tools_authorize_business_context_only_from_the_manifest(self):
        business_context, _, _ = self.context(CORE_BUSINESS)
        files = CoreFiles(business_context.workspace, business_context.materials)
        document = files.read(BUSINESS_CONTEXT_MOUNT + "/cross-system.md")
        self.assertIn("外部付款成功不等", document["content"])
        self.assertIn(BUSINESS_CONTEXT_MOUNT + "/README.md",
                      files.glob("knowledge/**/*.md")["paths"])
        matches = files.grep("借据登记", path=BUSINESS_CONTEXT_MOUNT)
        self.assertEqual(1, len(matches["matches"]))

        core_context, _, _ = self.context(CORE)
        core_files = CoreFiles(core_context.workspace, core_context.materials)
        with self.assertRaisesRegex(ValueError, "不在本轮授权资料范围内"):
            core_files.read(BUSINESS_CONTEXT_MOUNT + "/cross-system.md")
        self.assertEqual([], core_files.glob("knowledge/**/*.md")["paths"])

    def test_workspace_mounts_business_context_only_when_authorized(self):
        self.runs += 1
        core_workspace = self.manager.prepare_core(
            run_id=f"RUN-capability-{self.runs}", include_business_context=False)
        self.assertIsNone(core_workspace.business_context_path)
        self.assertFalse((core_workspace.path / "knowledge" / "business-context").exists())
        self.assertNotIn("knowledge/business-context",
                         (core_workspace.path / "CLAUDE.md").read_text(encoding="utf-8"))
        business_workspace = self.manager.prepare_core(
            run_id="RUN-capability-business", include_business_context=True)
        self.assertTrue((business_workspace.path / BUSINESS_CONTEXT_MOUNT / "README.md").is_file())
        self.assertIn("knowledge/business-context",
                      (business_workspace.path / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_business_documents_do_not_expand_repository_scope(self):
        context, workspace, manifest = self.context(CORE_BUSINESS, ["channel"])
        self.assertEqual(("channel",), context.repository_ids)
        self.assertEqual(("channel",), workspace.repository_ids)
        self.assertNotIn("decision", {source.repository_id for source in manifest.sources})
        files = CoreFiles(context.workspace, context.materials)
        self.assertIn("外部付款成功不等",
                      files.read(BUSINESS_CONTEXT_MOUNT + "/cross-system.md")["content"])
        with self.assertRaisesRegex(ValueError, "不在本轮授权资料范围内"):
            files.read("repos/decision/Decision.java")

    def test_missing_business_context_keeps_file_capability_required(self):
        for path in self.documents.iterdir():
            path.unlink()
        context, _, manifest = self.context(CORE_BUSINESS)
        prepared = prepare_capabilities(context)
        statuses = {status.capability: status for status in prepared.statuses}
        self.assertEqual(("required", "ready"),
                         (statuses["file_tools"].policy, statuses["file_tools"].status))
        self.assertEqual(("optional", "missing"),
                         (statuses["business_context"].policy,
                          statuses["business_context"].status))
        self.assertFalse(next(source for source in manifest.sources
                              if source.kind == "business_context").available)
        files = CoreFiles(context.workspace, context.materials)
        self.assertIn("withdraw", files.read("repos/channel/Channel.java")["content"])


if __name__ == "__main__":
    unittest.main()

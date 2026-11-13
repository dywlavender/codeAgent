from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.context import QueryScope, RunContext
from business_code_agent.query.materials import MaterialManifest
from business_code_agent.query.profile import CORE, STANDARD, resolve_profile
from business_code_agent.query.workspace import WorkspaceManager


class CoreProfileTest(unittest.TestCase):
    def test_core_profile_is_explicit_and_keeps_legacy_placeholder_separate(self):
        profile = resolve_profile({"BUSINESS_CODE_AGENT_PROFILE": " core-v1 "})
        self.assertEqual(("core-v1", "langchain-direct", "direct"),
                         (profile.id, profile.runtime, profile.investigation))
        self.assertEqual(("source", "requirements"), profile.materials)
        self.assertEqual(("required", "disabled", "disabled"),
                         (profile.file_tools, profile.search_code, profile.contract_trace))
        self.assertNotEqual(STANDARD, profile)
        self.assertEqual(("standard-v1", "agent_direct", "optional", "disabled"),
                         (STANDARD.id, STANDARD.investigation, STANDARD.search_code,
                          STANDARD.contract_trace))
        self.assertEqual(CORE, profile)

    def test_core_profile_overrides_legacy_switches_and_rejects_unknown_names(self):
        profile = resolve_profile({
            "BUSINESS_CODE_AGENT_PROFILE": "core-v1",
            "BUSINESS_CODE_AGENT_WORKFLOW": "coordinated",
            "BUSINESS_CODE_AGENT_RETRIEVAL": "hybrid",
            "BUSINESS_CODE_AGENT_CONTRACTS": "on",
        })
        self.assertEqual(CORE, profile)
        with self.assertRaisesRegex(ValueError, "未知的 Agent Profile"):
            resolve_profile({"BUSINESS_CODE_AGENT_PROFILE": "core-v2"})


class CoreManifestTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Path(folder.name)

    def test_every_configured_repository_gets_an_entry_even_when_missing(self):
        (self.workspace / "repos" / "channel").mkdir(parents=True)
        (self.workspace / "repos" / "decision").mkdir(parents=True)
        manifest = MaterialManifest.for_core(self.workspace, [
            ("channel", "repos/channel"),
            ("middle", "repos/middle"),
            ("decision", "repos/decision"),
        ])
        sources = {source.repository_id: source for source in manifest.sources if source.kind == "source"}
        self.assertEqual({"channel", "middle", "decision"}, set(sources))
        self.assertEqual(("available", "missing", "available"),
                         (sources["channel"].status, sources["middle"].status,
                          sources["decision"].status))
        self.assertIsNone(sources["middle"].directory)
        self.assertEqual(["middle"], [source.repository_id for source in manifest.missing_required()])
        self.assertEqual({"channel", "decision"},
                         {source.repository_id for source in manifest.readable()})
        self.assertEqual("repos/middle", sources["middle"].path)
        self.assertEqual("middle", sources["middle"].to_dict()["repositoryId"])

    def test_core_manifest_only_contains_source_and_optional_requirements(self):
        (self.workspace / "repos" / "channel").mkdir(parents=True)
        (self.workspace / "knowledge" / "business-context").mkdir(parents=True)
        (self.workspace / "knowledge" / "ast").mkdir(parents=True)
        (self.workspace / "knowledge" / "generated-code-map").mkdir(parents=True)
        manifest = MaterialManifest.for_core(self.workspace, [("channel", "repos/channel")])
        self.assertEqual(["source", "requirements"], [source.kind for source in manifest.sources])
        self.assertEqual(["missing"], [source.status for source in manifest.sources[1:]])
        self.assertEqual(["repos/channel"], [path for path, _ in manifest.roots()])
        legacy = MaterialManifest.from_workspace(self.workspace)
        self.assertIn("business_context", {source.kind for source in legacy.sources})

    def test_requirements_are_mounted_only_when_present_and_enabled(self):
        (self.workspace / "repos" / "channel").mkdir(parents=True)
        (self.workspace / "requirements").mkdir()
        included = MaterialManifest.for_core(self.workspace, [("channel", "repos/channel")])
        self.assertEqual(("requirements", "available"),
                         (included.sources[-1].kind, included.sources[-1].status))
        excluded = MaterialManifest.for_core(self.workspace, [("channel", "repos/channel")],
                                             include_requirements=False)
        self.assertEqual(["source"], [source.kind for source in excluded.sources])


class RepositoryConfigurationTest(unittest.TestCase):
    def test_configured_repositories_include_missing_sources_and_ignore_unregistered_dirs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "channel").mkdir()
            (root / "unregistered").mkdir()
            config = root / "project.json"
            config.write_text(json.dumps({
                "project": {"id": "core-config", "name": "Core Config"},
                "repositories": [
                    {"id": "channel", "gitUrl": "unused", "localPath": "channel"},
                    {"id": "middle", "gitUrl": "unused", "localPath": "middle"},
                    {"id": "decision", "gitUrl": "unused", "localPath": "decision"},
                ],
            }), encoding="utf-8")
            manager = WorkspaceManager(project_config=config, workspace_root=root / "workspaces")
            self.assertEqual(["channel", "middle", "decision"],
                             [repository_id for repository_id, _ in manager.repository_sources()])
            self.assertFalse((root / "middle").exists())


class RunContextTest(unittest.TestCase):
    def test_context_is_an_immutable_authorization_snapshot(self):
        project = ProjectContext.legacy(":memory:")
        context = RunContext(
            run_id="RUN-1", project=project, profile=CORE,
            repository_ids=("channel", "decision"), workspace=Path("/tmp/core"),
            materials=(), session_id=None,
        )
        self.assertEqual(("channel", "decision"), context.repository_ids)
        with self.assertRaises(FrozenInstanceError):
            context.session_id = "DA-1"
        updated = replace(context, session_id="DA-1")
        self.assertEqual("DA-1", updated.session_id)
        self.assertIsNone(context.session_id)
        self.assertEqual(QueryScope((), ("channel",)), QueryScope((), ("channel",)))


if __name__ == "__main__":
    unittest.main()

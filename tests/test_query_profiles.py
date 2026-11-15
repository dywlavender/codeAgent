"""Focused tests for the existing V1 profile/material/tool preparation interfaces."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent import preparation as preparation_module
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.langchain_agent.preparation import prepare_tools
from business_code_agent.query.materials import MATERIAL_KINDS, MaterialManifest
from business_code_agent.query.profile import ToolStatus, execution_snapshot, resolve_profile
from business_code_agent.query.runtime import RequiredToolUnavailable


class ResolveProfileTest(unittest.TestCase):
    def test_default_runtime_is_pi_and_legacy_needs_explicit_runtime(self):
        for environment in ({}, {"BUSINESS_CODE_AGENT_PROFILE": "  "}):
            with self.subTest(environment=environment):
                profile = resolve_profile(environment)
                self.assertEqual(("pi-v1", "pi", "pi"), (profile.id, profile.runtime, profile.investigation))
                self.assertEqual(MATERIAL_KINDS, profile.materials)
        profile = resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "langchain"})
        self.assertEqual(
            ("coordinated-legacy-v1", "langchain", "coordinated", "plain"),
            (profile.id, profile.runtime, profile.investigation, profile.answer_mode),
        )
        self.assertEqual(("required", "optional", "optional"),
                         (profile.file_tools, profile.search_code, profile.contract_trace))

    def test_basic_and_tracked_map_legacy_switches(self):
        for workflow in ("basic", "tracked"):
            for retrieval, contracts, policy in (
                (None, None, "disabled"), ("files", "off", "disabled"),
                ("auto", "auto", "optional"), ("hybrid", "on", "required"),
            ):
                with self.subTest(workflow=workflow, retrieval=retrieval, contracts=contracts):
                    environment = {"BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                                   "BUSINESS_CODE_AGENT_WORKFLOW": workflow,
                                   "BUSINESS_CODE_AGENT_ANSWER_MODE": "facts"}
                    if retrieval is not None:
                        environment["BUSINESS_CODE_AGENT_RETRIEVAL"] = retrieval
                        environment["BUSINESS_CODE_AGENT_CONTRACTS"] = contracts
                    profile = resolve_profile(environment)
                    self.assertEqual(f"{workflow}-legacy-v1", profile.id)
                    self.assertEqual(workflow, profile.investigation)
                    self.assertEqual("facts", profile.answer_mode)
                    self.assertEqual((policy, policy), (profile.search_code, profile.contract_trace))

    def test_explicit_profiles_take_precedence_over_legacy_switches(self):
        for name, runtime, investigation, search, contracts in (
            ("standard-v1", "langchain", "agent_direct", "optional", "disabled"),
            ("coordinated-legacy-v1", "langchain", "coordinated", "optional", "optional"),
            ("claude-compat-v1", "claude", "claude_cli", "disabled", "disabled"),
        ):
            with self.subTest(profile=name):
                profile = resolve_profile({
                    "BUSINESS_CODE_AGENT_PROFILE": f" {name} ",
                    "BUSINESS_CODE_AGENT_RUNTIME": "claude",
                    "BUSINESS_CODE_AGENT_WORKFLOW": "invalid",
                    "BUSINESS_CODE_AGENT_ANSWER_MODE": "invalid",
                    "BUSINESS_CODE_AGENT_RETRIEVAL": "invalid",
                    "BUSINESS_CODE_AGENT_CONTRACTS": "invalid",
                })
                self.assertEqual((name, runtime, investigation, "plain", "required", search, contracts),
                                 (profile.id, profile.runtime, profile.investigation, profile.answer_mode,
                                  profile.file_tools, profile.search_code, profile.contract_trace))
        self.assertEqual("claude-compat-v1", resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "CLAUDE"}).id)

    def test_invalid_profile_and_legacy_values_are_rejected(self):
        for key, message in (
            ("PROFILE", "未知的 Agent Profile"), ("WORKFLOW", "WORKFLOW"),
            ("ANSWER_MODE", "ANSWER_MODE"), ("RETRIEVAL", "RETRIEVAL"), ("CONTRACTS", "CONTRACTS"),
            ("RUNTIME", "RUNTIME"),
        ):
            environment = {f"BUSINESS_CODE_AGENT_{key}": "invalid"}
            if key != "PROFILE":
                environment["BUSINESS_CODE_AGENT_RUNTIME"] = (
                    "invalid" if key == "RUNTIME" else "langchain")
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, message):
                resolve_profile(environment)

    def test_contracts_answer_requires_contract_tools(self):
        for policy in ("auto", "on", "off"):
            with self.subTest(contracts=policy):
                environment = {"BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                               "BUSINESS_CODE_AGENT_ANSWER_MODE": "contracts",
                               "BUSINESS_CODE_AGENT_CONTRACTS": policy}
                if policy == "off":
                    with self.assertRaisesRegex(ValueError, "contracts 答题模式需要启用工程契约索引"):
                        resolve_profile(environment)
                else:
                    profile = resolve_profile(environment)
                    self.assertEqual("contracts", profile.answer_mode)
                    self.assertEqual("required", profile.contract_trace)


class TemporaryWorkspaceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        # Do not inherit model credentials or tracing configuration from the host.
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write_fixture(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class MaterialManifestTest(TemporaryWorkspaceTest):
    def test_allowed_materials_intersect_with_historical_modes(self):
        for relative in ("repos/a", "requirements", "knowledge/business-context",
                         "knowledge/generated-code-map", "knowledge/ast"):
            (self.root / relative).mkdir(parents=True)
        mode_kinds = {
            None: set(MATERIAL_KINDS), "combined": set(MATERIAL_KINDS),
            "none": {"source", "requirements"},
            "backbone": {"source", "requirements", "business_context"},
            "ast": {"source", "requirements", "ast"},
        }
        for mode, expected in mode_kinds.items():
            for allowed in (MATERIAL_KINDS, ("source", "code_map", "ast"), ()):
                with self.subTest(mode=mode, allowed=allowed):
                    manifest = MaterialManifest.from_workspace(self.root, allowed=allowed, mode=mode)
                    self.assertEqual(expected & set(allowed), {source.kind for source in manifest.sources})
                    self.assertEqual(len(manifest.sources), len(manifest.readable()))

    def test_repository_scope_filters_manifest_and_file_roots(self):
        for repo in ("a", "b"):
            self.write_fixture(f"repos/{repo}/Main.java", "class Main {}\n")
        self.write_fixture("repos/not-a-directory", "ignored")
        for repositories, expected in ((None, ["repos/a", "repos/b"]), ({"b"}, ["repos/b"]), (set(), [])):
            with self.subTest(repositories=repositories):
                manifest = MaterialManifest.from_workspace(self.root, repositories, allowed=("source",))
                self.assertEqual(expected, [source.path for source in manifest.readable()])
                self.assertEqual([(path, self.root / path) for path in expected], manifest.roots())
                self.assertEqual(manifest.roots(), SourceFiles(self.root, manifest=manifest).roots)

    def test_business_context_falls_back_to_baseline_but_prefers_current_directory(self):
        self.write_fixture("knowledge/baseline/rules.txt", "legacy business rule")
        for primary_exists, expected_path, label in (
            (False, "knowledge/baseline", "业务基线"),
            (True, "knowledge/business-context", "业务补充知识"),
        ):
            with self.subTest(primary_exists=primary_exists):
                if primary_exists:
                    self.write_fixture("knowledge/business-context/rules.txt", "current business rule")
                manifest = MaterialManifest.from_workspace(self.root, allowed=("business_context",))
                self.assertEqual(1, len(manifest.sources))
                source = manifest.sources[0]
                self.assertEqual(("business_context", label, expected_path, "available"),
                                 (source.kind, source.label, source.path, source.status))
                self.assertEqual([(label, self.root / expected_path)], manifest.labelled_roots())

    def test_combined_ast_and_code_map_keep_independent_sources_and_versions(self):
        for relative in ("knowledge/ast", "knowledge/generated-code-map"):
            (self.root / relative).mkdir(parents=True)
        manifests = [MaterialManifest.from_workspace(
            self.root, allowed=("ast", "code_map"), mode="combined", ast_version=version,
        ) for version in ("ast-v1", "ast-v2")]
        for manifest, version in zip(manifests, ("ast-v1", "ast-v2")):
            with self.subTest(version=version):
                sources = {source.kind: source for source in manifest.sources}
                self.assertEqual({"ast", "code_map"}, set(sources))
                self.assertEqual("knowledge/ast", sources["ast"].path)
                self.assertEqual("knowledge/generated-code-map", sources["code_map"].path)
                self.assertNotEqual(sources["ast"].directory, sources["code_map"].directory)
                self.assertEqual(version, sources["ast"].to_dict()["version"])
                self.assertIsNone(sources["code_map"].to_dict()["version"])

    def test_historical_ast_mode_uses_code_map_mount_without_changing_source_kind(self):
        (self.root / "knowledge/generated-code-map").mkdir(parents=True)
        manifest = MaterialManifest.from_workspace(
            self.root, allowed=("ast", "code_map"), mode="ast", ast_version="historical-ast",
        )
        self.assertEqual(1, len(manifest.sources))
        source = manifest.sources[0]
        self.assertEqual(("ast", "knowledge/generated-code-map", "historical-ast", "available"),
                         (source.kind, source.path, source.version, source.status))

    def test_missing_materials_serialize_status_and_are_not_readable(self):
        for ast_status in (None, "stale", "unavailable"):
            with self.subTest(ast_status=ast_status):
                version = "old-ast" if ast_status else None
                manifest = MaterialManifest.from_workspace(self.root, ast_status=ast_status, ast_version=version)
                values = json.loads(json.dumps([source.to_dict() for source in manifest.sources]))
                self.assertEqual(set(MATERIAL_KINDS), {value["kind"] for value in values})
                self.assertEqual([], manifest.readable())
                self.assertEqual([], manifest.roots())
                self.assertEqual([], manifest.labelled_roots())
                for value in values:
                    self.assertFalse(value["available"])
                    self.assertIsNone(value["directory"])
                    expected_status = (ast_status or "missing") if value["kind"] == "ast" else "missing"
                    self.assertEqual(expected_status, value["status"])
                    self.assertEqual(version if value["kind"] == "ast" else None, value["version"])
                requirement = next(value for value in values if value["kind"] == "requirements")
                self.assertEqual({
                    "kind": "requirements", "label": "需求原文", "path": "requirements",
                    "directory": None, "purpose": "核对需求目标和验收约束", "status": "missing",
                    "available": False, "version": None,
                }, requirement)

    def test_current_limitation_authorized_missing_repository_is_omitted_when_peer_exists(self):
        """Characterization only: per-repository missing reporting is still unfixed."""
        (self.root / "repos/present").mkdir(parents=True)
        manifest = MaterialManifest.from_workspace(
            self.root, {"present", "absent"}, allowed=("source",),
        )
        # This records the gap, not a desired contract: repos/absent needs its own missing entry.
        self.assertEqual([("repos/present", "available")],
                         [(source.path, source.status) for source in manifest.sources])


class PrepareToolsTest(TemporaryWorkspaceTest):
    def setUp(self):
        super().setUp()
        self.write_fixture("repos/allowed/Main.java", "class Main {\n int total = 1;\n}\n")
        self.write_fixture("repos/excluded/Other.java", "class Other { int total = 2; }\n")
        self.materials = MaterialManifest.from_workspace(self.root, {"allowed"}, allowed=("source",))

    def prepare(self, profile, *, index_root=None, contract_root=None):
        return prepare_tools(profile, self.root, self.materials, project_id="profile-test",
                             index_root=index_root, contract_root=contract_root, code_search_db=None)

    def test_optional_hybrid_without_index_keeps_real_scoped_read_glob_grep(self):
        index_root = self.root / "empty-index"
        index_root.mkdir()
        before = sorted(self.root.rglob("*"))
        for root in (None, index_root):
            with self.subTest(index_root=root), patch.object(
                preparation_module, "find_spec", side_effect=AssertionError("must check index before dependencies"),
            ) as dependencies, patch.object(
                preparation_module, "ContractIndex", side_effect=AssertionError("no prepared contract index"),
            ) as contract_index:
                preparation = self.prepare(resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "langchain"}),
                                           index_root=root)
                preparation.require_tools()
                self.assertIs(preparation.materials, self.materials)
                statuses = {status.capability: status for status in preparation.tool_statuses}
                self.assertEqual(ToolStatus("file_tools", "required", "ready", ("Read", "Glob", "Grep")),
                                 statuses["file_tools"])
                self.assertEqual(ToolStatus("search_code", "optional", "unavailable",
                                            reason="混合检索索引尚未显式准备"), statuses["search_code"])
                self.assertEqual("unavailable", statuses["contract_trace"].status)
                self.assertEqual(ToolStatus("investigation", "required", "ready", ("Investigation",)),
                                 statuses["investigation"])
                self.assertIsNone(preparation.search_code)
                self.assertIsNone(preparation.contract_index)
                tools = {tool.name: tool for tool in preparation.file_tools}
                self.assertEqual({"Read", "Glob", "Grep"}, set(tools))
                self.assertEqual({"paths": ["repos/allowed/Main.java"], "truncated": False},
                                 json.loads(tools["Glob"].invoke({"pattern": "**/*.java"})))
                self.assertEqual({"matches": [{"path": "repos/allowed/Main.java", "line": 2,
                                               "text": " int total = 1;"}], "truncated": False},
                                 json.loads(tools["Grep"].invoke({"pattern": "total", "literal": True})))
                read = json.loads(tools["Read"].invoke({"path": "repos/allowed/Main.java", "offset": 2, "limit": 1}))
                self.assertEqual(("repos/allowed/Main.java", 2, 2, "2:  int total = 1;"),
                                 (read["path"], read["lineStart"], read["lineEnd"], read["content"]))
                rejected = json.loads(tools["Read"].invoke({"path": "repos/excluded/Other.java"}))
                self.assertIn("路径不在本轮授权资料范围内", rejected["error"])
                dependencies.assert_not_called()
                contract_index.assert_not_called()
        self.assertEqual(before, sorted(self.root.rglob("*")))

    def test_disabled_contract_does_not_probe_existing_file_or_construct_index(self):
        # A sentinel, deliberately not a valid SQLite index; disabled means it is never inspected.
        sentinel = self.write_fixture("contract-index/contracts.sqlite", "not an index")
        self.assertTrue(sentinel.is_file())
        profile = resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                                   "BUSINESS_CODE_AGENT_WORKFLOW": "basic"})
        with patch.object(Path, "is_file", side_effect=AssertionError("disabled index must not be probed")) as probe, \
                patch.object(preparation_module, "ContractIndex", side_effect=AssertionError("disabled")) as constructor:
            preparation = self.prepare(profile, contract_root=sentinel.parent)
            preparation.require_tools()
        probe.assert_not_called()
        constructor.assert_not_called()
        self.assertEqual((
            ToolStatus("file_tools", "required", "ready", ("Read", "Glob", "Grep")),
            ToolStatus("search_code", "disabled", "disabled"),
            ToolStatus("contract_trace", "disabled", "disabled"),
        ), preparation.tool_statuses)
        self.assertIsNone(preparation.contract_index)
        self.assertEqual({"Read", "Glob", "Grep"}, {tool.name for tool in preparation.file_tools})
        self.assertEqual("not an index", sentinel.read_text(encoding="utf-8"))

    def test_required_missing_indexes_raise_through_require_tools(self):
        profile = resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                                   "BUSINESS_CODE_AGENT_RETRIEVAL": "hybrid",
                                   "BUSINESS_CODE_AGENT_CONTRACTS": "on"})
        with patch.object(preparation_module, "find_spec", side_effect=AssertionError("no index")) as dependencies, \
                patch.object(preparation_module, "ContractIndex", side_effect=AssertionError("no index")) as constructor:
            preparation = self.prepare(profile, index_root=self.root / "missing-hybrid",
                                       contract_root=self.root / "missing-contract")
            with self.assertRaises(RequiredToolUnavailable) as raised:
                preparation.require_tools()
        statuses = {status.capability: status for status in preparation.tool_statuses}
        for capability in ("search_code", "contract_trace"):
            self.assertEqual(("required", "unavailable", (), None),
                             (statuses[capability].policy, statuses[capability].status,
                              statuses[capability].names, statuses[capability].version))
            self.assertIn(f"{capability}（{statuses[capability].reason}）", str(raised.exception))
        dependencies.assert_not_called()
        constructor.assert_not_called()
        self.assertFalse((self.root / "missing-hybrid").exists())
        self.assertFalse((self.root / "missing-contract").exists())


class ExecutionSnapshotTest(TemporaryWorkspaceTest):
    def test_snapshot_records_profile_materials_tool_versions_model_and_settings(self):
        (self.root / "knowledge/ast").mkdir(parents=True)
        materials = MaterialManifest.from_workspace(self.root, allowed=("ast",), ast_version="ast-version-7")
        profile = resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "langchain",
                                   "BUSINESS_CODE_AGENT_WORKFLOW": "tracked",
                                   "BUSINESS_CODE_AGENT_ANSWER_MODE": "facts",
                                   "BUSINESS_CODE_AGENT_RETRIEVAL": "auto"})
        # Synthetic public statuses test serialization, not index readiness or search quality.
        tools = (ToolStatus("file_tools", "required", "ready", ("Read", "Glob", "Grep")),
                 ToolStatus("search_code", "optional", "ready", ("SearchCode",), "hybrid-version-3"),
                 ToolStatus("contract_trace", "disabled", "disabled"))
        model = Mock(spec=["model_name"], model_name="snapshot-model")
        runtime = LangChainRuntime(profile=profile, model=model, max_calls=4, max_seconds=12, environment={
            "BUSINESS_CODE_MODEL_NAME": "unused-environment-model",
            "BUSINESS_CODE_AGENT_OUTPUT_TOKENS": "800",
            "BUSINESS_CODE_MODEL_ENABLE_THINKING": "false",
            "BUSINESS_CODE_MODEL_TEMPERATURE": ".1",
            "BUSINESS_CODE_AGENT_CONTEXT_CHARS": "12000",
            "BUSINESS_CODE_MODEL_TIMEOUT": "30",
        })
        expected_settings = {
            "agentVersion": 11, "requestedModel": "snapshot-model", "maxCalls": 4, "maxSeconds": 12.0,
            "answerMode": "facts", "workflowMode": "tracked", "outputTokens": "800", "thinking": "false",
            "temperature": ".1", "contextChars": "12000", "networkTimeoutSeconds": "30", "maxRetries": 0,
        }
        snapshot = execution_snapshot(profile, materials, tools, settings=runtime.execution_settings())
        self.assertEqual({
            "profileId": "tracked-legacy-v1", "investigation": "tracked", "answerMode": "facts",
            "model": "snapshot-model", "settings": expected_settings,
            "materials": [{"kind": "ast", "label": "AST 结构资料", "path": "knowledge/ast",
                           "directory": str(self.root / "knowledge/ast"), "purpose": "定位手动生成的版本化结构资料",
                           "status": "available", "available": True, "version": "ast-version-7"}],
            "tools": [
                {"capability": "file_tools", "policy": "required", "status": "ready",
                 "names": ["Read", "Glob", "Grep"], "version": None, "reason": None},
                {"capability": "search_code", "policy": "optional", "status": "ready",
                 "names": ["SearchCode"], "version": "hybrid-version-3", "reason": None},
                {"capability": "contract_trace", "policy": "disabled", "status": "disabled",
                 "names": [], "version": None, "reason": None},
            ],
        }, json.loads(json.dumps(snapshot)))
        self.assertEqual([], model.mock_calls)

    def test_snapshot_without_profile_or_settings_does_not_invent_metadata(self):
        self.assertEqual({
            "profileId": None, "investigation": None, "answerMode": None, "model": None,
            "settings": {}, "materials": [], "tools": [],
        }, execution_snapshot(None, MaterialManifest(())))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.agent.pi import PiAgentRuntime, PiConfigurationError
from business_code_agent.query.agent.pi.events import (PiEventAdapter, answer_body,
                                                       denied_tool_path, goal_coverage)
from business_code_agent.query.capabilities import prepare_pi_capabilities
from business_code_agent.query.context import RunContext
from business_code_agent.query.materials import MaterialManifest, MaterialSource
from business_code_agent.query.profile import PI, resolve_profile
from business_code_agent.query.runtime import RuntimeErrorBase
from business_code_agent.query.service import _default_runtime
from business_code_agent.query.workspace import WorkspaceError, WorkspaceManager


FAKE_PI = Path(__file__).parent / "fixtures" / "fake_pi_rpc.py"


def project_config(root: Path, repositories=("a", "b")) -> Path:
    config = root / "project.json"
    config.write_text(json.dumps({
        "project": {"id": "pi-test", "name": "Pi Test"},
        "repositories": [
            {"id": repository_id, "gitUrl": "unused",
             "localPath": str(root / "sources" / repository_id)}
            for repository_id in repositories
        ],
    }), encoding="utf-8")
    return config


class PiRuntimeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("a", "b"):
            source = self.root / "sources" / repository_id
            source.mkdir(parents=True)
            (source / "hello.txt").write_text(f"hello-{repository_id}\n", encoding="utf-8")
        self.config = project_config(self.root)
        self.manager = WorkspaceManager(project_config=self.config,
                                        workspace_root=self.root / "workspaces")
        self.project = ProjectContext("pi-test", "Pi Test", self.config, self.root,
                                      self.root / "knowledge.db", True)

    def context_and_prepared(self, mode="combined", repositories=("a", "b")):
        workspace = self.manager.prepare_pi(workspace_key="conversation-test",
                                            mode=mode, repository_ids=list(repositories))
        manifest = MaterialManifest.from_workspace(
            workspace.path, set(workspace.repository_ids),
            allowed=PI.materials, mode=mode, ast_version=workspace.ast_version_id,
            ast_status=workspace.ast_status)
        context = RunContext(
            run_id="RUN-pi-test", project=self.project, profile=PI,
            repository_ids=workspace.repository_ids, workspace=workspace.path,
            materials=manifest.sources, session_id=None)
        prepared = prepare_pi_capabilities(context, version="9.9.9", mode=mode)
        return context, prepared, workspace

    def runtime(self, scenario="normal", **kwargs):
        environment = {"FAKE_PI_SCENARIO": scenario, **kwargs.pop("environment", {})}
        return PiAgentRuntime(
            profile=PI, environment=environment,
            command=[sys.executable, str(FAKE_PI)],
            session_root=self.root / "pi-sessions", **kwargs)

    def test_answer_rewrites_temporary_citation_paths(self):
        workspace = self.root / "answer-workspace"
        workspace.mkdir()
        adapter = PiEventAdapter(lambda event: None, workspace=workspace,
                                 allowed_roots=())
        adapter.reads = [{"path": "repos/a/Main.java", "content": "line1\n",
                          "lineStart": 1, "lineEnd": 1}]
        adapter.assistant_messages = [{
            "role": "assistant",
            "content": f"见 [Main.java:1]({workspace}/repos/a/Main.java:1)。",
        }]
        self.assertEqual("见 [Main.java:1](repos/a/Main.java:1)。", adapter.answer())

    def test_system_prompt_requires_route_and_handler_chain(self):
        context, prepared, _ = self.context_and_prepared(mode="none")
        prompt = self.runtime()._system_prompt(context, prepared, mode="none")["text"]
        self.assertIn("找到第一个入口", prompt)
        self.assertIn("注册点与实际处理器", prompt)
        self.assertIn("题目要求的各项都有源码依据后停止调查", prompt)
        self.assertIn("## 目标覆盖", prompt)
        guidance = "## 当前工程导航\n任务入口由当前系统的注册点定位。"
        prompt = self.runtime()._system_prompt(
            replace(context, system_guidance=guidance), prepared, mode="none")["text"]
        self.assertIn(guidance, prompt)

    def test_layered_prompt_separates_behavior_and_authorized_project_context(self):
        context, prepared, _ = self.context_and_prepared(mode="none", repositories=("a",))
        guidance = "## 当前工程导航\n按系统查找入口。"
        runtime = self.runtime(prompt_variant="layered")
        prompt = runtime._system_prompt(replace(context, system_guidance=guidance), prepared, mode="none")
        self.assertEqual(["behavior", "project", "navigation"], [part["kind"] for part in prompt["sections"]])
        self.assertEqual(prompt["text"], "".join(part["text"] for part in prompt["sections"]))
        self.assertIn("方法内部的条件与分支以正文为准", prompt["text"])
        self.assertIn("repos/a/", prompt["text"])
        self.assertNotIn("repos/b/", prompt["text"])
        self.assertNotIn("Pi 原生工具", prompt["text"])
        self.assertEqual("layered", runtime.with_model("deepseek/other").prompt_variant)

    def test_goal_coverage_requires_delivered_read_evidence(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\n",
                  "lineStart": 10, "lineEnd": 11},
                 {"path": "knowledge/system-guides/a/SYSTEM.md", "content": "路线说明",
                  "lineStart": 1, "lineEnd": 1}]
        answer = (
            "答案正文。\n\n## 目标覆盖\n"
            "- [已核实] 创建入口：`repos/a/Main.java:10-11`\n"
            "- [已核实] 审批入口：`repos/a/Missing.java:1`\n"
            "- [已核实] 混合证据：`repos/a/Main.java:10-11` 与 `repos/a/Other.java:1`\n"
            "- [已核实] 仅有导航：`knowledge/system-guides/a/SYSTEM.md:1`\n"
            "- [部分] 还款：已知 command，缺 Handler\n"
            "- [未核实] 状态机例外\n")
        coverage = goal_coverage(answer, reads, workspace="/workspace")
        self.assertEqual(6, coverage["total"])
        self.assertEqual(1, coverage["verified"])
        self.assertEqual(1, coverage["partial"])
        self.assertEqual(4, coverage["unverified"])
        self.assertFalse(coverage["complete"])
        self.assertEqual(["repos/a/Main.java:10-11"],
                         coverage["goals"][0]["confirmedReferences"])
        self.assertEqual("答案正文。", answer_body(answer))

    def test_goal_coverage_marks_complete_only_when_every_goal_is_verified(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\n",
                  "lineStart": 1, "lineEnd": 1}]
        answer = "## 目标覆盖\n- [已核实] 入口：`repos/a/Main.java:1`\n"
        coverage = goal_coverage(answer, reads)
        self.assertTrue(coverage["complete"])
        self.assertEqual(1, coverage["verified"])
        self.assertIsNone(goal_coverage("没有覆盖小节。", reads)["complete"])

    def test_normal_run_translates_events_and_persists_session(self):
        context, prepared, _ = self.context_and_prepared()
        calls = []
        events = []
        result = self.runtime().ask("hello?", context=context, prepared=prepared,
                                    event_callback=events.append,
                                    call_callback=calls.append, mode="combined")
        self.assertEqual("completed", result.status)
        self.assertEqual("fake answer from pi", result.answer)
        self.assertTrue(Path(result.runtime_session_id).is_file())
        self.assertEqual(30, result.usage["inputTokens"])
        self.assertEqual(13, result.usage["outputTokens"])
        self.assertEqual(2, result.usage["modelCalls"])
        self.assertEqual(1, result.usage["toolCalls"])
        self.assertEqual(["fake-model"], result.usage["reportedModels"])
        self.assertEqual("9.9.9", result.usage["piVersion"])
        self.assertEqual(16, len(result.usage["systemPromptSha256"]))
        self.assertEqual("fake/fake-model", result.usage["actualModel"])
        self.assertIsNone(result.usage["investigation"]["goalCoverage"]["complete"])
        self.assertEqual([1, 1, 2, 2], [call["attempt"] for call in calls])
        self.assertEqual(["running", "completed", "running", "completed"],
                         [call["status"] for call in calls])
        self.assertEqual(["investigation", "investigation", "investigation", "answer"],
                         [call["phase"] for call in calls])
        self.assertTrue(any(event["eventType"] == "tool"
                            and event["payload"]["name"] == "read"
                            and event["payload"]["status"] == "completed"
                            for event in events))
        self.assertTrue(any(event["eventType"] == "text"
                            and event["payload"]["text"] == "fake answer from pi"
                            for event in events))
        self.assertEqual("completed", events[-1]["payload"]["phase"])

    def test_follow_up_reuses_the_pi_session(self):
        context, prepared, _ = self.context_and_prepared()
        runtime = self.runtime()
        first = runtime.ask("first", context=context, prepared=prepared, mode="combined")
        second = runtime.ask("second", context=context, prepared=prepared,
                             session_id=first.runtime_session_id, mode="combined")
        self.assertEqual(first.runtime_session_id, second.runtime_session_id)
        lines = Path(second.runtime_session_id).read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, sum(1 for line in lines if json.loads(line)["type"] == "prompt"))

    def test_cancel_aborts_and_returns_cancelled(self):
        context, prepared, _ = self.context_and_prepared()
        runtime = self.runtime("cancel", timeout=30)
        cancelled = threading.Event()
        results = []
        errors = []

        def run():
            try:
                results.append(runtime.ask("cancel me", context=context, prepared=prepared,
                                           cancel_check=cancelled.is_set, mode="combined"))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.6)
        cancelled.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("cancelled", results[0].status)
        self.assertEqual("已停止。", results[0].answer)

    def test_timeout_is_reported_after_abort(self):
        context, prepared, _ = self.context_and_prepared()
        runtime = self.runtime("timeout", timeout=1.0)
        with self.assertRaises(RuntimeErrorBase) as caught:
            runtime.ask("slow", context=context, prepared=prepared, mode="combined")
        self.assertIn("超时", str(caught.exception))

    def test_model_error_and_missing_answer_are_failures(self):
        context, prepared, _ = self.context_and_prepared()
        with self.assertRaises(RuntimeErrorBase) as caught:
            self.runtime("error").ask("fail", context=context, prepared=prepared, mode="combined")
        self.assertIn("fake provider down", str(caught.exception))
        with self.assertRaises(RuntimeErrorBase) as caught:
            self.runtime("no_answer").ask("nothing", context=context, prepared=prepared,
                                          mode="combined")
        self.assertIn("没有返回最终答案", str(caught.exception))

    def test_length_stop_reason_marks_incomplete(self):
        context, prepared, _ = self.context_and_prepared()
        result = self.runtime("length").ask("long", context=context, prepared=prepared,
                                            mode="combined")
        self.assertEqual("incomplete", result.status)
        self.assertTrue(result.answer.startswith("（回答因模型输出长度限制被截断）"))
        self.assertEqual("length", result.usage["stopReason"])

    def test_configured_model_and_extension_are_checked(self):
        context, prepared, _ = self.context_and_prepared()
        with self.assertRaises(PiConfigurationError):
            self.runtime(model="other-model").ask("hello", context=context,
                                                  prepared=prepared, mode="combined")
        with self.assertRaises(PiConfigurationError):
            self.runtime(environment={"FAKE_PI_NO_EXTENSION": "1"}).ask(
                "hello", context=context, prepared=prepared, mode="combined")

    def test_tool_path_outside_authorization_aborts_the_run(self):
        context, prepared, _ = self.context_and_prepared()
        events = []
        with self.assertRaises(RuntimeErrorBase) as caught:
            self.runtime("bad_path").ask("read secrets", context=context, prepared=prepared,
                                         event_callback=events.append, mode="combined")
        self.assertIn("工具访问越界", str(caught.exception))
        self.assertTrue(any(event["eventType"] == "error"
                            and event["payload"].get("subtype") == "ToolPathDenied"
                            for event in events))

    def test_models_catalog_reads_pi_without_leaking_provider_details(self):
        catalog = self.runtime().models_catalog()
        self.assertEqual("9.9.9", catalog["piVersion"])
        self.assertEqual("fake/fake-model", catalog["defaultModel"])
        self.assertEqual(["fake/fake-model", "zcode/glm-5.3-flash"],
                         [item["id"] for item in catalog["items"]])
        self.assertEqual({"id", "name", "provider", "contextWindow",
                          "maxTokens", "reasoning", "input"},
                         set(catalog["items"][0]))
        serialized = json.dumps(catalog)
        self.assertNotIn("provider.internal", serialized)
        self.assertNotIn("secret", serialized)

        configured = self.runtime(model="zcode/glm-5.3-flash",
                                  environment={"FAKE_PI_ECHO_MODEL": "1"}).models_catalog()
        self.assertEqual("zcode/glm-5.3-flash", configured["configuredModel"])
        self.assertEqual("zcode/glm-5.3-flash", configured["selectedModel"])

    def test_model_environment_sets_default_model(self):
        runtime = self.runtime(environment={"BUSINESS_CODE_PI_MODEL": "deepseek/gpt-6-luna"})
        self.assertEqual("deepseek/gpt-6-luna", runtime.model)
        self.assertEqual("deepseek/gpt-6-luna",
                         runtime.execution_settings()["requestedModel"])

    def test_with_model_rebinds_only_the_selected_run(self):
        runtime = self.runtime()
        selected = runtime.with_model("zcode/glm-5.3-flash")
        self.assertIsNot(runtime, selected)
        self.assertEqual("", runtime.model)
        self.assertEqual("zcode/glm-5.3-flash", selected.model)
        self.assertEqual("zcode/glm-5.3-flash",
                         selected.execution_settings()["requestedModel"])
        self.assertEqual("pi", selected.runtime_name.lower())

    def test_command_exposes_only_read_only_tools_and_loads_search_extension(self):
        runtime = self.runtime()
        root = self.root / "pi-sessions"
        command = runtime._command(root, None, "system prompt")
        self.assertIn("read,grep,find,ls,codemode", command)
        self.assertNotIn("bash", command)
        self.assertNotIn("edit", command)
        self.assertNotIn("write", command)
        self.assertIn("--no-context-files", command)
        self.assertIn("--no-extensions", command)
        self.assertIn("builtin:codemode", command)
        self.assertTrue(any(item.endswith("codeagent_search.js") for item in command))

    def test_path_guard_accepts_authorized_symlinks_and_rejects_escapes(self):
        repo = (self.root / "sources" / "a").resolve()
        workspace = (self.root / "workspaces" / "pi-test" / "pi" / "key").resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(denied_tool_path("read", {"path": "repos/a/hello.txt"},
                                           workspace=workspace, allowed_roots=(repo,)))
        self.assertIsNotNone(denied_tool_path("read", {"path": "/etc/passwd"},
                                              workspace=workspace, allowed_roots=(repo,)))
        self.assertIsNotNone(denied_tool_path("read", {"path": "../outside.txt"},
                                              workspace=workspace, allowed_roots=(repo,)))
        self.assertIsNotNone(denied_tool_path("read", {"path": "~/secret"},
                                              workspace=workspace, allowed_roots=(repo,)))


class PiWorkspaceAndCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for repository_id in ("a", "b"):
            source = self.root / "sources" / repository_id
            source.mkdir(parents=True)
            (source / "Main.java").write_text("class Main {}\n", encoding="utf-8")
        self.business = self.root / "business"
        self.business.mkdir()
        (self.business / "terms.md").write_text("术语说明\n", encoding="utf-8")
        self.code_map = self.root / "code-map"
        self.code_map.mkdir()
        (self.code_map / "index.md").write_text("导航\n", encoding="utf-8")
        self.ast = self.root / "ast"
        self.ast.mkdir()
        (self.ast / "structure.md").write_text("结构\n", encoding="utf-8")
        self.config = project_config(self.root)
        self.manager = WorkspaceManager(project_config=self.config,
                                        workspace_root=self.root / "workspaces")

    def test_prepare_pi_refreshes_scope_links_in_a_stable_workspace(self):
        first = self.manager.prepare_pi(workspace_key="conversation-1", mode="none",
                                        repository_ids=["a"])
        self.assertTrue((first.path / "repos" / "a").is_dir())
        self.assertFalse((first.path / "repos" / "b").exists())
        self.assertFalse((first.path / "knowledge" / "business-context").exists())
        second = self.manager.prepare_pi(workspace_key="conversation-1", mode="none",
                                         repository_ids=["b"])
        self.assertEqual(first.path, second.path)
        self.assertFalse((second.path / "repos" / "a").exists())
        self.assertTrue((second.path / "repos" / "b").is_dir())

    def test_explicit_empty_repository_scope_is_rejected(self):
        with self.assertRaisesRegex(WorkspaceError, "未包含任何代码仓库"):
            self.manager.prepare_pi(workspace_key="empty", mode="none", repository_ids=[])
        self.assertFalse((self.manager.root / "pi" / "empty").exists())

    def test_mode_material_mounts(self):
        none = self.manager.prepare_pi(workspace_key="none", mode="none",
                                       business_context_source=self.business,
                                       repository_ids=["a"])
        self.assertFalse((none.path / "knowledge" / "business-context").exists())
        backbone = self.manager.prepare_pi(workspace_key="backbone", mode="backbone",
                                           business_context_source=self.business,
                                           repository_ids=["a"])
        self.assertEqual(self.business.resolve(),
                         (backbone.path / "knowledge" / "business-context").resolve())
        ast = self.manager.prepare_pi(workspace_key="ast", mode="ast",
                                      code_map_source=self.ast)
        self.assertFalse((ast.path / "knowledge" / "generated-code-map").exists())
        self.assertFalse((ast.path / "knowledge" / "business-context").exists())
        combined = self.manager.prepare_pi(
            workspace_key="combined", mode="combined",
            business_context_source=self.business, code_map_source=self.code_map)
        self.assertEqual(self.business.resolve(),
                         (combined.path / "knowledge" / "business-context").resolve())
        self.assertEqual(self.code_map.resolve(),
                         (combined.path / "knowledge" / "generated-code-map").resolve())
        (combined.path / "knowledge" / "ast").symlink_to(self.ast, target_is_directory=True)
        scoped = self.manager.prepare_pi(
            workspace_key="combined", mode="combined", repository_ids=["a"],
            code_map_source=self.code_map)
        self.assertEqual(combined.path, scoped.path)
        self.assertFalse((scoped.path / "knowledge" / "generated-code-map").exists())
        self.assertFalse((scoped.path / "knowledge" / "ast").exists())

    def test_pi_capabilities_follow_the_material_mode(self):
        repository = self.root / "sources" / "a"
        project = ProjectContext("pi-test", "Pi Test", self.config, self.root,
                                 self.root / "knowledge.db", True)
        base = MaterialSource("source", "代码仓库 a", "repos/a", repository, "available",
                              repository_id="a")
        requirements = MaterialSource("requirements", "需求原文", "requirements", None, "missing")
        business = MaterialSource("business_context", "业务补充知识", "knowledge/business-context",
                                  self.business, "available")
        ast = MaterialSource("ast", "AST 关系索引", None, None, "missing")
        code_map = MaterialSource("code_map", "自动代码地图", "knowledge/generated-code-map",
                                  self.code_map, "available")
        context = RunContext(run_id="RUN", project=project, profile=PI,
                             repository_ids=("a",), workspace=self.root,
                             materials=(base, requirements), session_id=None)
        none = prepare_pi_capabilities(context, version="1", mode="none")
        statuses = {status.capability: status for status in none.statuses}
        self.assertEqual(("required", "ready"), (statuses["file_tools"].policy,
                                                 statuses["file_tools"].status))
        self.assertEqual(("disabled", "disabled"), (statuses["business_context"].policy,
                                                     statuses["business_context"].status))
        self.assertEqual("disabled", statuses["ast"].status)
        self.assertEqual(("read", "grep", "find", "ls", "codemode"),
                         statuses["file_tools"].names)

        context = RunContext(run_id="RUN", project=project, profile=PI,
                             repository_ids=("a",), workspace=self.root,
                             materials=(base, requirements, business, ast, code_map),
                             session_id=None)
        combined = prepare_pi_capabilities(context, version="1", mode="combined")
        statuses = {status.capability: status for status in combined.statuses}
        self.assertEqual("ready", statuses["business_context"].status)
        self.assertEqual("missing", statuses["ast"].status)
        self.assertEqual("ready", statuses["code_map"].status)
        self.assertEqual("disabled", statuses["search_code"].status)


class PiProfileAndDefaultRuntimeTest(unittest.TestCase):
    def test_runtime_and_profile_selection(self):
        self.assertEqual(PI, resolve_profile({}))
        self.assertEqual(PI, resolve_profile({"BUSINESS_CODE_AGENT_RUNTIME": "pi"}))
        self.assertEqual(PI, resolve_profile({"BUSINESS_CODE_AGENT_PROFILE": "pi-v1"}))
        with patch.dict(os.environ, {}, clear=True):
            runtime = _default_runtime(project_id="pi", project_data_root=Path("/tmp/pi-data"))
        self.assertEqual("PI", runtime.runtime_name)
        self.assertEqual(PI, runtime.profile)
        self.assertEqual("pi-sessions", runtime.session_root.name)
        self.assertEqual("pi-data", runtime.session_root.parent.name)

    def test_online_runtime_ignores_legacy_profile_and_runtime_selection(self):
        with patch.dict(os.environ, {
            "BUSINESS_CODE_AGENT_PROFILE": "core-v1",
            "BUSINESS_CODE_AGENT_RUNTIME": "langchain",
            "BUSINESS_CODE_AGENT_WORKFLOW": "coordinated",
            "BUSINESS_CODE_AGENT_RETRIEVAL": "hybrid",
        }, clear=True):
            runtime = _default_runtime(index_root=Path("/tmp/legacy-index"), project_id="legacy")
        self.assertEqual("PI", runtime.runtime_name)
        self.assertEqual(PI, runtime.profile)


if __name__ == "__main__":
    unittest.main()

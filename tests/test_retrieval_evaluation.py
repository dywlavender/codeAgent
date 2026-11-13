"""Evidence coverage, frozen inputs, budget boundaries, and model isolation."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from business_code_agent.codeintel.retrieval import pack_context
from business_code_agent.evaluation.fixed_context import FixedContextRuntime, answer_context
from business_code_agent.evaluation.retrieval import (evaluate, evidence_metrics, materialize_candidates,
                                                       prepare, read_json, validate_suite, write_json)
from business_code_agent.evaluation.retrieval_service import RetrievalEvaluationService
from business_code_agent.platform.projects import ProjectContext, ProjectRegistry
from business_code_agent.query.runtime import RuntimeResult


def make_context(root, project_id="test"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir()
    (root / "repo/main.py").write_text("def settle(order):\n    if order.done:\n        return\n    save(order)\n")
    config = root / "project.json"
    config.write_text(json.dumps({"project": {"id": project_id},
                                  "repositories": [{"id": "repo", "localPath": "repo"}]}))
    return ProjectContext.legacy(root / ".data/knowledge.db", config)


def suite(project_id="test"):
    return {"id": "evidence-v1", "name": "回归", "projectId": project_id, "annotationStatus": "draft",
            "cases": [{"id": "settle", "question": "settle 的 order.done 检查在哪里？",
                       "split": "development", "referenceAnswer": "GOLD_ANSWER_DO_NOT_SEND",
                       "evidence": [{"id": "guard", "role": "condition", "alternatives": [[{
                           "sourceId": "repo", "path": "main.py", "quote": "    if order.done:\n        return"}]]}]}]}


class ContextRuntime:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def build_command(self, prompt, **kwargs):
        return ["test-runtime", "--model", self.model]

    def ask(self, prompt, *, workspace, **kwargs):
        self.calls.append((prompt, list(Path(workspace).iterdir())))
        return RuntimeResult("main.py:2-3 检查 done", None, usage={"reportedModels": [self.model]})


class RetrievalEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.context = make_context(self.root / "one")
        self.output = self.context.evaluations_root / "retrieval/retrieval-test"

    def test_frozen_corpus_and_independent_coverage(self):
        prepare(self.context, suite(), self.output)
        (self.root / "one/repo/main.py").write_text("replaced source")
        result = evaluate(self.output, "test", budgets=[500])
        row = read_json(self.output / "strategies/lexical/settle-500.json")
        self.assertEqual(1, row["candidateMetrics"]["coverage"])
        self.assertEqual(1, row["contextMetrics"]["coverage"])
        self.assertIn("if order.done", row["context"])
        self.assertIsNone(row["sufficiencyReview"])
        self.assertEqual("draft", result["annotationStatus"])
        self.assertNotIn("GOLD_ANSWER_DO_NOT_SEND", (self.output / "corpus.json").read_text())
        with self.assertRaises(ValueError):
            evaluate(self.output, "other-project")

    def test_legacy_corpus_includes_configured_requirements(self):
        requirements = self.context.config_path.parent / "business-requirements"
        requirements.mkdir()
        original = "外部成功只表示付款完成，中台入账后业务才成功。"
        (requirements / "status.md").write_text(original, encoding="utf-8")
        config = read_json(self.context.config_path)
        config["requirementsRoot"] = requirements.name
        write_json(self.context.config_path, config)
        protocol = prepare(self.context, suite(), self.output)
        documents = read_json(self.output / "corpus.json")["documents"]
        requirement = next(d for d in documents if d["sourceId"] == "requirements")
        self.assertEqual(original, requirement["content"])
        self.assertEqual("requirement", requirement["kind"])
        self.assertEqual(str(requirements), next(s["root"] for s in protocol["sources"] if s["id"] == "requirements"))

    def test_registered_corpus_uses_managed_requirements(self):
        configured = self.context.config_path.parent / "requirements"
        configured.mkdir()
        (configured / "status.md").write_text("unimported configured material", encoding="utf-8")
        registry = ProjectRegistry(self.root / "platform")
        context = registry.register(self.context.config_path)
        (context.requirements_root / "status.md").write_text("managed project material", encoding="utf-8")
        output = context.evaluations_root / "retrieval/managed-test"
        prepare(context, suite(), output)
        documents = read_json(output / "corpus.json")["documents"]
        requirements = [d for d in documents if d["sourceId"] == "requirements"]
        self.assertEqual(["managed project material"], [d["content"] for d in requirements])
        self.assertFalse(context.ast_root.exists() and any(context.ast_root.iterdir()))

    def test_stale_quote_prevents_scoring(self):
        data = suite()
        data["cases"][0]["evidence"][0]["alternatives"][0][0]["quote"] = "nonexistent guarantee"
        with self.assertRaisesRegex(ValueError, "引文"):
            prepare(self.context, data, self.output)
        self.assertFalse((self.output / "protocol.json").exists())

    def test_ambiguous_quote_requires_locator(self):
        (self.root / "one/repo/main.py").write_text("save(order)\nsave(order)\n")
        data = suite()
        anchor = data["cases"][0]["evidence"][0]["alternatives"][0][0]
        anchor["quote"] = "save(order)"
        with self.assertRaisesRegex(ValueError, "不唯一"):
            prepare(self.context, data, self.output)
        anchor["lineStart"] = 2
        prepare(self.context, data, self.output)

    def test_project_binding_with_identical_case_and_repo_ids(self):
        other = make_context(self.root / "two", "other")
        with self.assertRaisesRegex(ValueError, "工程"):
            prepare(other, suite(), self.output)
        validate_suite(suite("other"), "other")

    def test_all_relation_endpoints_and_alternative_proofs(self):
        a = {"sourceId": "a", "path": "A.java", "lineStart": 2, "lineEnd": 4}
        b = {"sourceId": "b", "path": "B.java", "lineStart": 8, "lineEnd": 10}
        groups = [{"id": "link", "role": "relation", "alternatives": [[a, b]]}]
        self.assertEqual(0, evidence_metrics(groups, [a])["coverage"])
        self.assertEqual(1, evidence_metrics(groups, [a, b])["relationCoverage"])
        groups[0]["alternatives"].append([a])
        self.assertEqual(1, evidence_metrics(groups, [a])["coverage"])

    def test_budget_counts_headers_trims_whole_lines_and_deduplicates(self):
        item = {"id": "a", "sourceId": "repo", "kind": "source", "path": "A.py",
                "lineStart": 1, "lineEnd": 4, "content": "first\nsecond\nthird\nfourth"}
        selected, text = pack_context([item, item], 62)
        self.assertLessEqual(len(text), 62)
        self.assertEqual(1, text.count("first"))
        self.assertTrue(all(len(p["content"].splitlines()) == p["lineEnd"] - p["lineStart"] + 1 for p in selected))

    def test_import_uses_frozen_original_and_rejects_wrong_version(self):
        prepare(self.context, suite(), self.output)
        documents = read_json(self.output / "corpus.json")["documents"]
        raw = [{"sourceId": "repo", "path": "main.py", "lineStart": 2, "lineEnd": 3,
                "content": "FORGED_ANSWER"}]
        result = materialize_candidates(raw, documents, 40)
        self.assertNotIn("FORGED_ANSWER", result[0]["content"])
        with self.assertRaisesRegex(ValueError, "版本"):
            evaluate(self.output, "test", imported={"projectId": "test", "snapshotId": "other", "cases": {}})
        evaluate(self.output, "test", budgets=[500], strategy="external", imported={
            "projectId": "test", "snapshotId": self.output.name, "cases": {"settle": raw}})
        with self.assertRaisesRegex(ValueError, "已存在"):
            evaluate(self.output, "test", strategy="external", budgets=[500], imported={
                "projectId": "test", "snapshotId": self.output.name, "cases": {"settle": raw}})

    def test_no_unlabelled_evidence_is_automatically_irrelevant(self):
        prepare(self.context, suite(), self.output)
        summary = evaluate(self.output, "test", budgets=[500])
        self.assertNotIn("irrelevant", summary["items"][0])
        self.assertNotIn("sufficient", summary["items"][0]["contextMetrics"])

    def test_replay_keeps_candidates_and_baseline_without_rerunning_corpus_search(self):
        prepare(self.context, suite(), self.output)
        evaluate(self.output, "test", budgets=[500])
        path = self.output / "strategies/lexical/settle-500.json"
        saved = path.read_bytes()
        baseline = read_json(path)
        (self.root / "one/repo/main.py").write_text("changed live source")
        with patch("business_code_agent.evaluation.retrieval.TextRetriever", side_effect=AssertionError("new search")):
            result = evaluate(self.output, "test", budgets=[500], strategy="repacked", candidate_source="lexical", context_policy="focused")
        current = read_json(self.output / "strategies/repacked/settle-500.json")
        self.assertEqual(saved, path.read_bytes())
        self.assertEqual(baseline["candidates"], current["candidates"])
        self.assertTrue(current["comparison"]["candidatesUnchanged"])
        self.assertEqual(baseline["retrievalSeconds"], current["retrievalSeconds"])
        self.assertIsNotNone(current["candidateReplaySeconds"])
        self.assertEqual("lexical", result["retrievalStrategy"])
        self.assertEqual("focused", result["contextPolicy"])
        self.assertNotIn("GOLD_ANSWER_DO_NOT_SEND", current["context"])
        with self.assertRaisesRegex(ValueError, "候选数量"):
            evaluate(self.output, "test", budgets=[500], strategy="changed-limit", candidate_source="lexical", candidate_limit=2)

    def test_selection_validation_rejects_wrong_blank_line_positions(self):
        prepare(self.context, suite(), self.output)
        fake = {"id": "wrong", "sourceId": "repo", "kind": "source", "path": "main.py",
                "lineStart": 1, "lineEnd": 5, "content": "def settle(order):"}
        with patch("business_code_agent.evaluation.retrieval.pack_context", return_value=([fake], "wrong")):
            with self.assertRaisesRegex(ValueError, "行号"):
                evaluate(self.output, "test", budgets=[500])
        self.assertFalse((self.output / "strategies/lexical/summary.json").exists())

    def test_equal_coverage_still_reports_gained_and_lost_groups(self):
        data = suite()
        data["cases"][0]["evidence"].append({"id": "write", "role": "outcome", "alternatives": [[{
            "sourceId": "repo", "path": "main.py", "quote": "    save(order)"}]]})
        prepare(self.context, data, self.output)
        documents = read_json(self.output / "corpus.json")["documents"]
        guard, write = materialize_candidates([
            {"sourceId": "repo", "path": "main.py", "lineStart": 2, "lineEnd": 3},
            {"sourceId": "repo", "path": "main.py", "lineStart": 4, "lineEnd": 4}], documents, 40)
        with patch("business_code_agent.evaluation.retrieval.pack_context", return_value=([guard], guard["content"])):
            evaluate(self.output, "test", budgets=[500])
        with patch("business_code_agent.evaluation.retrieval.pack_context", return_value=([write], write["content"])):
            summary = evaluate(self.output, "test", budgets=[500], strategy="other-context", candidate_source="lexical")
        pair = summary["items"][0]["comparison"]
        self.assertEqual(0, pair["coverageDelta"])
        self.assertEqual(["write"], pair["gainedGroups"])
        self.assertEqual(["guard"], pair["lostGroups"])

    def test_fixed_evidence_same_prompt_different_models_no_gold_or_files(self):
        prepare(self.context, suite(), self.output)
        evaluate(self.output, "test", budgets=[500])
        a, b = ContextRuntime("model-a"), ContextRuntime("model-b")
        first = answer_context(self.output, "test", "settle", budget=500, model="model-a", runtime=a)
        second = answer_context(self.output, "test", "settle", budget=500, model="model-b", runtime=b)
        self.assertEqual(a.calls[0], b.calls[0])
        self.assertEqual([], a.calls[0][1])
        self.assertNotIn("GOLD_ANSWER_DO_NOT_SEND", a.calls[0][0])
        self.assertNotIn("alternatives", a.calls[0][0])
        self.assertEqual(["model-a"], first["reportedModels"])
        self.assertEqual(["model-b"], second["reportedModels"])
        self.assertEqual(2, len(list((self.output / "answers").iterdir())))

    def test_fixed_command_disables_search_settings_and_resume(self):
        runtime = FixedContextRuntime(model="candidate", connection_settings={})
        command = runtime.build_command("question", workspace=str(self.root))
        self.assertEqual("", command[command.index("--tools") + 1])
        self.assertEqual("", command[command.index("--setting-sources") + 1])
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--add-dir", command)
        with self.assertRaises(ValueError):
            runtime.build_command("question", workspace=str(self.root), session_id="previous")

    def test_fixed_runtime_keeps_connection_without_user_instructions(self):
        with patch.dict("os.environ", {}, clear=True):
            runtime = FixedContextRuntime(model="configured-default", connection_settings={
                "model": "requested-alias", "instructions": "UNRELATED_FACTS", "hooks": {"start": "x"},
                "env": {"ANTHROPIC_AUTH_TOKEN": "example-token", "ANTHROPIC_BASE_URL": "https://example.invalid",
                        "UNRELATED_INSTRUCTION": "UNRELATED_FACTS"}})
        self.assertEqual({"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}, set(runtime.environment))
        command = runtime.build_command("question", workspace=str(self.root))
        self.assertIn("requested-alias", command)
        self.assertNotIn("example-token", str(command))
        self.assertNotIn("UNRELATED_FACTS", str(command))

    def test_env_connection_selects_compatible_runtime_and_records_real_limit(self):
        prepare(self.context, suite(), self.output)
        evaluate(self.output, "test", budgets=[500])
        runtime = ContextRuntime("reported-qwen")
        with patch.dict("os.environ", {"BUSINESS_CODE_MODEL_ENABLED": "true", "BUSINESS_CODE_MODEL_PROVIDER": "openai",
                                      "BUSINESS_CODE_MODEL_NAME": "configured-qwen"}, clear=True):
            with patch("business_code_agent.evaluation.fixed_context.CompatibleContextRuntime", return_value=runtime) as factory:
                result = answer_context(self.output, "test", "settle", budget=500)
        factory.assert_called_once_with(model="configured-default")
        self.assertEqual("configured-qwen", result["connection"]["model"])
        self.assertIsNone(result["budgetUsd"])
        self.assertFalse(result["spendLimitSupported"])
        self.assertNotIn("GOLD_ANSWER_DO_NOT_SEND", runtime.calls[0][0])

    def test_service_persists_results_without_generating_ast(self):
        service = RetrievalEvaluationService(self.context)
        service.import_suite(suite())
        started = service.start({"suiteId": "evidence-v1", "budgets": [500]})
        service._active[started["id"]].join(10)
        detail = service.get_run(started["id"])
        self.assertEqual("completed", detail["status"])
        self.assertEqual(1, detail["strategies"][0]["items"][0]["contextMetrics"]["coverage"])
        self.assertFalse(self.context.ast_root.exists())
        compared = service.compare_context(started["id"], {"budgets": [500], "contextPolicy": "focused"})
        self.assertTrue(compared["items"][0]["comparison"]["candidatesUnchanged"])
        self.assertEqual(2, len(service.get_run(started["id"])["strategies"]))
        self.assertFalse(self.context.ast_root.exists())
        old = service.suites_root / "evidence-v1.json"
        data = read_json(old)
        data["annotationStatus"] = "superseded"
        old.write_text(json.dumps(data))
        service.import_suite({**suite(), "id": "evidence-v2"})
        self.assertEqual("evidence-v2", service.list_suites()["items"][0]["id"])
        with self.assertRaisesRegex(ValueError, "修订"):
            service.start({"suiteId": "evidence-v1"})
        with self.assertRaises(ValueError):
            service.import_suite(suite("another-project"))


if __name__ == "__main__":
    unittest.main()

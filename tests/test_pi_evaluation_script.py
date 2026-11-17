from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluation" / "evaluate_pi.py"
SPEC = importlib.util.spec_from_file_location("evaluate_pi", SCRIPT)
assert SPEC and SPEC.loader
evaluate_pi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_pi)


class PiEvaluationMetricsTest(unittest.TestCase):
    def test_trace_metrics_count_reads_searches_and_proxy_waste(self):
        trace = {
            "run": {
                "workspace": "/tmp/workspace",
                "durationMs": 1200,
                "usage": {
                    "modelCalls": 2,
                    "toolCalls": 4,
                    "totalTokens": 100,
                    "stopReason": "stop",
                    "verification": {
                        "semanticProof": False,
                        "readFiles": ["repos/a/X.java"],
                        "references": [
                            {"path": "repos/a/X.java", "line": 10, "lineEnd": 10,
                             "matchedPath": "repos/a/X.java", "confirmed": True},
                            {"path": "repos/b/Y.java", "line": 3, "lineEnd": 3,
                             "confirmed": False},
                        ],
                        "unconfirmed": ["repos/b/Y.java"],
                    },
                },
            },
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "find-1", "name": "find", "status": "completed",
                    "input": {"path": "repos/a", "pattern": "*.java"},
                    "output": "/tmp/workspace/repos/a/X.java\nrepos/a/Z.java\n"}},
                {"eventType": "tool", "payload": {
                    "id": "read-1", "name": "read", "status": "completed",
                    "input": {"path": "/tmp/workspace/repos/a/X.java", "offset": 10,
                              "limit": 2},
                    "output": "line1\nline2\n"}},
                {"eventType": "model", "payload": {"attempt": 2}},
                {"eventType": "tool", "payload": {
                    "id": "find-2", "name": "find", "status": "completed",
                    "input": {"path": "repos/b", "pattern": "*.java"}, "output": ""}},
                {"eventType": "tool", "payload": {
                    "id": "find-3", "name": "find", "status": "completed",
                    "input": {"path": "repos/b", "pattern": "*.java"}, "output": ""}},
            ],
        }
        metrics = evaluate_pi.analyze_trace(trace, ["X.java", "missing.java"])
        self.assertEqual(1, metrics["keyFilesRead"])
        self.assertEqual(2, metrics["keyFilesTotal"])
        self.assertEqual([True, False], [item["read"] for item in metrics["keyFiles"]])
        self.assertEqual(1, metrics["uniqueReadFiles"])
        self.assertEqual(4, metrics["toolCalls"])
        self.assertEqual([[10, 11]], metrics["reads"][0]["ranges"])
        self.assertEqual(2, metrics["candidateFiles"])
        self.assertEqual(["repos/a/Z.java"], metrics["unreadCandidates"])
        self.assertEqual(2, metrics["emptySearches"])
        self.assertEqual(1, metrics["repeatedToolCalls"])
        self.assertEqual(0, metrics["searchesWithoutRead"])
        self.assertEqual(3, metrics["invalidInvestigationProxy"])
        self.assertEqual((1, 2), (metrics["references"]["confirmed"],
                                  metrics["references"]["total"]))
        self.assertEqual({"a": {"reads": 1, "searches": 1},
                          "b": {"reads": 0, "searches": 2}}, metrics["repos"])

    def test_stopping_metrics_separate_novel_and_redundant_post_key_work(self):
        trace = {
            "run": {"workspace": "/tmp/workspace", "usage": {}},
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "read-key", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 1, "limit": 2},
                    "output": "line1\nline2\n"}},
                {"eventType": "tool", "payload": {
                    "id": "grep-1", "name": "grep", "status": "completed",
                    "input": {"path": "repos/a", "pattern": "needle"},
                    "output": "repos/a/Y.java:4:  needle();\n"}},
                {"eventType": "model", "payload": {"attempt": 2}},
                {"eventType": "tool", "payload": {
                    "id": "read-again", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 1, "limit": 2},
                    "output": "line1\nline2\n"}},
                {"eventType": "tool", "payload": {
                    "id": "read-new", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/Z.java", "offset": 1, "limit": 2},
                    "output": "zeta1\nzeta2\n"}},
                {"eventType": "tool", "payload": {
                    "id": "grep-2", "name": "grep", "status": "completed",
                    "input": {"path": "repos/a", "pattern": "needle"},
                    "output": "repos/a/Y.java:4:  needle();\nrepos/a/W.java:9:  needle();\n"}},
                {"eventType": "tool", "payload": {
                    "id": "read-w", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/W.java", "offset": 9, "limit": 2},
                    "output": "w1\nw2\n"}},
                {"eventType": "tool", "payload": {
                    "id": "grep-3", "name": "grep", "status": "completed",
                    "input": {"path": "repos/a", "pattern": "other"},
                    "output": "repos/a/V.java:3:  other();\n"}},
                {"eventType": "tool", "payload": {
                    "id": "ls-1", "name": "ls", "status": "completed",
                    "input": {"path": "repos/a"}, "output": "X.java\nZ.java\n"}},
            ],
        }
        stopping = evaluate_pi.analyze_trace(trace, ["X.java"])["stopping"]
        self.assertEqual(6, stopping["postKeyToolCalls"])
        self.assertEqual(3, stopping["postKeyNovelCalls"])
        self.assertEqual(1, stopping["postKeyRedundantCalls"])
        self.assertEqual(1, stopping["postKeyUnusedSearchCalls"])
        self.assertEqual(1, stopping["postKeyUnclassifiedCalls"])
        self.assertEqual(2, stopping["postKeyNewFiles"])
        self.assertEqual(2, stopping["postKeyNewReadCalls"])
        self.assertEqual(1, stopping["postKeyNovelCandidatePaths"])
        self.assertEqual(1, stopping["postKeyUnusedCandidatePaths"])

    def test_goal_coverage_citations_do_not_inflate_answer_references(self):
        trace = {
            "run": {
                "workspace": "/tmp/workspace",
                "answer": ("见 `repos/a/X.java:1`。\n\n## 目标覆盖\n"
                           "- [已核实] 入口：`repos/a/X.java:1`\n"
                           "- [已核实] 其他：`repos/a/Y.java:2`\n"),
                "usage": {},
            },
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "read-1", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 1, "limit": 1},
                    "output": "line1\n"}},
            ],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(1, refs["total"])
        self.assertEqual(1, refs["confirmed"])
        self.assertEqual(0, refs["unseen"])

    def test_goal_coverage_is_reported_from_usage(self):
        trace = {
            "run": {"workspace": "/tmp/workspace", "usage": {"investigation": {
                "goalCoverage": {
                    "total": 2, "verified": 1, "partial": 1, "unverified": 0,
                    "complete": False, "goals": [],
                },
            }}},
            "events": [],
        }
        coverage = evaluate_pi.analyze_trace(trace)["goalCoverage"]
        self.assertEqual(2, coverage["total"])
        self.assertFalse(coverage["complete"])

    def test_reference_provenance_separates_search_seen_and_unseen(self):
        trace = {
            "run": {
                "workspace": "/tmp/workspace",
                "usage": {"modelCalls": 1, "verification": {
                    "semanticProof": False,
                    "references": [
                        {"path": "repos/a/X.java", "line": 1, "lineEnd": 1,
                         "matchedPath": "repos/a/X.java", "fileRead": True,
                         "confirmed": True},
                        {"path": "Y.java", "line": 7, "lineEnd": 7,
                         "matchedPath": "repos/b/Y.java", "fileRead": False,
                         "confirmed": False},
                        {"path": "Z.java", "line": 3, "lineEnd": 3,
                         "matchedPath": "repos/b/Z.java", "fileRead": False,
                         "confirmed": False},
                        {"path": "notes.md", "line": None, "lineEnd": None,
                         "matchedPath": None, "fileRead": False,
                         "confirmed": False},
                    ],
                }},
            },
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "read-1", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 1, "limit": 2},
                    "output": "line1\nline2\n"}},
                {"eventType": "tool", "payload": {
                    "id": "grep-1", "name": "grep", "status": "completed",
                    "input": {"path": "repos/b", "pattern": "needle"},
                    "output": "repos/b/Y.java:7:  needle();\nrepos/b/Y.java-8-  next();\n"}},
            ],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(4, refs["total"])
        self.assertEqual(1, refs["confirmed"])
        self.assertEqual(1, refs["searchSeen"])
        self.assertEqual(1, refs["unseen"])
        self.assertEqual(1, refs["pathOnly"])
        self.assertEqual(0, refs["fileReadNotRange"])
        self.assertEqual(["repos/b/Z.java:3"], refs["unseenReferences"])

    def test_reference_ellipsis_path_matches_delivered_search_line(self):
        trace = {
            "run": {"usage": {"verification": {"references": [{
                "path": "fineract-core/.../CommandHandlerProvider.java",
                "line": 91, "lineEnd": 91, "matchedPath": None,
                "fileRead": False, "confirmed": False,
            }]}}},
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "grep-1", "name": "grep", "status": "completed",
                    "input": {"path": "repos/fineract-repo", "pattern": "getHandler"},
                    "output": "repos/fineract-repo/fineract-core/src/main/java/org/apache/"
                              "fineract/commands/provider/CommandHandlerProvider.java:91:"
                              "  public NewCommandSourceHandler getHandler(...\n"}},
            ],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(1, refs["searchSeen"])
        self.assertEqual(0, refs["unseen"])

    def test_archived_answer_is_reparsed_for_reference_counts(self):
        trace = {
            "run": {
                "workspace": "/tmp/workspace",
                "answer": "见 [X.java:1](/tmp/workspace/repos/a/X.java:1)。",
                "usage": {"verification": {"references": [
                    {"path": "X.java", "line": 1, "lineEnd": 1, "confirmed": True},
                    {"path": "repos/a/X.java", "line": 1, "lineEnd": 1,
                     "confirmed": True},
                ]}},
            },
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "read-1", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 1, "limit": 2},
                    "output": "line1\nline2\n"}},
            ],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(1, refs["total"])
        self.assertEqual(1, refs["confirmed"])

    def test_grep_context_lines_do_not_become_candidate_paths(self):
        trace = {
            "run": {"workspace": "/tmp/workspace", "usage": {}},
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "grep-1", "name": "grep", "status": "completed",
                    "input": {"path": "repos/a", "pattern": "needle"},
                    "output": ("repos/a/X.java-260-              data: value\n"
                               "repos/a/X.java:261:  needle();\n")}},
            ],
        }
        metrics = evaluate_pi.analyze_trace(trace)
        self.assertEqual(1, metrics["candidateFiles"])
        self.assertEqual(["repos/a/X.java"], metrics["unreadCandidates"])

    def test_label_and_target_references_are_deduplicated(self):
        trace = {
            "run": {
                "workspace": "/tmp/workspace",
                "usage": {"verification": {"references": [
                    {"path": "X.java", "line": 10, "lineEnd": 10,
                     "matchedPath": "repos/a/X.java", "fileRead": True,
                     "confirmed": True},
                    {"path": "/tmp/workspace/repos/a/X.java", "line": 10,
                     "lineEnd": 10, "matchedPath": None, "fileRead": False,
                     "confirmed": False},
                ]}},
            },
            "events": [
                {"eventType": "model", "payload": {"attempt": 1}},
                {"eventType": "tool", "payload": {
                    "id": "read-1", "name": "read", "status": "completed",
                    "input": {"path": "repos/a/X.java", "offset": 10, "limit": 2},
                    "output": "line1\nline2\n"}},
            ],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(1, refs["total"])
        self.assertEqual(1, refs["confirmed"])
        self.assertEqual(1, refs["formatIssues"]["absolute"])
        self.assertEqual(0, refs["formatIssues"]["unresolved"])

    def test_reference_format_counts_unrooted_and_duplicated_paths(self):
        trace = {
            "run": {"workspace": "/tmp/workspace", "usage": {"verification": {
                "references": [
                    {"path": "/tmp/workspace/repos/a/X.java", "line": 1,
                     "lineEnd": 1, "confirmed": False},
                    {"path": "/other/repos/a/Y.java", "line": 2, "lineEnd": 2,
                     "confirmed": False},
                    {"path": "/tmp/workspace/repos/project/pi/CONV-1/repos/a/Z.java",
                     "line": 3, "lineEnd": 3, "confirmed": False},
                ]}}},
            "events": [{"eventType": "model", "payload": {"attempt": 1}}],
        }
        refs = evaluate_pi.analyze_trace(trace)["references"]
        self.assertEqual(3, refs["total"])
        self.assertEqual(3, refs["formatIssues"]["absolute"])
        self.assertEqual(1, refs["formatIssues"]["unrooted"])
        self.assertEqual(1, refs["formatIssues"]["duplicated"])
        self.assertEqual(2, refs["formatIssues"]["malformed"])
        self.assertEqual(0, refs["formatIssues"]["unresolved"])

    def test_review_requires_verdict_quote_and_evidence(self):
        checks = ["检查项"]
        review = {"reviewSchemaVersion": 2, "checksReview": [{
            "index": 1, "check": "检查项", "verdict": "partial",
            "answerQuotes": ["答案原句"], "evidence": ["repos/a/X.java:10"],
            "reason": "只覆盖部分路径"}]}
        evaluate_pi.check_review(review, checks, answer="答案原句，见 repos/a/X.java:10。")
        missing_quote = json.loads(json.dumps(review))
        missing_quote["checksReview"][0]["answerQuotes"] = []
        with self.assertRaises(ValueError):
            evaluate_pi.check_review(missing_quote, checks, answer="答案原句")
        missing_verdict = json.loads(json.dumps(review))
        missing_verdict["checksReview"][0]["verdict"] = None
        with self.assertRaises(ValueError):
            evaluate_pi.check_review(missing_verdict, checks, answer="答案原句")
        wrong_quote = json.loads(json.dumps(review))
        wrong_quote["checksReview"][0]["answerQuotes"] = ["答案中没有的句子"]
        with self.assertRaises(ValueError):
            evaluate_pi.check_review(wrong_quote, checks, answer="答案原句")

    def test_review_command_writes_per_run_and_batch_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            batch = Path(folder)
            run = batch / "runs" / "native-frontend-none-r1"
            run.mkdir(parents=True)
            result = {"caseId": "native-frontend", "mode": "none", "repeat": 1,
                      "runId": "RUN-1", "modelActual": "m", "status": "completed",
                      "checks": ["检查项"], "issues": [],
                      "answer": "答案原句，见 repos/a/X.java:10。"}
            (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
            (run / "review.json").write_text(json.dumps({
                "reviewSchemaVersion": 2, "checksReview": [{
                    "index": 1, "check": "检查项", "verdict": "pass",
                    "answerQuotes": ["答案原句"], "evidence": ["repos/a/X.java:10"],
                    "reason": "已核实"}], "issues": []}), encoding="utf-8")
            evaluate_pi.command_review(Namespace(batch=batch, allow_unreviewed=False))
            self.assertIn("通过", (run / "checks.md").read_text(encoding="utf-8"))
            self.assertIn("native-frontend", (batch / "checks.md").read_text(
                encoding="utf-8"))

    def test_resume_requires_matching_batch_config(self):
        with tempfile.TemporaryDirectory() as folder:
            batch = Path(folder)
            expected = {"requestedModel": "m", "cases": ["a"], "modes": ["none"],
                        "repeats": 2, "thinking": None}
            with self.assertRaises(ValueError):
                evaluate_pi.check_resume(batch, expected)
            (batch / "batch.json").write_text(json.dumps({**expected, "requestedModel": "x"}))
            with self.assertRaises(ValueError):
                evaluate_pi.check_resume(batch, expected)
            (batch / "batch.json").write_text(json.dumps(expected))
            evaluate_pi.check_resume(batch, expected)

    def test_plan_merges_suites_without_calling_models(self):
        root = Path(__file__).resolve().parents[1]
        evaluations = root / "validation-projects" / "complex-loan" / "evaluations"
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "batch"
            completed = subprocess.run([
                sys.executable, str(SCRIPT), "run",
                "--suite", str(evaluations / "complex-loan.json"),
                "--suite", str(evaluations / "complex-loan-diagnostic-v3.json"),
                "--case", "native-frontend", "--case", "biz-payment-pending",
                "--modes", "none,backbone", "--repeats", "2",
                "--model", "zcode/glm-5.3-flash",
                "--output", str(output), "--plan",
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            plan = json.loads(completed.stdout)
            self.assertEqual(8, len(plan["runs"]))
            self.assertIn("native-frontend/none/r1", plan["runs"])
            self.assertIn("biz-payment-pending/backbone/r2", plan["runs"])
            self.assertFalse(output.exists())

    def test_prompt_variant_plan_pairs_runs_and_alternates_the_starting_arm(self):
        root = Path(__file__).resolve().parents[1]
        suite = root / "validation-projects/complex-loan/evaluations/complex-loan.json"
        with tempfile.TemporaryDirectory() as folder:
            completed = subprocess.run([
                sys.executable, str(SCRIPT), "run", "--suite", str(suite), "--case", "native-frontend",
                "--prompt-variants", "baseline,layered", "--repeats", "2", "--output", folder, "--plan",
            ], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([
            "native-frontend/none/baseline/r1", "native-frontend/none/layered/r1",
            "native-frontend/none/layered/r2", "native-frontend/none/baseline/r2",
        ], json.loads(completed.stdout)["runs"])


if __name__ == "__main__":
    unittest.main()

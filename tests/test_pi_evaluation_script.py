from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
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
                            {"path": "repos/a/X.java", "confirmed": True},
                            {"path": "repos/b/Y.java", "confirmed": False},
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


if __name__ == "__main__":
    unittest.main()

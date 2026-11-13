"""阶段 0 structured-retrieval comparison metrics."""
import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.code_search_metrics import (
    citation_report, code_search_comparison, no_results_claim)


class CitationReportTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        source = self.root / "repos/demo/Pay.java"
        source.parent.mkdir(parents=True)
        self.content = "\n".join(f"line {number}" for number in range(1, 11))
        source.write_text(self.content, encoding="utf-8")

    def test_valid_citation_counts(self):
        report = citation_report(
            "见 repos/demo/Pay.java:3 与 repos/demo/Pay.java:2-4。", self.root)
        self.assertEqual(2, report["cited"])
        self.assertEqual(2, report["valid"])
        self.assertEqual([], report["invalid"])

    def test_out_of_range_line_is_invalid(self):
        report = citation_report("见 repos/demo/Pay.java:99。", self.root)
        self.assertEqual(1, report["cited"])
        self.assertEqual(0, report["valid"])
        self.assertEqual("line-out-of-range", report["invalid"][0]["reason"])

    def test_missing_file_is_invalid(self):
        report = citation_report("见 repos/demo/Miss.java:1。", self.root)
        self.assertEqual("file-missing", report["invalid"][0]["reason"])

    def test_suffix_match_tolerates_frozen_repo_rename(self):
        # Batch inputs rename repositories to repo-N; suffix matching still
        # validates citations written against the original mount paths.
        report = citation_report("见 repos/demo/Pay.java:5。", self.root)
        self.assertEqual(1, report["valid"])


class ComparisonTest(unittest.TestCase):
    def run_pair(self, experiment_answer):
        return [
            {"caseId": "q1", "arm": "baseline", "answer": "见 repos/demo/Pay.java:3。",
             "sourceRoot": None, "totalTokens": 900, "modelCalls": 4},
            {"caseId": "q1", "arm": "experiment", "answer": experiment_answer,
             "sourceRoot": None, "totalTokens": 600, "modelCalls": 3,
             "noResultEvidence": False},
        ]

    def test_paired_deltas_and_unpaired(self):
        runs = self.run_pair("通过 SearchSymbols 定位 repos/demo/Pay.java:3。") + [
            {"caseId": "q2", "arm": "baseline", "answer": "x", "totalTokens": 1, "modelCalls": 1}]
        summary = code_search_comparison(runs)
        self.assertEqual(1, summary["totals"]["cases"])
        self.assertEqual(-300, summary["totals"]["tokensDeltaSum"])
        self.assertEqual(-1, summary["totals"]["callsDeltaSum"])
        self.assertEqual(["q2"], summary["unpaired"])

    def test_no_results_claim_recorded_for_review(self):
        summary = code_search_comparison(
            self.run_pair("结构索引中不存在该符号，代码库中未找到相关实现。"))
        pair = summary["pairs"][0]["noResults"]
        self.assertFalse(pair["baseline"])
        self.assertTrue(pair["experiment"])
        self.assertFalse(pair["experimentHadEvidence"])


class NoResultsClaimTest(unittest.TestCase):
    def test_claims(self):
        self.assertTrue(no_results_claim("代码库中未找到相关实现。"))
        self.assertTrue(no_results_claim("结构索引中不存在该符号。"))
        self.assertFalse(no_results_claim("已找到 repos/demo/Pay.java:3 的实现。"))


if __name__ == "__main__":
    unittest.main()

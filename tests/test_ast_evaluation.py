import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.harness import reference_usage
from business_code_agent.evaluation.diagnosis import diagnosis_summaries


class AstEvaluationTest(unittest.TestCase):
    def test_usage_distinguishes_injection_read_and_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            baseline = Path(folder) / "baseline"
            baseline.mkdir()
            (baseline / "project-overview.md").write_text("structure overview")
            trace = [{"name": "Glob", "status": "completed", "input": {"path": str(baseline / "ast")}},
                     {"name": "Read", "status": "completed", "input": {"file_path": str(baseline / "ast/repo-0-index.md")}}]
            alias = Path(folder) / "baseline-alias"
            alias.symlink_to(baseline, target_is_directory=True)
            result = reference_usage(trace, alias, ["--append-system-prompt", "structure overview"])
            self.assertTrue(result["overviewInjected"])
            self.assertEqual(1, result["astContentCalls"])

    def test_ast_increment_is_paired_against_same_overview(self):
        rows = [{"id": arm, "questionId": "q", "repeat": 1, "arm": arm,
                 "status": "completed", "elapsedSeconds": seconds}
                for arm, seconds in [("code_only", 15), ("overview", 12), ("overview_ast", 9)]]
        result = diagnosis_summaries(rows, {"q": {"checks": ["x"]}}, ["code_only", "overview", "overview_ast"])
        self.assertEqual("overview", result["astVsOverview"]["controlArm"])
        self.assertEqual(-3, result["astVsOverview"]["pairs"][0]["secondsDelta"])

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.evaluation.diagnosis import prepare_reassessment, diagnosis_summaries
from business_code_agent.evaluation.runner import _arm_documents
from business_code_agent.evaluation.scoring import RubricInvalid, parse_review, model_review_score
from business_code_agent.evaluation.harness import judge_answer
from business_code_agent.query_agent.runtime import RuntimeResult


class DiagnosisTest(unittest.TestCase):
    def test_overview_arm_excludes_flows(self):
        docs = {"project-overview.md": "map", "flows/a.md": "details"}
        result = _arm_documents("overview", docs, None)
        self.assertEqual(["project-overview.md"], list(result))
        self.assertTrue(result["project-overview.md"].startswith("map"))
        self.assertEqual(docs, _arm_documents("optional", docs, None))
        self.assertEqual({}, _arm_documents("code_only", docs, None))
        with self.assertRaises(ValueError):
            _arm_documents("overview", {"flows/a.md": "details"}, None)

    def test_invalid_rubric_is_not_answer_failure_score(self):
        with self.assertRaises(RubricInvalid):
            parse_review('{"rubricInvalid":"源码值已改变"}', 1)
        self.assertIsNone(model_review_score({"review": {"status": "rubric_invalid"}}, {"checks": ["x"]}))

    def test_invalid_rubric_does_not_repeat_model_call(self):
        class Judge:
            calls = 0

            def ask(self, *args, **kwargs):
                self.calls += 1
                return RuntimeResult('{"rubricInvalid":"值已改变，Source.java:1"}', "judge", status="completed")

        judge = Judge()
        with tempfile.TemporaryDirectory() as folder:
            review = judge_answer({"question": "q", "checks": ["old"]}, "new", folder, judge, folder)
        self.assertEqual("rubric_invalid", review["status"])
        self.assertEqual(1, judge.calls)

    def test_reassessment_preserves_source_and_rejects_question_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "old"
            (source / "inputs").mkdir(parents=True)
            (source / "inputs" / "source.txt").write_text("fixed code")
            case = {"id": "q", "question": "question", "checks": ["old"]}
            protocol = {"cases": [case], "jobs": [{"case": "q", "arm": "code_only", "repeat": 1}]}
            (source / "protocol.json").write_text(json.dumps(protocol))
            result = {"id": "q-r1-code_only", "questionId": "q", "answer": "original", "review": {"status": "completed"}}
            (source / "results.json").write_text(json.dumps([result]))
            before = (source / "results.json").read_bytes()
            suite = {"cases": [{**case, "checks": ["corrected"]}]}
            target = prepare_reassessment(source, root / "new", suite)
            self.assertEqual(before, (source / "results.json").read_bytes())
            new = json.loads((target / "results.json").read_text())[0]
            self.assertEqual("original", new["answer"])
            self.assertNotIn("review", new)
            self.assertTrue((target / "runs/q-r1-code_only/original-review.json").is_file())
            suite["cases"][0]["question"] = "different"
            with self.assertRaises(ValueError):
                prepare_reassessment(source, root / "bad", suite)
            self.assertFalse((root / "bad").exists())

    def test_incremental_cost_has_own_paired_denominator(self):
        cases = {"q": {"checks": ["x"], "category": "business_investigation"}}
        rows = [{"id": arm, "questionId": "q", "repeat": 1, "arm": arm,
                 "status": "failed" if arm == "code_only" else "completed",
                 "elapsedSeconds": seconds, "toolCalls": 1}
                for arm, seconds in [("code_only", 0), ("overview", 10), ("optional", 7)]]
        summary = diagnosis_summaries(rows, cases, ["code_only", "overview", "optional"])
        self.assertEqual(0, summary["categories"]["business_investigation"]["costBlocks"])
        self.assertEqual(1, summary["fullVsOverview"]["costBlocks"])
        self.assertEqual(-3, summary["fullVsOverview"]["pairs"][0]["secondsDelta"])

"""Fixed-evidence replay excludes reference answers and other projects."""

import json
import unittest

from scripts.evaluation.evaluate_answer_consistency import prepare, replay


class AnswerReplayTest(unittest.TestCase):
    def setUp(self):
        self.source_run = {
            "projectId": "A", "answer": "OLD ANSWER MUST NOT BE PROVIDED",
            "experiment": {"caseId": "receipt", "retrieval": "files", "requestedModel": "fixture"},
            "events": [{"eventType": "tool", "payload": {
                "id": "r", "name": "Read", "status": "completed",
                "output": json.dumps({"path": "repos/service/Receipt.java", "lineStart": 8, "lineEnd": 8,
                                      "content": '8: return Map.of("phase", phase);'}),
            }}],
        }
        self.suite = {"projectId": "A", "cases": [{
            "id": "receipt", "question": "返回哪些数据", "expectedAnswer": "REFERENCE MUST NOT BE PROVIDED",
            "evidence": ["LABEL MUST NOT BE PROVIDED"],
        }]}

    def test_frozen_input_uses_original_question_and_delivered_lines_only(self):
        pack = prepare(self.source_run, self.suite, "receipt", "A")
        self.assertEqual(1, pack["sourceBlocks"])
        self.assertEqual(1, pack["deliveredRows"])
        self.assertEqual(0, pack["omittedOriginalBlocks"])
        self.assertIn('8: return Map.of("phase", phase);', pack["evidenceText"])
        self.assertNotIn("MUST NOT BE PROVIDED", json.dumps(pack))

    def test_cross_project_or_different_question_is_rejected_before_model_call(self):
        with self.assertRaises(ValueError):
            prepare(self.source_run, self.suite, "receipt", "B")
        with self.assertRaises(ValueError):
            prepare(self.source_run, self.suite, "other", "A")
        pack = prepare(self.source_run, self.suite, "receipt", "A")
        with self.assertRaises(ValueError):
            replay(pack, "B")

    def test_changed_prompt_is_rejected_before_model_call(self):
        pack = prepare(self.source_run, self.suite, "receipt", "A")
        pack["systemRules"] = "另一个答题协议"
        with self.assertRaises(ValueError):
            replay(pack, "A")


if __name__ == "__main__":
    unittest.main()

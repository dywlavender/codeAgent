"""Context selection preserves source positions and a real character budget."""
import copy
import unittest

from business_code_agent.codeintel.retrieval import pack_context


def passage(path, start, content, score=1):
    return {"id": f"repo:{path}:{start}-{start + content.count(chr(10))}",
            "sourceId": "repo", "kind": "source", "path": path,
            "lineStart": start, "lineEnd": start + content.count("\n"),
            "content": content, "score": score}


class ContextSelectionTest(unittest.TestCase):
    def test_focused_preserves_empty_lines_overlap_and_unselected_gaps(self):
        candidates = [passage("A.py", 1, "def settle():\n    prepare()\n\n    save()\n"),
                      passage("A.py", 4, "    save()\n\n    return result"),
                      passage("A.py", 9, "def retry():\n    settle()\n")]
        original = copy.deepcopy(candidates)
        selected, context = pack_context(candidates, 10000, policy="focused", question="settle retry")
        actual = {(p["sourceId"], p["path"], i): line for p in selected
                  for i, line in enumerate(p["content"].split("\n"), p["lineStart"])}
        expected = {(p["sourceId"], p["path"], i): line for p in candidates
                    for i, line in enumerate(p["content"].split("\n"), p["lineStart"])}
        self.assertEqual(expected, actual)
        self.assertNotIn(("repo", "A.py", 8), actual)
        self.assertEqual(2, len(selected))
        self.assertEqual(candidates, original)
        self.assertIn("A.py:1-6", context)
        self.assertIn("A.py:9-11", context)

    def test_budgets_and_locations_for_every_policy(self):
        candidates = [passage("A.py", 1, "def settle():\n    prepare()\n\n    save()\n"),
                      passage("B.py", 3, "def retry():\n    settle()\n", score=.9)]
        for policy in ("ranked", "diverse", "focused"):
            for budget in (0, 50, 100, 200, 1000):
                with self.subTest(policy=policy, budget=budget):
                    selected, context = pack_context(candidates, budget, policy=policy, question="retry settle")
                    self.assertLessEqual(len(context), budget)
                    for item in selected:
                        self.assertEqual(item["lineEnd"] - item["lineStart"] + 1, len(item["content"].split("\n")))
                    self.assertEqual((selected, context), pack_context(candidates, budget, policy=policy, question="retry settle"))

    def test_diversity_does_not_mutate_original_ranking(self):
        candidates = [passage("A.py", 1, "payment succeeds and booking retries", 10),
                      passage("B.py", 1, "payment succeeds and booking retries", 9.5),
                      passage("C.py", 1, "limit occupation writes ledger records", 9)]
        original = copy.deepcopy(candidates)
        selected, _ = pack_context(candidates, 10000, policy="diverse")
        self.assertEqual(["A.py", "C.py", "B.py"], [x["path"] for x in selected])
        self.assertEqual(original, candidates)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from business_code_agent.query.agent.citations import (normalize_answer_paths,
                                                       verify_references)


class CitationWorkspacePathTest(unittest.TestCase):
    def test_absolute_workspace_paths_are_normalized_before_matching(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\n",
                  "lineStart": 1, "lineEnd": 2}]
        answer = ("见 /tmp/agent-ws/repos/a/Main.java:1-2，"
                  "以及 tmp/agent-ws/repos/a/Main.java:2。")
        verification = verify_references(answer, reads, workspace="/tmp/agent-ws")
        self.assertEqual(["repos/a/Main.java", "repos/a/Main.java"],
                         [item["path"] for item in verification["references"]])
        self.assertTrue(all(item["confirmed"]
                            for item in verification["references"]))
        self.assertEqual([], verification["unconfirmed"])

    def test_markdown_link_counts_once_and_prefers_matching_target(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\nline3\n",
                  "lineStart": 1, "lineEnd": 3}]
        answer = "见 [Main.java:2](/tmp/agent-ws/repos/a/Main.java:2)。"
        verification = verify_references(answer, reads, workspace="/tmp/agent-ws")
        self.assertEqual(1, len(verification["references"]))
        reference = verification["references"][0]
        self.assertEqual("repos/a/Main.java", reference["path"])
        self.assertEqual(2, reference["line"])
        self.assertTrue(reference["confirmed"])
        self.assertEqual([], verification["unconfirmed"])

    def test_duplicate_citations_for_same_range_are_counted_once(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\nline3\n",
                  "lineStart": 1, "lineEnd": 3}]
        answer = ("[Main.java:2](/tmp/agent-ws/repos/a/Main.java:2)；"
                  "另见 repos/a/Main.java:2。")
        verification = verify_references(answer, reads, workspace="/tmp/agent-ws")
        self.assertEqual(1, len(verification["references"]))
        self.assertTrue(verification["references"][0]["confirmed"])

    def test_duplicated_workspace_prefix_resolves_to_project_path(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\n",
                  "lineStart": 1, "lineEnd": 2}]
        answer = ("[Main.java:1](/tmp/agent-ws/repos/project/pi/CONV-1/repos/"
                  "a/Main.java:1)")
        verification = verify_references(answer, reads, workspace="/tmp/agent-ws")
        self.assertEqual(1, len(verification["references"]))
        self.assertEqual("repos/a/Main.java", verification["references"][0]["path"])
        self.assertTrue(verification["references"][0]["confirmed"])

    def test_normalize_answer_paths_rewrites_temporary_targets_only(self):
        reads = [{"path": "repos/a/Main.java", "content": "line1\nline2\n",
                  "lineStart": 1, "lineEnd": 2}]
        answer = ("见 [Main.java:2](/tmp/agent-ws/repos/a/Main.java:2) 与 "
                  "/tmp/other/repos/a/Main.java:1；普通文件名 Main.java 保持不变。")
        normalized = normalize_answer_paths(answer, reads, workspace="/tmp/agent-ws")
        self.assertNotIn("/tmp/agent-ws", normalized)
        self.assertNotIn("/tmp/other", normalized)
        self.assertIn("[Main.java:2](repos/a/Main.java:2)", normalized)
        self.assertIn("普通文件名 Main.java 保持不变", normalized)


if __name__ == "__main__":
    unittest.main()

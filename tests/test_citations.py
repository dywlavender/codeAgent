from __future__ import annotations

import unittest

from business_code_agent.query.agent.citations import verify_references


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


if __name__ == "__main__":
    unittest.main()

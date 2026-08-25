from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from business_code_agent.cli import load_demo


class OptionalAdapterTest(unittest.TestCase):
    def test_langgraph_checkpoint_adapter(self):
        try:
            from langgraph.checkpoint.memory import InMemorySaver
            from business_code_agent.langgraph_adapter import build_graph
        except ImportError:
            self.skipTest("langgraph optional dependency is not installed")
        with tempfile.TemporaryDirectory() as folder:
            db = load_demo(str(Path(folder) / "graph.db"))
            graph = build_graph(db, InMemorySaver())
            config = {"configurable": {"thread_id": "acceptance-1"}}
            result = graph.invoke({"question": "为什么提款校验 repayType，这个值从哪里来？"}, config)
            self.assertEqual("SUFFICIENT", result["result"]["evidence_status"])
            snapshot = graph.get_state(config)
            self.assertEqual("SUFFICIENT", snapshot.values["result"]["evidence_status"])
            self.assertNotIn("answer", snapshot.values["result"])
            self.assertNotIn("apply.setRepayType", str(snapshot.values["result"]))
            history = list(graph.get_state_history(config))
            step_iterations = [
                item.values.get("agent_state", {}).get("iteration")
                for item in history
                if item.values.get("agent_state")
            ]
            self.assertTrue({0, 1, 2, 3}.issubset(set(step_iterations)))
            self.assertGreaterEqual(len(history), 6, "each evidence iteration must be checkpointed")
            db.close()


if __name__ == "__main__":
    unittest.main()

"""Real LangChain/provider transport against a local simulated model endpoint."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from business_code_agent.query.langchain_agent import LangChainRuntime


class LangChainHttpTest(unittest.TestCase):
    def test_compatible_transport_tool_schema_execution_and_usage(self):
        self.exercise_transport("plain")

    def test_facts_transport_and_program_rendering(self):
        self.exercise_transport("facts")

    def test_unlimited_production_transport_uses_normal_tool_loop(self):
        self.exercise_transport("plain", max_calls=None)

    def exercise_transport(self, mode, max_calls=2):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                has_source = any(m["role"] == "tool" for m in body["messages"]) or (
                    ("已取得原文" in str(body["messages"][-1])
                     or "本轮实际交付的原文" in str(body["messages"][-1]))
                    and "int total = 1" in str(body["messages"][-1])
                )
                message = (
                    {
                        "role": "assistant",
                        "content": "total = 1，见 repos/a/Ledger.java:1",
                    }
                    if has_source
                    else {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "read-source",
                                "type": "function",
                                "function": {
                                    "name": "Read",
                                    "arguments": json.dumps(
                                        {"path": "repos/a/Ledger.java"}
                                    ),
                                },
                            }
                        ],
                    }
                )
                if has_source and mode == "facts":
                    message["content"] = json.dumps({
                        "resources": [{"id": "R", "label": "初始值", "scope": "business"}],
                        "facts": [{"id": "F", "resource": "R", "kind": "behavior",
                                   "statement": "total = 1", "refs": [{"source": "S1", "start": 1, "end": 1}]}],
                        "answers": [{"resource": "R", "kind": "behavior", "facts": ["F"]}],
                    })
                result = {
                    "id": "local-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "local-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "stop" if has_source else "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "repos/a").mkdir(parents=True)
                (root / "repos/a/Ledger.java").write_text("int total = 1;")
                (root / "CLAUDE.md").write_text("只读源码并引用行号")
                env = {
                    "BUSINESS_CODE_MODEL_NAME": "local-fixture",
                    "BUSINESS_CODE_MODEL_API_KEY": "local-test-only",
                    "BUSINESS_CODE_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                }
                result = LangChainRuntime(environment=env, max_calls=max_calls, answer_mode=mode, workflow_mode="tracked").ask(
                    "total 是多少", workspace=root
                )
                self.assertIn("total = 1", result.answer)
                self.assertEqual(2, len(requests))
                self.assertEqual(20, result.usage["input_tokens"])
                self.assertEqual(["local-fixture"], result.usage["reportedModels"])
                names = {t["function"]["name"] for t in requests[0]["tools"]}
                self.assertEqual({"Read", "Glob", "Grep", "Investigation"}, names)
                self.assertIn("int total = 1", requests[1]["messages"][-1]["content"])
                if max_calls is None:
                    self.assertTrue(requests[1].get("tools"))
                    self.assertIn("tool", {m["role"] for m in requests[1]["messages"]})
                    self.assertFalse(result.usage["separateFinalization"])
                else:
                    self.assertFalse(requests[1].get("tools"))
                    self.assertEqual(
                        {"system", "user"}, {m["role"] for m in requests[1]["messages"]}
                    )
                    self.assertTrue(result.usage["separateFinalization"])
                self.assertIn("repos/a/", requests[0]["messages"][0]["content"])
                trace = json.loads(Path(result.usage["tracePath"]).read_text())
                self.assertEqual(len(requests), len(trace["calls"]))
                # The provider serializes empty assistant tool-call text as
                # null; the pre-serialization snapshot correctly retains "".
                self.assertEqual([m.get("content") or "" for m in requests[-1]["messages"]],
                                 [m.get("content") or "" for m in trace["calls"][-1]["messages"]])
                self.assertEqual([c["id"] for m in requests[-1]["messages"] for c in m.get("tool_calls", [])],
                                 [c["id"] for m in trace["calls"][-1]["messages"] for c in m.get("toolCalls", [])])
                self.assertNotIn("local-test-only", json.dumps(trace))
                if mode == "facts":
                    audit = result.usage["verification"]
                    self.assertEqual("checked", audit["status"])
                    self.assertFalse(audit["semanticProof"])
                    self.assertIn("repos/a/Ledger.java:1", result.answer)
                    self.assertNotIn('"resources"', result.answer)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

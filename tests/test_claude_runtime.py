from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from business_code_agent.query_agent.claude_runtime import ClaudeCodeRuntime, ClaudeRuntimeError


class _FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", return_code: int = 0, running: bool = False):
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.return_code = return_code
        self.running = running
        self.terminated = False

    def poll(self):
        return None if self.running and not self.terminated else self.return_code

    def wait(self, timeout=None):
        if self.running and not self.terminated:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.return_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class ClaudeRuntimeTest(unittest.TestCase):
    def test_cancel_terminates_real_child_and_preserves_partial_answer(self):
        class LocalRuntime(ClaudeCodeRuntime):
            def build_command(self, question, *, workspace, session_id=None):
                payload = {"type": "assistant", "session_id": "cancel-session", "message": {
                    "id": "m", "content": [{"type": "text", "text": "已收到的部分回答"}],
                }}
                return [sys.executable, "-u", "-c",
                        "import time; print(" + repr(json.dumps(payload)) + ", flush=True); time.sleep(60)"]

        cancel = threading.Event()
        processes = []
        original_popen = subprocess.Popen
        def launch(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as folder:
            started = time.monotonic()
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", side_effect=launch):
                result = LocalRuntime(command=sys.executable, timeout_seconds=10).ask(
                    "q", workspace=folder, cancel_check=cancel.is_set,
                    event_callback=lambda event: cancel.set() if event["eventType"] == "text" else None,
                )
        self.assertEqual("cancelled", result.status)
        self.assertEqual("已收到的部分回答", result.answer)
        self.assertEqual("cancel-session", result.runtime_session_id)
        self.assertIsNotNone(processes[0].poll())
        self.assertLess(time.monotonic() - started, 8)

    def test_stream_events_answer_and_resume_are_parsed(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            stream = "\n".join([
                json.dumps({"type": "system", "subtype": "init", "session_id": "session-1"}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "先读取业务基线"}]}}),
                json.dumps({"type": "tool_use", "name": "Read", "input": {"path": "knowledge/baseline/withdraw.md"}}),
                json.dumps({"type": "result", "session_id": "session-1", "result": "提款需要校验银行卡。", "usage": {"input_tokens": 10}}),
            ]) + "\n"
            process = _FakeProcess(stream)
            events = []
            runtime = ClaudeCodeRuntime(command=sys.executable)
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process) as popen:
                result = runtime.ask("提款有哪些校验？", workspace=str(workspace), event_callback=events.append)
                self.assertEqual("session-1", result.runtime_session_id)
                self.assertEqual("提款需要校验银行卡。", result.answer)
                self.assertEqual(4, len(result.events))
                self.assertEqual(4, len(events))
                command = popen.call_args.args[0]
                self.assertIn("--output-format", command)
                self.assertIn("stream-json", command)
                self.assertIn("--include-partial-messages", command)
                self.assertIn("--permission-mode", command)
                self.assertIn("dontAsk", command)
                self.assertIn("--tools", command)
                self.assertIn("Read,Glob,Grep", command)
                self.assertIn("--disallowed-tools", command)

            process = _FakeProcess(stream)
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process) as popen:
                runtime.ask("银行卡为什么必须存在？", workspace=str(workspace), session_id="session-1")
                command = popen.call_args.args[0]
                self.assertIn("--resume", command)
                self.assertEqual("session-1", command[command.index("--resume") + 1])

    def test_thinking_updates_emit_once_per_segment(self):
        thinking = [{"type": "system", "subtype": "thinking_tokens",
                     "estimated_tokens": count, "estimated_tokens_delta": 1}
                    for count in range(100)]
        # Session metadata must still be read from suppressed updates.
        thinking[-1]["session_id"] = "thinking-session"
        payloads = [
            {"type": "system", "subtype": "init"},
            *thinking,
            {"type": "tool_use", "name": "Read", "input": {"path": "baseline.md"}},
            {"type": "tool_result", "content": "业务基线"},
            *thinking,
            {"type": "result", "result": "回答", "usage": {"output_tokens": 250}},
        ]
        process = _FakeProcess("\n".join(json.dumps(payload) for payload in payloads) + "\n")
        events = []
        with tempfile.TemporaryDirectory() as folder:
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process):
                with self.assertLogs("business_code_agent.query_agent.claude_runtime", level="INFO") as logs:
                    result = ClaudeCodeRuntime(command=sys.executable).ask(
                        "q", workspace=folder, event_callback=events.append)
        self.assertEqual(result.events, events)
        self.assertEqual(list(range(1, 7)), [event["sequence"] for event in events])
        self.assertEqual(["status", "status", "tool", "tool", "status", "status"],
                         [event["eventType"] for event in events])
        for event in (events[1], events[4]):
            self.assertEqual("thinking", event["payload"]["phase"])
            self.assertNotIn("estimated_tokens", event["payload"])
            self.assertNotIn("estimated_tokens_delta", event["payload"])
        self.assertEqual(2, sum("phase=thinking" in line for line in logs.output))
        self.assertEqual("thinking-session", result.runtime_session_id)
        self.assertEqual({"output_tokens": 250}, result.usage)
        self.assertEqual("回答", result.answer)

    def test_nonzero_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            process = _FakeProcess("", "authentication failed\n", return_code=3)
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process):
                with self.assertRaises(ClaudeRuntimeError) as raised:
                    ClaudeCodeRuntime(command=sys.executable).ask("q", workspace=folder)
            self.assertIn("authentication failed", str(raised.exception))

    def test_long_answer_is_not_truncated_by_event_preview(self):
        answer = "完整回答" * 2000
        process = _FakeProcess(json.dumps({"type": "result", "result": answer}) + "\n")
        with tempfile.TemporaryDirectory() as folder:
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process):
                result = ClaudeCodeRuntime(command=sys.executable).ask("q", workspace=folder)
        self.assertEqual(answer, result.answer)

    def test_structured_failure_is_reported_even_when_process_exits_zero(self):
        stream = json.dumps({"type": "result", "is_error": True, "errors": ["authentication failed"]}) + "\n"
        with tempfile.TemporaryDirectory() as folder:
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=_FakeProcess(stream)):
                with self.assertRaisesRegex(ClaudeRuntimeError, "authentication failed"):
                    ClaudeCodeRuntime(command=sys.executable).ask("q", workspace=folder)

    def test_timeout_terminates_process(self):
        with tempfile.TemporaryDirectory() as folder:
            process = _FakeProcess("", running=True)
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process):
                with self.assertRaises(ClaudeRuntimeError) as raised:
                    ClaudeCodeRuntime(command=sys.executable, timeout_seconds=1).ask("q", workspace=folder)
            self.assertIn("超时", str(raised.exception))
            self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()

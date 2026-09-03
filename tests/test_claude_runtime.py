from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
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

    def test_nonzero_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            process = _FakeProcess("", "authentication failed\n", return_code=3)
            with patch("business_code_agent.query_agent.claude_runtime.subprocess.Popen", return_value=process):
                with self.assertRaises(ClaudeRuntimeError) as raised:
                    ClaudeCodeRuntime(command=sys.executable).ask("q", workspace=folder)
            self.assertIn("authentication failed", str(raised.exception))

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

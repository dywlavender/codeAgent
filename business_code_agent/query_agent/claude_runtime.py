"""Claude Code subprocess runtime.

The command line is intentionally kept in one place. It was verified against
the installed Claude Code CLI with ``claude --help`` and uses only its
read/search tools for this product's first runtime.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .runtime import EventCallback, RuntimeErrorBase, RuntimeResult


class ClaudeRuntimeError(RuntimeErrorBase):
    """Claude Code could not complete a query."""


class ClaudeCodeRuntime:
    runtime_name = "CLAUDE_CODE"

    def __init__(
        self,
        *,
        command: str = "claude",
        timeout_seconds: float = 600,
        read_tools: tuple[str, ...] = ("Read", "Glob", "Grep"),
        environment: Mapping[str, str] | None = None,
    ):
        self.command = command
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.read_tools = tuple(read_tools)
        self.environment = dict(environment or {})

    def build_command(self, question: str, *, workspace: str | Path, session_id: str | None = None) -> list[str]:
        """Build the exact read-only Claude Code invocation."""
        command = [
            self.command,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "dontAsk",
            "--tools", ",".join(self.read_tools),
            "--disallowed-tools", "Edit,Write,Bash,NotebookEdit,Task",
            "--add-dir", str(Path(workspace).expanduser().resolve()),
        ]
        if session_id:
            command.extend(["--resume", str(session_id)])
        command.extend(["-p", str(question)])
        return command

    def ask(
        self,
        question: str,
        *,
        workspace: str,
        session_id: str | None = None,
        event_callback: EventCallback | None = None,
    ) -> RuntimeResult:
        question = str(question or "").strip()
        if not question:
            raise ClaudeRuntimeError("问题不能为空")
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise ClaudeRuntimeError(f"Claude workspace 不存在: {workspace_path}")
        if not shutil.which(self.command) and not Path(self.command).is_file():
            raise ClaudeRuntimeError(f"没有找到 Claude Code CLI: {self.command}")

        environment = os.environ.copy()
        environment.update(self.environment)
        process = subprocess.Popen(
            self.build_command(question, workspace=workspace_path, session_id=session_id),
            cwd=str(workspace_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        channel: queue.Queue[tuple[str, str | None]] = queue.Queue()
        threads = [
            threading.Thread(target=_read_stream, args=("stdout", process.stdout, channel), daemon=True),
            threading.Thread(target=_read_stream, args=("stderr", process.stderr, channel), daemon=True),
        ]
        for thread in threads:
            thread.start()

        events: list[dict[str, Any]] = []
        plain_text: list[str] = []
        stderr: list[str] = []
        session = None
        usage: dict[str, Any] = {}
        stdout_done = stderr_done = False
        started = time.monotonic()
        try:
            while not (stdout_done and stderr_done and process.poll() is not None):
                remaining = self.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    _terminate(process)
                    raise ClaudeRuntimeError(f"Claude Code 请求超时（>{self.timeout_seconds:g}s）")
                try:
                    stream, line = channel.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    if stream == "stdout":
                        stdout_done = True
                    else:
                        stderr_done = True
                    continue
                if stream == "stderr":
                    if line.strip():
                        stderr.append(line.rstrip())
                    continue
                payload = _parse_json_line(line)
                if payload is None:
                    if line.strip():
                        plain_text.append(line.rstrip())
                    continue
                event = _normalise_event(payload, len(events) + 1)
                events.append(event)
                if event_callback:
                    event_callback(event)
                candidate_session = _session_id(payload)
                if candidate_session:
                    session = candidate_session
                candidate_usage = _usage(payload)
                if candidate_usage:
                    usage.update(candidate_usage)
                answer = _answer_text(payload)
                if answer:
                    plain_text.append(answer)
            return_code = process.wait(timeout=2)
        except ClaudeRuntimeError:
            raise
        except subprocess.TimeoutExpired as exc:
            _terminate(process)
            raise ClaudeRuntimeError("等待 Claude Code 进程结束超时") from exc
        except OSError as exc:
            _terminate(process)
            raise ClaudeRuntimeError(f"启动 Claude Code 失败: {exc}") from exc
        finally:
            for thread in threads:
                thread.join(timeout=1)

        if return_code != 0:
            detail = "\n".join(stderr[-8:]) or "Claude Code 返回失败状态"
            raise ClaudeRuntimeError(detail)
        answer = _result_answer(events) or _clean_answer(plain_text)
        if not answer:
            detail = "\n".join(stderr[-8:])
            raise ClaudeRuntimeError(detail or "Claude Code 没有返回回答")
        return RuntimeResult(answer=answer, runtime_session_id=session, events=events, usage=usage)


def _read_stream(name: str, stream, channel: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        for line in iter(stream.readline, ""):
            channel.put((name, line))
    finally:
        channel.put((name, None))
        try:
            stream.close()
        except OSError:
            pass


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _parse_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _normalise_event(payload: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    event_type = str(payload.get("type") or payload.get("event") or "message")
    event = {
        "sequence": sequence,
        "eventType": event_type,
        "subtype": str(payload.get("subtype") or ""),
        "payload": _compact(payload),
    }
    session = _session_id(payload)
    if session:
        event["sessionId"] = session
    return event


def _compact(value: Any, *, limit: int = 6000, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            result[str(key)] = _compact(item, limit=limit, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_compact(item, limit=limit, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    return value


def _session_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("session_id", "sessionId"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("message", "result", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            value = _session_id(nested)
            if value:
                return value
    return None


def _usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("usage")
    if isinstance(value, Mapping):
        return dict(value)
    nested = payload.get("result")
    if isinstance(nested, Mapping) and isinstance(nested.get("usage"), Mapping):
        return dict(nested["usage"])
    return {}


def _answer_text(payload: Mapping[str, Any]) -> str:
    for key in ("result", "text", "content"):
        value = payload.get(key)
        text = _text_value(value)
        if text and (payload.get("type") == "result" or key != "result"):
            return text
    message = payload.get("message")
    return _text_value(message)


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("result", "text", "content"):
            text = _text_value(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, Mapping) and item.get("type") not in {None, "text"}:
                continue
            text = _text_value(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _result_answer(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("eventType") != "result":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            value = _text_value(payload.get("result"))
            if value:
                return value
    return ""


def _clean_answer(values: list[str]) -> str:
    return "\n".join(value.strip() for value in values if value and value.strip()).strip()

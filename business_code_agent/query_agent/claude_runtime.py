"""Claude Code subprocess runtime.

The command line is intentionally kept in one place. It was verified against
the installed Claude Code CLI with ``claude --help`` and uses only its
read/search tools for this product's first runtime.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .runtime import EventCallback, RuntimeErrorBase, RuntimeResult
from .progress import ProgressEvents

logger = logging.getLogger(__name__)


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
            "--include-partial-messages",
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
        cancel_check: Callable[[], bool] | None = None,
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
        logger.info("Claude 启动: command=%s workspace=%s session=%s timeout=%ss tools=%s",
                    self.command, workspace_path, session_id or "new", self.timeout_seconds, ",".join(self.read_tools))
        try:
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
        except OSError:
            logger.exception("Claude 进程启动失败: command=%s workspace=%s", self.command, workspace_path)
            raise
        pid = getattr(process, "pid", "unknown")
        logger.info("Claude 进程已启动: pid=%s", pid)
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
        session = session_id
        usage: dict[str, Any] = {}
        final_answer = ""
        result_error = ""
        stdout_done = stderr_done = False
        started = time.monotonic()
        last_output = started
        next_progress = started + 15
        first_output = True
        progress = ProgressEvents()
        cancelled = False

        def emit(updates):
            for event in updates:
                event["sequence"] = len(events) + 1
                events.append(event)
                # Text chunks are deliberately not logged one by one.
                if event["eventType"] != "text":
                    logger.info("Claude 进度: pid=%s sequence=%s type=%s phase=%s elapsed=%.1fs",
                                pid, len(events), event["eventType"], event["payload"].get("phase", ""),
                                time.monotonic() - started)
                if event_callback:
                    event_callback(event)
        try:
            while not (stdout_done and stderr_done and process.poll() is not None):
                if cancel_check and cancel_check():
                    _terminate(process)
                    emit(progress.flush(force=True))
                    cancelled = True
                    logger.info("Claude 已取消: pid=%s elapsed=%.1fs", pid, time.monotonic() - started)
                    break
                now = time.monotonic()
                remaining = self.timeout_seconds - (now - started)
                if remaining <= 0:
                    logger.error("Claude 超时: pid=%s elapsed=%.1fs idle=%.1fs events=%s stderr_lines=%s",
                                 pid, now - started, now - last_output, len(events), len(stderr))
                    _terminate(process)
                    raise ClaudeRuntimeError(f"Claude Code 请求超时（>{self.timeout_seconds:g}s）")
                if now >= next_progress:
                    logger.info("Claude 等待中: pid=%s elapsed=%.1fs idle=%.1fs events=%s stderr_lines=%s",
                                pid, now - started, now - last_output, len(events), len(stderr))
                    next_progress = now + 15
                try:
                    stream, line = channel.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    emit(progress.flush())
                    continue
                if line is None:
                    logger.debug("Claude 输出流结束: pid=%s stream=%s", pid, stream)
                    if stream == "stdout":
                        stdout_done = True
                    else:
                        stderr_done = True
                    continue
                last_output = time.monotonic()
                if first_output:
                    logger.info("Claude 首次输出: pid=%s stream=%s elapsed=%.1fs", pid, stream, last_output - started)
                    first_output = False
                if stream == "stderr":
                    if line.strip():
                        stderr.append(line.rstrip())
                        logger.debug("Claude stderr: pid=%s line=%s characters=%s", pid, len(stderr), len(line))
                    continue
                payload = _parse_json_line(line)
                if payload is None:
                    if line.strip():
                        plain_text.append(line.rstrip())
                    continue
                candidate_session = _session_id(payload)
                if candidate_session:
                    session = candidate_session
                candidate_usage = _usage(payload)
                if candidate_usage:
                    usage.update(candidate_usage)
                emit(progress.feed(payload))
                answer = _answer_text(payload)
                if payload.get("type") == "result":
                    if payload.get("is_error") or str(payload.get("subtype", "")).startswith("error"):
                        errors = payload.get("errors") or []
                        result_error = "\n".join(str(item) for item in errors) if isinstance(errors, list) else str(errors)
                        result_error = result_error or answer or "Claude Code 返回失败结果"
                    else:
                        final_answer = answer
                if answer:
                    plain_text.append(answer)
            emit(progress.flush(force=True))
            return_code = process.wait(timeout=2)
            logger.info("Claude 进程结束: pid=%s exit_code=%s elapsed=%.1fs events=%s stderr_lines=%s",
                        pid, return_code, time.monotonic() - started, len(events), len(stderr))
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

        if cancelled:
            blocks = {}
            for event in events:
                if event["eventType"] == "text":
                    data = event["payload"]
                    blocks[data["id"]] = (blocks.get(data["id"], "") if data["mode"] == "append" else "") + data["text"]
            return RuntimeResult(answer="\n\n".join(blocks.values()), runtime_session_id=session,
                                 events=events, usage=usage, status="cancelled")
        if return_code != 0 or result_error:
            logger.error("Claude 回答失败: pid=%s exit_code=%s result_error=%s", pid, return_code, bool(result_error))
            detail = result_error or "\n".join(stderr[-8:]) or "Claude Code 返回失败状态"
            raise ClaudeRuntimeError(detail)
        # Use the raw result as the authoritative, untruncated final answer.
        answer = final_answer or _clean_answer(plain_text)
        if not answer:
            logger.error("Claude 返回空回答: pid=%s events=%s", pid, len(events))
            detail = "\n".join(stderr[-8:])
            raise ClaudeRuntimeError(detail or "Claude Code 没有返回回答")
        logger.info("Claude 回答完成: pid=%s session=%s answer_characters=%s elapsed=%.1fs",
                    pid, session, len(answer), time.monotonic() - started)
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
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _parse_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


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


def _clean_answer(values: list[str]) -> str:
    return "\n".join(value.strip() for value in values if value and value.strip()).strip()

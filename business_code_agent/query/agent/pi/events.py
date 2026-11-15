"""Translate Pi RPC session events into CodeAgent query events."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping


Emit = Callable[[dict[str, Any]], None]
CallCallback = Callable[[dict[str, Any]], None]

_PATH_KEYS = {"path", "paths", "directory", "dir", "cwd", "file", "files"}
_PATH_TOOLS = {"read", "grep", "find", "ls"}


def message_text(message: Mapping[str, Any] | None) -> str:
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def message_tool_calls(message: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    calls = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "toolCall":
            calls.append({
                "id": block.get("id"),
                "name": block.get("name"),
                "arguments": block.get("arguments") or {},
            })
    return calls


def tool_result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping):
        content = result.get("content")
        if isinstance(content, list):
            text = "".join(str(block.get("text") or "") for block in content
                           if isinstance(block, Mapping) and block.get("type") == "text")
            if text:
                return text
        elif isinstance(content, str) and content:
            return content
        if "structuredContent" in result:
            value = result["structuredContent"]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def denied_tool_path(tool_name: str, arguments: Mapping[str, Any], *,
                     workspace: Path, allowed_roots: tuple[Path, ...]) -> str | None:
    """Return a reason when a read-only tool path escapes this turn's roots.

    Pi validates tools after the model call starts, so this is a detection and
    abort guard rather than an OS sandbox. Symlinked authorized repositories are
    accepted because their resolved directories are part of ``allowed_roots``.
    """

    if tool_name not in _PATH_TOOLS or not isinstance(arguments, Mapping):
        return None
    values: list[str] = []
    for key, value in arguments.items():
        if key not in _PATH_KEYS:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    roots = (Path(workspace).resolve(), *(Path(root).resolve() for root in allowed_roots))
    for raw in values:
        if raw.startswith("~"):
            return f"禁止访问主目录路径：{raw}"
        candidate = Path(raw)
        target = candidate if candidate.is_absolute() else Path(workspace) / candidate
        resolved = target.resolve()
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            return f"路径不在本轮授权范围：{raw}"
    return None


class PiEventAdapter:
    """Stateful conversion of one Pi run into the public event/trace shape."""

    def __init__(self, emit: Emit, *, workspace: Path, allowed_roots: tuple[Path, ...],
                 call_callback: CallCallback | None = None):
        self._emit_raw = emit
        self.workspace = Path(workspace).resolve()
        self.allowed_roots = allowed_roots
        self.call_callback = call_callback
        self.started = time.monotonic()
        self.sequence = 0
        self.model_calls = 0
        self.tool_calls = 0
        self.assistant_messages: list[dict[str, Any]] = []
        self.final_message: dict[str, Any] | None = None
        self.tool_started: dict[str, dict[str, Any]] = {}
        self.tool_finished: set[str] = set()
        self.denied_tool: dict[str, Any] | None = None
        self.aborted = False
        self.settled = False
        self.compactions = 0
        self.models: set[str] = set()
        self.pending_attempt: int | None = None
        self.tokens = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                       "total": 0, "cost": 0.0}

    def emit(self, kind: str, **payload: Any) -> None:
        self.sequence += 1
        self._emit_raw({
            "sequence": self.sequence,
            "eventType": kind,
            "payload": payload,
            "elapsedMs": round((time.monotonic() - self.started) * 1000, 3),
        })

    def handle(self, record: Mapping[str, Any]) -> None:
        kind = str(record.get("type") or "")
        if kind == "agent_start":
            self.emit("status", phase="thinking", label="Pi 正在分析")
        elif kind == "message_start":
            self._message_start(record.get("message"))
        elif kind == "message_update":
            self._message_update(record)
        elif kind == "message_end":
            self._message_end(record.get("message"))
        elif kind == "tool_execution_start":
            self._tool_start(record)
        elif kind == "tool_execution_end":
            self._tool_end(record)
        elif kind == "compaction_start":
            self.compactions += 1
            self.emit("status", phase="compaction", label="Pi 正在压缩上下文")
        elif kind == "compaction_end":
            self._compaction_end(record)
        elif kind == "auto_retry_start":
            self.emit("status", phase="retrying", label="Pi 正在重试模型请求",
                      attempt=record.get("attempt"), maxAttempts=record.get("maxAttempts"),
                      error=record.get("errorMessage"))
        elif kind == "auto_retry_end":
            if not record.get("success"):
                self.emit("status", phase="retrying", label="Pi 模型请求重试失败",
                          attempt=record.get("attempt"), error=record.get("finalError"))
        elif kind == "agent_end":
            self._agent_end(record)
        elif kind == "agent_settled":
            self.settled = True
            self.aborted = bool(record.get("aborted"))
        elif kind == "extension_error":
            self.emit("error", subtype="ExtensionError",
                      payload={"extension": record.get("extensionPath"),
                               "event": record.get("event"), "error": record.get("error")})
        elif kind == "error":
            self.emit("error", subtype="PiEventError", payload={"error": record})

    def _message_start(self, message: Any) -> None:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            return
        self.model_calls += 1
        self.pending_attempt = self.model_calls
        self._record_call(message, status="running", response_ready=False)

    def _message_update(self, record: Mapping[str, Any]) -> None:
        event = record.get("assistantMessageEvent")
        if isinstance(event, Mapping) and event.get("type") == "error":
            self.emit("error", subtype="ModelStreamError",
                      payload={"error": event.get("error") or event.get("reason")})

    def _message_end(self, message: Any) -> None:
        if not isinstance(message, Mapping):
            return
        role = message.get("role")
        if role == "toolResult":
            self._tool_result_message(message)
            return
        if role != "assistant":
            return
        self.assistant_messages.append(dict(message))
        self.final_message = dict(message)
        if self.pending_attempt is None:
            self.model_calls += 1
            self.pending_attempt = self.model_calls
        usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else {}
        self._accumulate_usage(usage)
        model = str(message.get("model") or "")
        if model:
            self.models.add(model)
        calls = message_tool_calls(message)
        text = message_text(message)
        stop_reason = str(message.get("stopReason") or "")
        phase = "answer" if not calls and stop_reason in {"stop", "length"} else "investigation"
        self.emit("model", attempt=self.model_calls, phase=phase,
                  messageCount=len(self.assistant_messages),
                  toolCalls=[str(call.get("name") or "") for call in calls],
                  outputChars=len(text),
                  inputTokens=usage.get("input"), outputTokens=usage.get("output"),
                  model=model or None)
        self._record_call(message)
        self.pending_attempt = None

    def _record_call(self, message: Mapping[str, Any], *, status: str | None = None,
                     response_ready: bool = True) -> None:
        if not self.call_callback:
            return
        usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else {}
        calls = message_tool_calls(message)
        stop_reason = str(message.get("stopReason") or "")
        final_status = status or ("failed" if stop_reason == "error" else "completed")
        entry = {
            "attempt": self.pending_attempt or self.model_calls,
            "phase": "answer" if not calls and stop_reason in {"stop", "length"} else "investigation",
            "startedAt": message.get("timestamp") or int(time.time() * 1000),
            "durationMs": message.get("durationMs"),
            "elapsedMs": round((time.monotonic() - self.started) * 1000, 3),
            "messages": [],
            "tools": [],
            "responses": [],
            "usage": [],
            "inputTextChars": 0,
            "status": final_status,
        }
        if response_ready:
            entry["responses"] = [{
                "role": "assistant",
                "content": message_text(message),
                "toolCalls": calls,
                "model": message.get("model"),
                "provider": message.get("provider"),
                "stopReason": stop_reason,
                "usage": dict(usage),
            }]
            entry["usage"] = [dict(usage)]
        if message.get("errorMessage"):
            entry["error"] = message.get("errorMessage")
            entry["errorType"] = "PiModelError"
        self.call_callback(entry)

    def finish_pending(self, status: str, error: str | None = None) -> None:
        """Mark a model call that never produced a message_end."""
        if self.pending_attempt is None or not self.call_callback:
            return
        entry = {
            "attempt": self.pending_attempt,
            "phase": "investigation",
            "startedAt": int(time.time() * 1000),
            "elapsedMs": round((time.monotonic() - self.started) * 1000, 3),
            "messages": [],
            "tools": [],
            "responses": [],
            "usage": [],
            "inputTextChars": 0,
            "status": status,
        }
        if error:
            entry["error"] = error
            entry["errorType"] = "PiRuntimeError"
        self.call_callback(entry)
        self.pending_attempt = None

    def _tool_start(self, record: Mapping[str, Any]) -> None:
        call_id = str(record.get("toolCallId") or "")
        name = str(record.get("toolName") or "")
        arguments = record.get("args") if isinstance(record.get("args"), Mapping) else {}
        self.tool_calls += 1
        self.tool_started[call_id] = {"name": name, "input": dict(arguments)}
        self.emit("tool", id=call_id, name=name, input=dict(arguments),
                  status="running", origin="model")
        reason = denied_tool_path(name, arguments, workspace=self.workspace,
                                  allowed_roots=self.allowed_roots)
        if reason:
            self.denied_tool = {"id": call_id, "name": name, "reason": reason}
            self.emit("error", subtype="ToolPathDenied",
                      payload={"tool": name, "arguments": dict(arguments), "error": reason})

    def _tool_end(self, record: Mapping[str, Any]) -> None:
        call_id = str(record.get("toolCallId") or "")
        started = self.tool_started.get(call_id, {})
        name = str(record.get("toolName") or started.get("name") or "")
        output = tool_result_text(record.get("result"))
        status = "error" if record.get("isError") else "completed"
        self.tool_finished.add(call_id)
        self.emit("tool", id=call_id, name=name, input=started.get("input") or {},
                  status=status, output=output, origin="model",
                  durationMs=record.get("durationMs"))

    def _tool_result_message(self, message: Mapping[str, Any]) -> None:
        call_id = str(message.get("toolCallId") or "")
        if not call_id or call_id in self.tool_finished:
            return
        started = self.tool_started.get(call_id, {})
        output = tool_result_text(message)
        status = "error" if message.get("isError") else "completed"
        self.tool_finished.add(call_id)
        self.emit("tool", id=call_id, name=str(message.get("toolName") or started.get("name") or ""),
                  input=started.get("input") or {}, status=status, output=output,
                  origin="model")

    def _compaction_end(self, record: Mapping[str, Any]) -> None:
        result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
        status = "completed" if result else ("aborted" if record.get("aborted") else "failed")
        payload = {
            "status": status,
            "reason": record.get("reason"),
            "beforeInputTokens": result.get("tokensBefore"),
            "afterInputTokens": result.get("estimatedTokensAfter"),
            "willRetry": record.get("willRetry"),
            "usage": result.get("usage"),
        }
        if record.get("errorMessage"):
            payload["error"] = record.get("errorMessage")
        self.emit("compaction", **payload)

    def _agent_end(self, record: Mapping[str, Any]) -> None:
        if record.get("willRetry"):
            self.emit("status", phase="retrying", label="Pi 正在恢复模型请求")
        messages = record.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, Mapping) and message.get("role") == "assistant":
                    if message_text(message):
                        self.final_message = dict(message)
                    break

    def _accumulate_usage(self, usage: Mapping[str, Any]) -> None:
        for key in ("input", "output", "cacheRead", "cacheWrite", "total"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                self.tokens[key] += int(value)
        cost = usage.get("cost")
        if isinstance(cost, Mapping):
            value = cost.get("total")
        else:
            value = cost
        if isinstance(value, (int, float)):
            self.tokens["cost"] += float(value)

    def answer(self) -> str:
        for message in reversed(self.assistant_messages):
            text = message_text(message).strip()
            if text:
                return text
        return ""

    @property
    def stop_reason(self) -> str | None:
        if self.final_message is None:
            return None
        value = self.final_message.get("stopReason")
        return str(value) if value else None

    @property
    def error_message(self) -> str | None:
        if self.final_message is None:
            return None
        value = self.final_message.get("errorMessage")
        return str(value) if value else None

    def usage(self) -> dict[str, Any]:
        return {
            "modelCalls": self.model_calls,
            "toolCalls": self.tool_calls,
            "inputTokens": self.tokens["input"],
            "outputTokens": self.tokens["output"],
            "cacheReadTokens": self.tokens["cacheRead"],
            "cacheWriteTokens": self.tokens["cacheWrite"],
            "totalTokens": self.tokens["total"],
            "cost": self.tokens["cost"],
            "reportedModels": sorted(self.models),
            "stopReason": self.stop_reason,
            "piAborted": self.aborted,
            "compactions": self.compactions,
            "semanticProof": False,
        }

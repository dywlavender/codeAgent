"""Deterministic Pi RPC stand-in used by unit tests.

It implements only the protocol subset CodeAgent consumes. Scenarios are
selected with ``FAKE_PI_SCENARIO`` so cancellation, timeout, provider errors and
tool-path violations can be tested without a real model.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path


LOCK = threading.Lock()
SESSION_FILE: Path | None = None
SESSION_ID = uuid.uuid4().hex
ABORT = threading.Event()
PROMPT_COUNT = 0
SELECTED_MODEL = ""

DEFAULT_MODELS = [
    {"id": "fake-model", "name": "Fake Model", "provider": "fake",
     "baseUrl": "http://provider.internal/v1", "apiKey": "secret",
     "contextWindow": 1000, "maxTokens": 100, "reasoning": False, "input": ["text"]},
    {"id": "glm-5.3-flash", "name": "glm-5.3-flash", "provider": "zcode",
     "baseUrl": "http://127.0.0.1:9999/v1", "apiKey": "secret",
     "contextWindow": 128000, "maxTokens": 16384, "reasoning": False, "input": ["text", "image"]},
]


def emit(record: dict) -> None:
    with LOCK:
        sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def response(request_id, command, *, success=True, data=None, error=None):
    record = {"id": request_id, "type": "response", "command": command, "success": success}
    if data is not None:
        record["data"] = data
    if error is not None:
        record["error"] = error
    emit(record)


def options(argv: list[str]) -> dict:
    values = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--session-dir", "--session", "--system-prompt", "--tools",
                     "--name", "--model", "--provider"} and index + 1 < len(argv):
            values[token] = argv[index + 1]
            index += 2
            continue
        if token == "--version":
            values[token] = True
        index += 1
    return values


def active_model() -> dict:
    value = SELECTED_MODEL if os.environ.get("FAKE_PI_ECHO_MODEL") == "1" else ""
    provider, _, identifier = value.partition("/")
    if not identifier:
        provider, identifier = "fake", value or "fake-model"
    return {"id": identifier, "provider": provider, "name": identifier}


def available_models() -> list[dict]:
    raw = os.environ.get("FAKE_PI_MODELS")
    if raw:
        value = json.loads(raw)
        return value if isinstance(value, list) else DEFAULT_MODELS
    return DEFAULT_MODELS


def session_state() -> dict:
    count = 0
    if SESSION_FILE and SESSION_FILE.is_file():
        count = sum(1 for _ in SESSION_FILE.open(encoding="utf-8"))
    return {
        "model": active_model(),
        "thinkingLevel": "off", "isStreaming": False, "isCompacting": False,
        "steeringMode": "one-at-a-time", "followUpMode": "one-at-a-time",
        "sessionFile": str(SESSION_FILE), "sessionId": SESSION_ID,
        "autoCompactionEnabled": True, "messageCount": count,
        "pendingMessageCount": 0,
    }


def append_session(kind: str) -> None:
    global PROMPT_COUNT
    if SESSION_FILE is None:
        return
    with LOCK:
        PROMPT_COUNT += 1
        with SESSION_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": kind, "prompt": PROMPT_COUNT},
                                    ensure_ascii=False) + "\n")


def assistant(text="", stop="stop", *, usage=None, tool_calls=(), error=None):
    message = {
        "role": "assistant", "content": [], "api": "fake", "provider": "fake",
        "model": "fake-model", "usage": usage or {
            "input": 10, "output": 5, "cacheRead": 0, "cacheWrite": 0,
            "totalTokens": 15, "cost": {"input": 0, "output": 0, "cacheRead": 0,
                                        "cacheWrite": 0, "total": 0},
        },
        "stopReason": stop, "timestamp": int(time.time() * 1000), "durationMs": 1,
    }
    if text:
        message["content"].append({"type": "text", "text": text})
    for call_id, name, arguments in tool_calls:
        message["content"].append({"type": "toolCall", "id": call_id, "name": name,
                                   "arguments": arguments})
    if error:
        message["errorMessage"] = error
    return message


def run_normal() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_end", "message": {"role": "user", "content": "fake question",
                                              "timestamp": 1}})
    call_id = f"call-{uuid.uuid4().hex}"
    emit({"type": "message_start", "message": assistant(
        stop="pending", tool_calls=[(call_id, "read", {"path": "repos/demo/hello.txt"})])})
    emit({"type": "message_end", "message": assistant(
        stop="toolUse", tool_calls=[(call_id, "read", {"path": "repos/demo/hello.txt"})])})
    emit({"type": "tool_execution_start", "toolCallId": call_id, "toolName": "read",
          "args": {"path": "repos/demo/hello.txt"}})
    emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": "read",
          "result": {"content": [{"type": "text", "text": "fake-file-content"}]},
          "isError": False, "durationMs": 2})
    emit({"type": "message_end", "message": {"role": "toolResult", "toolCallId": call_id,
                                              "toolName": "read", "isError": False,
                                              "content": [{"type": "text",
                                                           "text": "fake-file-content"}],
                                              "timestamp": 2}})
    final = assistant("fake answer from pi", usage={
        "input": 20, "output": 8, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 28,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    })
    emit({"type": "message_start", "message": assistant(stop="pending")})
    emit({"type": "message_end", "message": final})
    emit({"type": "agent_end", "messages": [assistant("fake answer from pi")],
          "willRetry": False})
    emit({"type": "agent_settled", "aborted": False})


def run_error() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_start", "message": assistant(stop="pending")})
    emit({"type": "message_end", "message": assistant(stop="error",
                                                       error="fake provider down")})
    emit({"type": "agent_settled", "aborted": False})


def run_length() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_start", "message": assistant(stop="pending")})
    emit({"type": "message_end", "message": assistant("partial answer", stop="length")})
    emit({"type": "agent_settled", "aborted": False})


def run_no_answer() -> None:
    emit({"type": "agent_start"})
    emit({"type": "agent_settled", "aborted": False})


def run_bad_path() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_start", "message": assistant(stop="pending")})
    call_id = f"call-{uuid.uuid4().hex}"
    emit({"type": "tool_execution_start", "toolCallId": call_id, "toolName": "read",
          "args": {"path": "/etc/passwd"}})
    emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": "read",
          "result": {"content": [{"type": "text", "text": "leaked"}]},
          "isError": False, "durationMs": 1})
    emit({"type": "agent_settled", "aborted": bool(ABORT.wait(2))})


def run_citation() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_end", "message": {"role": "user", "content": "cite question",
                                              "timestamp": 1}})
    call_id = f"call-{uuid.uuid4().hex}"
    tool_call = (call_id, "read", {"path": "repos/a/Main.java", "offset": 1, "limit": 2})
    emit({"type": "message_start", "message": assistant(stop="pending", tool_calls=[tool_call])})
    emit({"type": "message_end", "message": assistant(stop="toolUse", tool_calls=[tool_call])})
    emit({"type": "tool_execution_start", "toolCallId": call_id, "toolName": "read",
          "args": {"path": "repos/a/Main.java", "offset": 1, "limit": 2}})
    emit({"type": "tool_execution_end", "toolCallId": call_id, "toolName": "read",
          "result": {"content": [{"type": "text",
                                  "text": "class Main { // a\n}\n"}]},
          "isError": False, "durationMs": 2})
    emit({"type": "message_end", "message": {"role": "toolResult", "toolCallId": call_id,
                                              "toolName": "read", "isError": False,
                                              "content": [{"type": "text",
                                                           "text": "class Main { // a\n}\n"}],
                                              "timestamp": 2}})
    final = assistant(
        "提款入口在 repos/a/Main.java:2。完整方法见 repos/a/Main.java:1-2。\n"
        "另见 repos/b/Main.java:1 和 missing/Other.java:1。")
    emit({"type": "message_start", "message": assistant(stop="pending")})
    emit({"type": "message_end", "message": final})
    emit({"type": "agent_end", "messages": [final], "willRetry": False})
    emit({"type": "agent_settled", "aborted": False})


def run_cancellable() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_start", "message": assistant(stop="pending")})
    ABORT.wait(30)
    emit({"type": "agent_settled", "aborted": True})


def run_timeout() -> None:
    emit({"type": "agent_start"})
    emit({"type": "message_start", "message": assistant(stop="pending")})
    ABORT.wait(30)
    time.sleep(0.05)
    emit({"type": "agent_settled", "aborted": True})


def prompt_worker(scenario: str) -> None:
    append_session("prompt")
    if scenario == "normal":
        run_normal()
    elif scenario == "citation":
        run_citation()
    elif scenario == "error":
        run_error()
    elif scenario == "length":
        run_length()
    elif scenario == "no_answer":
        run_no_answer()
    elif scenario == "bad_path":
        run_bad_path()
    elif scenario in {"cancel", "timeout"}:
        run_cancellable() if scenario == "cancel" else run_timeout()
    else:
        run_normal()


def main() -> int:
    global SESSION_FILE, SELECTED_MODEL
    opts = options(sys.argv[1:])
    if opts.get("--version"):
        print("9.9.9")
        return 0
    SELECTED_MODEL = opts.get("--model") or ""
    session_dir = Path(opts.get("--session-dir") or ".pi-sessions")
    session_dir.mkdir(parents=True, exist_ok=True)
    session_value = opts.get("--session")
    if session_value:
        SESSION_FILE = Path(session_value)
    else:
        SESSION_FILE = session_dir / f"fake-{uuid.uuid4().hex}.jsonl"
    if not SESSION_FILE.exists():
        SESSION_FILE.write_text("", encoding="utf-8")
    scenario = os.environ.get("FAKE_PI_SCENARIO", "normal")
    tools = set((opts.get("--tools") or "").split(","))
    ast_broken = os.environ.get("FAKE_PI_NO_AST_EXTENSION") == "1"
    commands = [{"name": "codeagent-readonly-search", "source": "extension"}]
    if "find_code_symbol" in tools and not ast_broken:
        commands.append({"name": "codeagent-ast-loaded", "source": "extension"})
    if os.environ.get("FAKE_PI_NO_EXTENSION") == "1":
        commands = []

    for line in sys.stdin:
        if not line.strip():
            continue
        record = json.loads(line)
        kind = record.get("type")
        request_id = record.get("id")
        if kind == "get_state":
            response(request_id, kind, data=session_state())
        elif kind == "get_available_models":
            response(request_id, kind, data={"models": available_models()})
        elif kind == "get_commands":
            response(request_id, kind, data={"commands": commands})
        elif kind == "get_session_stats":
            response(request_id, kind, data={
                "sessionFile": str(SESSION_FILE), "sessionId": SESSION_ID,
                "tokens": {"input": 30, "output": 13, "cacheRead": 0,
                           "cacheWrite": 0, "total": 43},
                "cost": 0.1, "contextUsage": {"tokens": 43, "contextWindow": 1000,
                                              "percent": 4.3},
            })
        elif kind == "get_messages":
            response(request_id, kind, data={"messages": []})
        elif kind == "prompt":
            if ast_broken and ("find_code_symbol" in tools
                               or "find_code_symbol" in (opts.get("--system-prompt") or "")):
                response(request_id, kind, success=False, error="AST was advertised after load failure")
                continue
            response(request_id, kind, data={"disposition": "started"})
            threading.Thread(target=prompt_worker, args=(scenario,), daemon=True).start()
        elif kind == "abort":
            ABORT.set()
            response(request_id, kind, data={})
        else:
            response(request_id, kind, data={})
    return 0


if __name__ == "__main__":
    sys.exit(main())

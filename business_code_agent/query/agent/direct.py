"""Independent model/tool loop for core-v1.

This runtime deliberately has no coordinator, investigation state, automatic
re-reads or answer post-processing. The model chooses the registered read-only
tools until it submits an answer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

from business_code_agent.query.capabilities import PreparedCapabilities
from business_code_agent.query.context import RunContext
from business_code_agent.query.profile import AgentProfile
from business_code_agent.query.runtime import RuntimeErrorBase, RuntimeResult
from business_code_agent.query.traces import public_message
from .session import (
    CHECKPOINT_RUNTIME_ONLY,
    DirectSessionStore,
    ReadLedger,
    compact_checkpoint_messages,
    compact_messages,
    retire_changed_sources,
)


PROMPT_VERSION = 2
CHECKPOINT_VERSION = 2
CHECKPOINT_ENABLED_ENV = "BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED"
CHECKPOINT_TIMEOUT_ENV = "BUSINESS_CODE_AGENT_CHECKPOINT_TIMEOUT"
CHECKPOINT_OUTPUT_TOKENS_ENV = "BUSINESS_CODE_AGENT_CHECKPOINT_OUTPUT_TOKENS"
CHECKPOINT_CONTEXT_CHARS_ENV = "BUSINESS_CODE_AGENT_CHECKPOINT_CONTEXT_CHARS"
CHECKPOINT_DEFAULT_TIMEOUT = 45.0
CHECKPOINT_DEFAULT_OUTPUT_TOKENS = 1024
CHECKPOINT_DEFAULT_CONTEXT_CHARS = 20000
_STOPPED = "cancelled"
STALL_WARN_READS = 6
STALL_FINALIZE_READS = 10
FINALIZE_RESERVED_CALLS = 2
FINALIZE_RESERVED_SECONDS = 200.0
CHECKPOINT_SYSTEM = (
    "你是只读调查进度整理器。只输出一个 JSON，不要解释。"
    "结构：{\"draftAnswer\":\"一句话\",\"aspects\":[{\"id\":\"A1\",\"question\":\"事项\","
    "\"required\":true,\"status\":\"confirmed|partial|missing\",\"claim\":\"结论\","
    "\"evidence\":[\"repos/...:1-5\"]}],\"gaps\":[{\"aspectId\":\"A1\","
    "\"importance\":\"required|optional\",\"resolvable\":true,\"reason\":\"原因\"}]}。"
    "最多 3 个 aspects、2 个 gaps，总数不超过 800 字。"
)
CHECKPOINT_REQUEST = (
    "现在执行调查进展检查点。只输出上述 JSON 对象，不要输出 JSON 以外的文字。")

STALL_NOTICE = (
    "\n\n调查提醒：最近连续多次 Read 没有新增证据。不要继续重复读取同一区间；"
    "请基于已读内容直接作答，未确认的部分明确说明。")
FINALIZE_NOTICE = (
    "\n\n调查已到达收尾点。现在不要再调用工具，只能基于本轮已读取的源码作答。\n"
    "要求：1. 先给结论，再给源码依据；2. 只引用已读文件与行区间；"
    "3. 未读取或无法确认的内容列为缺口，不要编造。")
FINAL_ANSWER_REQUEST = (
    "立即输出最终答案。不要描述你接下来准备读取什么，也不要调用工具。"
    "请按“结论—源码依据—未确认缺口”组织；没有依据的内容明确列为缺口。"
    "答案控制在 1000 字以内。")
_CITATION = re.compile(
    r"([A-Za-z0-9_./-]+\.(?:java|kt|scala|groovy|py|go|rs|cs|cpp|cxx|c|hpp|hxx|h|"
    r"jsx|js|tsx|ts|vue|html|xml|sql|yml|yaml|properties|json|toml|gradle|md|txt))"
    r"(?![A-Za-z0-9])(?::(\d+)(?:-(\d+))?)?"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Stopped(Exception):
    pass


class DirectAgentRuntime:
    runtime_name = "LANGCHAIN_DIRECT"

    def __init__(self, *, profile: AgentProfile, model=None, environment=None,
                 max_calls=None, max_seconds=None, project_id=None):
        if profile.investigation != "direct":
            raise ValueError(f"{profile.id} 不是直接调查 Profile")
        self.profile = profile
        self.model = model
        self.environment = dict(os.environ if environment is None else environment)
        self.max_calls = int(max_calls) if max_calls else None
        self.max_seconds = float(max_seconds) if max_seconds else None
        if self.max_calls is not None and self.max_calls < 1:
            raise ValueError("max_calls 必须为正整数或不设置")
        if self.max_seconds is not None and self.max_seconds < 0:
            raise ValueError("max_seconds 必须为正数或不设置")
        self.project_id = project_id

    def checkpoint_enabled(self):
        return self.environment.get(CHECKPOINT_ENABLED_ENV, "false").strip().lower() in {
            "1", "true", "yes", "on"}

    def checkpoint_limits(self, elapsed):
        timeout = float(self.environment.get(
            CHECKPOINT_TIMEOUT_ENV, str(CHECKPOINT_DEFAULT_TIMEOUT)))
        max_tokens = max(1, int(self.environment.get(
            CHECKPOINT_OUTPUT_TOKENS_ENV, str(CHECKPOINT_DEFAULT_OUTPUT_TOKENS))))
        if self.max_seconds is not None:
            reserve = min(FINALIZE_RESERVED_SECONDS, self.max_seconds * 0.4)
            remaining = self.max_seconds - elapsed - reserve
            timeout = min(timeout, max(1.0, remaining))
        return timeout, max_tokens

    def execution_settings(self):
        checkpoint = self.checkpoint_enabled()
        revision = f"core-direct-{PROMPT_VERSION}"
        if checkpoint:
            revision += f"-checkpoint-{CHECKPOINT_VERSION}"
        return {
            "agentVersion": 1,
            "runtimeRevision": revision,
            "requestedModel": getattr(self.model, "model_name", None)
            or self.environment.get("BUSINESS_CODE_MODEL_NAME"),
            "maxCalls": self.max_calls,
            "maxSeconds": self.max_seconds,
            "answerMode": self.profile.answer_mode,
            "workflowMode": self.profile.investigation,
            "outputTokens": self.environment.get("BUSINESS_CODE_AGENT_OUTPUT_TOKENS", "4096"),
            "finalOutputTokens": self.environment.get(
                "BUSINESS_CODE_AGENT_FINAL_OUTPUT_TOKENS", "3072"),
            "thinking": self.environment.get("BUSINESS_CODE_MODEL_ENABLE_THINKING"),
            "temperature": self.environment.get("BUSINESS_CODE_MODEL_TEMPERATURE", ".2"),
            "contextChars": self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000"),
            "networkTimeoutSeconds": self.environment.get("BUSINESS_CODE_MODEL_TIMEOUT", "120"),
            "maxRetries": 0,
            "checkpointEnabled": checkpoint,
            "checkpointVersion": CHECKPOINT_VERSION if checkpoint else None,
            "checkpointTimeoutSeconds": self.environment.get(
                CHECKPOINT_TIMEOUT_ENV, str(CHECKPOINT_DEFAULT_TIMEOUT)),
            "checkpointOutputTokens": self.environment.get(
                CHECKPOINT_OUTPUT_TOKENS_ENV, str(CHECKPOINT_DEFAULT_OUTPUT_TOKENS)),
            "checkpointContextChars": self.environment.get(
                CHECKPOINT_CONTEXT_CHARS_ENV, str(CHECKPOINT_DEFAULT_CONTEXT_CHARS)),
        }

    def chat_model(self, *, timeout=None, max_tokens=None):
        if self.model is not None:
            return self.model
        env = self.environment
        if env.get("BUSINESS_CODE_MODEL_ENABLED", "true").lower() not in {"true", "1", "yes"}:
            raise RuntimeErrorBase("当前模型连接已禁用，请启用 BUSINESS_CODE_MODEL_ENABLED")
        if env.get("BUSINESS_CODE_MODEL_PROVIDER", "openai").lower() != "openai":
            raise RuntimeErrorBase(
                "当前运行时需要 OpenAI 兼容协议；请设置 BUSINESS_CODE_MODEL_PROVIDER=openai")
        if not all(env.get("BUSINESS_CODE_MODEL_" + key) for key in ("NAME", "API_KEY", "BASE_URL")):
            raise RuntimeErrorBase(
                "请配置 BUSINESS_CODE_MODEL_NAME、BASE_URL、API_KEY 后启动问答")
        extra = {}
        if env.get("BUSINESS_CODE_MODEL_ENABLE_THINKING") is not None:
            extra["enable_thinking"] = env["BUSINESS_CODE_MODEL_ENABLE_THINKING"].lower() in {
                "1", "true", "yes"}
        return ChatOpenAI(
            model=env["BUSINESS_CODE_MODEL_NAME"],
            api_key=env["BUSINESS_CODE_MODEL_API_KEY"],
            base_url=env.get("BUSINESS_CODE_MODEL_BASE_URL"),
            temperature=float(env.get("BUSINESS_CODE_MODEL_TEMPERATURE", ".2")),
            timeout=float(timeout or env.get("BUSINESS_CODE_MODEL_TIMEOUT", "120")),
            max_retries=0,
            max_tokens=int(max_tokens or env.get("BUSINESS_CODE_AGENT_OUTPUT_TOKENS", "4096")),
            use_responses_api=False,
            extra_body=extra,
        )

    def ask(self, question, *, context: RunContext, prepared: PreparedCapabilities,
            session_id=None, event_callback=None, cancel_check=None,
            call_callback=None) -> RuntimeResult:
        if context.profile.id != self.profile.id:
            raise ValueError("RunContext 与 Runtime Profile 不一致")
        if not prepared.tools:
            raise RuntimeErrorBase("本轮没有可用的只读工具")
        return asyncio.run(self._ask(question, context=context, prepared=prepared,
                                     session_id=session_id, event_callback=event_callback,
                                     cancel_check=cancel_check, call_callback=call_callback))

    async def _ask(self, question, *, context, prepared, session_id, event_callback, cancel_check,
                   call_callback):
        started = time.monotonic()
        events: list[dict] = []
        calls = 0
        token_usage: dict[str, int] = {}
        reported_models: set[str] = set()
        tool_calls = 0
        cached_reads = 0
        recovered_reads = 0
        evidence_gaps = 0
        stalled_reads = 0
        peak_stalled_reads = 0
        checkpoint_calls = 0
        checkpoint_summary = None
        stop_reason = None

        def emit(kind, **payload):
            event = {"sequence": len(events) + 1, "eventType": kind, "payload": payload,
                     "elapsedMs": round((time.monotonic() - started) * 1000, 3)}
            events.append(event)
            if event_callback:
                event_callback(event)

        def final_result(answer, status="completed"):
            return RuntimeResult(
                answer, session,
                events,
                {**usage(), "stopReason": stop_reason},
                status=status,
            )

        def usage():
            return {
                "modelCalls": calls,
                "inputTokens": token_usage.get("input_tokens"),
                "outputTokens": token_usage.get("output_tokens"),
                "reportedModels": sorted(reported_models),
                "toolCalls": tool_calls,
                "cachedReads": cached_reads,
                "recoveredReads": recovered_reads,
                "evidenceGaps": evidence_gaps,
                "stalledReads": stalled_reads,
                "peakStalledReads": peak_stalled_reads,
                "checkpointCalls": checkpoint_calls,
                "checkpoint": checkpoint_summary,
                "semanticProof": False,
            }

        def stopped(reason):
            nonlocal stop_reason
            stop_reason = reason
            if reason == _STOPPED:
                emit("status", phase="cancelled", label="已停止")
                return final_result("已停止。", status="cancelled")
            emit("status", phase="incomplete", label="调查未完成（达到显式限制）")
            return final_result("本次运行达到显式指定的调查限制，调查未完成；已有调用轨迹已保存。",
                                status="incomplete")

        def finish(answer, status, reason):
            nonlocal stop_reason
            stop_reason = reason
            verification = verify_answer(answer, messages)
            emit("text", id="answer", text=answer, mode="replace")
            if status == "completed":
                emit("status", phase="completed", label="已完成")
            else:
                label = "预算用尽" if reason == "budget" else "连续无新增证据"
                emit("status", phase="incomplete", label=f"调查未完成（{label}），已保留部分答案")
            if not cancel_check or not cancel_check():
                store.save(session, scope, messages)
            result_usage = usage()
            result_usage["verification"] = verification
            result_usage["references"] = verification["references"]
            result_usage["stopReason"] = reason
            return RuntimeResult(answer, session, events, result_usage, status=status)

        store = DirectSessionStore(context.project.data_root / "query-sessions" / "core")
        scope = self._session_scope(context, prepared)
        session = session_id or context.session_id or f"DA-{uuid.uuid4().hex}"
        messages = retire_changed_sources(store.load(session, scope), prepared.files)
        messages.append(HumanMessage(question))
        system_prompt = self._system_prompt(context, prepared)
        chat = self.chat_model()
        bound = chat.bind_tools(list(prepared.tools))
        tool_map = {tool.name: tool for tool in prepared.tools}
        ledger = ReadLedger()
        char_limit = int(self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000"))

        def record_usage(message):
            for key, value in (getattr(message, "usage_metadata", None) or {}).items():
                if isinstance(value, (int, float)):
                    token_usage[key] = token_usage.get(key, 0) + int(value)
            if (message.response_metadata or {}).get("model_name"):
                reported_models.add(str(message.response_metadata["model_name"]))

        tool_definitions = [convert_to_openai_tool(tool) for tool in prepared.tools]

        def record_call(entry):
            if call_callback:
                call_callback({**entry})

        def begin_call(request_messages, attempt, tools=None, phase="investigation"):
            entry = {
                "attempt": attempt,
                "phase": phase,
                "startedAt": _now(),
                "elapsedMs": round((time.monotonic() - started) * 1000, 3),
                "messages": [public_message(message) for message in request_messages],
                "tools": tool_definitions if tools is None else tools,
                "status": "running",
                "inputTextChars": sum(len(str(message.content)) for message in request_messages),
            }
            record_call(entry)
            return entry

        def finish_call(entry, call_started, *, response=None, status=None, error=None, phase=None):
            entry["status"] = status or ("failed" if error else "completed")
            entry["completedAt"] = _now()
            entry["durationMs"] = round((time.monotonic() - call_started) * 1000, 3)
            if error is not None:
                entry["errorType"] = type(error).__name__
                entry["error"] = str(error)
            if response is not None:
                entry["responses"] = [public_message(response)]
                entry["usage"] = [getattr(response, "usage_metadata", None)]
                if phase:
                    entry["phase"] = phase
                elif not getattr(response, "tool_calls", None):
                    entry["phase"] = "answer"
            record_call(entry)

        async def controlled(awaitable):
            pending = asyncio.ensure_future(awaitable)
            try:
                while not pending.done():
                    await asyncio.wait([pending], timeout=0.1)
                    if cancel_check and cancel_check():
                        raise _Stopped(_STOPPED)
                    if self.max_seconds is not None and time.monotonic() - started >= self.max_seconds:
                        raise _Stopped("budget")
                return pending.result()
            finally:
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                elif not pending.cancelled():
                    pending.exception()

        checkpoint_enabled = self.checkpoint_enabled()
        checkpoint_done = False

        def finalize_state():
            remaining_calls = (None if self.max_calls is None
                               else self.max_calls - calls)
            remaining_seconds = (None if self.max_seconds is None
                                 else self.max_seconds - (time.monotonic() - started))
            reserve = (min(FINALIZE_RESERVED_SECONDS, self.max_seconds * 0.4)
                       if self.max_seconds is not None else FINALIZE_RESERVED_SECONDS)
            if remaining_calls is not None and remaining_calls <= FINALIZE_RESERVED_CALLS:
                return "budget", reserve
            if remaining_seconds is not None and remaining_seconds <= reserve:
                return "budget", reserve
            if stalled_reads >= STALL_FINALIZE_READS:
                return "no_progress", reserve
            return None, reserve

        def checkpoint_due():
            if not checkpoint_enabled or checkpoint_done or calls < 1 or not ledger.index():
                return False
            return _checkpoint_due(self.max_calls, self.max_seconds, calls,
                                   time.monotonic() - started)

        async def run_checkpoint():
            nonlocal calls, checkpoint_calls, checkpoint_summary
            prompt = CHECKPOINT_SYSTEM
            request = HumanMessage(CHECKPOINT_REQUEST)
            try:
                timeout, max_tokens = self.checkpoint_limits(time.monotonic() - started)
                checkpoint_chars = max(2000, int(self.environment.get(
                    CHECKPOINT_CONTEXT_CHARS_ENV, str(CHECKPOINT_DEFAULT_CONTEXT_CHARS))))
            except (TypeError, ValueError):
                checkpoint_summary = {"status": "failed", "reason": "config"}
                emit("checkpoint", attempt=calls, status="failed", reason="config")
                return None
            content_budget = max(
                1, min(char_limit, checkpoint_chars) - len(prompt) - len(request.content))
            bounded = compact_checkpoint_messages(messages, content_budget)
            if (sum(len(str(message.content)) for message in bounded)
                    + len(request.content) > content_budget):
                checkpoint_summary = {"status": "skipped", "reason": "context"}
                emit("checkpoint", attempt=calls, status="skipped", reason="context")
                return None
            if timeout <= 1.0:
                checkpoint_summary = {"status": "skipped", "reason": "budget"}
                emit("checkpoint", attempt=calls, status="skipped", reason="budget")
                return None
            calls += 1
            checkpoint_calls += 1
            emit("status", phase="checkpoint", label="正在整理调查进展（检查点）")
            call_started = time.monotonic()
            request_messages = [SystemMessage(prompt), *bounded, request]
            call_entry = begin_call(request_messages, calls, tools=[], phase="checkpoint")
            try:
                checkpoint_chat = self.chat_model(timeout=timeout, max_tokens=max_tokens)
                response = await controlled(checkpoint_chat.ainvoke(request_messages))
            except _Stopped as exc:
                reason = str(exc) or _STOPPED
                finish_call(call_entry, call_started,
                            status="cancelled" if reason == _STOPPED else "incomplete")
                raise
            except Exception as exc:
                finish_call(call_entry, call_started, error=exc)
                checkpoint_summary = {"status": "failed", "errorType": type(exc).__name__}
                call_entry["checkpoint"] = checkpoint_summary
                record_call(call_entry)
                emit("checkpoint", attempt=calls, status="failed",
                     errorType=type(exc).__name__)
                return None
            record_usage(response)
            finish_call(call_entry, call_started, response=response, phase="checkpoint")
            data = _checkpoint_json(str(response.text or ""))
            reason = "parse"
            parsed = None
            if isinstance(data, dict):
                try:
                    parsed = _normalize_checkpoint(data)
                except (TypeError, ValueError, KeyError, AttributeError) as exc:
                    reason = "structure"
                    checkpoint_summary = {"status": "failed", "reason": reason,
                                          "errorType": type(exc).__name__}
                if parsed is None and checkpoint_summary is None:
                    reason = "structure"
            if parsed is None:
                checkpoint_summary = checkpoint_summary or {"status": "failed", "reason": reason}
                call_entry["checkpoint"] = checkpoint_summary
                record_call(call_entry)
                emit("checkpoint", attempt=calls, status="failed", reason=reason)
                return None
            try:
                _verify_checkpoint(parsed, messages)
                confirmed = sum(1 for item in parsed["aspects"]
                                if item["status"] == "confirmed")
                partial = sum(1 for item in parsed["aspects"]
                               if item["status"] == "partial")
                missing = sum(1 for item in parsed["aspects"]
                               if item["status"] == "missing")
                required = [gap for gap in parsed["gaps"] if gap["importance"] == "required"]
                unresolvable = [gap for gap in required if not gap["resolvable"]]
                adjusted = [gap for gap in parsed["gaps"] if gap.get("importanceAdjusted")]
                progress = _checkpoint_progress(parsed)
            except Exception as exc:
                checkpoint_summary = {"status": "failed", "reason": "structure",
                                      "errorType": type(exc).__name__}
                call_entry["checkpoint"] = checkpoint_summary
                record_call(call_entry)
                emit("checkpoint", attempt=calls, status="failed", reason="structure",
                     errorType=type(exc).__name__)
                return None
            checkpoint_summary = {
                "status": "completed",
                "confirmedCount": confirmed,
                "partialCount": partial,
                "missingCount": missing,
                "gapCount": len(parsed["gaps"]),
                "requiredGapCount": len(required),
                "requiredUnresolvableCount": len(unresolvable),
                "adjustedGapCount": len(adjusted),
                "gaps": [{"aspectId": gap["aspectId"], "question": gap["question"],
                          "importance": gap["importance"], "resolvable": gap["resolvable"]}
                         for gap in parsed["gaps"][:8]],
            }
            call_entry["checkpoint"] = {
                **checkpoint_summary,
                "draftAnswer": parsed["draftAnswer"][:1000],
                "aspects": [{"id": item["id"], "question": item["question"],
                             "required": item["required"], "status": item["status"],
                             "evidence": item["evidence"],
                             "verification": item.get("verification")}
                            for item in parsed["aspects"][:8]],
            }
            record_call(call_entry)
            emit("checkpoint", attempt=calls, status="completed",
                 confirmedCount=confirmed, partialCount=partial, missingCount=missing,
                 gapCount=len(parsed["gaps"]), requiredGapCount=len(required),
                 requiredUnresolvableCount=len(unresolvable),
                 adjustedGapCount=len(adjusted))
            messages.append(HumanMessage(
                progress, additional_kwargs={"runtimeOnly": CHECKPOINT_RUNTIME_ONLY}))
            return progress

        try:
            while True:
                if cancel_check and cancel_check():
                    return stopped(_STOPPED)
                if self.max_seconds is not None and time.monotonic() - started >= self.max_seconds:
                    return stopped("budget")
                if self.max_calls is not None and calls >= self.max_calls:
                    return stopped("budget")
                finalize_reason, finalize_reserve = finalize_state()
                if not finalize_reason and checkpoint_due():
                    checkpoint_done = True
                    await run_checkpoint()
                    finalize_reason, finalize_reserve = finalize_state()
                prompt = system_prompt
                invoker = bound
                finalize_label = ""
                final_message = None
                if finalize_reason:
                    finalize_label = "预算用尽" if finalize_reason == "budget" else "连续无新增证据"
                    index = ledger.index()
                    prompt = system_prompt + FINALIZE_NOTICE
                    if index:
                        prompt += "\n\n已读证据索引（仅用于引用，不要重复读取）：\n" + index
                    final_message = HumanMessage(FINAL_ANSWER_REQUEST)
                    invoker = self.chat_model(
                        timeout=max(60.0, finalize_reserve),
                        max_tokens=int(self.environment.get(
                            "BUSINESS_CODE_AGENT_FINAL_OUTPUT_TOKENS", "3072")))
                elif stalled_reads >= STALL_WARN_READS:
                    prompt = system_prompt + STALL_NOTICE
                tail_chars = len(final_message.content) if final_message is not None else 0
                content_budget = max(1, char_limit - len(prompt) - tail_chars)
                bounded = compact_messages(messages, content_budget)
                ledger.observe(bounded)
                if (sum(len(str(message.content)) for message in bounded) + tail_chars
                        > content_budget):
                    raise RuntimeErrorBase("上下文容量不足，请缩小问题范围后重试")
                calls += 1
                if finalize_reason:
                    emit("status", phase="thinking", label=f"正在整理答案（{finalize_label}）")
                elif stalled_reads >= STALL_WARN_READS:
                    emit("status", phase="thinking", label=f"正在分析（第 {calls} 轮，连续无新增证据）")
                else:
                    emit("status", phase="thinking", label=f"正在分析（第 {calls} 轮）")
                request_messages = [SystemMessage(prompt), *bounded]
                if final_message is not None:
                    request_messages.append(final_message)
                call_started = time.monotonic()
                call_entry = begin_call(request_messages, calls,
                                        tools=[] if finalize_reason else None)
                try:
                    response = await controlled(invoker.ainvoke(request_messages))
                except _Stopped as exc:
                    reason = str(exc) or _STOPPED
                    finish_call(call_entry, call_started,
                                status="cancelled" if reason == _STOPPED else "incomplete")
                    return stopped(reason)
                except Exception as exc:
                    finish_call(call_entry, call_started, error=exc)
                    if finalize_reason:
                        return finish(
                            "本次运行达到显式指定的调查限制，调查未完成；已有调用轨迹已保存。",
                            "incomplete", finalize_reason)
                    raise
                record_usage(response)
                messages.append(response)
                finish_call(call_entry, call_started, response=response,
                            phase="answer" if finalize_reason else None)
                emit("model", attempt=calls, messageCount=len(messages),
                     toolCalls=[call.get("name") for call in (response.tool_calls or [])],
                     outputChars=len(str(response.text or "")),
                     inputTokens=token_usage.get("input_tokens"),
                     outputTokens=token_usage.get("output_tokens"))
                if finalize_reason:
                    final_text = str(response.text or "").strip()
                    if final_text:
                        final = (f"（调查未完成：{finalize_label}，"
                                 f"以下为基于已读证据整理的部分答案）\n\n{final_text}")
                    else:
                        final = "本次运行达到显式指定的调查限制，调查未完成；已有调用轨迹已保存。"
                    return finish(final, "incomplete", finalize_reason)
                pending_calls = response.tool_calls or []
                if not pending_calls:
                    final = str(response.text or "").strip()
                    if not final:
                        raise RuntimeErrorBase("模型没有返回最终答案")
                    break
                for call in pending_calls:
                    if cancel_check and cancel_check():
                        return stopped(_STOPPED)
                    tool_calls += 1
                    call_id = str(call.get("id") or f"tool-{uuid.uuid4().hex}")
                    name = str(call.get("name") or "")
                    arguments = call.get("args") or {}
                    emit("tool", id=call_id, name=name, input=arguments,
                         status="running", origin="model")
                    tool = tool_map.get(name)
                    if tool is None:
                        output = json.dumps({"error": f"未注册工具：{name}", "errorType": "UnknownTool"},
                                            ensure_ascii=False)
                    else:
                        cached = ledger.cached(arguments) if name == "Read" else None
                        if cached is not None:
                            cached_reads += 1
                            stalled_reads += 1
                            peak_stalled_reads = max(peak_stalled_reads, stalled_reads)
                            if cached.get("recovered"):
                                recovered_reads += 1
                            if cached.get("evidenceGap"):
                                evidence_gaps += 1
                            output = json.dumps(cached, ensure_ascii=False)
                        else:
                            try:
                                value = tool.invoke(arguments)
                                output = (value if isinstance(value, str)
                                          else json.dumps(value, ensure_ascii=False))
                                if name == "Read":
                                    ledger.record(output)
                                    stalled_reads = 0
                            except Exception as exc:
                                output = json.dumps({"error": str(exc), "errorType": type(exc).__name__},
                                                    ensure_ascii=False)
                    try:
                        failed = bool(json.loads(output).get("error"))
                    except (TypeError, ValueError):
                        failed = False
                    emit("tool", id=call_id, name=name, input=arguments,
                         status="error" if failed else "completed", output=output, origin="model")
                    messages.append(ToolMessage(
                        content=output, name=name, tool_call_id=call_id,
                        status="error" if failed else "success",
                    ))
        except _Stopped as exc:
            return stopped(str(exc) or _STOPPED)

        return finish(final, "completed", None)

    def _session_scope(self, context: RunContext, prepared: PreparedCapabilities) -> dict:
        settings = self.execution_settings()
        scope = {
            "projectId": context.project.project_id,
            "profileId": context.profile.id,
            "repositoryIds": list(context.repository_ids),
            "materials": [[source.kind, source.path, source.status, source.version]
                          for source in context.materials],
            "tools": [[tool.name, str(getattr(tool, "description", ""))]
                      for tool in prepared.tools],
            "instructions": [text for _, text in prepared.instructions],
            "model": settings.get("requestedModel"),
            "contextChars": int(self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000")),
            "promptVersion": PROMPT_VERSION,
            "checkpoint": settings.get("checkpointEnabled"),
            "checkpointVersion": settings.get("checkpointVersion"),
        }
        if settings.get("checkpointEnabled"):
            scope.update({
                "checkpointTimeoutSeconds": settings.get("checkpointTimeoutSeconds"),
                "checkpointOutputTokens": settings.get("checkpointOutputTokens"),
                "checkpointContextChars": settings.get("checkpointContextChars"),
            })
        return scope

    def _system_prompt(self, context: RunContext, prepared: PreparedCapabilities) -> str:
        sources = [f"- {source.repository_id}: {source.path}"
                   for source in context.materials
                   if source.kind == "source" and source.available]
        requirements = next((source for source in context.materials
                             if source.kind == "requirements"), None)
        requirement_line = (
            f"- requirements：{requirements.path}（{'可读取' if requirements.available else '缺失'}）"
            if requirements else "- requirements：未授权"
        )
        guidance = "\n".join(text for _, text in prepared.instructions)
        return (
            "你是 CodeAgent，在只授权的只读资料范围内自主调查源码并回答问题。\n\n"
            f"工程：{context.project.project_name}（{context.project.project_id}）\n"
            "授权源码仓库：\n" + ("\n".join(sources) or "- 无") + "\n"
            f"{requirement_line}\n\n"
            "工具由你自行选择；没有程序预设的问题分类、调查步骤或自动补查。\n\n"
            "调查策略：\n"
            "1. 先明确问题需要确认的对象、行为、前置条件和结果，并列出问题明确要求覆盖的仓库、系统或职责环节；不得省略这些关键环节。\n"
            "2. 已知文件或方法时直接定位；宽范围关键词优先用 Grep 的 files 模式发现候选，再缩小 path 用 matches 定位行号，最后 Read 核实；Grep 命中（含 context）只是定位线索，不能作为引用依据。工具选择可根据问题灵活调整。\n"
            "3. 跨系统或跨仓库问题，沿调用、路由、配置和数据线索确认每个相关系统的实际职责；不因问题提到某系统就禁止调查其他授权系统，也不因一个仓库已能解释部分链路就跳过问题要求的其他系统。\n"
            "4. 每次继续调查前明确要解决的未确认问题；若下一步只会增加无关实现细节，不再扩展。\n"
            "5. 问题要求的关键对象和环节均已有源码依据时直接回答，不以穷尽所有关联类作为完成条件；无法确认的关键环节明确说明缺口，不能用推测填补。\n"
            "6. 业务知识用于理解术语和业务约定，当前实现必须以已读源码为依据。\n\n"
            "作答要求：\n"
            "1. 具体实现必须以 Read 到的源码为依据；Glob/Grep 命中只是定位线索。\n"
            "2. 跨仓库或跨系统问题，可根据已读代码中的调用、路由、配置或事件线索，"
            "自行决定是否继续检索其他授权仓库。\n"
            "3. 无法确认的关系、未读取的部分和访问失败要明确说明；"
            "不要虚构调用链，也不要把局部代码当作完整业务流程。\n"
            "4. 最终回答使用中文，关键实现结论给出源码相对路径和行号，"
            "例如 repos/channel/src/main/java/Example.java:42。\n"
            "5. 需求原文只说明目标，不能当作已经实现；区分已确认实现与未确认项。\n"
            "6. 只使用已注册工具，不修改文件，不访问未授权目录。\n\n"
            f"{guidance}"
        )


def _checkpoint_due(max_calls, max_seconds, calls, elapsed):
    call_half = None
    time_half = None
    if max_calls is not None:
        normal_calls = max(1, max_calls - FINALIZE_RESERVED_CALLS)
        call_half = normal_calls / 2.0
    if max_seconds is not None:
        reserve = min(FINALIZE_RESERVED_SECONDS, max_seconds * 0.4)
        normal_seconds = max(1.0, max_seconds - reserve)
        time_half = normal_seconds / 2.0
    if call_half is None and time_half is None:
        call_half, time_half = 10.0, 120.0
    return ((call_half is not None and calls >= call_half)
            or (time_half is not None and elapsed >= time_half))


def _checkpoint_json(text):
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(value[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_checkpoint(text):
    data = _checkpoint_json(text)
    if not isinstance(data, dict):
        return None
    try:
        return _normalize_checkpoint(data)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def _normalize_checkpoint(data):
    if not isinstance(data, dict):
        return None
    raw_aspects = data.get("aspects")
    if not isinstance(raw_aspects, list) or not raw_aspects:
        return None
    aspects = []
    seen = set()
    for item in raw_aspects[:5]:
        if not isinstance(item, dict):
            return None
        aspect_id = str(item.get("id") or "").strip()[:40]
        if not aspect_id or aspect_id in seen:
            return None
        seen.add(aspect_id)
        required = item.get("required", True)
        if not isinstance(required, bool):
            return None
        status = str(item.get("status") or "").strip().lower()
        if status not in {"confirmed", "partial", "missing"}:
            return None
        question = str(item.get("question") or "").strip()[:300]
        claim = str(item.get("claim") or "").strip()[:800]
        if not (question or claim):
            return None
        raw_evidence = item.get("evidence")
        if raw_evidence is None:
            raw_evidence = []
        if not isinstance(raw_evidence, list):
            return None
        evidence = []
        for value in raw_evidence[:5]:
            if not isinstance(value, str):
                return None
            value = value.strip()[:240]
            if value:
                evidence.append(value)
        aspects.append({
            "id": aspect_id,
            "question": question,
            "required": required,
            "status": status,
            "claim": claim,
            "evidence": evidence,
        })
    if not any(aspect["required"] for aspect in aspects):
        return None
    raw_gaps = data.get("gaps")
    if raw_gaps is None:
        raw_gaps = []
    if not isinstance(raw_gaps, list):
        return None
    gaps = []
    for item in raw_gaps[:4]:
        if not isinstance(item, dict):
            return None
        aspect_id = str(item.get("aspectId") or "").strip()[:40]
        if aspect_id not in seen:
            return None
        importance = str(item.get("importance") or "").strip().lower()
        if importance not in {"required", "optional"}:
            return None
        resolvable = item.get("resolvable", True)
        if not isinstance(resolvable, bool):
            return None
        gaps.append({
            "aspectId": aspect_id,
            "question": str(item.get("question") or "").strip()[:300],
            "importance": importance,
            "resolvable": resolvable,
            "reason": str(item.get("reason") or "").strip()[:400],
            "nextAction": str(item.get("nextAction") or "").strip()[:400],
        })
    by_id = {aspect["id"]: aspect for aspect in aspects}
    for gap in gaps:
        if not gap["question"]:
            aspect = by_id[gap["aspectId"]]
            gap["question"] = aspect["question"] or aspect["claim"]
    for aspect in aspects:
        linked = [gap for gap in gaps if gap["aspectId"] == aspect["id"]]
        if aspect["status"] == "missing" and not linked:
            return None
        if aspect["status"] == "missing" and aspect["required"]:
            if not any(gap["importance"] == "required" for gap in linked):
                for gap in linked:
                    gap["importance"] = "required"
                    gap["importanceAdjusted"] = True
    return {
        "draftAnswer": str(data.get("draftAnswer") or "").strip()[:2000],
        "aspects": aspects,
        "gaps": gaps,
    }


def _verify_checkpoint(checkpoint, messages):
    for aspect in checkpoint["aspects"]:
        if aspect["status"] != "confirmed":
            continue
        if not aspect["evidence"]:
            aspect["status"] = "partial"
            aspect["verification"] = "no_evidence"
            continue
        references = verify_answer("\n".join(aspect["evidence"]), messages)["references"]
        if references and all(reference["confirmed"] for reference in references):
            aspect["verification"] = "citation_verified"
        else:
            aspect["status"] = "partial"
            aspect["verification"] = "citation_unverified"
    return checkpoint


def _checkpoint_progress(checkpoint):
    lines = ["[调查进展检查点] 程序已核对引用；未通过核验的结论只能视为部分确认。"]
    confirmed = [item for item in checkpoint["aspects"] if item["status"] == "confirmed"]
    partial = [item for item in checkpoint["aspects"] if item["status"] == "partial"]
    missing = [item for item in checkpoint["aspects"] if item["status"] == "missing"]
    if confirmed:
        lines.append("已确认：")
        for item in confirmed[:6]:
            evidence = "；".join(item["evidence"][:2])
            lines.append(f"- {item['question'] or item['claim']}：{item['claim']}"
                         + (f"（{evidence}）" if evidence else ""))
    if partial:
        lines.append("部分确认（不影响回答时可在答案中说明，不必为此扩展调查）：")
        for item in partial[:6]:
            lines.append(f"- {item['question'] or item['claim']}：{item['claim']}")
    if missing:
        lines.append("尚未确认：")
        for item in missing[:5]:
            flag = "必需" if item["required"] else "非必需"
            lines.append(f"- [{flag}] {item['question'] or item['claim']}")
    required = [gap for gap in checkpoint["gaps"] if gap["importance"] == "required"]
    resolvable = [gap for gap in required if gap["resolvable"]]
    unresolvable = [gap for gap in required if not gap["resolvable"]]
    optional = [gap for gap in checkpoint["gaps"] if gap["importance"] == "optional"]
    if resolvable:
        lines.append("关键缺口（下一步只补查这些，补齐后立即作答）：")
        for gap in resolvable[:6]:
            line = f"- {gap['question']}：{gap['reason']}"
            if gap["nextAction"]:
                line += f"；建议：{gap['nextAction']}"
            lines.append(line)
    if unresolvable:
        lines.append("当前授权资料内无法继续核实（不要继续搜索；在答案中说明限制）：")
        for gap in unresolvable[:6]:
            line = f"- {gap['question']}：{gap['reason']}"
            if gap["nextAction"]:
                line += f"；原计划：{gap['nextAction']}"
            lines.append(line)
    if not required:
        lines.append("没有必须补齐的关键缺口；若已确认结论足以回答，请直接作答，"
                     "未确认项在答案中说明。")
    if optional:
        lines.append(f"另有 {len(optional)} 个可选缺口，不影响回答，不必继续调查。")
    lines.append("除非已有结论与源码矛盾，不要再扩展无关分支。")
    return "\n".join(lines)[:2600]


def verify_answer(answer: str, messages) -> dict:
    """Check cited paths against this run's delivered Read results."""
    reads = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "Read":
            continue
        try:
            data = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if data.get("error") or not data.get("content"):
            continue
        reads.append(data)
    by_path: dict[str, list[dict]] = {}
    for data in reads:
        by_path.setdefault(data["path"], []).append(data)
    references = []
    for match in _CITATION.finditer(answer or ""):
        cited = match.group(1).lstrip("/")
        if cited.startswith("./"):
            cited = cited[2:]
        line = int(match.group(2)) if match.group(2) else None
        line_end = int(match.group(3)) if match.group(3) else line
        matched_path = _match_reference_path(cited, by_path)
        confirmed = bool(matched_path and line is not None
                         and _delivered(by_path[matched_path], line, line_end))
        references.append({
            "path": cited,
            "kind": _reference_kind(matched_path or cited),
            "line": line,
            "lineEnd": line_end,
            "matchedPath": matched_path,
            "fileRead": matched_path is not None,
            "confirmed": confirmed,
        })
    return {
        "semanticProof": False,
        "references": references,
        "unconfirmed": [_reference_text(reference) for reference in references
                        if not reference["confirmed"]],
        "readFiles": sorted(by_path),
    }


def _match_reference_path(cited: str, by_path) -> str | None:
    if cited in by_path:
        return cited
    candidates = [path for path in by_path if path.endswith("/" + cited)]
    if len(candidates) == 1:
        return candidates[0]
    if "..." not in cited and "…" not in cited:
        return None
    pattern = cited.replace("…", "...")
    if not pattern.replace(".", "").strip("/"):
        return None
    expression = re.escape(pattern).replace(r"\.\.\.", ".*")
    matcher = re.compile(r"(?:^|/)" + expression + r"$")
    matches = [path for path in by_path if matcher.search(path)]
    return matches[0] if len(matches) == 1 else None


def _reference_kind(path: str) -> str:
    if path.startswith("knowledge/business-context/"):
        return "business_context"
    if path.startswith("requirements/"):
        return "requirements"
    return "source"


def _reference_text(reference) -> str:
    path, line, line_end = reference["path"], reference["line"], reference["lineEnd"]
    if line is None:
        return path
    return f"{path}:{line}" if line_end == line else f"{path}:{line}-{line_end}"


def _delivered(read_results, start, end) -> bool:
    """Whether the cited line interval was fully present in delivered Read output."""
    low, high = sorted((start, end))
    intervals = sorted(
        (item["lineStart"], item["lineEnd"]) for item in read_results
        if item.get("lineStart") is not None and item.get("lineEnd") is not None)
    cursor = low
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end + 1)
        if cursor > high:
            return True
    return False

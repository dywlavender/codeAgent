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
from .session import DirectSessionStore, ReadLedger, compact_messages, retire_changed_sources


PROMPT_VERSION = 2
_STOPPED = "cancelled"
STALL_WARN_READS = 6
STALL_FINALIZE_READS = 10
FINALIZE_RESERVED_CALLS = 2
FINALIZE_RESERVED_SECONDS = 200.0
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

    def execution_settings(self):
        return {
            "agentVersion": 1,
            "runtimeRevision": f"core-direct-{PROMPT_VERSION}",
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

        def begin_call(request_messages, attempt, tools=None):
            entry = {
                "attempt": attempt,
                "phase": "investigation",
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

        try:
            while True:
                if cancel_check and cancel_check():
                    return stopped(_STOPPED)
                if self.max_seconds is not None and time.monotonic() - started >= self.max_seconds:
                    return stopped("budget")
                if self.max_calls is not None and calls >= self.max_calls:
                    return stopped("budget")
                finalize_reason = None
                remaining_calls = (None if self.max_calls is None
                                   else self.max_calls - calls)
                remaining_seconds = (None if self.max_seconds is None
                                     else self.max_seconds - (time.monotonic() - started))
                finalize_reserve = (min(FINALIZE_RESERVED_SECONDS, self.max_seconds * 0.4)
                                    if self.max_seconds is not None else FINALIZE_RESERVED_SECONDS)
                if remaining_calls is not None and remaining_calls <= FINALIZE_RESERVED_CALLS:
                    finalize_reason = "budget"
                elif (remaining_seconds is not None
                      and remaining_seconds <= finalize_reserve):
                    finalize_reason = "budget"
                elif stalled_reads >= STALL_FINALIZE_READS:
                    finalize_reason = "no_progress"
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
        return {
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
        }

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

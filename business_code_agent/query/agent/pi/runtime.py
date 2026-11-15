"""Pi-backed runtime for the unified online query path."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from business_code_agent.query.capabilities import (PI_TOOL_NAMES,
                                                    prepare_pi_capabilities)
from business_code_agent.query.context import RunContext
from business_code_agent.query.profile import AgentProfile
from business_code_agent.query.runtime import RuntimeErrorBase, RuntimeResult

from .events import PiEventAdapter
from .models import read_model_catalog
from .process import PiProcess, PiProcessError
from .sessions import (prepare_session_root, resolve_session_file,
                       session_root as project_session_root, validate_session_file)


SEARCH_EXTENSION = Path(__file__).with_name("extension") / "codeagent_search.js"
SEARCH_EXTENSION_COMMAND = "codeagent-readonly-search"


class PiConfigurationError(RuntimeErrorBase):
    """Pi or its configured model is not ready for this run."""


class PiAgentRuntime:
    """One Pi RPC process per question, with Pi-owned session state."""

    runtime_name = "PI"

    def __init__(self, *, profile: AgentProfile, environment: Mapping[str, str] | None = None,
                 command: str | list[str] | tuple[str, ...] | None = None,
                 model: str | None = None, timeout: float | None = None,
                 session_root: str | Path | None = None, thinking: str | None = None):
        self.profile = profile
        # Pi is a child process: it needs the host environment (PATH, provider
        # credentials, proxy settings) plus any explicit overrides.
        self.environment = {**os.environ, **(environment or {})}
        raw_command = command if command is not None else self.environment.get(
            "BUSINESS_CODE_PI_COMMAND", "pi")
        self.command = (tuple(shlex.split(raw_command)) if isinstance(raw_command, str)
                        else tuple(str(item) for item in raw_command))
        if not self.command:
            raise PiConfigurationError("BUSINESS_CODE_PI_COMMAND 不能为空")
        self.model = (model if model is not None else
                      self.environment.get("BUSINESS_CODE_PI_MODEL", "")).strip()
        raw_timeout = (timeout if timeout is not None else
                       self.environment.get("BUSINESS_CODE_PI_TIMEOUT", "600"))
        try:
            self.timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise PiConfigurationError("BUSINESS_CODE_PI_TIMEOUT 必须是数字") from exc
        if self.timeout <= 0:
            raise PiConfigurationError("BUSINESS_CODE_PI_TIMEOUT 必须大于 0")
        self.thinking = (thinking if thinking is not None else
                         self.environment.get("BUSINESS_CODE_PI_THINKING", "")).strip() or None
        self.session_root = (Path(session_root).expanduser().resolve()
                             if session_root else None)
        self._version: str | None = None
        self._version_error: str | None = None

    def execution_settings(self) -> dict[str, Any]:
        return {
            "runtime": "pi",
            "requestedModel": self.model or None,
            "piCommand": " ".join(self.command),
            "piVersion": self._version,
            "tools": list(PI_TOOL_NAMES),
            "timeoutSeconds": self.timeout,
            "thinking": self.thinking,
            "sessionRoot": str(self.session_root) if self.session_root else None,
        }

    def with_model(self, model: str | None) -> "PiAgentRuntime":
        """Return a runtime bound to one selected model without mutating config."""
        selected = str(model or "").strip()
        if not selected or selected == self.model:
            return self
        clone = PiAgentRuntime(
            profile=self.profile, environment=self.environment, command=self.command,
            model=selected, timeout=self.timeout, session_root=self.session_root,
            thinking=self.thinking)
        clone._version = self._version
        clone._version_error = self._version_error
        return clone

    def models_catalog(self) -> dict[str, Any]:
        """Return Pi's available models with provider details removed."""
        version = self.preflight()
        try:
            catalog = read_model_catalog(
                self.command, self.environment, configured_model=self.model,
                timeout=min(30.0, max(10.0, self.timeout)))
        except RuntimeErrorBase as exc:
            raise PiConfigurationError(f"Pi 模型列表读取失败：{exc}") from exc
        except PiProcessError as exc:
            raise PiConfigurationError(f"Pi 模型列表读取失败：{exc}") from exc
        catalog["piVersion"] = version
        return catalog

    def preflight(self) -> str:
        """Check the CLI and cache its reported version."""
        if self._version:
            return self._version
        if self._version_error:
            raise PiConfigurationError(self._version_error)
        executable = self.command[0]
        found = Path(executable).expanduser().is_file() or shutil.which(executable)
        if not found:
            self._version_error = f"Pi CLI 不可用：{executable}"
            raise PiConfigurationError(self._version_error)
        try:
            result = subprocess.run(
                [*self.command, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
                env=self.environment or None, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._version_error = f"无法检查 Pi CLI：{exc}"
            raise PiConfigurationError(self._version_error) from exc
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            detail = (result.stderr or output).strip() or "unknown error"
            self._version_error = f"Pi CLI 版本检查失败：{detail}"
            raise PiConfigurationError(self._version_error)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        match = next((re.search(r"\d+\.\d+\.\d+[^\s]*", line) for line in reversed(lines)
                      if re.search(r"\d+\.\d+\.\d+", line)), None)
        self._version = match.group(0) if match else (lines[-1] if lines else "unknown")
        return self._version

    def ask(self, question: str, *, context: RunContext,
            prepared=None, session_id: str | None = None, event_callback=None,
            cancel_check=None, call_callback=None, mode: str | None = None) -> RuntimeResult:
        started = time.monotonic()
        version = self.preflight()
        if prepared is None:
            prepared = prepare_pi_capabilities(context, version=version, mode=mode)
        root = prepare_session_root(self.session_root or project_session_root(
            context.project.data_root))
        existing = resolve_session_file(root, session_id)
        events: list[dict[str, Any]] = []

        def emit(event: dict[str, Any]) -> None:
            events.append(event)
            if event_callback:
                event_callback(event)

        allowed_roots = tuple(source.directory for source in context.materials
                              if source.available)
        adapter = PiEventAdapter(emit, workspace=context.workspace,
                                 allowed_roots=allowed_roots,
                                 call_callback=call_callback)
        command = self._command(root, existing, self._system_prompt(
            context, prepared, mode=mode))
        process_environment = {
            **self.environment,
            "CODEAGENT_PI_WORKSPACE": str(Path(context.workspace).resolve()),
            "CODEAGENT_PI_ROOTS": json.dumps(
                [str(Path(context.workspace).resolve()),
                 *(str(Path(root).resolve()) for root in allowed_roots)],
                ensure_ascii=False),
            "CODEAGENT_PI_MOUNTS": json.dumps(
                {str(source.path): str(Path(source.directory).resolve())
                 for source in context.materials
                 if source.available and source.directory},
                ensure_ascii=False),
        }
        process = PiProcess(command, cwd=context.workspace,
                            environment=process_environment)
        cancelled = False
        timed_out = False
        session_file = existing
        actual_model = ""
        try:
            process.start()
            state = self._request(process, {"type": "get_state"}, adapter=adapter,
                                  timeout=min(30.0, max(5.0, self.timeout)))
            if not state.get("success"):
                raise PiConfigurationError(
                    f"Pi 状态检查失败：{state.get('error') or 'unknown error'}")
            state_data = state.get("data") if isinstance(state.get("data"), Mapping) else {}
            actual_model = self._verify_model(state_data.get("model"))
            self._verify_search_extension(process, adapter)
            reported_session = state_data.get("sessionFile")
            if session_file is None:
                session_file = validate_session_file(
                    root, reported_session, require_exists=False)
            else:
                current = validate_session_file(
                    root, reported_session, require_exists=False)
                if current != session_file:
                    session_file = current

            self._emit(emit, "status", phase="thinking", label="Pi 正在分析")
            prompt_id = self._request_id("prompt")
            process.send({"id": prompt_id, "type": "prompt", "message": question})
            response = self._wait_response(process, prompt_id, adapter,
                                           timeout=min(60.0, self.timeout))
            if not response.get("success"):
                raise RuntimeErrorBase(
                    f"Pi 拒绝了本次问题：{response.get('error') or 'unknown error'}")
            disposition = (response.get("data") or {}).get("disposition")
            if disposition == "handled":
                raise RuntimeErrorBase("Pi 输入处理器消费了本次问题，但没有启动调查")

            deadline = started + self.timeout
            abort_at: float | None = None
            while not adapter.settled:
                if adapter.denied_tool:
                    self._send_abort(process)
                    adapter.finish_pending("failed", adapter.denied_tool["reason"])
                    raise RuntimeErrorBase(
                        f"Pi 工具访问越界：{adapter.denied_tool['reason']}")
                now = time.monotonic()
                if not cancelled and cancel_check and cancel_check():
                    cancelled = True
                    abort_at = now
                    self._send_abort(process)
                if not cancelled and now >= deadline and abort_at is None:
                    timed_out = True
                    abort_at = now
                    self._send_abort(process)
                if abort_at is not None and now - abort_at > 20:
                    raise RuntimeErrorBase("Pi 在停止请求后仍未结束子进程")
                try:
                    record = process.receive(timeout=0.2)
                except PiProcessError:
                    if cancelled and process.exit_code is not None:
                        break
                    raise
                if record is None:
                    continue
                kind = record.get("type")
                if kind == "response":
                    continue
                if kind == "extension_ui_request":
                    self._answer_extension_ui(process, record)
                    continue
                adapter.handle(record)

            stats = self._optional_data(process, {"type": "get_session_stats"}, adapter)
            final_state = self._optional_data(process, {"type": "get_state"}, adapter)
            if final_state and final_state.get("sessionFile"):
                session_file = validate_session_file(root, final_state["sessionFile"])
            if session_file is None:
                raise RuntimeErrorBase("Pi 未创建会话文件，无法保存追问上下文")
        except PiConfigurationError:
            raise
        except PiProcessError as exc:
            if cancelled:
                adapter.finish_pending("cancelled")
                answer = "已停止。"
                self._emit(emit, "text", id="answer", text=answer, mode="replace")
                self._emit(emit, "status", phase="cancelled", label="已停止")
                return RuntimeResult(answer, str(session_file) if session_file else None,
                                     events, {"stopReason": "aborted", "semanticProof": False},
                                     status="cancelled")
            adapter.finish_pending("failed", str(exc))
            raise RuntimeErrorBase(f"Pi 运行失败：{exc}") from exc
        finally:
            process.close()

        usage = adapter.usage()
        usage["piVersion"] = version
        usage["actualModel"] = actual_model or None
        usage["piSessionFile"] = str(session_file)
        if stats:
            tokens = stats.get("tokens") if isinstance(stats.get("tokens"), Mapping) else {}
            if tokens:
                usage.update({
                    "inputTokens": tokens.get("input"),
                    "outputTokens": tokens.get("output"),
                    "cacheReadTokens": tokens.get("cacheRead"),
                    "cacheWriteTokens": tokens.get("cacheWrite"),
                    "totalTokens": tokens.get("total"),
                })
            if stats.get("cost") is not None:
                usage["cost"] = stats.get("cost")
            usage["contextUsage"] = stats.get("contextUsage")
            usage["piSessionId"] = stats.get("sessionId")
        elif final_state and final_state.get("sessionId"):
            usage["piSessionId"] = final_state.get("sessionId")

        if cancelled:
            adapter.finish_pending("cancelled")
            answer = "已停止。"
            self._emit(emit, "text", id="answer", text=answer, mode="replace")
            self._emit(emit, "status", phase="cancelled", label="已停止")
            return RuntimeResult(answer, str(session_file), events, usage, status="cancelled")
        if timed_out:
            adapter.finish_pending("failed", "timeout")
            raise RuntimeErrorBase(f"Pi 运行超时（{self.timeout:g} 秒），已请求停止")
        if adapter.error_message:
            raise RuntimeErrorBase(f"Pi 模型请求失败：{adapter.error_message}")
        if adapter.aborted:
            adapter.finish_pending("failed", "aborted")
            raise RuntimeErrorBase("Pi 运行被中止，但没有收到用户取消")
        if adapter.stop_reason == "toolUse":
            raise RuntimeErrorBase("Pi 在工具调用后结束，但没有返回最终答案")
        answer = adapter.answer()
        if not answer:
            raise RuntimeErrorBase("Pi 没有返回最终答案")
        incomplete = adapter.stop_reason == "length"
        if incomplete:
            answer = "（回答因模型输出长度限制被截断）\n\n" + answer
        self._emit(emit, "text", id="answer", text=answer, mode="replace")
        self._emit(emit, "status", phase="incomplete" if incomplete else "completed",
                   label="回答被截断（达到模型输出上限）" if incomplete else "已完成")
        return RuntimeResult(answer, str(session_file), events, usage,
                             status="incomplete" if incomplete else "completed")

    def _command(self, root: Path, existing: Path | None, system_prompt: str) -> list[str]:
        command = [
            *self.command,
            "--mode", "rpc",
            "--session-dir", str(root),
            "--no-extensions", "-e", "builtin:codemode",
            "-e", str(SEARCH_EXTENSION),
            "--no-context-files", "--no-mcp", "--no-skills",
            "--no-prompt-templates", "--no-themes", "--no-approve",
            "--system-prompt", system_prompt,
            "--tools", ",".join(PI_TOOL_NAMES),
        ]
        if existing is not None:
            command.extend(["--session", str(existing)])
        else:
            command.extend(["--name", "CodeAgent"])
        if self.model:
            command.extend(["--model", self.model])
        if self.thinking:
            command.extend(["--thinking", self.thinking])
        return command

    def _system_prompt(self, context: RunContext, prepared, *, mode: str | None) -> str:
        purposes = {
            "source": "源码，核实当前实现",
            "requirements": "需求原文，说明业务目标",
            "business_context": "业务补充知识，解释术语、职责与业务约定",
            "code_map": "自动代码地图，只用于定位文件和符号",
            "ast": "AST 结构资料，只用于定位，不证明实现",
        }
        roots = "\n".join(
            f"- {source.path}/：{purposes.get(source.kind, source.kind)}"
            for source in context.materials if source.available
        ) or "- 本轮没有可读资料"
        guidance = "\n".join(text for _, text in prepared.instructions if text)
        mode_label = {
            "none": "无主干（源码与需求）",
            "backbone": "有主干（源码、需求与业务补充知识）",
            "ast": "AST（源码、需求与结构资料）",
            "combined": "组合资料（全部已授权资料）",
        }.get(mode or "", mode or "组合资料")
        return (
            "你是 CodeAgent 的只读代码问答 Agent，负责基于本轮授权资料回答用户问题。"
            "你不修改文件，也不执行 shell 命令。\n\n"
            f"当前工程：{context.project.project_id}；资料模式：{mode_label}。\n"
            "本轮授权资料根（全部是相对当前工作目录的路径）：\n"
            f"{roots}\n\n"
            "调查规则：\n"
            "- 只允许读取上述根目录下的相对路径；禁止绝对路径、..、~/.pi、/opt 等目录。\n"
            "- 你可以使用 read、grep、find、ls、codemode。工具顺序由你根据问题决定，"
            "不要求固定流程；已知文件直接 read，需要定位时用 grep/find/ls，"
            "可用 codemode 并行读取和过滤大输出。\n"
            "- 没有 edit、write、bash，不要尝试修改文件或执行命令。\n"
            "- 源码是当前实现的依据；业务知识解释术语和业务约定；需求说明目标；"
            "代码地图和 AST 只用于定位，不直接证明实现。\n"
            "- 关键结论必须给出真实文件路径和行号；未确认的内容明确标注为未核实或证据缺口。\n"
            "- 用中文直接回答问题，不要复述工具调用过程。\n"
            + (f"\n能力说明：\n{guidance}\n" if guidance else "")
        )

    def _verify_search_extension(self, process: PiProcess,
                                 adapter: PiEventAdapter) -> None:
        if not SEARCH_EXTENSION.is_file():
            raise PiConfigurationError(f"Pi 只读检索扩展不存在：{SEARCH_EXTENSION}")
        response = self._request(process, {"type": "get_commands"}, adapter=adapter,
                                 timeout=15)
        if not response.get("success"):
            raise PiConfigurationError(
                f"Pi 扩展检查失败：{response.get('error') or 'unknown error'}")
        data = response.get("data") if isinstance(response.get("data"), Mapping) else {}
        commands = data.get("commands") if isinstance(data.get("commands"), list) else []
        names = {str(item.get("name")) for item in commands if isinstance(item, Mapping)}
        if SEARCH_EXTENSION_COMMAND not in names:
            raise PiConfigurationError(
                "Pi 只读检索扩展未加载；grep/find 可能无法跟随授权仓库链接")

    def _verify_model(self, model: Any) -> str:
        if not isinstance(model, Mapping):
            if self.model:
                raise PiConfigurationError(
                    f"Pi 没有选择模型，但配置了 {self.model}")
            return ""
        identifier = str(model.get("id") or "")
        provider = str(model.get("provider") or "")
        name = str(model.get("name") or "")
        actual = f"{provider}/{identifier}" if provider and identifier else identifier or name
        if not self.model:
            return actual
        configured = self.model.split(":", 1)[0]
        candidates = {identifier, name, actual}
        if not any(candidate and fnmatch.fnmatchcase(candidate, configured)
                   for candidate in candidates):
            raise PiConfigurationError(
                f"Pi 实际模型 {actual or '未知'} 与配置 {self.model} 不一致")
        return actual

    def _request(self, process: PiProcess, command: Mapping[str, Any], *,
                 adapter: PiEventAdapter | None, timeout: float) -> dict[str, Any]:
        request_id = self._request_id(str(command.get("type") or "request"))
        process.send({**command, "id": request_id})
        return self._wait_response(process, request_id, adapter, timeout=timeout)

    def _wait_response(self, process: PiProcess, request_id: str,
                       adapter: PiEventAdapter | None, *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeErrorBase(f"Pi 未在 {timeout:g} 秒内响应请求")
            record = process.receive(timeout=min(0.2, remaining))
            if record is None:
                continue
            if record.get("type") == "response" and record.get("id") == request_id:
                return record
            if record.get("type") == "extension_ui_request":
                self._answer_extension_ui(process, record)
                continue
            if adapter is not None:
                adapter.handle(record)

    def _optional_data(self, process: PiProcess, command: Mapping[str, Any],
                       adapter: PiEventAdapter | None) -> Mapping[str, Any] | None:
        try:
            response = self._request(process, command, adapter=adapter, timeout=15)
        except (RuntimeErrorBase, PiProcessError):
            return None
        if not response.get("success"):
            return None
        data = response.get("data")
        return data if isinstance(data, Mapping) else None

    def _send_abort(self, process: PiProcess) -> None:
        try:
            process.send({"id": self._request_id("abort"), "type": "abort"})
        except PiProcessError:
            pass

    @staticmethod
    def _answer_extension_ui(process: PiProcess, record: Mapping[str, Any]) -> None:
        method = record.get("method")
        if method not in {"select", "confirm", "input", "editor"}:
            return
        try:
            process.send({"type": "extension_ui_response", "id": record.get("id"),
                          "cancelled": True})
        except PiProcessError:
            pass

    @staticmethod
    def _request_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _emit(emit, kind: str, **payload: Any) -> None:
        emit({"eventType": kind, "payload": payload})

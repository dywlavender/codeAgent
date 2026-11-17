"""Pi-backed runtime for the unified online query path."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from business_code_agent.query.capabilities import (PI_TOOL_NAMES,
                                                    prepare_pi_capabilities)
from business_code_agent.query.context import RunContext
from business_code_agent.query.profile import AgentProfile
from business_code_agent.query.runtime import RuntimeErrorBase, RuntimeResult

from .events import PiEventAdapter
from .models import read_model_catalog
from .process import PiProcess, PiProcessError
from .prompts import PROMPT_VARIANTS, system_prompt as build_system_prompt
from .sessions import (prepare_session_root, resolve_session_file,
                       session_root as project_session_root, validate_session_file)


SEARCH_EXTENSION = Path(__file__).with_name("extension") / "codeagent_search.js"
AST_EXTENSION = Path(__file__).with_name("extension") / "codeagent_ast.js"
SEARCH_EXTENSION_COMMAND = "codeagent-readonly-search"
AST_EXTENSION_COMMAND = "codeagent-ast-loaded"


class PiConfigurationError(RuntimeErrorBase):
    """Pi or its configured model is not ready for this run."""


class _AstExtensionUnavailable(Exception):
    """The optional AST tools could not be loaded before prompting Pi."""


class PiAgentRuntime:
    """One Pi RPC process per question, with Pi-owned session state."""

    runtime_name = "PI"

    def __init__(self, *, profile: AgentProfile, environment: Mapping[str, str] | None = None,
                 command: str | list[str] | tuple[str, ...] | None = None,
                 model: str | None = None, timeout: float | None = None,
                 session_root: str | Path | None = None, thinking: str | None = None,
                 prompt_variant: str = "baseline"):
        self.profile = profile
        if prompt_variant not in PROMPT_VARIANTS:
            raise PiConfigurationError(f"未知的 Pi 提示词版本：{prompt_variant}")
        self.prompt_variant = prompt_variant
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
            **({"promptVariant": self.prompt_variant} if self.prompt_variant != "baseline" else {}),
        }

    def with_model(self, model: str | None) -> "PiAgentRuntime":
        """Return a runtime bound to one selected model without mutating config."""
        selected = str(model or "").strip()
        if not selected or selected == self.model:
            return self
        clone = PiAgentRuntime(
            profile=self.profile, environment=self.environment, command=self.command,
            model=selected, timeout=self.timeout, session_root=self.session_root,
            thinking=self.thinking, prompt_variant=self.prompt_variant)
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
            cancel_check=None, call_callback=None, prepared_callback=None,
            prompt_callback=None, mode: str | None = None) -> RuntimeResult:
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
                              if source.directory is not None)
        adapter = PiEventAdapter(emit, workspace=context.workspace,
                                 allowed_roots=allowed_roots,
                                 call_callback=call_callback)
        process_environment = {
            **self.environment,
            "CODEAGENT_PI_WORKSPACE": str(Path(context.workspace).resolve()),
            "CODEAGENT_PI_PROMPT_VARIANT": self.prompt_variant,
            "CODEAGENT_PI_ROOTS": json.dumps(
                [str(Path(context.workspace).resolve()),
                 *(str(Path(root).resolve()) for root in allowed_roots)],
                ensure_ascii=False),
            "CODEAGENT_PI_MOUNTS": json.dumps(
                {str(source.path): str(Path(source.directory).resolve())
                 for source in context.materials
                 if source.directory is not None},
                ensure_ascii=False),
        }
        cancelled = False
        timed_out = False
        session_file = existing
        process = None
        prompt_capture = TemporaryDirectory(prefix="pi-prompt-") if prompt_callback else None
        snapshot = None
        if prompt_capture:
            capture_path = Path(prompt_capture.name) / "observed.jsonl"
            process_environment["CODEAGENT_PI_PROMPT_TRACE"] = str(capture_path)
        try:
            try:
                process, state_data, actual_model, prompt = self._start_checked(
                    root, existing, context, prepared, mode, adapter, process_environment)
            except _AstExtensionUnavailable as exc:
                prepared = prepared.without_pi_ast(str(exc))
                fallback_session = prepared_callback(prepared) if prepared_callback else None
                session_file = resolve_session_file(root, fallback_session)
                self._emit(emit, "status", phase="ast_unavailable", capability="ast",
                           label=f"{exc}，本轮继续使用文件工具")
                process, state_data, actual_model, prompt = self._start_checked(
                    root, session_file, context, prepared, mode, adapter, process_environment)
            else:
                if prepared_callback:
                    prepared_callback(prepared)
            system_prompt = prompt["text"]
            if prompt_callback:
                snapshot = {**prompt, "sessionResumed": session_file is not None,
                            "enabledTools": list(prepared.pi_tool_names), "observed": []}
                prompt_callback(snapshot)
            session_file = validate_session_file(
                root, state_data.get("sessionFile"), require_exists=False)

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
            if process is not None:
                process.close()
            if prompt_capture:
                try:
                    if snapshot and capture_path.is_file():
                        try:
                            observed = [json.loads(line) for line in capture_path.read_text(encoding="utf-8").splitlines()]
                        except (OSError, ValueError):
                            observed = []
                        if observed:
                            prompt_callback({**snapshot, "observed": observed})
                finally:
                    prompt_capture.cleanup()

        usage = adapter.usage()
        usage["systemPromptSha256"] = sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
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

    def _start_checked(self, root, existing, context, prepared, mode, adapter, environment):
        env = dict(environment)
        if prepared.ast_reader:
            if not AST_EXTENSION.is_file():
                raise _AstExtensionUnavailable("Pi AST 扩展文件缺失")
            reader = prepared.ast_reader
            material = reader.material
            env.update({
                "CODEAGENT_PI_AST_SCOPE": json.dumps({
                    "versionId": reader.version_id, "projectId": reader.project_id,
                    "astRoot": str(material.root),
                    "projectConfig": str(material.project_config) if material.project_config else None,
                    "repositories": [{"repositoryId": repo.repository_id, "root": str(repo.root), "mount": repo.mount}
                                     for repo in reader.repositories],
                }, ensure_ascii=False),
                "CODEAGENT_PI_AST_PYTHON": sys.executable,
                "CODEAGENT_PI_AST_IMPORT_ROOT": str(Path(__file__).resolve().parents[4]),
            })
        prompt = self._system_prompt(context, prepared, mode=mode)
        command = self._command(root, existing, prompt["text"], prepared=prepared)
        process = PiProcess(command, cwd=context.workspace, environment=env)
        try:
            process.start()
            state = self._request(process, {"type": "get_state"}, adapter=adapter,
                                  timeout=min(30.0, max(5.0, self.timeout)))
            if not state.get("success"):
                raise PiConfigurationError(
                    f"Pi 状态检查失败：{state.get('error') or 'unknown error'}")
            state_data = state.get("data") if isinstance(state.get("data"), Mapping) else {}
            actual_model = self._verify_model(state_data.get("model"))
            names = self._verify_search_extension(process, adapter)
            if prepared.ast_reader and AST_EXTENSION_COMMAND not in names:
                raise _AstExtensionUnavailable("Pi AST 扩展未完成加载")
        except Exception as exc:
            process.close()
            if (isinstance(exc, PiProcessError) and prepared.ast_reader
                    and AST_EXTENSION.name in process.diagnostics()):
                raise _AstExtensionUnavailable("Pi AST 扩展启动失败") from exc
            raise
        return process, state_data, actual_model, prompt

    def _command(self, root: Path, existing: Path | None, system_prompt: str, *, prepared=None) -> list[str]:
        command = [
            *self.command,
            "--mode", "rpc",
            "--session-dir", str(root),
            "--no-extensions", "-e", "builtin:codemode",
            "-e", str(SEARCH_EXTENSION),
            "--no-context-files", "--no-mcp", "--no-skills",
            "--no-prompt-templates", "--no-themes", "--no-approve",
            "--system-prompt", system_prompt,
            "--tools", ",".join(prepared.pi_tool_names if prepared else PI_TOOL_NAMES),
        ]
        if prepared and prepared.ast_reader:
            command.extend(["-e", str(AST_EXTENSION)])
        if existing is not None:
            command.extend(["--session", str(existing)])
        else:
            command.extend(["--name", "CodeAgent"])
        if self.model:
            command.extend(["--model", self.model])
        if self.thinking:
            command.extend(["--thinking", self.thinking])
        return command

    def _system_prompt(self, context: RunContext, prepared, *, mode: str | None) -> dict:
        return build_system_prompt(context, prepared, mode, self.prompt_variant)

    def _verify_search_extension(self, process: PiProcess,
                                 adapter: PiEventAdapter) -> set[str]:
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
        return names

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

"""Project-scoped persistence and orchestration for interchangeable Agents.

The runtime owns model/tool execution and context. This service prepares a
read-only workspace, enforces conversation boundaries and records the exchange.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from business_code_agent.codeintel.ast.material import AstMaterial
from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.capabilities import (prepare_capabilities,
                                                    prepare_pi_capabilities)
from business_code_agent.query.context import RunContext

from .materials import MATERIAL_KINDS, MaterialManifest
from .profile import execution_snapshot, resolve_online_profile
from .runtime import RequiredToolUnavailable, RunPreparation, RuntimeErrorBase, normalize_runtime_result
from .traces import RunTraceArchive
from .workspace import MODE_DESCRIPTIONS, Workspace, WorkspaceError, WorkspaceManager, _safe_name


logger = logging.getLogger(__name__)

QUERY_MODES = ("combined", "none", "backbone", "ast")
QUERY_MODE_LABELS = {
    "basic": "基础对照", "tracked": "跟踪调查",
    "combined": "组合资料", "none": "无主干", "backbone": "有主干", "ast": "AST",
    "core": "零索引核心",
}
_TERMINAL_STATUS_LABELS = {"cancelled": "已停止", "incomplete": "调查未完成（达到显式限制）"}
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9_.:/@+-]{1,200}")


def _final_status(status: str) -> str:
    return status if status in {"completed", *_TERMINAL_STATUS_LABELS} else "completed"


class QueryRuntimeError(RuntimeErrorBase):
    """A query could not be completed by the configured runtime."""


class QueryConfigurationError(QueryRuntimeError):
    """The selected profile cannot run with the current prerequisites."""


class QueryBusyError(ValueError):
    def __init__(self, run_id: str):
        super().__init__("当前会话仍有任务在运行，请等待完成或先停止该任务。")
        self.run_id = run_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default
    return parsed


def _session_config(execution, manifest, *, core=False):
    value = {
        "profileId": execution["profileId"],
        "investigation": execution["investigation"],
        "answerMode": execution["answerMode"],
        "model": execution["model"],
        "tools": [{key: tool[key] for key in ("capability", "policy", "status", "names", "version")}
                  for tool in execution["tools"]],
        "materials": (
            [[source.kind, source.path, source.status, source.version]
             for source in manifest.sources]
            if core else
            [[source.kind, source.path, str(source.directory), source.version]
             for source in manifest.readable()]
        ),
        "settings": execution["settings"],
    }
    if core:
        value["repositoryIds"] = [source.repository_id for source in manifest.sources
                                  if source.kind == "source"]
    return value


class QueryService:
    """Glue between SQLite, a project workspace and an AgentRuntime."""

    def __init__(
        self,
        db,
        *,
        db_path: str | None = None,
        project_config: str | Path | None = None,
        runtime=None,
        workspace_manager: WorkspaceManager | None = None,
        workspace_root: str | Path | None = None,
        business_context_root: str | Path | None = None,
        code_map_root: str | Path | None = None,
        ast_data_root: str | Path | None = None,
        baseline_root: str | Path | None = None,
        requirements_root: str | Path | None = None,
        project_id: str | None = None,
        project_scoped: bool = False,
        retrieval_root: str | Path | None = None,
        trace_root: str | Path | None = None,
        ast_material_factory=None,
        project_context: ProjectContext | None = None,
    ):
        self.ast_material_factory = ast_material_factory
        self.db = db
        self.db_path = db_path
        self.project_config = str(project_config) if project_config else None
        self.ast_data_root = Path(ast_data_root).expanduser().resolve() if ast_data_root else None
        self.project_id = str(project_id or "").strip() or None
        self.project_scoped = bool(project_scoped and self.project_id)
        self.workspace_manager = workspace_manager or WorkspaceManager(
            db,
            project_config=project_config,
            workspace_root=workspace_root or self._default_workspace_root(project_config, db_path),
            business_context_root=business_context_root,
            code_map_root=code_map_root,
            baseline_root=baseline_root,
            requirements_root=requirements_root,
        )
        if not self.ast_data_root and not self.project_config:
            self.ast_data_root = self.workspace_manager.root / "ast"
        self.index_root = retrieval_root or (Path(db_path).parent / 'retrieval-index' if db_path and db_path != ':memory:' else None)
        self.runtime = runtime
        self.trace_root = Path(trace_root or (Path(db_path).parent / "query-traces" if db_path and db_path != ":memory:"
                                            else self.workspace_manager.root / "query-traces"))
        self.project_context = project_context or self._derive_project_context()

    def _derive_project_context(self) -> ProjectContext:
        """Reuse ProjectContext when the composition root did not inject it."""
        if self.project_scoped and self.project_id and self.db_path:
            database = Path(self.db_path).expanduser().resolve()
            config = Path(self.project_config).expanduser().resolve() if self.project_config else None
            return ProjectContext(self.project_id, self.workspace_manager.project_name, config,
                                  database.parent, database, True)
        database = self.db_path if self.db_path and self.db_path != ":memory:" else str(
            Path.cwd() / ".data" / "knowledge.db")
        return ProjectContext.legacy(database, self.project_config)

    @staticmethod
    def _default_workspace_root(
        project_config: str | Path | None,
        db_path: str | None,
    ) -> str | None:
        # With a project config WorkspaceManager already places workspaces next
        # to that project. For a standalone database keep generated files next
        # to the database instead of unexpectedly writing into the source tree.
        if project_config or not db_path or db_path == ":memory:":
            return None
        path = Path(db_path).expanduser()
        if path.parent == Path("."):
            return None
        return str(path.parent / "agent-workspaces")

    def query(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        history=(),
        event_callback=None,
        run_callback=None,
        scope=None,
        mode: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch one question to the selected runtime."""
        if not str(question or "").strip():
            raise ValueError("question is required")
        runtime = self.runtime if self.runtime is not None else _default_runtime(
            index_root=self.index_root, project_id=self.project_id,
            project_data_root=self.project_context.data_root)
        profile = getattr(runtime, "profile", None)
        if profile is not None and profile.investigation == "pi":
            return self._query_pi(
                question, conversation_id=conversation_id, event_callback=event_callback,
                run_callback=run_callback, scope=scope, mode=mode, model=model,
                runtime=runtime,
            )
        if model:
            raise ValueError("model 仅支持 Pi 运行时")
        if profile is not None and profile.investigation == "direct":
            return self._query_core(
                question, conversation_id=conversation_id, event_callback=event_callback,
                run_callback=run_callback, scope=scope, mode=mode, runtime=runtime,
            )
        return self._query_legacy(
            question, conversation_id=conversation_id, history=history,
            event_callback=event_callback, run_callback=run_callback,
            scope=scope, mode=mode, runtime=runtime,
        )

    def list_pi_models(self) -> dict[str, Any]:
        """Return Pi's available models without exposing provider credentials."""
        runtime = self.runtime if self.runtime is not None else _default_runtime(
            index_root=self.index_root, project_id=self.project_id,
            project_data_root=self.project_context.data_root)
        catalog = getattr(runtime, "models_catalog", None)
        if catalog is None:
            raise QueryConfigurationError("当前运行时没有提供模型列表")
        try:
            return catalog()
        except QueryConfigurationError:
            raise
        except RuntimeErrorBase as exc:
            raise QueryConfigurationError(str(exc)) from exc

    def _query_legacy(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        history=(),
        event_callback=None,
        run_callback=None,
        scope=None,
        mode: str | None = None,
        runtime=None,
    ) -> dict[str, Any]:
        """Run one question and persist the complete runtime exchange.

        ``history`` remains an accepted argument for old clients. The selected
        runtime restores its own session state.

        ``scope`` optionally narrows the investigation to configured systems
        and repositories: ``{"systemIds": [...], "repositoryIds": [...]}``.
        ``None``/empty means every mounted source stays authorized.
        """
        del history
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")

        logger.info("查询请求: conversation=%s question_characters=%s", conversation_id or "new", len(question))
        effective_mode = self._normalize_mode(mode)
        effective_scope = self._normalize_scope(scope)
        self._validate_owned_id(conversation_id, "conversation")
        runtime = runtime or self.runtime or _default_runtime(
            index_root=self.index_root, project_id=self.project_id,
            project_data_root=self.project_context.data_root)
        runtime_name = str(getattr(runtime, "runtime_name", "CLAUDE_CODE"))
        profile = getattr(runtime, "profile", None)
        workspace = self._workspace_for_mode(effective_mode)
        ast_version_id = workspace.ast_version_id
        logger.info("查询工作区就绪: workspace=%s path=%s", workspace.id, workspace.path)
        repositories = {_safe_name(repo) for repo in effective_scope["repositoryIds"]} if effective_scope else None
        manifest = MaterialManifest.from_workspace(
            workspace.path, repositories, allowed=profile.materials if profile else MATERIAL_KINDS,
            mode=effective_mode, ast_version=ast_version_id, ast_status=workspace.ast_status,
        )
        preparation = (runtime.prepare(workspace=workspace.path, materials=manifest, project_id=self.project_id or workspace.id)
                       if profile else None)
        settings = runtime.execution_settings() if profile else {}
        execution = execution_snapshot(profile, manifest, preparation.tool_statuses if preparation else (), settings=settings)
        session_config = _session_config(execution, manifest) if profile else None
        execution_json = _json(execution)
        run_id = self._new_id("RUN")
        started_at = _now()
        started_clock = time.monotonic()
        # Hold the write lock across the active-run check and insertion so
        # another tab/request cannot start the same session concurrently.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            conversation = self._get_or_create_conversation(conversation_id, workspace, effective_mode, runtime_name)
            stored_scope = _load_json(conversation.get("scope_json"), None)
            stored_mode = str(conversation.get("mode") or "backbone")
            stored_ast_version_id = conversation.get("ast_version_id")
            version_changed = ast_version_id != stored_ast_version_id
            if (effective_scope != stored_scope or effective_mode != stored_mode or version_changed
                    or conversation.get("runtime") != runtime_name
                    or session_config != _load_json(conversation.get("session_config_json"), None)):
                self.db.execute(
                    """UPDATE query_conversation SET scope_json=?,mode=?,ast_version_id=?,
                       runtime_session_id=NULL,session_config_json=?,updated_at=? WHERE id=?""",
                    (_json(effective_scope) if effective_scope else None, effective_mode, ast_version_id,
                     _json(session_config) if session_config is not None else None, _now(), conversation["id"]),
                )
                conversation["runtime_session_id"] = None
            active = self.db.execute(
                "SELECT id FROM query_run WHERE conversation_id=? AND status IN ('running','cancelling') LIMIT 1",
                (conversation["id"],),
            ).fetchone()
            if active:
                raise QueryBusyError(active["id"])
            self.db.execute(
                """INSERT INTO query_run
                   (id,conversation_id,runtime,runtime_session_id,question,status,answer,
                    error,usage_json,scope_json,mode,ast_version_id,execution_json,started_at,completed_at,duration_ms)
                   VALUES (?,?,?,?,?,'running','',NULL,'{}',?,?,?,?,?,NULL,0)""",
                (run_id, conversation["id"], runtime_name, conversation["runtime_session_id"], question,
                 _json(effective_scope) if effective_scope else None, effective_mode, ast_version_id, execution_json, started_at),
            )
            self._save_message(conversation["id"], run_id, "user", question, started_at)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        logger.info("查询开始: run=%s conversation=%s runtime=%s session=%s",
                    run_id, conversation["id"], runtime_name, conversation["runtime_session_id"] or "new")
        emitted: list[dict[str, Any]] = []
        client_connected = True
        archive = RunTraceArchive(self.trace_root, run_id, self.project_id)
        archive.start({"runId": run_id, "conversationId": conversation["id"], "question": question,
                       "runtime": runtime_name, "mode": effective_mode, "scope": effective_scope,
                       "astVersionId": ast_version_id, "astStatus": workspace.ast_status,
                       "materials": [{"label": label, "path": str(path)}
                                     for label, path in manifest.labelled_roots()],
                       "execution": execution,
                       "startedAt": started_at, "status": "running"}, workspace.path)

        def on_event(event: Mapping[str, Any]) -> None:
            nonlocal client_connected
            normalized = dict(event)
            emitted.append(normalized)
            self._save_event(run_id, normalized, len(emitted))
            self.db.commit()
            archive.event({**normalized, "at": normalized.get("at", _now())})
            if event_callback and client_connected:
                try:
                    event_callback(normalized)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    client_connected = False
                    # A disconnected SSE client must not turn a successful
                    # runtime invocation into a failed persisted run.
                    logger.warning("查询客户端已断开: run=%s；后台继续保存结果", run_id)

        try:
            if run_callback:
                run_callback({"runId": run_id, "conversationId": conversation["id"],
                              "mode": effective_mode, "modeLabel": QUERY_MODE_LABELS[effective_mode],
                              "astVersionId": ast_version_id, "execution": execution})
            if preparation is not None:
                preparation.require_tools()
            raw_result = (runtime.ask_prepared if hasattr(runtime, "ask_prepared") else runtime.ask)(
                question,
                workspace=str(workspace.path),
                session_id=conversation["runtime_session_id"],
                event_callback=on_event,
                cancel_check=lambda: self.db.execute("SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()[0] == "cancelling",
                repositories=repositories,
                **({"preparation": preparation} if hasattr(runtime, "ask_prepared") else {}),
            )
            result = normalize_runtime_result(raw_result)
        except Exception as exc:
            duration = round((time.monotonic() - started_clock) * 1000, 3)
            logger.error("查询失败: run=%s conversation=%s duration_ms=%s error_type=%s",
                         run_id, conversation["id"], duration, type(exc).__name__)
            error_text = str(exc) or exc.__class__.__name__
            on_event(
                {
                    "eventType": "error",
                    "subtype": exc.__class__.__name__,
                    "payload": {"error": error_text},
                },
            )
            archive.finish(status="failed", error=error_text, completedAt=_now(), durationMs=duration)
            self.db.execute(
                """UPDATE query_run SET status='failed',error=?,completed_at=?,duration_ms=?
                   WHERE id=?""",
                (error_text, _now(), duration, run_id),
            )
            self.db.commit()
            if isinstance(exc, QueryRuntimeError):
                raise
            if isinstance(exc, RequiredToolUnavailable):
                raise QueryConfigurationError(error_text) from exc
            raise QueryRuntimeError(error_text) from exc

        # A custom runtime may return events without invoking the callback.
        for event in result.events:
            if event not in emitted:
                on_event(event)

        session_id = result.runtime_session_id or conversation["runtime_session_id"]
        completed_at = _now()
        final_status = _final_status(result.status)
        terminal_label = _TERMINAL_STATUS_LABELS.get(final_status)
        if terminal_label and not any(
                (event.get("payload") or {}).get("phase") == final_status for event in emitted):
            on_event({"sequence": len(emitted) + 1, "eventType": "status",
                      "payload": {"phase": final_status, "label": terminal_label}})
        duration = round((time.monotonic() - started_clock) * 1000, 3)
        usage = {**result.usage, "traceArchive": str(archive.path)}
        archive.finish(status=final_status, answer=result.answer, usage=usage,
                       completedAt=completed_at, durationMs=duration)
        self.db.execute(
            """UPDATE query_run SET status=?,runtime_session_id=?,answer=?,
               usage_json=?,completed_at=?,duration_ms=? WHERE id=?""",
            (final_status, session_id, result.answer, _json(usage), completed_at, duration, run_id),
        )
        if result.answer:
            self._save_message(conversation["id"], run_id, "assistant", result.answer, completed_at)
        self.db.execute(
            """UPDATE query_conversation SET runtime=?,runtime_session_id=?,workspace_id=?,ast_version_id=?,updated_at=?
               WHERE id=?""",
            (runtime_name, session_id, workspace.id, ast_version_id, completed_at, conversation["id"]),
        )
        self.db.commit()
        logger.info("查询结束: run=%s status=%s conversation=%s session=%s duration_ms=%s events=%s answer_characters=%s",
                    run_id, final_status, conversation["id"], session_id, duration, len(emitted), len(result.answer))
        return {
            "runId": run_id,
            "conversationId": conversation["id"],
            "projectId": self.project_id,
            "runtime": runtime_name,
            "sessionId": session_id,
            "workspaceId": workspace.id,
            "mode": effective_mode,
            "modeLabel": QUERY_MODE_LABELS[effective_mode],
            "astVersionId": ast_version_id,
            "execution": execution,
            "status": final_status,
            "scope": effective_scope,
            "answer": result.answer,
            "events": emitted,
            "usage": usage,
        }

    @staticmethod
    def _normalize_mode(mode: str | None, *, core: bool = False) -> str:
        if core:
            value = str(mode or "core").strip().casefold()
            if value in {"", "core", "combined"}:
                return "core"
            raise ValueError(
                "core-v1 只使用源码与需求，不使用旧资料模式；请移除 mode 参数"
            )
        value = str(mode or "combined").strip().casefold()
        aliases = {"no_backbone": "none", "without": "none", "full": "backbone", "overview": "backbone"}
        value = aliases.get(value, value)
        if value not in QUERY_MODES:
            raise ValueError("mode 必须是 combined、none、backbone 或 ast")
        return value

    def _query_core(self, question, *, conversation_id, event_callback, run_callback,
                    scope, mode, runtime) -> dict[str, Any]:
        """Run core-v1: create the run first, then prepare and call the model."""
        profile = runtime.profile
        effective_mode = self._normalize_mode(mode, core=True)
        effective_scope = self._normalize_scope(scope)
        self._validate_owned_id(conversation_id, "conversation")
        runtime_name = str(getattr(runtime, "runtime_name", "LANGCHAIN_DIRECT"))
        repositories = (sorted(set(effective_scope["repositoryIds"]))
                        if effective_scope is not None else None)
        logger.info("core 查询请求: conversation=%s question_characters=%s",
                    conversation_id or "new", len(question))
        run_id = self._new_id("RUN")
        started_at = _now()
        started_clock = time.monotonic()
        workspace_id = self.workspace_manager.project_id
        self.db.execute("BEGIN IMMEDIATE")
        try:
            conversation = self._get_or_create_conversation(
                conversation_id, workspace_id, effective_mode, runtime_name)
            active = self.db.execute(
                "SELECT id FROM query_run WHERE conversation_id=? AND status IN ('running','cancelling') LIMIT 1",
                (conversation["id"],),
            ).fetchone()
            if active:
                raise QueryBusyError(active["id"])
            self.db.execute(
                """INSERT INTO query_run
                   (id,conversation_id,runtime,runtime_session_id,question,status,answer,
                    error,usage_json,scope_json,mode,ast_version_id,execution_json,started_at,completed_at,duration_ms)
                   VALUES (?,?,?,?,?,'running','',NULL,'{}',?,?,NULL,'{}',?,NULL,0)""",
                (run_id, conversation["id"], runtime_name, conversation["runtime_session_id"],
                 question, _json(effective_scope) if effective_scope else None,
                 effective_mode, started_at),
            )
            self._save_message(conversation["id"], run_id, "user", question, started_at)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        settings = runtime.execution_settings()
        expected_repositories = (repositories if repositories is not None else sorted(
            repository_id for repository_id, _ in self.workspace_manager.repository_sources()))
        base_execution = execution_snapshot(profile, MaterialManifest(()), (), settings=settings)
        base_execution["repositoryIds"] = list(expected_repositories)
        emitted: list[dict[str, Any]] = []
        client_connected = True
        archive = RunTraceArchive(self.trace_root, run_id, self.project_id)
        archive.start({"runId": run_id, "conversationId": conversation["id"], "question": question,
                       "runtime": runtime_name, "mode": effective_mode, "scope": effective_scope,
                       "astVersionId": None, "astStatus": None, "materials": [],
                       "execution": base_execution, "startedAt": started_at, "status": "running"},
                      self.workspace_manager.root)

        def on_event(event: Mapping[str, Any]) -> None:
            nonlocal client_connected
            normalized = dict(event)
            emitted.append(normalized)
            self._save_event(run_id, normalized, len(emitted))
            self.db.commit()
            archive.event({**normalized, "at": normalized.get("at", _now())})
            if event_callback and client_connected:
                try:
                    event_callback(normalized)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    client_connected = False
                    logger.warning("core 查询客户端已断开: run=%s；后台继续保存结果", run_id)

        workspace = None
        execution = base_execution
        callback_sent = False
        try:
            include_business_context = profile.business_context != "disabled"
            workspace = self.workspace_manager.prepare_core(
                run_id=run_id, repository_ids=repositories, include_requirements=True,
                include_business_context=include_business_context)
            manifest = MaterialManifest.for_core(
                workspace.path, workspace.repository_mounts,
                include_requirements=True, include_business_context=include_business_context)
            missing = manifest.missing_required()
            if missing:
                raise WorkspaceError("授权仓库不可用，未调用模型：" + "、".join(
                    source.repository_id or source.label for source in missing))
            context = RunContext(
                run_id=run_id, project=self.project_context, profile=profile,
                repository_ids=workspace.repository_ids, workspace=workspace.path,
                materials=manifest.sources, session_id=None,
            )
            prepared = prepare_capabilities(context)
            preparation = RunPreparation(manifest, prepared.statuses)
            execution = execution_snapshot(profile, manifest, prepared.statuses, settings=settings)
            execution["repositoryIds"] = list(workspace.repository_ids)
            session_config = _session_config(execution, manifest, core=True)
            archive.workspace = workspace.path.resolve()
            archive.update_run(execution=execution, mode=effective_mode, astVersionId=None,
                               workspace=str(workspace.path),
                               materials=[{"label": label, "path": str(path)}
                                          for label, path in manifest.labelled_roots()])
            self._apply_core_run(conversation, run_id, effective_scope, effective_mode,
                                 execution, session_config, runtime_name, workspace_id)
            if run_callback:
                callback_sent = True
                run_callback({"runId": run_id, "conversationId": conversation["id"],
                              "mode": effective_mode, "modeLabel": QUERY_MODE_LABELS[effective_mode],
                              "astVersionId": None, "execution": execution})
            preparation.require_tools()
            context = replace(context, session_id=conversation["runtime_session_id"])
            ask_kwargs = {
                "context": context, "prepared": prepared,
                "session_id": conversation["runtime_session_id"],
                "event_callback": on_event,
                "cancel_check": lambda: self.db.execute(
                    "SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()[0] == "cancelling",
            }
            from business_code_agent.query.agent.direct import DirectAgentRuntime
            if isinstance(runtime, DirectAgentRuntime):
                ask_kwargs["call_callback"] = archive.record_call
            raw_result = runtime.ask(question, **ask_kwargs)
            result = normalize_runtime_result(raw_result)
        except Exception as exc:
            duration = round((time.monotonic() - started_clock) * 1000, 3)
            error_text = str(exc) or exc.__class__.__name__
            logger.error("core 查询失败: run=%s conversation=%s duration_ms=%s error_type=%s",
                         run_id, conversation["id"], duration, type(exc).__name__)
            failure_execution = {**execution, "phase": "failed", "error": error_text}
            failure_execution.setdefault("repositoryIds", list(expected_repositories))
            archive.update_run(execution=failure_execution, mode=effective_mode,
                               astVersionId=None,
                               workspace=str(workspace.path) if workspace else str(self.workspace_manager.root))
            if not callback_sent and run_callback:
                run_callback({"runId": run_id, "conversationId": conversation["id"],
                              "mode": effective_mode, "modeLabel": QUERY_MODE_LABELS[effective_mode],
                              "astVersionId": None, "execution": failure_execution})
            on_event({"eventType": "error", "subtype": exc.__class__.__name__,
                      "payload": {"error": error_text}})
            archive.finish(status="failed", error=error_text, completedAt=_now(), durationMs=duration)
            self.db.execute(
                """UPDATE query_run SET status='failed',error=?,execution_json=?,completed_at=?,duration_ms=?
                   WHERE id=?""",
                (error_text, _json(failure_execution), _now(), duration, run_id),
            )
            self.db.commit()
            if isinstance(exc, QueryRuntimeError):
                raise
            if isinstance(exc, (RequiredToolUnavailable, WorkspaceError)):
                raise QueryConfigurationError(error_text) from exc
            raise QueryRuntimeError(error_text) from exc

        for event in result.events:
            if event not in emitted:
                on_event(event)
        session_id = result.runtime_session_id or conversation["runtime_session_id"]
        completed_at = _now()
        final_status = _final_status(result.status)
        terminal_label = _TERMINAL_STATUS_LABELS.get(final_status)
        if terminal_label and not any(
                (event.get("payload") or {}).get("phase") == final_status for event in emitted):
            on_event({"sequence": len(emitted) + 1, "eventType": "status",
                      "payload": {"phase": final_status, "label": terminal_label}})
        duration = round((time.monotonic() - started_clock) * 1000, 3)
        usage = {**result.usage, "traceArchive": str(archive.path)}
        archive.finish(status=final_status, answer=result.answer, usage=usage,
                       completedAt=completed_at, durationMs=duration)
        self.db.execute(
            """UPDATE query_run SET status=?,runtime_session_id=?,answer=?,usage_json=?,
               completed_at=?,duration_ms=? WHERE id=?""",
            (final_status, session_id, result.answer, _json(usage), completed_at, duration, run_id),
        )
        if result.answer:
            self._save_message(conversation["id"], run_id, "assistant", result.answer, completed_at)
        self.db.execute(
            """UPDATE query_conversation SET runtime=?,runtime_session_id=?,workspace_id=?,
               ast_version_id=NULL,updated_at=? WHERE id=?""",
            (runtime_name, session_id, workspace.id, completed_at, conversation["id"]),
        )
        self.db.commit()
        logger.info("core 查询结束: run=%s status=%s conversation=%s session=%s duration_ms=%s events=%s",
                    run_id, final_status, conversation["id"], session_id, duration, len(emitted))
        return {
            "runId": run_id,
            "conversationId": conversation["id"],
            "projectId": self.project_id,
            "runtime": runtime_name,
            "sessionId": session_id,
            "workspaceId": workspace.id,
            "mode": effective_mode,
            "modeLabel": QUERY_MODE_LABELS[effective_mode],
            "astVersionId": None,
            "execution": execution,
            "status": final_status,
            "scope": effective_scope,
            "answer": result.answer,
            "events": emitted,
            "usage": usage,
        }

    def _query_pi(self, question, *, conversation_id, event_callback, run_callback,
                  scope, mode, model, runtime) -> dict[str, Any]:
        """Run every online material mode through the single Pi agent loop."""
        from business_code_agent.query.agent.pi import PiAgentRuntime, PiConfigurationError

        profile = runtime.profile
        requested_model = str(model or "").strip()
        if requested_model and not _MODEL_PATTERN.fullmatch(requested_model):
            raise ValueError("model 格式不正确")
        if requested_model:
            bind_model = getattr(runtime, "with_model", None)
            if bind_model is None:
                raise ValueError("当前运行时不支持选择模型")
            runtime = bind_model(requested_model)
        effective_mode = self._normalize_mode(mode)
        effective_scope = self._normalize_scope(scope)
        self._validate_owned_id(conversation_id, "conversation")
        runtime_name = str(getattr(runtime, "runtime_name", "PI"))
        repositories = (sorted(set(effective_scope["repositoryIds"]))
                        if effective_scope is not None else None)
        logger.info("Pi 查询请求: conversation=%s question_characters=%s mode=%s model=%s",
                    conversation_id or "new", len(question), effective_mode,
                    requested_model or "default")
        run_id = self._new_id("RUN")
        started_at = _now()
        started_clock = time.monotonic()
        workspace_id = self.workspace_manager.project_id
        self.db.execute("BEGIN IMMEDIATE")
        try:
            conversation = self._get_or_create_conversation(
                conversation_id, workspace_id, effective_mode, runtime_name)
            active = self.db.execute(
                "SELECT id FROM query_run WHERE conversation_id=? AND status IN ('running','cancelling') LIMIT 1",
                (conversation["id"],),
            ).fetchone()
            if active:
                raise QueryBusyError(active["id"])
            self.db.execute(
                """INSERT INTO query_run
                   (id,conversation_id,runtime,runtime_session_id,question,status,answer,
                    error,usage_json,scope_json,mode,ast_version_id,execution_json,started_at,completed_at,duration_ms)
                   VALUES (?,?,?,?,?,'running','',NULL,'{}',?,?,NULL,'{}',?,NULL,0)""",
                (run_id, conversation["id"], runtime_name, conversation["runtime_session_id"],
                 question, _json(effective_scope) if effective_scope else None,
                 effective_mode, started_at),
            )
            self._save_message(conversation["id"], run_id, "user", question, started_at)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        settings = runtime.execution_settings()
        expected_repositories = (repositories if repositories is not None else sorted(
            repository_id for repository_id, _ in self.workspace_manager.repository_sources()))
        base_execution = execution_snapshot(profile, MaterialManifest(()), (), settings=settings)
        base_execution["repositoryIds"] = list(expected_repositories)
        emitted: list[dict[str, Any]] = []
        client_connected = True
        archive = RunTraceArchive(self.trace_root, run_id, self.project_id)
        archive.start({"runId": run_id, "conversationId": conversation["id"], "question": question,
                       "runtime": runtime_name, "mode": effective_mode, "scope": effective_scope,
                       "astVersionId": None, "astStatus": None, "materials": [],
                       "execution": base_execution, "startedAt": started_at, "status": "running"},
                      self.workspace_manager.root)

        def on_event(event: Mapping[str, Any]) -> None:
            nonlocal client_connected
            normalized = dict(event)
            emitted.append(normalized)
            self._save_event(run_id, normalized, len(emitted))
            self.db.commit()
            archive.event({**normalized, "at": normalized.get("at", _now())})
            if event_callback and client_connected:
                try:
                    event_callback(normalized)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    client_connected = False
                    logger.warning("Pi 查询客户端已断开: run=%s；后台继续保存结果", run_id)

        workspace = None
        execution = base_execution
        callback_sent = False
        try:
            selection = self._mode_selection(effective_mode)
            workspace = self.workspace_manager.prepare_pi(
                workspace_key=conversation["id"], mode=effective_mode,
                repository_ids=repositories, **selection)
            manifest = MaterialManifest.from_workspace(
                workspace.path, set(workspace.repository_ids) if workspace.repository_ids else None,
                allowed=profile.materials, mode=effective_mode,
                ast_version=workspace.ast_version_id, ast_status=workspace.ast_status,
            )
            missing = manifest.missing_required()
            if missing:
                raise WorkspaceError("授权仓库不可用，未调用模型：" + "、".join(
                    source.repository_id or source.label for source in missing))
            context = RunContext(
                run_id=run_id, project=self.project_context, profile=profile,
                repository_ids=workspace.repository_ids, workspace=workspace.path,
                materials=manifest.sources, session_id=None,
            )
            version = runtime.preflight()
            settings = runtime.execution_settings()
            prepared = prepare_pi_capabilities(context, version=version, mode=effective_mode)
            preparation = RunPreparation(manifest, prepared.statuses)
            execution = execution_snapshot(profile, manifest, prepared.statuses, settings=settings)
            execution["repositoryIds"] = list(workspace.repository_ids)
            execution["mode"] = effective_mode
            session_config = _session_config(execution, manifest, core=True)
            session_config["mode"] = effective_mode
            session_config["astVersionId"] = workspace.ast_version_id
            archive.workspace = workspace.path.resolve()
            archive.update_run(execution=execution, mode=effective_mode,
                               astVersionId=workspace.ast_version_id,
                               workspace=str(workspace.path),
                               materials=[{"label": label, "path": str(path)}
                                          for label, path in manifest.labelled_roots()])
            self._apply_pi_run(conversation, run_id, effective_scope, effective_mode,
                               workspace.ast_version_id, execution, session_config,
                               runtime_name, workspace_id)
            if run_callback:
                callback_sent = True
                run_callback({"runId": run_id, "conversationId": conversation["id"],
                              "mode": effective_mode, "modeLabel": QUERY_MODE_LABELS[effective_mode],
                              "astVersionId": workspace.ast_version_id, "execution": execution})
            preparation.require_tools()
            context = replace(context, session_id=conversation["runtime_session_id"])
            ask_kwargs = {
                "context": context, "prepared": prepared,
                "session_id": conversation["runtime_session_id"],
                "event_callback": on_event,
                "cancel_check": lambda: self.db.execute(
                    "SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()[0] == "cancelling",
                "mode": effective_mode,
            }
            if isinstance(runtime, PiAgentRuntime):
                ask_kwargs["call_callback"] = archive.record_call
            raw_result = runtime.ask(question, **ask_kwargs)
            result = normalize_runtime_result(raw_result)
            actual_model = result.usage.get("actualModel")
            if actual_model:
                execution = {
                    **execution,
                    "model": actual_model,
                    "settings": {**execution["settings"], "actualModel": actual_model},
                }
        except Exception as exc:
            duration = round((time.monotonic() - started_clock) * 1000, 3)
            error_text = str(exc) or exc.__class__.__name__
            logger.error("Pi 查询失败: run=%s conversation=%s duration_ms=%s error_type=%s",
                         run_id, conversation["id"], duration, type(exc).__name__)
            failure_execution = {**execution, "phase": "failed", "error": error_text}
            failure_execution.setdefault("repositoryIds", list(expected_repositories))
            archive.update_run(execution=failure_execution, mode=effective_mode,
                               astVersionId=workspace.ast_version_id if workspace else None,
                               workspace=str(workspace.path) if workspace else str(self.workspace_manager.root))
            if not callback_sent and run_callback:
                run_callback({"runId": run_id, "conversationId": conversation["id"],
                              "mode": effective_mode, "modeLabel": QUERY_MODE_LABELS[effective_mode],
                              "astVersionId": workspace.ast_version_id if workspace else None,
                              "execution": failure_execution})
            on_event({"eventType": "error", "subtype": exc.__class__.__name__,
                      "payload": {"error": error_text}})
            archive.finish(status="failed", error=error_text, completedAt=_now(), durationMs=duration)
            self.db.execute(
                """UPDATE query_run SET status='failed',error=?,execution_json=?,completed_at=?,duration_ms=?
                   WHERE id=?""",
                (error_text, _json(failure_execution), _now(), duration, run_id),
            )
            self.db.commit()
            if isinstance(exc, QueryRuntimeError):
                raise
            if isinstance(exc, (RequiredToolUnavailable, WorkspaceError, PiConfigurationError)):
                raise QueryConfigurationError(error_text) from exc
            raise QueryRuntimeError(error_text) from exc

        for event in result.events:
            if event not in emitted:
                on_event(event)
        session_id = result.runtime_session_id or conversation["runtime_session_id"]
        completed_at = _now()
        final_status = _final_status(result.status)
        terminal_label = _TERMINAL_STATUS_LABELS.get(final_status)
        if terminal_label and not any(
                (event.get("payload") or {}).get("phase") == final_status for event in emitted):
            on_event({"sequence": len(emitted) + 1, "eventType": "status",
                      "payload": {"phase": final_status, "label": terminal_label}})
        duration = round((time.monotonic() - started_clock) * 1000, 3)
        usage = {**result.usage, "traceArchive": str(archive.path)}
        archive.update_run(execution=execution)
        archive.finish(status=final_status, answer=result.answer, usage=usage,
                       completedAt=completed_at, durationMs=duration)
        self.db.execute(
            """UPDATE query_run SET status=?,runtime_session_id=?,answer=?,usage_json=?,
               execution_json=?,completed_at=?,duration_ms=? WHERE id=?""",
            (final_status, session_id, result.answer, _json(usage), _json(execution),
             completed_at, duration, run_id),
        )
        if result.answer:
            self._save_message(conversation["id"], run_id, "assistant", result.answer, completed_at)
        self.db.execute(
            """UPDATE query_conversation SET runtime=?,runtime_session_id=?,workspace_id=?,
               ast_version_id=?,updated_at=? WHERE id=?""",
            (runtime_name, session_id, workspace.id, workspace.ast_version_id,
             completed_at, conversation["id"]),
        )
        self.db.commit()
        logger.info("Pi 查询结束: run=%s status=%s conversation=%s session=%s duration_ms=%s events=%s",
                    run_id, final_status, conversation["id"], session_id, duration, len(emitted))
        return {
            "runId": run_id,
            "conversationId": conversation["id"],
            "projectId": self.project_id,
            "runtime": runtime_name,
            "sessionId": session_id,
            "workspaceId": workspace.id,
            "mode": effective_mode,
            "modeLabel": QUERY_MODE_LABELS[effective_mode],
            "astVersionId": workspace.ast_version_id,
            "execution": execution,
            "status": final_status,
            "scope": effective_scope,
            "answer": result.answer,
            "events": emitted,
            "usage": usage,
        }

    def _apply_pi_run(self, conversation, run_id, scope, mode, ast_version_id, execution,
                      session_config, runtime_name, workspace_id) -> None:
        stored_scope = _load_json(conversation.get("scope_json"), None)
        stored_mode = str(conversation.get("mode") or "backbone")
        changed = (
            scope != stored_scope
            or mode != stored_mode
            or ast_version_id != conversation.get("ast_version_id")
            or conversation.get("runtime") != runtime_name
            or session_config != _load_json(conversation.get("session_config_json"), None)
        )
        session_id = None if changed else conversation.get("runtime_session_id")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                """UPDATE query_conversation SET scope_json=?,mode=?,ast_version_id=?,
                   runtime=?,runtime_session_id=?,session_config_json=?,workspace_id=?,updated_at=?
                   WHERE id=?""",
                (_json(scope) if scope else None, mode, ast_version_id, runtime_name, session_id,
                 _json(session_config), workspace_id, _now(), conversation["id"]),
            )
            self.db.execute(
                "UPDATE query_run SET mode=?,ast_version_id=?,execution_json=?,runtime_session_id=? WHERE id=?",
                (mode, ast_version_id, _json(execution), session_id, run_id),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        conversation.update({
            "runtime": runtime_name, "runtime_session_id": session_id,
            "workspace_id": workspace_id, "mode": mode, "ast_version_id": ast_version_id,
            "scope_json": _json(scope) if scope else None,
            "session_config_json": _json(session_config),
        })

    def _apply_core_run(self, conversation, run_id, scope, mode, execution,
                        session_config, runtime_name, workspace_id) -> None:
        stored_scope = _load_json(conversation.get("scope_json"), None)
        stored_mode = str(conversation.get("mode") or "backbone")
        changed = (
            scope != stored_scope
            or mode != stored_mode
            or conversation.get("ast_version_id") is not None
            or conversation.get("runtime") != runtime_name
            or session_config != _load_json(conversation.get("session_config_json"), None)
        )
        session_id = None if changed else conversation.get("runtime_session_id")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                """UPDATE query_conversation SET scope_json=?,mode=?,ast_version_id=NULL,
                   runtime=?,runtime_session_id=?,session_config_json=?,workspace_id=?,updated_at=?
                   WHERE id=?""",
                (_json(scope) if scope else None, mode, runtime_name, session_id,
                 _json(session_config), workspace_id, _now(), conversation["id"]),
            )
            self.db.execute(
                "UPDATE query_run SET mode=?,ast_version_id=NULL,execution_json=?,runtime_session_id=? WHERE id=?",
                (mode, _json(execution), session_id, run_id),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        conversation.update({
            "runtime": runtime_name, "runtime_session_id": session_id,
            "workspace_id": workspace_id, "mode": mode, "ast_version_id": None,
            "scope_json": _json(scope) if scope else None,
            "session_config_json": _json(session_config),
        })

    def _mode_selection(self, mode: str) -> dict[str, Any]:
        business_context_source = None
        code_map_source = None
        description = None
        ast_version_id = None
        ast_source = None
        ast_status = None
        if mode in {"combined", "ast"}:
            factory = self.ast_material_factory or (
                lambda: AstMaterial(project_config=self.project_config, data_root=self.ast_data_root))
            ast = factory()
            ast_state = ast.status()
            ast_status = ast_state["status"]
            current = ast.version(ast_state.get("currentVersionId")) if ast_status == "available" else None
            if not current and ast_status == "available":
                ast_status = "missing"
            if not current and mode == "ast":
                raise ValueError("AST 资料尚未生成或已经待更新，请先在 AST 管理页手动生成")
            if current:
                ast_version_id, metadata, source = current
                if mode == "combined":
                    ast_source = source
                else:
                    code_map_source = source
                description = (
                    f"{MODE_DESCRIPTIONS[mode]} AST 生成时间：{metadata.get('generatedAt') or '未知'}。"
                )
            else:
                reason = {"missing": "未生成", "stale": "待更新", "generating": "正在生成", "failed": "生成失败"}.get(ast_status, "不可用")
                description = f"{MODE_DESCRIPTIONS[mode]} AST {reason}，本轮使用其余可用资料，不自动生成或更新。"
        elif mode == "none":
            description = MODE_DESCRIPTIONS["none"]
        else:
            description = MODE_DESCRIPTIONS["backbone"]
        return {
            "business_context_source": business_context_source,
            "code_map_source": code_map_source,
            "ast_source": ast_source,
            "ast_status": ast_status,
            "mode_description": description,
            "ast_version_id": ast_version_id,
        }

    def _workspace_for_mode(self, mode: str) -> Workspace:
        return self.workspace_manager.ensure(mode=mode, **self._mode_selection(mode))

    def _normalize_scope(self, scope: Any) -> dict[str, list[str]] | None:
        """校验并展开查询范围；返回 None 表示不限定（全部资料）。

        系统会展开成对应工程集合；repositoryIds 可以直接指定工程。任何未在
        项目配置中登记的 id 都直接拒绝，避免静默放大或缩小调查范围。
        """
        if scope in (None, "", {}):
            return None
        if not isinstance(scope, dict):
            raise ValueError("scope 必须是对象，形如 {systemIds: [], repositoryIds: []}")
        raw_systems = scope.get("systemIds") or []
        raw_repositories = scope.get("repositoryIds") or []
        if not isinstance(raw_systems, list) or not isinstance(raw_repositories, list):
            raise ValueError("scope.systemIds 和 scope.repositoryIds 必须是数组")
        system_ids = sorted({str(item).strip() for item in raw_systems if str(item).strip()})
        repository_ids = sorted({str(item).strip() for item in raw_repositories if str(item).strip()})
        if not system_ids and not repository_ids:
            return None

        config = self.workspace_manager.config or {}
        configured_systems = {str(item.get("id") or "").strip()
                              for item in config.get("systems") or [] if isinstance(item, dict)}
        configured_repositories = {str(item.get("id") or "").strip()
                                   for item in config.get("repositories") or [] if isinstance(item, dict)}
        application_pairs = [(str(item.get("systemId") or "").strip(), str(item.get("repositoryId") or "").strip())
                             for item in config.get("applications") or [] if isinstance(item, dict)]
        for system_id in system_ids:
            if system_id not in configured_systems:
                raise ValueError(f"scope 包含未配置的系统: {system_id}")
        for repository_id in repository_ids:
            if repository_id not in configured_repositories:
                raise ValueError(f"scope 包含未配置的工程: {repository_id}")
        effective = set(repository_ids)
        for system_id, repo_id in application_pairs:
            if system_id in system_ids and repo_id:
                effective.add(repo_id)
        return {"systemIds": system_ids, "repositoryIds": sorted(effective)}

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        self._validate_owned_id(run_id, "run")
        self.db.execute("UPDATE query_run SET status='cancelling' WHERE id=? AND status='running'", (run_id,))
        self.db.commit()
        row = self.db.execute("SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return {"runId": run_id, "status": row["status"]}

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._validate_owned_id(run_id, "run")
        row = self.db.execute("SELECT * FROM query_run WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        value = dict(row)
        events = [self._event_dict(item) for item in self.db.execute(
            "SELECT * FROM query_event WHERE run_id=? ORDER BY sequence", (run_id,)
        )]
        feedback = [dict(item) for item in self.db.execute(
            "SELECT id,rating,comment,created_at FROM query_feedback WHERE run_id=? ORDER BY created_at,id",
            (run_id,),
        )]
        conversation_id = value["conversation_id"]
        conversation = self.db.execute(
            "SELECT runtime,runtime_session_id,workspace_id,mode,ast_version_id FROM query_conversation WHERE id=?",
            (conversation_id,),
        ).fetchone()
        # A later combined turn may add AST to this conversation. Its version
        # must never be attributed to an older run that did not use AST.
        ast_version_id = value.get("ast_version_id")
        return {
            "id": value["id"],
            "runId": value["id"],
            "conversationId": conversation_id,
            "projectId": self.project_id,
            "runtime": value["runtime"],
            "sessionId": value["runtime_session_id"],
            "workspaceId": conversation["workspace_id"] if conversation else None,
            "mode": value.get("mode") or (conversation["mode"] if conversation else "backbone"),
            "modeLabel": QUERY_MODE_LABELS.get(value.get("mode") or (conversation["mode"] if conversation else "backbone"), "有主干"),
            "astVersionId": ast_version_id,
            "execution": _load_json(value.get("execution_json"), None),
            "question": value["question"],
            "status": value["status"],
            "scope": _load_json(value["scope_json"], None),
            "answer": value["answer"],
            "error": value["error"],
            "usage": _load_json(value["usage_json"], {}),
            "startedAt": value["started_at"],
            "completedAt": value["completed_at"],
            "durationMs": value["duration_ms"],
            "events": events,
            "feedback": feedback,
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._validate_owned_id(conversation_id, "conversation")
        if not self.db.execute("SELECT 1 FROM query_conversation WHERE id=?", (conversation_id,)).fetchone():
            raise KeyError(conversation_id)
        rows = self.db.execute(
            "SELECT id FROM query_run WHERE conversation_id=? ORDER BY started_at,id", (conversation_id,),
        ).fetchall()
        return {"conversationId": conversation_id, "projectId": self.project_id,
                "items": [self.get_run(row["id"]) for row in rows]}

    def get_trace(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        archive = RunTraceArchive(self.trace_root, run_id, self.project_id)
        value = archive.read(run)
        legacy = run["usage"].get("tracePath")
        if value.get("historical") and legacy:
            path = Path(legacy).resolve()
            if path.is_relative_to(self.workspace_manager.root.resolve()) and path.parent.name == ".langchain-traces" and path.is_file():
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                if snapshot.get("projectId") == self.project_id:
                    value.update({k: v for k, v in snapshot.items() if k not in {"projectId", "runId", "run", "events"}})
                    calls = snapshot.get("calls") or []
                    full = any(call.get("messages") or call.get("tools") for call in calls)
                    value.update(historical=False, modelCallRecordsAvailable=bool(calls),
                                 requestSnapshotAvailable=full,
                                 modelSnapshotAvailable=full,
                                 runtimeEvents=snapshot.get("events", []))
        return value

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        self._validate_owned_id(conversation_id, "conversation")
        if not self.db.execute("SELECT 1 FROM query_conversation WHERE id=?", (conversation_id,)).fetchone():
            raise KeyError(conversation_id)
        active = self.db.execute(
            "SELECT id FROM query_run WHERE conversation_id=? AND status IN ('running','cancelling') LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if active:
            raise QueryBusyError(active["id"])
        self.db.execute(
            "DELETE FROM query_event WHERE run_id IN (SELECT id FROM query_run WHERE conversation_id=?)",
            (conversation_id,),
        )
        self.db.execute(
            "DELETE FROM query_feedback WHERE run_id IN (SELECT id FROM query_run WHERE conversation_id=?)",
            (conversation_id,),
        )
        self.db.execute("DELETE FROM query_message WHERE conversation_id=?", (conversation_id,))
        self.db.execute("DELETE FROM query_run WHERE conversation_id=?", (conversation_id,))
        self.db.execute("DELETE FROM query_conversation WHERE id=?", (conversation_id,))
        self.db.commit()
        logger.info("会话删除: conversation=%s", conversation_id)
        return {"conversationId": conversation_id, "deleted": True}

    def list_conversations(self, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        args = []
        boundary = ""
        if cursor:
            try:
                position = json.loads(cursor)
                if not isinstance(position, list) or len(position) != 2 or not all(isinstance(item, str) for item in position):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("invalid conversation cursor") from None
            boundary = "AND (r.started_at,r.id) < (?,?)"
            args.extend(position)
        args.append(limit + 1)
        rows = self.db.execute(
            f"""SELECT r.id,r.conversation_id,r.question,r.status,r.started_at,c.workspace_id,c.scope_json,c.mode,
                       r.ast_version_id
                FROM query_run r JOIN query_conversation c ON c.id=r.conversation_id
                WHERE r.id=(SELECT latest.id FROM query_run latest
                            WHERE latest.conversation_id=r.conversation_id
                            ORDER BY latest.started_at DESC,latest.id DESC LIMIT 1)
                {boundary}
                ORDER BY r.started_at DESC,r.id DESC LIMIT ?""", args,
        ).fetchall()
        page = rows[:limit]
        return {
            "items": [{"id": row["id"], "runId": row["id"], "conversationId": row["conversation_id"],
                       "projectId": self.project_id,
                       "question": row["question"], "status": row["status"], "startedAt": row["started_at"],
                       "workspaceId": row["workspace_id"], "scope": _load_json(row["scope_json"], None),
                       "mode": row["mode"] or "backbone", "modeLabel": QUERY_MODE_LABELS.get(row["mode"] or "backbone", "有主干"),
                       "astVersionId": row["ast_version_id"]}
                      for row in page],
            "nextCursor": _json([page[-1]["started_at"], page[-1]["id"]]) if len(rows) > limit else None,
        }

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.db.execute(
            """SELECT id,conversation_id,runtime,runtime_session_id,question,status,answer,error,
                      mode,ast_version_id,execution_json,started_at,completed_at,duration_ms
                 FROM query_run ORDER BY started_at DESC,id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "runId": row["id"],
                "conversationId": row["conversation_id"],
                "projectId": self.project_id,
                "runtime": row["runtime"],
                "sessionId": row["runtime_session_id"],
                "question": row["question"],
                "status": row["status"],
                "mode": row["mode"] or "backbone",
                "modeLabel": QUERY_MODE_LABELS.get(row["mode"] or "backbone", "有主干"),
                "astVersionId": row["ast_version_id"],
                "execution": _load_json(row["execution_json"], None),
                "answer": row["answer"],
                "error": row["error"],
                "startedAt": row["started_at"],
                "completedAt": row["completed_at"],
                "durationMs": row["duration_ms"],
            }
            for row in rows
        ]

    def record_feedback(self, run_id: str, rating: str, comment: str = "") -> dict[str, Any]:
        self._validate_owned_id(run_id, "run")
        if not self.db.execute("SELECT 1 FROM query_run WHERE id=?", (run_id,)).fetchone():
            raise KeyError(run_id)
        rating = str(rating).strip().upper()
        if rating not in {"HELPFUL", "NOT_HELPFUL"}:
            raise ValueError("rating must be HELPFUL or NOT_HELPFUL")
        feedback_id = f"QFB-{uuid.uuid4().hex}"
        self.db.execute(
            "INSERT INTO query_feedback(id,run_id,rating,comment,created_at) VALUES (?,?,?,?,?)",
            (feedback_id, run_id, rating, str(comment or "")[:1000], _now()),
        )
        self.db.commit()
        return {"id": feedback_id, "runId": run_id, "rating": rating}

    def workspace_summary(self) -> dict[str, Any]:
        raw_repositories = [dict(row) for row in self.db.execute(
            "SELECT id,root_path,indexed_at FROM repository ORDER BY indexed_at DESC"
        )]
        repositories = [{
            "id": row["id"],
            "displayName": Path(row["root_path"]).name,
            "rootPath": row["root_path"],
            "indexedAt": row["indexed_at"],
        } for row in raw_repositories]
        applications = [dict(row) for row in self.db.execute(
            """SELECT a.id,a.name,a.repository_id repositoryId,a.source_root sourceRoot,
                      a.app_type type,a.language,a.framework,a.status,
                      s.id systemId,s.name systemName
                 FROM application a LEFT JOIN software_system s ON s.id=a.system_id
                WHERE a.status='ACTIVE' ORDER BY s.name,a.name"""
        )]
        project = self.workspace_manager.project_name
        runtime = self.runtime
        profile = getattr(runtime, "profile", None) if runtime is not None else None
        if runtime is None:
            try:
                profile = resolve_online_profile(dict(os.environ))
            except Exception:
                profile = None
        if profile is not None and profile.investigation == "direct":
            workspace_info = {
                "id": self.workspace_manager.project_id,
                "path": str(self.workspace_manager.root),
                "mode": "core",
            }
            sources = self.workspace_manager.core_source_summary(
                include_business_context=profile.business_context != "disabled")
        elif profile is not None and profile.investigation == "pi":
            workspace_info = {
                "id": self.workspace_manager.project_id,
                "path": str(self.workspace_manager.root / "pi"),
                "mode": "pi",
            }
            sources = self.workspace_manager.source_summary()
        else:
            sources = self.workspace_manager.source_summary()
            try:
                workspace = self._workspace_for_mode("combined")
                workspace_info = workspace.to_dict()
                sources = self.workspace_manager.source_summary(workspace)
            except Exception as exc:
                logger.warning("无法准备 workspace 摘要: %s", exc)
                workspace_info = {"id": self.workspace_manager.project_id}
        return {
            "projectId": self.project_id,
            "project": project,
            "workspace": workspace_info,
            "sources": sources,
            "repositories": repositories,
            "applications": applications,
            "counts": {
                "symbols": self.db.execute("SELECT count(*) FROM code_symbol").fetchone()[0],
                "facts": self.db.execute("SELECT count(*) FROM code_fact").fetchone()[0],
                "businessKnowledge": self.db.execute(
                    "SELECT count(*) FROM business_entity WHERE status!='DEPRECATED'"
                ).fetchone()[0],
                "businessFlows": self.db.execute(
                    "SELECT count(*) FROM business_entity WHERE entity_type='FLOW' AND status!='DEPRECATED'"
                ).fetchone()[0],
                "entryAnchors": self.db.execute(
                    "SELECT count(*) FROM business_entry_anchor WHERE status IN ('ACTIVE','VERIFIED')"
                ).fetchone()[0],
                "repositories": len(repositories),
                "requirements": self.db.execute("SELECT count(*) FROM requirement").fetchone()[0],
                "runs": self.db.execute("SELECT count(*) FROM query_run").fetchone()[0],
                "applications": len(applications),
                "integrationEdges": self.db.execute(
                    "SELECT count(*) FROM cross_application_edge WHERE status='VERIFIED'"
                ).fetchone()[0],
            },
        }

    def _new_id(self, prefix: str) -> str:
        if self.project_scoped:
            return f"{prefix}-{_safe_name(self.project_id)}-{uuid.uuid4().hex}"
        return f"{prefix}-{uuid.uuid4().hex}"

    def _validate_owned_id(self, value: str | None, kind: str) -> None:
        if not value or not self.project_scoped:
            return
        prefix = "CONV" if kind == "conversation" else "RUN"
        expected = f"{prefix}-{_safe_name(self.project_id)}-"
        if not str(value).startswith(expected):
            raise ValueError(f"{kind} 不属于当前工程")

    def _get_or_create_conversation(
        self,
        conversation_id: str | None,
        workspace: Workspace | str,
        mode: str,
        runtime_name: str,
    ) -> dict[str, Any]:
        workspace_id = workspace if isinstance(workspace, str) else workspace.id
        ast_version_id = None if isinstance(workspace, str) else workspace.ast_version_id
        now = _now()
        if conversation_id:
            row = self.db.execute(
                """SELECT id,runtime,runtime_session_id,workspace_id,scope_json,mode,ast_version_id,
                   session_config_json FROM query_conversation WHERE id=?""",
                (conversation_id,),
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE query_conversation SET workspace_id=?,updated_at=? WHERE id=?",
                    (workspace_id, now, conversation_id),
                )
                value = dict(row)
                value["workspace_id"] = workspace_id
                return value
        conversation_id = conversation_id or self._new_id("CONV")
        self.db.execute(
            """INSERT INTO query_conversation
               (id,runtime,runtime_session_id,workspace_id,scope_json,mode,ast_version_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (conversation_id, runtime_name, None, workspace_id, None, mode,
             ast_version_id, now, now),
        )
        return {
            "id": conversation_id,
            "runtime": runtime_name,
            "runtime_session_id": None,
            "workspace_id": workspace_id,
            "mode": mode,
            "ast_version_id": ast_version_id,
        }

    def _save_message(self, conversation_id: str, run_id: str, role: str, content: str, created_at: str) -> None:
        self.db.execute(
            """INSERT INTO query_message(id,conversation_id,run_id,role,content,created_at)
               VALUES (?,?,?,?,?,?)""",
            (f"QMSG-{uuid.uuid4().hex}", conversation_id, run_id, role, str(content or ""), created_at),
        )

    def _save_event(self, run_id: str, event: Mapping[str, Any], fallback_sequence: int) -> None:
        sequence = event.get("sequence")
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            sequence = fallback_sequence
        sequence = max(1, sequence)
        event_type = str(event.get("eventType") or event.get("event_type") or event.get("type") or "message")
        payload = event.get("payload", event)
        self.db.execute(
            """INSERT OR IGNORE INTO query_event
               (id,run_id,sequence,event_type,payload_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (f"QEV-{uuid.uuid4().hex}", run_id, sequence, event_type, _json(payload), _now()),
        )

    @staticmethod
    def _event_dict(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "runId": row["run_id"],
            "sequence": row["sequence"],
            "eventType": row["event_type"],
            "payload": _load_json(row["payload_json"], {}),
            "createdAt": row["created_at"],
        }


def _default_runtime(*, index_root=None, project_id=None, project_data_root=None):
    """Build the online runtime. Normal API queries always run Pi."""
    environment = dict(os.environ)
    configured = (environment.get("BUSINESS_CODE_AGENT_PROFILE")
                  or environment.get("BUSINESS_CODE_AGENT_RUNTIME") or "").strip()
    if configured and configured.lower() not in {"pi", "pi-v1"}:
        logger.warning("在线问答固定使用 Pi，已忽略旧 Runtime 配置: %s", configured)
    profile = resolve_online_profile(environment)
    from business_code_agent.query.agent.pi import PiAgentRuntime

    root = Path(project_data_root) / "pi-sessions" if project_data_root else None
    return PiAgentRuntime(profile=profile, environment=environment, session_root=root)


__all__ = ["QueryRuntimeError", "QueryService"]

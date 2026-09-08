"""Persistence and orchestration for the Claude Code query runtime.

This module intentionally contains no question classifier, retriever, evidence
evaluator or answer composer. Claude Code owns investigation and conversation
state; the service only prepares a read-only workspace, invokes the runtime and
records the exchange.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .claude_runtime import ClaudeCodeRuntime
from .runtime import RuntimeErrorBase, normalize_runtime_result
from .workspace import Workspace, WorkspaceManager, _safe_name


logger = logging.getLogger(__name__)

QUERY_MODES = ("none", "backbone", "ast")
QUERY_MODE_LABELS = {"none": "无主干", "backbone": "有主干", "ast": "AST"}


class QueryRuntimeError(RuntimeErrorBase):
    """A query could not be completed by the configured runtime."""


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
    ):
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
        self.runtime = runtime or _default_runtime()

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
    ) -> dict[str, Any]:
        """Run one question and persist the complete runtime exchange.

        ``history`` remains an accepted argument for old clients, but is
        deliberately ignored. Session recovery belongs to Claude Code's
        ``--resume`` mechanism, not to Python-side prompt reconstruction.

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
        workspace = self._workspace_for_mode(effective_mode)
        ast_version_id = workspace.ast_version_id
        logger.info("查询工作区就绪: workspace=%s path=%s", workspace.id, workspace.path)
        effective_scope = self._normalize_scope(scope)
        self._validate_owned_id(conversation_id, "conversation")
        run_id = self._new_id("RUN")
        started_at = _now()
        started_clock = time.monotonic()
        # Hold the write lock across the active-run check and insertion so
        # another tab/request cannot start the same session concurrently.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            conversation = self._get_or_create_conversation(conversation_id, workspace, effective_mode)
            stored_scope = _load_json(conversation.get("scope_json"), None)
            stored_mode = str(conversation.get("mode") or "backbone")
            stored_ast_version_id = conversation.get("ast_version_id")
            version_changed = ast_version_id != stored_ast_version_id
            if effective_scope != stored_scope or effective_mode != stored_mode or version_changed:
                # 范围变更不续用旧 runtime 会话：resumed session 可能保留上一范围的目录授权。
                self.db.execute(
                    "UPDATE query_conversation SET scope_json=?,mode=?,ast_version_id=?,runtime_session_id=NULL,updated_at=? WHERE id=?",
                    (_json(effective_scope) if effective_scope else None, effective_mode, ast_version_id,
                     _now(), conversation["id"]),
                )
                conversation["runtime_session_id"] = None
                conversation["scope_json"] = _json(effective_scope) if effective_scope else None
                conversation["mode"] = effective_mode
                conversation["ast_version_id"] = ast_version_id
            active = self.db.execute(
                "SELECT id FROM query_run WHERE conversation_id=? AND status IN ('running','cancelling') LIMIT 1",
                (conversation["id"],),
            ).fetchone()
            if active:
                raise QueryBusyError(active["id"])
            self.db.execute(
                """INSERT INTO query_run
                   (id,conversation_id,runtime,runtime_session_id,question,status,answer,
                    error,usage_json,scope_json,mode,ast_version_id,started_at,completed_at,duration_ms)
                   VALUES (?,?,?,?,?,'running','',NULL,'{}',?,?,?,?,NULL,0)""",
                (run_id, conversation["id"], self._runtime_name(), conversation["runtime_session_id"], question,
                 _json(effective_scope) if effective_scope else None, effective_mode, ast_version_id, started_at),
            )
            self._save_message(conversation["id"], run_id, "user", question, started_at)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        logger.info("查询开始: run=%s conversation=%s runtime=%s session=%s",
                    run_id, conversation["id"], self._runtime_name(), conversation["runtime_session_id"] or "new")
        emitted: list[dict[str, Any]] = []
        client_connected = True

        def on_event(event: Mapping[str, Any]) -> None:
            nonlocal client_connected
            normalized = dict(event)
            emitted.append(normalized)
            self._save_event(run_id, normalized, len(emitted))
            self.db.commit()
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
                              "astVersionId": ast_version_id})
            raw_result = self.runtime.ask(
                question,
                workspace=str(workspace.path),
                session_id=conversation["runtime_session_id"],
                event_callback=on_event,
                cancel_check=lambda: self.db.execute("SELECT status FROM query_run WHERE id=?", (run_id,)).fetchone()[0] == "cancelling",
                repositories={_safe_name(repo) for repo in effective_scope["repositoryIds"]} if effective_scope else None,
            )
            result = normalize_runtime_result(raw_result)
        except Exception as exc:
            duration = round((time.monotonic() - started_clock) * 1000, 3)
            logger.error("查询失败: run=%s conversation=%s duration_ms=%s error_type=%s",
                         run_id, conversation["id"], duration, type(exc).__name__)
            error_text = str(exc) or exc.__class__.__name__
            self._save_event(
                run_id,
                {
                    "eventType": "error",
                    "subtype": exc.__class__.__name__,
                    "payload": {"error": error_text},
                },
                len(emitted) + 1,
            )
            self.db.execute(
                """UPDATE query_run SET status='failed',error=?,completed_at=?,duration_ms=?
                   WHERE id=?""",
                (error_text, _now(), duration, run_id),
            )
            self.db.commit()
            if isinstance(exc, QueryRuntimeError):
                raise
            raise QueryRuntimeError(error_text) from exc

        # A custom runtime may return events without invoking the callback.
        for event in result.events:
            if event not in emitted:
                on_event(event)

        session_id = result.runtime_session_id or conversation["runtime_session_id"]
        completed_at = _now()
        final_status = "cancelled" if result.status == "cancelled" else "completed"
        if final_status == "cancelled":
            on_event({"sequence": len(emitted) + 1, "eventType": "status",
                      "payload": {"phase": "cancelled", "label": "已停止"}})
        duration = round((time.monotonic() - started_clock) * 1000, 3)
        self.db.execute(
            """UPDATE query_run SET status=?,runtime_session_id=?,answer=?,
               usage_json=?,completed_at=?,duration_ms=? WHERE id=?""",
            (final_status, session_id, result.answer, _json(result.usage), completed_at, duration, run_id),
        )
        if result.answer:
            self._save_message(conversation["id"], run_id, "assistant", result.answer, completed_at)
        self.db.execute(
            """UPDATE query_conversation SET runtime=?,runtime_session_id=?,workspace_id=?,ast_version_id=?,updated_at=?
               WHERE id=?""",
            (self._runtime_name(), session_id, workspace.id, ast_version_id, completed_at, conversation["id"]),
        )
        self.db.commit()
        logger.info("查询结束: run=%s status=%s conversation=%s session=%s duration_ms=%s events=%s answer_characters=%s",
                    run_id, final_status, conversation["id"], session_id, duration, len(emitted), len(result.answer))
        return {
            "runId": run_id,
            "conversationId": conversation["id"],
            "projectId": self.project_id,
            "runtime": self._runtime_name(),
            "sessionId": session_id,
            "workspaceId": workspace.id,
            "mode": effective_mode,
            "modeLabel": QUERY_MODE_LABELS[effective_mode],
            "astVersionId": ast_version_id,
            "status": final_status,
            "scope": effective_scope,
            "answer": result.answer,
            "events": emitted,
            "usage": result.usage,
        }

    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        value = str(mode or "backbone").strip().casefold()
        aliases = {"no_backbone": "none", "without": "none", "full": "backbone", "overview": "backbone"}
        value = aliases.get(value, value)
        if value not in QUERY_MODES:
            raise ValueError("mode 必须是 none、backbone 或 ast")
        return value

    def _workspace_for_mode(self, mode: str) -> Workspace:
        business_context_source = None
        code_map_source = None
        description = None
        ast_version_id = None
        if mode == "ast":
            from ..evaluation.ast_service import AstService
            current = AstService(project_config=self.project_config, data_root=self.ast_data_root).current_version()
            if not current:
                raise ValueError("AST 资料尚未生成或已经待更新，请先在 AST 管理页手动生成")
            version_id, metadata, code_map_source = current
            ast_version_id = version_id
            description = f"当前模式：AST（资料版本 {version_id}，生成时间 {metadata.get('generatedAt') or '未知'}）。仅提供用户手动生成的结构资料，不提供人工业务补充知识。"
        elif mode == "none":
            description = "当前模式：无主干。正常项目 README、需求原文和源码可用，不提供业务补充知识或自动代码地图。"
        else:
            description = "当前模式：有主干。提供项目特有的业务补充知识，按问题需要读取；源码仍是当前实现依据。"
        return self.workspace_manager.ensure(mode=mode, business_context_source=business_context_source,
                                             code_map_source=code_map_source,
                                             mode_description=description, ast_version_id=ast_version_id)

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
        ast_version_id = value.get("ast_version_id") or (conversation["ast_version_id"] if conversation else None)
        return {
            "id": value["id"],
            "runId": value["id"],
            "conversationId": conversation_id,
            "projectId": self.project_id,
            "runtime": value["runtime"],
            "sessionId": value["runtime_session_id"] or (conversation["runtime_session_id"] if conversation else None),
            "workspaceId": conversation["workspace_id"] if conversation else None,
            "mode": value.get("mode") or (conversation["mode"] if conversation else "backbone"),
            "modeLabel": QUERY_MODE_LABELS.get(value.get("mode") or (conversation["mode"] if conversation else "backbone"), "有主干"),
            "astVersionId": ast_version_id,
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
                      mode,ast_version_id,started_at,completed_at,duration_ms
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
        try:
            workspace = self.workspace_manager.ensure()
            workspace_info = workspace.to_dict()
        except Exception as exc:
            logger.warning("无法准备 workspace 摘要: %s", exc)
            workspace_info = {"id": self.workspace_manager.project_id}
        return {
            "projectId": self.project_id,
            "project": project,
            "workspace": workspace_info,
            "sources": self.workspace_manager.source_summary(),
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

    def _runtime_name(self) -> str:
        return str(getattr(self.runtime, "runtime_name", "CLAUDE_CODE"))

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
        workspace: Workspace,
        mode: str,
    ) -> dict[str, Any]:
        now = _now()
        if conversation_id:
            row = self.db.execute(
                "SELECT id,runtime,runtime_session_id,workspace_id,scope_json,mode,ast_version_id FROM query_conversation WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE query_conversation SET workspace_id=?,updated_at=? WHERE id=?",
                    (workspace.id, now, conversation_id),
                )
                value = dict(row)
                value["workspace_id"] = workspace.id
                return value
        conversation_id = conversation_id or self._new_id("CONV")
        self.db.execute(
            """INSERT INTO query_conversation
               (id,runtime,runtime_session_id,workspace_id,scope_json,mode,ast_version_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (conversation_id, self._runtime_name(), None, workspace.id, None, mode,
             workspace.ast_version_id, now, now),
        )
        return {
            "id": conversation_id,
            "runtime": self._runtime_name(),
            "runtime_session_id": None,
            "workspace_id": workspace.id,
            "mode": mode,
            "ast_version_id": workspace.ast_version_id,
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


def _default_runtime() -> ClaudeCodeRuntime:
    command = os.environ.get("CLAUDE_CODE_COMMAND", "claude").strip() or "claude"
    timeout_raw = os.environ.get("CLAUDE_CODE_TIMEOUT", "600")
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 600.0
    tools = tuple(
        item.strip()
        for item in os.environ.get("CLAUDE_CODE_READ_TOOLS", "Read,Glob,Grep").split(",")
        if item.strip()
    ) or ("Read", "Glob", "Grep")
    return ClaudeCodeRuntime(command=command, timeout_seconds=timeout, read_tools=tools)


__all__ = ["QueryRuntimeError", "QueryService"]

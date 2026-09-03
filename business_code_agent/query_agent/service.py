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
from .workspace import Workspace, WorkspaceManager


logger = logging.getLogger(__name__)


class QueryRuntimeError(RuntimeErrorBase):
    """A query could not be completed by the configured runtime."""


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
    ):
        self.db = db
        self.db_path = db_path
        self.project_config = str(project_config) if project_config else None
        self.workspace_manager = workspace_manager or WorkspaceManager(
            db,
            project_config=project_config,
            workspace_root=self._default_workspace_root(project_config, db_path),
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
    ) -> dict[str, Any]:
        """Run one question and persist the complete runtime exchange.

        ``history`` remains an accepted argument for old clients, but is
        deliberately ignored. Session recovery belongs to Claude Code's
        ``--resume`` mechanism, not to Python-side prompt reconstruction.
        """
        del history
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")

        workspace = self.workspace_manager.ensure()
        conversation = self._get_or_create_conversation(conversation_id, workspace)
        run_id = f"RUN-{uuid.uuid4().hex}"
        started_at = _now()
        started_clock = time.monotonic()
        self.db.execute(
            """INSERT INTO query_run
               (id,conversation_id,runtime,runtime_session_id,question,status,answer,
                error,usage_json,started_at,completed_at,duration_ms)
               VALUES (?,?,?,?,?,'running','',NULL,'{}',?,NULL,0)""",
            (
                run_id,
                conversation["id"],
                self._runtime_name(),
                conversation["runtime_session_id"],
                question,
                started_at,
            ),
        )
        self._save_message(conversation["id"], run_id, "user", question, started_at)
        self.db.commit()

        emitted: list[dict[str, Any]] = []

        def on_event(event: Mapping[str, Any]) -> None:
            normalized = dict(event)
            emitted.append(normalized)
            self._save_event(run_id, normalized, len(emitted))
            self.db.commit()
            if event_callback:
                try:
                    event_callback(normalized)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # A disconnected SSE client must not turn a successful
                    # runtime invocation into a failed persisted run.
                    logger.debug("query event client disconnected", exc_info=True)

        try:
            raw_result = self.runtime.ask(
                question,
                workspace=str(workspace.path),
                session_id=conversation["runtime_session_id"],
                event_callback=on_event,
            )
            result = normalize_runtime_result(raw_result)
        except Exception as exc:
            duration = round((time.monotonic() - started_clock) * 1000, 3)
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
        duration = round((time.monotonic() - started_clock) * 1000, 3)
        self.db.execute(
            """UPDATE query_run SET status='completed',runtime_session_id=?,answer=?,
               usage_json=?,completed_at=?,duration_ms=? WHERE id=?""",
            (session_id, result.answer, _json(result.usage), completed_at, duration, run_id),
        )
        self._save_message(conversation["id"], run_id, "assistant", result.answer, completed_at)
        self.db.execute(
            """UPDATE query_conversation SET runtime=?,runtime_session_id=?,workspace_id=?,updated_at=?
               WHERE id=?""",
            (self._runtime_name(), session_id, workspace.id, completed_at, conversation["id"]),
        )
        self.db.commit()
        return {
            "runId": run_id,
            "conversationId": conversation["id"],
            "runtime": self._runtime_name(),
            "sessionId": session_id,
            "workspaceId": workspace.id,
            "status": "completed",
            "answer": result.answer,
            "events": emitted,
            "usage": result.usage,
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
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
            "SELECT runtime,runtime_session_id,workspace_id FROM query_conversation WHERE id=?",
            (conversation_id,),
        ).fetchone()
        return {
            "id": value["id"],
            "runId": value["id"],
            "conversationId": conversation_id,
            "runtime": value["runtime"],
            "sessionId": value["runtime_session_id"] or (conversation["runtime_session_id"] if conversation else None),
            "workspaceId": conversation["workspace_id"] if conversation else None,
            "question": value["question"],
            "status": value["status"],
            "answer": value["answer"],
            "error": value["error"],
            "usage": _load_json(value["usage_json"], {}),
            "startedAt": value["started_at"],
            "completedAt": value["completed_at"],
            "durationMs": value["duration_ms"],
            "events": events,
            "feedback": feedback,
        }

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        rows = self.db.execute(
            """SELECT id,conversation_id,runtime,runtime_session_id,question,status,answer,error,
                      started_at,completed_at,duration_ms
                 FROM query_run ORDER BY started_at DESC,id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "runId": row["id"],
                "conversationId": row["conversation_id"],
                "runtime": row["runtime"],
                "sessionId": row["runtime_session_id"],
                "question": row["question"],
                "status": row["status"],
                "answer": row["answer"],
                "error": row["error"],
                "startedAt": row["started_at"],
                "completedAt": row["completed_at"],
                "durationMs": row["duration_ms"],
            }
            for row in rows
        ]

    def record_feedback(self, run_id: str, rating: str, comment: str = "") -> dict[str, Any]:
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
            "project": project,
            "workspace": workspace_info,
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

    def _get_or_create_conversation(
        self,
        conversation_id: str | None,
        workspace: Workspace,
    ) -> dict[str, Any]:
        now = _now()
        if conversation_id:
            row = self.db.execute(
                "SELECT id,runtime,runtime_session_id,workspace_id FROM query_conversation WHERE id=?",
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
        conversation_id = conversation_id or f"CONV-{uuid.uuid4().hex}"
        self.db.execute(
            """INSERT INTO query_conversation
               (id,runtime,runtime_session_id,workspace_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (conversation_id, self._runtime_name(), None, workspace.id, now, now),
        )
        return {
            "id": conversation_id,
            "runtime": self._runtime_name(),
            "runtime_session_id": None,
            "workspace_id": workspace.id,
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

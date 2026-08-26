from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..knowledge_update.langchain_adapter import model_config_from_environment
from ..schema import connect
from .agent import BusinessCodeQueryAgent
from .langchain_adapter import LangChainQueryAnalyzer, LangChainQueryComposer


def _safe_json_loads(text):
    """Parse stored JSON, returning an empty dict for legacy/corrupt rows."""
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class QueryService:
    def __init__(self, db, *, db_path: str | None = None, project_config: str | None = None):
        self.db = db
        connection_factory = (lambda: connect(db_path)) if db_path and db_path != ":memory:" else None
        analyzer, composer = self._model_stages(project_config)
        self.agent = BusinessCodeQueryAgent(
            db, connection_factory=connection_factory,
            query_analyzer=analyzer, answer_composer=composer,
        )

    def query(self, question: str, *, conversation_id: str | None = None, history=()) -> dict:
        conversation_id = conversation_id or f"CONV-{uuid.uuid4().hex[:16]}"
        stored_history = self._conversation_history(conversation_id)
        merged_history = [*stored_history, *list(history or [])][-6:]
        value = self.agent.run(question, history=merged_history)
        self._record_conversation(conversation_id, value["runId"], question, value["answer"])
        value["conversationId"] = conversation_id
        # The UI renders exact Evidence records, not the broader symbol context
        # temporarily loaded into the agent's bounded reasoning context.
        value["evidence"] = self.evidence_for_answer(value["answer"])
        return value

    def _model_stages(self, project_config):
        config = model_config_from_environment()
        if config is None:
            if not project_config or not Path(project_config).is_file():
                return None, None
            try:
                payload = json.loads(Path(project_config).read_text(encoding="utf-8"))
                config = payload.get("queryModel") or payload.get("model")
            except Exception:
                return None, None
        try:
            if not isinstance(config, dict) or not config.get("enabled", True):
                return None, None
            analyzer = LangChainQueryAnalyzer.from_config(config)
            return analyzer, LangChainQueryComposer(analyzer.model)
        except Exception:
            return None, None

    def _conversation_history(self, conversation_id):
        rows = self.db.execute(
            "SELECT role,content FROM query_message WHERE conversation_id=? ORDER BY created_at,id LIMIT 12",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _record_conversation(self, conversation_id, run_id, question, answer):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT OR IGNORE INTO query_conversation(id,created_at,updated_at) VALUES (?, ?, ?)",
            (conversation_id, now, now),
        )
        self.db.execute(
            "INSERT INTO query_message(id,conversation_id,run_id,role,content,created_at) VALUES (?, ?, ?, 'user', ?, ?)",
            (f"QMSG-{uuid.uuid4().hex}", conversation_id, run_id, question, now),
        )
        self.db.execute(
            "INSERT INTO query_message(id,conversation_id,run_id,role,content,created_at) VALUES (?, ?, ?, 'assistant', ?, ?)",
            (f"QMSG-{uuid.uuid4().hex}", conversation_id, run_id, answer.get("conclusion", ""), now),
        )
        self.db.execute("UPDATE query_conversation SET updated_at=? WHERE id=?", (now, conversation_id))
        self.db.commit()

    def get_run(self, run_id: str) -> dict:
        run = self.db.execute("SELECT * FROM query_agent_run WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError(run_id)
        value = dict(run)
        # Legacy or interrupted rows may carry missing/corrupt JSON; degrade
        # gracefully instead of failing the whole replay endpoint.
        value["answer"] = _safe_json_loads(value.pop("answer_json"))
        value["state"] = _safe_json_loads(value.pop("state_json"))
        value["steps"] = [dict(row) for row in self.db.execute(
            "SELECT * FROM query_agent_step WHERE run_id=? ORDER BY created_at,id", (run_id,)
        )]
        value["toolCalls"] = [dict(row) for row in self.db.execute(
            "SELECT * FROM query_tool_call WHERE run_id=? ORDER BY created_at,id", (run_id,)
        )]
        value["checkpoints"] = [dict(row) for row in self.db.execute(
            "SELECT sequence,node_name,state_json,created_at FROM query_checkpoint WHERE run_id=? ORDER BY sequence", (run_id,)
        )]
        value["evidence"] = self.evidence_for_answer(value["answer"])
        message = self.db.execute(
            "SELECT conversation_id FROM query_message WHERE run_id=? ORDER BY created_at LIMIT 1", (run_id,)
        ).fetchone()
        value["conversationId"] = message["conversation_id"] if message else None
        return value

    def record_feedback(self, run_id: str, rating: str, comment: str = "") -> dict:
        if not self.db.execute("SELECT 1 FROM query_agent_run WHERE id=?", (run_id,)).fetchone():
            raise KeyError(run_id)
        rating = str(rating).strip().upper()
        if rating not in {"HELPFUL", "NOT_HELPFUL"}:
            raise ValueError("rating must be HELPFUL or NOT_HELPFUL")
        from datetime import datetime, timezone
        feedback_id = f"QFB-{uuid.uuid4().hex[:16]}"
        self.db.execute(
            "INSERT INTO query_feedback VALUES (?, ?, ?, ?, ?)",
            (feedback_id, run_id, rating, str(comment or "")[:1000], datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()
        return {"id": feedback_id, "runId": run_id, "rating": rating}

    def evidence_for_answer(self, answer: dict) -> list[dict]:
        evidence_ids = list(dict.fromkeys(
            evidence_id
            for section in ("facts", "inferences")
            for item in answer.get(section, [])
            for evidence_id in item.get("evidenceIds", [])
        ))
        for conflict in answer.get("conflicts", []):
            evidence_ids.extend(conflict.get("evidenceIds", []))
        values = []
        for evidence_id in dict.fromkeys(evidence_ids):
            row = self.db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if not row:
                continue
            item = dict(row)
            source_type = "BUSINESS" if item["source_type"] == "MANUAL" else item["source_type"]
            location = {"locator": item["locator"]}
            if source_type == "CODE":
                location.update({"file": item["locator"], "startLine": item["line_start"], "endLine": item["line_end"]})
            elif source_type == "REQUIREMENT":
                location.update({"chunkId": item["chunk_id"], "section": item["locator"]})
            else:
                location.update({"knowledgeId": item["source_id"]})
            code_fact = self.db.execute(
                """SELECT cs.id symbol_id,cs.qualified_name,cf.fact_type
                     FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                    WHERE cf.evidence_id=? ORDER BY cs.qualified_name LIMIT 1""",
                (evidence_id,),
            ).fetchone() if source_type == "CODE" else None
            values.append({
                "evidenceId": item["id"], "sourceType": source_type,
                "sourceId": code_fact["symbol_id"] if code_fact else item["source_id"],
                "sourceVersion": item["source_version"],
                "location": location, "content": item["excerpt"],
                "contentHash": item["content_hash"], "status": "CONFIRMED" if source_type == "BUSINESS" else "DIRECT",
                "symbol": code_fact["qualified_name"] if code_fact else None,
                "relationType": code_fact["fact_type"] if code_fact else None,
            })
        return values

    def list_runs(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        return [dict(row) for row in self.db.execute(
            """SELECT id,question,intent,status,evidence_status,iterations,
                      source_characters,created_at,completed_at
                 FROM query_agent_run ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )]

    def workspace_summary(self) -> dict:
        raw_repositories = [dict(row) for row in self.db.execute(
            "SELECT id,root_path,indexed_at FROM repository ORDER BY indexed_at DESC"
        )]
        repositories = [{
            "id": row["id"], "displayName": Path(row["root_path"]).name,
            "indexedAt": row["indexed_at"], "revision": None,
        } for row in raw_repositories]
        return {
            "project": repositories[0]["id"] if repositories else "未索引项目",
            "repositories": repositories,
            "counts": {
                "symbols": self.db.execute("SELECT count(*) FROM code_symbol").fetchone()[0],
                "facts": self.db.execute("SELECT count(*) FROM code_fact").fetchone()[0],
                "businessKnowledge": self.db.execute(
                    "SELECT count(*) FROM business_function WHERE status='PUBLISHED'"
                ).fetchone()[0],
                "pendingProposals": self.db.execute(
                    """SELECT count(*) FROM knowledge_update_proposal
                         WHERE status IN ('PENDING_REVIEW','DEFERRED','CHANGES_REQUESTED')"""
                ).fetchone()[0],
                "requirements": self.db.execute("SELECT count(*) FROM requirement").fetchone()[0],
                "runs": self.db.execute("SELECT count(*) FROM query_agent_run").fetchone()[0],
            },
        }

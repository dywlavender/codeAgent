from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone

from ..util import dumps, stable_id


class QueryRunRecorder:
    def __init__(self, db, run_id: str, question: str):
        self.db = db
        self.run_id = run_id
        self.question = question
        self.checkpoint_sequence = 0

    def start(self, intent: str = "UNKNOWN") -> None:
        now = _now()
        self.db.execute(
            """INSERT INTO query_agent_run VALUES
               (?, ?, ?, 'running', 'INSUFFICIENT', 0, 0, '{}', '{}', ?, NULL)""",
            (self.run_id, self.question, intent, now),
        )
        self.db.commit()

    def update_intent(self, intent: str) -> None:
        self.db.execute("UPDATE query_agent_run SET intent=? WHERE id=?", (intent, self.run_id))

    def step(self, name: str, iteration: int, input_summary: dict, output_summary: dict, evidence_count: int, duration_ms: float) -> str:
        step_id = stable_id("QSTEP", self.run_id, name, str(iteration), str(time.time_ns()))
        self.db.execute(
            "INSERT INTO query_agent_step VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, self.run_id, name, iteration, dumps(input_summary), dumps(output_summary), evidence_count, duration_ms, _now()),
        )
        return step_id

    def tool_call(self, step_id: str, tool_name: str, tool_input: dict, result_count: int, iteration: int, duration_ms: float) -> None:
        call_id = stable_id("QCALL", step_id, tool_name, str(time.time_ns()))
        self.db.execute(
            "INSERT INTO query_tool_call VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, self.run_id, step_id, tool_name, dumps(tool_input), result_count, iteration, duration_ms, _now()),
        )

    def checkpoint(self, node_name: str, state: dict) -> None:
        self.checkpoint_sequence += 1
        self.db.execute(
            "INSERT INTO query_checkpoint VALUES (?, ?, ?, ?, ?)",
            (self.run_id, self.checkpoint_sequence, node_name, dumps(state), _now()),
        )
        self.db.commit()

    def finish(self, state: dict, answer: dict, status: str, source_characters: int) -> None:
        self.db.execute(
            """UPDATE query_agent_run SET status='completed', evidence_status=?, iterations=?,
                      source_characters=?, answer_json=?, state_json=?, completed_at=? WHERE id=?""",
            (status, state.get("iteration", 0), source_characters, dumps(answer), dumps(state), _now(), self.run_id),
        )
        self.db.commit()

    def fail(self, error_type: str, state: dict | None = None) -> None:
        payload = {"errorType": error_type}
        self.db.execute(
            "UPDATE query_agent_run SET status='failed', answer_json=?, state_json=?, completed_at=? WHERE id=?",
            (dumps(payload), dumps(state or {}), _now(), self.run_id),
        )
        self.db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

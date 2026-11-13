"""Runtime abstraction for the query surface.

The service deliberately knows nothing about prompt construction, tool
selection or conversation memory. Those responsibilities belong to the
runtime implementation (by default the project-owned LangChain agent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


EventCallback = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class RuntimeResult:
    answer: str
    runtime_session_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "runtimeSessionId": self.runtime_session_id,
            "events": [dict(event) for event in self.events],
            "usage": dict(self.usage),
            "status": self.status,
        }


class RuntimeErrorBase(RuntimeError):
    """Base error surfaced by a runtime implementation."""


def normalize_runtime_result(value: RuntimeResult | Mapping[str, Any]) -> RuntimeResult:
    """Accept the dataclass or a small mapping from test/custom runtimes."""
    if isinstance(value, RuntimeResult):
        return value
    if not isinstance(value, Mapping):
        raise RuntimeErrorBase("runtime returned an invalid result")
    answer = str(value.get("answer") or value.get("result") or "").strip()
    if not answer and value.get("status") != "cancelled":
        raise RuntimeErrorBase("runtime returned an empty answer")
    raw_events = value.get("events") or []
    events = [dict(event) for event in raw_events if isinstance(event, Mapping)]
    session_id = value.get("runtimeSessionId", value.get("runtime_session_id", value.get("sessionId")))
    usage = value.get("usage") or {}
    return RuntimeResult(
        answer=answer,
        runtime_session_id=str(session_id) if session_id else None,
        events=events,
        usage=dict(usage) if isinstance(usage, Mapping) else {},
        status=str(value.get("status") or "completed"),
    )

"""Persist public model inputs/outputs without provider credentials or reasoning."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from business_code_agent.query.traces import public_message


class TraceRecorder:
    def __init__(self, workspace, project_id, question, settings, on_update=None):
        self.started = time.monotonic()
        self.on_update = on_update
        self.path = Path(workspace) / ".langchain-traces" / f"{uuid.uuid4().hex}.trace.json"
        self.value = {
            "projectId": project_id, "question": question, "settings": settings,
            "snapshotStage": "langchain_before_provider_serialization",
            "startedAt": datetime.now(timezone.utc).isoformat(), "status": "running",
            "calls": [], "events": [], "semanticProof": False,
        }
        self.write()

    def write(self, *, notify=True):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        if notify and self.on_update:
            self.on_update(str(self.path))

    def event(self, event):
        self.value["events"].append({**event, "at": datetime.now(timezone.utc).isoformat(),
                                     "elapsedMs": round((time.monotonic() - self.started) * 1000, 3)})
        self.write(notify=False)

    def begin(self, phase, messages, tools, investigation):
        self.value["investigation"] = investigation
        for key in ("retrievalPlan", "contracts"):
            if key in investigation:
                self.value[key] = investigation[key]
        entry = {
            "attempt": len(self.value["calls"]) + 1, "phase": phase,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "elapsedMs": round((time.monotonic() - self.started) * 1000, 3),
            "messages": [public_message(m) for m in messages],
            "tools": [convert_to_openai_tool(t) for t in tools],
            "investigation": investigation, "status": "running",
            "inputTextChars": sum(len(str(m.content)) for m in messages),
        }
        self.value["calls"].append(entry)
        self.write()
        return entry

    def finish(self, entry, messages=None, error=None):
        entry["status"] = "failed" if error else "completed"
        if error is not None and type(error).__name__ == "CancelledError":
            entry["status"] = "cancelled"
        entry["completedAt"] = datetime.now(timezone.utc).isoformat()
        entry["durationMs"] = round((time.monotonic() - self.started) * 1000 - entry["elapsedMs"], 3)
        if error:
            entry["errorType"] = type(error).__name__
            entry["error"] = str(error)
        else:
            entry["responses"] = [public_message(m) for m in messages]
            entry["usage"] = [m.usage_metadata for m in messages if isinstance(m, AIMessage)]
        self.write()

    def complete(self, status, **values):
        self.value.update(values, status=status, completedAt=datetime.now(timezone.utc).isoformat(),
                          durationMs=round((time.monotonic() - self.started) * 1000, 3))
        self.write()

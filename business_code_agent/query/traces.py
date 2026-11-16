"""Run-owned trace archives survive workspace refreshes and failed requests."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from langchain_core.messages import AIMessage, ToolMessage


def public_message(message):
    """Serialize a model-visible message without private reasoning or credentials."""
    value = {"role": message.type, "content": message.text if isinstance(message, AIMessage) else message.content}
    if isinstance(message, AIMessage):
        value["toolCalls"] = message.tool_calls
        value["metadata"] = {key: message.response_metadata[key] for key in
                             ("model_name", "model_provider", "finish_reason", "id") if key in message.response_metadata}
    if isinstance(message, ToolMessage):
        value.update({"toolCallId": message.tool_call_id, "name": message.name})
    return value


class RunTraceArchive:
    def __init__(self, root, run_id, project_id=None):
        self.path = Path(root) / f"{run_id}.json"
        self.project_id = project_id
        self.run_id = run_id
        self.runtime_path = None
        self.workspace = None
        self.value = {}

    def start(self, run, workspace):
        self.workspace = Path(workspace).resolve()
        pi_runtime = str(run.get("runtime") or "").upper() == "PI"
        self.value = {
            "schemaVersion": 1, "runId": self.run_id, "projectId": self.project_id,
            "run": run, "calls": [], "events": [],
            "modelCallRecordsAvailable": False,
            "requestSnapshotAvailable": False,
            "modelSnapshotAvailable": False,
            "snapshotStage": ("pi_message_summary" if pi_runtime
                              else "langchain_before_provider_serialization"),
            "snapshotNote": ("Pi 每次模型调用只记录阶段、工具名、响应文本和用量摘要，"
                             "不保存完整请求 messages/tools。" if pi_runtime else
                             "LangChain 在 provider 序列化前保存完整模型请求。"),
            "semanticProof": False,
        }
        self.write()

    def event(self, event):
        self.value["events"].append(event)
        if event.get("eventType") == "trace":
            candidate = Path(event["payload"]["tracePath"]).resolve()
            if candidate.is_relative_to(self.workspace / ".langchain-traces") and candidate.name.endswith(".trace.json"):
                self.runtime_path = candidate
        self.refresh()
        self.write()

    def record_call(self, call):
        """Upsert one model request so in-flight and failed calls are retained."""
        entry = {**call}
        for index, existing in enumerate(self.value["calls"]):
            if existing.get("attempt") == entry.get("attempt"):
                self.value["calls"][index] = entry
                break
        else:
            self.value["calls"].append(entry)
        calls = self.value["calls"]
        full = any(item.get("messages") or item.get("tools") for item in calls)
        self.value["modelCallRecordsAvailable"] = bool(calls)
        self.value["requestSnapshotAvailable"] = full
        self.value["modelSnapshotAvailable"] = full
        self.write()

    def refresh(self):
        if not self.runtime_path or not self.runtime_path.is_file():
            return
        snapshot = json.loads(self.runtime_path.read_text(encoding="utf-8"))
        if self.project_id and snapshot.get("projectId") != self.project_id:
            raise ValueError("调用轨迹不属于当前工程")
        for key in ("calls", "settings", "investigation", "contracts", "retrievalPlan", "finalAnswer", "stopReason", "status"):
            if key in snapshot:
                self.value[key] = snapshot[key]
        # Runtime events have precise timing; the archive also retains all API events.
        self.value["runtimeEvents"] = snapshot.get("events", [])
        calls = snapshot.get("calls") or []
        full = any(call.get("messages") or call.get("tools") for call in calls)
        self.value["modelCallRecordsAvailable"] = bool(calls)
        self.value["requestSnapshotAvailable"] = full
        self.value["modelSnapshotAvailable"] = full

    def update_run(self, **fields):
        self.value.setdefault("run", {}).update(fields)
        self.write()

    def finish(self, **run):
        self.refresh()
        self.value["run"].update(run)
        self.value["status"] = run["status"]
        stop_reason = run.get("stopReason") or (run.get("usage") or {}).get("stopReason")
        if stop_reason:
            self.value["stopReason"] = stop_reason
        self.write()

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(self.value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def read(self, run):
        if self.path.is_file():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("projectId") != self.project_id or value.get("runId") != self.run_id:
                raise ValueError("调用轨迹不属于当前工程或运行")
            return {**value, "run": {**value.get("run", {}), **run}}
        return {"schemaVersion": 1, "runId": self.run_id, "projectId": self.project_id, "run": run,
                "calls": [], "events": run["events"],
                "modelCallRecordsAvailable": False, "requestSnapshotAvailable": False,
                "modelSnapshotAvailable": False, "snapshotStage": "events_only",
                "historical": True, "semanticProof": False}

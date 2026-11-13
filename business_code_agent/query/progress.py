"""Translate CLI envelopes into user-facing progress, never raw thinking text.

Event payloads are also the persisted contract: status, tool and text updates
have stable IDs so live clients and history replay use the same reducer.
"""

from __future__ import annotations

import json
import time


class ProgressEvents:
    def __init__(self):
        self.message_id = "message-0"
        self.message_count = 0
        self.blocks = {}
        self.text_indices = {}
        self.tools = {}
        self.phase = None
        self.pending_id = None
        self.pending_text = ""
        self.last_flush = time.monotonic()

    def flush(self, *, force=False):
        if not self.pending_text or (not force and time.monotonic() - self.last_flush < 0.1):
            return []
        event = self._event("text", id=self.pending_id, text=self.pending_text, mode="append")
        self.pending_text = ""
        self.last_flush = time.monotonic()
        return [event]

    @staticmethod
    def _event(kind, **payload):
        return {"eventType": kind, "payload": payload}

    def _status(self, phase, label):
        if self.phase == phase:
            return []
        self.phase = phase
        return [self._event("status", phase=phase, label=label)]

    def _text_id(self, stream_index):
        # CLI snapshots can omit thinking blocks. Raw content indices therefore
        # differ from stream indices; use the ordinal among text blocks only.
        if stream_index not in self.text_indices:
            self.text_indices[stream_index] = len(self.text_indices)
        return f"{self.message_id}:{self.text_indices[stream_index]}"

    def _tool(self, block):
        tool_id = block.get("id") or f"tool-{len(self.tools) + 1}"
        data = {"id": tool_id, "name": block.get("name", "Tool"),
                "input": block.get("input") or {}, "status": "running"}
        if self.tools.get(tool_id) == data:
            return []
        self.tools[tool_id] = data
        self.phase = "tool"
        return [self._event("tool", **data)]

    def feed(self, payload):
        kind = payload.get("type")
        if kind == "stream_event":
            return self._stream(payload.get("event") or {})
        events = self.flush(force=True)
        if kind == "system":
            if payload.get("subtype") == "init":
                events += self._status("starting", "正在准备工作区")
            elif payload.get("subtype") == "thinking_tokens":
                events += self._status("thinking", "正在分析")
        elif kind == "assistant":
            message = payload.get("message") or {}
            message_id = message.get("id") or self.message_id
            text_index = 0
            for block in message.get("content") or []:
                if block.get("type") == "tool_use":
                    events += self._tool(block)
                elif block.get("type") == "text":
                    if block.get("text"):
                        self.phase = "text"
                        events.append(self._event("text", id=f"{message_id}:{text_index}",
                                                  text=block["text"], mode="replace"))
                    text_index += 1
        elif kind in {"tool_use", "tool_result", "user"}:
            blocks = (payload.get("message") or {}).get("content", []) if kind == "user" else [payload]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    events += self._tool(block)
                elif block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id") or block.get("id")
                    data = {**self.tools.get(tool_id, {}), "id": tool_id,
                            "status": "error" if block.get("is_error") else "completed",
                            "output": _preview(block.get("content", ""))}
                    self.tools[tool_id] = data
                    events.append(self._event("tool", **data))
        elif kind == "result":
            failed = payload.get("is_error") or str(payload.get("subtype", "")).startswith("error")
            events += self._status("failed" if failed else "completed", "分析失败" if failed else "已完成")
        elif kind == "error":
            events.append(self._event("error", error=_preview(payload.get("error") or payload)))
        return events

    def _stream(self, event):
        kind = event.get("type")
        index = event.get("index", 0)
        if kind == "message_start":
            events = self.flush(force=True)
            self.message_count += 1
            self.message_id = (event.get("message") or {}).get("id") or f"message-{self.message_count}"
            self.blocks = {}
            self.text_indices = {}
            return events
        if kind == "content_block_start":
            events = self.flush(force=True)
            block = dict(event.get("content_block") or {})
            self.blocks[index] = block
            if block.get("type") == "tool_use":
                events += self._tool(block)
            elif block.get("type") in {"thinking", "redacted_thinking"}:
                events += self._status("thinking", "正在分析")
            elif block.get("type") == "text":
                text_id = self._text_id(index)
                if block.get("text"):
                    self.phase = "text"
                    events.append(self._event("text", id=text_id, text=block["text"], mode="append"))
            return events
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                self.phase = "text"
                text_id = self._text_id(index)
                events = self.flush(force=True) if text_id != self.pending_id else []
                self.pending_id = text_id
                self.pending_text += delta.get("text", "")
                return events + self.flush()
            if delta.get("type") == "input_json_delta":
                block = self.blocks.setdefault(index, {})
                block["partial_json"] = block.get("partial_json", "") + delta.get("partial_json", "")
            # Thinking/signature deltas are neither persisted nor forwarded.
            return []
        if kind == "content_block_stop":
            events = self.flush(force=True)
            block = self.blocks.get(index, {})
            if block.get("type") == "tool_use":
                if block.get("partial_json"):
                    try:
                        block["input"] = json.loads(block["partial_json"])
                    except ValueError:
                        pass  # The complete assistant envelope supplies the input.
                events += self._tool(block)
            return events
        if kind == "message_stop":
            return self.flush(force=True)
        return []


def _preview(value):
    if isinstance(value, list):
        value = "\n".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value[:6000] + ("…" if len(value) > 6000 else "")

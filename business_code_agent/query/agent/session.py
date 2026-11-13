"""Project-scoped session state for direct core runs."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage, messages_from_dict, messages_to_dict


SESSION_PREFIX = "DA-"
RETIRED_TOOL_BODY = "[已废弃的历史工具原文；源码可能更新，如结论依赖它请重新 Read。]"


def compact_messages(messages, char_limit=64000):
    """Retire old tool bodies and then drop complete older turns when needed."""
    result = list(messages)
    total = sum(len(str(message.content)) for message in result)
    tool_positions = [index for index, message in enumerate(result) if isinstance(message, ToolMessage)]
    for index in tool_positions[:-3]:
        if total <= char_limit:
            break
        old = result[index]
        total -= max(0, len(str(old.content)) - len(RETIRED_TOOL_BODY))
        result[index] = old.model_copy(update={"content": RETIRED_TOOL_BODY})
    if total > char_limit:
        humans = [index for index, message in enumerate(result) if isinstance(message, HumanMessage)]
        if len(humans) > 1:
            result = result[humans[-1]:]
    return result


class DirectSessionStore:
    """Store model messages outside the per-run workspace."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, session):
        if not session or not session.startswith(SESSION_PREFIX) or not session[3:].isalnum():
            return None
        return self.root / f"{session}.json"

    def load(self, session, scope):
        path = self._path(session)
        if path is None or not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if value.get("scope") != scope:
            return []
        return messages_from_dict(value.get("messages") or [])

    def save(self, session, scope, messages):
        path = self._path(session)
        if path is None:
            return
        humans = [index for index, message in enumerate(messages) if isinstance(message, HumanMessage)]
        start = humans[-6] if len(humans) > 6 else 0
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "scope": scope,
            "messages": messages_to_dict(compact_messages(messages[start:])),
        }, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def retire_changed_sources(messages, files):
    """Drop historical Read bodies whose current file no longer matches."""
    if files is None:
        return list(messages)
    result = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "Read":
            stale = True
            try:
                data = json.loads(message.content)
                file, _ = files.locate(data["path"])
                stale = file.stat().st_mtime_ns != data.get("sourceMtimeNs")
            except (OSError, ValueError, KeyError, TypeError):
                stale = True
            if stale:
                message = message.model_copy(update={"content": RETIRED_TOOL_BODY})
        result.append(message)
    return result

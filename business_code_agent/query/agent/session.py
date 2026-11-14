"""Project-scoped session state for direct core runs."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage, messages_from_dict, messages_to_dict


SESSION_PREFIX = "DA-"
RETIRED_TOOL_BODY = "[已废弃的历史工具原文；源码可能更新，如结论依赖它请重新 Read。]"


def _read_key(path) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("./")


def _read_range(arguments) -> tuple[int, int]:
    try:
        offset = max(1, int(arguments.get("offset") or 1))
    except (TypeError, ValueError):
        offset = 1
    try:
        limit = min(240, max(1, int(arguments.get("limit") or 160)))
    except (TypeError, ValueError):
        limit = 160
    return offset, offset + limit - 1


def _merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered(ranges, start, end) -> bool:
    low, high = sorted((start, end))
    for covered_start, covered_end in ranges:
        if covered_end < low:
            continue
        return covered_start <= low and covered_end >= high
    return False


class ReadLedger:
    """Remember delivered Read ranges for this run, independent of compaction."""

    def __init__(self):
        self._reads = {}

    def cached(self, arguments) -> dict | None:
        if str(arguments.get("path") or "").strip() == "":
            return None
        entry = self._reads.get(_read_key(arguments.get("path")))
        if entry is None:
            return None
        start, end = _read_range(arguments)
        total = entry.get("totalLines")
        if total is not None:
            end = min(end, total)
        if end < start or not _covered(entry["ranges"], start, end):
            return None
        return {
            "path": entry["path"], "lineStart": start, "lineEnd": end,
            "totalLines": total, "cached": True,
            "note": "该区间本轮已读取，原文可能因上下文预算被移除；不要重复读取同一区间，"
                    "请基于已有结论回答或读取尚未覆盖的区间。",
        }

    def record(self, output) -> None:
        try:
            data = json.loads(output)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict) or data.get("error") or not data.get("path"):
            return
        start, end = data.get("lineStart"), data.get("lineEnd")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            return
        path = str(data["path"])
        entry = self._reads.setdefault(_read_key(path), {"path": path, "ranges": [],
                                                        "totalLines": data.get("totalLines")})
        if data.get("totalLines") is not None:
            entry["totalLines"] = data["totalLines"]
        entry["ranges"] = _merge_ranges([*entry["ranges"], (start, end)])


def _retired_tool_content(message) -> str:
    content = str(message.content)
    if content.startswith("[历史读取已移除]") or content.startswith("[历史工具结果已移除]"):
        return content
    if getattr(message, "name", "") == "Read":
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("path") and data.get("lineStart") is not None:
            name = str(data["path"]).replace("\\", "/").rsplit("/", 1)[-1]
            return (f"[历史读取已移除] {name}:{data['lineStart']}-{data.get('lineEnd')}；"
                    "同一区间不会再次返回，勿重复读取。")
    return f"[历史工具结果已移除] {getattr(message, 'name', 'tool')}；勿重复相同调用。"


def compact_messages(messages, char_limit=64000):
    """Retire old tool bodies and then drop complete older turns when needed."""
    result = list(messages)
    total = sum(len(str(message.content)) for message in result)
    tool_positions = [index for index, message in enumerate(result) if isinstance(message, ToolMessage)]
    for index in tool_positions[:-3]:
        if total <= char_limit:
            break
        old = result[index]
        retired = _retired_tool_content(old)
        total -= max(0, len(str(old.content)) - len(retired))
        result[index] = old.model_copy(update={"content": retired})
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
            content = str(message.content)
            if content.startswith("[历史读取已移除]") or content == RETIRED_TOOL_BODY:
                result.append(message)
                continue
            stale = True
            try:
                data = json.loads(content)
                if isinstance(data, dict) and data.get("cached"):
                    result.append(message)
                    continue
                file, _ = files.locate(data["path"])
                stale = file.stat().st_mtime_ns != data.get("sourceMtimeNs")
            except (OSError, ValueError, KeyError, TypeError):
                stale = True
            if stale:
                message = message.model_copy(update={"content": RETIRED_TOOL_BODY})
        result.append(message)
    return result

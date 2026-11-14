"""Project-scoped session state for direct core runs."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage, messages_from_dict, messages_to_dict


SESSION_PREFIX = "DA-"
RETIRED_TOOL_BODY = "[已废弃的历史工具原文；源码可能更新，如结论依赖它请重新 Read。]"
RECOVERIES_PER_PATH = 2
RECOVERIES_PER_RUN = 8


def _read_key(path) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("./")


def _read_payload(content):
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("error") or not data.get("path"):
        return None
    return data


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
    """Remember delivered Read ranges and recover compacted evidence on demand."""

    def __init__(self):
        self._reads = {}
        self._active = {}
        self._recoveries = {}
        self._recovered_total = 0

    def observe(self, messages) -> None:
        """Refresh which delivered Read bodies are still visible in bounded context."""
        active = {}
        for message in messages:
            if not isinstance(message, ToolMessage) or getattr(message, "name", "") != "Read":
                continue
            data = _read_payload(message.content)
            if data is None or not data.get("content"):
                continue
            start, end = data.get("lineStart"), data.get("lineEnd")
            if not isinstance(start, int) or not isinstance(end, int) or end < start:
                continue
            active.setdefault(_read_key(data["path"]), []).append((start, end))
        self._active = {key: _merge_ranges(ranges) for key, ranges in active.items()}

    def cached(self, arguments) -> dict | None:
        if str(arguments.get("path") or "").strip() == "":
            return None
        key = _read_key(arguments.get("path"))
        entry = self._reads.get(key)
        if entry is None:
            return None
        start, end = _read_range(arguments)
        total = entry.get("totalLines")
        if total is not None:
            end = min(end, total)
        if end < start or not _covered(entry["ranges"], start, end):
            return None
        if _covered(self._active.get(key, []), start, end):
            return {
                "path": entry["path"], "lineStart": start, "lineEnd": end,
                "totalLines": total, "cached": True, "contentInContext": True,
                "note": "该区间本轮已读取且原文仍在当前上下文中；请直接引用上文，不要重复读取。",
            }
        recovered = self._recover(key, entry, start, end)
        if recovered is not None:
            return {**recovered, "cached": True, "recovered": True,
                    "contentInContext": True,
                    "note": "该区间原文此前因上下文预算被移除，本次已恢复；"
                            "请基于该原文作答，不要重复读取同一区间。"}
        return {
            "path": entry["path"], "lineStart": start, "lineEnd": end,
            "totalLines": total, "cached": True, "contentInContext": False,
            "evidenceGap": True,
            "note": "该区间原文已因上下文预算移除，且本轮恢复次数已用尽；"
                    "请基于当前上下文中的结论作答，并在答案中明确说明该区间无法复核。",
        }

    def _recover(self, key, entry, start, end):
        if (self._recoveries.get(key, 0) >= RECOVERIES_PER_PATH
                or self._recovered_total >= RECOVERIES_PER_RUN):
            return None
        segments = [segment for segment in entry["segments"]
                    if segment["end"] >= start and segment["start"] <= end]
        if not segments:
            return None
        covered = _merge_ranges((segment["start"], segment["end"]) for segment in segments)
        if not _covered(covered, start, end):
            return None
        covering = next((segment for segment in segments
                         if segment["start"] <= start and segment["end"] >= end), None)
        if covering is not None:
            payload = dict(covering["data"])
        else:
            lines = {}
            for segment in sorted(segments, key=lambda item: item["start"]):
                for raw in str(segment["data"].get("content") or "").splitlines():
                    number, separator, text = raw.partition(": ")
                    if separator and number.isdigit():
                        lines.setdefault(int(number), text)
            payload = {
                **segments[0]["data"], "lineStart": start, "lineEnd": end,
                "content": "\n".join(f"{number}: {lines[number]}" for number in sorted(lines)
                                     if start <= number <= end),
            }
        self._recoveries[key] = self._recoveries.get(key, 0) + 1
        self._recovered_total += 1
        return payload

    def record(self, output) -> None:
        data = _read_payload(output)
        if data is None:
            return
        start, end = data.get("lineStart"), data.get("lineEnd")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            return
        key = _read_key(data["path"])
        entry = self._reads.setdefault(key, {"path": data["path"], "ranges": [],
                                             "segments": [], "totalLines": data.get("totalLines")})
        if data.get("totalLines") is not None:
            entry["totalLines"] = data["totalLines"]
        entry["ranges"] = _merge_ranges([*entry["ranges"], (start, end)])
        entry["segments"].append({"start": start, "end": end, "data": dict(data)})
        self._active[key] = _merge_ranges([*self._active.get(key, []), (start, end)])


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
                    "需要原文时可再次 Read 该区间（受控恢复），恢复次数有限。")
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
                if isinstance(data, dict) and data.get("cached") and not data.get("content"):
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

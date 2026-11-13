"""Bounded conversation state; old tool bodies are explicitly retired."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import ToolMessage, messages_from_dict, messages_to_dict


def compact(messages, char_limit=64000):
    result = list(messages)
    total = sum(len(str(m.content)) for m in result)
    # Preserve complete call/result pairs. Only retire old bodies, never remove
    # a tool response independently of its corresponding assistant tool call.
    tool_positions = [i for i, m in enumerate(result) if isinstance(m, ToolMessage)]
    for i in tool_positions[:-3]:
        if total <= char_limit:
            break
        old = result[i]
        body = "[已废弃的旧工具输出；如结论依赖它，请重新 Read 对应原文。]"
        total -= max(0, len(str(old.content)) - len(body))
        result[i] = old.model_copy(update={"content": body})
    return result


class SessionStore:
    def __init__(self, workspace):
        self.root = Path(workspace) / ".langchain-sessions"

    def load(self, session, scope):
        if not session:
            return []
        if not session.startswith("LC-") or not session[3:].isalnum():
            return []
        path = self.root / f"{session}.json"
        if not path.exists():
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["scope"] != scope:
            return []
        return messages_from_dict(value["messages"])

    def save(self, session, scope, messages):
        # Retain the latest complete turns; never cut in the middle of a tool cycle.
        humans = [i for i, m in enumerate(messages) if m.type == "human"]
        start = humans[-6] if len(humans) > 6 else 0
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "scope": scope,
                    "messages": messages_to_dict(compact(messages[start:])),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


def retire_changed_sources(messages, files):
    result = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.name in {"Read", "SearchCode", "TraceContract", "TraceResponseFields", "TraceBusinessRules"}:
            stale = message.name != "Read"
            if message.name == "Read":
                try:
                    data = json.loads(message.content)
                    file, _ = files.locate(data["path"])
                    stale = file.stat().st_mtime_ns != data.get("sourceMtimeNs")
                except (OSError, ValueError, KeyError):
                    stale = True
            if stale:
                message = message.model_copy(
                    update={
                        "content": "[已废弃的历史检索原文；源码可能更新，请重新 Read 或 SearchCode。]"
                    }
                )
        result.append(message)
    return result


def answer_evidence(messages, char_limit):
    """Only actual Read/SearchCode originals, not search hits or model notes."""
    blocks = {}
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in {
            "Read",
            "SearchCode", "TraceContract", "TraceResponseFields", "TraceBusinessRules",
        }:
            continue
        try:
            data = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if data.get("error"):
            continue
        if message.name in {"TraceContract", "TraceResponseFields", "TraceBusinessRules"}:
            for source in data.get("evidence", []):
                key = (source["path"], source["start"], source["end"])
                blocks[key] = f"[源码] {source['path']}:{source['start']}-{source['end']}\n" + source["content"]
            continue
        if not data.get("content"):
            continue
        if message.name == "Read":
            key = (data["path"], data["lineStart"], data["lineEnd"])
            kind = (
                "需求原文"
                if data["path"].startswith("requirements/")
                else (
                    "业务补充资料" if data["path"].startswith("knowledge/") else "源码"
                )
            )
            header = f"[{kind}] {data['path']}:{data['lineStart']}-{data['lineEnd']}"
            if data.get("truncated"):
                header += " [此后原文未读取]"
            block = header + "\n" + data["content"]
        else:
            key = ("search", data["content"])
            block = data["content"]
        blocks[key] = block
    selected = []
    omitted = 0
    for block in reversed(list(blocks.values())):
        if sum(map(len, selected)) + len(block) + len(selected) * 2 <= char_limit:
            selected.append(block)
        else:
            omitted += 1
    return "\n\n".join(reversed(selected)), omitted

"""Pure Markdown parsing rules for business investigation entry hints.

Kept outside the knowledge domain so query-time navigation can read anchors
without depending on the durable knowledge service.
"""

from __future__ import annotations

import re

ENTRY_TYPES = {"PAGE", "CONTROLLER", "JOB", "CONSUMER", "ENTRY_CLASS", "OTHER"}


class EntryAnchorError(ValueError):
    """Invalid or ambiguous business entry anchor input."""


def validate_entry_name(value: object) -> str:
    """Allow a class/page/job name, never a package, method or file location."""
    name = str(value or "").strip()
    if not name or len(name) > 200:
        raise EntryAnchorError("entryName 不能为空且长度不能超过 200")
    if any(token in name for token in ("/", "\\", "::", "(", ")", ":", "\n", "\r")):
        raise EntryAnchorError(f"entryName 只能是入口名称，不能包含路径或方法签名: {name}")
    if "." in name and not name.lower().endswith((".vue", ".ts", ".tsx", ".js", ".jsx")):
        raise EntryAnchorError(f"entryName 不能保存限定类名或方法名: {name}")
    return name


def extract_entry_anchors(section: str) -> list[dict]:
    """Read explicit application, type and name entries from Markdown."""
    anchors = []
    for line in section.splitlines():
        value = line.strip()
        if not value.startswith(("-", "*", "+")):
            continue
        value = re.sub(r"^[-*+]\s*", "", value).strip()
        parts = [part.strip() for part in value.split("|")]
        if len(parts) != 3:
            continue
        application, entry_type, entry_name = parts
        entry_type = entry_type.upper()
        if entry_type not in ENTRY_TYPES:
            continue
        try:
            validate_entry_name(entry_name)
        except EntryAnchorError:
            continue
        anchors.append({
            "application": application, "entryType": entry_type, "entryName": entry_name,
            "sourceType": "HUMAN", "status": "ACTIVE", "sourceQuote": value,
        })
    return anchors

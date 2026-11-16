"""Optional code navigation documents selected from existing project topology."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .navigation import NAVIGATION_FILE

SYSTEM_GUIDES_MOUNT = "knowledge/system-guides"
_DOCUMENT_ID = re.compile(r"[\w][\w .-]*")
_SUMMARY_HEADING = re.compile(r"^##[ \t]+导航摘要[ \t]*$", re.MULTILINE)
_NEXT_SECTION = re.compile(r"^#{1,2}[ \t]+", re.MULTILINE)
_APP_LOCATIONS_CHARS = 2200
_GUIDE_RULES_CHARS = 2800
_DOC_INLINE_CHARS = 750


@dataclass(frozen=True)
class GuideDocument:
    system_id: str
    application_id: str | None
    text: str

    @property
    def relative_path(self) -> str:
        filename = (f"applications/{self.application_id}.md"
                    if self.application_id else "SYSTEM.md")
        return f"{self.system_id}/{filename}"

    @property
    def workspace_path(self) -> str:
        return f"{SYSTEM_GUIDES_MOUNT}/{self.relative_path}"


def load_system_guides(source: Path, navigation: dict) -> tuple[GuideDocument, ...]:
    """Read only the system and application files selected by navigation."""
    if source.is_symlink() or not source.is_dir():
        return ()
    root = source.resolve()
    candidates = {}
    for app in navigation["applications"]:
        system_id, application_id = str(app["systemId"] or ""), str(app["id"] or "")
        if not _DOCUMENT_ID.fullmatch(system_id):
            continue
        candidates[system_id, None] = None
        if _DOCUMENT_ID.fullmatch(application_id):
            candidates[system_id, application_id] = None
    documents = []
    for system_id, application_id in candidates:
        candidate = GuideDocument(system_id, application_id, "")
        path = root / candidate.relative_path
        try:
            # A link to a different application's file is outside this selection.
            if path.resolve() != path or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if text.strip():
            documents.append(GuideDocument(system_id, application_id, text))
    return tuple(documents)


def render_system_guidance(navigation: dict, documents: tuple[GuideDocument, ...]) -> str:
    """Build a bounded prompt section from locations and authored guidance."""
    apps = navigation["applications"]
    if not apps:
        return ""
    rows = []
    for app in apps:
        name = app["id"] if app["name"] == app["id"] else f"{app['name']}（{app['id']}）"
        rows.append(f"- {app['systemName'] or '未登记系统'} / {name}：{app['mount']} [{app['type']}]")
    locations, clipped = _bounded(rows, _APP_LOCATIONS_CHARS)
    lines = ["## 当前工程导航", "已授权应用与代码位置（来自工程配置）", *locations]
    if clipped:
        lines.append(f"更多应用位置见 {NAVIGATION_FILE}。")
    if documents:
        blocks = []
        for doc in documents:
            label = (f"应用 {doc.application_id} 的特殊规则"
                     if doc.application_id else f"系统 {doc.system_id} 的通用规则")
            blocks.append(f"{label}（按需 read {doc.workspace_path}）\n{_inline_rules(doc.text)}")
        rules, clipped = _bounded(blocks, _GUIDE_RULES_CHARS)
        lines.extend(rules)
        if clipped:
            lines.append(f"其余导航文档可在 {SYSTEM_GUIDES_MOUNT}/ 按需查阅。")
    lines.append("这些位置和规则用于选择调查入口。按当前源码核实实际路径与实现，遇到例外时继续搜索。")
    return "\n".join(lines)


def _inline_rules(text: str) -> str:
    text = text.strip()
    if len(text) <= _DOC_INLINE_CHARS:
        return text
    summary = _SUMMARY_HEADING.search(text)
    if summary:
        excerpt = _NEXT_SECTION.split(text[summary.end():], maxsplit=1)[0].strip() or text
    else:
        excerpt = text
    if len(excerpt) > _DOC_INLINE_CHARS:
        excerpt = excerpt[:_DOC_INLINE_CHARS].rstrip() + "…"
    return excerpt


def _bounded(blocks: list[str], budget: int) -> tuple[list[str], bool]:
    selected = []
    clipped = False
    for block in blocks:
        if len(block) + 1 > budget:
            clipped = True
            continue
        selected.append(block)
        budget -= len(block) + 1
    return selected, clipped

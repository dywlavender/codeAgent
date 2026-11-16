"""Read and save a project's Business Context Markdown."""

from __future__ import annotations

import re
import tempfile
import threading
from pathlib import Path


_save_lock = threading.Lock()
_heading = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
_fence = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class BusinessContextConflict(ValueError):
    """The document changed after the editor opened it."""


class BusinessContextFiles:
    def __init__(self, root: Path, *, editable: bool = True):
        self.root = root.resolve()
        self.editable = editable

    def list(self) -> dict:
        items = sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*.md")
            if path.is_file() and path.resolve() == path
        )
        return {"items": items, "editable": self.editable}

    def read(self, name: str) -> dict:
        return self._document(name, self._path(name).read_bytes().decode("utf-8"))

    def save(self, name: str, *, previous: str | None, content: str) -> dict:
        if not self.editable:
            raise ValueError("历史业务基线只读。请先迁移到业务补充知识目录。")
        if not isinstance(content, str) or (previous is not None and not isinstance(previous, str)):
            raise ValueError("content 必须是文本，previousContent 必须是原文或 null")
        path = self._path(name)
        with _save_lock:
            try:
                current = path.read_bytes().decode("utf-8")
            except FileNotFoundError:
                current = None
            if current != previous:
                raise BusinessContextConflict("文档已更新。请重新打开最新原文，再合并你的修改。")
            if current != content:
                self._replace(path, content)
        return self._document(name, content)

    @staticmethod
    def _document(name: str, content: str) -> dict:
        return {"name": name, "content": content, "sections": markdown_sections(content)}

    def _path(self, name: str) -> Path:
        if (not isinstance(name, str) or not name.endswith(".md")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or any(char in name for char in ("\\", ":", "\0"))):
            raise ValueError("请选择业务补充知识目录内的 Markdown 文件")
        path = self.root.joinpath(*name.split("/"))
        if path.resolve() != path:
            raise ValueError("业务补充知识编辑器仅支持目录内的普通文件")
        return path

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=".context-", suffix=".tmp", delete=False,
        )
        temporary = Path(stream.name)
        try:
            with stream:
                stream.write(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def markdown_sections(content: str) -> list[dict]:
    """Return inclusive line ranges, including each heading's subsections."""
    lines = content.splitlines()
    headings = []
    fence = ""
    for number, line in enumerate(lines, 1):
        marker = _fence.match(line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not line[marker.end():].strip():
                fence = ""
            continue
        if not fence and (match := _heading.match(line.lstrip("\ufeff"))):
            headings.append((number, len(match[1]), match[2]))
    sections = []
    for index, (start, level, title) in enumerate(headings):
        end = next((line - 1 for line, depth, _ in headings[index + 1:] if depth <= level), len(lines))
        sections.append({"title": title, "startLine": start, "endLine": end})
    return sections

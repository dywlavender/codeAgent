"""Read and save a project's Business Context Markdown."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path


_save_lock = threading.Lock()


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
        return {"name": name, "content": self._path(name).read_bytes().decode("utf-8")}

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
        return {"name": name, "content": content}

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

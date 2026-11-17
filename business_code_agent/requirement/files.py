"""Project-scoped raw materials with bounded previews."""

from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from business_code_agent.shared.docparse import RequirementDocumentParser

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
_TEXT = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".xml"}
_upload_lock = threading.Lock()


class RawMaterials:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def list(self) -> list[dict]:
        return [self._metadata(path) for path in sorted(self.root.rglob("*"))
                if path.is_file() and path.resolve() == path]

    def path(self, name: str) -> Path:
        if any(part in {"", ".", ".."} for part in name.split("/")) or any(c in name for c in ("\\", ":", "\0")):
            raise ValueError("请选择资料目录内的文件")
        path = self.root.joinpath(*name.split("/"))
        if path.resolve() != path:
            raise ValueError("请选择资料目录内的普通文件")
        return path

    def preview(self, name: str) -> dict:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        data = self._metadata(path)
        content = None
        if path.suffix.lower() == ".docx":
            content = RequirementDocumentParser().parse(str(path))["original"]
        elif path.suffix.lower() in _TEXT:
            with path.open(encoding="utf-8-sig", errors="replace") as stream:
                content = stream.read(64001)
        return {**data, "content": content[:64000] if content is not None else None,
                "truncated": content is not None and len(content) > 64000}

    def upload(self, name: str, content: bytes) -> dict:
        if "/" in name:
            raise ValueError("上传时请提供单个文件名")
        path = self.path(name)
        with _upload_lock:
            if path.exists():
                raise FileExistsError(name)
            self.root.mkdir(parents=True, exist_ok=True)
            stream = tempfile.NamedTemporaryFile(dir=self.root, delete=False)
            temporary = Path(stream.name)
            try:
                with stream:
                    stream.write(content)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return self._metadata(path)

    def _metadata(self, path: Path) -> dict:
        stat = path.stat()
        return {"name": path.relative_to(self.root).as_posix(), "bytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}

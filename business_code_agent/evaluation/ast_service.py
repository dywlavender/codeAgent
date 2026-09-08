"""Project-scoped, user-triggered AST material management.

AST generation is intentionally separate from query/evaluation setup.  This
service only reads the current status on construction and starts work when
``generate`` is called explicitly by the user.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .ast_docs import generate_ast_documents
from .runner import resolve_project_sources

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _source_stamp(repository_sources: list[tuple[str, Path]]) -> dict:
    """Return a human-auditable source stamp without storing a hash."""
    latest = 0.0
    files = 0
    roots = []
    ignored = {".git", "node_modules", "target", ".venv", "__pycache__", ".data"}
    for repository_id, root in repository_sources:
        roots.append({"id": repository_id, "path": str(root)})
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or ignored.intersection(path.parts):
                continue
            try:
                latest = max(latest, path.stat().st_mtime)
                files += 1
            except OSError:
                continue
    modified = datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None
    return {"fileCount": files, "latestModifiedAt": modified, "repositories": roots}


class AstService:
    STATUSES = {"missing", "generating", "available", "stale", "failed"}

    def __init__(self, *, project_config=None, data_root=None):
        self.project_config = Path(project_config).expanduser().resolve() if project_config else None
        config = _read_json(self.project_config, {}) if self.project_config else {}
        project = config.get("project") if isinstance(config, dict) else None
        self.project_id = str(project.get("id") or "").strip() if isinstance(project, dict) else None
        if data_root:
            self.root = Path(data_root).expanduser().resolve()
        elif self.project_config:
            self.root = self.project_config.parent / ".data" / "ast"
        else:
            self.root = Path.cwd() / ".data" / "ast"
        self.versions_root = self.root / "versions"
        self.index_path = self.root / "index.json"
        self.versions_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}

    def _index(self) -> dict:
        return _read_json(self.index_path, {"status": "missing", "currentVersionId": None})

    def _save_index(self, value: dict) -> None:
        _write_json(self.index_path, value)

    def _read_sources(self):
        if not self.project_config:
            raise ValueError("当前服务未配置项目")
        return resolve_project_sources(self.project_config)[:2]

    def _current_source_stamp(self) -> dict | None:
        try:
            _, repositories, _, _ = resolve_project_sources(self.project_config)
            return _source_stamp(repositories)
        except Exception as exc:
            logger.info("AST 源码状态暂不可读取: %s", exc)
            return None

    def status(self) -> dict:
        value = self._index()
        current_id = value.get("currentVersionId")
        active = next(iter(self._active.values()), None)
        if active and active.get("status") == "generating":
            return {**value, **{key: item for key, item in active.items() if key != "thread"}, "status": "generating"}
        if not current_id:
            return {**value, "status": value.get("status") or "missing"}
        metadata = _read_json(self.versions_root / current_id / "metadata.json", {})
        current_stamp = self._current_source_stamp()
        status = "available"
        if value.get("status") == "failed":
            status = "failed"
        elif current_stamp and metadata.get("sourceStamp") and current_stamp != metadata["sourceStamp"]:
            status = "stale"
        return {
            **value,
            "status": status,
            "currentVersionId": current_id,
            "current": metadata or None,
        }

    def current_version(self) -> tuple[str, dict, Path] | None:
        status = self.status()
        version_id = status.get("currentVersionId")
        if not version_id or status.get("status") != "available":
            return None
        return self.version(version_id)

    def version(self, version_id: str | None) -> tuple[str, dict, Path] | None:
        if not version_id or not re.fullmatch(r"ast-[A-Za-z0-9_-]+", str(version_id)):
            return None
        version_id = str(version_id)
        directory = self.versions_root / version_id / "documents"
        metadata = _read_json(self.versions_root / version_id / "metadata.json", {})
        if not directory.is_dir():
            return None
        return version_id, metadata, directory

    def list_documents(self, version_id: str | None = None) -> dict:
        status = self.status()
        version_id = version_id or status.get("currentVersionId")
        if not version_id:
            return {"versionId": None, "items": []}
        version_info = self.version(version_id)
        if not version_info:
            raise KeyError(version_id)
        version_id, metadata, directory = version_info
        items = []
        for path in sorted(directory.rglob("*.md")):
            relative = path.relative_to(directory).as_posix()
            items.append({"path": relative, "bytes": path.stat().st_size})
        return {"versionId": version_id, "items": items, "metadata": metadata}

    def read_document(self, version_id: str, path: str) -> dict:
        version_info = self.version(version_id)
        if not version_info:
            raise KeyError(version_id)
        version_id, _, directory = version_info
        directory = directory.resolve()
        target = (directory / str(path or "")).resolve()
        if directory not in target.parents or not target.is_file():
            raise KeyError(path)
        return {"versionId": version_id, "path": target.relative_to(directory).as_posix(),
                "content": target.read_text(encoding="utf-8", errors="replace")}

    def generate(self) -> dict:
        with self._lock:
            active = next(iter(self._active.values()), None)
            if active and active.get("status") == "generating":
                return {"generationId": active["generationId"], "status": "generating", "duplicate": True}
            generation_id = f"ast-generation-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            value = {"generationId": generation_id, "status": "generating", "phase": "preparing", "progress": 0,
                     "startedAt": _now(), "error": None}
            self._active[generation_id] = value
            self._save_index({**self._index(), **value, "currentVersionId": self._index().get("currentVersionId")})
            thread = threading.Thread(target=self._run_generation, args=(generation_id,), daemon=True,
                                      name=f"{generation_id}")
            self._active[generation_id]["thread"] = thread
            thread.start()
            return {key: value[key] for key in ("generationId", "status", "phase", "progress", "startedAt")}

    def _run_generation(self, generation_id: str) -> None:
        started = time.monotonic()
        record = self._active[generation_id]
        try:
            record.update(phase="preparing", progress=10)
            _, repository_sources, _, _ = resolve_project_sources(self.project_config)
            source_stamp = _source_stamp(repository_sources)
            with tempfile.TemporaryDirectory(prefix="ast-generation-") as folder:
                inputs = Path(folder) / "inputs"
                inputs.mkdir()
                repository_names = []
                for number, (repository_id, source) in enumerate(repository_sources):
                    name = f"repo-{number}"
                    repository_names.append((repository_id, name))
                    shutil.copytree(source, inputs / name,
                                    ignore=shutil.ignore_patterns(".git", "node_modules", "target", ".venv", "__pycache__"))
                    record.update(phase=f"parsing:{repository_id}", progress=min(75, 20 + number * 15))
                documents = generate_ast_documents(inputs, repository_names)
            version_id = f"ast-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            version_root = self.versions_root / version_id
            number = 1
            while version_root.exists():
                number += 1
                version_id = f"ast-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{number}"
                version_root = self.versions_root / version_id
            document_root = version_root / "documents"
            for name, content in documents.items():
                target = document_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            metadata = {
                "projectId": self.project_id, "versionId": version_id, "generatedAt": _now(),
                "sourceStamp": source_stamp, "sourceRepositories": source_stamp["repositories"],
                "documentCount": len(documents), "generationSeconds": round(time.monotonic() - started, 3),
                "generatorVersion": 2,
            }
            _write_json(version_root / "metadata.json", metadata)
            index = self._index()
            self._save_index({**index, "status": "available", "currentVersionId": version_id,
                              "updatedAt": metadata["generatedAt"], "error": None,
                              "generationId": generation_id, "current": metadata})
            record.update(status="available", phase="completed", progress=100,
                          versionId=version_id, finishedAt=_now(), metadata=metadata)
        except Exception as exc:
            logger.exception("AST 生成失败")
            record.update(status="failed", phase="failed", progress=100, error=str(exc), finishedAt=_now())
            self._save_index({**self._index(), "status": "failed", "generationId": generation_id,
                              "error": str(exc), "updatedAt": _now()})
        finally:
            with self._lock:
                self._active.pop(generation_id, None)

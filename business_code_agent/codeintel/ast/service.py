"""Project-scoped, user-triggered AST material management.

AST generation is intentionally separate from query/evaluation setup.  This
service only reads the current status on construction and starts work when
``generate`` is called explicitly by the user.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .generator import generate_ast_documents
from .material import AstMaterial, source_stamp

logger = logging.getLogger(__name__)

# Generation ids live in this process only. A persisted "generating" index
# without any live generation id belongs to a dead process and can be healed.
_LIVE_GENERATIONS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class AstService(AstMaterial):
    def __init__(self, *, project_config=None, data_root=None):
        super().__init__(project_config=project_config, data_root=data_root)
        self.versions_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self._heal_interrupted()

    def _heal_interrupted(self) -> None:
        """Mark a 'generating' index entry failed when no generation is live.

        The in-process active map dies with the server, so a 'generating'
        index seen without any live generation id belongs to a dead process
        and would otherwise block status forever.
        """
        value = self._index()
        if value.get("status") != "generating" or _LIVE_GENERATIONS:
            return
        self._save_index({**value, "status": "failed",
                          "error": "服务重启中断了上一次生成，请重新生成",
                          "updatedAt": _now()})

    def _save_index(self, value: dict) -> None:
        _write_json(self.index_path, value)

    def status(self) -> dict:
        active = next(iter(self._active.values()), None)
        if active and active.get("status") == "generating":
            return {**self._index(), **{key: item for key, item in active.items() if key != "thread"}}
        return super().status()

    def current_version(self) -> tuple[str, dict, Path] | None:
        status = self.status()
        version_id = status.get("currentVersionId")
        if not version_id or status.get("status") != "available":
            return None
        return self.version(version_id)

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
            _LIVE_GENERATIONS.add(generation_id)
            self._save_index({**self._index(), **value, "currentVersionId": self._index().get("currentVersionId")})
            thread = threading.Thread(target=self._run_generation, args=(generation_id,), daemon=True,
                                      name=f"{generation_id}")
            self._active[generation_id]["thread"] = thread
            thread.start()
            return {key: value[key] for key in ("generationId", "status", "phase", "progress", "startedAt")}

    def _run_generation(self, generation_id: str) -> None:
        started = time.monotonic()
        record = self._active[generation_id]
        version_root: Path | None = None
        try:
            record.update(phase="preparing", progress=10)
            repository_sources = self._source_repositories()
            stamp_value = source_stamp(repository_sources,
                                       cache_path=self.root / "source-stamp-cache.json")
            version_id, version_root = self._allocate_version_root()
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
                documents = generate_ast_documents(inputs, repository_names,
                                                   db_path=version_root / "index.db")
            document_root = version_root / "documents"
            for name, content in documents.items():
                target = document_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            metadata = {
                "projectId": self.project_id, "versionId": version_id, "generatedAt": _now(),
                "sourceStamp": stamp_value, "sourceRepositories": stamp_value["repositories"],
                "repositoryMap": {snapshot: repository for repository, snapshot in repository_names},
                "documentCount": len(documents), "generationSeconds": round(time.monotonic() - started, 3),
                "generatorVersion": 3, "indexDb": "index.db",
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
            if version_root is not None:
                shutil.rmtree(version_root, ignore_errors=True)
            record.update(status="failed", phase="failed", progress=100, error=str(exc), finishedAt=_now())
            self._save_index({**self._index(), "status": "failed", "generationId": generation_id,
                              "error": str(exc), "updatedAt": _now()})
        finally:
            _LIVE_GENERATIONS.discard(generation_id)
            with self._lock:
                self._active.pop(generation_id, None)

    def _allocate_version_root(self) -> tuple[str, Path]:
        """Reserve one unused version directory before generation starts.

        Allocating first lets the persisted index database be written straight
        into its final version directory instead of a temporary location.
        """
        version_id = f"ast-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        version_root = self.versions_root / version_id
        number = 1
        while version_root.exists():
            number += 1
            version_id = f"ast-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{number}"
            version_root = self.versions_root / version_id
        version_root.mkdir(parents=True, exist_ok=True)
        return version_id, version_root

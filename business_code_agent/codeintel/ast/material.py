"""Read the metadata and current state of manually generated AST versions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from business_code_agent.platform.repositories import configured_repository_sources


STAMP_CACHE_VERSION = 1
STAMP_IGNORED_PARTS = {".git", "node_modules", "target", ".venv", "__pycache__", ".data"}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _iso_mtime(latest: float) -> str | None:
    return datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None


def source_stamp(repositories: list[tuple[str, Path]], *, cache_path=None,
                 content_digest: bool = True) -> dict:
    """Return a comparable source stamp for the given repositories.

    ``content_digest=True`` aggregates per-file SHA-256 digests so touching a
    file no longer flips the stamp while real content changes always do. The
    ``cache_path`` JSON stores per-file ``(size, mtime_ns, sha256)`` entries so
    repeated reads only hash files whose size or mtime changed. The legacy
    ``content_digest=False`` shape (file count + latest mtime) is kept for
    comparing against versions generated before digests existed.
    """
    latest = 0.0
    files = 0
    roots = []
    digests = []
    cache = _read_json(cache_path, None) if cache_path else None
    if not isinstance(cache, dict) or cache.get("version") != STAMP_CACHE_VERSION:
        cache = {"version": STAMP_CACHE_VERSION, "repos": {}}
    repo_cache = cache["repos"]
    cache_dirty = False
    for repository_id, root in repositories:
        roots.append({"id": repository_id, "path": str(root)})
        if not root.is_dir():
            continue
        cached_files = (repo_cache.get(str(root)) or {}).get("files") or {}
        entries = {}
        for path in root.rglob("*"):
            if not path.is_file() or STAMP_IGNORED_PARTS.intersection(path.parts):
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            files += 1
            latest = max(latest, stat.st_mtime)
            cached = cached_files.get(relative)
            if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                sha = cached[2]
            else:
                try:
                    sha = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
                cache_dirty = True
            entries[relative] = [stat.st_size, stat.st_mtime_ns, sha]
        payload = "".join(f"{name}\x1f{entry[2]}\x1e" for name, entry in sorted(entries.items()))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        digests.append(f"{root}\x1f{digest}")
        if repo_cache.get(str(root), {}).get("files") != entries:
            repo_cache[str(root)] = {"files": entries, "digest": digest}
            cache_dirty = True
    stamp = {"fileCount": files, "latestModifiedAt": _iso_mtime(latest), "repositories": roots}
    if content_digest:
        stamp["contentDigest"] = (hashlib.sha256("".join(sorted(digests)).encode("utf-8")).hexdigest()
                                  if digests else None)
    if cache_dirty and cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return stamp


def current_stamp(saved_stamp, repositories: list[tuple[str, Path]], cache_path=None) -> dict:
    """Compute the stamp comparable against one saved AST metadata stamp.

    Versions generated before content digests existed keep comparing via the
    legacy mtime stamp, so their status does not change until regeneration.
    """
    if saved_stamp and "contentDigest" in saved_stamp:
        return source_stamp(repositories, cache_path=cache_path, content_digest=True)
    return source_stamp(repositories, cache_path=cache_path, content_digest=False)


class AstMaterial:
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

    def _index(self) -> dict:
        return _read_json(self.index_path, {"status": "missing", "currentVersionId": None})

    def status(self) -> dict:
        """Report the snapshot state, re-checking the source stamp saved at generation."""
        value = self._index()
        current_id = value.get("currentVersionId")
        if not current_id or value.get("status") in {"generating", "failed"}:
            return {**value, "status": value.get("status") or "missing"}
        version = self.version(current_id)
        if version is None:
            return {**value, "status": "missing", "current": None}
        _, metadata, _ = version
        saved_stamp = metadata.get("sourceStamp")
        status = "stale" if saved_stamp and self._current_source_stamp(saved_stamp) != saved_stamp else "available"
        return {**value, "status": status, "currentVersionId": current_id, "current": metadata or None}

    def _source_repositories(self, saved_stamp=None) -> list[tuple[str, Path]]:
        if self.project_config:
            config = _read_json(self.project_config, {})
            repositories = configured_repository_sources(config, self.project_config)
        else:
            roots = (saved_stamp or {}).get("repositories") or []
            repositories = [(item["id"], Path(item["path"])) for item in roots]
        if not repositories or any(not path.is_dir() for _, path in repositories):
            raise ValueError("请先准备项目配置中的本地代码仓库")
        return repositories

    def _current_source_stamp(self, saved_stamp=None) -> dict | None:
        try:
            return current_stamp(saved_stamp, self._source_repositories(saved_stamp),
                                 cache_path=self.root / "source-stamp-cache.json")
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def version(self, version_id: str | None) -> tuple[str, dict, Path] | None:
        if not version_id or not re.fullmatch(r"ast-[A-Za-z0-9_-]+", str(version_id)):
            return None
        version_id = str(version_id)
        directory = self.versions_root / version_id / "documents"
        metadata = _read_json(self.versions_root / version_id / "metadata.json", {})
        if not directory.is_dir():
            return None
        return version_id, metadata, directory

"""Describe the file roots authorized for one query turn."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


MATERIAL_KINDS = ("source", "requirements", "business_context", "code_map", "ast")
BUSINESS_CONTEXT_MOUNT = "knowledge/business-context"
_PURPOSES = {
    "source": "核实当前实现，README 提供项目背景",
    "requirements": "核对需求目标和验收约束",
    "business_context": "解释项目术语、职责边界和业务约定",
    "code_map": "定位代码目录、文件和符号",
    "ast": "定位手动生成的版本化结构资料",
}
_BASE_MATERIALS = {"source", "requirements"}
_MODE_MATERIALS = {
    "none": _BASE_MATERIALS,
    "backbone": _BASE_MATERIALS | {"business_context"},
    "ast": _BASE_MATERIALS | {"ast"},
}


@dataclass(frozen=True)
class MaterialSource:
    kind: str
    label: str
    path: str
    directory: Path | None
    status: str
    version: str | None = None
    repository_id: str | None = None

    @property
    def available(self) -> bool:
        return self.directory is not None

    def to_dict(self) -> dict:
        value = {
            "kind": self.kind, "label": self.label, "path": self.path,
            "directory": str(self.directory) if self.available else None,
            "purpose": _PURPOSES[self.kind], "status": self.status,
            "available": self.available, "version": self.version,
        }
        if self.repository_id:
            value["repositoryId"] = self.repository_id
        return value


@dataclass(frozen=True)
class MaterialManifest:
    sources: tuple[MaterialSource, ...]

    @classmethod
    def from_workspace(cls, workspace, repositories=None, *, allowed=MATERIAL_KINDS,
                       mode=None, ast_version=None, ast_status=None, versions=True):
        root = Path(workspace)
        kinds = set(allowed) & _MODE_MATERIALS.get(mode, set(MATERIAL_KINDS))
        context = "knowledge/business-context"
        context_label = "业务补充知识"
        if not (root / context).is_dir() and (root / "knowledge/baseline").is_dir():
            context = "knowledge/baseline"
            context_label = "业务基线"
        code_map = "knowledge/generated-code-map"
        sources = []

        def add(kind, label, path, version=None, unavailable="missing",
                repository_id=None):
            if kind in kinds:
                candidate = root / path
                directory = candidate.resolve() if candidate.is_dir() else None
                if versions and version is None and directory is not None:
                    if kind == "source":
                        version = _source_revision(directory)
                    elif kind in {"business_context", "requirements", "code_map"}:
                        version = _directory_revision(directory)
                sources.append(MaterialSource(kind, label, path, directory,
                                              "available" if directory else unavailable,
                                              version, repository_id))

        add("business_context", context_label, context)
        add("code_map", "自动代码地图", code_map)
        # The historical AST-only workspace uses the code map mount.
        add("ast", "AST 结构资料", code_map if mode == "ast" else "knowledge/ast",
            ast_version, ast_status or "missing")
        add("requirements", "需求原文", "requirements")
        if "source" in kinds:
            repos = root / "repos"
            entries = sorted(repos.iterdir()) if repos.is_dir() else []
            for entry in entries:
                if entry.is_dir() and (repositories is None or entry.name in repositories):
                    add("source", f"代码仓库 {entry.name}", f"repos/{entry.name}",
                        repository_id=entry.name)
            if not any(source.kind == "source" for source in sources):
                sources.append(MaterialSource("source", "代码仓库", "repos", None, "missing"))
        return cls(tuple(sources))

    @classmethod
    def for_core(cls, workspace, repositories, *, include_requirements=True,
                 include_business_context=False) -> "MaterialManifest":
        """Build the core authorization list from configured repositories.

        ``repositories`` contains every expected ``(repository_id, mount)``
        from project configuration plus the normalized scope. Entries are never
        derived by listing the workspace, so a missing authorized repository
        stays visible instead of being silently omitted. Optional business
        context is mounted only when the selected profile authorizes it.
        """
        root = Path(workspace)
        sources = []
        for repository_id, mount in repositories:
            mount_path = PurePosixPath(str(mount))
            if mount_path.is_absolute() or ".." in mount_path.parts:
                raise ValueError(f"仓库挂载路径不合法：{mount}")
            candidate = root / mount
            directory = candidate.resolve() if candidate.is_dir() else None
            sources.append(MaterialSource(
                "source", f"代码仓库 {repository_id}", str(mount), directory,
                "available" if directory else "missing", repository_id=repository_id,
            ))
        if include_requirements:
            candidate = root / "requirements"
            directory = candidate.resolve() if candidate.is_dir() else None
            sources.append(MaterialSource(
                "requirements", "需求原文", "requirements", directory,
                "available" if directory else "missing",
            ))
        if include_business_context:
            candidate = root / BUSINESS_CONTEXT_MOUNT
            directory = candidate.resolve() if candidate.is_dir() else None
            version = _directory_revision(directory) if directory else None
            sources.append(MaterialSource(
                "business_context", "业务补充知识", BUSINESS_CONTEXT_MOUNT,
                directory if version else None,
                "available" if version else "missing", version,
            ))
        return cls(tuple(sources))

    def missing_required(self) -> list[MaterialSource]:
        return [source for source in self.sources if source.kind == "source" and not source.available]

    def readable(self) -> list[MaterialSource]:
        return [source for source in self.sources if source.available]

    def labelled_roots(self) -> list[tuple[str, Path]]:
        return [(source.label, source.directory) for source in self.readable()]

    def roots(self) -> list[tuple[str, Path]]:
        return [(source.path, source.directory) for source in self.readable()]


def _directory_revision(directory: Path) -> str | None:
    """Hash business document names and contents for session invalidation."""
    if not directory.is_dir():
        return None
    digest = hashlib.sha256()
    count = 0
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
        count += 1
    return f"sha256:{digest.hexdigest()[:16]}" if count else None


_SOURCE_IGNORED_DIRS = {
    ".git", ".idea", ".gradle", ".venv", "venv", "__pycache__",
    "node_modules", "target", "build", "dist", "out",
}


def _source_revision(directory: Path) -> str | None:
    """Cheap repository revision from names, sizes and mtimes.

    Source repositories can be large, so session invalidation uses metadata
    rather than reading every file. Business and requirement documents keep
    the content hash above.
    """
    if not directory.is_dir():
        return None
    digest = hashlib.sha256()
    count = 0
    for current, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.startswith(".") and name not in _SOURCE_IGNORED_DIRS)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\0")
            count += 1
    return f"meta-sha256:{digest.hexdigest()[:16]}" if count else None

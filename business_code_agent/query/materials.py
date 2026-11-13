"""Describe the file roots authorized for one query turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MATERIAL_KINDS = ("source", "requirements", "business_context", "code_map", "ast")
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

    @property
    def available(self) -> bool:
        return self.directory is not None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "path": self.path,
            "directory": str(self.directory) if self.available else None,
            "purpose": _PURPOSES[self.kind], "status": self.status,
            "available": self.available, "version": self.version,
        }


@dataclass(frozen=True)
class MaterialManifest:
    sources: tuple[MaterialSource, ...]

    @classmethod
    def from_workspace(cls, workspace, repositories=None, *, allowed=MATERIAL_KINDS,
                       mode=None, ast_version=None, ast_status=None):
        root = Path(workspace)
        kinds = set(allowed) & _MODE_MATERIALS.get(mode, set(MATERIAL_KINDS))
        context = "knowledge/business-context"
        context_label = "业务补充知识"
        if not (root / context).is_dir() and (root / "knowledge/baseline").is_dir():
            context = "knowledge/baseline"
            context_label = "业务基线"
        code_map = "knowledge/generated-code-map"
        sources = []

        def add(kind, label, path, version=None, unavailable="missing"):
            if kind in kinds:
                candidate = root / path
                directory = candidate.resolve() if candidate.is_dir() else None
                sources.append(MaterialSource(kind, label, path, directory,
                                              "available" if directory else unavailable, version))

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
                    add("source", f"代码仓库 {entry.name}", f"repos/{entry.name}")
            if not any(source.kind == "source" for source in sources):
                sources.append(MaterialSource("source", "代码仓库", "repos", None, "missing"))
        return cls(tuple(sources))

    def readable(self) -> list[MaterialSource]:
        return [source for source in self.sources if source.available]

    def labelled_roots(self) -> list[tuple[str, Path]]:
        return [(source.label, source.directory) for source in self.readable()]

    def roots(self) -> list[tuple[str, Path]]:
        return [(source.path, source.directory) for source in self.readable()]

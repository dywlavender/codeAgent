"""Project registration and runtime boundaries.

The application historically accepted one ``project.config.json`` and one
database when it started.  This module adds the platform-level boundary
without changing that compatibility path: a registry maps a stable project
id to a configuration file and an independent data root, while
``ProjectContext`` gives services one place to obtain all project-owned
paths.

The registry deliberately stores paths and metadata only.  Knowledge,
conversations, AST versions and evaluations remain in the selected project's
database/data root; the registry is not a second source of business data.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


class ProjectRegistryError(ValueError):
    """The project registry or one of its entries is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _safe_project_id(value: object) -> str:
    project_id = str(value or "").strip()
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectRegistryError(
            "project.id 只能包含字母、数字、下划线和连字符，且长度不超过 81 个字符"
        )
    return project_id


@dataclass(frozen=True)
class ProjectContext:
    """Immutable paths and metadata for one registered project."""

    project_id: str
    project_name: str
    config_path: Path | None
    data_root: Path
    db_path: Path
    registered: bool = False

    @property
    def knowledge_root(self) -> Path:
        return self.data_root / "knowledge"

    @property
    def requirements_root(self) -> Path:
        return self.data_root / "requirements"

    @property
    def workspace_root(self) -> Path:
        # Keep the historical compatibility directory name for the old
        # single-project API. Registered projects use the platform layout.
        return self.data_root / ("workspaces" if self.registered else "agent-workspaces")

    @property
    def ast_root(self) -> Path:
        return self.data_root / "ast"

    @property
    def evaluation_suites_root(self) -> Path:
        return self.data_root / "evaluation-suites"

    @property
    def evaluations_root(self) -> Path:
        return self.data_root / "evaluations"

    @property
    def snapshots_root(self) -> Path:
        return self.data_root / "snapshots"

    def ensure_layout(self) -> None:
        """Create project-owned directories, never generate knowledge/AST."""
        self.data_root.mkdir(parents=True, exist_ok=True)
        for path in (
            self.knowledge_root,
            self.requirements_root,
            self.workspace_root,
            self.ast_root,
            self.evaluation_suites_root,
            self.evaluations_root,
            self.snapshots_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.project_id,
            "name": self.project_name,
            "configPath": str(self.config_path) if self.config_path else None,
            "dataRoot": str(self.data_root),
            "database": str(self.db_path),
            "registered": self.registered,
        }

    @classmethod
    def legacy(
        cls,
        db_path: str | Path,
        project_config: str | Path | None = None,
    ) -> "ProjectContext":
        """Build the compatibility context used by the old single-project CLI."""
        config_path = Path(project_config).expanduser().resolve() if project_config else None
        project_id = "default"
        project_name = "本地工程"
        if config_path and config_path.is_file():
            payload = _read_json(config_path, {})
            project = payload.get("project") if isinstance(payload, dict) else None
            if isinstance(project, Mapping):
                project_id = str(project.get("id") or project_id).strip() or project_id
                project_name = str(project.get("name") or project_id).strip() or project_id
        database = Path(db_path).expanduser()
        if not database.is_absolute():
            database = database.resolve()
        # Keep the old layout exactly when the compatibility API is used.
        data_root = config_path.parent / ".data" if config_path else database.parent
        return cls(project_id, project_name, config_path, data_root.resolve(), database, False)


class ProjectRegistry:
    """Persistent registry for multiple independent project contexts."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "registry.json"
        self.projects_root = self.root / "projects"
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def _payload(self) -> dict[str, Any]:
        value = _read_json(self.path, {"version": 1, "projects": []})
        if not isinstance(value, dict):
            raise ProjectRegistryError("工程注册表必须是 JSON 对象")
        projects = value.get("projects", [])
        if not isinstance(projects, list):
            raise ProjectRegistryError("工程注册表 projects 必须是数组")
        return {"version": int(value.get("version") or 1), "projects": projects}

    def list(self) -> list[dict[str, Any]]:
        result = []
        seen: set[str] = set()
        for raw in self._payload()["projects"]:
            if not isinstance(raw, dict):
                continue
            project_id = _safe_project_id(raw.get("id"))
            if project_id in seen:
                raise ProjectRegistryError(f"工程 id 重复: {project_id}")
            seen.add(project_id)
            context = self._context_from_record(raw)
            result.append({**context.to_dict(), "registeredAt": raw.get("registeredAt"),
                           "updatedAt": raw.get("updatedAt")})
        return sorted(result, key=lambda item: (str(item.get("name") or ""), item["id"]))

    def register(
        self,
        config_path: str | Path,
        *,
        data_root: str | Path | None = None,
        name: str | None = None,
    ) -> ProjectContext:
        config = Path(config_path).expanduser().resolve()
        payload = _read_json(config, None)
        if not isinstance(payload, dict):
            raise ProjectRegistryError(f"项目配置无法读取: {config}")
        project = payload.get("project")
        if not isinstance(project, dict):
            raise ProjectRegistryError("项目配置缺少 project 对象")
        project_id = _safe_project_id(project.get("id"))
        project_name = str(name or project.get("name") or project_id).strip() or project_id
        records = self._payload()["projects"]
        existing = next((item for item in records if isinstance(item, dict) and item.get("id") == project_id), None)
        now = _now()
        if data_root is None and existing:
            data_root = existing.get("dataRoot")
        root = Path(data_root).expanduser() if data_root else self.projects_root / project_id
        if not root.is_absolute():
            root = (self.root / root).resolve()
        else:
            root = root.resolve()
        for item in records:
            if not isinstance(item, dict) or item.get("id") == project_id:
                continue
            other_value = str(item.get("dataRoot") or "").strip()
            other_root = Path(other_value).expanduser() if other_value else self.projects_root / str(item.get("id"))
            if not other_root.is_absolute():
                other_root = (self.root / other_root).resolve()
            if other_root.resolve() == root:
                raise ProjectRegistryError(f"工程数据目录已被其他工程占用: {root}")
        context = ProjectContext(project_id, project_name, config, root, root / "knowledge.db", True)
        context.ensure_layout()
        _seed_project_materials(payload, config.parent, context)
        record = {
            "id": project_id,
            "name": project_name,
            "configPath": str(config),
            "dataRoot": str(root),
            "registeredAt": (existing or {}).get("registeredAt") or now,
            "updatedAt": now,
        }
        if existing:
            index = records.index(existing)
            records[index] = record
        else:
            records.append(record)
        _write_json(self.path, {"version": 1, "projects": records})
        _write_json(root / "project.json", record)
        return context

    def get(self, project_id: str) -> ProjectContext:
        project_id = _safe_project_id(project_id)
        for raw in self._payload()["projects"]:
            if isinstance(raw, dict) and raw.get("id") == project_id:
                context = self._context_from_record(raw)
                context.ensure_layout()
                return context
        raise KeyError(project_id)

    def default(self, project_id: str | None = None) -> ProjectContext:
        if project_id:
            return self.get(project_id)
        projects = self.list()
        if not projects:
            raise ProjectRegistryError("工程注册表为空，请先登记工程")
        return self.get(projects[0]["id"])

    def _context_from_record(self, record: Mapping[str, Any]) -> ProjectContext:
        project_id = _safe_project_id(record.get("id"))
        config_value = str(record.get("configPath") or "").strip()
        config = Path(config_value).expanduser() if config_value else None
        if config and not config.is_absolute():
            config = (self.root / config).resolve()
        root_value = str(record.get("dataRoot") or "").strip()
        root = Path(root_value).expanduser() if root_value else self.projects_root / project_id
        if not root.is_absolute():
            root = (self.root / root).resolve()
        return ProjectContext(
            project_id,
            str(record.get("name") or project_id).strip() or project_id,
            config.resolve() if config else None,
            root.resolve(),
            (root / "knowledge.db").resolve(),
            True,
        )


__all__ = ["ProjectContext", "ProjectRegistry", "ProjectRegistryError", "PROJECT_ID_RE"]


def _seed_project_materials(payload: Mapping[str, Any], config_parent: Path, context: ProjectContext) -> None:
    """Seed managed text materials once, without overwriting an existing copy.

    Repositories remain external live sources.  Baseline and requirement
    documents are small, user-managed project materials, so an initial copy
    gives a newly registered project an independent starting point while
    keeping re-registration non-destructive.
    """
    knowledge = payload.get("knowledge") if isinstance(payload, dict) else None
    baseline_value = knowledge.get("baselineRoot") if isinstance(knowledge, dict) else None
    requirements = payload.get("requirements") if isinstance(payload, dict) else None
    requirements_value = payload.get("requirementsRoot") or payload.get("requirementRoot")
    if requirements_value is None and isinstance(requirements, dict):
        requirements_value = requirements.get("root")

    sources = (
        (_resolve_config_path(config_parent, baseline_value or "knowledge/baseline"), context.knowledge_root / "baseline"),
        (_resolve_config_path(config_parent, requirements_value or "requirements"), context.requirements_root),
    )
    for source, target in sources:
        if not source.is_dir() or source.resolve() == target.resolve():
            continue
        try:
            if target.exists() and any(target.iterdir()):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
        except OSError as exc:
            raise ProjectRegistryError(f"无法初始化工程资料目录 {target}: {exc}") from exc


def _resolve_config_path(config_parent: Path, value: object) -> Path:
    candidate = Path(str(value or "")).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (config_parent / candidate).resolve()

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from sqlite3 import Connection


APPLICATION_TYPES = {"FRONTEND", "BACKEND", "JOB", "GATEWAY"}


class ApplicationConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SystemConfig:
    system_id: str
    name: str


@dataclass(frozen=True)
class ApplicationConfig:
    application_id: str
    name: str
    system_id: str
    repository_id: str
    source_root: str
    app_type: str
    language: str = ""
    framework: str = ""


def load_application_config(
    config_path: str | Path,
    project: dict,
    repository_ids: set[str],
) -> tuple[list[SystemConfig], list[ApplicationConfig]]:
    path = Path(config_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationConfigError(f"项目接入拓扑无法读取: {exc}") from exc

    raw_systems = payload.get("systems")
    if raw_systems is None:
        systems = [SystemConfig(str(project["id"]), str(project.get("name") or project["id"]))]
    elif not isinstance(raw_systems, list) or not raw_systems:
        raise ApplicationConfigError("systems 必须是非空数组")
    else:
        systems = []
        for index, item in enumerate(raw_systems):
            if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                raise ApplicationConfigError(f"systems[{index}] 缺少 id")
            system_id = str(item["id"]).strip()
            systems.append(SystemConfig(system_id, str(item.get("name") or system_id).strip()))
    _assert_unique([item.system_id for item in systems], "system id")
    system_ids = {item.system_id for item in systems}

    raw_applications = payload.get("applications")
    if raw_applications is None:
        default_system = systems[0].system_id
        applications = [
            ApplicationConfig(repo_id, repo_id, default_system, repo_id, ".", "BACKEND", "mixed", "")
            for repo_id in sorted(repository_ids)
        ]
    elif not isinstance(raw_applications, list) or not raw_applications:
        raise ApplicationConfigError("applications 必须是非空数组")
    else:
        applications = []
        for index, item in enumerate(raw_applications):
            if not isinstance(item, dict):
                raise ApplicationConfigError(f"applications[{index}] 必须是对象")
            application_id = str(item.get("id") or "").strip()
            repository_id = str(item.get("repositoryId") or "").strip()
            system_id = str(item.get("systemId") or "").strip()
            app_type = str(item.get("type") or item.get("appType") or "").strip().upper()
            if not application_id:
                raise ApplicationConfigError(f"applications[{index}] 缺少 id")
            if repository_id not in repository_ids:
                raise ApplicationConfigError(f"应用 {application_id} 引用了未知仓库: {repository_id}")
            if system_id not in system_ids:
                raise ApplicationConfigError(f"应用 {application_id} 引用了未知系统: {system_id}")
            if app_type not in APPLICATION_TYPES:
                raise ApplicationConfigError(
                    f"应用 {application_id} 的 type 必须是 {', '.join(sorted(APPLICATION_TYPES))}"
                )
            source_root = _source_root(item.get("sourceRoot") or ".")
            applications.append(ApplicationConfig(
                application_id, str(item.get("name") or application_id).strip(),
                system_id, repository_id, source_root, app_type,
                str(item.get("language") or "").strip().lower(),
                str(item.get("framework") or "").strip().lower(),
            ))
    _assert_unique([item.application_id for item in applications], "application id")
    _assert_unique(
        [f"{item.repository_id}:{item.source_root}" for item in applications],
        "application repository/sourceRoot",
    )
    return systems, applications


class ApplicationTopologyStore:
    def __init__(self, db: Connection):
        self.db = db

    def replace(self, systems: list[SystemConfig], applications: list[ApplicationConfig]) -> dict[str, int]:
        self.db.execute("DELETE FROM cross_application_edge")
        self.db.execute("DELETE FROM application_code_file")
        # Entry anchors reference application IDs and must survive a code
        # re-index. Replacing application rows with DELETE would violate that
        # foreign key and make every anchor disappear on refresh, so upsert the
        # configured topology instead.
        application_ids = {item.application_id for item in applications}
        self.db.execute("UPDATE application SET status='DEPRECATED'")
        self.db.execute("UPDATE software_system SET status='DEPRECATED'")
        self.db.executemany(
            """INSERT INTO software_system(id,name,status) VALUES (?,?,'ACTIVE')
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='ACTIVE'""",
            [(item.system_id, item.name) for item in systems],
        )
        self.db.executemany(
            """INSERT INTO application(
                   id,name,system_id,repository_id,source_root,app_type,language,framework,status
                 ) VALUES (?,?,?,?,?,?,?,?,'ACTIVE')
                 ON CONFLICT(id) DO UPDATE SET name=excluded.name,system_id=excluded.system_id,
                   repository_id=excluded.repository_id,source_root=excluded.source_root,
                   app_type=excluded.app_type,language=excluded.language,framework=excluded.framework,
                   status='ACTIVE'""",
            [(
                item.application_id, item.name, item.system_id, item.repository_id,
                item.source_root, item.app_type, item.language, item.framework,
            ) for item in applications],
        )
        # Keep removed application rows for historical references, but retire
        # their anchors so runtime resolution cannot use a stale application.
        if application_ids:
            marks = ",".join("?" for _ in application_ids)
            self.db.execute(
                f"UPDATE business_entry_anchor SET status='DEPRECATED',updated_at=? WHERE application_id NOT IN ({marks})",
                (datetime.now(timezone.utc).isoformat(), *application_ids),
            )
        mapped = self._map_files(applications)
        self.db.commit()
        return {"systems": len(systems), "applications": len(applications), "mappedFiles": mapped}

    def _map_files(self, applications: list[ApplicationConfig]) -> int:
        by_repository: dict[str, list[ApplicationConfig]] = {}
        for application in applications:
            by_repository.setdefault(application.repository_id, []).append(application)
        mapped = 0
        for row in self.db.execute("SELECT id,repository_id,path FROM code_file"):
            matches = [
                item for item in by_repository.get(row["repository_id"], [])
                if _contains(item.source_root, str(row["path"]))
            ]
            if not matches:
                continue
            longest = max(len(item.source_root) for item in matches)
            for item in matches:
                if len(item.source_root) != longest:
                    continue
                self.db.execute(
                    "INSERT INTO application_code_file(application_id,file_id) VALUES (?,?)",
                    (item.application_id, row["id"]),
                )
                mapped += 1
        return mapped


def _source_root(value: object) -> str:
    raw = str(value).strip().replace("\\", "/") or "."
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ApplicationConfigError(f"sourceRoot 必须是仓库内相对路径: {raw}")
    normalized = str(path).strip("/")
    return normalized or "."


def _contains(source_root: str, file_path: str) -> bool:
    if source_root == ".":
        return True
    normalized = file_path.replace("\\", "/").lstrip("/")
    return normalized == source_root or normalized.startswith(source_root.rstrip("/") + "/")


def _assert_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ApplicationConfigError(f"{label} 重复: {value}")
        seen.add(value)

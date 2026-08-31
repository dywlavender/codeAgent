from __future__ import annotations

import logging

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .application_topology import ApplicationTopologyStore, load_application_config
from .indexing import CodeIndexer
from .integration_edges import IntegrationEdgeResolver
from .schema import connect


logger = logging.getLogger(__name__)

class ProjectSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepositoryConfig:
    repository_id: str
    git_url: str
    local_path: Path
    branch: str | None = None


def load_project_config(config_path: str | Path) -> tuple[dict, list[RepositoryConfig]]:
    path = Path(config_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectSyncError(f"项目配置无法读取: {exc}") from exc

    project = payload.get("project")
    if not isinstance(project, dict) or not str(project.get("id", "")).strip():
        raise ProjectSyncError("项目配置缺少 project.id")

    items = payload.get("repositories")
    if not isinstance(items, list) or not items:
        raise ProjectSyncError("项目配置至少需要一个 repositories 条目")

    default_root = payload.get("repositoryRoot", ".data/repositories")
    root = _resolve_from_config(path, default_root)
    repositories: list[RepositoryConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ProjectSyncError(f"repositories[{index}] 必须是对象")
        repository_id = str(item.get("id", "")).strip()
        git_url = _resolve_git_url(path, str(item.get("gitUrl", "")).strip())
        if not repository_id:
            raise ProjectSyncError(f"repositories[{index}] 缺少 id")
        if repository_id in seen:
            raise ProjectSyncError(f"仓库 id 重复: {repository_id}")
        if not git_url:
            raise ProjectSyncError(f"仓库 {repository_id} 缺少 gitUrl")
        seen.add(repository_id)
        local_value = item.get("localPath")
        local_path = _resolve_from_config(path, local_value) if local_value else (root / repository_id).resolve()
        branch = str(item.get("branch", "")).strip() or None
        repositories.append(RepositoryConfig(repository_id, git_url, local_path, branch))
    return project, repositories


def sync_project(config_path: str | Path, db_path: str | Path, *, offline: bool = False) -> dict:
    if not offline and not shutil.which("git"):
        raise ProjectSyncError("没有找到 Git，请先安装 Git")

    project, repositories = load_project_config(config_path)
    systems, applications = load_application_config(
        config_path, project, {item.repository_id for item in repositories},
    )
    db = connect(str(db_path))
    results = []
    topology = integration_edges = {}
    try:
        indexer = CodeIndexer(db)
        for repository in repositories:
            sync_result = sync_repository_offline(repository) if offline else sync_repository(repository)
            indexed = indexer.ingest(str(repository.local_path), repository.repository_id)
            results.append({**sync_result, "indexed": indexed})
        topology = ApplicationTopologyStore(db).replace(systems, applications)
        integration_edges = IntegrationEdgeResolver(db).rebuild()
    finally:
        db.close()

    return {
        "projectId": project["id"],
        "projectName": project.get("name") or project["id"],
        "repositories": results,
        "topology": topology,
        "integrationEdges": integration_edges,
    }


def sync_repository_offline(config: RepositoryConfig) -> dict:
    """Use a bundled local checkout without invoking Git or any network.

    Offline bundles intentionally omit ``.git`` to reduce size and avoid
    leaking repository metadata.  The copied source directory is the release
    snapshot; updating it requires generating a new offline bundle.
    """
    target = config.local_path
    if not target.is_dir():
        raise ProjectSyncError(
            f"离线包缺少仓库 {config.repository_id}: {target}，请在联网构建机重新生成离线包"
        )
    return {
        "id": config.repository_id,
        "path": str(target),
        "branch": config.branch or "bundled-snapshot",
        "syncStatus": "OFFLINE_BUNDLED",
    }


def sync_repository(config: RepositoryConfig) -> dict:
    target = config.local_path
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        arguments = ["clone"]
        if config.branch:
            arguments += ["--branch", config.branch, "--single-branch"]
        arguments += [config.git_url, str(target)]
        logger.info("克隆仓库 %s ← %s (branch=%s)", config.repository_id, config.git_url, config.branch or "default")
        _git(None, *arguments)
        branch = _current_branch(target)
        return _result(config, branch, "CLONED")

    if not (target / ".git").is_dir():
        raise ProjectSyncError(f"本地目录不是 Git 仓库: {target}")

    remote_url = _git(target, "remote", "get-url", "origin")
    if _normalise_remote(remote_url) != _normalise_remote(config.git_url):
        raise ProjectSyncError(
            f"仓库 {config.repository_id} 的 origin 与配置不一致: {target}"
        )

    changes = _git(target, "status", "--porcelain", "--untracked-files=normal")
    if changes:
        raise ProjectSyncError(
            f"仓库 {config.repository_id} 存在未提交修改，请先处理后再同步: {target}"
        )

    branch = _current_branch(target)
    if config.branch and branch != config.branch:
        raise ProjectSyncError(
            f"仓库 {config.repository_id} 当前分支是 {branch}，配置要求 {config.branch}"
        )

    _git(target, "fetch", "origin", branch, "--prune")
    remote_ref = f"origin/{branch}"
    local_revision = _git(target, "rev-parse", "HEAD")
    remote_revision = _git(target, "rev-parse", remote_ref)
    if local_revision == remote_revision:
        logger.info("仓库 %s 已是最新 (branch=%s)", config.repository_id, branch)
        return _result(config, branch, "UP_TO_DATE")

    if _is_ancestor(target, "HEAD", remote_ref):
        logger.info("快进更新仓库 %s： %s → %s", config.repository_id, local_revision[:8], remote_revision[:8])
        _git(target, "merge", "--ff-only", remote_ref)
        return _result(config, branch, "UPDATED")

    if _is_ancestor(target, remote_ref, "HEAD"):
        raise ProjectSyncError(
            f"仓库 {config.repository_id} 包含尚未推送的本地提交，未自动覆盖: {target}"
        )

    raise ProjectSyncError(
        f"仓库 {config.repository_id} 的本地分支与远端已经分叉，请人工处理: {target}"
    )


def _result(config: RepositoryConfig, branch: str, status: str) -> dict:
    return {
        "id": config.repository_id,
        "path": str(config.local_path),
        "branch": branch,
        "syncStatus": status,
    }


def _current_branch(repository: Path) -> str:
    branch = _git(repository, "branch", "--show-current")
    if not branch:
        raise ProjectSyncError(f"仓库处于 detached HEAD 状态: {repository}")
    return branch


def _is_ancestor(repository: Path, earlier: str, later: str) -> bool:
    process = subprocess.run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", earlier, later],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode not in (0, 1):
        detail = process.stderr.strip() or process.stdout.strip()
        raise ProjectSyncError(f"Git 无法比较本地与远端版本: {detail}")
    return process.returncode == 0


def _git(repository: Path | None, *arguments: str) -> str:
    command = ["git"]
    if repository is not None:
        command += ["-C", str(repository)]
    command += list(arguments)
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "未知 Git 错误"
        raise ProjectSyncError(detail)
    return process.stdout.strip()


def _resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def _resolve_git_url(config_path: Path, value: str) -> str:
    """Resolve local relative Git sources while leaving hosted URLs unchanged.

    A validation fixture can point at a sibling directory with
    ``"gitUrl": "examples/validation-project"``.  Resolving that path once at
    config-load time makes the cloned repository's ``origin`` stable across
    subsequent syncs.  HTTPS, SSH and other non-local Git URLs remain exactly
    as configured.
    """
    if not value or "://" in value or value.startswith("git@"):
        return value
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return str(candidate.resolve())
    configured = (config_path.parent / candidate).resolve()
    if configured.exists():
        return str(configured)
    working_directory = (Path.cwd() / candidate).resolve()
    if working_directory.exists():
        return str(working_directory)
    return value


def _normalise_remote(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/").removesuffix(".git")

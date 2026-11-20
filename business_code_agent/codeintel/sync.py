from __future__ import annotations

import logging
import re

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from business_code_agent.knowledge.topology import ApplicationTopologyStore, load_application_config
from .map import generate_code_map
from .indexing import CodeIndexer
from business_code_agent.knowledge.edges import IntegrationEdgeResolver
from business_code_agent.storage.schema import connect


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
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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


def sync_project(config_path: str | Path, db_path: str | Path, *, offline: bool = False,
                 code_map_root: str | Path | None = None) -> dict:
    if not offline and not shutil.which("git"):
        raise ProjectSyncError("没有找到 Git，请先安装 Git")

    project, repositories = load_project_config(config_path)
    systems, applications = load_application_config(
        config_path, project, {item.repository_id for item in repositories},
    )
    db = connect(str(db_path))
    results = []
    topology = integration_edges = {}
    code_map = None
    try:
        indexer = CodeIndexer(db)
        for repository in repositories:
            sync_result = sync_repository_offline(repository) if offline else sync_repository(repository)
            indexed = indexer.ingest(str(repository.local_path), repository.repository_id)
            results.append({**sync_result, "indexed": indexed})
        topology = ApplicationTopologyStore(db).replace(systems, applications)
        integration_edges = IntegrationEdgeResolver(db).rebuild()
        if code_map_root is not None:
            code_map = generate_code_map(db, code_map_root, project_name=project.get("name") or project["id"])
    finally:
        db.close()

    return {
        "projectId": project["id"],
        "projectName": project.get("name") or project["id"],
        "repositories": results,
        "topology": topology,
        "integrationEdges": integration_edges,
        "codeMap": code_map,
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
    if _is_local_snapshot_url(config.git_url):
        if not target.is_dir():
            raise ProjectSyncError(
                f"仓库 {config.repository_id} 使用本地快照配置，但 localPath 不存在: {target}；"
                "请修正 localPath，或把 gitUrl 改成可访问的内网 Git 地址"
            )
        return {
            **_result(config, config.branch or "local", "LOCAL_SNAPSHOT"),
            "message": "gitUrl=unused，跳过 Git 同步并直接使用 localPath",
        }
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

    _raise_on_local_changes(config, target)

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


def _raise_on_local_changes(config: RepositoryConfig, target: Path) -> None:
    """Refuse to sync a checkout that has real uncommitted work.

    AOCI writes ``aoci.txt``/``aoci.*.txt``/``AGENTS.md``/``.gitattributes``
    and the ``.aoci/`` directory into a repository.  Those files legitimately
    appear as uncommitted changes right after ``aoci init``, and treating them
    like ordinary edits made ``sync-project`` fail on every launch until a
    human committed them.  They stay allowed *only* in a repository that
    actually carries AOCI cognition; every other repository keeps the
    original rule, so local edits are never merged into a fetched revision
    silently.
    """
    changes = list(_uncommitted_changes(target))
    if not changes:
        return

    if _is_aoci_repository(target):
        rejected = [change for change in changes if not _is_allowed_aoci_change(change)]
        if not rejected:
            logger.info("仓库 %s 仅有 AOCI 资产未提交变更，按白名单放行", config.repository_id)
            return
        detail = "、".join(change.describe() for change in rejected)
        raise ProjectSyncError(
            f"仓库 {config.repository_id} 存在未提交修改，请先处理后再同步: {target}；"
            f"不在 AOCI 白名单内的改动路径: {detail}"
        )

    detail = "、".join(change.describe() for change in changes)
    raise ProjectSyncError(
        f"仓库 {config.repository_id} 存在未提交修改，请先处理后再同步: {target}；"
        f"改动路径: {detail}"
    )


@dataclass(frozen=True)
class GitChange:
    """One ``git status --porcelain`` record.

    ``status`` keeps both XY columns; ``paths`` holds every affected path,
    because a rename touches the old and the new name and both must be
    judged by the whitelist.
    """

    status: str
    paths: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.status.strip() or '?'} {' -> '.join(self.paths)}"


def _uncommitted_changes(repository: Path) -> list[GitChange]:
    """Parse ``git status --porcelain --untracked-files=normal`` leniently.

    ``--untracked-files=normal`` collapses an entirely untracked directory
    into a single ``dir/`` record, so the parser must treat a trailing slash
    as a directory prefix rather than a literal file name.
    """
    output = _git_unstripped(repository, "status", "--porcelain", "--untracked-files=normal")
    changes: list[GitChange] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        if len(line) < 3:
            changes.append(GitChange(line.ljust(2)[:2], (line,)))
            continue
        status, payload = line[:2], line[3:]
        if "R" in status or "C" in status:
            source, destination = _split_rename_payload(payload)
            if destination is not None:
                changes.append(GitChange(status, (_unquote_git_path(source), _unquote_git_path(destination))))
                continue
        changes.append(GitChange(status, (_unquote_git_path(payload),)))
    return changes


def _split_rename_payload(payload: str) -> tuple[str, str | None]:
    """Split ``old -> new`` while keeping quoted paths intact.

    Git quotes any path that contains `` -> `` itself, so the separator is
    only searched outside double quotes; splitting on the first `` -> ``
    would otherwise cut a filename such as ``"a -> b.java"`` in half.  When
    no separator is found the record is treated as a plain path.
    """
    quoted = False
    index = 0
    while index < len(payload):
        character = payload[index]
        if character == '"':
            quoted = not quoted
            index += 1
            continue
        if character == "\\" and quoted:
            index += 2
            continue
        if not quoted and payload.startswith(" -> ", index):
            return payload[:index], payload[index + 4:]
        index += 1
    return payload, None


_AOCI_TEXT_ASSET = re.compile(r"aoci(?:\.[A-Za-z0-9_-]+)*\.txt", re.IGNORECASE)


def _is_aoci_repository(target: Path) -> bool:
    """Recognize a checkout that carries AOCI cognition assets."""
    if (target / "aoci.txt").is_file():
        return True
    return (target / ".aoci").is_dir() and any(
        (target / name).is_file()
        for name in ("aoci.meta.txt", "aoci.code.txt", "aoci.database.txt")
    )


def _is_allowed_aoci_change(change: GitChange) -> bool:
    """Decide whether every path of one change is an AOCI asset.

    A rename such as ``aoci.txt -> src/aoci.txt`` must not slip through: the
    whole record is only allowed when *both* sides are assets.
    """
    return all(_is_allowed_aoci_asset(path) for path in change.paths)


def _is_allowed_aoci_asset(path: str) -> bool:
    """Whitelist AOCI cognition assets and their generated index sidecars."""
    name = path.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if name in {"AGENTS.md", ".gitattributes"}:
        return True
    # ``.aoci/`` is covered by its own .gitignore, but a repository without
    # that file (or a freshly initialized one) can report the directory itself.
    if name == ".aoci" or name.startswith(".aoci/"):
        return True
    if "/" in name:
        # An AOCI asset nested somewhere else is a real edit, not tool output.
        return False
    return _AOCI_TEXT_ASSET.fullmatch(name) is not None


def _unquote_git_path(value: str) -> str:
    """Undo the C-style quoting Git applies to unusual paths.

    Git wraps paths containing non-ASCII, control characters, quotes or
    backslashes in double quotes and escapes the bytes; a path literally
    containing `` -> `` is quoted as well, which keeps the rename separator
    unambiguous.
    """
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    body = value[1:-1]
    escapes = {"a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
               '"': '"', "\\": "\\"}
    decoded = bytearray()
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\" and index + 1 < len(body):
            following = body[index + 1]
            if following in escapes:
                decoded.extend(escapes[following].encode("utf-8"))
                index += 2
                continue
            octal = re.match(r"[0-7]{1,3}", body[index + 1:])
            if octal:
                decoded.append(int(octal.group(), 8) & 0xFF)
                index += 1 + len(octal.group())
                continue
            decoded.extend(following.encode("utf-8"))
            index += 2
            continue
        decoded.extend(character.encode("utf-8"))
        index += 1
    return decoded.decode("utf-8", "replace")


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


def _git_unstripped(repository: Path, *arguments: str) -> str:
    """Run Git and keep stdout byte-for-byte instead of stripping it.

    ``git status --porcelain`` encodes the file state in the first two
    columns, so the leading space of `` M src/Entry.java`` is meaningful.
    ``_git`` strips the whole output, which shifts that first record to
    ``M src/Entry.java`` and makes fixed-column parsing read ``rc/Entry.java``.
    ``errors="replace"`` keeps a repository with a non-UTF-8 file name from
    raising ``UnicodeDecodeError`` here.
    """
    command = ["git", "-C", str(repository), *arguments]
    process = subprocess.run(command, capture_output=True, text=True,
                             errors="replace", check=False)
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "未知 Git 错误"
        raise ProjectSyncError(detail)
    return process.stdout


def _resolve_from_config(config_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def _resolve_git_url(config_path: Path, value: str) -> str:
    """Resolve local relative Git sources while leaving hosted URLs unchanged.

    A validation fixture can point at a sibling directory with
    ``"gitUrl": "examples/demo"``.  Resolving that path once at
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


def _is_local_snapshot_url(value: str) -> bool:
    """Recognize the explicit placeholder used by local example fixtures."""
    return str(value or "").strip().casefold() in {"unused", "local", "local-only"}


def _normalise_remote(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/").removesuffix(".git")

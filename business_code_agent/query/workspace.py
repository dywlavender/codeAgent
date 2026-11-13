"""Build the read-only filesystem view used by Claude Code.

The query runtime deliberately exposes files instead of translating business
knowledge and code into Python-owned retrieval objects. A workspace contains
only a generated ``CLAUDE.md`` and links to the already synchronised source,
project-specific business context, generated code map and requirement
directories.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .materials import BUSINESS_CONTEXT_MOUNT, MaterialManifest
from .navigation import NAVIGATION_FILE, build_navigation

logger = logging.getLogger(__name__)

TURN_CONTEXT_FILENAME = ".codeagent-turn-context.md"

MODE_DESCRIPTIONS = {
    "combined": "本轮组合使用基础资料、业务补充知识、已有自动代码地图和可用的手动 AST 结构资料，按问题需要检索和读取。源码确认当前实现，需求说明目标，业务补充解释项目语义，结构资料只用于定位。",
    "none": "当前模式：无主干。正常项目 README、需求原文和源码可用，不提供业务补充知识或自动代码地图。",
    "backbone": "当前模式：有主干。提供项目特有的业务补充知识，按问题需要读取；源码仍是当前实现依据。",
    "ast": "当前模式：AST。仅提供用户手动生成的结构资料和代码索引，不提供人工业务补充知识；结构条目只用于定位，行为仍须读取源码。",
    "raw": "评测 A：原始项目资料。只提供源码、README 和需求原文，不提供业务补充知识或自动代码地图。",
    "old_baseline": "评测 B：历史完整主干。只用于历史对照；当前实现仍以源码核实。",
    "code_map": "评测 C：自动代码地图。只提供结构导航，不提供人工业务补充知识；行为仍须读取源码。",
    "business_context": "评测 D：业务补充知识。只提供经过分类的项目特有语义，不提供自动代码地图；行为仍须读取源码。",
    "code_map_context": "评测 E：自动代码地图 + 业务补充知识。两类资料均按需读取，结构和语义资料都不是实现证据。",
}

_STATUS_LABELS = {
    "READABLE": "可读取",
    "EMPTY": "目录为空",
    "MISSING": "目录缺失",
    "UNREADABLE": "无法读取",
}


class WorkspaceError(RuntimeError):
    """The Claude workspace cannot be created safely."""


def workspace_sources(root: Path, repositories: set[str] | None = None) -> list[tuple[str, Path]]:
    """Resolve only the source slots maintained by WorkspaceManager.

    ``repositories`` is an optional allowlist of workspace repo directory
    names (as produced by :func:`_safe_name`). ``None`` keeps every mounted
    repo; a set keeps only the listed ones — this is what enforces a query
    scope at the CLI permission level.
    """
    return MaterialManifest.from_workspace(root, repositories).labelled_roots()


def search_instructions(sources: list[tuple[str, Path]], scoped: bool = False) -> str:
    """Describe the same roots used by CLI authorization, including on resume."""
    paths = "\n".join(f"- {label}：{json.dumps(str(path), ensure_ascii=False)}" for label, path in sources)
    source_list = paths or (
        "当前没有已挂载的可搜索来源目录。若任务提示已经提供原文，直接处理该输入；"
        "否则如实说明缺少来源，不要猜测目录。"
    )
    scope_note = ""
    if scoped:
        scope_note = ("\n本轮调查已限定范围：只授权上述代码目录，未列出的代码目录没有授权，"
                      "不要尝试访问。如果问题确实需要范围外的系统或工程，如实说明需要扩大范围，"
                      "不要编造范围外的实现。\n")
    return f"""## 本轮检索范围

{source_list}
{scope_note}
这是本轮可搜索的真实来源目录清单，续聊时也以本清单为准。
Glob/Grep 的 path 必须明确指定清单中的一个目录或其内部目录，不要省略 path，也不要合并成它们的共同上级目录。
跨仓库搜索请分别指定相关仓库目录。根据已读源码中的调用、事件或路由线索，以及相关业务资料中的系统边界，决定是否继续调查其他已列出的仓库。没有匹配时调整检索词或范围；发现匹配后也可能需要跨仓库继续核查。
Read 用于读取已定位的文件，不要把目录作为 file_path。列文件使用 Glob。
如果一次调用因路径越界被拒绝，先核对请求路径。若误用了共同上级目录或旧目录，改用清单中明确授权的相关目录；这不是扩大权限。
若清单中的正确路径仍被拒绝，停止访问该来源，如实报告具体路径及错误；不要换工具绕过权限。
某个路径访问失败不代表所有资料不可读。没有搜到也不等于代码不存在；回答应区分未找到、访问失败和源码已确认。
"""


def project_index(sources: list[tuple[str, Path]]) -> str:
    """Add a short source index without injecting a business document.

    ``project-overview.md`` used to be copied into every prompt.  The new
    contract keeps prompt context limited to a small index and leaves all
    business-context and code-map documents available for on-demand reads.
    A deliberately named ``project-index.md`` may provide a short, stable
    index; the historical overview filename is never auto-injected.
    """
    index_documents = []
    for label, directory in sources:
        if label not in {"业务补充知识", "业务基线", "自动代码地图", "AST 结构资料"}:
            continue
        candidate = directory / "project-index.md"
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            logger.warning("无法读取项目索引: path=%s error=%s", candidate, exc)
            continue
        if content:
            index_documents.append((label, candidate, directory, content))
    if not index_documents:
        return ""
    lines = ["## 本轮项目资料索引", "", "以下资料按需读取；项目当前实现必须以源码核实。"]
    for label, _index_path, _index_directory, content in index_documents:
        lines.extend([
            "",
            f"{label}的 project-index.md：",
            content,
        ])
    return "\n".join(lines)


def read_turn_context(root: Path) -> str:
    """Read generated per-turn metadata without treating it as project evidence."""
    path = root / TURN_CONTEXT_FILENAME
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _directory_status(path: Path) -> str:
    """Report the state of a mounted directory without following other roots."""
    try:
        if not path.is_dir():
            return "MISSING"
        with os.scandir(path) as entries:
            return "EMPTY" if next(entries, None) is None else "READABLE"
    except OSError:
        return "UNREADABLE"


@dataclass(frozen=True)
class Workspace:
    id: str
    path: Path
    claude_file: Path
    knowledge_path: Path
    requirements_path: Path
    repositories_path: Path
    mode: str | None = None
    ast_version_id: str | None = None
    business_context_path: Path | None = None
    code_map_path: Path | None = None
    turn_context_path: Path | None = None
    ast_path: Path | None = None
    ast_status: str | None = None
    repository_ids: tuple[str, ...] = ()
    repository_mounts: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "claudeFile": str(self.claude_file),
            "knowledgePath": str(self.knowledge_path),
            "businessContextPath": str(self.business_context_path or self.knowledge_path),
            "codeMapPath": str(self.code_map_path) if self.code_map_path else None,
            "requirementsPath": str(self.requirements_path),
            "repositoriesPath": str(self.repositories_path),
            "mode": self.mode,
            "astVersionId": self.ast_version_id,
            "astPath": str(self.ast_path) if self.ast_path else None,
            "astStatus": self.ast_status,
            "turnContextPath": str(self.turn_context_path) if self.turn_context_path else None,
            "repositoryIds": list(self.repository_ids),
        }


class WorkspaceManager:
    """Create one stable, project-scoped view for the Claude runtime."""

    def __init__(
        self,
        db=None,
        *,
        project_config: str | Path | None = None,
        workspace_root: str | Path | None = None,
        business_context_root: str | Path | None = None,
        code_map_root: str | Path | None = None,
        baseline_root: str | Path | None = None,
        requirements_root: str | Path | None = None,
    ):
        self.db = db
        self.project_config = Path(project_config).expanduser().resolve() if project_config else None
        self.config = self._load_config()
        self.project_id = str((self.config.get("project") or {}).get("id") or "default").strip() or "default"
        self.project_name = str((self.config.get("project") or {}).get("name") or self.project_id).strip()
        knowledge = self.config.get("knowledge") or {}
        configured_business_context = knowledge.get("businessContextRoot")
        configured_legacy_baseline = knowledge.get("baselineRoot")
        configured_code_map = knowledge.get("codeMapRoot") or knowledge.get("generatedCodeMapRoot")
        legacy_material_requested = bool(baseline_root or configured_legacy_baseline)
        self._new_material_layout = bool(
            business_context_root or configured_business_context or
            ((code_map_root or configured_code_map) and not legacy_material_requested)
        )
        business_context_value = (
            business_context_root or baseline_root or configured_business_context or
            (configured_legacy_baseline if not self._new_material_layout else None) or
            ("knowledge/business-context" if self._new_material_layout else "knowledge/baseline")
        )
        self.business_context_root = self._resolve_material_path(business_context_value)
        # Keep this property for internal and external compatibility. New
        # callers should use business_context_root.
        self.baseline_root = Path(baseline_root).expanduser().resolve() if baseline_root else None
        self.code_map_root = self._resolve_material_path(
            code_map_root or configured_code_map or "knowledge/generated-code-map"
        )
        self.requirements_root = (
            Path(requirements_root).expanduser().resolve() if requirements_root else
            self._configured_path(
                self.config.get("requirementsRoot")
                or self.config.get("requirementRoot")
                or ((self.config.get("requirements") or {}).get("root") if isinstance(self.config.get("requirements"), dict) else None)
                or "requirements"
            )
        )
        if workspace_root:
            base = Path(workspace_root).expanduser().resolve()
        elif self.project_config:
            base = self.project_config.parent / ".data" / "agent-workspaces"
        else:
            base = Path.cwd() / ".data" / "agent-workspaces"
        self.root = base / _safe_name(self.project_id)

    def _mode_root(self, mode: str | None = None) -> Path:
        return self.root if not mode else self.root / "modes" / _safe_name(mode)

    def prepare_core(self, *, run_id: str, repository_ids=None,
                     include_requirements: bool = True,
                     include_business_context: bool = False) -> Workspace:
        """Create a run-isolated workspace containing only authorized sources.

        The expected repository set comes from project configuration and the
        normalized scope, never from directories that happen to exist. All
        selected repositories are checked before any model call; a missing or
        unreadable one fails with its repository id. Business context is an
        optional profile-authorized document mount; no AST, code map or index
        is inspected or mounted.
        """
        configured = self.repository_sources()
        if not configured:
            raise WorkspaceError("项目未配置代码仓库，无法启动 core 调查")
        available = {repository_id: source for repository_id, source in configured}
        selected_ids = (sorted(dict.fromkeys(str(repository_id) for repository_id in repository_ids))
                        if repository_ids else [repository_id for repository_id, _ in configured])
        unknown = [repository_id for repository_id in selected_ids if repository_id not in available]
        if unknown:
            raise WorkspaceError("仓库未在项目配置中登记：" + "、".join(unknown))
        selected = [(repository_id, available[repository_id]) for repository_id in selected_ids]
        unusable = []
        for repository_id, source in selected:
            if not source.is_dir():
                unusable.append(f"{repository_id}（目录不存在: {source}）")
                continue
            try:
                with os.scandir(source):
                    pass
            except OSError as exc:
                unusable.append(f"{repository_id}（无法读取: {exc}）")
        if unusable:
            raise WorkspaceError("授权仓库不可用，未调用模型：" + "；".join(unusable))

        root = self.root / "core" / _safe_name(run_id)
        root.mkdir(parents=True, exist_ok=True)
        repositories = root / "repos"
        repositories.mkdir(exist_ok=True)
        desired = {_safe_name(repository_id) for repository_id, _ in selected}
        for child in repositories.iterdir():
            if child.is_symlink() and child.name not in desired:
                child.unlink()
        for repository_id, source in selected:
            link = repositories / _safe_name(repository_id)
            self._link_directory(link, source)
            if not link.is_dir():
                raise WorkspaceError(f"仓库挂载失败：{repository_id}")
        requirements_link = root / "requirements"
        if include_requirements and self.requirements_root.is_dir():
            self._link_directory(requirements_link, self.requirements_root)
        else:
            self._unlink_directory(requirements_link)
        business_link = root / BUSINESS_CONTEXT_MOUNT
        if include_business_context and self.business_context_root.is_dir():
            self._link_directory(business_link, self.business_context_root)
        else:
            self._unlink_directory(business_link)
        claude_file = root / "CLAUDE.md"
        claude_file.write_text(self._core_instructions(include_business_context), encoding="utf-8")
        navigation = build_navigation(
            self.config,
            {repository_id: f"repos/{_safe_name(repository_id)}" for repository_id, _ in selected},
            self.project_id, self.project_name,
        )
        (root / NAVIGATION_FILE).write_text(
            json.dumps(navigation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        mounts = tuple((repository_id, f"repos/{_safe_name(repository_id)}")
                       for repository_id, _ in selected)
        return Workspace(
            id=self.project_id,
            path=root,
            claude_file=claude_file,
            knowledge_path=root / "knowledge",
            requirements_path=requirements_link,
            repositories_path=repositories,
            mode="core",
            repository_ids=tuple(repository_id for repository_id, _ in selected),
            repository_mounts=mounts,
            business_context_path=(business_link
                                   if include_business_context and business_link.is_dir() else None),
        )

    def ensure(self, *, mode: str | None = None,
               business_context_source: str | Path | None = None,
               code_map_source: str | Path | None = None,
               ast_source: str | Path | None = None,
               ast_status: str | None = None,
               baseline_source: str | Path | None = None,
               mode_description: str | None = None,
               ast_version_id: str | None = None) -> Workspace:
        root = self._mode_root(mode)
        root.mkdir(parents=True, exist_ok=True)
        knowledge = root / "knowledge"
        repositories = root / "repos"
        knowledge.mkdir(exist_ok=True)
        repositories.mkdir(exist_ok=True)
        business_context_link = knowledge / (
            "business-context" if self._new_material_layout else "baseline"
        )
        code_map_link = knowledge / "generated-code-map"
        ast_link = knowledge / "ast"
        requirements_link = root / "requirements"
        if baseline_source is not None:
            # Historical callers used baseline_source for both the human
            # material and the AST material. Route the latter to the new
            # structural slot without breaking those callers.
            if mode == "ast" and code_map_source is None:
                code_map_source = baseline_source
            elif business_context_source is None:
                business_context_source = baseline_source
        if business_context_source is None:
            business_context_source = self.business_context_root
        else:
            business_context_source = Path(business_context_source).expanduser().resolve()
        if code_map_source is None:
            code_map_source = self.code_map_root if mode not in {"none", "backbone", "ast"} else None
        elif code_map_source is not None:
            code_map_source = Path(code_map_source).expanduser().resolve()
        if mode in {"none", "raw"}:
            code_map_source = None
            business_context_source = None
        elif mode in {"backbone", "business_context", "old_baseline"}:
            code_map_source = None
        elif mode in {"ast", "code_map", "code_map_context"} and (not code_map_source or not code_map_source.is_dir()):
            message = "AST 资料目录不可用，请先手动生成 AST" if mode == "ast" else "自动代码地图不可用，请先完成源码同步和索引"
            raise WorkspaceError(message)
        if mode in {"ast", "code_map"}:
            business_context_source = None
        # Production mounts prepared AST separately, so it can coexist with
        # both business context and the generated code map. Experimental modes
        # keep their original isolated material sets.
        ast_source = Path(ast_source).expanduser().resolve() if ast_source and mode == "combined" else None
        requirements_source = self.requirements_root
        self._link_directory(business_context_link, business_context_source) if business_context_source else self._unlink_directory(business_context_link)
        self._link_directory(code_map_link, code_map_source) if code_map_source else self._unlink_directory(code_map_link)
        self._link_directory(ast_link, ast_source) if ast_source else self._unlink_directory(ast_link)
        self._link_directory(requirements_link, requirements_source)
        linked_repositories = self._repository_sources()
        desired_names = {_safe_name(repository_id) for repository_id, _ in linked_repositories}
        # Remove only stale links previously created by this manager. Real
        # directories are left alone so a user can keep auxiliary workspace
        # files without them being destroyed on refresh.
        for child in repositories.iterdir():
            if child.name not in desired_names and child.is_symlink():
                child.unlink()
        for repository_id, source in linked_repositories:
            self._link_directory(repositories / _safe_name(repository_id), source)
        claude_file = root / "CLAUDE.md"
        claude_file.write_text(self._claude_instructions(), encoding="utf-8")
        turn_context_file = root / TURN_CONTEXT_FILENAME
        turn_context_file.write_text(
            self._turn_context(root, mode, mode_description, ast_version_id), encoding="utf-8"
        )
        navigation = build_navigation(
            self.config,
            {repository_id: f"repos/{_safe_name(repository_id)}"
             for repository_id, _ in linked_repositories
             if (repositories / _safe_name(repository_id)).is_dir()},
            self.project_id, self.project_name,
        )
        (root / NAVIGATION_FILE).write_text(
            json.dumps(navigation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return Workspace(
            id=self.project_id,
            path=root,
            claude_file=claude_file,
            knowledge_path=business_context_link,
            requirements_path=requirements_link,
            repositories_path=repositories,
            mode=mode,
            ast_version_id=ast_version_id,
            business_context_path=business_context_link,
            code_map_path=code_map_link,
            turn_context_path=turn_context_file,
            ast_path=ast_link,
            ast_status=ast_status,
        )

    def refresh(self) -> Workspace:
        """Reconcile links after repository or business-knowledge changes."""
        return self.ensure()

    def source_summary(self, workspace: Workspace | None = None) -> list[dict[str, Any]]:
        """Report filesystem readiness separately from CLI permission setup."""
        root = workspace.path if workspace else self.root
        if workspace and workspace.mode == "core":
            configured = dict(self._repository_sources())
            slots = []
            if workspace.requirements_path.is_dir():
                slots.append(("requirements", "需求原文", workspace.requirements_path,
                              self.requirements_root))
            for repository_id, mount in workspace.repository_mounts:
                slots.append(("repository", repository_id, workspace.path / mount,
                              configured.get(repository_id)))
            result = []
            for kind, name, link, path in slots:
                mounted = path is not None and link.is_dir() and link.resolve() == Path(path).resolve()
                result.append({
                    "id": f"{kind}:{name}", "kind": kind, "name": name,
                    "path": str(path or link), "status": "READABLE" if mounted else "MISSING",
                    "authorizationConfigured": mounted,
                })
            return result
        slots = [
            (("business_context", "业务补充知识", root / "knowledge" / "business-context", self.business_context_root)
             if self._new_material_layout else
             ("baseline", "业务基线", root / "knowledge" / "baseline", self.business_context_root)),
            ("code_map", "自动代码地图", root / "knowledge" / "generated-code-map", self.code_map_root),
            ("requirements", "需求原文", root / "requirements", self.requirements_root),
        ]
        if workspace and workspace.mode == "combined" and workspace.ast_path:
            slots.append(("ast", "AST 结构", workspace.ast_path, workspace.ast_path.resolve()))
        slots.extend(("repository", name, root / "repos" / _safe_name(name), path)
                     for name, path in self._repository_sources())
        result = []
        for kind, name, link, path in slots:
            status = "MISSING"
            try:
                if path.is_dir():
                    with os.scandir(path) as entries:
                        status = "EMPTY" if next(entries, None) is None else "READABLE"
                mounted = link.is_dir() and link.resolve() == path.resolve()
            except OSError:
                status = "UNREADABLE"
                mounted = False
            result.append({"id": f"{kind}:{name}", "kind": kind, "name": name,
                           "path": str(path), "status": status,
                           "authorizationConfigured": mounted})
            if kind == "ast":
                result[-1].update(availability=workspace.ast_status, versionId=workspace.ast_version_id)
        return result

    def _load_config(self) -> dict[str, Any]:
        if not self.project_config or not self.project_config.is_file():
            return {}
        try:
            value = json.loads(self.project_config.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"项目配置无法读取: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkspaceError("项目配置必须是 JSON 对象")
        return value

    def _configured_path(self, value: Any) -> Path:
        raw = str(value or "").strip()
        base = self.project_config.parent if self.project_config else Path.cwd()
        candidate = Path(raw).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()

    def _resolve_material_path(self, value: Any) -> Path:
        candidate = Path(str(value or "")).expanduser()
        return candidate.resolve() if candidate.is_absolute() else self._configured_path(candidate)

    def repository_sources(self) -> list[tuple[str, Path]]:
        """Return every configured repository, including missing directories."""
        return self._repository_sources()

    def core_source_summary(self, *, include_business_context: bool = False) -> list[dict[str, Any]]:
        """Describe core sources without touching AST, code indexes or unknown material."""
        result = []
        for repository_id, path in self._repository_sources():
            status = _directory_status(path)
            result.append({
                "id": f"repository:{repository_id}", "kind": "repository",
                "name": repository_id, "path": str(path), "status": status,
                "authorizationConfigured": status in {"READABLE", "EMPTY"},
            })
        result.append({
            "id": "requirements:需求原文", "kind": "requirements", "name": "需求原文",
            "path": str(self.requirements_root),
            "status": _directory_status(self.requirements_root),
            "authorizationConfigured": self.requirements_root.is_dir(),
        })
        if include_business_context:
            status = _directory_status(self.business_context_root)
            result.append({
                "id": "business_context:业务补充知识", "kind": "business_context",
                "name": "业务补充知识", "path": str(self.business_context_root),
                "status": status, "authorizationConfigured": status == "READABLE",
            })
        return result

    def _repository_sources(self) -> list[tuple[str, Path]]:
        configured = self.config.get("repositories")
        values: list[tuple[str, Path]] = []
        seen: set[str] = set()
        if isinstance(configured, list):
            for item in configured:
                if not isinstance(item, dict):
                    continue
                repository_id = str(item.get("id") or "").strip()
                if not repository_id or repository_id in seen:
                    continue
                raw_path = item.get("localPath")
                if raw_path:
                    source = self._configured_path(raw_path)
                    # A project config may retain a historical localPath while
                    # the synchroniser has moved the checkout to
                    # repositoryRoot. Prefer the live indexed checkout when it
                    # is available instead of exposing a broken link.
                    db_source = self._repository_from_db(repository_id)
                    if not source.exists() and db_source is not None and db_source.exists():
                        source = db_source
                else:
                    source = self._repository_from_db(repository_id)
                    if source is None:
                        source = self._configured_path(
                            Path(str(self.config.get("repositoryRoot") or ".data/repositories")) / repository_id
                        )
                values.append((repository_id, source))
                seen.add(repository_id)
        if values:
            return values
        if self.db is not None:
            try:
                rows = self.db.execute("SELECT id,root_path FROM repository ORDER BY id").fetchall()
            except Exception:
                rows = []
            for row in rows:
                repository_id = str(row["id"] if hasattr(row, "keys") else row[0])
                source = Path(str(row["root_path"] if hasattr(row, "keys") else row[1])).expanduser().resolve()
                values.append((repository_id, source))
        return values

    def _repository_from_db(self, repository_id: str) -> Path | None:
        if self.db is None:
            return None
        try:
            row = self.db.execute("SELECT root_path FROM repository WHERE id=?", (repository_id,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        value = row["root_path"] if hasattr(row, "keys") else row[0]
        return Path(str(value)).expanduser().resolve()

    def _link_directory(self, link: Path, source: Path) -> None:
        source = source.resolve()
        if not source.is_dir():
            # Optional/unavailable sources must not leave dangling links that
            # look like mounted data to the agent.
            if link.is_symlink():
                link.unlink()
            return
        if link.is_symlink():
            if link.resolve() == source:
                return
            link.unlink()
        elif link.exists():
            if link.is_dir() and not any(link.iterdir()):
                link.rmdir()
            else:
                raise WorkspaceError(f"工作区路径已存在且不是本工具创建的链接: {link}")

        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(source, target_is_directory=True)
            return
        except OSError as exc:
            if os.name != "nt":
                raise WorkspaceError(f"无法创建工作区软链接 {link} -> {source}: {exc}") from exc

        # Windows may deny directory symlinks without Developer Mode. A
        # junction still exposes the same live files and does not copy source.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise WorkspaceError(f"无法创建工作区目录链接 {link} -> {source}: {detail}")

    def _unlink_directory(self, link: Path) -> None:
        """Remove only an empty or manager-created structural link."""
        if link.is_symlink():
            link.unlink()
        elif link.is_dir() and not any(link.iterdir()):
            link.rmdir()

    def _turn_context(self, root: Path, mode: str | None, mode_description: str | None,
                      ast_version_id: str | None) -> str:
        """Build dynamic context that must be refreshed on every turn."""
        mode_note = mode_description or MODE_DESCRIPTIONS.get(
            mode or "", "当前模式由调用方指定；以下资料状态以本轮清单为准。"
        )
        material_kind = "business_context" if self._new_material_layout else "baseline"
        material_label = "业务补充知识" if self._new_material_layout else "业务基线"
        provided_by_mode = {
            "combined": {material_kind, "code_map", "ast", "requirements", "repository"},
            "none": {"requirements", "repository"},
            "raw": {"requirements", "repository"},
            "backbone": {material_kind, "requirements", "repository"},
            "business_context": {material_kind, "requirements", "repository"},
            "old_baseline": {material_kind, "requirements", "repository"},
            "ast": {"code_map", "requirements", "repository"},
            "code_map": {"code_map", "requirements", "repository"},
            "code_map_context": {material_kind, "code_map", "requirements", "repository"},
        }.get(mode or "")

        lines = [
            "## 本轮工作区上下文",
            "",
            f"项目：{self.project_name}",
            mode_note,
        ]
        if ast_version_id and mode in {"ast", "combined"}:
            lines.append(f"AST 资料版本已固定：{ast_version_id}。结构条目只用于定位，行为仍须读取源码。")
        lines.extend([
            "",
            "本轮项目、模式、资料状态和唯一授权来源以本段及后续检索范围为准；"
            "未列出的来源不属于本轮调查范围。",
            "资料状态只说明可用性，不证明资料内容正确，也不证明模型已经读取。",
            "",
            "资料状态：",
        ])

        source_slots = [
            (material_kind, material_label,
             root / "knowledge" / ("business-context" if self._new_material_layout else "baseline")),
            ("code_map", "自动代码地图", root / "knowledge" / "generated-code-map"),
            ("ast", "AST 结构资料", root / "knowledge" / "ast"),
            ("requirements", "需求原文", root / "requirements"),
        ]
        for kind, label, path in source_slots:
            if provided_by_mode is not None and kind not in provided_by_mode:
                status = "本模式不提供"
            else:
                status = _STATUS_LABELS.get(_directory_status(path), "目录不可用")
            lines.append(f"- {label}：{status}")

        repositories = self._repository_sources()
        repository_paths = [root / "repos" / _safe_name(repository_id) for repository_id, _ in repositories]
        missing_repositories = [path for path in repository_paths if _directory_status(path) != "READABLE"]
        if repository_paths:
            detail = f"已登记 {len(repository_paths)} 个"
            if missing_repositories:
                detail += f"，其中 {len(missing_repositories)} 个目录不可用"
            lines.append(f"- 代码仓库：{detail}；实际授权以本轮检索范围为准")
        else:
            lines.append("- 代码仓库：未配置或目录不可用")
        return "\n".join(lines) + "\n"

    def _core_instructions(self, include_business_context: bool = False) -> str:
        lines = [
            "# CodeAgent core",
            "",
            "本轮只授权源码仓库和可选需求原文。只读使用 Read、Glob、Grep；不要修改、创建或删除文件。",
            "",
            "- 源码是当前实现的依据，需求只说明目标；检索命中只是线索，关键结论必须 Read。",
            "- 只能访问 repos/ 和 requirements/ 下的相对路径，不得访问其他目录或宿主绝对路径。",
        ]
        if include_business_context:
            lines.extend([
                "- 业务补充知识位于 knowledge/business-context/，只用于解释术语、职责和业务约定；",
                "  不能当作源码已经实现，仍需读取源码核实。",
                "- 业务文档提到但本轮未授权的仓库或系统，只记录为待核实关系，不能访问其源码。",
            ])
        lines.append("- 无法确认的关系明确列为缺口，不把局部代码当作完整业务链。")
        return "\n".join(lines) + "\n"

    def _claude_instructions(self) -> str:
        return """# CodeAgent

你正在回答当前工程的业务与代码问题。每轮追加提示会提供当前项目、模式、资料状态、唯一授权来源目录和必要的短索引；不要把其他会话或旧提示中的范围当作本轮范围。

## 资料使用

- 源码用于确认当前实现；README 提供项目背景，具体行为以源码核实。
- 需求原文表达提出过的目标和约束，不代表已经实现。
- 业务补充知识用于解释有来源的项目术语、业务约定、职责边界、隐式跨系统关系和例外。引用时说明适用范围；待核查内容不能当作已确认事实。
- 项目索引、AST、代码地图和入口名称只帮助定位，不证明业务行为。

## 调查与作答

1. 问题涉及业务语义、名称歧义、系统边界或例外时，优先查看相关补充知识；明确的局部代码问题可以直接读源码。无需每题读取所有资料。
2. 当前实现结论必须读取相关源码。根据实际调用、事件、路由和相关业务线索决定是否继续调查其他已列出的仓库；找到第一个入口并不代表问题已回答。
3. 证据支持本题结论时直接回答。还有影响结论的缺口且存在可查的新线索时继续调查。查不到下一步时，给出能确认的结论和具体缺口。
4. 区分资料约定、代码已实现和仍待确认的部分。资料冲突时分别说明，不根据名称或文档链接猜测实现。
5. 重要实现结论给出源码路径与行号，重要业务约定给出文档位置。金额单位、枚举和前置条件要有依据，局部字段检查不能扩展为完整业务状态验证，总结不得强于证据。
6. 只报告影响答案的资料缺口；未找到、访问失败、源码已确认要区分。

当前会话只允许读取和搜索文件，不要修改源码、写文件、提交 Git 或删除文件。
"""


def _safe_name(value: str) -> str:
    value = str(value or "default").strip()
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return cleaned.strip("._") or "default"

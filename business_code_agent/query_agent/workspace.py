"""Build the read-only filesystem view used by Claude Code.

The query runtime deliberately exposes files instead of translating business
knowledge and code into Python-owned retrieval objects. A workspace contains
only a generated ``CLAUDE.md`` and links to the already synchronised source,
business baseline and requirement directories.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkspaceError(RuntimeError):
    """The Claude workspace cannot be created safely."""


@dataclass(frozen=True)
class Workspace:
    id: str
    path: Path
    claude_file: Path
    knowledge_path: Path
    requirements_path: Path
    repositories_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "claudeFile": str(self.claude_file),
            "knowledgePath": str(self.knowledge_path),
            "requirementsPath": str(self.requirements_path),
            "repositoriesPath": str(self.repositories_path),
        }


class WorkspaceManager:
    """Create one stable, project-scoped view for the Claude runtime."""

    def __init__(
        self,
        db=None,
        *,
        project_config: str | Path | None = None,
        workspace_root: str | Path | None = None,
    ):
        self.db = db
        self.project_config = Path(project_config).expanduser().resolve() if project_config else None
        self.config = self._load_config()
        self.project_id = str((self.config.get("project") or {}).get("id") or "default").strip() or "default"
        self.project_name = str((self.config.get("project") or {}).get("name") or self.project_id).strip()
        if workspace_root:
            base = Path(workspace_root).expanduser().resolve()
        elif self.project_config:
            base = self.project_config.parent / ".data" / "agent-workspaces"
        else:
            base = Path.cwd() / ".data" / "agent-workspaces"
        self.root = base / _safe_name(self.project_id)

    def ensure(self) -> Workspace:
        self.root.mkdir(parents=True, exist_ok=True)
        knowledge = self.root / "knowledge"
        repositories = self.root / "repos"
        knowledge.mkdir(exist_ok=True)
        repositories.mkdir(exist_ok=True)
        baseline_link = knowledge / "baseline"
        requirements_link = self.root / "requirements"
        baseline_source = self._configured_path(
            ((self.config.get("knowledge") or {}).get("baselineRoot") or "knowledge/baseline")
        )
        requirements_source = self._configured_path(
            self.config.get("requirementsRoot")
            or self.config.get("requirementRoot")
            or ((self.config.get("requirements") or {}).get("root") if isinstance(self.config.get("requirements"), dict) else None)
            or "requirements"
        )
        self._link_directory(baseline_link, baseline_source)
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
        claude_file = self.root / "CLAUDE.md"
        claude_file.write_text(self._claude_instructions(), encoding="utf-8")
        return Workspace(
            id=self.project_id,
            path=self.root,
            claude_file=claude_file,
            knowledge_path=baseline_link,
            requirements_path=requirements_link,
            repositories_path=repositories,
        )

    def refresh(self) -> Workspace:
        """Reconcile links after repository or business-knowledge changes."""
        return self.ensure()

    def _load_config(self) -> dict[str, Any]:
        if not self.project_config or not self.project_config.is_file():
            return {}
        try:
            value = json.loads(self.project_config.read_text(encoding="utf-8"))
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

    def _claude_instructions(self) -> str:
        return f"""# CodeAgent

你正在回答项目「{self.project_name}」的业务和代码问题。

## 可用信息

- `knowledge/baseline/`：人工维护的业务知识。
- `requirements/`：需求原文（如果项目提供）。
- `repos/`：当前项目实际代码仓库。

## 调查原则

1. 根据用户问题自行决定是否需要读取业务知识或需求原文。
2. 业务知识用于理解业务含义、系统职责和调查入口。
3. `调查入口` / Entry Anchor 只是代码搜索起点，不代表实际实现。
4. 涉及当前代码实现的结论，必须读取 `repos/` 中的实际源码确认。
5. 不要根据类名、方法名或业务知识猜测代码行为。
6. 用户问调用链时重点调查调用关系；用户问业务逻辑时重点读取方法实现、条件、分支、校验、计算、异常和返回逻辑。
7. 不要为了完整调用链而无止境向下搜索，只调查回答当前问题必要的内容。
8. 业务知识与代码不一致时分别说明，不要覆盖其中任何一方。
9. 回答重要代码结论时给出文件路径和代码行号。

当前会话只允许读取和搜索文件，不要修改源码、写文件、提交 Git 或删除文件。
"""


def _safe_name(value: str) -> str:
    value = str(value or "default").strip()
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return cleaned.strip("._") or "default"

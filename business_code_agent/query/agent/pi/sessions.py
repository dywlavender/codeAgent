"""Resolve CodeAgent conversation state to a Pi session file."""

from __future__ import annotations

from pathlib import Path


def session_root(project_data_root: Path) -> Path:
    return Path(project_data_root).expanduser().resolve() / "pi-sessions"


def prepare_session_root(root: Path) -> Path:
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_session_file(root: Path, value: str | None) -> Path | None:
    """Return an existing session file inside *root*, or None for a new session.

    Conversation records store the absolute session path produced by
    ``get_state``. A bare Pi session id is also accepted so a manually migrated
    conversation does not silently point outside the project data directory.
    """

    root = Path(root).expanduser().resolve()
    if not value:
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved.is_file() and resolved.is_relative_to(root):
        return resolved
    if Path(str(value)).name != str(value):
        return None
    matches = [path.resolve() for path in root.glob("*.jsonl")
               if str(value) in path.name and path.is_file()]
    unique = sorted(set(matches))
    if len(unique) == 1 and unique[0].is_relative_to(root):
        return unique[0]
    return None


def validate_session_file(root: Path, value: str | None, *,
                          require_exists: bool = True) -> Path:
    root = Path(root).expanduser().resolve()
    if not value:
        raise ValueError("Pi 未返回会话文件")
    resolved = Path(value).expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Pi 会话文件不在工程数据目录内：{value}")
    if require_exists and not resolved.is_file():
        raise ValueError(f"Pi 会话文件不存在：{value}")
    return resolved

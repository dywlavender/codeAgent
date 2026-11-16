"""Immutable authorization snapshot for one query run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from business_code_agent.platform.projects import ProjectContext

from .materials import MaterialSource
from .profile import AgentProfile


@dataclass(frozen=True)
class QueryScope:
    system_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]


@dataclass(frozen=True)
class RunContext:
    run_id: str
    project: ProjectContext
    profile: AgentProfile
    repository_ids: tuple[str, ...]
    workspace: Path
    materials: tuple[MaterialSource, ...]
    session_id: str | None
    system_guidance: str = ""

"""Optional code navigation documents selected from existing project topology."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


SYSTEM_GUIDES_MOUNT = "knowledge/system-guides"
_DOCUMENT_ID = re.compile(r"[\w][\w .-]*")


@dataclass(frozen=True)
class GuideDocument:
    system_id: str
    application_id: str | None
    text: str

    @property
    def relative_path(self) -> str:
        filename = (f"applications/{self.application_id}.md"
                    if self.application_id else "SYSTEM.md")
        return f"{self.system_id}/{filename}"

    @property
    def workspace_path(self) -> str:
        return f"{SYSTEM_GUIDES_MOUNT}/{self.relative_path}"


def load_system_guides(source: Path, navigation: dict) -> tuple[GuideDocument, ...]:
    """Read only the system and application files selected by navigation."""
    if source.is_symlink() or not source.is_dir():
        return ()
    root = source.resolve()
    candidates = {}
    for app in navigation["applications"]:
        system_id, application_id = str(app["systemId"] or ""), str(app["id"] or "")
        if not _DOCUMENT_ID.fullmatch(system_id):
            continue
        candidates[system_id, None] = None
        if _DOCUMENT_ID.fullmatch(application_id):
            candidates[system_id, application_id] = None
    documents = []
    for system_id, application_id in candidates:
        candidate = GuideDocument(system_id, application_id, "")
        path = root / candidate.relative_path
        try:
            # A link to a different application's file is outside this selection.
            if path.resolve() != path or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if text.strip():
            documents.append(GuideDocument(system_id, application_id, text))
    return tuple(documents)

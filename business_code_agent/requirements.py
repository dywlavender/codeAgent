"""Compatibility facade for the canonical requirement ingestion service."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection


class RequirementBuilder:
    """Backward-compatible name that delegates to :class:`RequirementService`."""

    def __init__(self, db: Connection):
        self.db = db

    def ingest(self, path: str) -> str:
        from .requirement.service import RequirementService

        source = Path(path)
        metadata = json.loads(source.read_text(encoding="utf-8")) if source.suffix.lower() == ".json" else {}
        payload = RequirementService(self.db).import_document(
            str(source), requirement_id=metadata.get("id"), title=metadata.get("title"),
        )
        return payload["id"]

    def ingest_text(
        self,
        path: str,
        requirement_id: str,
        *,
        title: str | None = None,
        version: str = "1",
        structurer=None,
    ) -> str:
        # ``version`` and an injected compatibility structurer are intentionally not
        # used: the canonical parser owns versioning and evidence boundaries.
        from .requirement.service import RequirementService

        payload = RequirementService(self.db).import_document(
            str(Path(path)), requirement_id=requirement_id, title=title,
        )
        return payload["id"]

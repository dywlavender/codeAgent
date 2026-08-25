from __future__ import annotations

from typing import Any, Mapping

from ..code_intelligence import JavaIndexer


class JavaCodeFactMaintainer:
    """Production adapter for deterministic code indexing and evidence invalidation."""

    def __init__(self, db, *, indexer: JavaIndexer | None = None):
        self.db = db
        self.indexer = indexer or JavaIndexer(db)

    def refresh(self, repository_id: str, root_path: str) -> Mapping[str, Any]:
        counts = self.indexer.ingest(root_path, repository_id)
        return {
            "repositoryId": repository_id,
            "rootPath": root_path,
            "filesChanged": counts["files"],
            "symbols": counts["symbols"],
            "facts": counts["facts"],
            "businessKnowledgeChanged": False,
        }


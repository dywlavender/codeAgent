from __future__ import annotations

from sqlite3 import Connection

from .code_intelligence import JavaIndexer
from .web_intelligence import WebIndexer


class CodeIndexer:
    """Repository-level indexer that delegates language-specific extraction."""

    def __init__(self, db: Connection):
        self.db = db
        self.java = JavaIndexer(db)
        self.web = WebIndexer(db)

    def ingest(self, root: str, repository_id: str = "repo-main") -> dict[str, int]:
        java = self.java.ingest(root, repository_id)
        web = self.web.ingest(root, repository_id)
        return {
            "files": java["files"] + web["files"],
            "symbols": self.db.execute(
                """SELECT count(*) FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                     WHERE cf.repository_id=?""",
                (repository_id,),
            ).fetchone()[0],
            "facts": self.db.execute(
                """SELECT count(*) FROM code_fact f JOIN code_symbol cs ON cs.id=f.symbol_id
                     JOIN code_file cf ON cf.id=cs.file_id WHERE cf.repository_id=?""",
                (repository_id,),
            ).fetchone()[0],
            "java": java,
            "web": web,
        }

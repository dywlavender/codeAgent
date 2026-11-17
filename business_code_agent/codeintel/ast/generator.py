"""Build a versioned structure index from frozen repository copies."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from business_code_agent.codeintel.analysis import CALLABLE_KINDS, RESOLVABLE_TYPE_KINDS, RESOLVED_EDGE_FACTS
from business_code_agent.codeintel.indexing import CodeIndexer
from business_code_agent.storage.schema import connect


def generate_ast_index(inputs_dir: Path, repository_names, *, db_path: Path) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(str(db_path))) as db:
        for _, snapshot_name in repository_names:
            CodeIndexer(db).ingest(str(inputs_dir / snapshot_name), snapshot_name)
        return index_statistics(db)


def index_statistics(db) -> dict:
    symbols = dict(db.execute("SELECT kind, COUNT(*) FROM code_symbol GROUP BY kind"))
    facts = dict(db.execute("SELECT fact_type, COUNT(*) FROM code_fact GROUP BY fact_type"))
    relations = {kind: facts.get(kind, 0) for kind in RESOLVED_EDGE_FACTS}
    return {
        "fileCount": db.execute("SELECT COUNT(*) FROM code_file").fetchone()[0],
        "symbolCount": sum(symbols.values()),
        "typeCount": sum(symbols.get(kind, 0) for kind in RESOLVABLE_TYPE_KINDS),
        "callableCount": sum(symbols.get(kind, 0) for kind in CALLABLE_KINDS),
        "relationCount": sum(relations.values()),
        "relationsByType": relations,
    }

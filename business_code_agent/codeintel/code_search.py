"""Exact symbol lookup over a persisted code index via FTS5.

The ``code_symbol_fts`` virtual table mirrors ``code_symbol`` through
triggers (storage/schema.py), so any database written by ``CodeIndexer`` —
the production knowledge database or a frozen AST ``index.db`` — is
searchable with no extra build step. Lookup returns定位线索 only: symbol
identity, kind and ``file:line``; the content still comes from ``Read``.
"""

from __future__ import annotations

import re
from sqlite3 import Connection

_TOKEN_RE = re.compile(r"[0-9A-Za-z_\u4e00-\u9fff]+")


def search_symbols(db: Connection, query: str, top_k: int = 10) -> list[dict]:
    """Return ranked symbol matches for an identifier-style query.

    Three FTS5 passes run in precision order — phrase (the query tokens in
    sequence), AND, then OR — and the first pass that returns rows wins, so
    an exact query is never diluted by looser matches. Results whose fully
    qualified name contains the raw query as a substring are marked ``exact``
    and sorted first. An empty query or no match returns an empty list;
    callers decide whether to escalate to another retrieval channel.
    """
    query = str(query or "").strip()
    if not query:
        return []
    top_k = max(1, min(int(top_k), 50))
    lowered = query.casefold()
    tokens = [token.casefold() for token in _TOKEN_RE.findall(query)]
    if not tokens:
        return []

    results: list[dict] = []
    for origin, matcher in (
        ("phrase", '"%s"' % " ".join(tokens)),
        ("and", " ".join(tokens)),
        ("or", " OR ".join(tokens)),
    ):
        rows = db.execute(
            """SELECT s.id AS symbol_id, s.qualified_name AS fqn, s.name AS name,
                      s.kind AS kind, s.line_start AS line_start, s.line_end AS line_end,
                      f.path AS path, f.repository_id AS repository_id, fts.rank AS rank
                 FROM code_symbol_fts fts
                 JOIN code_symbol s ON s.rowid = fts.rowid
                 JOIN code_file f ON f.id = s.file_id
                WHERE code_symbol_fts MATCH ?
                ORDER BY rank LIMIT ?""",
            (matcher, top_k * 4 if origin != "or" else top_k * 8),
        ).fetchall()
        if rows:
            results = [dict(row) for row in rows]
            break

    for item in results:
        item["exact"] = lowered in str(item["fqn"]).casefold()
    results.sort(key=lambda item: (not item["exact"], item["rank"]))
    return results[:top_k]

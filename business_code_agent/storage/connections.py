"""Shared SQLite connection helpers for secondary snapshot databases.

The main knowledge database goes through ``schema.connect``, which creates and
migrates the primary tables. Contract and retrieval snapshots keep their own
schemas and must not use it; they use ``open_sqlite`` instead.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_sqlite(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection without applying the primary schema.

    Readonly mode uses a URI so a missing snapshot is never created implicitly.
    """
    target = Path(path)
    if readonly:
        return sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
    return sqlite3.connect(target)

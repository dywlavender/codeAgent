"""SearchSymbols tool: exact FTS5 lookup over a persisted code index.

The tool is registered only when the caller binds an explicit code index
database — a frozen AST ``index.db`` for evaluation arms, or later the
production knowledge database. It returns locating clues (fqn, kind,
``file:line``) only; conclusions still come from reading the source.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from business_code_agent.codeintel.code_search import search_symbols
from business_code_agent.storage.schema import connect


def search_symbols_tool(db_path: str | Path):
    """Build the SearchSymbols tool bound to one code index database."""
    database = str(Path(db_path))

    @tool
    def SearchSymbols(query: str, limit: int = 8) -> str:
        """按符号名或全限定名精确检索代码符号（如 ChargeService.charge、Pay），返回 fqn、类型、文件和行号。结果是定位线索；结论必须 Read 源码核实。查不到只表示结构索引没有此符号，不代表代码不存在。"""
        db = connect(database)
        try:
            try:
                top_k = max(1, min(int(limit), 20))
            except (TypeError, ValueError):
                top_k = 8
            rows = search_symbols(db, query, top_k=top_k)
        finally:
            db.close()
        return json.dumps({"query": query, "matches": rows}, ensure_ascii=False)

    return SearchSymbols

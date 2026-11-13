"""Reuse an existing code index as source-checked navigation positions.

No parser, AST generation, indexing, or model is invoked here. Method ranges
are accepted only for matching frozen Java files and existing declarations.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from business_code_agent.storage.connections import open_sqlite

from .retrieval import passages


def existing_method_ranges(database: Path, documents: list[dict], sources: list[dict]) -> dict:
    if not database.is_file():
        raise ValueError("当前工程没有已保存的代码索引，请先显式完成源码索引")
    roots = {s["id"]: Path(s["root"]).resolve() for s in sources if s["kind"] == "source"}
    frozen = {(d["sourceId"], d["path"]): d for d in documents if d["kind"] == "source" and d["path"].endswith(".java")}
    files, excluded, indexed_at = [], [], {}
    db = open_sqlite(database, readonly=True)
    db.row_factory = sqlite3.Row
    try:
        required = {"repository", "code_file", "code_symbol", "code_fact", "evidence"}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise ValueError("当前工程没有可复用的方法索引")
        for row in db.execute("SELECT id,root_path,indexed_at FROM repository"):
            if row["id"] not in roots:
                continue
            if Path(row["root_path"]).resolve() != roots[row["id"]]:
                raise ValueError("代码索引的仓库目录与本工程冻结资料不一致")
            indexed_at[row["id"]] = row["indexed_at"]
        rows = db.execute("""SELECT cf.repository_id,cf.path,s.kind,s.name,s.qualified_name,
                                    s.line_start,s.line_end,e.excerpt AS declaration
                               FROM code_symbol s JOIN code_file cf ON cf.id=s.file_id
                          LEFT JOIN code_fact f ON f.symbol_id=s.id AND f.fact_type='CODE_DECLARATION'
                          LEFT JOIN evidence e ON e.id=f.evidence_id
                              WHERE s.kind IN ('METHOD','API')
                           ORDER BY cf.repository_id,cf.path,s.line_start,s.line_end""").fetchall()
    finally:
        db.close()
    grouped = defaultdict(list)
    for row in rows:
        if (row["repository_id"], row["path"]) in frozen:
            grouped[row["repository_id"], row["path"]].append(dict(row))
    for (source_id, path), methods in grouped.items():
        document = frozen[source_id, path]
        root = roots.get(source_id)
        live = root / path if root is not None else None
        reason = None
        if live is None or not live.is_file() or not live.resolve().is_relative_to(root):
            reason = "source_missing"
        elif not indexed_at.get(source_id):
            reason = "index_time_unknown"
        elif live.stat().st_mtime > datetime.fromisoformat(indexed_at[source_id]).timestamp():
            reason = "source_newer_than_index"
        elif live.read_text(encoding="utf-8-sig") != document["content"]:
            reason = "source_differs_from_frozen"
        if reason:
            excluded.append({"sourceId": source_id, "path": path, "reason": reason})
            continue
        lines = document["content"].splitlines()
        accepted, seen = [], set()
        for method in methods:
            first, last = method["line_start"], method["line_end"]
            key = (first, last)
            declaration = method["declaration"]
            if key in seen:
                continue
            seen.add(key)
            valid = (type(first) is int and type(last) is int and 1 <= first <= last <= len(lines)
                     and declaration and lines[first - 1].strip() == declaration.strip()
                     and re.search(r"\b" + re.escape(method["name"]) + r"\s*\(", "\n".join(lines[first - 1:min(last, first + 24)]))
                     and lines[last - 1].strip().endswith(("}", ";")))
            if not valid:
                excluded.append({"sourceId": source_id, "path": path, "lineStart": first,
                                 "reason": "method_locator_mismatch"})
                continue
            accepted.append({"name": method["name"], "qualifiedName": method["qualified_name"],
                             "kind": method["kind"], "lineStart": first, "lineEnd": last,
                             "declaration": declaration.strip(), "ending": lines[last - 1].strip()})
        if accepted:
            files.append({"sourceId": source_id, "path": path, "indexedAt": indexed_at[source_id],
                          "methods": accepted})
    if not files:
        raise ValueError("现有代码索引没有通过冻结原文核对的方法位置；不会自动重新解析源码")
    return {"origin": "existing_code_index", "language": "java", "files": files,
            "methodCount": sum(len(f["methods"]) for f in files), "excluded": excluded}


def method_passages(documents: list[dict], structure: dict, *, max_lines=70) -> list[dict]:
    """Use saved method boundaries; preserve all other text through windows."""
    indexed = {(f["sourceId"], f["path"]): f["methods"] for f in structure["files"]}
    result = []
    for document in documents:
        lines = document["content"].splitlines()
        methods = indexed.get((document["sourceId"], document["path"]), [])
        if not methods:
            for item in passages([document], max_lines=max_lines):
                item["unitType"] = "section" if document["path"].endswith(".md") else "window"
                result.append(item)
            continue
        occupied = set()
        for method in methods:
            first, last = method["lineStart"], method["lineEnd"]
            if not (1 <= first <= last <= len(lines)) or lines[first - 1].strip() != method["declaration"] or lines[last - 1].strip() != method["ending"]:
                raise ValueError("已保存的方法位置与冻结原文不一致")
            begin = first - 1
            # Keep directly preceding documentation, without interpreting it.
            while begin and lines[begin - 1].strip().startswith(("//", "/*", "*")) and begin not in occupied:
                begin -= 1
            stop = last
            while stop < len(lines) and not lines[stop].strip():
                stop += 1
            result.append({"id": f"{document['sourceId']}:{document['path']}:{begin + 1}-{stop}",
                           "sourceId": document["sourceId"], "kind": document["kind"], "path": document["path"],
                           "lineStart": begin + 1, "lineEnd": stop, "content": "\n".join(lines[begin:stop]),
                           "unitType": "method", "symbolName": method["qualifiedName"],
                           "methodLineStart": first, "methodLineEnd": last,
                           "structureVersionId": structure["versionId"]})
            occupied.update(range(begin + 1, stop + 1))
        # Imports, fields, configuration and files without usable methods stay
        # available; choosing methods does not reduce the normal corpus.
        start = 0
        while start < len(lines):
            if start + 1 in occupied:
                start += 1
                continue
            end = start + 1
            while end < len(lines) and end + 1 not in occupied:
                end += 1
            remainder = {**document, "content": "\n".join(lines[start:end]) + ("\n" if not lines[end - 1] else "")}
            for item in passages([remainder], max_lines=max_lines):
                item["lineStart"] += start
                item["lineEnd"] += start
                item["id"] = f"{item['sourceId']}:{item['path']}:{item['lineStart']}-{item['lineEnd']}"
                item["unitType"] = "window"
                result.append(item)
            start = end
    return result

"""Generate a small, source-oriented code map from the local index.

The map is navigation material, not a second implementation model.  It is
written only by an explicit repository sync/index operation and contains
repository, file, symbol and line-location hints.  Runtime answers must still
read the source files before making implementation claims.
"""

from __future__ import annotations

import re
from pathlib import Path
from sqlite3 import Connection


def generate_code_map(db: Connection, root: str | Path, *, project_name: str | None = None) -> dict:
    """Render the current index into ``project-index.md`` and repo maps."""
    target = Path(root).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    repositories_dir = target / "repositories"
    repositories_dir.mkdir(parents=True, exist_ok=True)

    repositories = db.execute(
        """SELECT id,root_path FROM repository ORDER BY id"""
    ).fetchall()
    index_lines = [
        f"# {project_name or '项目'} · 项目资料索引",
        "",
        "这是根据当前已索引源码生成的自动代码地图，只用于定位文件和符号，不含业务解释。",
        "回答实现问题前必须读取源码；没有条目不等于源码不存在。",
        "",
        "| 仓库 | 源码根目录 | 文件数 | 符号数 | 结构资料 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    written = []
    for row in repositories:
        repository_id = str(row["id"] if hasattr(row, "keys") else row[0])
        root_path = str(row["root_path"] if hasattr(row, "keys") else row[1])
        counts = db.execute(
            """SELECT count(DISTINCT cf.id) AS files, count(DISTINCT cs.id) AS symbols
                 FROM code_file cf LEFT JOIN code_symbol cs ON cs.file_id=cf.id
                WHERE cf.repository_id=?""", (repository_id,)
        ).fetchone()
        safe = _safe_name(repository_id)
        map_path = repositories_dir / f"{safe}.md"
        file_rows = db.execute(
            """SELECT cf.path AS path, cs.kind AS kind, cs.qualified_name AS qualified_name,
                      cs.line_start AS line_start
                 FROM code_file cf LEFT JOIN code_symbol cs ON cs.file_id=cf.id
                WHERE cf.repository_id=? ORDER BY cf.path, cs.line_start, cs.qualified_name""",
            (repository_id,),
        ).fetchall()
        map_path.write_text(_repository_map(repository_id, root_path, file_rows), encoding="utf-8")
        written.append(str(map_path.relative_to(target)))
        files = int(counts["files"] if hasattr(counts, "keys") else counts[0])
        symbols = int(counts["symbols"] if hasattr(counts, "keys") else counts[1])
        index_lines.append(
            f"| {repository_id} | `{root_path}` | {files} | {symbols} | "
            f"[仓库索引](repositories/{safe}.md) |"
        )
    index_lines.extend([
        "",
        "仓库索引只提供搜索入口和结构线索；当前行为、条件、异常和返回值以源码为准。",
    ])
    index_path = target / "project-index.md"
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return {"root": str(target), "index": str(index_path), "repositories": len(repositories),
            "documents": len(written) + 1, "files": written}


def _repository_map(repository_id: str, root_path: str, rows) -> str:
    lines = [
        f"# 自动代码地图：{repository_id}",
        "",
        f"源码根目录：`{root_path}`",
        "",
        "以下是索引中的文件和符号定位提示，不代表调用链或业务职责；请读取源码确认。",
        "",
    ]
    current_path = None
    for row in rows:
        path = str(row["path"] if hasattr(row, "keys") else row[0])
        if path != current_path:
            lines.extend([f"## `{path}`", ""])
            current_path = path
        qualified = row["qualified_name"] if hasattr(row, "keys") else row[2]
        kind = row["kind"] if hasattr(row, "keys") else row[1]
        line = row["line_start"] if hasattr(row, "keys") else row[3]
        if qualified:
            lines.append(f"- {kind or 'SYMBOL'} `{qualified}` · L{line or '?'}")
    if current_path is None:
        lines.append("（当前索引没有可展示的文件条目。）")
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return re.sub(r"-+", "-", cleaned).strip("-") or "repository"


__all__ = ["generate_code_map"]

"""Generate a small, source-oriented code map from the local index.

The map is navigation material, not a second implementation model.  It is
written only by an explicit repository sync/index operation and contains
repository, file, symbol, endpoint, integration and line-location hints.
Runtime answers must still read the source files before making implementation
claims.
"""

from __future__ import annotations

import re
from pathlib import Path
from sqlite3 import Connection


def generate_code_map(db: Connection, root: str | Path, *, project_name: str | None = None) -> dict:
    """Render the current index into a small navigable Markdown map.

    Facts and resolved integration edges are copied as mechanically extracted
    navigation hints. They are intentionally not rewritten as business prose
    or asserted call chains.
    """
    target = Path(root).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    repositories_dir = target / "repositories"
    repositories_dir.mkdir(parents=True, exist_ok=True)

    repositories = db.execute("SELECT id FROM repository ORDER BY id").fetchall()
    applications = _rows(db, """SELECT a.id AS id, a.name AS name,
                                      a.system_id AS system_id,
                                      COALESCE(s.name, a.system_id) AS system_name,
                                      a.repository_id AS repository_id,
                                      a.source_root AS source_root,
                                      a.app_type AS app_type,
                                      a.language AS language,
                                      a.framework AS framework,
                                      a.status AS status
                                 FROM application a
                            LEFT JOIN software_system s ON s.id=a.system_id
                                ORDER BY a.id""")
    index_lines = [
        f"# {project_name or '项目'} · 项目资料索引",
        "",
        "这是根据当前已索引源码生成的自动代码地图，只用于定位文件、结构和跨应用技术线索，不含业务解释。",
        "回答实现问题前必须读取源码；没有条目不等于源码不存在。",
        "",
        "## 可用结构资料",
        "",
        "- [仓库目录](repositories.md)：仓库、文件和符号入口。",
        "- [应用目录](applications.md)：系统、应用与源码仓库的自动拓扑。",
        "- `repositories/`：每个仓库的文件、符号、Endpoint、RPC、消息、任务和路由线索。",
        "",
    ]
    written = []
    repository_rows = []
    for row in repositories:
        repository_id = str(row["id"] if hasattr(row, "keys") else row[0])
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
        fact_rows = _rows(db, """SELECT cf.path AS path, cs.kind AS kind,
                                      cs.qualified_name AS qualified_name,
                                      cs.line_start AS line_start,
                                      f.fact_type AS fact_type,
                                      f.subject AS subject,
                                      f.target AS target
                                 FROM code_fact f
                                 JOIN code_symbol cs ON cs.id=f.symbol_id
                                 JOIN code_file cf ON cf.id=cs.file_id
                                WHERE cf.repository_id=?
                                  AND f.fact_type IN (
                                    'HTTP_BASE_PATH','HTTP_ENDPOINT','HTTP_CALL',
                                    'RPC_SERVICE','RPC_CALL','MQ_PRODUCER',
                                    'MQ_CONSUMER','JOB','ROUTE','PAGE'
                                  )
                                ORDER BY f.fact_type,cf.path,cs.line_start,f.subject""", (repository_id,))
        edge_rows = _integration_edges(db, repository_id)
        map_path.write_text(_repository_map(repository_id, file_rows, fact_rows, edge_rows), encoding="utf-8")
        written.append(str(map_path.relative_to(target)))
        files = int(counts["files"] if hasattr(counts, "keys") else counts[0])
        symbols = int(counts["symbols"] if hasattr(counts, "keys") else counts[1])
        repository_rows.append({"id": repository_id, "files": files,
                                "symbols": symbols, "map": f"repositories/{safe}.md"})
    repositories_path = target / "repositories.md"
    repositories_path.write_text(_repositories_map(repository_rows), encoding="utf-8")
    applications_path = target / "applications.md"
    applications_path.write_text(_applications_map(applications), encoding="utf-8")
    written.extend(["repositories.md", "applications.md"])
    index_path = target / "project-index.md"
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    written.append("project-index.md")
    return {"root": str(target), "index": str(index_path), "repositories": len(repositories),
            "applications": len(applications), "documents": len(written), "files": written}


def _repository_map(repository_id: str, rows, fact_rows=(), edge_rows=()) -> str:
    lines = [
        f"# 自动代码地图：{repository_id}",
        "",
        f"仓库标识：`{repository_id}`",
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
    lines.extend(["", "## 自动结构线索", "", "以下条目来自索引器或应用边解析，只用于定位，不能代替源码核验。"])
    by_fact: dict[str, list[str]] = {}
    for row in fact_rows:
        fact_type = str(_value(row, "fact_type", ""))
        subject = str(_value(row, "subject", ""))
        target = str(_value(row, "target", ""))
        qualified = str(_value(row, "qualified_name", ""))
        path = str(_value(row, "path", ""))
        line = _value(row, "line_start", "?") or "?"
        text = f"`{qualified or path}` · {subject or '-'}"
        if target:
            text += f" → `{target}`"
        text += f" · {path}:L{line}"
        by_fact.setdefault(fact_type, []).append(text)
    for fact_type in sorted(by_fact):
        lines.extend([f"### {_fact_label(fact_type)}", ""])
        lines.extend(f"- {item}" for item in by_fact[fact_type])
        lines.append("")
    if edge_rows:
        lines.extend(["### 跨应用技术边", ""])
        for row in edge_rows:
            lines.append(
                f"- `{_value(row, 'source_application', '?')}` / `{_value(row, 'source_symbol', '?')}` "
                f"—{_value(row, 'protocol', '?')}→ "
                f"`{_value(row, 'target_application', '?')}` / `{_value(row, 'target_symbol', '?')}` "
                f"({_value(row, 'status', '?')}; {_value(row, 'edge_key', '?')})"
            )
    else:
        lines.append("（当前索引没有已解析的跨应用技术边。）")
    return "\n".join(lines) + "\n"


def _repositories_map(rows) -> str:
    lines = ["# 自动代码地图：仓库目录", "", "只提供仓库、文件和符号定位；代码行为必须回到源码确认。", "",
             "| 仓库 | 文件数 | 符号数 | 结构资料 |",
             "| --- | ---: | ---: | --- |"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['files']} | {row['symbols']} | [{row['id']}]({row['map']}) |")
    return "\n".join(lines) + "\n"


def _applications_map(rows) -> str:
    lines = ["# 自动代码地图：应用目录", "", "系统与应用拓扑来自项目配置和索引结果，不代表业务职责或调用结论。", "",
             "| 应用 | 系统 | 仓库 | 类型 | 语言 / 框架 | 源码范围 | 状态 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        language = str(_value(row, "language", ""))
        framework = str(_value(row, "framework", ""))
        stack = " / ".join(item for item in (language, framework) if item) or "-"
        lines.append(f"| {_value(row, 'name', _value(row, 'id', '?'))} | {_value(row, 'system_name', '?')} | "
                     f"{_value(row, 'repository_id', '?')} | {_value(row, 'app_type', '?')} | {stack} | "
                     f"`{_value(row, 'source_root', '.')}` | {_value(row, 'status', '?')} |")
    if len(lines) == 7:
        lines.append("（当前索引没有应用拓扑记录。）")
    return "\n".join(lines) + "\n"


def _integration_edges(db: Connection, repository_id: str):
    return _rows(db, """SELECT e.edge_type AS edge_type, e.protocol AS protocol,
                              e.edge_key AS edge_key, e.status AS status,
                              sa.name AS source_application,
                              ss.qualified_name AS source_symbol,
                              ta.name AS target_application,
                              ts.qualified_name AS target_symbol
                         FROM cross_application_edge e
                         JOIN application sa_app ON sa_app.id=e.source_application_id
                         JOIN application ta_app ON ta_app.id=e.target_application_id
                         JOIN application sa ON sa.id=e.source_application_id
                         JOIN application ta ON ta.id=e.target_application_id
                         JOIN code_symbol ss ON ss.id=e.source_symbol_id
                         JOIN code_symbol ts ON ts.id=e.target_symbol_id
                        WHERE sa_app.repository_id=? OR ta_app.repository_id=?
                        ORDER BY sa.name,ss.qualified_name,ta.name,ts.qualified_name""",
                      (repository_id, repository_id))


def _rows(db: Connection, query: str, params=()):
    return db.execute(query, params).fetchall()


def _value(row, key: str, default=None):
    if hasattr(row, "keys"):
        return row[key]
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _fact_label(value: str) -> str:
    return {
        "HTTP_BASE_PATH": "HTTP 基础路径",
        "HTTP_ENDPOINT": "HTTP Endpoint",
        "HTTP_CALL": "HTTP 调用",
        "RPC_SERVICE": "RPC 服务",
        "RPC_CALL": "RPC 调用",
        "MQ_PRODUCER": "消息生产者",
        "MQ_CONSUMER": "消息消费者",
        "JOB": "Job",
        "ROUTE": "Route",
        "PAGE": "页面",
    }.get(value, value)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return re.sub(r"-+", "-", cleaned).strip("-") or "repository"


__all__ = ["generate_code_map"]

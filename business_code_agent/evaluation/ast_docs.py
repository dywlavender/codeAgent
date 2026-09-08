"""AST-arm documents: mechanically derived code structure maps.

The third comparison arm answers whether a tree-sitter derived code map (no
business prose) helps investigation as much as the human business baseline.
It reuses the production indexer — the same tree-sitter Java parser and the
pattern-based web indexer used by ``sync-project`` — over the frozen
repository snapshots, then renders one navigable markdown document per
repository area (gradle module / source root), grouped by package or
directory. Main sources only; behaviour still lives in the source files.
"""

from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from pathlib import Path

from ..indexing import CodeIndexer
from ..schema import connect

MAX_DOC_LINES = 240
MAX_DOC_CHARS = 16000
MAX_METHODS_PER_TYPE = 40
TYPE_KINDS = {"CLASS", "INTERFACE", "ENUM", "RECORD", "PAGE", "COMPONENT"}
METHOD_KINDS = {"METHOD", "API", "FUNCTION"}
ENDPOINT_FACTS = {"HTTP_ENDPOINT", "HTTP_CALL", "RPC_CALL"}
SKIP_TEST_PARTS = {"test", "tests", "__tests__"}


def generate_ast_documents(inputs_dir: Path, repository_names=None) -> dict[str, str]:
    """Index the frozen repo copies and return {file_name: markdown}."""
    documents: dict[str, str] = {}
    names = dict((snapshot, name) for name, snapshot in (repository_names or []))
    overview = ["# AST 结构总览", "", "这是从冻结源码生成的结构导航，不含人工业务主干和题库答案。",
                "先按仓库目录选择结构索引；需要类型、方法或端点线索时按需读取，不要求每题读取。",
                "相对源码路径属于对应 repo-N；实际授权路径见本轮检索范围。结构条目不是已验证调用链，行为仍须读取源码。", "",
                "| 源码目录 | 仓库标识 | 主源码结构索引 |", "| --- | --- | --- |"]
    with tempfile.TemporaryDirectory(prefix="ast-docs-") as folder:
        db = connect(str(Path(folder) / "ast.db"))
        try:
            for repo_dir in sorted(p for p in Path(inputs_dir).iterdir()
                                   if p.is_dir() and p.name.startswith("repo-")):
                CodeIndexer(db).ingest(str(repo_dir), repo_dir.name)
                rendered = _render_repository(db, repo_dir.name)
                documents.update({f"ast/{key}": value for key, value in rendered.items()})
                entries = [f"# {names.get(repo_dir.name, repo_dir.name)} 结构索引", "",
                           f"源码目录：{repo_dir.name}。以下按源码模块分片，使用类型、方法、HTTP路径检索；不代表业务职责。", ""]
                for key, value in sorted(rendered.items()):
                    area = value.splitlines()[0].split(" · ", 1)[-1]
                    entries.append(f"- [{area} / {key}]({key})")
                index_name = f"ast/{repo_dir.name}-index.md"
                documents[index_name] = "\n".join(entries) + "\n"
                overview.append(f"| {repo_dir.name} | {names.get(repo_dir.name, repo_dir.name)} | [{len(rendered)} 份分片]({index_name}) |")
        finally:
            db.close()
    overview += ["", "Java 类型与方法使用 tree-sitter；Web 条目使用现有模式解析。测试仍可从源码读取，但不进入此结构地图。",
                 "方法清单有上限，截断处明确标记；没有条目不等于源码不存在。"]
    # New workspaces call the generated structural entry a project index. Keep
    # the historical filename in frozen AST output so old evaluation batches
    # remain readable and reproducible.
    documents["project-index.md"] = "\n".join(overview) + "\n"
    documents["project-overview.md"] = documents["project-index.md"]
    return documents


def _is_main_source(path: str) -> bool:
    parts = path.split("/")
    return not (set(parts) & SKIP_TEST_PARTS)


def _area(path: str) -> str:
    parts = path.split("/")
    if parts[0] == "src":
        return "/".join(parts[:3])
    return parts[0]


def _group_key(kind: str, qualified_name: str, path: str) -> str:
    if kind in TYPE_KINDS or kind in METHOD_KINDS:
        if kind not in {"PAGE", "COMPONENT", "FUNCTION"} and "." in qualified_name:
            return qualified_name.rsplit(".", 1)[0]
    return str(Path(path).parent)


def _render_repository(db, repo_id: str) -> dict[str, str]:
    symbols = db.execute(
        """SELECT cf.path AS path, cs.kind AS kind, cs.qualified_name AS qualified_name,
                  cs.name AS name, cs.line_start AS line_start
             FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
            WHERE cf.repository_id=? ORDER BY cf.path, cs.line_start""", (repo_id,)).fetchall()
    endpoints = defaultdict(list)
    facts = db.execute(
            """SELECT cf.path AS path, cs.kind AS kind, cs.qualified_name AS qualified_name, cs.line_start AS line_start,
                      f.fact_type AS fact_type, f.subject AS subject, f.target AS target
                 FROM code_fact f JOIN code_symbol cs ON cs.id=f.symbol_id
                 JOIN code_file cf ON cf.id=cs.file_id
                WHERE cf.repository_id=?""", (repo_id,)).fetchall()
    bases = {(row["path"], row["qualified_name"]): row["target"] for row in facts
             if row["fact_type"] == "HTTP_BASE_PATH"}
    for row in facts:
        owner = row["qualified_name"].rsplit(".", 1)[0] if row["kind"] in METHOD_KINDS else row["qualified_name"]
        key = (row["path"], owner)
        if row["fact_type"] in ENDPOINT_FACTS:
            target = row["target"]
            if row["fact_type"] in {"HTTP_ENDPOINT", "RPC_CALL"} and bases.get(key):
                target = bases[key].rstrip("/") + "/" + target.lstrip("/")
            endpoints[key].append(
                f"{row['fact_type']} {row['subject']} {target} (L{row['line_start']})")
        elif row["fact_type"] == "ROUTE":
            endpoints[key].append(
                f"ROUTE {row['subject']} → {row['target']} (L{row['line_start']})")

    areas: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    type_methods: dict[str, list[str]] = defaultdict(list)
    type_rows: dict[str, tuple] = {}
    for row in symbols:
        if not _is_main_source(row["path"]):
            continue
        area = _area(row["path"])
        if row["kind"] in TYPE_KINDS:
            key = (row["path"], row["qualified_name"])
            type_rows[key] = (row["kind"], row["name"], row["path"], row["line_start"])
            areas[area][_group_key(row["kind"], row["qualified_name"], row["path"])].append(key)
        elif row["kind"] in METHOD_KINDS:
            owner = row["qualified_name"].rsplit(".", 1)[0] if "." in row["qualified_name"] else row["qualified_name"]
            type_methods[(row["path"], owner)].append(f"{row['name']}(L{row['line_start']})")

    documents: dict[str, str] = {}
    for area in sorted(areas):
        sections = []
        for group in sorted(areas[area]):
            for key in areas[area][group]:
                lines = [f"## {group}", ""]
                kind, name, path, line_start = type_rows[key]
                lines.append(f"- {name} — {kind} ({path}:{line_start})")
                endpoint_list = endpoints.get(key)
                if endpoint_list:
                    for item in endpoint_list[:12]:
                        lines.append(f"  - 端点: {item}")
                methods = type_methods.get(key) or []
                shown = methods[:MAX_METHODS_PER_TYPE]
                method_line = "、".join(shown)
                if len(methods) > MAX_METHODS_PER_TYPE:
                    method_line += f" …（另有 {len(methods) - MAX_METHODS_PER_TYPE} 个方法）"
                if method_line:
                    lines.append(f"  - 方法: {method_line}")
                sections.append("\n".join(lines))

        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", area).strip("-") or "root"
        header = (f"# AST 结构地图 — {repo_id} · {area}\n\n"
                  "来源：tree-sitter/模式解析的机械清单（类型、方法、HTTP 端点），不含业务解释；"
                  "仅主源码，测试代码不在地图内。清单只用于定位，行为以源码为准。\n")
        documents.update(_split(f"{repo_id}-{slug}", header, sections))
    return documents


def _split(base_name: str, header: str, sections: list[str]) -> dict[str, str]:
    documents: dict[str, str] = {}
    current: list[str] = []
    part = 1
    for section in sections:
        candidate = _join(header, [*current, section])
        if current and (len(candidate.splitlines()) > MAX_DOC_LINES or len(candidate) > MAX_DOC_CHARS):
            documents[f"{base_name}-{part:02d}.md"] = _join(header, current)
            part += 1
            current = []
        current.append(section)
    if current:
        name = f"{base_name}.md" if part == 1 else f"{base_name}-{part:02d}.md"
        documents[name] = _join(header, current)
    return documents


def _join(header: str, sections: list[str]) -> str:
    return header + "\n" + "\n\n".join(sections) + "\n"

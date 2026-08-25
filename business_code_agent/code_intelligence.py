from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection

from .util import digest, stable_id


CLASS_RE = re.compile(r"\b(class|interface|enum)\s+(\w+)")
METHOD_RE = re.compile(r"^\s*(?:public|protected|private|static|final|synchronized|abstract|native|\s)+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{?")
FIELD_RE = re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], ?.]+\s+(\w+)\s*(?:=[^;]*)?;")
SET_RE = re.compile(r"\b(\w+)\.set([A-Z]\w*)\s*\(")
GET_RE = re.compile(r"\b(\w+)\.get([A-Z]\w*)\s*\(")
CHECK_HINT_RE = re.compile(r"\b(if|assert|throw)\b|\.equals\s*\(")
CALL_RE = re.compile(r"\b(\w+)\.(\w+)\s*\(")
SQL_TABLE_RE = re.compile(r"\b(?:from|join|update|into)\s+([\w.]+)", re.IGNORECASE)
SQL_COLUMN_RE = re.compile(r"\b(?:select|where|set|and|or|,)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
INSERT_COLUMNS_RE = re.compile(r"\binsert\s+into\s+[\w.]+\s*\(([^)]+)\)", re.IGNORECASE | re.DOTALL)
UPDATE_SET_RE = re.compile(r"\bset\s+([\w.]+)\s*=", re.IGNORECASE)
SKIP_DIRS = {"target", "build", ".git", ".idea", ".gradle", "node_modules"}


class JavaIndexer:
    """Conservative M1 parser with a replaceable Tree-sitter boundary.

    It indexes only directly observable syntax. It deliberately does not claim
    cross-method data flow or resolve Java types.
    """

    def __init__(self, db: Connection):
        self.db = db
        self.syntax_backend = self._load_tree_sitter()
        self._pending_function_revalidations: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _load_tree_sitter():
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java

            return Parser(Language(tree_sitter_java.language()))
        except ImportError:
            return None

    def ingest(self, root: str, repository_id: str = "repo-main") -> dict[str, int]:
        root_path = Path(root).resolve()
        self._pending_function_revalidations.clear()
        self.db.execute(
            "INSERT OR REPLACE INTO repository VALUES (?, ?, ?)",
            (repository_id, str(root_path), datetime.now(timezone.utc).isoformat()),
        )
        counts = {"files": 0, "symbols": 0, "facts": 0}
        for path in self._source_files(root_path, "*.java"):
            result = self._ingest_file(repository_id, root_path, path)
            counts = {key: counts[key] + result[key] for key in counts}
        for path in self._source_files(root_path, "*.xml"):
            result = self._ingest_mybatis(repository_id, root_path, path)
            counts = {key: counts[key] + result[key] for key in counts}
        self._remove_deleted(repository_id, root_path)
        self._flush_function_revalidations()
        self.db.commit()
        if counts["files"]:
            counts["symbols"] = self.db.execute(
                """SELECT count(*) FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                     WHERE cf.repository_id=?""",
                (repository_id,),
            ).fetchone()[0]
            counts["facts"] = self.db.execute(
                """SELECT count(*) FROM code_fact f JOIN code_symbol cs ON cs.id=f.symbol_id
                     JOIN code_file cf ON cf.id=cs.file_id WHERE cf.repository_id=?""",
                (repository_id,),
            ).fetchone()[0]
        return counts

    @staticmethod
    def _source_files(root: Path, pattern: str) -> list[Path]:
        return sorted(
            path for path in root.rglob(pattern)
            if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
        )

    def _ingest_file(self, repository_id: str, root: Path, path: Path) -> dict[str, int]:
        content = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root))
        file_id = stable_id("CF", repository_id, relative)
        old = self.db.execute("SELECT content_hash FROM code_file WHERE id=?", (file_id,)).fetchone()
        if old and old[0] == digest(content):
            return {"files": 0, "symbols": 0, "facts": 0}
        if old:
            self._archive_file_evidence(file_id, "SOURCE_MODIFIED", relative)
            self._mark_file_relations_stale(file_id, "SOURCE_MODIFIED", relative)
        self.db.execute("DELETE FROM code_fact WHERE symbol_id IN (SELECT id FROM code_symbol WHERE file_id=?)", (file_id,))
        self.db.execute("DELETE FROM code_symbol WHERE file_id=?", (file_id,))
        self.db.execute("INSERT OR REPLACE INTO code_file VALUES (?, ?, ?, ?)", (file_id, repository_id, relative, digest(content)))
        self._record_change(repository_id, relative, old[0] if old else None, digest(content))
        if self.syntax_backend is not None:
            tree = self.syntax_backend.parse(content.encode("utf-8"))
            self.db.execute(
                "INSERT OR REPLACE INTO parse_diagnostic VALUES (?, 'tree-sitter-java', ?, ?, ?)",
                (file_id, int(tree.root_node.has_error), tree.root_node.type, datetime.now(timezone.utc).isoformat()),
            )
            if not tree.root_node.has_error:
                return self._index_tree(file_id, relative, content, tree.root_node)
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO parse_diagnostic VALUES (?, 'conservative-pattern', 0, 'unknown', ?)",
                (file_id, datetime.now(timezone.utc).isoformat()),
            )

        lines = content.splitlines()
        class_name = path.stem
        package = ""
        current_symbol: tuple[str, str] | None = None
        symbols = facts = 0
        brace_depth = 0
        method_depth = -1
        pending_api_annotation = False
        for number, line in enumerate(lines, 1):
            package_match = re.search(r"^\s*package\s+([\w.]+)", line)
            if package_match:
                package = package_match.group(1)
            class_match = CLASS_RE.search(line)
            if class_match:
                class_name = class_match.group(2)
                qualified = f"{package}.{class_name}" if package else class_name
                sid = stable_id("SYM", file_id, qualified, str(number))
                self._symbol(sid, file_id, class_match.group(1).upper(), qualified, class_name, number, len(lines))
                symbols += 1
            method_match = METHOD_RE.match(line)
            if method_match and method_match.group(1) not in {"if", "for", "while", "switch", "catch"}:
                method = method_match.group(1)
                qualified = f"{package + '.' if package else ''}{class_name}.{method}"
                sid = stable_id("SYM", file_id, qualified, str(number))
                symbol_kind = "API" if pending_api_annotation or re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b", line) else "METHOD"
                self._symbol(sid, file_id, symbol_kind, qualified, method, number, len(lines))
                current_symbol = (sid, qualified)
                method_depth = brace_depth
                symbols += 1
                pending_api_annotation = False
            elif re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b", line):
                pending_api_annotation = True
            elif line.strip() and not line.lstrip().startswith("@"):
                pending_api_annotation = False
            if current_symbol is None and brace_depth >= 1:
                field_match = FIELD_RE.match(line)
                if field_match:
                    field_name = field_match.group(1)
                    field_qualified = f"{package + '.' if package else ''}{class_name}.{field_name}"
                    field_id = stable_id("SYM", file_id, field_qualified, str(number))
                    self._symbol(field_id, file_id, "FIELD", field_qualified, field_name, number, number)
                    symbols += 1
            if current_symbol:
                for match in SET_RE.finditer(line):
                    field = match.group(2)[0].lower() + match.group(2)[1:]
                    self._fact(current_symbol, "WRITE_FIELD", field, f"{match.group(1)}.{field}", file_id, relative, number, line)
                    facts += 1
                for match in GET_RE.finditer(line):
                    field = match.group(2)[0].lower() + match.group(2)[1:]
                    kind = "CHECK_FIELD" if CHECK_HINT_RE.search(line) else "READ_FIELD"
                    self._fact(current_symbol, kind, field, f"{match.group(1)}.{field}", file_id, relative, number, line)
                    facts += 1
                for match in CALL_RE.finditer(line):
                    if match.group(2).startswith(("get", "set")):
                        continue
                    self._fact(current_symbol, "CALL", match.group(2), f"{match.group(1)}.{match.group(2)}", file_id, relative, number, line)
                    facts += 1
            brace_depth += line.count("{") - line.count("}")
            if current_symbol and brace_depth <= method_depth:
                self.db.execute("UPDATE code_symbol SET line_end=? WHERE id=?", (number, current_symbol[0]))
                current_symbol = None
        return {"files": 1, "symbols": symbols, "facts": facts}

    def _index_tree(self, file_id: str, relative: str, content: str, root_node) -> dict[str, int]:
        source = content.encode("utf-8")
        package_node = next((node for node in root_node.children if node.type == "package_declaration"), None)
        package = ""
        if package_node:
            package = self._node_text(source, package_node).removeprefix("package").removesuffix(";").strip()
        symbols = facts = 0

        def visit_types(node, parents: list[str]) -> None:
            nonlocal symbols, facts
            if node.type not in {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}:
                for child in node.children:
                    visit_types(child, parents)
                return
            name_node = node.child_by_field_name("name")
            if not name_node:
                return
            name = self._node_text(source, name_node)
            qualified_type = ".".join(filter(None, [package, *parents, name]))
            line_start, line_end = node.start_point.row + 1, node.end_point.row + 1
            sid = stable_id("SYM", file_id, qualified_type, str(line_start))
            self._symbol(sid, file_id, node.type.removesuffix("_declaration").upper(), qualified_type, name, line_start, line_end)
            symbols += 1
            body = node.child_by_field_name("body")
            if not body:
                return
            for child in body.children:
                if child.type in {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}:
                    visit_types(child, [*parents, name])
                elif child.type in {"method_declaration", "constructor_declaration"}:
                    method_name_node = child.child_by_field_name("name")
                    if not method_name_node:
                        continue
                    method_name = self._node_text(source, method_name_node)
                    qualified = f"{qualified_type}.{method_name}"
                    method_start, method_end = child.start_point.row + 1, child.end_point.row + 1
                    method_id = stable_id("SYM", file_id, qualified, str(method_start))
                    method_source = self._node_text(source, child)
                    symbol_kind = "API" if re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b", method_source) else "METHOD"
                    self._symbol(method_id, file_id, symbol_kind, qualified, method_name, method_start, method_end)
                    symbols += 1
                    for invocation, ancestors in self._invocations(child):
                        name_part = invocation.child_by_field_name("name")
                        if not name_part:
                            continue
                        call_name = self._node_text(source, name_part)
                        invocation_text = self._node_text(source, invocation)
                        line_no = invocation.start_point.row + 1
                        arguments = invocation.child_by_field_name("arguments")
                        argument_count = sum(1 for item in arguments.named_children) if arguments else 0
                        if call_name.startswith("set") and len(call_name) > 3 and argument_count == 1:
                            field = call_name[3].lower() + call_name[4:]
                            kind = "WRITE_FIELD"
                        elif call_name.startswith("get") and len(call_name) > 3 and argument_count == 0:
                            field = call_name[3].lower() + call_name[4:]
                            kind = "CHECK_FIELD" if any(parent.type in {"if_statement", "assert_statement", "throw_statement"} for parent in ancestors) else "READ_FIELD"
                        else:
                            field = call_name
                            kind = "CALL"
                        self._fact(
                            (method_id, qualified), kind, field, invocation_text,
                            file_id, relative, line_no, invocation_text,
                            end_line=invocation.end_point.row + 1,
                        )
                        facts += 1
                elif child.type in {"field_declaration", "constant_declaration"}:
                    for declarator in child.named_children:
                        if declarator.type != "variable_declarator":
                            continue
                        field_name_node = declarator.child_by_field_name("name")
                        if not field_name_node:
                            continue
                        field_name = self._node_text(source, field_name_node)
                        field_qualified = f"{qualified_type}.{field_name}"
                        field_line = declarator.start_point.row + 1
                        field_id = stable_id("SYM", file_id, field_qualified, str(field_line))
                        self._symbol(field_id, file_id, "FIELD", field_qualified, field_name, field_line, declarator.end_point.row + 1)
                        symbols += 1

        visit_types(root_node, [])
        return {"files": 1, "symbols": symbols, "facts": facts}

    @staticmethod
    def _node_text(source: bytes, node) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8")

    @staticmethod
    def _invocations(method_node):
        results = []

        def walk(node, ancestors):
            if node.type == "method_invocation":
                results.append((node, ancestors))
            for child in node.children:
                walk(child, [*ancestors, node])

        body = method_node.child_by_field_name("body")
        if body:
            walk(body, [method_node])
        return results

    def _ingest_mybatis(self, repository_id: str, root: Path, path: Path) -> dict[str, int]:
        content = path.read_text(encoding="utf-8")
        if "<mapper" not in content:
            return {"files": 0, "symbols": 0, "facts": 0}
        relative = str(path.relative_to(root))
        file_id = stable_id("CF", repository_id, relative)
        old = self.db.execute("SELECT content_hash FROM code_file WHERE id=?", (file_id,)).fetchone()
        if old and old[0] == digest(content):
            return {"files": 0, "symbols": 0, "facts": 0}
        if old:
            self._archive_file_evidence(file_id, "SOURCE_MODIFIED", relative)
            self._mark_file_relations_stale(file_id, "SOURCE_MODIFIED", relative)
        self.db.execute("DELETE FROM code_fact WHERE symbol_id IN (SELECT id FROM code_symbol WHERE file_id=?)", (file_id,))
        self.db.execute("DELETE FROM code_symbol WHERE file_id=?", (file_id,))
        self.db.execute("INSERT OR REPLACE INTO code_file VALUES (?, ?, ?, ?)", (file_id, repository_id, relative, digest(content)))
        self._record_change(repository_id, relative, old[0] if old else None, digest(content))
        try:
            mapper = ET.fromstring(content)
        except ET.ParseError:
            return {"files": 1, "symbols": 0, "facts": 0}
        namespace = mapper.attrib.get("namespace", path.stem)
        result_maps: dict[str, list[tuple[str, str]]] = {}
        for node in mapper:
            if node.tag.rsplit("}", 1)[-1] != "resultMap" or not node.attrib.get("id"):
                continue
            mappings = []
            for child in node:
                column = child.attrib.get("column")
                prop = child.attrib.get("property")
                if column and prop:
                    mappings.append((column, prop))
            result_maps[node.attrib["id"]] = mappings
        symbols = facts = 0
        lines = content.splitlines()
        for node in mapper:
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in {"select", "insert", "update", "delete"} or not node.attrib.get("id"):
                continue
            name = node.attrib["id"]
            qualified = f"{namespace}.{name}"
            line_no = next((i for i, line in enumerate(lines, 1) if f'id="{name}"' in line or f"id='{name}'" in line), 1)
            closing = f"</{tag}>"
            end_line = next(
                (i for i, line in enumerate(lines[line_no - 1:], line_no) if closing in line),
                line_no,
            )
            sid = stable_id("SYM", file_id, qualified, str(line_no))
            self._symbol(sid, file_id, "MYBATIS_STATEMENT", qualified, name, line_no, end_line)
            symbols += 1
            sql = " ".join("".join(node.itertext()).split())
            for table in dict.fromkeys(SQL_TABLE_RE.findall(sql)):
                kind = "READ_TABLE" if tag == "select" else "WRITE_TABLE"
                self._fact((sid, qualified), kind, table, table, file_id, relative, line_no, sql, end_line=end_line)
                facts += 1
            for column in dict.fromkeys(SQL_COLUMN_RE.findall(sql)):
                kind = "READ_COLUMN" if tag == "select" else "WRITE_COLUMN"
                self._fact((sid, qualified), kind, column.split(".")[-1], column, file_id, relative, line_no, sql, end_line=end_line)
                facts += 1
            if tag == "select" and re.search(r"\bselect\s+\*", sql, re.IGNORECASE):
                result_map = node.attrib.get("resultMap")
                for column, prop in result_maps.get(result_map, []):
                    # Store the property as target so discovery can bridge
                    # snake_case DB columns and camelCase entity fields.
                    self._fact((sid, qualified), "READ_COLUMN", column, prop, file_id, relative, line_no, sql, end_line=end_line)
                    facts += 1
            write_columns: list[str] = []
            if tag == "insert":
                for group in INSERT_COLUMNS_RE.findall(sql):
                    write_columns.extend(item.strip().split(".")[-1] for item in group.split(",") if item.strip())
            elif tag == "update":
                write_columns.extend(item.split(".")[-1] for item in UPDATE_SET_RE.findall(sql))
            for column in dict.fromkeys(write_columns):
                if not re.fullmatch(r"[A-Za-z_][\w]*", column):
                    continue
                self._fact((sid, qualified), "WRITE_COLUMN", column, column, file_id, relative, line_no, sql, end_line=end_line)
                facts += 1
        return {"files": 1, "symbols": symbols, "facts": facts}

    def _record_change(self, repository_id: str, relative: str, previous: str | None, current: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        change_id = stable_id("CHG", repository_id, relative, current)
        self.db.execute(
            "INSERT OR IGNORE INTO ingestion_change VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change_id, repository_id, relative, "ADDED" if previous is None else "MODIFIED", previous, current, now),
        )

    def _remove_deleted(self, repository_id: str, root: Path) -> None:
        indexed = self.db.execute("SELECT id, path, content_hash FROM code_file WHERE repository_id=?", (repository_id,)).fetchall()
        for row in indexed:
            if (root / row["path"]).exists():
                continue
            self._mark_file_relations_stale(row["id"], "SOURCE_DELETED", row["path"])
            self._archive_file_evidence(row["id"], "SOURCE_DELETED", row["path"])
            self.db.execute("DELETE FROM code_fact WHERE symbol_id IN (SELECT id FROM code_symbol WHERE file_id=?)", (row["id"],))
            self.db.execute("DELETE FROM code_symbol WHERE file_id=?", (row["id"],))
            self.db.execute("DELETE FROM parse_diagnostic WHERE file_id=?", (row["id"],))
            self.db.execute("DELETE FROM code_file WHERE id=?", (row["id"],))
            now = datetime.now(timezone.utc).isoformat()
            change_id = stable_id("CHG", repository_id, row["path"], "DELETED", row["content_hash"])
            self.db.execute(
                "INSERT OR IGNORE INTO ingestion_change VALUES (?, ?, ?, 'DELETED', ?, NULL, ?)",
                (change_id, repository_id, row["path"], row["content_hash"], now),
            )

    def _mark_file_relations_stale(self, file_id: str, trigger_type: str, path: str) -> None:
        # Code changes invalidate evidence, not published knowledge directly.
        # The canonical governance workflow creates one review proposal per
        # affected function in ``_collect_function_revalidation`` below.
        self._collect_function_revalidation(file_id, trigger_type, path)

    def _collect_function_revalidation(self, file_id: str, trigger_type: str, path: str) -> None:
        """Create one review proposal per affected function, never per changed fact."""
        affected = self.db.execute(
            """SELECT DISTINCT bf.id function_id, bf.current_version_id, e.id evidence_id
                 FROM business_function bf
                 JOIN business_function_version bfv ON bfv.id=bf.current_version_id
                 JOIN function_item_evidence fie ON fie.function_version_id=bfv.id
                 JOIN evidence e ON e.id=fie.evidence_id
                WHERE bf.status='PUBLISHED' AND e.source_type='CODE' AND e.source_id=?""",
            (file_id,),
        ).fetchall()
        grouped: dict[str, dict] = {}
        for row in affected:
            item = grouped.setdefault(row["function_id"], {
                "version_id": row["current_version_id"], "evidence_ids": [],
            })
            item["evidence_ids"].append(row["evidence_id"])
        trigger_id = f"{file_id}:{path}"
        for function_id, value in grouped.items():
            key = (function_id, trigger_type, trigger_id)
            pending = self._pending_function_revalidations.setdefault(key, {
                "function_id": function_id,
                "trigger_type": trigger_type,
                "trigger_id": trigger_id,
                "path": path,
                "file_id": file_id,
                "version_id": value["version_id"],
                "evidence_ids": [],
            })
            pending["evidence_ids"].extend(value["evidence_ids"])

    def _flush_function_revalidations(self) -> None:
        if not self._pending_function_revalidations:
            return
        from .knowledge_update.repository import KnowledgeGovernanceRepository, retain_active_evidence

        repository = KnowledgeGovernanceRepository(self.db)
        for value in self._pending_function_revalidations.values():
            function_id = value["function_id"]
            trigger_type = value["trigger_type"]
            trigger_id = value["trigger_id"]
            path = value["path"]
            new_evidence = [row[0] for row in self.db.execute(
                """SELECT DISTINCT cf.evidence_id FROM code_fact cf
                     JOIN code_symbol cs ON cs.id=cf.symbol_id
                    WHERE cs.file_id=? ORDER BY cf.evidence_id LIMIT 40""",
                (value["file_id"],),
            )]
            existing = self.db.execute(
                """SELECT id FROM knowledge_update_proposal
                    WHERE target_function_id=? AND trigger_type=? AND trigger_id=?
                      AND status IN ('DRAFT','PENDING_REVIEW','DEFERRED','CHANGES_REQUESTED')
                    LIMIT 1""",
                (function_id, trigger_type, trigger_id),
            ).fetchone()
            if existing:
                continue
            current = repository.get_function(function_id)
            snapshot = retain_active_evidence(self.db, current["snapshot"])
            matched_evidence: list[str] = []
            for entry in snapshot.get("entries", []):
                target_id = str(entry.get("target_id") or "")
                if not target_id:
                    continue
                entry_evidence = [row[0] for row in self.db.execute(
                    "SELECT evidence_id FROM code_fact WHERE symbol_id=? ORDER BY evidence_id LIMIT 20",
                    (target_id,),
                )]
                if entry_evidence:
                    entry["evidence_ids"] = entry_evidence
                    matched_evidence.extend(entry_evidence)
            snapshot["evidence_ids"] = list(dict.fromkeys([
                *snapshot.get("evidence_ids", []), *matched_evidence,
            ]))
            proposal = repository.create_proposal(
                f"重新验证功能知识：{current['function']['name']}",
                trigger_type,
                trigger_id,
                snapshot,
                "code-indexer",
                target_function_id=function_id,
                action="UPDATE",
                summary=f"关联源码 {path} 已变化，需要确认业务语义是否仍然有效",
                base_version_id=value["version_id"],
            )
            proposal_id = proposal["proposal"]["id"]
            repository.add_proposal_item(
                proposal_id,
                "REVALIDATE",
                "EVIDENCE",
                before={"status": "ACTIVE", "file": path},
                after={"status": "REQUIRES_REVIEW", "file": path},
                rationale="已发布功能引用的代码证据发生变化；仅标记复核，不自动修改业务知识",
                confidence=1.0,
                evidence_ids=list(dict.fromkeys([*value["evidence_ids"], *new_evidence])),
            )
            repository.submit_proposal(proposal_id)

    def _archive_file_evidence(self, file_id: str, trigger_type: str, path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """UPDATE evidence_lifecycle SET status='HISTORICAL', superseded_at=?,
                      trigger_type=?, trigger_id=?
                 WHERE evidence_id IN (
                   SELECT cf.evidence_id FROM code_fact cf
                   JOIN code_symbol cs ON cs.id=cf.symbol_id WHERE cs.file_id=?
                 )""",
            (now, trigger_type, path, file_id),
        )

    def _symbol(self, sid: str, file_id: str, kind: str, qualified: str, name: str, start: int, end: int) -> None:
        self.db.execute("INSERT INTO code_symbol VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, file_id, kind, qualified, name, start, end))

    def _fact(self, symbol: tuple[str, str], kind: str, subject: str, target: str, file_id: str, path: str, line_no: int, line: str, *, end_line: int | None = None) -> None:
        file_row = self.db.execute("SELECT content_hash FROM code_file WHERE id=?", (file_id,)).fetchone()
        source_version = file_row["content_hash"] if file_row else "unknown"
        evidence_id = stable_id("EV", "CODE", file_id, source_version, str(line_no), line.strip())
        self.db.execute(
            "INSERT OR REPLACE INTO evidence VALUES (?, 'CODE', ?, ?, ?, ?, ?, NULL, ?, ?)",
            (evidence_id, file_id, source_version, path, line_no, end_line or line_no, digest(line.strip()), line.strip()),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO evidence_lifecycle VALUES (?, 'ACTIVE', NULL, NULL, NULL)",
            (evidence_id,),
        )
        fact_id = stable_id("FACT", symbol[0], kind, subject, target, evidence_id)
        self.db.execute("INSERT OR IGNORE INTO code_fact VALUES (?, ?, ?, ?, ?, ?)", (fact_id, symbol[0], kind, subject, target, evidence_id))

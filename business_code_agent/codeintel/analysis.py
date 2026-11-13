from __future__ import annotations

import logging

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection

from business_code_agent.shared.util import digest, stable_id


CLASS_RE = re.compile(r"\b(class|interface|enum)\s+(\w+)")
METHOD_RE = re.compile(r"^\s*(?:public|protected|private|static|final|synchronized|abstract|native|\s)+[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{?")
FIELD_RE = re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], ?.]+\s+(\w+)\s*(?:=[^;]*)?;")
SET_RE = re.compile(r"\b(\w+)\.set([A-Z]\w*)\s*\(")
GET_RE = re.compile(r"\b(\w+)\.get([A-Z]\w*)\s*\(")
CHECK_HINT_RE = re.compile(r"\b(if|assert|throw)\b|\.equals\s*\(")
CALL_RE = re.compile(r"\b(\w+)\.(\w+)\s*\(")
BARE_CALL_RE = re.compile(r"^(\w+)\s*\(")
RECEIVER_CALL_RE = re.compile(r"^(\w+)\.(\w+)(?:\s*\(|$)")
EXTENDS_RE = re.compile(r"\bextends\s+([A-Za-z_$][\w$]*)")
IMPLEMENTS_RE = re.compile(r"\bimplements\s+([A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)")
SQL_TABLE_RE = re.compile(r"\b(?:from|join|update|into)\s+([\w.]+)", re.IGNORECASE)
SQL_COLUMN_RE = re.compile(r"\b(?:select|where|set|and|or|,)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
INSERT_COLUMNS_RE = re.compile(r"\binsert\s+into\s+[\w.]+\s*\(([^)]+)\)", re.IGNORECASE | re.DOTALL)
UPDATE_SET_RE = re.compile(r"\bset\s+([\w.]+)\s*=", re.IGNORECASE)
SKIP_DIRS = {"target", "build", ".git", ".idea", ".gradle", "node_modules"}
# Symbol kinds that may serve as edge endpoints. Types receive INHERITS edges
# and receive calls; callables are call targets. Web modules (PAGE/COMPONENT)
# use the same code_symbol/code_fact tables as Java types.
RESOLVABLE_TYPE_KINDS = ("CLASS", "INTERFACE", "ENUM", "RECORD", "PAGE", "COMPONENT")
CALLABLE_KINDS = ("METHOD", "API", "FUNCTION")
RESOLVED_EDGE_FACTS = ("CALLS", "INHERITS", "IMPLEMENTS")
INHERITS_DECL_FACT = "INHERITS_DECL"
IMPLEMENTS_DECL_FACT = "IMPLEMENTS_DECL"
# Header clause node types that may declare parents, and the subtree node
# types whose contents are generic arguments rather than declared parents.
# tree-sitter-java names the implements clause "super_interfaces"; the other
# spellings keep extraction tolerant across grammar versions. A class's
# "permits" clause declares children (the reverse direction) and is not a
# parent edge, so it is deliberately not collected.
_INHERITS_CLAUSE_NODE_TYPES = {"extends", "superclass", "extends_interfaces"}
_IMPLEMENTS_CLAUSE_NODE_TYPES = {"super_interfaces", "interfaces", "implements"}
_GENERIC_SUBTREE_NODE_TYPES = {"type_arguments", "type_parameters", "wildcard", "super", "extends_super"}


logger = logging.getLogger(__name__)


class JavaIndexer:
    """Conservative parser with a replaceable Tree-sitter boundary.

    It indexes only directly observable syntax and deliberately does not claim
    cross-method data flow. On top of the raw facts it maintains one resolved
    edge layer: same-repository INHERITS and CALLS facts whose endpoints are
    unambiguously resolved to indexed symbols. Raw CALL and INHERITS_DECL
    facts stay untouched as the pending layer; anything that cannot be
    resolved to exactly one same-repository symbol is omitted, never guessed.
    """

    def __init__(self, db: Connection):
        self.db = db
        self.syntax_backend = self._load_tree_sitter()

    @staticmethod
    def _load_tree_sitter():
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java

            return Parser(Language(tree_sitter_java.language()))
        except ImportError:
            logger.warning("tree-sitter 未安装，Java 解析使用内置解析器（pip install '.[tree-sitter]' 可启用）")
            return None

    def ingest(self, root: str, repository_id: str = "repo-main") -> dict[str, int]:
        root_path = Path(root).resolve()
        self.db.execute(
            "INSERT OR REPLACE INTO repository VALUES (?, ?, ?)",
            (repository_id, str(root_path), datetime.now(timezone.utc).isoformat()),
        )
        counts = {"files": 0, "symbols": 0, "facts": 0}
        logger.info("开始索引仓库 %s 于 %s", repository_id, root_path)
        for path in self._source_files(root_path, "*.java"):
            result = self._ingest_file(repository_id, root_path, path)
            counts = {key: counts[key] + result[key] for key in counts}
        for path in self._source_files(root_path, "*.xml"):
            result = self._ingest_mybatis(repository_id, root_path, path)
            counts = {key: counts[key] + result[key] for key in counts}
        self._remove_deleted(repository_id, root_path)
        counts["edges"] = self.resolve_edges(repository_id)
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
        logger.info("索引完成 %s：files=%d symbols=%d facts=%d", repository_id, counts["files"], counts["symbols"], counts["facts"])
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
        integration_source = bool(re.search(r"@(FeignClient|(?:Get|Post|Put|Delete|Patch|Request)Mapping)\b", content))
        integration_indexed = self.db.execute(
            """SELECT 1 FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                 WHERE s.file_id=? AND f.fact_type IN ('HTTP_BASE_PATH','HTTP_ENDPOINT','RPC_SERVICE','RPC_CALL') LIMIT 1""",
            (file_id,),
        ).fetchone()
        declarations_indexed = self.db.execute(
            """SELECT 1 FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                 WHERE s.file_id=? AND f.fact_type='CODE_DECLARATION' LIMIT 1""",
            (file_id,),
        ).fetchone()
        declaration_expected = self.syntax_backend is not None or any(
            METHOD_RE.match(line) for line in content.splitlines()
        )
        if old and old[0] == digest(content) and (not declaration_expected or declarations_indexed) and (not integration_source or integration_indexed):
            return {"files": 0, "symbols": 0, "facts": 0}
        if old:
            self._archive_file_evidence(file_id, "SOURCE_MODIFIED", relative)
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
                result = self._index_tree(file_id, relative, content, tree.root_node)
                result["facts"] += self._index_spring_integrations(file_id, relative, content)
                return result
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
                for parent, edge_kind in _declared_parents(line):
                    self._fact((sid, qualified), f"{edge_kind}_DECL", qualified, parent,
                               file_id, relative, number, line)
                    facts += 1
            method_match = METHOD_RE.match(line)
            if method_match and method_match.group(1) not in {"if", "for", "while", "switch", "catch"}:
                method = method_match.group(1)
                qualified = f"{package + '.' if package else ''}{class_name}.{method}"
                sid = stable_id("SYM", file_id, qualified, str(number))
                symbol_kind = "API" if pending_api_annotation or re.search(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b", line) else "METHOD"
                self._symbol(sid, file_id, symbol_kind, qualified, method, number, len(lines))
                self._fact((sid, qualified), "CODE_DECLARATION", "METHOD", qualified, file_id, relative, number, line)
                current_symbol = (sid, qualified)
                method_depth = brace_depth
                symbols += 1
                facts += 1
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
        facts += self._index_spring_integrations(file_id, relative, content)
        return {"files": 1, "symbols": symbols, "facts": facts}

    def _index_spring_integrations(self, file_id: str, relative: str, content: str) -> int:
        """Extract Spring endpoints and Feign calls without merging their evidence."""
        type_match = re.search(r"\b(class|interface)\s+([A-Za-z_$][\w$]*)", content)
        if not type_match:
            return 0
        class_row = self.db.execute(
            """SELECT id,qualified_name,line_start FROM code_symbol
                 WHERE file_id=? AND kind IN ('CLASS','INTERFACE')
                 ORDER BY line_start LIMIT 1""",
            (file_id,),
        ).fetchone()
        if not class_row:
            return 0
        before_type = content[:type_match.start()]
        feign_matches = list(re.finditer(r"@FeignClient\s*\((.*?)\)", before_type, re.DOTALL))
        controller = bool(re.search(r"@(RestController|Controller)\b", before_type))
        facts = 0
        if feign_matches:
            annotation = feign_matches[-1]
            service = _annotation_string(annotation.group(1), keys=("name", "value"))
            if service:
                line = _line_number(content, annotation.start())
                self._fact(
                    (class_row["id"], class_row["qualified_name"]), "RPC_SERVICE", service, service,
                    file_id, relative, line, annotation.group(0),
                    end_line=_line_number(content, annotation.end()),
                )
                facts += 1

        class_mappings = list(re.finditer(r"@RequestMapping\s*\((.*?)\)", before_type, re.DOTALL))
        if class_mappings:
            annotation = class_mappings[-1]
            base_path = _annotation_string(annotation.group(1), keys=("path", "value"))
            if base_path:
                line = _line_number(content, annotation.start())
                self._fact(
                    (class_row["id"], class_row["qualified_name"]), "HTTP_BASE_PATH", "ANY", _normalize_http_path(base_path),
                    file_id, relative, line, annotation.group(0),
                    end_line=_line_number(content, annotation.end()),
                )
                facts += 1

        mapping_re = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\((.*?)\))?", re.DOTALL)
        method_rows = self.db.execute(
            """SELECT id,qualified_name,line_start FROM code_symbol
                 WHERE file_id=? AND kind IN ('API','METHOD') ORDER BY line_start""",
            (file_id,),
        ).fetchall()
        for annotation in mapping_re.finditer(content):
            if annotation.start() < type_match.start():
                continue
            annotation_line = _line_number(content, annotation.start())
            method_row = next(
                (row for row in method_rows if annotation_line <= row["line_start"] <= annotation_line + 20),
                None,
            )
            if not method_row:
                continue
            method = annotation.group(1).upper()
            arguments = annotation.group(2) or ""
            if method == "REQUEST":
                request_method = re.search(r"RequestMethod\.([A-Z]+)", arguments)
                method = request_method.group(1) if request_method else "ANY"
            path = _annotation_string(arguments, keys=("path", "value")) or "/"
            fact_type = "RPC_CALL" if feign_matches else "HTTP_ENDPOINT" if controller else ""
            if not fact_type:
                continue
            self._fact(
                (method_row["id"], method_row["qualified_name"]), fact_type, method, _normalize_http_path(path),
                file_id, relative, annotation_line, annotation.group(0),
                end_line=_line_number(content, annotation.end()),
            )
            facts += 1
        return facts

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
            for parent_name, edge_kind, clause_text in self._declared_parent_types(source, node):
                self._fact((sid, qualified_type), f"{edge_kind}_DECL", qualified_type, parent_name,
                           file_id, relative, line_start, clause_text)
                facts += 1
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
                    declaration = content.splitlines()[method_start - 1].strip()
                    self._fact(
                        (method_id, qualified), "CODE_DECLARATION", "METHOD", qualified,
                        file_id, relative, method_start, declaration,
                    )
                    facts += 1
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
    def _declared_parent_types(source: bytes, node) -> list[tuple[str, str, str]]:
        """Declared parent simple names from the header clauses of one type.

        Returns ``(simple_name, edge_kind, clause_text)`` where edge_kind is
        ``INHERITS`` for extends-style clauses and ``IMPLEMENTS`` for
        implements-style clauses. Scans header children before the body,
        skipping generic argument subtrees so ``extends Comparable<T>``
        records only the declared parent. Grammar versions that rename clause
        nodes simply yield no parents — extraction stays conservative by
        construction.
        """
        parents: list[tuple[str, str, str]] = []
        body = node.child_by_field_name("body")
        for child in node.children:
            if body is not None and child.start_byte >= body.start_byte:
                break
            if child.type in _INHERITS_CLAUSE_NODE_TYPES:
                edge_kind = "INHERITS"
            elif child.type in _IMPLEMENTS_CLAUSE_NODE_TYPES:
                edge_kind = "IMPLEMENTS"
            else:
                continue
            clause_text = source[child.start_byte:child.end_byte].decode("utf-8")
            stack = [child]
            while stack:
                item = stack.pop()
                for sub in item.children:
                    if sub.type in _GENERIC_SUBTREE_NODE_TYPES:
                        continue
                    if sub.type == "type_identifier":
                        parents.append((JavaIndexer._node_text(source, sub), edge_kind, clause_text))
                    else:
                        stack.append(sub)
        return parents

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

    def resolve_edges(self, repository_id: str) -> int:
        """Resolve pending declarations into same-repository CALLS/INHERITS edges.

        Raw ``CALL`` and ``INHERITS_DECL`` facts stay untouched as the pending
        layer. An edge is emitted only when its endpoint resolves to exactly
        one same-repository symbol and, for calls, the named method exists on
        that type; everything else is omitted rather than guessed. Stale
        resolved edges are removed so repeated ingests converge.
        """
        type_simple: dict[str, set[str]] = {}
        type_qualified: set[str] = set()
        methods_by_owner: dict[str, set[str]] = {}
        for row in self.db.execute(
            """SELECT cs.kind AS kind, cs.qualified_name AS qualified_name, cs.name AS name
                 FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                WHERE cf.repository_id=?""",
            (repository_id,),
        ).fetchall():
            qualified = row["qualified_name"]
            if row["kind"] in RESOLVABLE_TYPE_KINDS:
                type_qualified.add(qualified)
                type_simple.setdefault(qualified.rsplit(".", 1)[-1], set()).add(qualified)
            elif row["kind"] in CALLABLE_KINDS:
                owner = qualified.rsplit(".", 1)[0] if "." in qualified else qualified
                methods_by_owner.setdefault(owner, set()).add(row["name"])

        expected: dict[str, tuple] = {}
        rows = self.db.execute(
            """SELECT f.id AS fact_id, f.fact_type AS fact_type, f.subject AS subject,
                      f.target AS target, f.evidence_id AS evidence_id,
                      s.id AS symbol_id, s.qualified_name AS caller
                 FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                 JOIN code_file cf ON cf.id=s.file_id
                WHERE cf.repository_id=? AND f.fact_type IN ('CALL', ?, ?)""",
            (repository_id, INHERITS_DECL_FACT, IMPLEMENTS_DECL_FACT),
        ).fetchall()
        for row in rows:
            if row["fact_type"] in (INHERITS_DECL_FACT, IMPLEMENTS_DECL_FACT):
                edge_kind = row["fact_type"].removesuffix("_DECL")
                parent = self._resolve_type(row["target"], type_simple, type_qualified)
                if not parent:
                    continue
                fact_id = stable_id("FACT", row["symbol_id"], edge_kind, row["subject"], parent, row["evidence_id"])
                expected[fact_id] = (fact_id, row["symbol_id"], edge_kind, row["subject"], parent, row["evidence_id"])
                continue
            parsed = _parse_call_target(row["target"])
            if parsed is None:
                continue
            receiver, method = parsed
            if receiver in (None, "this"):
                owner = row["caller"].rsplit(".", 1)[0] if "." in row["caller"] else row["caller"]
            else:
                owner = self._resolve_type(receiver, type_simple, type_qualified)
            if not owner or method not in methods_by_owner.get(owner, ()):
                continue
            target = f"{owner}.{method}"
            fact_id = stable_id("FACT", row["symbol_id"], "CALLS", row["caller"], target, row["evidence_id"])
            expected[fact_id] = (fact_id, row["symbol_id"], "CALLS", row["caller"], target, row["evidence_id"])

        placeholders = ", ".join("?" for _ in RESOLVED_EDGE_FACTS)
        existing = {item["id"] for item in self.db.execute(
            f"""SELECT f.id AS id FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                  JOIN code_file cf ON cf.id=s.file_id
                 WHERE cf.repository_id=? AND f.fact_type IN ({placeholders})""",
            (repository_id, *RESOLVED_EDGE_FACTS),
        ).fetchall()}
        for fact_id in existing - expected.keys():
            self.db.execute("DELETE FROM code_fact WHERE id=?", (fact_id,))
        inserted = 0
        for fact_id, values in expected.items():
            if fact_id in existing:
                continue
            self.db.execute("INSERT OR IGNORE INTO code_fact VALUES (?, ?, ?, ?, ?, ?)", values)
            inserted += 1
        return inserted

    @staticmethod
    def _resolve_type(name: str, type_simple: dict[str, set[str]], type_qualified: set[str]) -> str | None:
        """Resolve one type reference to exactly one same-repository qualified name."""
        if name in type_qualified:
            return name
        if "." not in name:
            candidates = type_simple.get(name)
            if candidates and len(candidates) == 1:
                return next(iter(candidates))
        return None

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


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _declared_parents(line: str) -> list[tuple[str, str]]:
    """Declared parent simple names from one regex-parsed declaration line.

    Returns ``(simple_name, edge_kind)`` pairs; extends maps to INHERITS and
    implements to IMPLEMENTS so the two relations stay separable.
    """
    parents: list[tuple[str, str]] = []
    extends = EXTENDS_RE.search(line)
    if extends:
        parents.append((extends.group(1), "INHERITS"))
    implements = IMPLEMENTS_RE.search(line)
    if implements:
        parents.extend((part.strip(), "IMPLEMENTS")
                       for part in implements.group(1).split(",") if part.strip())
    return parents


def _matching_paren(text: str, open_index: int) -> int | None:
    """Return the index of the paren closing the one at *open_index*."""
    depth = 0
    for index in range(open_index, len(text)):
        character = text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _parse_call_target(target: str) -> tuple[str | None, str] | None:
    """Split one raw CALL target into ``(receiver, method)``.

    A ``None`` receiver means a bare or ``this``-qualified call that resolves
    against the caller's own type. Chained calls whose receiver is a computed
    expression return ``None`` — the caller omits them instead of guessing.
    """
    target = target.strip()
    bare = re.match(r"^(\w+)\s*\(", target)
    if bare:
        open_index = target.find("(", bare.end(1))
        close = _matching_paren(target, open_index) if open_index >= 0 else None
        if close is not None and target[close + 1:].strip() == "":
            return None, bare.group(1)
        return None
    dotted = re.match(r"^([\w$]+(?:\.[\w$]+)*)\.(\w+)(?:\s*\(|$)", target)
    if dotted:
        return dotted.group(1), dotted.group(2)
    return None


def _annotation_string(arguments: str, *, keys: tuple[str, ...]) -> str:
    for key in keys:
        match = re.search(rf"\b{key}\s*=\s*(?:\{{\s*)?[\"']([^\"']+)[\"']", arguments)
        if match:
            return match.group(1)
    direct = re.search(r"(?:^|,)\s*(?:\{\s*)?[\"']([^\"']+)[\"']", arguments)
    return direct.group(1) if direct else ""


def _normalize_http_path(value: str) -> str:
    path = value.strip().split("?", 1)[0]
    return "/" + path.strip("/") if path.strip("/") else "/"

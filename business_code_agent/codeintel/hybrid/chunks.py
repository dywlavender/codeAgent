"""Function/class units, created only by an explicit indexing command."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def structural_chunks(document):
    lines = document["content"].splitlines()
    positions = []
    path = document["path"]
    suffix = Path(path).suffix.lower()
    if suffix in {".java", ".js", ".jsx", ".ts", ".tsx"}:
        from tree_sitter import Language, Parser

        if suffix == ".java":
            import tree_sitter_java

            language = tree_sitter_java.language()
        elif suffix in {".ts", ".tsx"}:
            import tree_sitter_typescript

            language = (
                tree_sitter_typescript.language_tsx()
                if suffix == ".tsx"
                else tree_sitter_typescript.language_typescript()
            )
        else:
            import tree_sitter_javascript

            language = tree_sitter_javascript.language()
        parser = Parser(Language(language))
        tree = parser.parse(document["content"].encode("utf-8"))
        functions = {
            "method_declaration",
            "constructor_declaration",
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
            "generator_function",
        }

        def visit(node, parent=""):
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode() if name_node else parent
            qualified = ".".join(x for x in (parent, name) if x)
            value = node.child_by_field_name("value")
            if node.type in functions or (
                node.type == "variable_declarator" and value and value.type in functions
            ):
                positions.append(
                    (
                        node.start_point.row + 1,
                        node.end_point.row + 1,
                        "function",
                        qualified,
                    )
                )
                return
            if node.type in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "abstract_class_declaration",
                "class",
            }:
                body = node.child_by_field_name("body")
                first_method = (
                    next(
                        (c for c in body.named_children if c.type in functions),
                        None,
                    )
                    if body
                    else None
                )
                end = (
                    first_method.start_point.row
                    if first_method
                    else node.end_point.row + 1
                )
                positions.append(
                    (
                        node.start_point.row + 1,
                        max(node.start_point.row + 1, end),
                        "class",
                        qualified,
                    )
                )
                parent = qualified
            for child in node.named_children:
                visit(child, parent)

        visit(tree.root_node)
    elif path.endswith(".py"):
        tree = ast.parse(document["content"])

        def visit_py(node, parent=""):
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    name = ".".join(x for x in (parent, child.name) if x)
                    start = min(
                        [child.lineno] + [d.lineno for d in child.decorator_list]
                    )
                    if isinstance(child, ast.ClassDef):
                        methods = [
                            m.lineno
                            for m in child.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ]
                        positions.append(
                            (
                                start,
                                min(methods) - 1 if methods else child.end_lineno,
                                "class",
                                name,
                            )
                        )
                        visit_py(child, name)
                    else:
                        positions.append((start, child.end_lineno, "function", name))
                else:
                    visit_py(child, parent)

        visit_py(tree)
    elif path.endswith(".md"):
        starts = (
            [0]
            + [i for i, line in enumerate(lines) if i and re.match(r"^#{1,6}\s", line)]
            + [len(lines)]
        )
        positions = [
            (a + 1, b, "section", lines[a].strip())
            for a, b in zip(starts, starts[1:])
            if a < b
        ]
    covered = set()
    result = []
    for start, end, kind, name in positions:
        # Attach immediately preceding comments/annotations to their declaration.
        while start > 1 and re.match(r"^\s*(//|/\*|\*|@|#)", lines[start - 2]):
            start -= 1
        covered.update(range(start, end + 1))
        result.append(_unit(document, lines, start, end, kind, name))
    # Imports, fields, configuration and unsupported languages stay available.
    # No token windows are presented as parsed functions.
    start = 1
    while start <= len(lines):
        if start in covered:
            start += 1
            continue
        end = start
        while end < len(lines) and end + 1 not in covered:
            end += 1
        if any(line.strip() for line in lines[start - 1 : end]):
            result.append(_unit(document, lines, start, end, "file_region", ""))
        start = end + 1
    return result


def _unit(document, lines, start, end, kind, name):
    content = "\n".join(lines[start - 1 : end])
    deprecated = bool(
        re.search(
            r"@Deprecated|^deprecated:\s*true",
            "\n".join(lines[max(0, start - 3) : min(end, start + 5)]),
            re.M | re.I,
        )
    )
    return {
        "id": f"{document['sourceId']}:{document['path']}:{start}-{end}",
        "sourceId": document["sourceId"],
        "kind": document["kind"],
        "path": document["path"],
        "lineStart": start,
        "lineEnd": end,
        "unitType": kind,
        "symbolName": name,
        "deprecated": deprecated,
        "content": content,
    }

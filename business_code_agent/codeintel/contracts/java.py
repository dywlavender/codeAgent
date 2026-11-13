"""Narrow Java intermediate representation, produced only by explicit indexing."""

from __future__ import annotations

import json
import re


def walk(node, *, skip=()):
    yield node
    for child in node.named_children:
        if child.type not in skip:
            yield from walk(child, skip=skip)


def text(node):
    return node.text.decode("utf-8") if node else ""


def span(node, path):
    return {"path": path, "start": node.start_point.row + 1, "end": node.end_point.row + 1}


def annotations(node):
    modifiers = next((n for n in node.named_children if n.type == "modifiers"), None)
    if not modifiers:
        return []
    return [n for n in modifiers.named_children if n.type in {"annotation", "marker_annotation"}]


def annotation_value(annotation, key="value"):
    args = annotation.child_by_field_name("arguments")
    if not args:
        return None
    for child in args.named_children:
        if child.type == "string_literal" and key == "value":
            return json.loads(text(child))
        if child.type == "element_value_pair" and text(child.child_by_field_name("key")) == key:
            value = child.child_by_field_name("value")
            if value.type == "string_literal":
                return json.loads(text(value))
    return None


def route_paths(annotation):
    args = annotation.child_by_field_name("arguments")
    if not args:
        return [""]
    value = None
    for child in args.named_children:
        if child.type == "element_value_pair":
            if text(child.child_by_field_name("key")) in {"value", "path"}:
                value = child.child_by_field_name("value")
        elif child.type in {"string_literal", "element_value_array_initializer", "identifier", "field_access"}:
            value = child
    if value is None:
        return [""]
    values = value.named_children if value.type == "element_value_array_initializer" else [value]
    if any(n.type != "string_literal" for n in values):
        return None
    return [json.loads(text(n)) for n in values]


def expression(node, path):
    if node is None:
        return {"kind": "unknown", "text": "missing expression"}
    result = {"ref": span(node, path)}
    kind = node.type
    if kind == "string_literal":
        return {**result, "kind": "literal", "value": json.loads(text(node))}
    if kind in {"identifier", "class_literal"}:
        return {**result, "kind": "name", "name": text(node)}
    if kind in {"decimal_integer_literal", "true", "false", "null_literal"}:
        try:
            return {**result, "kind": "literal", "value": json.loads(text(node).removesuffix("L").removesuffix("l"))}
        except ValueError:
            pass
    if kind == "ternary_expression":
        return {**result, "kind": "choice", "options": [expression(node.child_by_field_name(n), path) for n in ("consequence", "alternative")]}
    if kind in {"parenthesized_expression", "cast_expression"}:
        return expression(node.named_children[-1], path)
    if kind == "field_access":
        return {**result, "kind": "name", "name": text(node).removeprefix("this.")}
    if kind == "method_invocation":
        obj = node.child_by_field_name("object")
        args = node.child_by_field_name("arguments")
        return {**result, "kind": "call", "name": text(node.child_by_field_name("name")),
                "object": expression(obj, path) if obj else {"kind": "name", "name": "this"},
                "args": [expression(n, path) for n in args.named_children] if args else []}
    if kind == "object_creation_expression":
        args = node.child_by_field_name("arguments")
        return {**result, "kind": "new", "type": text(node.child_by_field_name("type")),
                "args": [expression(n, path) for n in args.named_children] if args else []}
    if kind == "lambda_expression":
        strings = [json.loads(text(n)) for n in walk(node) if n.type == "string_literal"]
        return {**result, "kind": "lambda", "strings": strings, "text": text(node)[:500]}
    if kind == "switch_expression":
        body = node.child_by_field_name("body")
        rules = []
        for rule in body.named_children:
            if rule.type != "switch_rule":
                continue
            label = rule.named_children[0]
            values = [json.loads(text(n)) for n in label.named_children if n.type == "string_literal"]
            output = rule.named_children[-1]
            value = output.named_children[0] if output.named_children else output
            rules.append({"inputs": values, "default": text(label) == "default", "output": expression(value, path), "ref": span(rule, path)})
        return {**result, "kind": "switch", "input": expression(node.child_by_field_name("condition"), path), "rules": rules}
    return {**result, "kind": "unknown", "text": text(node)[:500]}


def branch_context(node, method_body, path):
    conditions, branch_refs, phase, current = [], [], "body", node.parent
    while current and current != method_body:
        if current.type == "if_statement":
            condition = current.child_by_field_name("condition")
            yes = current.child_by_field_name("consequence")
            conditions.append({"expression": text(condition), "truth": bool(yes and yes.start_byte <= node.start_byte < yes.end_byte), "ref": span(condition, path)})
            branch = yes if yes and yes.start_byte <= node.start_byte < yes.end_byte else current.child_by_field_name("alternative")
            if branch:
                branch_refs.append({"path": path, "start": branch.start_point.row + 1, "end": branch.start_point.row + 1})
        elif current.type == "catch_clause":
            phase = "catch"
            branch_refs.append({"path": path, "start": current.start_point.row + 1, "end": current.start_point.row + 1})
        elif current.type == "try_statement" and phase == "body":
            phase = "try"
        current = current.parent
    return {"conditions": list(reversed(conditions)), "branchRefs": branch_refs, "phase": phase}


def parse_java(content, path, repository_id, application_id):
    from tree_sitter import Language, Parser
    import tree_sitter_java

    tree = Parser(Language(tree_sitter_java.language())).parse(content.encode("utf-8"))
    if tree.root_node.has_error:
        return {"types": [], "methods": [], "issues": [{"path": path, "reason": "java_parse_error"}]}
    package = next((text(n).removeprefix("package").removesuffix(";").strip() for n in tree.root_node.named_children if n.type == "package_declaration"), "")
    imports = {text(n).removeprefix("import").removesuffix(";").strip().split(".")[-1]: text(n).removeprefix("import").removesuffix(";").strip() for n in tree.root_node.named_children if n.type == "import_declaration"}
    types, methods, issues = [], [], []
    for owner in walk(tree.root_node):
        if owner.type not in {"class_declaration", "interface_declaration", "record_declaration", "enum_declaration"}:
            continue
        name = text(owner.child_by_field_name("name"))
        parents, parent = [], owner.parent
        while parent:
            if parent.type in {"class_declaration", "interface_declaration", "record_declaration"}:
                parents.insert(0, text(parent.child_by_field_name("name")))
            parent = parent.parent
        qualified = ".".join(p for p in (package, *parents, name) if p)
        type_id = f"{repository_id}:{path}::{qualified}"
        owner_annotations = annotations(owner)
        prefix, bases, controller = None, [""], False
        config_ref = None
        refs = []
        for ann in owner_annotations:
            ann_name = text(ann.child_by_field_name("name")).split(".")[-1]
            if ann_name == "ConfigurationProperties":
                prefix = annotation_value(ann, "prefix") or annotation_value(ann)
                config_ref = span(ann, path)
            elif ann_name == "RequestMapping":
                bases = route_paths(ann)
                refs.append(span(ann, path))
            elif ann_name in {"RestController", "Controller"}:
                controller = True
        fields, field_refs = {}, {}
        parameters = owner.child_by_field_name("parameters")
        components = []
        if parameters:
            for p in parameters.named_children:
                field = text(p.child_by_field_name("name"))
                fields[field] = text(p.child_by_field_name("type"))
                field_refs[field] = span(p, path)
                components.append(field)
        body = owner.child_by_field_name("body")
        if not body:
            continue
        for field in body.named_children:
            if field.type == "field_declaration":
                for declarator in field.named_children:
                    if declarator.type == "variable_declarator":
                        fields[text(declarator.child_by_field_name("name"))] = text(field.child_by_field_name("type"))
                        field_refs[text(declarator.child_by_field_name("name"))] = span(field, path)
        types.append({"id": type_id, "name": name, "qualified": qualified, "repositoryId": repository_id,
                      "applicationId": application_id, "path": path, "fields": fields, "components": components,
                      "configurationPrefix": prefix, "configurationRef": config_ref, "fieldRefs": field_refs,
                      "plainSerialization": not any(text(a.child_by_field_name("name")).split(".")[-1].startswith("Json") for a in walk(owner) if a.type in {"annotation", "marker_annotation"}),
                      "comment": text(owner.prev_named_sibling)[:800] if owner.prev_named_sibling and owner.prev_named_sibling.type == "block_comment" else "",
                      "imports": imports, "ref": span(owner, path)})
        for method in body.named_children:
            if method.type not in {"method_declaration", "constructor_declaration"}:
                continue
            method_name = text(method.child_by_field_name("name"))
            parameters = method.child_by_field_name("parameters")
            params = {text(p.child_by_field_name("name")): text(p.child_by_field_name("type")) for p in parameters.named_children if p.type == "formal_parameter"} if parameters else {}
            method_id = f"{type_id}.{method_name}/{len(params)}@{method.start_point.row + 1}"
            body_node = method.child_by_field_name("body")
            nodes = list(walk(body_node, skip={"lambda_expression", "class_declaration", "anonymous_class_body"})) if body_node else []
            locals_, local_types, assignments, returns, mutated, writes = {}, {}, {}, [], set(), []
            for node in nodes:
                if node.type == "method_invocation" and re.fullmatch(r"set[A-Z]\w*", text(node.child_by_field_name("name"))):
                    writes.append({"call": expression(node, path), **branch_context(node, body_node, path)})
                if node.type == "variable_declarator":
                    local = text(node.child_by_field_name("name"))
                    locals_[local] = expression(node.child_by_field_name("value"), path)
                    local_types[local] = text(node.parent.child_by_field_name("type"))
                elif node.type == "assignment_expression":
                    left = text(node.child_by_field_name("left")).removeprefix("this.")
                    assignments[left] = expression(node.child_by_field_name("right"), path)
                    mutated.add(left)
                elif node.type == "method_invocation" and re.fullmatch(r"put|putAll|remove\w*|clear|replace\w*|compute\w*|merge|add\w*|set\w*|sort", text(node.child_by_field_name("name"))):
                    receiver = node.child_by_field_name("object")
                    while receiver and receiver.type == "method_invocation":
                        receiver = receiver.child_by_field_name("object")
                    if receiver and receiver.type == "identifier":
                        mutated.add(text(receiver))
                elif node.type == "return_statement" and node.named_children:
                    returns.append(expression(node.named_children[0], path))
            routes, scheduled = [], False
            for ann in annotations(method):
                ann_name = text(ann.child_by_field_name("name")).split(".")[-1]
                if ann_name == "Scheduled":
                    scheduled = True
                match = re.fullmatch(r"(Get|Post|Put|Delete|Patch|Request)Mapping", ann_name)
                if match and controller:
                    verb = match[1].upper()
                    if verb == "REQUEST":
                        verb_match = re.search(r"RequestMethod\.([A-Z]+)", text(ann))
                        verb = verb_match[1] if verb_match else "ANY"
                    paths = route_paths(ann)
                    if bases is None or paths is None:
                        issues.append({"path": path, "reason": "dynamic_route_not_supported", "ref": span(ann, path)})
                        continue
                    for base in bases:
                        for route in paths:
                            routes.append({"method": verb, "path": "/" + "/".join(s.strip("/") for s in (base, route) if s.strip("/")), "refs": [*refs, span(ann, path)]})
            # Do not reuse a pre-mutation object shape through a local alias.
            for _ in locals_:
                for local, value in locals_.items():
                    if value.get("kind") == "name" and (local in mutated or value["name"] in mutated):
                        mutated.update((local, value["name"]))
            methods.append({"id": method_id, "typeId": type_id, "name": method_name, "params": params,
                            "returnType": text(method.child_by_field_name("type")), "locals": locals_, "localTypes": local_types, "assignments": assignments,
                            "mutatedLocals": sorted(mutated & locals_.keys()),
                            "writes": writes,
                            "returns": returns, "routes": routes, "scheduled": scheduled, "ref": span(method, path),
                            "comment": text(method.prev_named_sibling)[:600] if method.prev_named_sibling and method.prev_named_sibling.type in {"block_comment", "line_comment"} else "",
                            "declaration": content.splitlines()[method.start_point.row]})
    return {"types": types, "methods": methods, "issues": issues}

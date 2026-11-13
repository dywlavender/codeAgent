"""Resolve a bounded return contract from a prepared Java representation."""

from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import urlsplit


def unique_refs(values):
    return list({(v["path"], v["start"], v["end"]): v for v in values if v}.values())


def shape(fields=None, gaps=None, edges=None, mappings=None):
    return {"fields": fields or {}, "gaps": gaps or [], "edges": edges or [], "mappings": mappings or []}


def combine(values):
    result = shape()
    for value in values:
        result["fields"].update(value["fields"])
        for key in ("gaps", "edges", "mappings"):
            result[key].extend(value[key])
    return result


class Resolver:
    def __init__(self, units, applications):
        self.types = {t["id"]: t for unit in units for t in unit.get("types", [])}
        self.methods = {m["id"]: m for unit in units for m in unit.get("methods", [])}
        self.apps = {a["id"]: a for a in applications}
        self.endpoints = [(m, route) for m in self.methods.values() for route in m["routes"]]
        self.cache = {}

    def type_named(self, name, owner):
        raw = name.split("<", 1)[0].strip()
        qualified = owner["imports"].get(raw, raw)
        candidates = [t for t in self.types.values() if t["qualified"] == qualified or (qualified == raw and "." not in raw and t["name"] == raw)]
        same_app = [t for t in candidates if t["applicationId"] == owner["applicationId"]]
        local = [t for t in candidates if t["repositoryId"] == owner["repositoryId"]]
        candidates = same_app or local or candidates
        return candidates[0] if len(candidates) == 1 else None

    def call_target(self, expr, method):
        obj = expr.get("object", {})
        if obj.get("kind") != "name":
            return None
        owner = self.types[method["typeId"]]
        receiver = obj["name"]
        if receiver == "this":
            target_type = owner
        else:
            name = owner["fields"].get(receiver) or method["params"].get(receiver) or method.get("localTypes", {}).get(receiver)
            target_type = self.type_named(name, owner) if name else None
        if target_type is None:
            return None
        matches = [m for m in self.methods.values() if m["typeId"] == target_type["id"] and m["name"] == expr["name"] and len(m["params"]) == len(expr.get("args", []))]
        return matches[0] if len(matches) == 1 else None

    def gap(self, expr, method, reason):
        ref = expr.get("ref") or method["ref"]
        return shape(gaps=[{"reason": reason, "ref": ref, "methodId": method["id"],
                            "nextRead": {"path": ref["path"], "offset": max(1, ref["start"] - 3), "limit": min(100, ref["end"] - ref["start"] + 12)}}])

    def leaf(self, expr, value=None):
        return shape({"": {"refs": [expr["ref"]] if expr.get("ref") else [], "value": value, "origin": None, "conditional": False}})

    def method_shape(self, method_id, trail=()):
        if method_id in self.cache:
            return deepcopy(self.cache[method_id])
        method = self.methods[method_id]
        if method_id in trail or len(trail) >= 10:
            return self.gap({}, method, "recursive_or_depth_limit")
        if not method["returns"]:
            return self.gap({}, method, "return_contract_not_supported")
        branches = [self.value(e, method, (*trail, method_id)) for e in method["returns"]]
        result = combine(branches)
        for path, field in result["fields"].items():
            field["conditional"] = field.get("conditional", False) or not all(path in b["fields"] for b in branches)
        for field in result["fields"].values():
            field["refs"] = unique_refs(field["refs"])
        self.cache[method_id] = deepcopy(result)
        return result

    def value(self, expr, method, trail, variables=()):
        kind = expr["kind"]
        if kind == "literal":
            return self.leaf(expr, expr["value"])
        if kind == "choice":
            values = [self.value(v, method, trail, variables) for v in expr["options"]]
            result = combine(values)
            for key, field in result["fields"].items():
                field["conditional"] = not all(key in v["fields"] for v in values)
                variants = [v["fields"].get(key, {}).get("value") for v in values]
                field["value"] = variants[0] if all(v == variants[0] for v in variants) else None
            return result
        if kind == "name":
            name = expr["name"]
            if name in method.get("mutatedLocals", []):
                return self.gap(expr, method, "local_object_modified")
            if name in method["locals"] and name not in variables:
                result = self.value(method["locals"][name], method, trail, (*variables, name))
                for f in result["fields"].values():
                    f["refs"] = unique_refs([*f["refs"], expr.get("ref")])
                return result
            result = self.leaf(expr)
            owner = self.types[method["typeId"]]
            if name in owner["fields"] and name not in method["params"]:
                result["fields"][""]["binding"] = {"typeId": owner["id"], "member": name}
            return result
        if kind == "new":
            owner = self.types[method["typeId"]]
            dto = self.type_named(expr["type"], owner)
            if dto and dto.get("plainSerialization", True) and dto["components"] and len(dto["components"]) == len(expr["args"]):
                result = self.object_fields(list(zip(dto["components"], expr["args"])), expr, method, trail, variables)
                for path, fact in result["fields"].items():
                    component = path.split(".")[0].split("[")[0]
                    fact["refs"] = unique_refs([*fact["refs"], dto["fieldRefs"].get(component)])
                return result
            return self.gap(expr, method, "dto_serialization_not_supported")
        if kind == "switch":
            result = self.leaf(expr)
            result["mappings"] = [{"ref": expr["ref"], "rules": expr["rules"], "methodId": method["id"], "inputName": expr["input"].get("name")}]
            return result
        if kind != "call":
            return self.gap(expr, method, "expression_not_supported")
        receiver = expr["object"]
        name = receiver.get("name") if receiver.get("kind") == "name" else ""
        if name in {"Map", "java.util.Map"} and expr["name"] == "of":
            args = expr["args"]
            if len(args) % 2 or any(a.get("kind") != "literal" or not isinstance(a.get("value"), str) for a in args[::2]):
                return self.gap(expr, method, "map_keys_not_literal")
            return self.object_fields([(args[i]["value"], args[i + 1]) for i in range(0, len(args), 2)], expr, method, trail, variables)
        if name in {"List", "Set", "java.util.List"} and expr["name"] == "of":
            values = [self.value(a, method, trail, variables) for a in expr["args"]]
            result = combine(values)
            result["fields"] = {"[]" + ("." + k if k else ""): {**v, "conditional": v["conditional"] or not all(k in item["fields"] for item in values)} for k, v in result["fields"].items()}
            return result
        if name == "String" and expr["name"] == "valueOf" and len(expr["args"]) == 1:
            return self.value(expr["args"][0], method, trail, variables)
        if expr["name"] == "name" and not expr["args"]:
            result = self.value(receiver, method, trail, variables)
            for fact in result["fields"].values():
                fact["refs"] = unique_refs([*fact["refs"], expr["ref"]])
            return result
        if expr["name"] == "get" and len(expr["args"]) == 1 and isinstance(expr["args"][0].get("value"), str):
            obj = self.value(receiver, method, trail, variables)
            key = expr["args"][0]["value"]
            obj["fields"] = {p[len(key):].removeprefix("."): f for p, f in obj["fields"].items() if p == key or p.startswith(key + ".") or p.startswith(key + "[]")}
            return obj
        remote = self.http_target(expr, method)
        if remote is not None:
            if remote.get("gap"):
                return self.gap(expr, method, remote["gap"])
            target, refs = remote["method"], remote["refs"]
            result = self.method_shape(target["id"], trail)
            for field in result["fields"].values():
                field["refs"] = unique_refs([*field["refs"], *refs])
            result["edges"].append({"kind": "HTTP_RESPONSE", "source": method["id"], "target": target["id"], "refs": refs})
            return result
        target = self.call_target(expr, method)
        if target:
            result = self.method_shape(target["id"], trail)
            caller_owner = self.types[method["typeId"]]
            receiver_ref = caller_owner.get("fieldRefs", {}).get(name)
            connection_refs = unique_refs([expr["ref"], receiver_ref])
            if result["mappings"]:
                for mapping in result["mappings"]:
                    if mapping["methodId"] == target["id"]:
                        params = list(target["params"])
                        position = params.index(mapping["inputName"]) if mapping.get("inputName") in params else -1
                        arg = self.value(expr["args"][position], method, trail, variables) if 0 <= position < len(expr["args"]) else shape()
                        origins = [f["origin"] for f in arg["fields"].values() if f.get("origin")]
                        mapping["inputOrigins"] = origins
                        mapping["refs"] = unique_refs([mapping["ref"], expr["ref"], *[r for f in arg["fields"].values() for r in f["refs"]]])
            for f in result["fields"].values():
                f["refs"] = unique_refs([*f["refs"], *connection_refs])
            result["edges"].append({"kind": "RETURN_CALL", "source": method["id"], "target": target["id"], "refs": connection_refs})
            return result
        if expr["name"] in {"name", "toString"}:
            return self.leaf(expr)
        return self.gap(expr, method, "call_target_unresolved")

    def object_fields(self, pairs, expr, method, trail, variables):
        result = shape()
        for key, value in pairs:
            child = self.value(value, method, trail, variables)
            # A literal Map key is returned even if its value's nested shape is unknown.
            result["fields"][key] = {"refs": [expr["ref"]], "origin": {"methodId": method["id"], "field": key}, "value": None, "conditional": False}
            for path, fact in child["fields"].items():
                joined = key + (path if path.startswith("[]") else "." + path if path else "")
                origin = fact.get("origin")
                if not origin or origin["methodId"] == method["id"]:
                    origin = {"methodId": method["id"], "field": joined}
                refs = [expr["ref"]] if not path else unique_refs([*fact["refs"], expr["ref"]])
                result["fields"][joined] = {**fact, "refs": refs, "valueRefs": unique_refs([*fact["refs"], expr["ref"]]),
                                            "origin": origin}
            for mapping in child["mappings"]:
                mapping["outputField"] = key + ("." + mapping["outputField"] if mapping.get("outputField") else "")
            for category in ("gaps", "edges", "mappings"):
                if category == "gaps":
                    for gap in child[category]:
                        relative = gap.get("affectsField", "")
                        gap["affectsField"] = key + ("." + relative if relative else "")
                result[category].extend(child[category])
        return result

    def http_target(self, expr, method):
        chain, current = [], expr
        while current.get("kind") == "call":
            chain.append(current)
            current = current["object"]
        if not any(c["name"] == "retrieve" for c in chain) or not any(c["name"] == "uri" for c in chain):
            return None
        if expr["name"] != "body" or len(expr["args"]) != 1 or expr["args"][0].get("name") not in {"Map.class", "java.util.Map.class"}:
            return {"gap": "http_response_conversion_not_supported"}
        uri = next(c for c in chain if c["name"] == "uri")
        verb = next((c["name"].upper() for c in chain if c["name"] in {"get", "post", "put", "delete", "patch"}), None)
        if not verb or not uri["args"]:
            return {"gap": "http_uri_not_supported"}
        path_arg = uri["args"][0]
        route = path_arg.get("value") if path_arg.get("kind") == "literal" else None
        if path_arg.get("kind") == "lambda":
            paths = [s for s in path_arg["strings"] if s.startswith("/")]
            route = paths[0] if len(paths) == 1 else None
        if not isinstance(route, str):
            return {"gap": "dynamic_http_uri"}
        owner = self.types[method["typeId"]]
        receiver = current.get("name", "")
        client_type = owner["fields"].get(receiver, "")
        if owner["imports"].get(client_type, client_type) != "org.springframework.web.client.RestClient":
            return {"gap": "http_client_type_not_supported"}
        constructors = [m for m in self.methods.values() if m["typeId"] == owner["id"] and receiver in m["assignments"]]
        bases = []
        for constructor in constructors:
            value = constructor["assignments"][receiver]
            while value.get("kind") == "call":
                if value["name"] == "baseUrl" and value["args"]:
                    bases.append(value)
                value = value["object"]
        if len(bases) != 1:
            return {"gap": "http_base_url_unresolved"}
        base_call, refs = bases[0], [expr["ref"], uri["ref"], bases[0]["ref"]]
        base_expr = base_call["args"][0]
        base_url = base_expr.get("value") if base_expr.get("kind") == "literal" else None
        if base_expr.get("kind") == "call" and base_expr["object"].get("kind") == "name":
            property_receiver = base_expr["object"]["name"]
            property_type = self.type_named(owner["fields"].get(property_receiver, ""), owner)
            if property_type and property_type["configurationPrefix"]:
                key = property_type["configurationPrefix"] + "." + re.sub(r"([a-z])([A-Z])", r"\1-\2", base_expr["name"]).lower()
                config = self.apps.get(owner["applicationId"], {}).get("config", {}).get(key)
                if config:
                    base_url = config["value"]
                    refs += [config["ref"], property_type.get("configurationRef"), owner["fieldRefs"].get(property_receiver)]
        if not isinstance(base_url, str) or "${" in base_url:
            return {"gap": "http_configuration_unresolved"}
        url = urlsplit(base_url)
        matches = []
        for target, endpoint in self.endpoints:
            target_owner = self.types[target["typeId"]]
            app = self.apps.get(target_owner["applicationId"], {})
            config = app.get("config", {})
            port = config.get("server.port", {}).get("value")
            app_name = config.get("spring.application.name", {}).get("value", app.get("id"))
            service_matches = url.hostname == app_name or (url.hostname in {"localhost", "127.0.0.1", "::1"} and str(url.port or 80) == str(port))
            full_path = "/" + "/".join(s.strip("/") for s in (url.path, route) if s.strip("/"))
            context = config.get("server.servlet.context-path", {}).get("value", "")
            template = re.sub(r"\{[^{}]+\}", "{}", "/" + "/".join(s.strip("/") for s in (context, endpoint["path"]) if s.strip("/")))
            candidate = full_path.split("?", 1)[0]
            expected, actual = template.strip("/").split("/"), candidate.strip("/").split("/")
            route_matches = len(expected) == len(actual) and all(a == b or a == "{}" for a, b in zip(expected, actual))
            if service_matches and endpoint["method"] in {verb, "ANY"} and route_matches:
                matches.append((target, unique_refs([*refs, *endpoint["refs"], config.get("server.port", {}).get("ref"), config.get("spring.application.name", {}).get("ref")])))
        if len(matches) != 1:
            return {"gap": "http_target_ambiguous" if matches else "http_target_not_indexed_or_matched"}
        target, refs = matches[0]
        return {"method": target, "refs": refs}

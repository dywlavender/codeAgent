"""Bind source setter sites to response members; no inferred business meanings."""

from .resolve import unique_refs


def response_rules(resolver, response):
    bindings = {}
    for path, fact in response["fields"].items():
        binding = fact.get("binding")
        if path and binding:
            bindings.setdefault((binding["typeId"], binding["member"]), []).append(path)
    result = []
    for method in resolver.methods.values():
        for write in method.get("writes", []):
            call = write["call"]
            target = resolver.call_target(call, method)
            if not target or len(call["args"]) != 1 or len(target["params"]) != 1:
                continue
            parameter = next(iter(target["params"]))
            members = [member for member, value in target["assignments"].items()
                       if value.get("kind") == "name" and value.get("name") == parameter
                       and (target["typeId"], member) in bindings]
            argument = call["args"][0]
            if argument["kind"] == "literal":
                value = {"kind": "literal", "expression": str(argument["value"]), "value": argument["value"]}
            elif argument["kind"] == "name" and "." in argument["name"]:
                value = {"kind": "symbol", "expression": argument["name"]}
            else:
                continue
            for member in members:
                paths = bindings[(target["typeId"], member)]
                context = {"path": call["ref"]["path"], "start": max(method["ref"]["start"], call["ref"]["start"] - 6), "end": min(method["ref"]["end"], call["ref"]["end"] + 6)}
                if method["ref"]["end"] - method["ref"]["start"] < 60:
                    context = method["ref"]
                declaration = {**method["ref"], "end": min(method["ref"]["end"], method["ref"]["start"] + 2)}
                refs = unique_refs([context, declaration, target["ref"], call["ref"], *write.get("branchRefs", []),
                                    *[c["ref"] for c in write["conditions"]]])
                dependencies = unique_refs([ref for path in paths for ref in [*response["fields"][path]["refs"], *response["fields"][path].get("valueRefs", [])]])
                result.append({"id": "B" + str(len(result) + 1), "fields": paths,
                               "methodId": method["id"], "phase": write["phase"],
                               "conditions": write["conditions"], "assigned": value,
                               "binding": {"typeId": target["typeId"], "member": member},
                               "ref": call["ref"], "refs": refs,
                               "dependencies": dependencies,
                               "conditionsComplete": False, "executionProven": False})
    return result

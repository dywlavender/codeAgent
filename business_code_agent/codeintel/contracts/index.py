"""Project-scoped SQLite snapshot. Parsing happens only in prepare_index."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import re

from business_code_agent.codeintel.changes import diff_ranges
from business_code_agent.codeintel.retrieval import collect_documents
from business_code_agent.storage.connections import open_sqlite
from .java import parse_java
from .resolve import Resolver
from .rules import response_rules

SCHEMA_VERSION = 2


def read_index(root, project_id, *, allow_previous=False):
    path = Path(root).resolve() / "contracts.sqlite"
    if not path.is_file():
        raise ValueError("契约索引尚未显式准备，请先执行 prepare_contract_index.py")
    with closing(open_sqlite(path, readonly=True)) as db:
        metadata = json.loads(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        if metadata["projectId"] != project_id:
            raise ValueError("契约索引不属于当前工程")
        if metadata["schemaVersion"] != SCHEMA_VERSION and not allow_previous:
            raise ValueError("契约索引结构版本已变化，请显式执行 prepare_contract_index.py --reparse")
        files = {row[0]: json.loads(row[1]) for row in db.execute("SELECT path,data FROM source_file")}
        entries = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,data FROM contract_entry")}
    return {**metadata, "files": files, "entries": entries}


def configuration(content, path):
    result, parents = {}, []
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if path.endswith(".properties"):
            match = re.match(r"\s*([^=\s]+)\s*=\s*(.*?)\s*$", line)
            if not match:
                continue
            key, value = match.groups()
        else:
            match = re.match(r"^( *)([\w.-]+):(?:\s+(.*))?$", line)
            if not match:
                continue
            indent, key, value = len(match[1]), match[2], match[3]
            while parents and parents[-1][0] >= indent:
                parents.pop()
            if value is None:
                parents.append((indent, key))
                continue
            key = ".".join([*(p[1] for p in parents), key])
        # Only service identity and base addresses enter graph metadata.
        if key not in {"server.port", "spring.application.name", "server.servlet.context-path"} and not key.endswith((".base-url", ".baseUrl")):
            continue
        value = value.split(" #", 1)[0].strip().strip('"\'')
        result[key] = {"value": value, "ref": {"path": path, "start": number, "end": number}}
    return result


def application_for(config, repository_id, path):
    matches = []
    for app in config.get("applications", []):
        prefix = str(app.get("sourceRoot") or ".").strip("./")
        if app["repositoryId"] == repository_id and (not prefix or path.startswith(prefix + "/")):
            matches.append((len(prefix), app))
    return max(matches, key=lambda p: p[0])[1] if matches else None


def prepare_index(root, sources, config, project_id, repository_ids=None, *, reparse=False):
    root = Path(root).resolve()
    old = read_index(root, project_id, allow_previous=reparse) if (root / "contracts.sqlite").exists() else None
    selected = set(repository_ids) if repository_ids is not None else {s["id"] for s in sources if s["kind"] == "source"}
    selected_sources = [s for s in sources if s["kind"] == "source" and s["id"] in selected]
    if selected != {s["id"] for s in selected_sources}:
        raise ValueError("指定仓库不属于当前工程")
    previous = {"files": {}, "sourceObservations": {}}
    if old:
        for item in old["files"].values():
            key = item["repositoryId"] + "/" + item["relative"]
            previous["files"][key] = item["content"]
            previous["sourceObservations"][key] = item["stamp"]
    observations = {}
    documents, skipped = collect_documents(selected_sources, previous=previous, observations=observations)
    files, changes, parsed, reused = {}, [], 0, 0
    settings = {"applications": config.get("applications", []), "sources": selected_sources}
    configuration_changed = reparse or not old or old.get("configuration") != settings
    for document in documents:
        relative, repository = document["path"], document["sourceId"]
        if not relative.endswith(".java") and Path(relative).name not in {"application.yml", "application.yaml", "application.properties"}:
            continue
        path = "repos/" + repository + "/" + relative
        old_file = old["files"].get(path) if old else None
        unchanged = old_file and old_file["content"] == document["content"]
        app = application_for(config, repository, relative)
        item = {"path": path, "repositoryId": repository, "relative": relative,
                "content": document["content"], "stamp": observations[repository + "/" + relative],
                "applicationId": app["id"] if app else None}
        if relative.endswith(".java"):
            if unchanged and not configuration_changed:
                item["unit"] = old_file["unit"]
                reused += 1
            else:
                item["unit"] = parse_java(item["content"], path, repository, item["applicationId"])
                parsed += 1
        else:
            item["unit"] = {}
        files[path] = item
        if not unchanged:
            changes.append({"path": path, "kind": "modified" if old_file else "added",
                            "ranges": diff_ranges(old_file["content"], item["content"]) if old_file else []})
    for path in (old["files"].keys() - files.keys()) if old else []:
        changes.append({"path": path, "kind": "deleted", "ranges": []})
    if old and not changes and not configuration_changed:
        return {**old, "update": {"parsedJavaFiles": 0, "reusedJavaFiles": reused, "changes": []}}
    applications = [dict(a, config={}) for a in config.get("applications", [])]
    for app in applications:
        prefix = str(app.get("sourceRoot") or ".").strip("./")
        allowed = {((prefix + "/") if prefix else "") + p for p in (
            "src/main/resources/application.yml", "src/main/resources/application.yaml", "src/main/resources/application.properties",
            "application.yml", "application.yaml", "application.properties")}
        for item in files.values():
            if item["applicationId"] == app["id"] and item["relative"] in allowed:
                app["config"].update(configuration(item["content"], item["path"]))
    resolver = Resolver([item["unit"] for item in files.values()], applications)
    entries = {}
    for method, route in sorted(resolver.endpoints, key=lambda p: (p[0]["id"], p[1]["path"])):
        entry_id = "C" + str(len(entries) + 1)
        owner = resolver.types[method["typeId"]]
        app = next((a for a in applications if a["id"] == owner["applicationId"]), {})
        result = resolver.method_shape(method["id"])
        related = {method["id"], *(e["source"] for e in result["edges"]), *(e["target"] for e in result["edges"])}
        descriptions = [method["comment"], owner.get("comment", "")]
        for method_id in related:
            related_method = resolver.methods[method_id]
            descriptions += [related_method["comment"], resolver.types[related_method["typeId"]].get("comment", "")]
        for fact in result["fields"].values():
            fact["refs"] += route["refs"]
        entries[entry_id] = {
            "id": entry_id, "methodId": method["id"], "applicationId": owner["applicationId"],
            "applicationName": app.get("name", owner["repositoryId"]), "repositoryId": owner["repositoryId"],
            "symbol": owner["qualified"] + "." + method["name"], "route": route,
            "ref": method["ref"], "comment": method["comment"], "response": result,
            "businessRules": response_rules(resolver, result),
            "searchText": "\n".join(dict.fromkeys(descriptions)),
        }
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    metadata = {"schemaVersion": SCHEMA_VERSION, "projectId": project_id, "version": version,
                "configuration": settings, "coveredRepositories": sorted(selected),
                "applications": applications, "skipped": skipped,
                "issues": [issue for f in files.values() for issue in f["unit"].get("issues", [])],
                "update": {"parsedJavaFiles": parsed, "reusedJavaFiles": reused, "changes": changes}}
    root.mkdir(parents=True, exist_ok=True)
    with closing(open_sqlite(root / "contracts.sqlite")) as db:
        db.executescript("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS source_file(path TEXT PRIMARY KEY,data TEXT); CREATE TABLE IF NOT EXISTS contract_entry(id TEXT PRIMARY KEY,data TEXT);")
        with db:
            for table in ("source_file", "contract_entry", "metadata"):
                db.execute("DELETE FROM " + table)
            db.execute("INSERT INTO metadata VALUES ('snapshot',?)", (json.dumps(metadata, ensure_ascii=False),))
            db.executemany("INSERT INTO source_file VALUES (?,?)", [(p, json.dumps(v, ensure_ascii=False)) for p, v in files.items()])
            db.executemany("INSERT INTO contract_entry VALUES (?,?)", [(key, json.dumps(v, ensure_ascii=False)) for key, v in entries.items()])
    return {**metadata, "files": files, "entries": entries}

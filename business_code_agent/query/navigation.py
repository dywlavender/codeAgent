"""Short project/application navigation from configuration, never code proof."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

NAVIGATION_FILE = ".codeagent-project-navigation.json"


def build_navigation(config, mounts, project_id, project_name):
    systems = {s["id"]: s.get("name", s["id"]) for s in config.get("systems", [])}
    apps = []
    for app in config.get("applications", []):
        repository = app.get("repositoryId")
        if repository not in mounts:
            continue
        relative = PurePosixPath(app.get("sourceRoot") or ".")
        if relative.is_absolute() or ".." in relative.parts:
            continue
        apps.append({
            "id": app["id"], "name": app.get("name", app["id"]),
            "systemId": app.get("systemId", ""),
            "systemName": systems.get(app.get("systemId"), app.get("systemId", "")),
            "repositoryId": repository, "mount": str(PurePosixPath(mounts[repository]) / relative),
            "type": app.get("type", "UNKNOWN").upper(),
        })
    return {
        "projectId": project_id, "projectName": project_name,
        "repositories": [{"id": key, "mount": value} for key, value in mounts.items()],
        "applications": apps,
    }


def read_navigation(workspace, roots, project_id=None):
    path = Path(workspace) / NAVIGATION_FILE
    if not path.exists():
        return {"projectId": project_id, "repositories": [], "applications": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if project_id and value["projectId"] != project_id:
        raise ValueError("工程职责导航不属于当前工程")
    allowed = {mount for mount, _ in roots}
    repositories = [r for r in value["repositories"] if r["mount"] in allowed]
    ids = {r["id"] for r in repositories}
    return {
        **value, "repositories": repositories,
        "applications": [a for a in value["applications"] if a["repositoryId"] in ids],
    }


def roles_for(navigation, path):
    matched = [a for a in navigation["applications"] if path == a["mount"] or path.startswith(a["mount"] + "/")]
    if not matched:
        return set()
    depth = max(len(a["mount"]) for a in matched)
    return {a["type"] for a in matched if len(a["mount"]) == depth}


def render_navigation(navigation):
    lines = ["工程职责导航（来自配置，只用于选范围，不证明调用或页面已实现）："]
    for app in navigation["applications"]:
        lines.append(f"- {app['mount']}/：{app['name']}；系统={app['systemName']}；类型={app['type']}")
    configured = {a["repositoryId"] for a in navigation["applications"]}
    for repo in navigation["repositories"]:
        if repo["id"] not in configured:
            lines.append(f"- {repo['mount']}/：未登记应用职责")
    systems = {a["systemId"]: a["systemName"] for a in navigation["applications"] if a["systemId"]}
    for system_id, name in systems.items():
        if not any(a["systemId"] == system_id and a["type"] == "FRONTEND" for a in navigation["applications"]):
            lines.append(f"- {name}：本轮范围未登记前端应用；不能用其他系统的前端代替。")
    if not navigation["repositories"]:
        lines.append("- 没有可用的应用职责配置；不能根据仓库名称断言其职责。")
    return "\n".join(lines)

"""Scoped navigation and evidence handoff for questions about required inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from business_code_agent.shared.entry_anchors import extract_entry_anchors
from .investigation import Task


BUSINESS_MOUNTS = {"knowledge/business-context", "knowledge/baseline"}
ENTRY = "material-entry"
SUBMISSION = "material-submission"
REQUEST = "material-request"
VALIDATION = "material-validation"
CONNECTION = "material-connection"
REQUIREMENT = "material-requirement"
MATERIAL_ROLES = {
    SUBMISSION: ("核对对应页面的实际提交参数", "consumer"),
    REQUEST: ("核对所选入口的请求绑定和请求体", "read"),
    VALIDATION: ("核对所选入口实际执行的校验条件", "read"),
    CONNECTION: ("核对客户端声明、对应服务及目标端原文", "relation"),
    REQUIREMENT: ("核对相关需求或验收原文", "read"),
}

MATERIAL_RULES = (
    "围绕已定位的业务动作回答材料是否必需。核对请求绑定和实际校验条件，涉及页面提交时核对实际提交内容。"
    "涉及跨系统时引用实际调用与目标端。缺少关键原文时说明缺口，将结论限定在已核对的环节。"
    "区分 ID 非空检查、实际业务状态校验和需求原文中的要求。检索未命中只说明本轮查找结果。"
)

MATERIAL_GUIDANCE = (
    "\n材料是否必需的问题，优先沿检索事项中已定位的业务入口，使用已交付原文和具体引用补查。"
    "入口仍不明确时，可用本轮可用的代码地图、AST 或索引定位。"
    "用一次 Investigation 可同时记录 material-entry 和相关材料待办。"
    "material-entry 引用已读的本题入口，symbol 填原文中的方法名。涉及跨系统时分别引用入口和目标端。"
    "后续追加目标端时保留已确认的入口引用。"
    "material-request、material-validation 等源码待办的 refs 同时包含对应入口引用和实际支持该项的原文。"
    "按程序列出的材料待办填 status 和 refs。有具体待读位置时填 next_read，资料缺失时填写原因。"
)

_QUESTION = re.compile(
    r"(?P<operation>[^，。？！?!]{1,64}?)(?P<need>是否(?:需要|必须|要|必传)?|需不需要|要不要|需要|必须|必传)"
    r"\s*(?:上传|提交|提供|附上)?\s*(?P<material>[^，。？！?!]{1,48})"
)
_MATERIAL = re.compile(r"(?:材料|附件|影像|照片|证件|证明|资料|文件|文档|扫描件|复印件)$")
_CHANGE = re.compile(r"^(?:怎么|如何|修改|新增|删除|优化|调整|更换)")
_ENTRY_SECTION = re.compile(
    r"^#{1,6}[ \t]+(?:调查入口|entry anchors?)[ \t]*\n(.*?)(?=^#{1,6}[ \t]+|\Z)",
    re.I | re.M | re.S,
)
_POST_HANDLER = re.compile(
    r"@(Post|Put|Patch)Mapping\s*\(\s*(?:(?:path|value)\s*=\s*)?"
    r'["\'](/[^"\']*)["\']\s*\)\s*'
    r"(?:public\s+)?[\w$.<>?, \t]+\s+(\w+)\s*\(([^)]*)\)\s*[{;]"
)
_REQUEST_TYPE = re.compile(r"@RequestBody\s+(?:@\w+\s+)*([A-Z][\w$]*)\s+\w+")


@dataclass(frozen=True)
class MaterialQuestion:
    operation: str
    material: str
    requirement: bool


@dataclass(frozen=True, order=True)
class _FileTarget:
    mount: str
    name: str
    role: str
    method: str = ""
    entry: tuple[str, str] = ("", "")


def material_question(question):
    for clause in re.split(r"[，,。；;]", question.strip()):
        phrase = clause.strip(" \t？?!！")
        match = _QUESTION.fullmatch(phrase)
        if not match:
            continue
        operation = match["operation"].removeprefix("请问").strip()
        material = match["material"].removesuffix("吗").removesuffix("么").strip()
        is_question = clause.strip().endswith(("？", "?", "吗", "么")) or match["need"] in {
            "是否", "是否需要", "是否必须", "是否要", "是否必传", "需不需要", "要不要",
        }
        if operation and is_question and _MATERIAL.search(material) and not _CHANGE.match(material):
            return MaterialQuestion(operation, material, bool(re.search(r"需求|验收|产品规定", question)))
    return None


def _text(data):
    return re.sub(r"^\d+:[ \t]?", "", data.get("content", ""), flags=re.M)


def _ref(data):
    return {"path": data["path"], "start": data["lineStart"], "end": data["lineEnd"]}


class MaterialInvestigation:
    def __init__(self, question, files, navigation):
        self.question = question
        self.roots = {mount for mount, _ in files.roots}
        self.apps = navigation["applications"]
        self.lookups = {}
        self.located = {}
        self.entry_files = {}
        self.followup_files = {}
        self.checked_targets = set()
        self.source_reads = {}
        self.handler_symbols = {}
        self.role_files = {}
        self.terms = set()
        self.documents = {}
        self.selection = ()

    def seed(self, coordinator, *, locate_from_docs):
        self._plan(coordinator, REQUEST)
        self._plan(coordinator, VALIDATION)
        if coordinator.needs["consumer"]:
            self._plan(coordinator, SUBMISSION)
        if self.question.requirement:
            task = self._plan(coordinator, REQUIREMENT)
            if "requirements" not in self.roots:
                task.update(status="unavailable", note="本轮缺少需求原文目录")
            else:
                self._search_docs(coordinator, "requirements", REQUIREMENT)
        if locate_from_docs:
            for mount in sorted(self.roots & BUSINESS_MOUNTS):
                self._search_docs(coordinator, mount, "entry")

    def _search_docs(self, coordinator, mount, task):
        coordinator.enqueue("Grep", {"pattern": self.question.operation, "path": mount, "literal": True},
                            "按业务动作定位对应资料", task)

    @staticmethod
    def _plan(coordinator, role):
        return coordinator.task(role, MATERIAL_ROLES[role][0])

    def observe_grep(self, coordinator, args, data):
        root = args.get("path")
        if root not in (BUSINESS_MOUNTS | {"requirements"}) or args.get("pattern") != self.question.operation:
            return False
        locations = {}
        for hit in data.get("matches", []):
            locations.setdefault(hit["path"], hit["line"])
        for path, line in list(locations.items())[:3]:
            coordinator.enqueue("Read", {"path": path, "offset": max(1, line - 16)},
                                "读取命中业务动作的资料及入口", "entry")
        return True

    def _lookup(self, coordinator, app, name, role, method="", entry=("", "")):
        if not re.fullmatch(r"[A-Za-z_$][\w$.-]*", name, re.ASCII):
            return
        filename = name if "." in name else name + ".*"
        pattern = f'{app["mount"]}/**/{filename}'
        target = _FileTarget(app["mount"], name, role, method, entry)
        targets = self.lookups.setdefault(pattern, set())
        if target in targets:
            return
        targets.add(target)
        if pattern in self.located:
            self._attach(coordinator, self.located[pattern], target)
        else:
            coordinator.enqueue("Glob", {"pattern": pattern}, "按已读原文中的应用和符号定位文件", role)

    def observe_glob(self, coordinator, args, data):
        pattern = args.get("pattern")
        targets = self.lookups.get(pattern)
        if not targets:
            return False
        paths = data.get("paths", [])
        if len(paths) != 1 or data.get("truncated"):
            return False
        path = paths[0]
        self.located[pattern] = path
        for target in sorted(targets):
            self._attach(coordinator, path, target)
        return True

    def _attach(self, coordinator, path, target):
        self.followup_files.setdefault(path, set()).add(target)
        if target.role in {"PAGE", "CONTROLLER", "ENTRY_CLASS"}:
            self.entry_files[path] = {"path": path, "mount": target.mount, "symbol": target.name, "type": target.role}
        if path in self.source_reads:
            for data in self.source_reads[path].values():
                self._inspect(coordinator, target, data)
        coordinator.enqueue("Read", {"path": path}, "核对已定位入口或请求依赖的原文", target.role)

    def observe_read(self, coordinator, data):
        path = data["path"]
        if any(path.startswith(mount + "/") for mount in BUSINESS_MOUNTS):
            self._observe_business(coordinator, data)
        if path.startswith("repos/"):
            self.source_reads.setdefault(path, {})[(data["lineStart"], data["lineEnd"])] = data
        for target in sorted(self.followup_files.get(path, ())):
            self._inspect(coordinator, target, data)

    def _observe_business(self, coordinator, data):
        text = _text(data)
        operation = self.question.operation
        if operation not in text:
            return
        self.documents[data["path"]] = _ref(data)
        for line in text.splitlines():
            cells = line.strip().strip("|").split("|")
            if len(cells) == 2 and operation in cells[0] and re.search(r"申请|提交", cells[0]):
                self.terms.update(re.findall(r"[A-Za-z][\w-]*", cells[1], re.ASCII))
        for match in _ENTRY_SECTION.finditer(text):
            headings = {}
            for heading in re.finditer(r"^(#{1,6})[ \t]+(.+)$", text[:match.start()], re.M):
                depth = len(heading[1])
                headings = {level: name for level, name in headings.items() if level < depth}
                headings[depth] = heading[2]
            if not any(operation in name for name in headings.values()):
                continue
            for entry in extract_entry_anchors(match[1]):
                role = entry["entryType"]
                if role not in {"PAGE", "CONTROLLER", "ENTRY_CLASS"}:
                    continue
                apps = [app for app in self.apps if entry["application"] in {app["id"], app["name"]}]
                if len(apps) != 1:
                    continue
                if role == "PAGE":
                    if apps[0]["type"] != "FRONTEND":
                        continue
                    self._plan(coordinator, SUBMISSION)
                self._lookup(coordinator, apps[0], entry["entryName"], role)

    def _inspect(self, coordinator, target, data):
        """Schedule literal handler dependencies as candidates for Read."""
        path = data["path"]
        key = (target, path, data["lineStart"], data["lineEnd"])
        if key in self.checked_targets or not path.endswith(".java"):
            return
        self.checked_targets.add(key)
        app = next((app for app in self.apps if app["mount"] == target.mount), None)
        if app is None:
            return
        text = _text(data)
        if target.role == "CONTROLLER":
            suffixes = self.terms | {route.rsplit("/", 1)[-1] for route in coordinator.routes}
            for handler in _POST_HANDLER.finditer(text):
                _, route, symbol, parameters = handler.groups()
                if route.rsplit("/", 1)[-1] not in suffixes:
                    continue
                origin = (path, symbol)
                self.handler_symbols.setdefault(path, set()).add(symbol)
                request = _REQUEST_TYPE.search(parameters)
                if request:
                    self._lookup(coordinator, app, request[1], REQUEST, entry=origin)
                self._follow_delegate(coordinator, app, text, symbol, origin)
        elif target.role == REQUEST:
            if re.search(rf"\b(?:class|record)\s+{re.escape(target.name)}\b", text):
                self.role_files.setdefault((REQUEST, target.entry), set()).add(path)
        elif target.role == "delegate" and re.search(
            rf"^[ \t]*[\w$.<>?, \t]+\s+{re.escape(target.method)}\s*\([^;{{}}]*\)\s*[{{;]",
            text, re.M,
        ):
            if re.search(r"@FeignClient\b", text):
                self._plan(coordinator, CONNECTION)
                self.role_files.setdefault((CONNECTION, target.entry), set()).add(path)
            elif not self._follow_delegate(coordinator, app, text, target.method, target.entry):
                self.role_files.setdefault((VALIDATION, target.entry), set()).add(path)

    def _follow_delegate(self, coordinator, app, text, method, origin):
        call = re.search(
            rf"\b{re.escape(method)}\s*\([^;{{}}]*\)\s*\{{\s*return\s+(\w+)\.(\w+)\s*\(",
            text,
        )
        if not call:
            return False
        receiver, target = call.groups()
        types = set(re.findall(rf"\b([A-Z][\w$]*)\s+{re.escape(receiver)}\s*;", text))
        if len(types) == 1:
            self._lookup(coordinator, app, types.pop(), "delegate", target, origin)
        return True

    def reconcile(self, coordinator, investigation):
        for key, (objective, kind) in {
            ENTRY: ("核对本题实际提交的入口方法或路由", "read"), **MATERIAL_ROLES,
        }.items():
            if (key == ENTRY or key in coordinator.tasks) and key not in investigation.tasks:
                investigation.tasks[key] = Task(id=key, objective=objective, kind=kind, critical=False)
        candidate = investigation.tasks[ENTRY]
        entry_task = coordinator.tasks["entry"]
        if candidate.status == "supported":
            refs = [
                ref.model_dump() for ref in candidate.refs if ref.path.startswith("repos/") and ref.symbol
                and (not self.entry_files or ref.path in self.entry_files)
                and (ref.path not in self.handler_symbols or ref.symbol in self.handler_symbols[ref.path])
            ]
            if refs and (not self.selection or any(
                self._same_location(ref, old) for ref in refs for old in self.selection
            )):
                selected = {(ref["path"], ref["symbol"]): ref for ref in (*self.selection, *refs)}
                self.selection = tuple(selected.values())
                entry_task.update(status="source_read", evidence=list(self.selection), note="")
        elif not self.selection and candidate.status == "unavailable":
            entry_task.update(status="unavailable", note=candidate.note)
        if not self.selection and entry_task["status"] == "open" and self.handler_symbols:
            entry_task.update(status="partial", note="已取得入口候选原文，待确认本题的入口引用")
        for key in MATERIAL_ROLES:
            task = coordinator.tasks.get(key)
            if not task:
                continue
            submitted = investigation.tasks[key]
            refs = self._supporting_refs(key, submitted)
            if refs:
                task.update(status="evidence_ready", evidence=refs, note="")
            elif submitted.status == "unavailable":
                task.update(status="unavailable", evidence=[], note=submitted.note)
            elif submitted.status == "supported" or task["status"] == "evidence_ready":
                task.update(status="partial", evidence=[], note="需核对与当前入口对应的原文引用")

    def _supporting_refs(self, key, submitted):
        if submitted.status != "supported" or submitted.kind != MATERIAL_ROLES[key][1]:
            return []
        refs = [ref.model_dump() for ref in submitted.refs]
        paths = {ref["path"] for ref in refs}
        if key == REQUIREMENT:
            return refs if any(path.startswith("requirements/") for path in paths) else []
        if not self.selection or not all(path.startswith("repos/") for path in paths):
            return []
        if not any(self._same_location(ref, entry) for ref in refs for entry in self.selection):
            return []
        expected = {
            path for entry in self.selection
            for path in self.role_files.get((key, (entry["path"], entry["symbol"])), ())
        }
        if not expected <= paths:
            return []
        if key == SUBMISSION:
            pages = {path for path, entry in self.entry_files.items() if entry["type"] == "PAGE"}
            if pages and not pages & paths:
                return []
        return refs

    @staticmethod
    def _same_location(ref, entry):
        return (ref["path"] == entry["path"] and ref["symbol"] == entry["symbol"]
                and ref["start"] <= entry["end"] and entry["start"] <= ref["end"])

    def snapshot(self):
        return {
            "operation": self.question.operation, "material": self.question.material,
            "documents": list(self.documents.values()),
            "entryCandidates": [{**entry, "methods": sorted(self.handler_symbols.get(path, ()))}
                                for path, entry in self.entry_files.items()],
            "relatedSources": [
                {"task": key, "entryPath": entry[0], "symbol": entry[1], "paths": sorted(paths)}
                for (key, entry), paths in self.role_files.items()
            ],
            "searchTerms": sorted(self.terms), "selectedEntry": list(self.selection),
        }

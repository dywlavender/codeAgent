"""A small evidence-backed task queue; no question-specific business rules."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from business_code_agent.query.navigation import roles_for
from .context import answer_evidence
from .evidence import EvidenceCatalog
from .verification import Ref


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Record):
    path: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    symbol: str = ""


class PendingRead(Record):
    path: str
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=80, ge=1, le=240)


class Task(Record):
    id: str = Field(max_length=40)
    objective: str = Field(default="", max_length=180)
    kind: Literal["read", "relation", "consumer"] = "read"
    status: Literal["open", "supported", "unavailable"] = "open"
    critical: bool = True
    paths: list[str] = Field(default_factory=list, max_length=6, description="待核实的对象文件；目录只作搜索范围，不能代替文件／符号端点")
    refs: list[Location] = Field(default_factory=list, max_length=6)
    next_read: PendingRead | None = None
    note: str = Field(default="", max_length=240)


@tool
def Investigation(tasks: list[Task]) -> str:
    """更新直接影响本题结论的待办，可与搜索并行。只有缺证据会改变核心结论时才标为 critical，局部题可直接读取。
    新任务给 id/objective；完成任务用相同 id、status='supported' 与已交付原文的 path/start/end 引用。
    relation 须引用至少两个端点并填 symbol；consumer 须有实际前端代码引用。没有资料用 unavailable 并说明 note，不能当成不存在。
    已知具体文件和位置时填 next_read(path/offset/limit)，程序在下一次模型调用前补读。仅更新需要改变的字段。
    """
    return json.dumps({"tasks": [t.model_dump(exclude_unset=True) for t in tasks]}, ensure_ascii=False)


class InvestigationState:
    def __init__(self, project_id, files, navigation):
        self.project_id, self.files, self.navigation = project_id, files, navigation
        self.tasks = {}
        self.processed = set()
        self.attempted_reads = set()
        self.observations = []
        self.issues = []
        self.catalog = EvidenceCatalog(project_id, ())
        self.reopened_drafts = 0

    def _location(self, location, catalog):
        source = next((s for s in catalog.sources if s.path == location.path), None)
        if not source:
            return None
        ref = Ref(source=source.id, start=location.start, end=location.end)
        resolved = catalog.resolve(ref)
        if not resolved or (location.symbol and location.symbol not in catalog.excerpt(ref)):
            return None
        return resolved

    def _support_issue(self, task, catalog):
        values = [self._location(ref, catalog) for ref in task.refs]
        if not values or any(v is None or v.kind == "navigation" for v in values):
            return "引用尚未交付，或符号／行号不能核对"
        for path in task.paths:
            file, _ = self.files.locate(path)
            if file.is_dir():
                if not any(r.path.startswith(path + "/") for r in task.refs):
                    return "任务列出的目录范围尚无对象文件引用"
            elif not any(r.path == path for r in task.refs):
                return "任务列出的对象尚未全部提供原文引用"
        if task.kind in {"relation", "consumer"} and any(v.kind != "code" for v in values):
            return "实现关系需要源码引用，不能用需求或导航代替"
        if task.kind == "relation":
            anchors = {(r.path, r.symbol) for r in task.refs if r.symbol}
            if len(anchors) < 2:
                return "连接需要两个不同的文件／符号端点引用"
        if task.kind == "consumer" and not any("FRONTEND" in roles_for(self.navigation, r.path) for r in task.refs):
            return "尚无已登记前端应用的源码引用，不能认定页面使用"
        return None

    def observe(self, messages):
        # Prior answers/plans never become the current turn's implementation proof.
        start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
        current = messages[start:]
        evidence, _ = answer_evidence(current, float("inf"))
        self.catalog = EvidenceCatalog.from_text(self.project_id, evidence)
        calls = {c["id"]: c for m in current if isinstance(m, AIMessage) for c in m.tool_calls}
        commands = []
        for message in current:
            if not isinstance(message, ToolMessage) or message.tool_call_id in self.processed:
                continue
            self.processed.add(message.tool_call_id)
            call = calls.get(message.tool_call_id, {})
            try:
                data = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if message.name == "Investigation":
                commands.extend(data.get("tasks", []))
                continue
            args = call.get("args", {})
            if message.name == "Read":
                self.attempted_reads.add((args.get("path"), args.get("offset", 1), args.get("limit", 160)))
            outcome = (
                "error" if data.get("error") else
                "no_matches" if data.get("paths") == [] or data.get("matches") == [] else
                "truncated" if data.get("truncated") else "read" if message.name == "Read" else None
            )
            if outcome:
                self.observations.append({"tool": message.name, "input": args, "outcome": outcome})
        # Apply after all reads in the batch, independent of tool completion order.
        for command in commands:
            try:
                update = Task.model_validate(command)
                old = self.tasks.get(update.id)
                task = Task.model_validate({**(old.model_dump() if old else {}), **update.model_dump(exclude_unset=True)})
                if not task.objective:
                    raise ValueError("新任务需说明待确认的问题")
                task.paths = [self.files.locate(path)[1] for path in task.paths]
                for ref in task.refs:
                    ref.path = self.files.locate(ref.path)[1]
                if task.next_read:
                    task.next_read.path = self.files.locate(task.next_read.path)[1]
                if task.status == "supported":
                    issue = self._support_issue(task, self.catalog)
                    if issue:
                        task.status, task.note = "open", issue
                if task.status == "unavailable" and not task.note.strip():
                    task.status, task.note = "open", "请说明资料缺口，未确认不能当成不存在"
                self.tasks[task.id] = task
            except (ValueError, ValidationError) as exc:
                self.issues.append(str(exc)[:240])

    def next_read(self):
        for task in sorted(self.tasks.values(), key=lambda t: not t.critical):
            pending = task.next_read
            if task.status == "open" and pending and (pending.path, pending.offset, pending.limit) not in self.attempted_reads:
                return pending
        return None

    def has_open_critical(self):
        return any(t.critical and t.status == "open" for t in self.tasks.values())

    def revalidate(self, catalog):
        for task in self.tasks.values():
            if task.status == "supported":
                issue = self._support_issue(task, catalog)
                if issue:
                    task.status, task.note = "open", issue

    def snapshot(self):
        return {
            "tasks": [t.model_dump(exclude_none=True) for t in self.tasks.values()],
            "recentObservations": self.observations[-5:], "issues": self.issues[-3:],
            "reopenedDrafts": self.reopened_drafts, "semanticProof": False,
            "unresolvedCritical": [t.id for t in self.tasks.values() if t.critical and t.status != "supported"],
        }

    def render(self):
        return (
            "本轮调查状态（计划和搜索状态不是实现证据；supported 只表示引用端点已读，不证明语义连接正确）：\n"
            + json.dumps(self.snapshot(), ensure_ascii=False)
            + "\n只针对影响结论的缺口执行有明确目标的新补查。无可查线索时，依据已读原文作答并说明缺口。"
        )

    def qualify(self, answer):
        missing = [t.objective for t in self.tasks.values() if t.critical and t.status != "supported"]
        if missing:
            return answer + "\n\n本轮仍未确认：" + "；".join(missing) + "。以上相关结论和改动收益需保留这些条件。"
        return answer

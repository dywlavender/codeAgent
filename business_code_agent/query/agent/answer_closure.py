"""Record model-owned objectives and render an answer with explicit coverage."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from .evidence_ledger import EvidenceLedger, Finding
from .question_contract import QuestionContract, QuestionObjective


TOOL_NAME = "RecordFindings"
VERSION = 2
GUIDANCE = (
    "先保留用户原问题。在开始调查时用 RecordFindings 登记明确的子目标和逐字引文，可与定位工具同轮调用。"
    "简单单目标可直接用 Q0 表示原问题。后续发现遗漏时可追加目标。"
    "只登记原问题要求或回答所必需的连接；核实到问题要求的层级即可。"
    "relations 的 basis 填发现线索的 Read 路径和行号，connection 填显示实际调用、路由注册或配置绑定的源码位置。"
    "仅找到两端时把 connection 留空，取得连接原文后用 relation_id 更新。"
    "可随调查登记带精确源码路径和行号的 findings；同一目标提交的新 findings 替换该目标上次的记录。"
    "kind 用 source 核对实现、requirements 说明需求、business_context 说明业务约定。"
    "只追查会改变答案的缺口。已能回答时，用 RecordFindings(submit=true) 独立提交，无需额外问答回合。"
    "每条 claim 写一项能由所附 Read 原文独立支持的结论，包含适用条件和实际结果。"
    "引用用 Read 实际返回的完整路径和行区间，未核实处用 gap。"
    "可将有依据的局部结论与 gap 同时报，禁止把两端存在说成连接已确认。"
    "最终由程序按目标展示已提交的结论，独立的自由文本不会成为答案。"
)
FINAL_GUIDANCE = (
    "这轮不能调用工具。直接输出一个 JSON 对象，使用 RecordFindings 的同一字段，"
    "只需补充与现有 Read 原文关联的 findings 和 gap。不要再规划调查。"
    '格式：{"findings":[{"objective_id":"Q0","claim":"局部结论",'
    '"references":["repos/仓库/文件.java:10-12"],"gap":"未核实部分"}]}。'
)


class ObjectiveInput(BaseModel):
    requirement: str
    quote: str = Field(description="原问题中的连续逐字引文")


class RelationInput(BaseModel):
    objective_id: str
    relationship: str = ""
    basis: str = Field(default="", description="发现必要关系的 Read 路径和行号；登记新关系时提供")
    connection: list[str] = Field(default_factory=list, description="显示实际连接的源码路径和行号")
    relation_id: str = Field(default="", description="补齐已登记关系时使用该编号")


class FindingInput(BaseModel):
    objective_id: str
    claim: str = ""
    references: list[str] = Field(default_factory=list, description="Read 返回的完整相对路径:行号或路径:起始-结束")
    kind: Literal["source", "requirements", "business_context", "navigation"] = "source"
    gap: str = ""


class Update(BaseModel):
    objectives: list[ObjectiveInput] = Field(default_factory=list)
    relations: list[RelationInput] = Field(default_factory=list)
    findings: list[FindingInput] = Field(default_factory=list)
    submit: bool = False


class AnswerClosure:
    def __init__(self, question, materials):
        question = question.strip()
        root = QuestionObjective("Q0", question, "user", True, "other")
        self.contract = QuestionContract(question, (root,), segments=())
        self.quotes: dict[str, str] = {}
        self.ledger = EvidenceLedger(materials)
        self.issues: list[str] = []
        self.unlinked_relations: dict[str, list[str]] = {}
        self.revision_requested = False
        self.submitted = False

    def tool(self):
        return StructuredTool.from_function(
            self.record, name=TOOL_NAME, args_schema=Update,
            description="登记原问题要求、源码结论和必要关系。证据用 Read 返回的路径:行号。submit=true 提交最终答案。",
        )

    def record(self, objectives=(), relations=(), findings=(), submit=False, *, allow_revision=True):
        issues = []
        unlinked_relations = {}
        mixed_references = False
        goals = list(self.contract.objectives)
        quotes = {}
        for item in objectives:
            requirement, quote = item.requirement.strip(), item.quote.strip()
            if not requirement or not quote or quote not in self.contract.original_question:
                issues.append(f"目标引文不在原问题中：{requirement}")
            elif all(requirement != goal.requirement for goal in goals):
                goal = QuestionObjective(f"Q{len(goals)}", requirement, "user", True, "other")
                goals.append(goal)
                quotes[goal.id] = quote
        if quotes:
            try:
                self.contract = QuestionContract(self.contract.original_question, goals, segments=(),
                                                 revision=self.contract.revision + 1)
            except ValueError as exc:
                issues.append(str(exc))
            else:
                self.quotes.update(quotes)

        goal_ids = {goal.id for goal in self.contract.objectives}
        for item in relations:
            relation = self.ledger.relations.get(item.relation_id)
            if item.objective_id not in goal_ids or (item.relation_id and (
                    not relation or relation.objective_id != item.objective_id)):
                issues.append(f"关系关联的目标或编号无效：{item.objective_id} {item.relation_id}")
                continue
            if relation is None:
                basis = self.ledger.reference(item.basis, kinds={"source", "business_context"})
                relationship = item.relationship.strip()
                if not basis or not relationship:
                    issues.append(f"必要关系的 basis 需要 Read 路径和行号：{relationship}")
                    if relationship:
                        unlinked_relations.setdefault(item.objective_id, []).append(relationship)
                    continue
                relation = self.ledger.add_relation(item.objective_id, relationship, basis)
            connection, rejected = self.ledger.bind(item.connection, kinds={"source"})
            if rejected:
                mixed_references |= bool(connection)
                issues.extend(f"连接原文未核实：{ref}" for ref in rejected)
            else:
                self.ledger.resolve_relation(relation, connection)

        updates = {}
        for item in findings:
            if item.objective_id not in goal_ids:
                issues.append(f"结论关联的目标不存在：{item.objective_id}")
                continue
            evidence, rejected = self.ledger.bind(item.references, kinds={item.kind})
            mixed_references |= bool(evidence and rejected)
            updates.setdefault(item.objective_id, []).append(Finding(
                item.objective_id, item.claim.strip(), item.kind, evidence, rejected, item.gap.strip()))
            issues.extend(f"结论原文未核实：{ref}" for ref in rejected)
        self.ledger.findings.update(updates)
        self.issues = issues
        self.unlinked_relations = unlinked_relations
        if submit and allow_revision and mixed_references and not self.revision_requested:
            self.revision_requested = True
            return json.dumps({**self.coverage(), "submission": "revise",
                               "guidance": "结论混用了有效和无效引用。仅修正列出的项。"
                                           "把已经有依据的结论与未核实部分拆开，必要时定向补读。"
                                           "其余内容可保留，修正后再提交。"}, ensure_ascii=False)
        self.submitted |= submit
        return json.dumps(self.coverage(), ensure_ascii=False)

    def accept_text(self, text):
        """The existing no-tool budget finalization can submit the same fields."""
        try:
            proposal = Update.model_validate_json(text.strip().removeprefix("```json").removesuffix("```").strip())
        except ValidationError:
            return False
        self.record(proposal.objectives, proposal.relations, proposal.findings, submit=True, allow_revision=False)
        return True

    def _objectives(self):
        root, *others = self.contract.objectives
        if not others or root.id in self.ledger.findings or root.id in self.unlinked_relations or any(
                relation.objective_id == root.id for relation in self.ledger.relations.values()):
            return (root, *others)
        return others

    def _coverage(self, goal):
        findings = self.ledger.findings.get(goal.id, ())
        relations = [item for item in self.ledger.relations.values() if item.objective_id == goal.id]
        missing = [item for item in relations if not item.connection]
        evidenced = [item for item in findings if item.claim and item.evidence and not item.rejected]
        gaps = [item.gap for item in findings if item.gap]
        # A claim of completion for a blocked objective is withheld. The
        # model can still explicitly submit a narrower finding with its gap.
        accepted = [item for item in evidenced if not missing or item.gap]
        if len(evidenced) != sum(bool(item.claim) for item in findings):
            gaps.append("部分结论缺少与资料类型一致的有效原文引用。")
        gaps.extend(f"尚未核实连接：{item.relationship}" for item in missing)
        gaps.extend(f"尚未取得连接依据：{relationship}"
                    for relationship in self.unlinked_relations.get(goal.id, ()))
        if not accepted and not gaps:
            gaps.append("尚未取得可引用的结论。")
        status = ("supported" if accepted and not gaps else
                  "partial" if evidenced or relations else "unknown")
        return accepted, {"id": goal.id, "requirement": goal.requirement, "status": status,
                          "gaps": gaps, "relationIds": [item.id for item in relations]}

    def coverage(self):
        objectives = [self._coverage(goal)[1] for goal in self._objectives()]
        statuses = {goal["status"] for goal in objectives}
        status = next(iter(statuses)) if len(statuses) == 1 else "partial"
        if status == "supported" and self.issues:
            status = "partial"
        return {"registeredCoverage": status, "questionCoverage": "unknown", "semanticProof": False,
                "objectives": objectives, "issues": self.issues,
                "relations": [{"id": item.id, "objectiveId": item.objective_id,
                               "relationship": item.relationship, "status": item.status}
                              for item in self.ledger.relations.values()]}

    def prompt_state(self):
        lines = [f"- {goal.id} {goal.requirement}" for goal in self.contract.objectives if goal.id != "Q0"]
        lines.extend(f"- {relation.id} → {relation.objective_id} {relation.relationship}"
                     for relation in self.ledger.relations.values() if not relation.connection)
        return "\n\n已登记目标和待核实的必要连接：\n" + "\n".join(lines) if lines else ""

    def memory_findings(self):
        by_id = {item.id: item.reference for item in self.ledger.evidence.values()}
        findings = [item for goal in self._objectives() for item in self._coverage(goal)[0]]
        return [
            {"claim": item.claim, "evidence": [by_id[ref] for ref in item.evidence]}
            for item in findings
        ] + [
            {"claim": relation.relationship, "evidence": [by_id[ref] for ref in relation.connection]}
            for relation in self.ledger.relations.values() if relation.connection
        ]

    def snapshot(self):
        contract = self.contract.to_dict()
        for goal in contract["objectives"]:
            goal["questionQuote"] = self.quotes.get(goal["id"])
        return {**self.coverage(), "submitted": self.submitted, "revisionRequested": self.revision_requested,
                "questionContract": contract,
                "evidenceLedger": self.ledger.snapshot()}

    def render(self):
        by_id = {item.id: item.reference for item in self.ledger.evidence.values()}
        groups = []
        for goal in self._objectives():
            accepted, coverage = self._coverage(goal)
            lines = [f"### {goal.requirement}"] if goal.id != "Q0" else []
            for item in accepted:
                sources = "、".join(f"`{by_id[ref]}`" for ref in item.evidence)
                label = {"requirements": "需求依据", "business_context": "业务约定依据",
                         "navigation": "项目背景依据"}.get(item.kind, "源码依据")
                lines.append(f"{item.claim}\n\n{label} {sources}")
            for relation_id in coverage["relationIds"]:
                relation = self.ledger.relations[relation_id]
                if relation.connection and accepted:
                    sources = "、".join(f"`{by_id[ref]}`" for ref in relation.connection)
                    lines.append(f"{relation.relationship}的连接依据 {sources}")
            if coverage["gaps"]:
                lines.append("\n".join(f"- {gap}" for gap in dict.fromkeys(coverage["gaps"])))
            groups.append("\n\n".join(lines))
        if self.issues:
            groups.append("部分登记项缺少有效依据，已保留在调用轨迹中。")
        return "\n\n".join(groups)


def session_messages(messages):
    """Retain normal conversation history without carrying turn-specific IDs forward."""
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == TOOL_NAME:
            continue
        if isinstance(message, AIMessage) and any(call["name"] == TOOL_NAME for call in message.tool_calls):
            calls = [call for call in message.tool_calls if call["name"] != TOOL_NAME]
            if calls:
                yield AIMessage(content=message.content, tool_calls=calls)
        else:
            yield message

"""Frozen question objectives plus append-only discovered objectives for one run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

OBJECTIVE_KINDS = ("process", "behavior", "relationship", "data", "constraint", "other")
SOURCE_KINDS = ("source_code", "business_context", "user_followup")
EXTRACTION_STATUSES = ("ok", "fallback", "error")
MAX_OBJECTIVES = 12
CONTRACT_VERSION = 1

CONTRACT_EXTRACTION_PROMPT = (
    "你是问题目标提取器。只提取用户原问题中明确要求回答的目标，不要补充文件、类名或实现细节。"
    "只输出 JSON，不要解释：{\"objectives\":[{\"requirement\":\"目标原文\","
    "\"kind\":\"process|behavior|relationship|data|constraint|other\"}]}。"
    "目标 1-6 个；每个目标必须是原问题明确要求的对象、行为、关系或约束；不要输出泛化调查步骤。"
)


@dataclass(frozen=True)
class QuestionObjective:
    id: str
    requirement: str
    origin: str
    required: bool
    kind: str
    source: str | None = None
    source_kind: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement": self.requirement,
            "origin": self.origin,
            "required": self.required,
            "kind": self.kind,
            "source": self.source,
            "sourceKind": self.source_kind,
            "notes": list(self.notes),
        }


class QuestionContract:
    """Original requirements are frozen; discovered objectives may only be appended."""

    def __init__(self, original_question, objectives, *, revision=1,
                 extraction_status="ok"):
        self.original_question = _question_text(original_question)
        if not self.original_question:
            raise ValueError("原始问题不能为空")
        if extraction_status not in EXTRACTION_STATUSES:
            raise ValueError(f"未知的目标提取状态：{extraction_status}")
        self._objectives = list(objectives)
        self._revision = int(revision)
        if self._revision < 1:
            raise ValueError("问题契约版本必须为正整数")
        self._extraction_status = extraction_status
        self.validate()

    @classmethod
    def create(cls, original_question, extracted=(), *, extraction_status="ok"):
        question = _question_text(original_question)
        if not question:
            raise ValueError("原始问题不能为空")
        objectives = [QuestionObjective(
            id="Q0", requirement=question, origin="user", required=True, kind="other",
            notes=("完整回答原问题",))]
        for item in list(extracted)[:6]:
            requirement, kind = _extracted_item(item)
            if not requirement:
                continue
            if any(_normalize(requirement) == _normalize(obj.requirement)
                   for obj in objectives):
                continue
            objectives.append(QuestionObjective(
                id=f"Q{len(objectives)}", requirement=requirement, origin="user",
                required=True, kind=kind))
        if len(objectives) == 1 and extracted:
            extraction_status = "fallback"
        return cls(question, objectives, extraction_status=extraction_status)

    @classmethod
    def fallback(cls, original_question, reason="empty"):
        question = _question_text(original_question)
        if not question:
            raise ValueError("原始问题不能为空")
        return cls(
            question,
            [QuestionObjective(id="Q0", requirement=question, origin="user",
                               required=True, kind="other",
                               notes=(f"目标提取回退：{reason}",))],
            extraction_status="error" if reason == "error" else "fallback",
        )

    @property
    def revision(self):
        return self._revision

    @property
    def extraction_status(self):
        return self._extraction_status

    @property
    def objectives(self):
        return tuple(self._objectives)

    @property
    def required_objectives(self):
        return tuple(obj for obj in self._objectives if obj.required)

    def objective(self, objective_id):
        for obj in self._objectives:
            if obj.id == objective_id:
                return obj
        raise KeyError(objective_id)

    def add_discovered(self, requirement, *, source, source_kind="source_code",
                       kind="relationship", note=None):
        requirement = _text(requirement)
        source = _text(source)
        if not requirement:
            raise ValueError("派生目标不能为空")
        if not source:
            raise ValueError("派生目标必须给出源码或业务知识依据")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"未知的派生目标依据类型：{source_kind}")
        if kind not in OBJECTIVE_KINDS:
            kind = "other"
        for obj in self._objectives:
            if _normalize(obj.requirement) == _normalize(requirement):
                return obj
        if len(self._objectives) >= MAX_OBJECTIVES:
            raise ValueError("调查目标数量超过上限")
        objective = QuestionObjective(
            id=f"Q{len(self._objectives)}", requirement=requirement,
            origin="discovered", required=False, kind=kind,
            source=source, source_kind=source_kind,
            notes=(_text(note),) if _text(note) else (),
        )
        self._objectives.append(objective)
        self._revision += 1
        return objective

    def supplement(self, objective_id, note):
        note = _text(note)
        if not note:
            return self.objective(objective_id)
        for index, obj in enumerate(self._objectives):
            if obj.id != objective_id:
                continue
            if note in obj.notes:
                return obj
            updated = replace(obj, notes=(*obj.notes, note))
            self._objectives[index] = updated
            self._revision += 1
            return updated
        raise KeyError(objective_id)

    def validate(self):
        if not self.original_question:
            raise ValueError("原始问题不能为空")
        if not self._objectives:
            raise ValueError("问题契约至少需要一个目标")
        if len(self._objectives) > MAX_OBJECTIVES:
            raise ValueError("调查目标数量超过上限")
        ids = set()
        normalized = set()
        for obj in self._objectives:
            if not isinstance(obj, QuestionObjective):
                raise ValueError("问题契约包含无效目标")
            if not obj.id or obj.id in ids:
                raise ValueError("目标 id 必须唯一")
            ids.add(obj.id)
            if not obj.requirement or obj.origin not in {"user", "discovered"}:
                raise ValueError("目标缺少内容或来源")
            if obj.kind not in OBJECTIVE_KINDS:
                raise ValueError(f"未知的目标类型：{obj.kind}")
            if obj.origin == "user" and not obj.required:
                raise ValueError("用户原始目标必须为必答")
            if obj.origin == "discovered":
                if not obj.source or obj.source_kind not in SOURCE_KINDS:
                    raise ValueError("派生目标缺少有效依据")
                if obj.required:
                    raise ValueError("派生目标不能成为必答目标")
            key = _normalize(obj.requirement)
            if not key:
                raise ValueError("目标内容为空")
            if key in normalized:
                raise ValueError("目标内容重复")
            normalized.add(key)
        root = next((obj for obj in self._objectives if obj.id == "Q0"), None)
        if root is None or root.origin != "user" or not root.required:
            raise ValueError("问题契约缺少不可删除的原始问题目标")
        if root.requirement != self.original_question:
            raise ValueError("原始问题目标不得被修改")

    def prompt_view(self):
        lines = ["调查契约（用户明确要求的目标不可删除）："]
        for obj in self.required_objectives:
            lines.append(f"- {obj.id} [{obj.kind}] {obj.requirement}")
        optional = [obj for obj in self._objectives if not obj.required]
        if optional:
            lines.append("可选补充目标（来自源码或业务资料，不作为必答）：")
            for obj in optional:
                lines.append(f"- {obj.id} [{obj.kind}] {obj.requirement}")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "version": CONTRACT_VERSION,
            "originalQuestion": self.original_question,
            "revision": self._revision,
            "extractionStatus": self._extraction_status,
            "objectives": [obj.to_dict() for obj in self._objectives],
        }

    def __len__(self):
        return len(self._objectives)


def _extracted_item(item):
    if isinstance(item, str):
        return _text(item), "other"
    if not isinstance(item, dict):
        return "", "other"
    requirement = _text(item.get("requirement"))
    kind = str(item.get("kind") or "other").strip().lower()
    return requirement, kind if kind in OBJECTIVE_KINDS else "other"


def _json_object(text):
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(value[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_contract_response(text, original_question):
    data = _json_object(text)
    if not isinstance(data, dict) or not isinstance(data.get("objectives"), list):
        return QuestionContract.fallback(original_question, "parse")
    extracted = []
    for item in data["objectives"][:6]:
        requirement, kind = _extracted_item(item)
        if requirement:
            extracted.append({"requirement": requirement, "kind": kind})
    status = "ok" if extracted else "fallback"
    return QuestionContract.create(original_question, extracted, extraction_status=status)


def extract_question_contract(model, original_question):
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = model.invoke([SystemMessage(CONTRACT_EXTRACTION_PROMPT),
                                 HumanMessage(original_question)])
    except Exception:
        return QuestionContract.fallback(original_question, "error")
    return parse_contract_response(str(getattr(response, "text", "") or ""),
                                   original_question)


async def aextract_question_contract(model, original_question):
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        response = await model.ainvoke([SystemMessage(CONTRACT_EXTRACTION_PROMPT),
                                        HumanMessage(original_question)])
    except Exception:
        return QuestionContract.fallback(original_question, "error")
    return parse_contract_response(str(getattr(response, "text", "") or ""),
                                   original_question)


def _normalize(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def _text(value):
    return str(value or "").strip()[:2000]


def _question_text(value):
    return str(value or "").strip()

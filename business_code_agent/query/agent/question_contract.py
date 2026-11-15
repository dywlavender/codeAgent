"""Frozen question objectives plus append-only discovered objectives for one run."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, replace

OBJECTIVE_KINDS = ("process", "behavior", "relationship", "data", "constraint", "other")
SOURCE_KINDS = ("source_code", "business_context", "user_followup")
EXTRACTION_STATUSES = ("ok", "partial", "fallback", "error")
COVERAGE_STATUSES = ("verified", "partial", "unverified")
MAX_OBJECTIVES = 12
MAX_EXTRACTED = 6
CONTRACT_VERSION = 2

CONTRACT_EXTRACTION_PROMPT = (
    "你是问题目标提取器。只提取用户原问题中明确要求回答的目标，不要补充文件、类名或实现细节。"
    "原问题中的每个分句或并列要求分别输出一个目标，不要合并，也不要遗漏。"
    "只输出 JSON，不要解释：{\"objectives\":[{\"requirement\":\"目标原文\","
    "\"kind\":\"process|behavior|relationship|data|constraint|other\"}]}。"
    "目标 1-6 个；每个目标必须是原问题明确要求的对象、行为、关系或约束；不要输出泛化调查步骤。"
)

_SENTENCE_SPLIT = re.compile(r"[？?。！!；;\n]+")
_CLAUSE_SPLIT = re.compile(r"[，,]+")
_INTERROGATIVE_CUES = (
    "如何", "怎么", "怎样", "哪些", "哪个", "哪里", "哪儿", "哪",
    "是否", "会不会", "能不能", "能否", "什么", "为何", "为什么",
    "多少", "由谁", "谁", "何时", "什么时候", "呢", "吗",
)
_IMPERATIVE_CUES = ("请", "说明", "给出", "解释", "梳理", "列出", "追到")
_LEADING_CONNECTORS = re.compile(r"^(以及|并|同时|然后|再|另外|此外|最后)")
_CONDITIONAL_CUES = ("如果", "假如", "倘若", "要是")
_STOP_CHARS = set("的了呢吗在是和与及或把被将对于中上下里时后前等这那其分别为以所此")
_NON_CONTENT_CHARS = set(_STOP_CHARS)
for _cue in _INTERROGATIVE_CUES + _IMPERATIVE_CUES:
    _NON_CONTENT_CHARS.update(_cue)
_SCENE_CONTEXT = re.compile(
    r"^(?:在当前|在本|基于当前)[^，,]*(?:项目|代码|工程|实现)[^，,]*$")
_LEADING_LATIN = re.compile(
    r"^[A-Za-z][A-Za-z0-9._-]*(?:\s+[A-Za-z][A-Za-z0-9._-]*)+(?=\s*[\u4e00-\u9fff])")
_DISTRIBUTIVE = re.compile(
    r"(?P<count>[一二两三四五六七八九十]|\d{1,2})(?P<measure>次|个|张|笔|条|种|类|项)"
    r"(?P<object>[^，,。？?；;]{0,12}?)(?P<dist>各自|分别|各)")
_CHINESE_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CONTENT_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_COMPOUND_SPLIT = re.compile(r"[、]|和|与|及|或")


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
    question_quote: str | None = None
    question_quote_derived: bool = False
    parent_id: str | None = None
    blocking: bool = False
    necessity: str | None = None

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
            "questionQuote": self.question_quote,
            "questionQuoteDerived": self.question_quote_derived,
            "parentObjectiveId": self.parent_id,
            "blocking": self.blocking,
            "necessity": self.necessity,
        }


class QuestionContract:
    """Original requirements are frozen; discovered objectives may only be appended."""

    def __init__(self, original_question, objectives, *, revision=1,
                 extraction_status="partial", coverage_status="unverified",
                 missing_segments=(), truncated=False, truncated_count=0,
                 ungrounded_count=0):
        self.original_question = _question_text(original_question)
        if not self.original_question:
            raise ValueError("原始问题不能为空")
        if extraction_status not in EXTRACTION_STATUSES:
            raise ValueError(f"未知的目标提取状态：{extraction_status}")
        if coverage_status not in COVERAGE_STATUSES:
            raise ValueError(f"未知的问题覆盖状态：{coverage_status}")
        self._objectives = list(objectives)
        self._revision = int(revision)
        if self._revision < 1:
            raise ValueError("问题契约版本必须为正整数")
        self._extraction_status = extraction_status
        self._coverage_status = coverage_status
        self._missing_segments = tuple(
            text for value in missing_segments if (text := _text(value)))
        self._truncated = bool(truncated)
        self._truncated_count = int(truncated_count)
        self._ungrounded_count = int(ungrounded_count)
        if self._truncated_count < 0 or self._ungrounded_count < 0:
            raise ValueError("目标计数不能为负")
        self.validate()

    @classmethod
    def create(cls, original_question, extracted=(), *, require_quotes=False,
               revision=1):
        question = _question_text(original_question)
        if not question:
            raise ValueError("原始问题不能为空")
        items = list(extracted)
        objectives = [QuestionObjective(
            id="Q0", requirement=question, origin="user", required=True, kind="other",
            notes=("完整回答原问题",))]
        attempted = 0
        truncated_count = 0
        ungrounded_count = 0
        for item in items:
            requirement, kind, quote = _extracted_item(item)
            if not requirement:
                continue
            attempted += 1
            if any(_normalize(requirement) == _normalize(obj.requirement)
                   for obj in objectives):
                continue
            quote_was_provided = quote is not None
            derived = False
            if quote_was_provided:
                quote = _valid_quote(quote, question)
                if quote is None or not _quote_supports_requirement(requirement, quote):
                    quote = _locate_quote(requirement, question)
                    derived = quote is not None
                if quote is None:
                    ungrounded_count += 1
                    continue
            else:
                quote = _locate_quote(requirement, question)
                derived = quote is not None
                if quote is None and require_quotes:
                    ungrounded_count += 1
                    continue
            if len(objectives) > MAX_EXTRACTED:
                truncated_count += 1
                continue
            objectives.append(QuestionObjective(
                id=f"Q{len(objectives)}", requirement=requirement, origin="user",
                required=True, kind=kind, question_quote=quote,
                question_quote_derived=derived))
        extracted_objectives = [obj for obj in objectives if obj.id != "Q0"]
        coverage_status, missing_segments = assess_question_coverage(
            question, extracted_objectives)
        truncated = truncated_count > 0
        if truncated:
            coverage_status = "partial"
        if any(obj.question_quote is None for obj in extracted_objectives):
            coverage_status = "unverified" if not missing_segments else "partial"
        if not extracted_objectives:
            status = "partial" if attempted and ungrounded_count else "fallback"
        else:
            status = ("ok" if coverage_status == "verified" and not truncated
                      and not ungrounded_count else "partial")
        return cls(
            question, objectives, revision=revision, extraction_status=status,
            coverage_status=coverage_status, missing_segments=missing_segments,
            truncated=truncated, truncated_count=truncated_count,
            ungrounded_count=ungrounded_count)

    @classmethod
    def fallback(cls, original_question, reason="empty"):
        question = _question_text(original_question)
        if not question:
            raise ValueError("原始问题不能为空")
        segments = question_segments(question)
        return cls(
            question,
            [QuestionObjective(id="Q0", requirement=question, origin="user",
                               required=True, kind="other",
                               notes=(f"目标提取回退：{reason}",))],
            extraction_status="error" if reason == "error" else "fallback",
            coverage_status="partial" if segments else "unverified",
            missing_segments=segments,
        )

    @property
    def revision(self):
        return self._revision

    @property
    def extraction_status(self):
        return self._extraction_status

    @property
    def coverage_status(self):
        return self._coverage_status

    @property
    def missing_segments(self):
        return self._missing_segments

    @property
    def truncated(self):
        return self._truncated

    @property
    def truncated_count(self):
        return self._truncated_count

    @property
    def ungrounded_count(self):
        return self._ungrounded_count

    @property
    def objectives(self):
        return tuple(self._objectives)

    @property
    def required_objectives(self):
        return tuple(obj for obj in self._objectives if obj.required)

    @property
    def blocking_objectives(self):
        return tuple(obj for obj in self._objectives if obj.blocking)

    @property
    def structural_coverage_verified(self):
        """原问题明确要求已被目标结构覆盖；不代表语义正确或答案完整。"""
        return (self._extraction_status == "ok"
                and self._coverage_status == "verified"
                and not self._truncated and not self._ungrounded_count)

    def objective(self, objective_id):
        for obj in self._objectives:
            if obj.id == objective_id:
                return obj
        raise KeyError(objective_id)

    def add_user_followup(self, requirement, *, note=None):
        requirement = _text(requirement)
        if not requirement:
            raise ValueError("用户追问目标不能为空")
        for obj in self._objectives:
            if _normalize(obj.requirement) == _normalize(requirement):
                return obj
        if len(self._objectives) >= MAX_OBJECTIVES:
            raise ValueError("调查目标数量超过上限")
        objective = QuestionObjective(
            id=f"Q{len(self._objectives)}", requirement=requirement,
            origin="user", required=True, kind="other",
            source="用户后续追问", source_kind="user_followup",
            notes=(_text(note),) if _text(note) else (),
        )
        self._objectives.append(objective)
        self._revision += 1
        return objective

    def add_discovered(self, requirement, *, source, source_kind="source_code",
                       kind="relationship", note=None, parent_id=None, blocking=False,
                       necessity=None):
        requirement = _text(requirement)
        source = _text(source)
        parent_id = _text(parent_id) or None
        necessity = _text(necessity) or None
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
        if parent_id is not None and not any(obj.id == parent_id for obj in self._objectives):
            raise ValueError("父目标不存在")
        if blocking:
            if parent_id is None:
                raise ValueError("阻断性派生目标必须关联父目标")
            if not necessity:
                raise ValueError("阻断性派生目标必须说明必要性")
        if len(self._objectives) >= MAX_OBJECTIVES:
            raise ValueError("调查目标数量超过上限")
        objective = QuestionObjective(
            id=f"Q{len(self._objectives)}", requirement=requirement,
            origin="discovered", required=False, kind=kind,
            source=source, source_kind=source_kind,
            notes=(_text(note),) if _text(note) else (),
            parent_id=parent_id, blocking=bool(blocking), necessity=necessity,
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
        by_id = {}
        for obj in self._objectives:
            if not isinstance(obj, QuestionObjective):
                raise ValueError("问题契约包含无效目标")
            if not obj.id or obj.id in ids:
                raise ValueError("目标 id 必须唯一")
            ids.add(obj.id)
            by_id[obj.id] = obj
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
                    raise ValueError("派生目标不能成为用户必答目标")
            if obj.origin == "user" and obj.blocking:
                raise ValueError("用户目标不能标记为派生阻断关系")
            if obj.blocking and (not obj.parent_id or not obj.necessity):
                raise ValueError("阻断性目标必须关联父目标并说明必要性")
            if obj.question_quote is not None and _valid_quote(
                    obj.question_quote, self.original_question) is None:
                raise ValueError("目标引用的原问题片段无效")
            key = _normalize(obj.requirement)
            if not key:
                raise ValueError("目标内容为空")
            if key in normalized:
                raise ValueError("目标内容重复")
            normalized.add(key)
        root = by_id.get("Q0")
        if root is None or root.origin != "user" or not root.required:
            raise ValueError("问题契约缺少不可删除的原始问题目标")
        if root.requirement != self.original_question:
            raise ValueError("原始问题目标不得被修改")
        for obj in self._objectives:
            if obj.blocking and obj.parent_id not in by_id:
                raise ValueError("阻断性目标的父目标不存在")
            seen = set()
            current = obj
            while current.parent_id:
                if current.id in seen:
                    raise ValueError("父目标关系存在循环")
                seen.add(current.id)
                parent = by_id.get(current.parent_id)
                if parent is None:
                    raise ValueError("父目标不存在")
                current = parent
        if self._truncated != (self._truncated_count > 0):
            raise ValueError("目标截断标记与截断数量不一致")
        if self._extraction_status == "ok":
            if (self._coverage_status != "verified" or self._truncated
                    or self._ungrounded_count):
                raise ValueError("提取状态为 ok 时必须通过问题覆盖校验")
            if any(obj.origin == "user" and obj.id != "Q0" and not obj.question_quote
                   and obj.source_kind != "user_followup"
                   for obj in self._objectives):
                raise ValueError("提取状态为 ok 时用户目标必须带有原问题依据")

    def prompt_view(self):
        lines = ["调查契约（用户明确要求的目标不可删除）："]
        for obj in self.required_objectives:
            lines.append(f"- {obj.id} [{obj.kind}] {obj.requirement}")
        blocking = [obj for obj in self._objectives if obj.blocking]
        if blocking:
            lines.append("阻断性必要关系（未核实前不得将父目标视为完成）：")
            for obj in blocking:
                lines.append(
                    f"- {obj.id} [{obj.kind}] {obj.requirement}"
                    f"（父目标：{obj.parent_id}；依据：{obj.source}；"
                    f"必要性：{obj.necessity}）")
        optional = [obj for obj in self._objectives if not obj.required and not obj.blocking]
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
            "coverageStatus": self._coverage_status,
            "missingQuestionSegments": list(self._missing_segments),
            "truncated": self._truncated,
            "truncatedCount": self._truncated_count,
            "ungroundedCount": self._ungrounded_count,
            "objectives": [obj.to_dict() for obj in self._objectives],
        }

    def __len__(self):
        return len(self._objectives)


def question_segments(question):
    segments = []
    for raw in _SENTENCE_SPLIT.split(str(question or "")):
        sentence = raw.strip()
        if not sentence:
            continue
        clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(sentence)
                   if clause.strip()]
        sentence_segments = []
        current = []
        for clause in clauses:
            current.append(clause)
            if _has_requirement_cue(clause):
                sentence_segments.append("，".join(current))
                current = []
        if current and sentence_segments:
            sentence_segments[-1] = sentence_segments[-1] + "，" + "，".join(current)
        segments.extend(sentence_segments)
    return tuple(segments)


def assess_question_coverage(question, objectives):
    segments = question_segments(question)
    if not segments:
        return "unverified", ()
    requirements = [obj.requirement for obj in objectives]
    missing = tuple(segment for segment in segments
                    if not _segment_covered(segment, requirements))
    if missing:
        return "partial", missing
    return "verified", ()


def _has_requirement_cue(text):
    value = str(text or "").strip()
    if any(cue in value for cue in _INTERROGATIVE_CUES):
        return True
    stripped = _LEADING_CONNECTORS.sub("", value).strip()
    return any(stripped.startswith(cue) for cue in _IMPERATIVE_CUES)


def _segment_covered(segment, requirements):
    text = _segment_coverage_text(segment)
    parts = _coverage_parts(text)
    if _parts_covered(parts, requirements):
        return True
    count = _distributive_count(text)
    if count > 1:
        stripped = _strip_distributive(text)
        stripped_parts = _coverage_parts(stripped)
        coverers = [requirement for requirement in requirements
                    if _parts_covered(stripped_parts, [requirement])]
        if len(coverers) >= count:
            return True
    return False


def _parts_covered(parts, requirements):
    return all(
        any(_part_covered(part, requirement) for requirement in requirements)
        for part in parts)


def _part_covered(candidate, requirement):
    target = _distinctive(candidate)
    return bool(target) and target <= _distinctive(requirement)


def _distinctive(text):
    value = _content(text)
    for cue in _CONDITIONAL_CUES:
        value = value.replace(cue, "")
    return {char for char in value if char not in _NON_CONTENT_CHARS}


def _segment_coverage_text(segment):
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(str(segment or ""))
               if clause.strip()]
    kept = [clause for clause in clauses if not _SCENE_CONTEXT.match(clause)]
    text = "，".join(kept) if kept else str(segment or "")
    return _LEADING_LATIN.sub("", text).strip()


def _distributive_count(text):
    match = _DISTRIBUTIVE.search(str(text or ""))
    if not match:
        return 0
    raw = match.group("count")
    return int(raw) if raw.isdigit() else _CHINESE_NUMBERS.get(raw, 0)


def _strip_distributive(text):
    value = str(text or "")
    match = _DISTRIBUTIVE.search(value)
    if not match:
        return value
    return value[:match.start()] + match.group("object") + value[match.end():]


def _coverage_parts(candidate):
    parts = [part.strip() for part in _COMPOUND_SPLIT.split(candidate)]
    parts = [part for part in parts if len(_content(part)) >= 2]
    if 1 < len(parts) <= 8:
        return parts
    return [candidate]


def _bigrams(text):
    value = _content(text)
    if len(value) <= 1:
        return {value} if value else set()
    return {value[index:index + 2] for index in range(len(value) - 1)}


def _content(text):
    return _CONTENT_PATTERN.sub("", str(text or "").lower())


def _valid_quote(quote, question):
    quote = _text(quote)
    if len(_content(quote)) < 4:
        return None
    if _content(quote) not in _content(question):
        return None
    return quote


def _locate_quote(requirement, question):
    partial = None
    for segment in question_segments(question):
        text = _segment_coverage_text(segment)
        if len(_content(text)) < 4:
            continue
        if _part_covered(text, requirement):
            return text
        if partial is None and any(_part_covered(part, requirement)
                                   for part in _coverage_parts(text)):
            partial = text
    if partial is not None:
        return partial
    return _contiguous_quote(requirement, question)


def _contiguous_quote(requirement, question):
    required_text = _content(requirement)
    question_text = _content(question)
    if len(required_text) < 4 or len(question_text) < 4:
        return None
    block = difflib.SequenceMatcher(
        None, required_text, question_text, autojunk=False).find_longest_match(
            0, len(required_text), 0, len(question_text))
    minimum = max(4, int(len(required_text) * 0.15))
    if block.size < minimum:
        return None
    return question_text[block.b:block.b + block.size]


def _quote_supports_requirement(requirement, quote):
    requirement_text = _content(requirement)
    if not requirement_text:
        return False
    if len(requirement_text) <= 6:
        required_units = set(requirement_text)
        quote_units = set(_content(quote))
    else:
        required_units = _bigrams(requirement)
        quote_units = _bigrams(quote)
    if not required_units:
        return False
    return len(required_units & quote_units) / len(required_units) >= 0.3


def _extracted_item(item):
    if isinstance(item, str):
        return _text(item), "other", None
    if not isinstance(item, dict):
        return "", "other", None
    requirement = _text(item.get("requirement"))
    kind = str(item.get("kind") or "other").strip().lower()
    quote = _text(item.get("quote")) or None
    return requirement, kind if kind in OBJECTIVE_KINDS else "other", quote


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
    return QuestionContract.create(original_question, data["objectives"],
                                   require_quotes=True)


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

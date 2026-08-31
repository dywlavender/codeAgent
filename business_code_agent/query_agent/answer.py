from __future__ import annotations

from typing import Any, Iterable, Mapping

from .conflicts import ConflictDetector


ANSWER_KEYS = ("conclusion", "businessFlow", "technicalFlow", "facts", "inferences", "unknowns", "conflicts")


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    return list(value)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _evidence_ids(value: Any) -> list[str]:
    raw = _get(value, "evidenceIds", "evidence_ids", "evidenceId", "evidence_id", default=[]) or []
    return _unique_strings(_as_list(raw))


def _statement(value: Any) -> str:
    return str(_get(value, "statement", "fact", "content", "claim", default="")).strip()


def _source(value: Any) -> str:
    return str(_get(value, "sourceType", "source_type", "source", default="UNKNOWN")).upper()


def source_priority(intent: str, question: str = "") -> dict[str, int]:
    """Return question-sensitive weights, never a global source ordering."""
    intent = (intent or "").upper()
    question = (question or "").lower()
    if any(term in question for term in ("是否符合", "一致", "偏差", "compliance")):
        return {"CODE": 100, "REQUIREMENT": 100, "BUSINESS": 70}
    if any(term in question for term in ("是什么", "什么意思", "业务含义", "怎么理解")):
        return {"BUSINESS": 100, "CODE": 90, "REQUIREMENT": 80}
    if intent == "RULE_REASON" or any(term in question for term in ("为什么", "规则来源", "为何")):
        return {"REQUIREMENT": 100, "BUSINESS": 95, "CODE": 75}
    if intent == "CROSS_PROCESS" or any(term in question for term in ("跨流程", "什么关系", "之间")):
        return {"BUSINESS": 100, "REQUIREMENT": 90, "CODE": 85}
    if intent in {"DATA_TRACE", "BUSINESS_LOGIC"} or any(term in question for term in ("当前", "实际", "在哪", "怎么处理")):
        return {"CODE": 100, "BUSINESS": 75, "REQUIREMENT": 70}
    return {"CODE": 90, "BUSINESS": 90, "REQUIREMENT": 90}


class AnswerBuilder:
    """Build the stable M4 answer schema from verified state facts.

    Search candidates are intentionally never inspected.  A fact must carry at
    least one Evidence ID and must not be marked candidate/unverified.
    """

    def __init__(self, conflict_detector: ConflictDetector | None = None):
        self.conflict_detector = conflict_detector or ConflictDetector()

    def build(self, state: Any) -> dict[str, Any]:
        question = str(_get(state, "question", default=""))
        intent = str(_get(state, "intent", default="UNKNOWN"))
        priorities = source_priority(intent, question)
        known = _as_list(_get(state, "known_facts", "knownFacts", default=[]))
        facts = [fact for fact in (self._fact(item) for item in known) if fact]
        facts.sort(key=lambda item: (-priorities.get(item["sourceType"], 0), item["statement"], item["evidenceIds"]))

        # Detect before normalising to the public Fact schema so evaluator
        # metadata such as conflictKey/value/polarity remains available.
        detected = self.conflict_detector.detect(known)
        supplied = _as_list(_get(state, "conflicts", default=[]))
        conflicts = self._merge_conflicts(supplied, detected)
        unknowns = _unique_strings([
            *_as_list(_get(state, "unknowns", default=[])),
            *self._gap_questions(_as_list(_get(state, "evidence_gaps", "evidenceGaps", default=[]))),
        ])
        inferences = self._inferences(_as_list(_get(state, "inferences", default=[])))
        flow = self._business_flow(_as_list(_get(state, "business_flow", "businessFlow", default=[])))
        if not flow:
            flow = self._flow_from_facts(known, source_type="BUSINESS")
        technical_flow = self._flow_from_facts(known, source_type="CODE")

        if conflicts:
            conclusion = "CONFLICT：当前不同来源的已验证证据存在不一致，不能合并为单一确定结论。"
        elif not facts:
            conclusion = "当前证据不足，无法形成确定结论。"
            unknowns = _unique_strings([*unknowns, "未获得可支撑结论的已验证 Evidence。"])
        else:
            leading = [item["statement"] for item in facts[:3]]
            conclusion = "；".join(leading) + "。"

        return {
            "conclusion": conclusion,
            "businessFlow": flow,
            "technicalFlow": technical_flow,
            "facts": facts,
            "inferences": inferences,
            "unknowns": unknowns,
            "conflicts": conflicts,
        }

    @staticmethod
    def _fact(value: Any) -> dict[str, Any] | None:
        statement, ids = _statement(value), _evidence_ids(value)
        if not statement or not ids:
            return None
        if _get(value, "candidate", "isCandidate", default=False):
            return None
        if str(_get(value, "status", default="")).upper() in {
            "SUGGESTED", "STALE", "CONFLICT", "CANDIDATE",
        }:
            return None
        verified = _get(value, "verified", "isVerified", default=True)
        if verified is False:
            return None
        return {"statement": statement, "sourceType": _source(value), "evidenceIds": ids}

    @staticmethod
    def _inferences(values: list[Any]) -> list[Any]:
        result: list[Any] = []
        for value in values:
            if isinstance(value, str):
                # An untraceable free-text inference is not safe to render.
                continue
            statement, ids = _statement(value), _evidence_ids(value)
            if statement and ids and not _get(value, "candidate", "isCandidate", default=False):
                result.append({"statement": statement, "evidenceIds": ids})
        return result

    @staticmethod
    def _business_flow(values: list[Any]) -> list[Any]:
        result: list[Any] = []
        for value in values:
            if isinstance(value, str):
                continue
            ids = _evidence_ids(value)
            if not ids or _get(value, "candidate", "isCandidate", default=False):
                continue
            item = dict(value) if isinstance(value, Mapping) else {"statement": _statement(value)}
            item["evidenceIds"] = ids
            result.append(item)
        return result

    @staticmethod
    def _flow_from_facts(values: list[Any], *, source_type: str) -> list[Any]:
        result = []
        for value in values:
            if str(_get(value, "role", default="")).upper() != "PROCESS_LINK":
                continue
            if _source(value) != source_type:
                continue
            statement, ids = _statement(value), _evidence_ids(value)
            if statement and ids:
                result.append({"statement": statement, "evidenceIds": ids})
        return result

    @staticmethod
    def _gap_questions(gaps: list[Any]) -> list[str]:
        return [str(_get(gap, "question", default="")).strip() for gap in gaps if _get(gap, "question", default="")]

    @staticmethod
    def _merge_conflicts(supplied: list[Any], detected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for value in [*supplied, *detected]:
            item = dict(value) if isinstance(value, Mapping) else {"reason": str(value)}
            item["status"] = "CONFLICT"
            ids = _unique_strings(_as_list(item.get("evidenceIds", item.get("evidence_ids", []))))
            if ids:
                item["evidenceIds"] = ids
            identity = tuple(sorted(ids)) or (str(item.get("conflictKey", "")), str(item.get("reason", "")))
            if identity not in seen:
                seen.add(identity)
                result.append(item)
        return result


class AnswerRenderer:
    """Render an answer for people while leaving the structured result intact."""

    def render(self, answer: Mapping[str, Any]) -> str:
        lines = ["结论", str(answer.get("conclusion", ""))]
        self._section(lines, "业务链路", answer.get("businessFlow", []), self._flow_text)
        self._section(lines, "技术链路", answer.get("technicalFlow", []), self._flow_text)
        self._section(lines, "确定事实", answer.get("facts", []), self._fact_text)
        self._section(lines, "推断", answer.get("inferences", []), self._inference_text)
        self._section(lines, "未确认", answer.get("unknowns", []), str)
        self._section(lines, "冲突", answer.get("conflicts", []), self._conflict_text)
        self._section(lines, "建议追问", answer.get("suggestedFollowUps", []), str)
        return "\n\n".join(lines)

    @staticmethod
    def _section(lines: list[str], title: str, values: Iterable[Any], formatter: Any) -> None:
        values = list(values)
        if values:
            lines.extend((title, "\n".join(f"- {formatter(value)}" for value in values)))

    @staticmethod
    def _fact_text(value: Mapping[str, Any]) -> str:
        ids = ", ".join(value.get("evidenceIds", []))
        return f"[{value.get('sourceType', 'UNKNOWN')}] {value.get('statement', '')} (Evidence: {ids})"

    @staticmethod
    def _inference_text(value: Mapping[str, Any]) -> str:
        return f"{value.get('statement', '')} (Evidence: {', '.join(value.get('evidenceIds', []))})"

    @staticmethod
    def _flow_text(value: Any) -> str:
        if isinstance(value, Mapping):
            return str(value.get("statement", value.get("step", value)))
        return str(value)

    @staticmethod
    def _conflict_text(value: Mapping[str, Any]) -> str:
        sources = "；".join(f"{name.upper()}: {value.get(name)}" for name in ("business", "requirement", "code") if value.get(name))
        return f"CONFLICT {value.get('reason', '')}" + (f"（{sources}）" if sources else "")


def build_answer(state: Any) -> dict[str, Any]:
    return AnswerBuilder().build(state)


def render_answer(answer: Mapping[str, Any]) -> str:
    return AnswerRenderer().render(answer)

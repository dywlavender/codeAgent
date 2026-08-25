from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping


CONFLICT_STATUS = "CONFLICT"
_SOURCE_NAMES = ("BUSINESS", "REQUIREMENT", "CODE")
_NEGATION = re.compile(r"(?:不得|禁止|不能|不应|无需|不需|不再|未|\bnot\b|\bnever\b)", re.I)
_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)

# These are deliberately small, domain-independent strategy pairs.  More precise
# conflict detection should use ``conflictKey``/``value`` supplied by the
# evaluator instead of growing a business-specific synonym dictionary here.
_STRATEGIES = {
    "SNAPSHOT": ("申请阶段", "沿用", "已确认", "原值", "snapshot", "stored"),
    "REALTIME": ("实时查询", "重新查询", "即时查询", "real-time", "realtime", "live query"),
    "GENERATE": ("生成", "重新生成", "generate", "create"),
    "VALIDATE": ("校验", "验证", "validate", "check"),
}
_EXCLUSIVE = ({"SNAPSHOT", "REALTIME"},)


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


def _ids(value: Any) -> list[str]:
    raw = _get(value, "evidenceIds", "evidence_ids", "evidenceId", "evidence_id", default=[]) or []
    if isinstance(raw, str):
        raw = [raw]
    return list(dict.fromkeys(str(item) for item in raw if item))


def _source(value: Any) -> str:
    return str(_get(value, "sourceType", "source_type", "source", default="UNKNOWN")).upper()


def _statement(value: Any) -> str:
    return str(_get(value, "statement", "fact", "content", "claim", default="")).strip()


def _strategy(statement: str) -> str | None:
    lowered = statement.lower()
    for strategy, terms in _STRATEGIES.items():
        if any(term.lower() in lowered for term in terms):
            return strategy
    return None


def _polarity(value: Any, statement: str) -> bool:
    explicit = _get(value, "polarity", default=None)
    if explicit is not None:
        if isinstance(explicit, str):
            return explicit.upper() not in {"NEGATIVE", "FALSE", "NO", "0"}
        return bool(explicit)
    return not bool(_NEGATION.search(statement))


def _claim_key(value: Any, statement: str) -> str:
    explicit = _get(value, "conflictKey", "conflict_key", "claimKey", "claim_key", default="")
    if explicit:
        return str(explicit).strip().lower()
    subject = str(_get(value, "subject", default="")).strip()
    predicate = str(_get(value, "predicate", "relation", default="")).strip()
    field = str(_get(value, "field", "fieldName", "field_name", default="")).strip()
    if subject or predicate or field:
        return "|".join(part.lower() for part in (subject, field, predicate) if part)
    # Text fallback is intentionally conservative: it only groups claims that
    # share a concrete identifier, avoiding generic verbs such as "validate".
    identifiers = re.findall(r"[A-Za-z][A-Za-z0-9_.]{2,}", statement)
    return identifiers[0].lower() if identifiers else ""


def _explicit_value(value: Any) -> tuple[bool, str]:
    raw = _get(value, "value", "object", "target", "expected", default=None)
    return (raw is not None, "" if raw is None else str(raw).strip().lower())


def _contradiction(left: Any, right: Any) -> str | None:
    left_text, right_text = _statement(left), _statement(right)
    key = _claim_key(left, left_text)
    if not key or key != _claim_key(right, right_text):
        return None

    left_has_value, left_value = _explicit_value(left)
    right_has_value, right_value = _explicit_value(right)
    if left_has_value and right_has_value and left_value != right_value:
        return "同一业务命题的结构化值不一致"
    if _polarity(left, left_text) != _polarity(right, right_text):
        return "同一业务命题的肯定与否定规则不一致"

    left_strategy, right_strategy = _strategy(left_text), _strategy(right_text)
    if left_strategy and right_strategy and left_strategy != right_strategy:
        if any({left_strategy, right_strategy} <= pair for pair in _EXCLUSIVE):
            return "同一数据的沿用策略与实时获取策略不一致"
    return None


class ConflictDetector:
    """Find contradictions among evidence-backed structured claims.

    Candidates and claims without Evidence IDs are ignored.  The detector does
    not decide which source is "right"; it reports all participating sources.
    """

    def detect(self, facts: Iterable[Any]) -> list[dict[str, Any]]:
        usable = [
            fact for fact in facts
            if _statement(fact)
            and _ids(fact)
            and not _get(fact, "candidate", "isCandidate", default=False)
            and str(_get(fact, "status", default="")).upper() not in {"SUGGESTED", "REJECTED", "STALE"}
        ]
        groups: dict[str, list[Any]] = defaultdict(list)
        for fact in usable:
            key = _claim_key(fact, _statement(fact))
            if key:
                groups[key].append(fact)

        detected: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for key, claims in groups.items():
            reasons: list[str] = []
            conflicting: list[Any] = []
            for index, left in enumerate(claims):
                for right in claims[index + 1 :]:
                    if _source(left) == _source(right):
                        continue
                    reason = _contradiction(left, right)
                    if reason:
                        reasons.append(reason)
                        conflicting.extend((left, right))
            if not conflicting:
                continue
            unique_claims = list({id(item): item for item in conflicting}.values())
            evidence_ids = list(dict.fromkeys(eid for item in unique_claims for eid in _ids(item)))
            identity = (key, tuple(sorted(evidence_ids)))
            if identity in seen:
                continue
            seen.add(identity)
            by_source = {
                source.lower(): " / ".join(dict.fromkeys(_statement(item) for item in unique_claims if _source(item) == source))
                for source in _SOURCE_NAMES
            }
            detected.append({
                "status": CONFLICT_STATUS,
                "conflictKey": key,
                **by_source,
                "reason": "；".join(dict.fromkeys(reasons)),
                "evidenceIds": evidence_ids,
            })
        return detected


def detect_conflicts(facts: Iterable[Any]) -> list[dict[str, Any]]:
    return ConflictDetector().detect(facts)

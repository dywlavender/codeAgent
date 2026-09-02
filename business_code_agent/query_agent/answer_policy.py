"""Deterministic policy for closing a query after evidence evaluation."""

from __future__ import annotations

from .models import AnswerDecision, AnswerType, EvidenceStatus


NO_MODEL = "NO_MODEL"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
NO_VERIFIED_FACTS = "NO_VERIFIED_FACTS"


class AnswerPolicy:
    """Decide the answer type and whether the optional composer may run.

    Evidence status describes what the investigation proved.  Answer type
    describes how that result should be presented.  Keeping this decision in
    one small deterministic component prevents normal evidence gaps from being
    mistaken for model or service failures.
    """

    def decide(
        self,
        *,
        evidence_status: EvidenceStatus,
        facts: list[dict],
        conflicts: list[dict],
        unknowns: list[str],
        model_available: bool,
    ) -> AnswerDecision:
        del unknowns  # Reserved for future policy refinements; facts are authoritative here.
        status = evidence_status if isinstance(evidence_status, EvidenceStatus) else EvidenceStatus(evidence_status)
        has_facts = bool(facts)
        has_conflicts = bool(conflicts)

        # Treat a conflict as terminal even if an upstream component supplied
        # an inconsistent SUFFICIENT status.
        if status is EvidenceStatus.CONFLICT or has_conflicts:
            return AnswerDecision(AnswerType.CONFLICT, False, EVIDENCE_CONFLICT)

        if status is EvidenceStatus.SUFFICIENT and has_facts:
            return AnswerDecision(
                AnswerType.FULL,
                bool(model_available),
                "" if model_available else NO_MODEL,
            )

        if status is EvidenceStatus.INSUFFICIENT and has_facts:
            return AnswerDecision(AnswerType.PARTIAL, False, INSUFFICIENT_EVIDENCE)

        # This covers both INSUFFICIENT-without-facts and the defensive case of
        # SUFFICIENT-without-verified-facts.  Neither can support a model claim.
        return AnswerDecision(AnswerType.UNKNOWN, False, NO_VERIFIED_FACTS)

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .analysis_models import ImpactCandidate, UpdateAnalysis, UpdateSource


class UpdateAnalyzer(Protocol):
    """Semantic analysis port. Implementations may use a model or deterministic rules."""

    def analyze(self, source: UpdateSource, candidates: Sequence[ImpactCandidate]) -> UpdateAnalysis | Mapping[str, Any]: ...


class KnowledgeGovernanceStore(Protocol):
    """Narrow persistence boundary owned by the deterministic governance state machine."""

    def create_proposal(
        self, title: str, trigger_type: str, trigger_id: str, proposed_snapshot: Mapping[str, Any],
        created_by: str, target_function_id: str | None = None, action: str = "CREATE",
        summary: str = "", base_version_id: str | None = None,
    ) -> dict: ...

    def add_proposal_item(
        self, proposal_id: str, item_type: str, target_type: str, before: Any = None,
        after: Any = None, target_id: str | None = None, rationale: str = "",
        confidence: float = 0.0, evidence_ids: Sequence[str] = (),
    ) -> dict: ...

    def submit_proposal(self, proposal_id: str) -> dict: ...
    def review_proposal(self, proposal_id: str, decision: str, reviewer: str, comment: str = "") -> dict: ...
    def publish_proposal(self, proposal_id: str, published_by: str) -> dict: ...
    def get_proposal(self, proposal_id: str) -> dict: ...
    def get_function(self, function_id: str, version: int | None = None) -> dict: ...
    def list_functions(self, status: str = "PUBLISHED", limit: int = 50) -> list[dict]: ...


class CodeFactMaintainer(Protocol):
    """Code facts are refreshed automatically; this port must not mutate business semantics."""

    def refresh(self, repository_id: str, root_path: str) -> Mapping[str, Any]: ...


from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .analysis_models import (
    ImpactCandidate,
    ProposalAction,
    ProposedItem,
    UpdateAnalysis,
    UpdateSource,
    UpdateSourceType,
    snapshot_evidence,
)
from .ports import CodeFactMaintainer, KnowledgeGovernanceStore, UpdateAnalyzer


class KnowledgeUpdateAgent:
    """Domain orchestrator for knowledge maintenance.

    Semantic analysis can be delegated to a model, but proposal state transitions and
    publication are deliberately executed only through the governance repository.
    """

    def __init__(
        self,
        store: KnowledgeGovernanceStore,
        *,
        analyzer: UpdateAnalyzer | None = None,
        code_facts: CodeFactMaintainer | None = None,
        candidate_limit: int = 8,
    ):
        self.store = store
        self.analyzer = analyzer
        self.code_facts = code_facts
        self.candidate_limit = candidate_limit

    def propose(self, source: UpdateSource, *, created_by: str = "knowledge-update-agent") -> dict:
        candidates = self.identify_affected_functions(source)
        analysis_mode = "MODEL" if self.analyzer else "FALLBACK"
        try:
            raw = self.analyzer.analyze(source, candidates) if self.analyzer else self._fallback_analysis(source, candidates)
        except Exception as exc:
            from .langchain_adapter import ModelInvocationError
            if not isinstance(exc, ModelInvocationError):
                raise
            raw = self._fallback_analysis(source, candidates)
            analysis_mode = "FALLBACK"
        allowed_evidence = set(source.evidence_ids) | set().union(
            *(snapshot_evidence(item.current) for item in candidates), set()
        )
        analysis = raw if isinstance(raw, UpdateAnalysis) else UpdateAnalysis.from_mapping(
            raw,
            allowed_evidence=allowed_evidence,
            allowed_targets={item.function_id for item in candidates},
        )
        try:
            self._validate_analysis(analysis, source, candidates, validate_bindings=analysis_mode == "MODEL")
        except ValueError:
            if analysis_mode != "MODEL":
                raise
            analysis = self._fallback_analysis(source, candidates)
            analysis_mode = "FALLBACK"
            self._validate_analysis(analysis, source, candidates)
        proposal = self.store.create_proposal(
            analysis.title,
            source.source_type.value,
            source.source_id,
            analysis.proposed_snapshot,
            created_by,
            target_function_id=analysis.target_function_id,
            action=analysis.action.value,
            summary=analysis.summary,
            base_version_id=analysis.base_version_id,
        )
        proposal_id = _proposal_record(proposal)["id"]
        for item in analysis.items:
            self.store.add_proposal_item(
                proposal_id,
                item.item_type,
                item.target_type,
                before=item.before,
                after=item.after,
                target_id=item.target_id,
                rationale=item.rationale,
                confidence=item.confidence,
                evidence_ids=item.evidence_ids,
            )
        submitted = self.store.submit_proposal(proposal_id)
        return {
            "proposal": _proposal_record(submitted),
            "proposalItems": submitted.get("items", []) if isinstance(submitted, Mapping) else [],
            "reviews": submitted.get("reviews", []) if isinstance(submitted, Mapping) else [],
            "affectedFunctions": [item.to_dict() for item in candidates],
            "conflicts": list(analysis.conflicts),
            "unknowns": list(analysis.unknowns),
            "analysisMode": analysis_mode,
        }

    def from_code_changes(self, change_id: str, content: str, *, evidence_ids: Sequence[str] = (), metadata=None) -> dict:
        return self.propose(UpdateSource(UpdateSourceType.CODE_CHANGE, change_id, content, tuple(evidence_ids), metadata or {}))

    def from_requirement(self, requirement_id: str, content: str, *, evidence_ids: Sequence[str] = (), metadata=None) -> dict:
        return self.propose(UpdateSource(UpdateSourceType.REQUIREMENT, requirement_id, content, tuple(evidence_ids), metadata or {}))

    def from_document(self, document_id: str, content: str, *, evidence_ids: Sequence[str] = (), metadata=None) -> dict:
        return self.propose(UpdateSource(UpdateSourceType.DOCUMENT, document_id, content, tuple(evidence_ids), metadata or {}))

    def from_manual(self, instruction_id: str, content: str, *, evidence_ids: Sequence[str] = (), metadata=None) -> dict:
        return self.propose(UpdateSource(UpdateSourceType.MANUAL, instruction_id, content, tuple(evidence_ids), metadata or {}))

    def from_feedback(self, feedback_id: str, content: str, *, evidence_ids: Sequence[str] = (), metadata=None) -> dict:
        return self.propose(UpdateSource(UpdateSourceType.USER_FEEDBACK, feedback_id, content, tuple(evidence_ids), metadata or {}))

    def refresh_code_facts(self, repository_id: str, root_path: str) -> Mapping[str, Any]:
        if self.code_facts is None:
            raise RuntimeError("code fact maintainer is not configured")
        # The maintainer owns indexing and evidence invalidation only. Business changes
        # still enter the proposal workflow through from_code_changes().
        return self.code_facts.refresh(repository_id, root_path)

    def review(self, proposal_id: str, decision: str, *, reviewer: str, comment: str = "") -> dict:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        normalized = decision.upper()
        if normalized not in {"ACCEPT", "APPROVE", "REJECT", "DEFER", "REQUEST_CHANGES"}:
            raise ValueError(f"unsupported review decision: {decision}")
        return self.store.review_proposal(proposal_id, normalized, reviewer, comment)

    def publish(self, proposal_id: str, *, published_by: str) -> dict:
        if not published_by.strip():
            raise ValueError("published_by is required")
        # Repository state transition is authoritative and rejects non-approved work.
        return self.store.publish_proposal(proposal_id, published_by)

    def identify_affected_functions(self, source: UpdateSource) -> list[ImpactCandidate]:
        explicit = str(source.metadata.get("target_function_id", "")).strip()
        functions = self.store.list_functions(status="PUBLISHED", limit=200)
        scored: list[tuple[int, ImpactCandidate]] = []
        terms = _terms(source.content + " " + json.dumps(dict(source.metadata), ensure_ascii=False))
        for value in functions:
            function = value.get("function", value)
            function_id = str(function.get("id", ""))
            if not function_id:
                continue
            detail = self.store.get_function(function_id)
            function = detail.get("function", function)
            current = dict(detail.get("snapshot", detail.get("current", value)))
            version = detail.get("version", {})
            if version.get("id"):
                current["version_id"] = version["id"]
            text = json.dumps(current, ensure_ascii=False, default=str)
            overlap = sorted(terms & _terms(text), key=len, reverse=True)
            score = len(overlap) + (1000 if explicit == function_id else 0)
            if score <= 0:
                continue
            scored.append((score, ImpactCandidate(
                function_id=function_id,
                name=str(function.get("name") or current.get("name") or function_id),
                summary=str(function.get("summary") or current.get("summary") or ""),
                domain=str(function.get("domain") or current.get("domain") or ""),
                current=current,
                match_reasons=tuple(overlap[:8]) if overlap else ("explicit target",),
            )))
        scored.sort(key=lambda item: (-item[0], item[1].function_id))
        return [item for _, item in scored[:self.candidate_limit]]

    def _validate_analysis(
        self, analysis: UpdateAnalysis, source: UpdateSource,
        candidates: Sequence[ImpactCandidate], *, validate_bindings: bool = False,
    ) -> None:
        allowed_targets = {item.function_id for item in candidates}
        if analysis.target_function_id and analysis.target_function_id not in allowed_targets:
            raise ValueError("analysis target is outside affected-function candidates")
        if analysis.action != ProposalAction.CREATE and not analysis.target_function_id:
            raise ValueError("non-create proposal requires an affected function")
        allowed_evidence = set(source.evidence_ids) | set().union(
            *(snapshot_evidence(candidate.current) for candidate in candidates), set()
        )
        for item in analysis.items:
            if set(item.evidence_ids) - allowed_evidence:
                raise ValueError("proposal cites evidence outside the source")
        if validate_bindings:
            claims = source.metadata.get("_evidence_claims", source.metadata.get("evidence_claims", {}))
            source_ids = set(source.evidence_ids)
            current = next(
                (candidate.current for candidate in candidates if candidate.function_id == analysis.target_function_id),
                {},
            )
            for item in analysis.items:
                cited = set(item.evidence_ids) & source_ids
                if not cited:
                    raise ValueError("model proposal item requires source evidence")
                self._validate_claim_binding(
                    {"before": item.before, "after": item.after, "rationale": item.rationale},
                    cited, claims,
                )
            self._validate_snapshot_bindings(
                analysis.proposed_snapshot, current, source_ids, claims,
                proposal_items=analysis.items,
                require_root=analysis.action == ProposalAction.CREATE,
            )

    @staticmethod
    def _validate_claim_binding(value, evidence_ids, claims) -> None:
        if not isinstance(claims, Mapping) or not claims:
            return
        terms = _terms(json.dumps(value, ensure_ascii=False, default=str))
        for evidence_id in evidence_ids:
            claim_terms = _terms(str(claims.get(evidence_id, "")))
            if not claim_terms or not terms.intersection(claim_terms):
                raise ValueError("proposal evidence is not bound to its source claim")

    def _validate_snapshot_bindings(
        self, snapshot, current, source_ids, claims, *, proposal_items, require_root,
    ) -> None:
        root_changed = require_root or any(
            snapshot.get(key) != current.get(key) for key in ("name", "domain", "summary")
        )
        if root_changed:
            cited = set(snapshot.get("evidence_ids", [])) & source_ids
            if not cited:
                raise ValueError("changed function summary requires source evidence")
            self._validate_claim_binding(
                {key: snapshot.get(key) for key in ("name", "domain", "summary")}, cited, claims,
            )
        for collection in ("scenarios", "rules", "entries", "data_impacts"):
            previous = {
                str(item.get("id")): item for item in current.get(collection, []) if item.get("id")
            }
            for node in snapshot.get(collection, []):
                baseline = previous.get(str(node.get("id")))
                comparable = {key: value for key, value in node.items() if key != "evidence_ids"}
                old = (
                    {key: value for key, value in baseline.items() if key != "evidence_ids"}
                    if baseline else None
                )
                if old == comparable:
                    continue
                cited = set(node.get("evidence_ids", [])) & source_ids
                if not cited:
                    raise ValueError(f"changed {collection} item requires source evidence")
                self._validate_claim_binding(comparable, cited, claims)
            new_ids = {str(item.get("id")) for item in snapshot.get(collection, []) if item.get("id")}
            for removed_id in set(previous) - new_ids:
                removed = previous[removed_id]
                change = next(
                    (
                        item for item in proposal_items
                        if str(item.target_id or "") == removed_id
                        and any(term in item.item_type for term in ("DELETE", "REMOVE", "RETIRE"))
                    ),
                    None,
                )
                if change is None:
                    raise ValueError(f"removed {collection} item requires an explicit proposal item")
                cited = set(change.evidence_ids) & source_ids
                if not cited:
                    raise ValueError(f"removed {collection} item requires source evidence")
                self._validate_claim_binding(
                    {"before": change.before or removed, "rationale": change.rationale}, cited, claims,
                )

    def _fallback_analysis(self, source: UpdateSource, candidates: Sequence[ImpactCandidate]) -> UpdateAnalysis:
        target = candidates[0] if candidates else None
        name = str(source.metadata.get("function_name") or (target.name if target else "")).strip()
        if not name:
            name = str(source.metadata.get("title") or source.content.splitlines()[0]).strip()[:80]
        current = dict(target.current) if target else {}
        snapshot = {
            "name": name,
            "domain": current.get("domain", str(source.metadata.get("domain", ""))),
            "summary": current.get("summary", source.content.strip()[:240]),
            "scenarios": current.get("scenarios", []),
            "rules": current.get("rules", []),
            "entries": current.get("entries", []),
            "data_impacts": current.get("data_impacts", current.get("dataImpacts", [])),
            "evidence_ids": list(source.evidence_ids),
        }
        action = ProposalAction.UPDATE if target else ProposalAction.CREATE
        item = ProposedItem(
            item_type="SOURCE_IMPACT",
            target_type="FUNCTION",
            target_id=target.function_id if target else None,
            before=current.get("summary") if target else None,
            after=source.content.strip()[:500],
            rationale="新来源可能影响业务功能，未启用语义分析器，需管理员判断具体变更",
            confidence=0.0,
            evidence_ids=tuple(source.evidence_ids),
        )
        return UpdateAnalysis(
            title=f"复核业务知识：{name}", action=action,
            target_function_id=target.function_id if target else None,
            base_version_id=current.get("version_id") or current.get("versionId"),
            summary="检测到新的知识来源，已生成保守的人工复核提案",
            proposed_snapshot=snapshot, items=(item,),
            unknowns=("未配置语义分析器，业务含义尚未自动提取",),
        )


def _terms(value: str) -> set[str]:
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", value)
    }
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def _proposal_record(value: Mapping[str, Any]) -> Mapping[str, Any]:
    proposal = value.get("proposal", value)
    if not isinstance(proposal, Mapping) or not proposal.get("id"):
        raise ValueError("governance repository returned an invalid proposal")
    return proposal

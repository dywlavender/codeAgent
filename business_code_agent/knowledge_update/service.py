from __future__ import annotations

import logging

import json
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..util import digest
from .agent import KnowledgeUpdateAgent
from .analysis_models import UpdateSource, UpdateSourceType
from .langchain_adapter import LangChainUpdateAnalyzer, model_config_from_environment
from .repository import KnowledgeGovernanceRepository


logger = logging.getLogger(__name__)

class KnowledgeAdminService:
    """Application service shared by the governance API and future CLI adapters.

    Reads never require a configured model.  A model is created lazily only when
    an administrator asks the update agent to generate a semantic proposal.
    """

    def __init__(
        self,
        db,
        *,
        project_config: str | Path | None = None,
        analyzer=None,
        agent_factory=None,
    ):
        self.db = db
        self.repository = KnowledgeGovernanceRepository(db)
        self.project_config = Path(project_config).resolve() if project_config else None
        self._analyzer = analyzer
        self._agent_factory = agent_factory

    def list_pending(self, query: str = "", limit: int = 50) -> list[dict]:
        statuses = ("PENDING_REVIEW", "DEFERRED", "CHANGES_REQUESTED")
        values: list[dict] = []
        for status in statuses:
            values.extend(self.repository.list_proposals(status=status, limit=limit))
        return self._filter_proposals(values, query)[:limit]

    def list_proposals(self, query: str = "", limit: int = 50) -> list[dict]:
        return self._filter_proposals(self.repository.list_proposals(limit=limit), query)

    def list_functions(self, query: str = "", limit: int = 50) -> list[dict]:
        summaries = self.repository.list_functions(status="PUBLISHED", limit=limit)
        values = []
        for summary in summaries:
            detail = self.repository.get_function(summary["id"])
            snapshot = detail["snapshot"]
            evidence_ids = set(snapshot.get("evidence_ids", []))
            for collection in ("tags", "knowledge_tags", "knowledgeTags", "scenarios", "rules", "entries", "data_impacts"):
                for item in snapshot.get(collection, []):
                    if isinstance(item, dict):
                        evidence_ids.update(item.get("evidence_ids", item.get("evidenceIds", [])))
            values.append({
                **detail["function"],
                **snapshot,
                "version": detail["version"]["version"],
                "versionId": detail["version"]["id"],
                "evidenceCount": len(evidence_ids),
            })
        if not query.strip():
            return values
        needle = query.casefold()
        return [item for item in values if needle in json.dumps(item, ensure_ascii=False).casefold()]

    def get_proposal(self, proposal_id: str) -> dict:
        return self._proposal_view(self.repository.get_proposal(proposal_id))

    def generate(self, payload: Mapping[str, Any], *, created_by: str = "knowledge-update-agent") -> dict:
        logger.info("生成知识更新提案：sourceType=%s sourceId=%s createdBy=%s",
                    payload.get("sourceType"), payload.get("sourceId"), created_by)
        source_type = _source_type(payload.get("sourceType") or payload.get("source_type"))
        source_id = str(payload.get("sourceId") or payload.get("source_id") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not source_id:
            raise ValueError("sourceId is required")
        if not content:
            raise ValueError("content is required")

        evidence_ids = tuple(str(item) for item in (payload.get("evidenceIds") or payload.get("evidence_ids") or []))
        if evidence_ids:
            self._validate_evidence(evidence_ids)
        content, discovered_evidence, source_metadata = self._assemble_source_context(
            source_type, source_id, content
        )
        evidence_ids = tuple(dict.fromkeys([*evidence_ids, *discovered_evidence]))
        if not evidence_ids:
            evidence_ids = (self._create_source_evidence(source_type, source_id, content),)
        metadata = {**source_metadata, **dict(payload.get("metadata") or {})}
        metadata["_evidence_claims"] = self._evidence_claims(evidence_ids)
        target = str(payload.get("targetFunctionId") or payload.get("target_function_id") or "").strip()
        if target:
            resolved = self.db.execute(
                """SELECT id FROM business_function
                    WHERE status='PUBLISHED' AND (id=? OR name=?) ORDER BY id LIMIT 1""",
                (target, target),
            ).fetchone()
            if not resolved:
                raise ValueError("targetFunctionId does not match a published business function")
            metadata["target_function_id"] = resolved["id"]
        if payload.get("functionName") or payload.get("function_name"):
            metadata["function_name"] = payload.get("functionName") or payload.get("function_name")

        source = UpdateSource(source_type, source_id, content, evidence_ids, metadata)
        result = self._agent().propose(source, created_by=created_by)
        proposal = result.get("proposal", result)
        proposal_id = proposal.get("id") or proposal.get("proposal", {}).get("id")
        detail = self.get_proposal(proposal_id) if proposal_id else proposal
        return {**detail, "affectedFunctions": result.get("affectedFunctions", []),
                "conflicts": result.get("conflicts", []), "unknowns": result.get("unknowns", []),
                "analysisMode": result.get("analysisMode", "FALLBACK")}

    def generate_from_sources(self, payload: Mapping[str, Any], *, created_by: str = "knowledge-update-agent") -> dict:
        """Generate one reviewable knowledge proposal from the three user-facing sources.

        The UI should not make administrators understand source types or evidence
        IDs. This adapter assembles bounded code, requirement, and manual context,
        then reuses the existing proposal and publication state machine.
        """
        repository_ids = _string_list(payload.get("repositoryIds") or payload.get("repository_ids"))
        repository_id = str(payload.get("repositoryId") or payload.get("repository_id") or "").strip()
        if repository_id and repository_id not in repository_ids:
            repository_ids.insert(0, repository_id)
        requirement_ids = _string_list(payload.get("requirementIds") or payload.get("requirement_ids"))
        topic = str(payload.get("topic") or "").strip()
        business_note = str(payload.get("businessNote") or payload.get("business_note") or "").strip()
        if not repository_ids:
            repository_ids = [row["id"] for row in self.db.execute("SELECT id FROM repository ORDER BY indexed_at DESC LIMIT 1")]
        if not repository_ids and not requirement_ids and not business_note:
            raise ValueError("至少需要当前代码、需求依据或业务补充中的一项")

        parts: list[str] = []
        evidence_ids: list[str] = []
        metadata: dict[str, Any] = {
            "sourceResolution": "KNOWLEDGE_SOURCES",
            "repositories": repository_ids,
            "requirementIds": requirement_ids,
        }
        if topic:
            parts.append(f"本次知识主题：{topic}")
            metadata["topic"] = topic
        if business_note:
            parts.append("人工业务补充：\n" + business_note)

        for current_repository_id in repository_ids[:3]:
            code_content, code_evidence, code_metadata = self._repository_context(current_repository_id, topic or business_note)
            if code_content:
                parts.append(code_content)
                evidence_ids.extend(code_evidence)
                metadata.setdefault("repositoryContext", {})[current_repository_id] = code_metadata

        for requirement_id in requirement_ids[:5]:
            requirement_content, requirement_evidence, requirement_metadata = self._requirement_context(requirement_id, "")
            if requirement_content:
                parts.append(requirement_content)
                evidence_ids.extend(requirement_evidence)
                metadata.setdefault("requirementContext", {})[requirement_id] = requirement_metadata

        if not parts:
            raise ValueError("没有找到可用于生成知识的代码、需求或业务补充")
        source_id = f"KNOWLEDGE-{uuid.uuid4().hex[:16]}"
        if business_note:
            evidence_ids.append(self._create_source_evidence(UpdateSourceType.MANUAL, source_id, business_note))
        content = "\n\n".join(parts)
        if len(content) > 24000:
            content = content[:24000]
        return self.generate({
            "sourceType": "MANUAL",
            "sourceId": source_id,
            "content": content,
            "evidenceIds": list(dict.fromkeys(evidence_ids)),
            "metadata": metadata,
        }, created_by=created_by)

    def _repository_context(self, repository_id: str, hint: str = "") -> tuple[str, tuple[str, ...], dict]:
        repository = self.db.execute("SELECT id,indexed_at FROM repository WHERE id=?", (repository_id,)).fetchone()
        if not repository:
            return "", (), {"sourceResolution": "REPOSITORY_NOT_FOUND"}
        rows = self.db.execute(
            """SELECT cs.qualified_name,cs.kind,cf.fact_type,cf.subject,cf.target,cf.evidence_id
                 FROM code_file cfile
                 JOIN code_symbol cs ON cs.file_id=cfile.id
                 JOIN code_fact cf ON cf.symbol_id=cs.id
                WHERE cfile.repository_id=?
                ORDER BY cs.qualified_name,cf.fact_type LIMIT 80""",
            (repository_id,),
        ).fetchall()
        if not rows:
            return f"已索引代码仓库：{repository_id}（当前没有可用 Code Fact）", (), {"sourceResolution": "INDEXED_CODE_FACTS", "factCount": 0}
        lines = [f"已索引代码仓库：{repository_id}", "代码事实："]
        evidence_ids: list[str] = []
        for row in rows:
            line = f"- {row['qualified_name']} | {row['kind']} | {row['fact_type']} | {row['subject']} -> {row['target']} | Evidence: {row['evidence_id']}"
            if len("\n".join([*lines, line])) > 24000:
                break
            lines.append(line)
            evidence_ids.append(row["evidence_id"])
        return "\n".join(lines), tuple(dict.fromkeys(evidence_ids)), {
            "sourceResolution": "INDEXED_CODE_FACTS",
            "indexedAt": repository["indexed_at"],
            "factCount": len(evidence_ids),
            "hint": hint[:120],
        }

    def _assemble_source_context(
        self, source_type: UpdateSourceType, source_id: str, supplied_content: str
    ) -> tuple[str, tuple[str, ...], dict]:
        if source_type == UpdateSourceType.CODE_CHANGE:
            return self._code_change_context(source_id, supplied_content)
        if source_type == UpdateSourceType.REQUIREMENT:
            return self._requirement_context(source_id, supplied_content)
        return supplied_content, (), {}

    def _code_change_context(self, source_id: str, supplied_content: str) -> tuple[str, tuple[str, ...], dict]:
        repository = self.db.execute(
            "SELECT indexed_at FROM repository WHERE id=?", (source_id,)
        ).fetchone()
        if repository:
            rows = self.db.execute(
                """SELECT id,repository_id,file_path,change_type,indexed_at
                     FROM ingestion_change
                    WHERE repository_id=? AND indexed_at>=?
                    ORDER BY indexed_at,file_path LIMIT 20""",
                (source_id, repository["indexed_at"]),
            )
        else:
            rows = self.db.execute(
                """SELECT id,repository_id,file_path,change_type,indexed_at
                     FROM ingestion_change
                    WHERE id=? OR file_path=?
                    ORDER BY indexed_at DESC LIMIT 20""",
                (source_id, source_id),
            )
        changes = [dict(row) for row in rows]
        if not changes:
            return supplied_content, (), {"sourceResolution": "USER_SUPPLIED"}

        lines = [supplied_content, "已索引代码变化："]
        evidence_ids: list[str] = []
        for change in changes:
            lines.append(
                f"- {change['change_type']} {change['repository_id']}:{change['file_path']}"
            )
            facts = self.db.execute(
                """SELECT cs.qualified_name,cf.fact_type,cf.subject,cf.target,cf.evidence_id
                     FROM code_file cfile
                     JOIN code_symbol cs ON cs.file_id=cfile.id
                     JOIN code_fact cf ON cf.symbol_id=cs.id
                    WHERE cfile.repository_id=? AND cfile.path=?
                    ORDER BY cs.qualified_name,cf.fact_type LIMIT 40""",
                (change["repository_id"], change["file_path"]),
            ).fetchall()
            for fact in facts:
                line = (
                    f"  {fact['qualified_name']} | {fact['fact_type']} | "
                    f"{fact['subject']} -> {fact['target']} | Evidence: {fact['evidence_id']}"
                )
                if len("\n".join([*lines, line])) > 24000:
                    break
                lines.append(line)
                evidence_ids.append(fact["evidence_id"])
                if len(evidence_ids) >= 40:
                    break
            if len(evidence_ids) >= 40:
                break
        return "\n".join(lines), tuple(dict.fromkeys(evidence_ids)), {
            "sourceResolution": "INDEXED_CODE_CHANGE",
            "changeIds": [item["id"] for item in changes],
            "repositories": list(dict.fromkeys(item["repository_id"] for item in changes)),
        }

    def _requirement_context(self, source_id: str, supplied_content: str) -> tuple[str, tuple[str, ...], dict]:
        requirement = self.db.execute(
            "SELECT id,title,current_version FROM requirement WHERE id=?", (source_id,)
        ).fetchone()
        if not requirement:
            return supplied_content, (), {"sourceResolution": "USER_SUPPLIED"}
        version = self.db.execute(
            """SELECT id,version FROM requirement_version
                 WHERE requirement_id=? AND version=?""",
            (source_id, requirement["current_version"]),
        ).fetchone()
        if not version:
            return supplied_content, (), {"sourceResolution": "USER_SUPPLIED"}

        digest_row = self.db.execute(
            "SELECT digest_json FROM requirement_digest_v2 WHERE requirement_version_id=?",
            (version["id"],),
        ).fetchone()
        rules = [dict(row) for row in self.db.execute(
            """SELECT rr.id,rr.statement,rr.conditions_json,rr.result,rr.status,
                      group_concat(rc.evidence_id) evidence_ids
                 FROM requirement_rule rr
                 LEFT JOIN requirement_evidence re
                   ON re.fact_type='BUSINESS_RULE' AND re.fact_id=rr.id
                 LEFT JOIN requirement_chunk_v2 rc ON rc.id=re.chunk_id
                WHERE rr.requirement_version_id=?
                GROUP BY rr.id ORDER BY rr.id LIMIT 30""",
            (version["id"],),
        )]
        chunks = [dict(row) for row in self.db.execute(
            """SELECT id,evidence_id,section_path_json,content FROM requirement_chunk_v2
                 WHERE requirement_version_id=? ORDER BY sequence LIMIT 30""",
            (version["id"],),
        )]
        parts = [supplied_content, f"已导入需求：{requirement['title']}"]
        if digest_row:
            parts.append("需求摘要：" + digest_row["digest_json"])
        if rules:
            parts.append("业务规则：")
            parts.extend(
                f"- {item['statement']} | 条件 {item['conditions_json']} | 结果 {item['result']}"
                f" | Evidence: {item['evidence_ids'] or '未绑定'}"
                for item in rules
            )
        included_chunks = []
        for item in chunks:
            line = f"原文证据 [{item['evidence_id']}] {item['content']}"
            if len("\n".join([*parts, line])) > 24000:
                break
            parts.append(line)
            included_chunks.append(item)
        return "\n".join(parts), tuple(item["evidence_id"] for item in included_chunks), {
            "sourceResolution": "REQUIREMENT_DIGEST",
            "requirementVersionId": version["id"],
            "chunkIds": [item["id"] for item in included_chunks],
        }

    def _evidence_claims(self, evidence_ids: tuple[str, ...]) -> dict[str, str]:
        if not evidence_ids:
            return {}
        placeholders = ",".join("?" for _ in evidence_ids)
        return {
            row["id"]: row["excerpt"]
            for row in self.db.execute(
                f"SELECT id,excerpt FROM evidence WHERE id IN ({placeholders})", evidence_ids
            )
        }

    def review(
        self,
        proposal_id: str,
        action: str,
        *,
        reviewer: str = "admin",
        comment: str = "",
    ) -> dict:
        normalized = action.strip().upper()
        agent = KnowledgeUpdateAgent(self.repository)
        current = self.repository.get_proposal(proposal_id)["proposal"]
        if current["status"] in {"DEFERRED", "CHANGES_REQUESTED"}:
            self.repository.submit_proposal(proposal_id)
        if normalized in {"ACCEPT", "APPROVE"}:
            agent.review(proposal_id, "APPROVE", reviewer=reviewer, comment=comment)
            published = agent.publish(proposal_id, published_by=reviewer)
            logger.info("提案 %s 已接受并发布（reviewer=%s，function=%s）", proposal_id, reviewer, (published or {}).get("id") or (published or {}).get("functionId") or "-")
            return {"proposal": self.get_proposal(proposal_id), "publishedFunction": published}
        if normalized == "REJECT":
            reviewed = agent.review(proposal_id, "REJECT", reviewer=reviewer, comment=comment)
            logger.info("提案 %s 已驳回（reviewer=%s）", proposal_id, reviewer)
            return {"proposal": self._proposal_view(reviewed)}
        if normalized in {"DEFER", "REQUEST_CHANGES"}:
            decision = "DEFER" if _supports_defer(self.repository) else "REQUEST_CHANGES"
            reviewed = self.repository.review_proposal(proposal_id, decision, reviewer, comment)
            return {"proposal": self._proposal_view(reviewed)}
        raise ValueError(f"unsupported review action: {action}")

    def _agent(self) -> KnowledgeUpdateAgent:
        analyzer = self._analyzer
        if analyzer is None:
            model = self._model_config()
            if model and model.get("enabled", True):
                try:
                    analyzer = LangChainUpdateAnalyzer.from_config(model, agent_factory=self._agent_factory)
                except Exception:
                    analyzer = None
        return KnowledgeUpdateAgent(self.repository, analyzer=analyzer)

    def _model_config(self) -> dict | None:
        environment_config = model_config_from_environment()
        if environment_config is not None:
            return environment_config
        if not self.project_config or not self.project_config.is_file():
            return None
        payload = json.loads(self.project_config.read_text(encoding="utf-8"))
        value = payload.get("model")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("project model configuration must be an object")
        return value

    def _validate_evidence(self, evidence_ids: tuple[str, ...]) -> None:
        placeholders = ",".join("?" for _ in evidence_ids)
        found = {row[0] for row in self.db.execute(
            f"SELECT id FROM evidence WHERE id IN ({placeholders})", evidence_ids
        )}
        missing = set(evidence_ids) - found
        if missing:
            raise ValueError("one or more evidence references do not exist")

    def _create_source_evidence(self, source_type: UpdateSourceType, source_id: str, content: str) -> str:
        evidence_id = f"EV-KU-{uuid.uuid4().hex}"
        stored_type = {
            UpdateSourceType.REQUIREMENT: "REQUIREMENT",
            UpdateSourceType.CODE_CHANGE: "CODE_CHANGE",
            UpdateSourceType.DOCUMENT: "DOCUMENT",
            UpdateSourceType.USER_FEEDBACK: "USER_FEEDBACK",
            UpdateSourceType.MANUAL: "MANUAL",
        }[source_type]
        self.db.execute(
            """INSERT INTO evidence
               (id,source_type,source_id,source_version,locator,line_start,line_end,chunk_id,content_hash,excerpt)
               VALUES (?, ?, ?, '1', ?, NULL, NULL, NULL, ?, ?)""",
            (evidence_id, stored_type, source_id, f"knowledge-update:{source_id}", digest(content), content),
        )
        self.db.execute(
            "INSERT INTO evidence_lifecycle VALUES (?, 'ACTIVE', NULL, NULL, NULL)",
            (evidence_id,),
        )
        self.db.commit()
        return evidence_id

    def _filter_proposals(self, values: list[dict], query: str) -> list[dict]:
        views = [self._proposal_view({"proposal": item, "items": [], "reviews": []}) for item in values]
        if not query.strip():
            return views
        needle = query.casefold()
        return [item for item in views if needle in json.dumps(item, ensure_ascii=False).casefold()]

    def _proposal_view(self, detail: dict) -> dict:
        if "proposal" not in detail:
            return detail
        proposal = dict(detail["proposal"])
        items = detail.get("items") or detail.get("proposalItems") or []
        snapshot = proposal.get("proposed_snapshot") or proposal.get("proposedSnapshot") or {}
        before = None
        if proposal.get("base_version_id"):
            try:
                current = self.repository.get_function(proposal["target_function_id"])
                before = current["snapshot"]
            except KeyError:
                before = None
        snapshot_evidence = list(snapshot.get("evidence_ids", []))
        for collection in ("tags", "knowledge_tags", "knowledgeTags", "scenarios", "rules", "entries", "data_impacts"):
            for item in snapshot.get(collection, []):
                if isinstance(item, dict):
                    snapshot_evidence.extend(item.get("evidence_ids", item.get("evidenceIds", [])))
        evidence_ids = list(dict.fromkeys([
            *(evidence_id for item in items for evidence_id in item.get("evidence_ids", [])),
            *snapshot_evidence,
        ]))
        evidence = []
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence = [dict(row) for row in self.db.execute(
                f"""SELECT e.*,coalesce(el.status,'ACTIVE') lifecycle_status
                      FROM evidence e LEFT JOIN evidence_lifecycle el ON el.evidence_id=e.id
                     WHERE e.id IN ({placeholders}) ORDER BY e.source_type,e.locator""",
                evidence_ids,
            )]
        return {
            **proposal,
            "before": before,
            "after": snapshot,
            "items": items,
            "reviews": detail.get("reviews", []),
            "evidence": evidence,
            "affectedFunctions": [proposal["target_function_id"]] if proposal.get("target_function_id") else [],
        }


def _source_type(value: Any) -> UpdateSourceType:
    text = str(value or "MANUAL").strip().upper()
    aliases = {
        "ADMIN_NOTE": "MANUAL",
        "CODE": "CODE_CHANGE",
        "GIT_CHANGE": "CODE_CHANGE",
        "REQUIREMENT_CHANGE": "REQUIREMENT",
        "DOC": "DOCUMENT",
        "FEEDBACK": "USER_FEEDBACK",
    }
    try:
        return UpdateSourceType(aliases.get(text, text))
    except ValueError as exc:
        raise ValueError(f"unsupported sourceType: {value}") from exc


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _supports_defer(repository: KnowledgeGovernanceRepository) -> bool:
    # Kept local so databases created before DEFERRED support remain operable.
    try:
        from .models import ProposalStatus
        return "DEFERRED" in ProposalStatus.__members__
    except ImportError:
        return False

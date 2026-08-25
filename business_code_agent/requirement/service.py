from __future__ import annotations

from datetime import datetime, timezone

from ..util import dumps, stable_id
from .chunker import RequirementChunker
from .diff import diff_rules
from .extractor import RequirementDigestExtractor
from .ingestion import RequirementDocumentParser
from .matcher import RequirementRelationBuilder
from .repository import RequirementRepository


class RequirementService:
    def __init__(self, db, *, parser=None, extractor=None):
        self.db = db
        self.parser = parser or RequirementDocumentParser()
        self.extractor = extractor or RequirementDigestExtractor()
        self.chunker = RequirementChunker()
        self.repository = RequirementRepository(db)
        self.relations = RequirementRelationBuilder(db)

    def import_document(self, path: str, *, requirement_id: str | None = None, title: str | None = None) -> dict:
        parsed = self.parser.parse(path)
        parsed["source_path"] = str(__import__("pathlib").Path(path).resolve())
        parsed["title"] = title or parsed["title"]
        requirement_id = requirement_id or self.repository.next_id()
        version = self.repository.next_version(requirement_id)
        chunks = self.chunker.chunk(requirement_id, version, parsed["sections"], parsed["original"])
        digest_value = self.extractor.extract(requirement_id, parsed["title"], chunks)
        previous = self.repository.rules(requirement_id) if version > 1 else []
        version_id = self.repository.save(requirement_id, parsed, chunks, digest_value, version=version)
        actual_version = int(version_id.rsplit("V", 1)[-1])
        if actual_version != version:
            self.db.rollback()
            return {"id": requirement_id, "version": actual_version, "versionId": version_id, "unchanged": True}
        if version > 1:
            self._record_change(requirement_id, version_id, version, previous, digest_value)
        self.db.commit()
        return {"id": requirement_id, "version": version, "versionId": version_id, "digest": digest_value.to_dict(), "chunks": len(chunks), "unchanged": False}

    def enrich(self, requirement_id: str, *, version: int | None = None, limit: int = 30) -> dict:
        detail = self.repository.get(requirement_id, version)
        digest_value = self.extractor._validated(requirement_id, detail["digest"]["title"], self._chunks(detail["version"]["id"]), detail["digest"])
        relations = self.relations.enrich(requirement_id, detail["version"]["id"], digest_value, limit)
        return {"requirementId": requirement_id, "versionId": detail["version"]["id"], "relations": relations}

    def get(self, requirement_id: str, version: int | None = None) -> dict:
        return self.repository.get(requirement_id, version)

    def get_digest(self, requirement_id: str, version: int | None = None) -> dict:
        return self.repository.get_digest(requirement_id, version)

    def get_rule(self, requirement_id: str, rule_id: str | None = None, version: int | None = None):
        rules = self.repository.rules(requirement_id, version)
        if rule_id is None:
            return rules
        return next((item for item in rules if item["id"] == rule_id), None) or (_ for _ in ()).throw(KeyError(rule_id))

    def read_chunk(self, requirement_id: str, chunk_id: str) -> dict:
        return self.repository.read_chunk(requirement_id, chunk_id)

    def search(self, query: str):
        return self.repository.search(query)

    def search_chunks(self, requirement_id: str, query: str):
        return self.repository.search_chunks(requirement_id, query)

    def code_relations(self, requirement_id: str, version: int | None = None) -> list[dict]:
        detail = self.repository.get(requirement_id, version)
        return [item for item in detail["relations"] if item["target_type"] in {"METHOD", "API", "FIELD", "TABLE", "COLUMN"}]

    def history(self, requirement_id: str):
        return self.repository.history(requirement_id)

    def changes(self, requirement_id: str):
        return self.repository.changes(requirement_id)

    def find_by(self, kind: str, value: str) -> list[dict]:
        key = {"object": "business_objects", "process": "affected_processes", "field": "fields"}[kind]
        return [item for item in self.repository.search(value) if value in self.repository.get_digest(item["id"]).get(key, [])]

    def _chunks(self, version_id: str):
        rows = self.db.execute("SELECT * FROM requirement_chunk_v2 WHERE requirement_version_id=? ORDER BY sequence", (version_id,))
        from .models import RequirementChunk
        import json
        return [RequirementChunk(row["id"], json.loads(row["section_path_json"]), row["sequence"], row["content"], row["start_offset"], row["end_offset"], row["paragraph_start"], row["paragraph_end"], row["page"]) for row in rows]

    def _record_change(self, requirement_id, version_id, version, previous, digest_value):
        current = [rule.statement for rule in digest_value.business_rules]
        old = [row["statement"] for row in previous]
        change = diff_rules(old, current)
        previous_id = f"{requirement_id}-V{version - 1}"
        affected_knowledge = [row["target_id"] for row in self.db.execute("SELECT target_id FROM requirement_relation WHERE requirement_version_id=? AND target_type='BUSINESS_FUNCTION'", (previous_id,))]
        affected_code = [row["target_id"] for row in self.db.execute("SELECT target_id FROM requirement_relation WHERE requirement_version_id=? AND target_type IN ('METHOD','API','FIELD','TABLE','COLUMN')", (previous_id,))]
        now = datetime.now(timezone.utc).isoformat()
        change_id = stable_id("RDIFF", requirement_id, previous_id, version_id)
        self.db.execute(
            "INSERT INTO requirement_version_change VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (change_id, requirement_id, previous_id, version_id, dumps(change["addedRules"]), dumps(change["removedRules"]), dumps(change["changedRules"]), dumps(affected_knowledge), dumps(affected_code), now),
        )
        self._propose_function_revalidation(requirement_id, previous_id, version_id, version, change)

    def _propose_function_revalidation(self, requirement_id, previous_id, version_id, version, change):
        functions = self.db.execute(
            """SELECT DISTINCT bf.id
                 FROM business_function bf
                 JOIN business_function_version bfv ON bfv.id=bf.current_version_id
                 JOIN function_item_evidence fie ON fie.function_version_id=bfv.id
                 JOIN evidence e ON e.id=fie.evidence_id
                WHERE bf.status='PUBLISHED' AND e.source_type='REQUIREMENT'
                  AND e.source_id=? AND e.source_version=?""",
            (requirement_id, previous_id),
        ).fetchall()
        if not functions:
            return
        new_evidence = [row[0] for row in self.db.execute(
            """SELECT evidence_id FROM requirement_chunk_v2
                WHERE requirement_version_id=? ORDER BY sequence LIMIT 30""",
            (version_id,),
        )]
        from ..knowledge_update.repository import KnowledgeGovernanceRepository, retain_active_evidence

        repository = KnowledgeGovernanceRepository(self.db)
        for row in functions:
            function_id = row["id"]
            existing = self.db.execute(
                """SELECT id FROM knowledge_update_proposal
                    WHERE target_function_id=? AND trigger_type='REQUIREMENT_VERSION_CHANGED'
                      AND trigger_id=? AND status IN ('DRAFT','PENDING_REVIEW','DEFERRED','CHANGES_REQUESTED')
                    LIMIT 1""",
                (function_id, version_id),
            ).fetchone()
            if existing:
                continue
            current = repository.get_function(function_id)
            snapshot = retain_active_evidence(self.db, current["snapshot"], new_evidence)
            proposal = repository.create_proposal(
                f"重新验证需求关联：{current['function']['name']}",
                "REQUIREMENT_VERSION_CHANGED",
                version_id,
                snapshot,
                "requirement-ingestion",
                target_function_id=function_id,
                action="UPDATE",
                summary=f"需求 {requirement_id} V{version} 已更新，需要确认功能知识是否同步变化",
                base_version_id=current["version"]["id"],
            )
            proposal_id = proposal["proposal"]["id"]
            repository.add_proposal_item(
                proposal_id,
                "REVALIDATE",
                "REQUIREMENT_EVIDENCE",
                before={"versionId": previous_id},
                after={"versionId": version_id, **change},
                rationale="已发布功能引用的需求来源出现新版本；仅生成复核提案，不自动修改业务知识",
                confidence=1.0,
                evidence_ids=new_evidence,
            )
            repository.submit_proposal(proposal_id)

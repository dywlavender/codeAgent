from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..code_matching import CodeKnowledge, CodeMatcher
from ..util import digest, stable_id
from .langchain_adapter import ModelConfig, init_configured_chat_model, model_config_from_environment


ENTITY_TYPES = {
    "SYSTEM", "BUSINESS_TERM", "CAPABILITY", "FLOW", "RULE",
}
ALL_KNOWLEDGE_TYPES = {*ENTITY_TYPES, "RELATION"}
PROMOTION_STATUSES = {"CANDIDATE", "VERIFIED", "CONFLICTED", "DEPRECATED", "UNRESOLVED"}
RELATION_PATTERNS = {
    "TRIGGERS": re.compile(r"触发|导致|引发|后(?=[，,]|会|需要|将|触发|执行)"),
    "BELONGS_TO": re.compile(r"属于|归属|隶属|(?:是|为).{0,12}(?:类型|分类)"),
    "DEPENDS_ON": re.compile(r"依赖|基于|取决于"),
    "PRODUCES": re.compile(r"产生|生成|产出|创建"),
    "HANDLED_BY": re.compile(r"由.{0,40}(?:处理|负责|承办)"),
}
logger = logging.getLogger(__name__)


class BaselineExtractor(Protocol):
    def extract(self, *, source_path: str, text: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BaselineDocument:
    id: str
    path: str
    title: str
    text: str
    entities: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]
    mode: str


class BaselineKnowledgeService:
    """Import a natural-language baseline and build bounded code mappings.

    Human statements, code facts and mappings deliberately remain separate.
    The model may structure source text, but every accepted business item must
    point back to a literal excerpt in that source.
    """

    def __init__(self, db, *, project_config: str | Path | None = None, extractor: BaselineExtractor | None = None):
        self.db = db
        self.project_config = Path(project_config).resolve() if project_config else None
        self.config = self._load_config()
        self._extractor = extractor

    def refresh(self, *, map_code: bool = True, use_model: bool = True) -> dict[str, Any]:
        root = self.knowledge_root()
        if not root.is_dir():
            raise ValueError(f"业务基线目录不存在: {root}")
        paths = sorted(root.rglob("*.md"))
        extractor = self._extractor or (self._configured_extractor() if use_model else None)
        documents = [self._read_document(path, extractor) for path in paths]
        active_sources: set[str] = set()
        counts = {name: 0 for name in sorted(ALL_KNOWLEDGE_TYPES)}
        mapping_counts = {"VERIFIED": 0, "CANDIDATE": 0, "UNRESOLVED": 0}
        for document in documents:
            active_sources.add(document.id)
            self._save_document(document)
            for entity in document.entities:
                counts[entity["type"]] += 1
            counts["RELATION"] += len(document.relations)
            if map_code:
                current = self.rebuild_mappings(source_id=document.id)
                for key in mapping_counts:
                    mapping_counts[key] += current.get(key, 0)
        if active_sources:
            marks = ",".join("?" for _ in active_sources)
            self.db.execute(
                f"UPDATE business_baseline_source SET status='MISSING' WHERE id NOT IN ({marks})",
                tuple(active_sources),
            )
            self.db.execute(
                f"UPDATE business_entity SET status='DEPRECATED' WHERE source_id NOT IN ({marks})",
                tuple(active_sources),
            )
            self.db.execute(
                f"UPDATE business_relation_v2 SET status='DEPRECATED' WHERE source_id NOT IN ({marks})",
                tuple(active_sources),
            )
        else:
            self.db.execute("UPDATE business_baseline_source SET status='MISSING'")
            self.db.execute("UPDATE business_entity SET status='DEPRECATED'")
            self.db.execute("UPDATE business_relation_v2 SET status='DEPRECATED'")
        self.db.commit()
        return {
            "root": str(root), "sourceCount": len(documents), "entityCounts": counts,
            "mappingCounts": mapping_counts,
            "sources": [{"id": item.id, "title": item.title, "path": item.path, "mode": item.mode} for item in documents],
        }

    def list_entities(self, query: str = "", entity_type: str = "") -> list[dict[str, Any]]:
        clauses = ["status IN ('VERIFIED','CANDIDATE','CONFLICTED','UNRESOLVED')"]
        params: list[Any] = []
        if entity_type:
            normalized = entity_type.strip().upper()
            if normalized not in ENTITY_TYPES:
                raise ValueError(f"unsupported business entity type: {entity_type}")
            clauses.append("entity_type=?")
            params.append(normalized)
        rows = self.db.execute(
            "SELECT id FROM business_entity WHERE " + " AND ".join(clauses) + " ORDER BY entity_type,name",
            tuple(params),
        ).fetchall()
        values = [self.get_entity(row["id"]) for row in rows]
        needle = query.strip().casefold()
        if needle:
            values = [item for item in values if needle in json.dumps(item, ensure_ascii=False).casefold()]
        return values[:100]

    def list_relations(self, query: str = "") -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM business_relation_v2 WHERE status!='DEPRECATED' ORDER BY from_label,relation_type,to_label"
        ).fetchall()
        values = []
        for row in rows:
            item = self._relation_dict(row)
            item["mappings"] = self._mapping_values("RELATION", row["id"])
            values.append(item)
        needle = query.strip().casefold()
        return [item for item in values if not needle or needle in json.dumps(item, ensure_ascii=False).casefold()][:100]

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM business_entity WHERE id=?", (entity_id,)).fetchone()
        if not row:
            raise KeyError(entity_id)
        source = self.db.execute(
            "SELECT id,path,title,status,imported_at FROM business_baseline_source WHERE id=?", (row["source_id"],)
        ).fetchone()
        mappings = self._mapping_values("ENTITY", entity_id)
        outgoing = [self._relation_dict(item) for item in self.db.execute(
            "SELECT * FROM business_relation_v2 WHERE from_entity_id=? AND status!='DEPRECATED'", (entity_id,)
        )]
        incoming = [self._relation_dict(item) for item in self.db.execute(
            "SELECT * FROM business_relation_v2 WHERE to_entity_id=? AND status!='DEPRECATED'", (entity_id,)
        )]
        return {
            "id": row["id"], "type": row["entity_type"], "name": row["name"],
            "aliases": json.loads(row["aliases_json"]), "definition": row["definition"],
            "attributes": json.loads(row["attributes_json"]), "sourceType": row["source_type"],
            "sourceId": row["source_id"], "sourceEvidenceId": row["source_evidence_id"],
            "confidence": row["confidence"], "status": row["status"], "updatedAt": row["updated_at"],
            "source": dict(source) if source else None, "mappings": mappings,
            "relations": [*outgoing, *incoming],
        }

    def get_relation(self, relation_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM business_relation_v2 WHERE id=?", (relation_id,)).fetchone()
        if not row:
            raise KeyError(relation_id)
        result = self._relation_dict(row)
        result["mappings"] = self._mapping_values("RELATION", relation_id)
        return result

    def rebuild_mappings(self, *, source_id: str | None = None) -> dict[str, int]:
        matcher = CodeMatcher(self.db)
        counts = {"VERIFIED": 0, "CANDIDATE": 0, "UNRESOLVED": 0}
        entity_rows = self.db.execute(
            "SELECT * FROM business_entity WHERE status!='DEPRECATED'" + (" AND source_id=?" if source_id else ""),
            ((source_id,) if source_id else ()),
        ).fetchall()
        for row in entity_rows:
            self.db.execute(
                "DELETE FROM business_code_mapping WHERE business_type='ENTITY' AND business_id=? AND source_type='CODE'",
                (row["id"],),
            )
            attributes = json.loads(row["attributes_json"])
            aliases = json.loads(row["aliases_json"])
            terms = _mapping_terms(row["name"], aliases, attributes)
            knowledge = CodeKnowledge(
                title=row["name"], statement=row["definition"],
                business_objects=[row["name"], *aliases],
                processes=[str(value) for value in _as_list(attributes.get("businessActions") or attributes.get("steps"))],
                systems=[str(value) for value in _as_list(attributes.get("systems"))],
                keywords=terms, code_hints=[str(value) for value in _as_list(attributes.get("codeHints"))],
            )
            candidates = matcher.rank(knowledge, matcher.build_search_plan(knowledge), limit=5)
            statuses = self._save_candidates("ENTITY", row["id"], _mapping_relation(row["entity_type"]), terms, candidates)
            for status in statuses:
                counts[status] += 1
        relation_rows = self.db.execute(
            "SELECT * FROM business_relation_v2 WHERE status!='DEPRECATED'" + (" AND source_id=?" if source_id else ""),
            ((source_id,) if source_id else ()),
        ).fetchall()
        for row in relation_rows:
            self.db.execute(
                "DELETE FROM business_code_mapping WHERE business_type='RELATION' AND business_id=? AND source_type='CODE'",
                (row["id"],),
            )
            terms = _unique([row["from_label"], row["to_label"], row["scope"], *_split_identifier(row["relation_type"])])
            knowledge = CodeKnowledge(
                title=f"{row['from_label']} {row['relation_type']} {row['to_label']}",
                statement=f"{row['from_label']} {row['relation_type']} {row['to_label']}",
                business_objects=[row["from_label"], row["to_label"]], processes=terms, keywords=terms,
            )
            candidates = matcher.rank(knowledge, matcher.build_search_plan(knowledge), limit=5)
            statuses = self._save_candidates("RELATION", row["id"], "EVIDENCED_BY", terms, candidates)
            for status in statuses:
                counts[status] += 1
        self.db.commit()
        return counts

    def knowledge_root(self) -> Path:
        knowledge = self.config.get("knowledge") or {}
        configured = knowledge.get("baselineRoot") or knowledge.get("root") or "knowledge/baseline"
        base = self.project_config.parent if self.project_config else Path.cwd()
        path = Path(str(configured)).expanduser()
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    def _read_document(self, path: Path, extractor: BaselineExtractor | None) -> BaselineDocument:
        text = path.read_text(encoding="utf-8-sig")
        title = _document_title(text, path.stem)
        source_id = stable_id("BKS", str(path.resolve()))
        if extractor:
            try:
                payload = extractor.extract(source_path=str(path.resolve()), text=text)
                mode = "MODEL"
            except Exception as exc:
                logger.warning("业务基线模型调用失败，使用安全回退解析: %s", type(exc).__name__)
                payload = _deterministic_extract(text)
                mode = "MODEL_FALLBACK"
        else:
            payload = _deterministic_extract(text)
            mode = "SAFE_FALLBACK"
        entities, relations = _validate_payload(payload, text)
        return BaselineDocument(source_id, str(path.resolve()), title, text, tuple(entities), tuple(relations), mode)

    def _save_document(self, document: BaselineDocument) -> None:
        now = _now()
        revision = digest(document.text)
        self.db.execute(
            """INSERT INTO business_baseline_source(id,path,title,source_revision,content,status,imported_at)
               VALUES (?,?,?,?,?,'ACTIVE',?)
               ON CONFLICT(id) DO UPDATE SET path=excluded.path,title=excluded.title,
                 source_revision=excluded.source_revision,content=excluded.content,status='ACTIVE',imported_at=excluded.imported_at""",
            (document.id, document.path, document.title, revision, document.text, now),
        )
        self.db.execute("UPDATE business_entity SET status='DEPRECATED' WHERE source_id=?", (document.id,))
        self.db.execute("UPDATE business_relation_v2 SET status='DEPRECATED' WHERE source_id=?", (document.id,))
        entity_ids: dict[str, str] = {}
        alias_ids: dict[str, str] = {}
        for item in document.entities:
            entity_id = stable_id("BKE", document.id, item["type"], item["name"])
            entity_ids[item["name"].casefold()] = entity_id
            for alias in item["aliases"]:
                alias_ids[alias.casefold()] = entity_id
            evidence_id = self._save_source_evidence(document, item["sourceQuote"], entity_id)
            self.db.execute(
                """INSERT INTO business_entity
                   (id,entity_type,name,aliases_json,definition,attributes_json,source_type,source_id,
                    source_evidence_id,confidence,status,updated_at)
                   VALUES (?,?,?,?,?,?,'HUMAN',?,?,1.0,'VERIFIED',?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,aliases_json=excluded.aliases_json,
                     definition=excluded.definition,attributes_json=excluded.attributes_json,
                     source_evidence_id=excluded.source_evidence_id,confidence=1.0,status='VERIFIED',updated_at=excluded.updated_at""",
                (entity_id, item["type"], item["name"], json.dumps(item["aliases"], ensure_ascii=False),
                 item["definition"], json.dumps(item["attributes"], ensure_ascii=False), document.id,
                 evidence_id, now),
            )
        for item in document.relations:
            from_id = entity_ids.get(item["from"].casefold()) or alias_ids.get(item["from"].casefold())
            to_id = entity_ids.get(item["to"].casefold()) or alias_ids.get(item["to"].casefold())
            relation_id = stable_id("BKR", document.id, item["from"], item["relation"], item["to"], item["scope"])
            evidence_id = self._save_source_evidence(document, item["sourceQuote"], relation_id)
            self.db.execute(
                """INSERT INTO business_relation_v2
                   (id,from_entity_id,from_label,relation_type,to_entity_id,to_label,scope,attributes_json,
                    source_type,source_id,evidence_id,confidence,status,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,'HUMAN',?,?,1.0,'VERIFIED',?)
                   ON CONFLICT(id) DO UPDATE SET from_entity_id=excluded.from_entity_id,to_entity_id=excluded.to_entity_id,
                     attributes_json=excluded.attributes_json,evidence_id=excluded.evidence_id,
                     confidence=1.0,status='VERIFIED',updated_at=excluded.updated_at""",
                (relation_id, from_id, item["from"], item["relation"], to_id, item["to"], item["scope"],
                 json.dumps(item["attributes"], ensure_ascii=False), document.id, evidence_id, now),
            )
        self.db.commit()

    def _save_source_evidence(self, document: BaselineDocument, quote: str, owner_id: str) -> str:
        quote = quote.strip()
        offset = document.text.find(quote)
        line_start = document.text[:max(offset, 0)].count("\n") + 1
        line_end = line_start + quote.count("\n")
        evidence_id = stable_id("EVD", "BUSINESS_BASELINE", owner_id, quote)
        self.db.execute(
            """INSERT OR REPLACE INTO evidence
               (id,source_type,source_id,source_version,locator,line_start,line_end,chunk_id,content_hash,excerpt)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (evidence_id, "BUSINESS", owner_id, "1", document.path, line_start, line_end,
             document.id, digest(quote), quote[:2000]),
        )
        self.db.execute("INSERT OR REPLACE INTO evidence_lifecycle VALUES (?,'ACTIVE',NULL,NULL,NULL)", (evidence_id,))
        return evidence_id

    def _save_candidates(self, business_type, business_id, relation_type, terms, candidates) -> list[str]:
        if not candidates:
            self._insert_mapping(business_type, business_id, relation_type, None, "", "UNRESOLVED", 0.0,
                                 [], terms, "当前代码索引中未找到匹配项")
            return ["UNRESOLVED"]
        top = candidates[0]
        unique_top = len(candidates) == 1 or top.score >= candidates[1].score + 3
        verified = top.score >= 8 and unique_top and bool(top.evidence_ids)
        statuses: list[str] = []
        for index, candidate in enumerate(candidates):
            status = "VERIFIED" if index == 0 and verified else "CANDIDATE"
            confidence = min(0.99, max(0.35, candidate.score / 15))
            self._insert_mapping(
                business_type, business_id, relation_type, candidate.target_id, candidate.label,
                status, confidence, candidate.evidence_ids, terms, candidate.reason,
            )
            statuses.append(status)
        return statuses

    def _insert_mapping(self, business_type, business_id, relation_type, symbol_id, reference,
                        status, confidence, evidence_ids, terms, message):
        mapping_id = stable_id("BCM", business_type, business_id, relation_type, reference or "UNRESOLVED")
        self.db.execute(
            """INSERT INTO business_code_mapping
               (id,business_type,business_id,relation_type,code_symbol_id,code_reference,status,confidence,
                evidence_ids_json,search_terms_json,message,source_type,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'CODE',?)
               ON CONFLICT(business_type,business_id,relation_type,code_reference)
               DO UPDATE SET code_symbol_id=COALESCE(excluded.code_symbol_id,business_code_mapping.code_symbol_id),
                 status=CASE
                   WHEN business_code_mapping.status='VERIFIED' THEN 'VERIFIED'
                   WHEN business_code_mapping.source_type IN ('QUERY','QUERY_REVIEW') THEN business_code_mapping.status
                   ELSE excluded.status END,
                 confidence=MAX(business_code_mapping.confidence,excluded.confidence),
                 evidence_ids_json=CASE WHEN business_code_mapping.source_type IN ('QUERY','QUERY_REVIEW')
                                        THEN business_code_mapping.evidence_ids_json ELSE excluded.evidence_ids_json END,
                 search_terms_json=CASE WHEN business_code_mapping.source_type IN ('QUERY','QUERY_REVIEW')
                                        THEN business_code_mapping.search_terms_json ELSE excluded.search_terms_json END,
                 message=CASE WHEN business_code_mapping.source_type IN ('QUERY','QUERY_REVIEW')
                              THEN business_code_mapping.message ELSE excluded.message END,
                 source_type=CASE WHEN business_code_mapping.source_type IN ('QUERY','QUERY_REVIEW')
                                  THEN business_code_mapping.source_type ELSE 'CODE' END,
                 updated_at=excluded.updated_at""",
            (mapping_id, business_type, business_id, relation_type, symbol_id, reference, status, confidence,
             json.dumps(evidence_ids, ensure_ascii=False), json.dumps(terms, ensure_ascii=False), message[:500], _now()),
        )

    def _mapping_values(self, business_type: str, business_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT * FROM business_code_mapping
                WHERE business_type=? AND business_id=?
                ORDER BY confidence DESC,code_reference""",
            (business_type, business_id),
        ).fetchall()
        values = [self._mapping_dict(item) for item in rows]
        known = {(item["codeSymbolId"], item["codeReference"]) for item in values}
        observations = self.db.execute(
            """SELECT * FROM business_code_mapping_observation
                WHERE business_type=? AND business_id=? AND status='CANDIDATE'
                ORDER BY confidence DESC,code_reference""",
            (business_type, business_id),
        ).fetchall()
        for row in observations:
            key = (row["code_symbol_id"], row["code_reference"])
            if key in known:
                continue
            values.append({
                "id": row["id"], "businessType": row["business_type"], "businessId": row["business_id"],
                "relation": row["relation_type"], "codeSymbolId": row["code_symbol_id"],
                "codeReference": row["code_reference"], "status": "CANDIDATE",
                "confidence": row["confidence"], "evidenceIds": json.loads(row["evidence_ids_json"]),
                "searchTerms": [], "message": row["reason"], "sourceType": "QUERY",
                "updatedAt": row["created_at"], "observationId": row["id"],
            })
            known.add(key)
        return values

    def _configured_extractor(self) -> BaselineExtractor | None:
        config = model_config_from_environment()
        if not config or not config.get("enabled", True):
            return None
        try:
            return LangChainBaselineExtractor(init_configured_chat_model(ModelConfig.from_mapping(config)))
        except (RuntimeError, ValueError):
            return None

    def _load_config(self) -> dict[str, Any]:
        if not self.project_config or not self.project_config.is_file():
            return {"knowledge": {"baselineRoot": "knowledge/baseline"}}
        return json.loads(self.project_config.read_text(encoding="utf-8"))

    @staticmethod
    def _relation_dict(row) -> dict[str, Any]:
        return {
            "id": row["id"], "type": "RELATION", "fromEntityId": row["from_entity_id"],
            "from": row["from_label"], "relation": row["relation_type"],
            "toEntityId": row["to_entity_id"], "to": row["to_label"], "scope": row["scope"],
            "attributes": json.loads(row["attributes_json"]), "sourceType": row["source_type"],
            "sourceId": row["source_id"], "evidenceId": row["evidence_id"],
            "confidence": row["confidence"], "status": row["status"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _mapping_dict(row) -> dict[str, Any]:
        return {
            "id": row["id"], "businessType": row["business_type"], "businessId": row["business_id"],
            "relation": row["relation_type"], "codeSymbolId": row["code_symbol_id"],
            "codeReference": row["code_reference"], "status": row["status"],
            "confidence": row["confidence"], "evidenceIds": json.loads(row["evidence_ids_json"]),
            "searchTerms": json.loads(row["search_terms_json"]), "message": row["message"],
            "sourceType": row["source_type"], "updatedAt": row["updated_at"],
        }


class LangChainBaselineExtractor:
    def __init__(self, model, *, agent_factory=None):
        self.model = model
        self.agent_factory = agent_factory

    def extract(self, *, source_path: str, text: str) -> Mapping[str, Any]:
        factory = self.agent_factory
        if factory is None:
            from langchain.agents import create_agent
            factory = create_agent
        agent = factory(model=self.model, tools=[], response_format=_baseline_schema(), system_prompt=_BASELINE_PROMPT)
        result = agent.invoke({"messages": [{"role": "user", "content": json.dumps({"path": source_path, "text": text}, ensure_ascii=False)}]})
        structured = result.get("structured_response") if isinstance(result, Mapping) else None
        if structured is None:
            raise ValueError("业务基线模型没有返回结构化结果")
        value = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
        if not isinstance(value, Mapping):
            raise ValueError("业务基线结构化结果格式错误")
        return value


def _baseline_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class Entity(BaseModel):
        model_config = ConfigDict(extra="forbid")
        type: str
        name: str
        aliases: list[str] = Field(default_factory=list)
        definition: str
        attributes: dict[str, Any] = Field(default_factory=dict)
        sourceQuote: str

    class Relation(BaseModel):
        model_config = ConfigDict(extra="forbid")
        from_: str = Field(alias="from")
        relation: str
        to: str
        scope: str = ""
        attributes: dict[str, Any] = Field(default_factory=dict)
        sourceQuote: str

    class Result(BaseModel):
        model_config = ConfigDict(extra="forbid")
        entities: list[Entity] = Field(default_factory=list)
        relations: list[Relation] = Field(default_factory=list)

    return Result


_BASELINE_PROMPT = """你负责把人工业务基线转换为严格的内部知识结构。
只允许实体类型 SYSTEM、BUSINESS_TERM、CAPABILITY、FLOW、RULE；业务关系放在 relations。
不要补充原文没有表达的业务事实。每个实体和关系必须提供 sourceQuote，且必须逐字出现在原文。
实体的名称、别名和属性必须出现在 sourceQuote 所在的 Markdown 小节中；关系的 from、to 和关系语义必须同时出现在 sourceQuote 中。
FLOW 的业务步骤放 attributes.steps；RULE 的 condition、behavior、scope 放 attributes；
SYSTEM 的 responsibilities、nonResponsibilities 放 attributes。英文缩写或代码名称放 aliases 或 attributes.codeHints。
关系使用简短稳定的英文谓词，例如 TRIGGERS、PRODUCES、BELONGS_TO、DEPENDS_ON、HANDLED_BY。
无法确定时少提取，不要猜测代码类名。"""


def _validate_payload(payload: Mapping[str, Any], text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: list[dict[str, Any]] = []
    for raw in _as_list(payload.get("entities")):
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("type") or "").strip().upper()
        name = str(raw.get("name") or "").strip()
        definition = str(raw.get("definition") or "").strip()
        quote = str(raw.get("sourceQuote") or raw.get("source_quote") or "").strip()
        if kind not in ENTITY_TYPES or not name or not definition:
            continue
        quote = _grounded_quote(text, quote, name, definition)
        grounding_scope = _section_containing_quote(text, quote, name)
        # A model may use a valid quote as cover for a second, invented
        # business object.  The object name itself must be present in the
        # section containing the quote before it can be persisted.  Checking
        # the whole document would allow an alias from product A to leak into
        # product B when both are described in one domain baseline.
        if not _literal_in_source(grounding_scope, name):
            logger.warning("忽略缺少原文依据的业务实体: %s", name)
            continue
        entities.append({
            "type": kind, "name": name,
            # Aliases and retrieval hints are not harmless presentation
            # fields: they directly influence code search.  Keep only literal
            # values from the human source so a model cannot invent a class
            # name and then use that name to prove its own mapping.
            "aliases": _grounded_strings(raw.get("aliases"), grounding_scope),
            "definition": _grounded_definition(definition, grounding_scope, quote, name),
            "attributes": _grounded_attributes(raw.get("attributes"), grounding_scope),
            "sourceQuote": quote,
        })
    relations: list[dict[str, Any]] = []
    for raw in _as_list(payload.get("relations")):
        if not isinstance(raw, Mapping):
            continue
        source = str(raw.get("from") or raw.get("from_") or "").strip()
        relation = str(raw.get("relation") or "").strip().upper()
        target = str(raw.get("to") or "").strip()
        quote = str(raw.get("sourceQuote") or raw.get("source_quote") or "").strip()
        if not source or not relation or not target:
            continue
        quote = _grounded_quote(text, quote, source, target)
        # Relations are stricter than entities: both endpoints and the
        # linguistic expression of the normalized predicate must be present
        # in the same quoted passage.  Merely appearing elsewhere in the same
        # Markdown document is not evidence of a relationship.
        if not _literal_in_source(quote, source) or not _literal_in_source(quote, target):
            logger.warning("忽略缺少原文依据的业务关系: %s %s %s", source, relation, target)
            continue
        if not _relation_expressed(relation, quote):
            logger.warning("忽略原文未明确表达谓词的业务关系: %s %s %s", source, relation, target)
            continue
        relations.append({
            "from": source, "relation": relation, "to": target,
            "scope": _grounded_scalar(raw.get("scope"), quote) or "",
            "attributes": _grounded_attributes(raw.get("attributes"), quote), "sourceQuote": quote,
        })
    return entities, relations


def _grounded_quote(text: str, quote: str, *fallbacks: str) -> str:
    if quote:
        if quote in text:
            return quote
        raise ValueError("结构化知识引用的原文片段不存在")
    for value in fallbacks:
        if value and value in text:
            line = next((line.strip() for line in text.splitlines() if value in line), value)
            return line
    raise ValueError("结构化知识缺少可回溯的原文片段")


def _section_containing_quote(text: str, quote: str, anchor: str = "") -> str:
    """Return the narrowest Markdown section containing a quoted passage.

    The heading is included because an entity name is often authored there
    while its definition is in the following paragraph.  When identical
    quotes occur more than once, prefer the section containing the entity
    name instead of silently selecting the first document-wide occurrence.
    """
    offsets = [match.start() for match in re.finditer(re.escape(quote), text)]
    if not offsets:
        return quote
    headings = list(re.finditer(r"^(#{1,6})\s+.+?\s*$", text, re.MULTILINE))
    scopes: list[str] = []
    for offset in offsets:
        preceding = [item for item in headings if item.start() <= offset]
        if not preceding:
            scopes.append(quote)
            continue
        heading = preceding[-1]
        level = len(heading.group(1))
        end = next(
            (item.start() for item in headings if item.start() > heading.start() and len(item.group(1)) <= level),
            len(text),
        )
        scopes.append(text[heading.start():end].strip())
    if anchor:
        anchored = next((scope for scope in scopes if _literal_in_source(scope, anchor)), None)
        if anchored is not None:
            return anchored
    return scopes[0]


def _relation_expressed(relation: str, quote: str) -> bool:
    pattern = RELATION_PATTERNS.get(str(relation or "").upper())
    return bool(pattern and pattern.search(str(quote or "")))


def _literal_in_source(text: str, value: Any) -> bool:
    """Return whether *value* is an authored literal, tolerating whitespace.

    This deliberately does not perform semantic matching.  A semantic
    paraphrase is acceptable for a display definition only after it is
    replaced by a grounded source sentence; identifiers and aliases must be
    literal because they are later used as retrieval hints.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return False
    if candidate in text:
        # Do not let a short generated identifier pass merely because it is a
        # substring of a longer identifier (for example ``Guarantee`` inside
        # ``GuaranteeFileTask``). Chinese prose is intentionally left as
        # substring matching because compound business names are common.
        if re.fullmatch(r"[A-Za-z0-9_.$-]+", candidate):
            return bool(re.search(
                rf"(?<![A-Za-z0-9_.$-]){re.escape(candidate)}(?![A-Za-z0-9_.$-])",
                text,
                re.IGNORECASE,
            ))
        return True
    compact_text = re.sub(r"\s+", " ", text).strip().casefold()
    compact_candidate = re.sub(r"\s+", " ", candidate).strip().casefold()
    if not compact_candidate or compact_candidate not in compact_text:
        return False
    if re.fullmatch(r"[A-Za-z0-9_.$-]+", compact_candidate):
        return bool(re.search(
            rf"(?<![A-Za-z0-9_.$-]){re.escape(compact_candidate)}(?![A-Za-z0-9_.$-])",
            compact_text,
            re.IGNORECASE,
        ))
    return True


def _grounded_strings(value: Any, text: str) -> list[str]:
    result = []
    for item in _as_list(value):
        if isinstance(item, (str, int, float)):
            candidate = str(item).strip()
            if candidate and _literal_in_source(text, candidate):
                result.append(candidate)
    return _unique(result)


def _grounded_scalar(value: Any, text: str) -> str:
    if isinstance(value, (str, int, float)):
        candidate = str(value).strip()
        return candidate if candidate and _literal_in_source(text, candidate) else ""
    return ""


def _grounded_attributes(value: Any, text: str) -> dict[str, Any]:
    """Drop model-created attribute values that have no literal source.

    Attributes are intentionally treated conservatively as a whole tree.  A
    nested ``codeHints`` or ``steps`` value is still a retrieval input, and a
    free-form value that is not in the source must not survive into the
    knowledge database.  Empty containers are omitted instead of being stored
    as misleading facts.
    """
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, raw in value.items():
        grounded = _grounded_attribute_value(raw, text)
        if grounded is not _DROPPED:
            result[str(key)] = grounded
    return result


_DROPPED = object()


def _grounded_attribute_value(value: Any, text: str):
    if isinstance(value, Mapping):
        nested = _grounded_attributes(value, text)
        return nested if nested else _DROPPED
    if isinstance(value, (list, tuple, set)):
        nested = [item for item in (_grounded_attribute_value(item, text) for item in value) if item is not _DROPPED]
        return nested if nested else _DROPPED
    if isinstance(value, bool):
        # Boolean literals are uncommon in prose; retain them only when the
        # exact spelling was authored, otherwise discard the model assertion.
        return value if _literal_in_source(text, str(value).lower()) else _DROPPED
    if isinstance(value, (str, int, float)):
        candidate = str(value).strip()
        return candidate if candidate and _literal_in_source(text, candidate) else _DROPPED
    return _DROPPED


def _grounded_definition(definition: str, text: str, quote: str, name: str) -> str:
    if _literal_in_source(text, definition):
        return definition
    # Model definitions are often concise paraphrases.  Do not persist the
    # paraphrase as a fact; use the first non-heading sentence from the quoted
    # source instead.  It remains readable while preserving provenance.
    source_sentence = _first_sentence(_quote_body(quote))
    if source_sentence and _literal_in_source(text, source_sentence):
        return source_sentence
    return name


def _quote_body(quote: str) -> str:
    lines = []
    for line in str(quote or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", value)
        if value:
            lines.append(value)
    return " ".join(lines)


def _deterministic_extract(text: str) -> dict[str, Any]:
    """Safe no-model fallback: extract only explicit Markdown sections."""
    sections = _markdown_sections(text)
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for title, body, quote in sections:
        kind = _explicit_type(title, body)
        definition = _first_sentence(body) or title
        aliases = re.findall(r"(?:代码中(?:一般)?用|简称(?:为)?|别名(?:为)?)[：:\s]*([A-Za-z][A-Za-z0-9_-]*)", body)
        attributes: dict[str, Any] = {}
        if kind == "FLOW":
            attributes["steps"] = [line.strip().lstrip("-0123456789. ") for line in body.splitlines() if line.strip().startswith(("-", "1", "2", "3", "4", "5"))]
        if kind == "RULE":
            attributes["statement"] = definition
        identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", body)
        ignored = {"the", "and", "for", "with", "from", "this", "that"}
        code_hints = [value for value in identifiers if value.casefold() not in ignored]
        if code_hints:
            attributes["codeHints"] = _unique(code_hints)[:12]
        if "关系" not in title:
            entities.append({
                "type": kind, "name": title, "aliases": aliases,
                "definition": definition, "attributes": attributes, "sourceQuote": quote,
            })
        match = re.search(r"(.{2,24}?)(?:完成|成功|失败|创建)后[，,]\s*(?:需要|会|将)?\s*(.{2,32}?)(?:。|$)", body)
        if match:
            relations.append({
                "from": match.group(1).strip(), "relation": "TRIGGERS", "to": match.group(2).strip(),
                "scope": title, "attributes": {}, "sourceQuote": match.group(0).strip(),
            })
    return {"entities": entities, "relations": relations}


def _markdown_sections(text: str) -> list[tuple[str, str, str]]:
    matches = list(re.finditer(r"^#{1,3}\s+(.+?)\s*$", text, re.MULTILINE))
    result = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body and match.group(1).strip() == _document_title(text, ""):
            continue
        quote = text[match.start():end].strip()
        result.append((match.group(1).strip(), body, quote))
    if not result and text.strip():
        title = _document_title(text, "业务基线")
        result.append((title, text.strip(), text.strip()))
    return result


def _explicit_type(title: str, body: str) -> str:
    value = f"{title} {body[:80]}".casefold()
    if any(token in value for token in ("系统", "平台", "system")): return "SYSTEM"
    if any(token in value for token in ("流程", "flow")): return "FLOW"
    if any(token in value for token in ("规则", "条件", "rule")): return "RULE"
    if any(token in value for token in ("能力", "处理", "capability")): return "CAPABILITY"
    return "BUSINESS_TERM"


def _document_title(text: str, default: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else default


def _first_sentence(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return re.split(r"[。！？!?]\s*", compact, maxsplit=1)[0].strip()


def _mapping_terms(name: str, aliases: list[str], attributes: Mapping[str, Any]) -> list[str]:
    values: list[str] = [name, *aliases]
    for key in ("codeHints", "keywords", "businessActions", "steps", "responsibilities"):
        for item in _as_list(attributes.get(key)):
            if isinstance(item, Mapping):
                values.extend(str(value) for value in item.values() if isinstance(value, (str, int)))
            else:
                values.append(str(item))
    return _unique([value.strip() for value in values if value and value.strip()])[:20]


def _mapping_relation(entity_type: str) -> str:
    return {
        "SYSTEM": "OWNED_BY", "BUSINESS_TERM": "REPRESENTED_BY", "CAPABILITY": "IMPLEMENTED_BY",
        "FLOW": "IMPLEMENTED_BY", "RULE": "ENFORCED_BY",
    }[entity_type]


def _split_identifier(value: str) -> list[str]:
    return [part for part in re.split(r"[_\W]+", value) if len(part) > 1]


def _as_list(value: Any) -> list[Any]:
    if value is None: return []
    if isinstance(value, list): return value
    if isinstance(value, tuple): return list(value)
    return [value]


def _unique(values) -> list:
    return list(dict.fromkeys(value for value in values if value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

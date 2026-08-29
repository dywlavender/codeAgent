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
            item["mappings"] = [self._mapping_dict(mapping) for mapping in self.db.execute(
                "SELECT * FROM business_code_mapping WHERE business_type='RELATION' AND business_id=? ORDER BY confidence DESC",
                (row["id"],),
            )]
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
        mappings = [self._mapping_dict(item) for item in self.db.execute(
            "SELECT * FROM business_code_mapping WHERE business_type='ENTITY' AND business_id=? ORDER BY confidence DESC,code_reference",
            (entity_id,),
        )]
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
        result["mappings"] = [self._mapping_dict(item) for item in self.db.execute(
            "SELECT * FROM business_code_mapping WHERE business_type='RELATION' AND business_id=? ORDER BY confidence DESC",
            (relation_id,),
        )]
        return result

    def rebuild_mappings(self, *, source_id: str | None = None) -> dict[str, int]:
        matcher = CodeMatcher(self.db)
        counts = {"VERIFIED": 0, "CANDIDATE": 0, "UNRESOLVED": 0}
        entity_rows = self.db.execute(
            "SELECT * FROM business_entity WHERE status!='DEPRECATED'" + (" AND source_id=?" if source_id else ""),
            ((source_id,) if source_id else ()),
        ).fetchall()
        for row in entity_rows:
            self.db.execute("DELETE FROM business_code_mapping WHERE business_type='ENTITY' AND business_id=?", (row["id"],))
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
            self.db.execute("DELETE FROM business_code_mapping WHERE business_type='RELATION' AND business_id=?", (row["id"],))
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
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'CODE',?)""",
            (mapping_id, business_type, business_id, relation_type, symbol_id, reference, status, confidence,
             json.dumps(evidence_ids, ensure_ascii=False), json.dumps(terms, ensure_ascii=False), message[:500], _now()),
        )

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
        entities.append({
            "type": kind, "name": name,
            "aliases": _unique([str(value).strip() for value in _as_list(raw.get("aliases")) if str(value).strip()]),
            "definition": definition, "attributes": dict(raw.get("attributes") or {}), "sourceQuote": quote,
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
        relations.append({
            "from": source, "relation": relation, "to": target,
            "scope": str(raw.get("scope") or "").strip(),
            "attributes": dict(raw.get("attributes") or {}), "sourceQuote": quote,
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

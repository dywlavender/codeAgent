from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..util import digest, stable_id
from .langchain_adapter import (
    ModelConfig,
    init_configured_chat_model,
    model_config_from_environment,
)


@dataclass(frozen=True)
class FunctionalDocument:
    id: str
    name: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    summary: str
    scenarios: tuple[str, ...]
    entries: tuple[tuple[str, str, str], ...]
    key_tables: tuple[tuple[str, str], ...]
    notes: str
    source_path: str
    source_text: str


class FunctionalKnowledgeService:
    """Function-document ingestion and bounded code-navigation analysis."""

    def __init__(self, db, *, project_config: str | Path | None = None, analyzer=None):
        self.db = db
        self.project_config = Path(project_config).resolve() if project_config else None
        self.config = self._load_config()
        self._analyzer = analyzer

    def refresh(self, *, analyze: bool = True) -> dict:
        root = self.knowledge_root()
        if not root.is_dir():
            raise ValueError(f"功能知识目录不存在: {root}")
        documents = [parse_function_document(path) for path in sorted(root.rglob("*.md"))]
        duplicates = sorted({item.id for item in documents if sum(doc.id == item.id for doc in documents) > 1})
        if duplicates:
            raise ValueError("功能 ID 重复: " + ", ".join(duplicates))
        refreshed: list[dict] = []
        for document in documents:
            self._save_definition(document)
            self._build_index(document)
            if analyze:
                self.analyze(document.id)
            refreshed.append(self.get_function(document.id))
        active_ids = {item.id for item in documents}
        if active_ids:
            marks = ",".join("?" for _ in active_ids)
            self.db.execute(
                f"UPDATE functional_knowledge SET status='MISSING' WHERE id NOT IN ({marks})",
                tuple(active_ids),
            )
        else:
            self.db.execute("UPDATE functional_knowledge SET status='MISSING'")
        self.db.commit()
        return {
            "root": str(root),
            "functionCount": len(refreshed),
            "functions": refreshed,
        }

    def list_functions(self, query: str = "") -> list[dict]:
        rows = self.db.execute(
            "SELECT id FROM functional_knowledge WHERE status='ACTIVE' ORDER BY name"
        ).fetchall()
        values = [self.get_function(row["id"]) for row in rows]
        needle = query.strip().casefold()
        if not needle:
            return values
        return [item for item in values if needle in json.dumps(item["definition"], ensure_ascii=False).casefold()]

    def get_function(self, function_id: str) -> dict:
        row = self.db.execute("SELECT * FROM functional_knowledge WHERE id=?", (function_id,)).fetchone()
        if not row:
            raise KeyError(function_id)
        entries = [dict(item) for item in self.db.execute(
            "SELECT * FROM functional_entry_anchor WHERE function_id=? ORDER BY project_name,entry_type,class_name",
            (function_id,),
        )]
        for item in entries:
            item["candidateIds"] = json.loads(item.pop("candidate_ids_json"))
            if item.get("symbol_id"):
                location = self.db.execute(
                    """SELECT cs.qualified_name,cs.line_start,cs.line_end,cf.path
                         FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id WHERE cs.id=?""",
                    (item["symbol_id"],),
                ).fetchone()
                item["location"] = dict(location) if location else None
        tables = [dict(item) for item in self.db.execute(
            "SELECT * FROM functional_key_table WHERE function_id=? ORDER BY table_name", (function_id,)
        )]
        links = [dict(item) for item in self.db.execute(
            "SELECT * FROM functional_retrieval_link WHERE function_id=? ORDER BY relation_type,target_id",
            (function_id,),
        )]
        analysis_row = self.db.execute(
            "SELECT * FROM functional_analysis WHERE function_id=?", (function_id,)
        ).fetchone()
        analysis = {
            "status": "NOT_RUN", "flow": [], "rules": [], "coverage": {},
            "mode": "NONE", "analyzedAt": None, "message": "尚未分析",
        }
        if analysis_row:
            analysis = {
                "status": analysis_row["status"],
                "flow": json.loads(analysis_row["flow_json"]),
                "rules": json.loads(analysis_row["rules_json"]),
                "coverage": json.loads(analysis_row["coverage_json"]),
                "mode": analysis_row["mode"],
                "analyzedAt": analysis_row["analyzed_at"],
                "message": analysis_row["message"],
            }
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "definition": {
                "id": row["id"], "name": row["name"],
                "aliases": json.loads(row["aliases_json"]),
                "tags": json.loads(row["tags_json"]),
                "summary": row["summary"],
                "scenarios": json.loads(row["scenarios_json"]),
                "notes": row["notes"], "sourcePath": row["source_path"],
                "refreshedAt": row["refreshed_at"],
            },
            "entries": entries,
            "keyTables": tables,
            "retrievalLinks": links,
            "analysis": analysis,
        }

    def analyze(self, function_id: str) -> dict:
        detail = self.get_function(function_id)
        evidence_ids = list(dict.fromkeys(
            item["evidence_id"] for item in detail["retrievalLinks"] if item.get("evidence_id")
        ))
        coverage = self._coverage(detail, evidence_ids)
        if not evidence_ids:
            self._save_analysis(function_id, "INSUFFICIENT", [], [], coverage, "INDEX", "没有可用于分析的代码证据")
            return self.get_function(function_id)
        analyzer = self._analyzer or self._configured_analyzer()
        if analyzer is None:
            self._save_analysis(function_id, "INDEXED", [], [], coverage, "INDEX", "模型未配置，已完成检索索引")
            return self.get_function(function_id)
        context = self._analysis_context(detail, evidence_ids)
        try:
            result = analyzer.analyze(context, set(evidence_ids))
            flow = _validate_grounded_items(result.get("flow", []), set(evidence_ids), "业务流程")
            rules = _validate_grounded_items(result.get("rules", []), set(evidence_ids), "核心规则")
            self._save_analysis(function_id, "READY", flow, rules, coverage, "MODEL", "")
        except Exception as exc:
            self._save_analysis(function_id, "FAILED", [], [], coverage, "MODEL", str(exc))
        return self.get_function(function_id)

    def knowledge_root(self) -> Path:
        configured = self.config.get("knowledge", {}).get("root", "knowledge/functions")
        base = self.project_config.parent if self.project_config else Path.cwd()
        path = Path(str(configured)).expanduser()
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    def _load_config(self) -> dict:
        if not self.project_config or not self.project_config.is_file():
            return {"knowledge": {"root": "knowledge/functions"}, "repositories": []}
        return json.loads(self.project_config.read_text(encoding="utf-8"))

    def _save_definition(self, document: FunctionalDocument) -> None:
        now = _now()
        self.db.execute(
            """INSERT INTO functional_knowledge
               (id,name,aliases_json,tags_json,summary,scenarios_json,notes,source_path,source_fingerprint,status,refreshed_at)
               VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE',?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,aliases_json=excluded.aliases_json,
                 tags_json=excluded.tags_json,summary=excluded.summary,scenarios_json=excluded.scenarios_json,
                 notes=excluded.notes,source_path=excluded.source_path,source_fingerprint=excluded.source_fingerprint,
                 status='ACTIVE',refreshed_at=excluded.refreshed_at""",
            (document.id, document.name, json.dumps(document.aliases, ensure_ascii=False),
             json.dumps(document.tags, ensure_ascii=False), document.summary,
             json.dumps(document.scenarios, ensure_ascii=False), document.notes,
             document.source_path, digest(document.source_text), now),
        )
        self.db.execute("DELETE FROM functional_entry_anchor WHERE function_id=?", (document.id,))
        self.db.execute("DELETE FROM functional_key_table WHERE function_id=?", (document.id,))
        self.db.execute("DELETE FROM functional_retrieval_link WHERE function_id=?", (document.id,))
        document_evidence_id = stable_id("EVD", "FUNCTION_DOCUMENT", document.id, digest(document.source_text))
        self.db.execute(
            """INSERT OR REPLACE INTO evidence
               (id,source_type,source_id,source_version,locator,line_start,line_end,chunk_id,content_hash,excerpt)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (document_evidence_id, "BUSINESS", document.id, "1", document.source_path,
             1, len(document.source_text.splitlines()), None, digest(document.source_text),
             document.summary + ("\n业务场景：" + "；".join(document.scenarios) if document.scenarios else "")),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO evidence_lifecycle VALUES (?,'ACTIVE',NULL,NULL,NULL)",
            (document_evidence_id,),
        )
        self._add_link(document.id, "DOCUMENT", document.source_path, "MANUAL_DEFINITION",
                       "FUNCTION", document.id, document_evidence_id)
        for project_name, entry_type, class_name in document.entries:
            self.db.execute(
                "INSERT INTO functional_entry_anchor VALUES (?,?,?,?,?,?,?,?)",
                (stable_id("FEA", document.id, project_name, entry_type, class_name), document.id,
                 project_name, entry_type, class_name, None, "PENDING", "[]"),
            )
        for table_name, purpose in document.key_tables:
            self.db.execute(
                "INSERT INTO functional_key_table VALUES (?,?,?,?)",
                (stable_id("FKT", document.id, table_name.casefold()), document.id, table_name, purpose),
            )
        self.db.execute(
            """INSERT INTO functional_analysis(function_id,status,flow_json,rules_json,coverage_json,mode,message)
               VALUES (?,'NOT_RUN','[]','[]','{}','NONE','等待代码分析')
               ON CONFLICT(function_id) DO UPDATE SET status='NOT_RUN',flow_json='[]',rules_json='[]',
                 coverage_json='{}',mode='NONE',analyzed_at=NULL,message='等待代码分析'""",
            (document.id,),
        )
        self.db.commit()

    def _build_index(self, document: FunctionalDocument) -> None:
        for project_name, entry_type, class_name in document.entries:
            rows = self.db.execute(
                """SELECT cs.id,cs.file_id FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                    WHERE (cf.repository_id=? OR cf.path LIKE ?) AND cs.name=?
                      AND cs.kind NOT IN ('METHOD','API','FIELD','MYBATIS_STATEMENT')
                    ORDER BY cs.qualified_name""",
                (project_name, f"{project_name}/%", class_name),
            ).fetchall()
            status = "RESOLVED" if len(rows) == 1 else "NOT_FOUND" if not rows else "AMBIGUOUS"
            symbol_id = rows[0]["id"] if len(rows) == 1 else None
            anchor_id = stable_id("FEA", document.id, project_name, entry_type, class_name)
            self.db.execute(
                "UPDATE functional_entry_anchor SET symbol_id=?,resolution_status=?,candidate_ids_json=? WHERE id=?",
                (symbol_id, status, json.dumps([row["id"] for row in rows]), anchor_id),
            )
            if symbol_id:
                self._add_link(document.id, "ENTRY", anchor_id, "ENTRY_SYMBOL", "CODE_SYMBOL", symbol_id, None)
                facts = self.db.execute(
                    """SELECT cs.id symbol_id,cf.fact_type,cf.target,cf.evidence_id
                         FROM code_symbol owner
                         JOIN code_symbol cs ON cs.file_id=owner.file_id
                         JOIN code_fact cf ON cf.symbol_id=cs.id
                        WHERE owner.id=? AND cf.fact_type='CALL' ORDER BY cs.line_start LIMIT 24""",
                    (symbol_id,),
                ).fetchall()
                for fact in facts:
                    self._add_link(document.id, "CODE_SYMBOL", fact["symbol_id"], "DIRECT_CALL_HINT",
                                   "CODE_HINT", fact["target"], fact["evidence_id"])
        for table_name, _purpose in document.key_tables:
            rows = self.db.execute(
                """SELECT cs.id symbol_id,cf.fact_type,cf.evidence_id
                     FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                    WHERE cf.fact_type IN ('READ_TABLE','WRITE_TABLE') AND lower(cf.subject)=lower(?)
                    ORDER BY cs.qualified_name LIMIT 40""",
                (table_name,),
            ).fetchall()
            for fact in rows:
                self._add_link(document.id, "TABLE", table_name, fact["fact_type"],
                               "CODE_SYMBOL", fact["symbol_id"], fact["evidence_id"])
        self.db.commit()

    def _add_link(self, function_id, source_type, source_id, relation_type, target_type, target_id, evidence_id):
        link_id = stable_id("FRL", function_id, source_type, source_id, relation_type, target_type, target_id, evidence_id or "")
        self.db.execute(
            "INSERT OR IGNORE INTO functional_retrieval_link VALUES (?,?,?,?,?,?,?,?,?)",
            (link_id, function_id, source_type, source_id, relation_type, target_type, target_id, evidence_id, _now()),
        )

    def _analysis_context(self, detail: dict, evidence_ids: list[str]) -> dict:
        marks = ",".join("?" for _ in evidence_ids)
        evidence = [dict(row) for row in self.db.execute(
            f"SELECT id,locator,line_start,line_end,excerpt FROM evidence WHERE id IN ({marks})",
            tuple(evidence_ids),
        )]
        return {
            "function": detail["definition"],
            "entries": detail["entries"],
            "keyTables": detail["keyTables"],
            "retrievalLinks": detail["retrievalLinks"],
            "evidence": evidence,
        }

    def _configured_analyzer(self):
        config = model_config_from_environment()
        if not config or not config.get("enabled", True):
            return None
        try:
            return LangChainFunctionalAnalyzer(init_configured_chat_model(ModelConfig.from_mapping(config)))
        except (RuntimeError, ValueError):
            return None

    def _coverage(self, detail: dict, evidence_ids: list[str]) -> dict:
        statuses: dict[str, int] = {}
        for entry in detail["entries"]:
            statuses[entry["resolution_status"]] = statuses.get(entry["resolution_status"], 0) + 1
        return {
            "entryCount": len(detail["entries"]),
            "entryStatus": statuses,
            "keyTableCount": len(detail["keyTables"]),
            "retrievalLinkCount": len(detail["retrievalLinks"]),
            "evidenceCount": len(evidence_ids),
        }

    def _save_analysis(self, function_id, status, flow, rules, coverage, mode, message):
        analyzed_at = _now()
        self.db.execute(
            """INSERT INTO functional_analysis
               (function_id,status,flow_json,rules_json,coverage_json,mode,analyzed_at,message)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(function_id) DO UPDATE SET status=excluded.status,flow_json=excluded.flow_json,
                 rules_json=excluded.rules_json,coverage_json=excluded.coverage_json,mode=excluded.mode,
                 analyzed_at=excluded.analyzed_at,message=excluded.message""",
            (function_id, status, json.dumps(flow, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
             json.dumps(coverage, ensure_ascii=False), mode, analyzed_at, message[:500]),
        )
        self.db.commit()


class LangChainFunctionalAnalyzer:
    def __init__(self, model, *, agent_factory=None):
        self.model = model
        self.agent_factory = agent_factory

    def analyze(self, context: dict, allowed_evidence: set[str]) -> dict:
        factory = self.agent_factory
        if factory is None:
            from langchain.agents import create_agent
            factory = create_agent
        agent = factory(model=self.model, tools=[], response_format=_functional_schema(), system_prompt=_FUNCTION_PROMPT)
        result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}]})
        structured = result.get("structured_response") if isinstance(result, Mapping) else None
        if structured is None:
            raise ValueError("功能分析模型没有返回结构化结果")
        value = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
        if not isinstance(value, Mapping):
            raise ValueError("功能分析结果格式错误")
        return dict(value)


def parse_function_document(path: str | Path) -> FunctionalDocument:
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8-sig")
    metadata, body = _frontmatter(text)
    function_id = str(metadata.get("id") or "").strip()
    name = str(metadata.get("name") or "").strip()
    if not function_id or not name:
        raise ValueError(f"{path} 缺少 id 或 name")
    summary = _section(body, "功能说明").strip()
    scenarios = tuple(_bullets(_section(body, "业务场景")))
    entries = tuple(tuple(row[:3]) for row in _table(_section(body, "工程与入口"), 3))
    key_tables = tuple(tuple(row[:2]) for row in _table(_section(body, "关键表"), 2))
    if not summary:
        raise ValueError(f"{path} 缺少功能说明")
    if not entries:
        raise ValueError(f"{path} 至少登记一个工程入口")
    return FunctionalDocument(
        function_id, name, tuple(_list_value(metadata.get("aliases"))), tuple(_list_value(metadata.get("tags"))),
        summary, scenarios, entries, key_tables, _section(body, "补充说明").strip(), str(path), text,
    )


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("功能文档 frontmatter 未闭合")
    data: dict[str, Any] = {}
    current: str | None = None
    for raw in parts[1].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") and current:
            data.setdefault(current, []).append(line[1:].strip().strip("'\""))
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        current = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[current] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        elif value:
            data[current] = value.strip("'\"")
        else:
            data[current] = []
    return data, parts[2]


def _section(body: str, title: str) -> str:
    match = re.search(rf"^#+\s*{re.escape(title)}\s*$", body, re.MULTILINE)
    if not match:
        return ""
    rest = body[match.end():]
    end = re.search(r"^#+\s+", rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


def _bullets(value: str) -> list[str]:
    return [match.group(1).strip() for line in value.splitlines() if (match := re.match(r"\s*[-*]\s+(.+)", line))]


def _table(value: str, columns: int) -> list[list[str]]:
    rows = []
    for line in value.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < columns or all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        if cells[0] in {"工程", "表名"}:
            continue
        if all(cells[index] for index in range(columns)):
            rows.append(cells)
    return rows


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if value else []


def _validate_grounded_items(items: Any, allowed: set[str], label: str) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError(f"{label}必须是列表")
    values = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError(f"{label}条目格式错误")
        evidence = list(dict.fromkeys(str(value) for value in item.get("evidence_ids", [])))
        if not evidence or set(evidence) - allowed:
            raise ValueError(f"{label}存在没有有效代码证据的条目")
        values.append({**dict(item), "evidence_ids": evidence})
    return values


def _functional_schema():
    from pydantic import BaseModel, ConfigDict, Field

    class FlowStep(BaseModel):
        model_config = ConfigDict(extra="forbid")
        sequence: int = Field(ge=1)
        statement: str
        evidence_ids: list[str] = Field(min_length=1)

    class Rule(BaseModel):
        model_config = ConfigDict(extra="forbid")
        statement: str
        condition: str = ""
        result: str = ""
        evidence_ids: list[str] = Field(min_length=1)

    class Result(BaseModel):
        model_config = ConfigDict(extra="forbid")
        flow: list[FlowStep] = Field(default_factory=list, max_length=8)
        rules: list[Rule] = Field(default_factory=list, max_length=10)

    return Result


_FUNCTION_PROMPT = """你是功能代码分析 Agent。人工功能文档只提供业务语义和检索锚点。
根据给定的当前代码证据，生成简短的业务流程和核心业务规则，用于后续检索导航。
每条结论必须引用输入中存在的 evidence id；证据不足则不输出该条。
流程不超过 8 步，只描述有证据的主路径。规则只保留影响业务结果的条件、状态变化、限制或数据写入。
不要把日志、异常包装、通用技术代码写成业务规则，不要补充输入中不存在的调用关系。
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

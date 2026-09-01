from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import re
from typing import Any, Callable

from ..business_tools import BusinessTools
from ..requirement_tools import RequirementTools
from ..tools import EvidenceTools


@dataclass
class _ToolBundle:
    code: EvidenceTools
    business: BusinessTools
    requirement: RequirementTools


class QueryRetriever:
    """Deterministic three-source retrieval for the single M4 query agent.

    A shared SQLite connection is never moved to a worker thread.  Calls are
    safely serialized by default.  Supplying ``connection_factory`` enables
    true three-way parallel search; every worker obtains and closes its own
    connection.
    """

    def __init__(
        self,
        db,
        *,
        connection_factory: Callable[[], Any] | None = None,
        evidence_tools: EvidenceTools | None = None,
        business_tools: BusinessTools | None = None,
        requirement_tools: RequirementTools | None = None,
        candidate_limit: int = 20,
    ):
        self.db = db
        self.connection_factory = connection_factory
        self.tools = _ToolBundle(
            evidence_tools or EvidenceTools(db),
            business_tools or BusinessTools(db),
            requirement_tools or RequirementTools(db),
        )
        self.candidate_limit = candidate_limit

    def initial_search(self, understanding: Any, state: Any | None = None) -> dict:
        """Start from business knowledge and anchors, then use runtime code search.

        A global code search is used only for explicit code hints or when the
        question has no matching business card.  Once a business card exists,
        its maintained entry anchors are the investigation boundary; an
        unresolved anchor is reported to the agent rather than replaced by a
        broad search that can mix unrelated applications.
        """
        query = _search_query(understanding, state)
        fields = _values(understanding, state, "fields", "field_hints", "fieldHints")
        tables = _values(understanding, state, "tables", "table_hints", "tableHints")
        code_hints = _values(understanding, state, "code_hints", "codeHints")

        # Business search is the routing decision for this first pass.  The
        # repository can be large, so do not spend the initial budget on a
        # whole-code FTS scan when a maintained FLOW/CAPABILITY anchor exists.
        business_rows, business_calls = self._initial_business(self.tools.business, query)
        anchor_result = self._resolve_entry_anchors(business_rows)
        explicit_code_hint = bool(fields or tables or code_hints)
        use_global_code = explicit_code_hint or not business_rows
        if use_global_code:
            code_rows, code_calls = self._initial_code(self.tools.code, query, fields, tables, code_hints)
            code_rows = _dedupe([*anchor_result["candidates"], *code_rows], "evidence_id", "evidenceId", "id", "symbol_id", "symbolId")
        else:
            code_rows, code_calls = anchor_result["candidates"], []
        requirement_rows, requirement_calls = self._initial_requirement(self.tools.requirement, query)
        return {
            "search_query": query,
            "code_candidates": code_rows,
            "business_candidates": business_rows,
            "requirement_candidates": requirement_rows,
            "tool_calls": [*business_calls, *anchor_result["calls"], *code_calls, *requirement_calls],
            "parallel": False,
            "raw_evidence_loaded": False,
        }

    def expand(self, state: Any, gaps: list[Any] | None = None) -> dict:
        """Expand from deterministic relationships and explicit evidence targets."""
        gaps = list(gaps if gaps is not None else _get(state, "evidence_gaps", "evidenceGaps", default=[]))
        fields = set(_values(state, None, "field_hints", "fieldHints", "fields"))
        tables = set(_values(state, None, "table_hints", "tableHints", "tables"))
        symbols: set[str] = set()
        businesses: set[str] = set()
        requirements: set[str] = set()
        for gap in gaps:
            target = _get(gap, "target", "targetId", "target_id")
            gap_type = str(_get(gap, "type", default="")).upper()
            if isinstance(target, dict):
                target = _get(target, "id", "sourceId", "source_id")
            if target:
                target = str(target)
                if "FIELD" in gap_type:
                    fields.add(target)
                elif "TABLE" in gap_type or "COLUMN" in gap_type:
                    tables.add(target)
                elif "BUSINESS" in gap_type or target.startswith("BK-"):
                    businesses.add(target)
                elif "REQUIREMENT" in gap_type or target.startswith("REQ-"):
                    requirements.add(target)
                elif "CODE" in gap_type or target.startswith(("SYM-", "METHOD-", "API-")):
                    symbols.add(target)
                # Unknown gap targets are intentionally ignored.  A broad
                # text search would make the evidence loop look productive
                # while mixing unrelated code into the answer.

        for item in _as_list(_get(state, "code_candidates", "codeCandidates", default=[])):
            symbol = _get(item, "symbolId", "symbol_id", "id", "targetId", "target_id")
            if symbol:
                symbols.add(str(symbol))
        for item in _as_list(_get(state, "business_candidates", "businessCandidates", default=[])):
            value = _get(item, "id", "knowledgeId", "knowledge_id")
            if value:
                businesses.add(str(value))
        for item in _as_list(_get(state, "requirement_candidates", "requirementCandidates", default=[])):
            value = _get(item, "id", "requirementId", "requirement_id")
            if value:
                requirements.add(str(value))

        candidates = {"code": [], "business": [], "requirement": []}
        calls: list[dict] = []
        plan: list[dict] = []
        # 1. Field/Table relations.
        for field in sorted(fields):
            rows = self.tools.code.find_field_activity(field)
            candidates["code"].extend(_strip_raw(rows))
            calls.append(_call("find_field_activity", {"field": field}, rows, "CODE"))
            plan.append({"priority": 1, "strategy": "FIELD_RELATION", "target": field})
        for table in sorted(tables):
            rows = [*self.tools.code.find_table_reads(table), *self.tools.code.find_table_writes(table)]
            candidates["code"].extend(_strip_raw(rows))
            calls.append(_call("find_table_reads+writes", {"table": table}, rows, "CODE"))
            plan.append({"priority": 1, "strategy": "TABLE_RELATION", "target": table})

        # 2. Symbol relations.
        for symbol_id in sorted(symbols)[: self.candidate_limit]:
            try:
                rows = self.tools.code.get_symbol_relations(symbol_id)
            except KeyError:
                rows = []
            candidates["code"].extend({**row, "symbolId": symbol_id} for row in _strip_raw(rows))
            calls.append(_call("get_symbol_relations", {"symbolId": symbol_id}, rows, "CODE"))
            plan.append({"priority": 2, "strategy": "SYMBOL_RELATION", "target": symbol_id})
            edge_rows = self.tools.code.follow_integration_flow(symbol_id)
            candidates["code"].extend(_strip_raw(edge_rows))
            calls.append(_call("follow_integration_flow", {"symbolId": symbol_id}, edge_rows, "CODE"))
            if edge_rows:
                plan.append({"priority": 2, "strategy": "FOLLOW_INTEGRATION_EDGE", "target": symbol_id})
            local_targets = self.tools.code.resolve_local_calls(symbol_id)
            candidates["code"].extend(_strip_raw(local_targets))
            calls.append(_call("resolve_local_calls", {"symbolId": symbol_id}, local_targets, "CODE"))
            for target in local_targets:
                target_edges = self.tools.code.follow_integration_flow(target["id"])
                candidates["code"].extend(_strip_raw(target_edges))
                calls.append(_call("follow_integration_flow", {"symbolId": target["id"]}, target_edges, "CODE"))
                if target_edges:
                    plan.append({"priority": 2, "strategy": "FOLLOW_INTEGRATION_EDGE", "target": target["id"]})

        # 3. Confirmed/derived Business relations.
        for knowledge_id in sorted(businesses)[: self.candidate_limit]:
            try:
                detail = self.tools.business.get_business_knowledge(knowledge_id)
            except KeyError:
                continue
            card = detail.get("knowledge", {})
            candidates["business"].append(_business_summary(card))
            anchors = self._resolve_entry_anchors([{"id": knowledge_id}])
            candidates["code"].extend(anchors["candidates"])
            calls.extend(anchors["calls"])
            calls.append(_call("get_business_knowledge", {"knowledgeId": knowledge_id}, [*detail.get("relations", []), *detail.get("entryAnchors", [])], "BUSINESS"))
            plan.append({"priority": 3, "strategy": "BUSINESS_RELATION", "target": knowledge_id})
            if anchors["candidates"]:
                plan.extend(anchors["plan"])

        # 4. Requirement digest and explicit relations (still no chunks).
        for requirement_id in sorted(requirements)[: self.candidate_limit]:
            try:
                digest_value = self.tools.requirement.get_requirement_digest(requirement_id)
                relations = self.tools.requirement.find_requirement_code_relations(requirement_id)
            except KeyError:
                continue
            candidates["requirement"].append({"id": requirement_id, "digest": digest_value})
            candidates["code"].extend(_strip_raw(relations))
            calls.append(_call("get_requirement_digest", {"requirementId": requirement_id}, [digest_value], "REQUIREMENT"))
            calls.append(_call("find_requirement_code_relations", {"requirementId": requirement_id}, relations, "REQUIREMENT"))
            plan.append({"priority": 4, "strategy": "REQUIREMENT_RELATION", "target": requirement_id})

        return {
            "plan": plan,
            "code_candidates": _dedupe(candidates["code"], "evidence_id", "evidenceId", "symbolId", "symbol_id", "id", "target_id"),
            "business_candidates": _dedupe(candidates["business"], "id", "knowledge_id"),
            "requirement_candidates": _dedupe(candidates["requirement"], "id", "requirement_id"),
            "tool_calls": calls,
            "raw_evidence_loaded": False,
        }

    def _resolve_entry_anchors(self, business_rows: list[dict]) -> dict:
        """Resolve active human anchors into runtime-only code candidates."""
        candidates: list[dict] = []
        calls: list[dict] = []
        plan: list[dict] = []
        resolved = 0
        seen_anchors: set[str] = set()
        for business in business_rows:
            business_id = _get(business, "id", "knowledgeId", "knowledge_id", "sourceId", "source_id")
            if not business_id:
                continue
            anchors = self.tools.code.get_business_entry_anchors(str(business_id))
            calls.append(_call("get_business_entry_anchors", {"businessId": str(business_id)}, anchors, "BUSINESS"))
            for anchor in anchors:
                anchor_id = str(anchor.get("id") or repr(sorted(anchor.items())))
                if anchor_id in seen_anchors:
                    continue
                seen_anchors.add(anchor_id)
                application_id = str(anchor.get("applicationId") or "")
                entry_name = str(anchor.get("entryName") or "")
                resolution = self.tools.code.resolve_entry_anchor(application_id, entry_name)
                symbols = resolution.get("symbols", []) if isinstance(resolution, dict) else []
                calls.append(_call(
                    "resolve_entry_anchor",
                    {"applicationId": application_id, "entryName": entry_name},
                    symbols, "CODE",
                ))
                status = str(resolution.get("status") or "NOT_FOUND").upper()
                if status == "RESOLVED":
                    resolved += 1
                plan.append({
                    "priority": 2, "strategy": "ENTRY_ANCHOR",
                    "target": entry_name, "applicationId": application_id,
                    "resolution": status,
                })
                for symbol in symbols:
                    evidence_id = symbol.get("evidenceId") or symbol.get("evidence_id")
                    candidates.append({
                        "id": symbol.get("symbolId"),
                        "symbolId": symbol.get("symbolId"),
                        "symbol_id": symbol.get("symbolId"),
                        "kind": symbol.get("kind"),
                        "qualified_name": symbol.get("qualifiedName"),
                        "qualifiedName": symbol.get("qualifiedName"),
                        "application_id": application_id,
                        "applicationId": application_id,
                        "application_name": symbol.get("applicationName"),
                        "evidence_id": evidence_id,
                        "evidenceId": evidence_id,
                        "fact_type": symbol.get("factType") or symbol.get("fact_type"),
                        "factType": symbol.get("factType") or symbol.get("fact_type"),
                        "status": "DIRECT" if status == "RESOLVED" else "CANDIDATE",
                        "entry_anchor_id": anchor.get("id"),
                        "entryAnchorId": anchor.get("id"),
                        "entry_resolution": status,
                        "entryResolution": status,
                        "anchor_status": anchor.get("status"),
                        "anchor_application_id": application_id,
                        "anchor_entry_name": entry_name,
                    })
        return {
            "candidates": _dedupe(candidates, "evidence_id", "evidenceId", "symbol_id", "symbolId", "id"),
            "calls": calls,
            "plan": plan,
            "resolved": resolved,
            "hasAnchor": bool(seen_anchors),
        }

    def _initial_code(self, tools: EvidenceTools, query: str, fields: list[str], tables: list[str], hints: list[str]):
        candidates: list[dict] = []
        calls: list[dict] = []
        rows = tools.search_code(query, self.candidate_limit)
        candidates.extend(_strip_raw(rows))
        calls.append(_call("search_code", {"query": query}, rows, "CODE"))
        for hint in dict.fromkeys([*fields, *hints]):
            symbols = tools.search_symbol(hint)
            activity = tools.find_field_activity(hint)
            candidates.extend(_strip_raw(symbols))
            candidates.extend(_strip_raw(activity))
            calls.append(_call("search_symbol", {"query": hint}, symbols, "CODE"))
            calls.append(_call("find_field_reads+writes+checks", {"field": hint}, activity, "CODE"))
        for table in dict.fromkeys(tables):
            activity = [*tools.find_table_reads(table), *tools.find_table_writes(table)]
            candidates.extend(_strip_raw(activity))
            calls.append(_call("find_table_reads+writes", {"table": table}, activity, "CODE"))
        # One symbol can own several independently useful facts (for example a
        # read and a check), so fact evidence precedes symbol identity here.
        return _dedupe(candidates, "evidence_id", "evidenceId", "id", "symbol_id", "symbolId"), calls

    def _initial_business(self, tools: BusinessTools, query: str):
        rows, calls = [], []
        for variant in _query_variants(query):
            found = tools.search_business_knowledge(variant)
            rows.extend(found)
            calls.append(_call("search_business_knowledge", {"query": variant}, found, "BUSINESS"))
            if len(_dedupe(rows, "id", "knowledge_id")) >= self.candidate_limit:
                break
        rows = _dedupe(rows, "id", "knowledge_id")[: self.candidate_limit]
        return [_strip_one(item) for item in rows], calls

    def _initial_requirement(self, tools: RequirementTools, query: str):
        rows, calls = [], []
        for variant in _query_variants(query):
            found = tools.search_requirements(variant)
            rows.extend(found)
            calls.append(_call("search_requirements", {"query": variant}, found, "REQUIREMENT"))
            if len(_dedupe(rows, "id", "requirement_id")) >= self.candidate_limit:
                break
        rows = _dedupe(rows, "id", "requirement_id")[: self.candidate_limit]
        values = []
        for row in rows:
            item = _strip_one(row)
            try:
                item["digest"] = tools.get_requirement_digest(row["id"])
            except KeyError:
                pass
            values.append(item)
        if values:
            calls.append({"source": "REQUIREMENT", "tool": "get_requirement_digest", "input": {"ids": [row["id"] for row in rows]}, "resultCount": len(values)})
        return values, calls

    def _run_sources(self, operations: dict[str, Callable[[_ToolBundle], Any]]) -> dict:
        if self.connection_factory is None:
            return {name: operation(self.tools) for name, operation in operations.items()}

        def run(operation):
            with self._worker_bundle() as bundle:
                return operation(bundle)

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="query-retrieval") as executor:
            futures = {name: executor.submit(run, operation) for name, operation in operations.items()}
            return {name: future.result() for name, future in futures.items()}

    @contextmanager
    def _worker_bundle(self):
        resource = self.connection_factory()
        if hasattr(resource, "__enter__") and hasattr(resource, "__exit__"):
            try:
                with resource as db:
                    yield _ToolBundle(EvidenceTools(db), BusinessTools(db), RequirementTools(db))
            finally:
                # sqlite3.Connection.__exit__ commits or rolls back but does
                # not close the connection.  Worker-owned resources must not
                # survive the retrieval task.
                close = getattr(resource, "close", None)
                if close:
                    close()
            return
        try:
            yield _ToolBundle(EvidenceTools(resource), BusinessTools(resource), RequirementTools(resource))
        finally:
            close = getattr(resource, "close", None)
            if close:
                close()


def _search_query(understanding: Any, state: Any | None) -> str:
    terms: list[str] = []
    for value in (understanding, state):
        if value is None:
            continue
        for key in ("search_terms", "searchTerms", "business_objects", "businessObjects", "processes", "systems", "fields", "field_hints", "table_hints", "code_hints"):
            terms.extend(str(item) for item in _as_list(_get(value, key, default=[])) if str(item).strip())
    if not terms:
        terms.append(str(_get(understanding, "question", default=_get(state, "question", default=""))))
    return " ".join(dict.fromkeys(term.strip() for term in terms if term.strip()))


def _values(primary: Any, secondary: Any | None, *keys: str) -> list[str]:
    values = []
    for source in (primary, secondary):
        if source is None:
            continue
        for key in keys:
            values.extend(str(item) for item in _as_list(_get(source, key, default=[])) if str(item).strip())
    return list(dict.fromkeys(values))


def _strip_raw(rows) -> list[dict]:
    return [_strip_one(dict(row)) for row in rows]


def _strip_one(item: dict) -> dict:
    # Initial and expansion phases may carry provenance IDs and locations, but
    # never raw source/chunk text or stored excerpts.
    return {key: value for key, value in dict(item).items() if key not in {"content", "excerpt", "original_text"}}


def _business_summary(card: dict) -> dict:
    return {key: card.get(key) for key in ("id", "title", "statement", "status", "knowledge_type", "version") if key in card}


def _call(tool: str, tool_input: dict, rows, source: str) -> dict:
    return {"source": source, "tool": tool, "input": tool_input, "resultCount": len(rows)}


def _dedupe(items: list[dict], *keys: str) -> list[dict]:
    unique = {}
    for index, item in enumerate(items):
        identity = next((str(item[key]) for key in keys if item.get(key) is not None), None)
        identity = identity or repr(sorted(item.items()))
        if identity not in unique or _item_richness(item) > _item_richness(unique[identity]):
            unique[identity] = item
        if len(unique) >= 100:
            break
    return list(unique.values())


def _item_richness(item: dict) -> int:
    return sum(
        2 if isinstance(value, (list, tuple, set, dict)) and value else 1
        for value in item.values() if value not in (None, "", [], {})
    )


def _get(value: Any, *names: str, default=None):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    return list(value)


ThreeSourceRetriever = QueryRetriever


def _query_variants(query: str) -> list[str]:
    """Return bounded recall-oriented variants for unsegmented CJK queries.

    SQLite FTS cannot infer Chinese word boundaries.  The first attempt keeps
    the precise query; later attempts use explicit identifiers and CJK
    bigrams/unigrams from the same question.  This broadens recall without
    inventing repository names or business entities.
    """
    variants = [query.strip()]
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query)
    runs = re.findall(r"[\u4e00-\u9fff]+", query)
    preferred = ("申请", "提款", "还款", "校验", "生成", "规则", "需求", "流程", "阶段", "关系")
    fragments = [*identifiers]
    for run in runs:
        fragments.extend(term for term in preferred if term in run)
        fragments.extend(run[index:index + 2] for index in range(len(run) - 1))
    variants.extend(fragments)
    return list(dict.fromkeys(value for value in variants if len(value) > 1))[:16]

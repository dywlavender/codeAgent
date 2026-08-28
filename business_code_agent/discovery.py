from __future__ import annotations

from collections import Counter
from sqlite3 import Connection

from .tools import EvidenceTools


WRITE_TYPES = {"WRITE_FIELD", "WRITE_COLUMN"}
READ_TYPES = {"READ_FIELD", "READ_COLUMN"}
CHECK_TYPES = {"CHECK_FIELD"}


class RepositoryAnalyzer:
    """Generic repository inventory and field-lifecycle candidate discovery."""

    def __init__(self, db: Connection):
        self.db = db
        self.tools = EvidenceTools(db)

    def overview(self) -> dict:
        files = self.db.execute("SELECT count(*) FROM code_file").fetchone()[0]
        symbols = self.db.execute("SELECT count(*) FROM code_symbol").fetchone()[0]
        facts = self.db.execute("SELECT count(*) FROM code_fact").fetchone()[0]
        parse_errors = self.db.execute("SELECT count(*) FROM parse_diagnostic WHERE has_error=1").fetchone()[0]
        modules = Counter()
        for row in self.db.execute("SELECT path FROM code_file"):
            parts = row["path"].split("/")
            modules[parts[0] if len(parts) > 1 else "."] += 1
        fact_types = {
            row["fact_type"]: row["amount"]
            for row in self.db.execute("SELECT fact_type, count(*) amount FROM code_fact GROUP BY fact_type")
        }
        knowledge = {
            "requirements": self.db.execute("SELECT count(*) FROM requirement").fetchone()[0],
            "business_functions": self.db.execute(
                "SELECT count(*) FROM functional_knowledge WHERE status='ACTIVE'"
            ).fetchone()[0],
        }
        return {
            "files": files,
            "symbols": symbols,
            "facts": facts,
            "parse_errors": parse_errors,
            "modules": dict(sorted(modules.items())),
            "fact_types": fact_types,
            "knowledge_sources": knowledge,
        }

    def discover(self, limit: int | None = None, *, include_declared: bool = False) -> dict:
        candidates = [self._candidate(name) for name in self.tools.field_names()]
        declared_only_count = sum(item["classification"] == "DECLARED_ONLY" for item in candidates)
        if not include_declared:
            candidates = [item for item in candidates if item["classification"] != "DECLARED_ONLY"]
        candidates.sort(key=lambda item: (-item["score"], item["field"].lower()))
        if limit is not None:
            candidates = candidates[:limit]
        return {
            "overview": self.overview(),
            "candidates": candidates,
            "summary": dict(Counter(item["classification"] for item in candidates)),
            "declared_only_omitted": 0 if include_declared else declared_only_count,
        }

    def explain(self, field: str) -> dict:
        candidate = self._candidate(field)
        requirements = self.tools.search_requirements(field)
        business = self.tools.search_business(field)
        candidate["requirements"] = [item["id"] for item in requirements]
        candidate["business_functions"] = [item["id"] for item in business]
        candidate["explanation_level"] = self._explanation_level(candidate)
        candidate["gaps"] = self._gaps(candidate)
        return candidate

    def _candidate(self, field: str) -> dict:
        activity = self.tools.find_field_activity(field)
        writes = [item for item in activity if item["fact_type"] in WRITE_TYPES]
        reads = [item for item in activity if item["fact_type"] in READ_TYPES]
        checks = [item for item in activity if item["fact_type"] in CHECK_TYPES]
        declarations = [
            dict(row) for row in self.db.execute(
                """SELECT cs.id, cs.qualified_name, cs.line_start, cf.path locator
                     FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                    WHERE cs.kind='FIELD'"""
            )
            if self.tools.normalize_field(row["qualified_name"].rsplit(".", 1)[-1]) == self.tools.normalize_field(field)
        ]
        endpoints = writes + checks + reads + declarations
        modules = sorted({self._module(item["locator"]) for item in endpoints})
        if writes and checks:
            classification = "WRITE_AND_CHECK"
        elif writes and reads:
            classification = "WRITE_AND_READ"
        elif writes:
            classification = "WRITE_ONLY"
        elif checks:
            classification = "CHECK_ONLY"
        elif reads:
            classification = "READ_ONLY"
        else:
            classification = "DECLARED_ONLY"
        cross_module = len(modules) > 1
        score = len(writes) * 4 + len(checks) * 4 + len(reads) * 2 + len(declarations)
        if cross_module:
            score += 3
        return {
            "field": field,
            "classification": classification,
            "score": score,
            "cross_module": cross_module,
            "modules": modules,
            "declarations": declarations,
            "writes": writes,
            "reads": reads,
            "checks": checks,
        }

    @staticmethod
    def _module(locator: str) -> str:
        parts = locator.split("/")
        return parts[0] if len(parts) > 1 else "."

    @staticmethod
    def _explanation_level(candidate: dict) -> str:
        code_chain = bool(candidate["writes"] and (candidate["checks"] or candidate["reads"]))
        if code_chain and candidate["requirements"] and candidate["business_functions"]:
            return "EXPLAINED"
        if code_chain:
            return "CODE_CHAIN"
        if candidate["writes"] or candidate["checks"] or candidate["reads"]:
            return "CODE_FACTS"
        return "DECLARATION_ONLY"

    @staticmethod
    def _gaps(candidate: dict) -> list[str]:
        gaps = []
        if not candidate["writes"]:
            gaps.append("未发现写入证据")
        if not candidate["checks"]:
            gaps.append("未发现校验证据")
        if not candidate["requirements"]:
            gaps.append("未导入或未命中需求依据（可选增强）")
        if not candidate["business_functions"]:
            gaps.append("未命中功能知识文档（可选增强）")
        return gaps

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection

from .code_intelligence import JavaIndexer, SKIP_DIRS
from .util import digest, stable_id


WEB_SUFFIXES = {".vue", ".ts", ".tsx", ".js", ".jsx"}
FUNCTION_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
OPTION_METHOD_RE = re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", re.MULTILINE)
UI_EVENT_RE = re.compile(r"@(click|submit|change|confirm)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
HTTP_CALL_RE = re.compile(
    r"\b(?:axios|request|http|api|client)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*([\"'`])([^\"'`]+)\2",
    re.IGNORECASE,
)
FETCH_RE = re.compile(
    r"\bfetch\s*\(\s*([\"'`])([^\"'`]+)\1\s*(?:,\s*\{(?P<options>.*?)\})?",
    re.IGNORECASE | re.DOTALL,
)
ROUTE_PATH_RE = re.compile(r"\bpath\s*:\s*([\"'])(/[^\"']*)\1")
ROUTE_COMPONENT_RE = re.compile(r"\bcomponent\s*:\s*([A-Za-z_$][\w$]*)")
CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")


class WebIndexer(JavaIndexer):
    """Conservative Vue/JS/TS indexer for user entry points and HTTP exits."""

    def __init__(self, db: Connection):
        self.db = db
        self.syntax_backend = None

    def ingest(self, root: str, repository_id: str = "repo-main") -> dict[str, int]:
        root_path = Path(root).resolve()
        self.db.execute(
            "INSERT OR REPLACE INTO repository VALUES (?, ?, ?)",
            (repository_id, str(root_path), datetime.now(timezone.utc).isoformat()),
        )
        changed = 0
        for path in sorted(root_path.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in WEB_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root_path).parts):
                continue
            changed += self._ingest_web_file(repository_id, root_path, path)["files"]
        self._remove_deleted(repository_id, root_path)
        self.db.commit()
        return {
            "files": changed,
            "symbols": self.db.execute(
                """SELECT count(*) FROM code_symbol cs JOIN code_file cf ON cf.id=cs.file_id
                     WHERE cf.repository_id=? AND (
                       lower(cf.path) LIKE '%.vue' OR lower(cf.path) LIKE '%.ts' OR
                       lower(cf.path) LIKE '%.tsx' OR lower(cf.path) LIKE '%.js' OR
                       lower(cf.path) LIKE '%.jsx')""",
                (repository_id,),
            ).fetchone()[0],
            "facts": self.db.execute(
                """SELECT count(*) FROM code_fact f JOIN code_symbol cs ON cs.id=f.symbol_id
                     JOIN code_file cf ON cf.id=cs.file_id
                     WHERE cf.repository_id=? AND f.fact_type IN ('ROUTE','UI_EVENT','CALL','HTTP_CALL')
                       AND (lower(cf.path) LIKE '%.vue' OR lower(cf.path) LIKE '%.ts' OR
                            lower(cf.path) LIKE '%.tsx' OR lower(cf.path) LIKE '%.js' OR
                            lower(cf.path) LIKE '%.jsx')""",
                (repository_id,),
            ).fetchone()[0],
        }

    def _ingest_web_file(self, repository_id: str, root: Path, path: Path) -> dict[str, int]:
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        file_id = stable_id("CF", repository_id, relative)
        current_digest = digest(content)
        old = self.db.execute("SELECT content_hash FROM code_file WHERE id=?", (file_id,)).fetchone()
        declarations_indexed = self.db.execute(
            """SELECT 1 FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
                 WHERE s.file_id=? AND f.fact_type='CODE_DECLARATION' LIMIT 1""",
            (file_id,),
        ).fetchone()
        declaration_expected = bool(FUNCTION_RE.search(content) or OPTION_METHOD_RE.search(content))
        if old and old[0] == current_digest and (not declaration_expected or declarations_indexed):
            return {"files": 0, "symbols": 0, "facts": 0}
        if old:
            self._archive_file_evidence(file_id, "SOURCE_MODIFIED", relative)
            self._mark_file_relations_stale(file_id, "SOURCE_MODIFIED", relative)
        self.db.execute("DELETE FROM code_fact WHERE symbol_id IN (SELECT id FROM code_symbol WHERE file_id=?)", (file_id,))
        self.db.execute("DELETE FROM code_symbol WHERE file_id=?", (file_id,))
        self.db.execute(
            "INSERT OR REPLACE INTO code_file VALUES (?, ?, ?, ?)",
            (file_id, repository_id, relative, current_digest),
        )
        self._record_change(repository_id, relative, old[0] if old else None, current_digest)
        self.db.execute(
            "INSERT OR REPLACE INTO parse_diagnostic VALUES (?, 'web-conservative-pattern', 0, 'web-module', ?)",
            (file_id, datetime.now(timezone.utc).isoformat()),
        )

        lines = content.splitlines() or [""]
        module_name = path.stem
        module_kind = "PAGE" if any(part.lower() in {"page", "pages", "view", "views"} for part in path.parts) else "COMPONENT"
        module_qualified = f"{relative}::{module_name}"
        module_id = stable_id("SYM", file_id, module_qualified, "1")
        self._symbol(module_id, file_id, module_kind, module_qualified, module_name, 1, len(lines))

        starts: dict[tuple[int, str], None] = {}
        for match in FUNCTION_RE.finditer(content):
            starts[(_line_number(content, match.start()), match.group(1) or match.group(2))] = None
        for match in OPTION_METHOD_RE.finditer(content):
            name = match.group(1)
            if name not in {"if", "for", "while", "switch", "catch", "function"}:
                starts[(_line_number(content, match.start()), name)] = None
        ordered = sorted(starts)
        functions: list[tuple[int, int, str, str]] = []
        for index, (start, name) in enumerate(ordered):
            end = ordered[index + 1][0] - 1 if index + 1 < len(ordered) else len(lines)
            qualified = f"{relative}::{name}"
            symbol_id = stable_id("SYM", file_id, qualified, str(start))
            self._symbol(symbol_id, file_id, "METHOD", qualified, name, start, max(start, end))
            self._fact(
                (symbol_id, qualified), "CODE_DECLARATION", "METHOD", qualified,
                file_id, relative, start, lines[start - 1].strip(),
            )
            functions.append((start, max(start, end), symbol_id, qualified))

        facts = len(functions)
        for match in UI_EVENT_RE.finditer(content):
            line = _line_number(content, match.start())
            handler_match = re.search(r"[A-Za-z_$][\w$]*", match.group(2))
            if not handler_match:
                continue
            excerpt = match.group(0)
            self._fact(
                (module_id, module_qualified), "UI_EVENT", match.group(1).upper(), handler_match.group(0),
                file_id, relative, line, excerpt,
            )
            facts += 1

        for match in HTTP_CALL_RE.finditer(content):
            facts += self._http_fact(match.group(1), match.group(3), match.start(), match.group(0), functions, module_id, module_qualified, file_id, relative, content)
        for match in FETCH_RE.finditer(content):
            options = match.group("options") or ""
            method_match = re.search(r"\bmethod\s*:\s*[\"']([A-Za-z]+)[\"']", options, re.IGNORECASE)
            method = method_match.group(1) if method_match else "GET"
            facts += self._http_fact(method, match.group(2), match.start(), match.group(0), functions, module_id, module_qualified, file_id, relative, content)

        for match in ROUTE_PATH_RE.finditer(content):
            window = content[match.end():match.end() + 500]
            component = ROUTE_COMPONENT_RE.search(window)
            line = _line_number(content, match.start())
            end_offset = match.end() + (component.end() if component else 0)
            excerpt = content[match.start():end_offset]
            end_line = _line_number(content, max(match.start(), end_offset - 1))
            self._fact(
                (module_id, module_qualified), "ROUTE", match.group(2), component.group(1) if component else module_name,
                file_id, relative, line, excerpt, end_line=end_line,
            )
            facts += 1

        for start, end, symbol_id, qualified in functions:
            source = "\n".join(lines[start - 1:end])
            for match in CALL_RE.finditer(source):
                name = match.group(1)
                if name in {"if", "for", "while", "switch", "catch", "fetch"}:
                    continue
                line = start + source[:match.start()].count("\n")
                excerpt = lines[line - 1].strip()
                self._fact((symbol_id, qualified), "CALL", name, name, file_id, relative, line, excerpt)
                facts += 1
        return {"files": 1, "symbols": 1 + len(functions), "facts": facts}

    def _http_fact(
        self, method: str, path: str, offset: int, excerpt: str,
        functions: list[tuple[int, int, str, str]], module_id: str, module_qualified: str,
        file_id: str, relative: str, content: str,
    ) -> int:
        if "${" in path:
            return 0
        line = _line_number(content, offset)
        owner = next(((sid, qualified) for start, end, sid, qualified in functions if start <= line <= end), None)
        symbol = owner or (module_id, module_qualified)
        self._fact(symbol, "HTTP_CALL", method.upper(), _normalize_path(path), file_id, relative, line, excerpt)
        return 1


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _normalize_path(value: str) -> str:
    path = value.strip().split("?", 1)[0]
    return "/" + path.strip("/") if path.strip("/") else "/"

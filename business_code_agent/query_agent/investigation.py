"""Runtime state for a model-led source investigation.

The index is a navigation aid.  A source reference is created only after the
agent has actually called ``read_source``.  Raw source is kept in this object
while the model is running and is deliberately omitted from the metadata
projection persisted in query checkpoints.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Mapping


class SourceReadLedger:
    """Collect source reads and tool activity for one query run.

    Reference IDs are query-local and human-readable (``SRC-001``).  They are
    not code facts: the code symbol, file and line range remain the authority
    for a citation, while the ledger proves that the range was read during the
    current investigation.
    """

    def __init__(self, *, max_reads: int = 24, max_source_chars: int = 80_000, max_tool_calls: int = 48):
        self.max_reads = max(1, int(max_reads))
        self.max_source_chars = max(1, int(max_source_chars))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self._lock = Lock()
        self._references: list[dict[str, Any]] = []
        self._by_symbol: dict[str, dict[str, Any]] = {}
        self._business_evidence_ids: set[str] = set()
        self._tool_calls: list[dict[str, Any]] = []
        self._source_characters = 0

    @property
    def references(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._references]

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._tool_calls]

    @property
    def source_characters(self) -> int:
        with self._lock:
            return self._source_characters

    @property
    def business_evidence_ids(self) -> set[str]:
        with self._lock:
            return set(self._business_evidence_ids)

    def register_business_results(self, rows: Any) -> None:
        """Make evidence IDs returned by business search valid citations."""
        values = rows if isinstance(rows, list) else [rows]
        with self._lock:
            for row in values:
                if not isinstance(row, Mapping):
                    continue
                evidence_id = row.get("evidence_id") or row.get("evidenceId") or row.get("referenceId")
                if evidence_id:
                    self._business_evidence_ids.add(str(evidence_id))

    def record_source(self, source: Mapping[str, Any], symbol_id: str) -> dict[str, Any]:
        """Record a successful source read and return the reference metadata.

        Re-reading the same symbol returns the original reference.  Source
        content is attached only to the runtime copy; ``metadata()`` strips it
        before the result is written to SQLite.
        """
        symbol_id = str(symbol_id or "").strip()
        if not symbol_id:
            raise ValueError("symbol_id is required")
        content = str(source.get("content") or "")
        with self._lock:
            previous = self._by_symbol.get(symbol_id)
            if previous is not None:
                return dict(previous)
            if len(self._references) >= self.max_reads:
                raise RuntimeError("source read budget exhausted")
            remaining = self.max_source_chars - self._source_characters
            if remaining <= 0:
                raise RuntimeError("source character budget exhausted")
            truncated = len(content) > remaining
            if len(content) > remaining:
                content = content[:remaining]
            reference_id = f"SRC-{len(self._references) + 1:03d}"
            reference = {
                "referenceId": reference_id,
                "sourceType": "CODE",
                "sourceId": symbol_id,
                "qualifiedName": str(source.get("qualified_name") or source.get("qualifiedName") or ""),
                "file": str(source.get("path") or source.get("file") or ""),
                "startLine": _int_or_none(source.get("line_start", source.get("startLine"))),
                "endLine": _int_or_none(source.get("line_end", source.get("endLine"))),
                "truncated": truncated,
                "content": content,
            }
            self._references.append(reference)
            self._by_symbol[symbol_id] = reference
            self._source_characters += len(content)
            return dict(reference)

    def record_tool(self, name: str, tool_input: Mapping[str, Any] | None, result: Any) -> None:
        """Record a compact, raw-content-free tool trace."""
        with self._lock:
            if len(self._tool_calls) >= self.max_tool_calls:
                return
            if isinstance(result, list):
                result_count = len(result)
            elif isinstance(result, Mapping) and isinstance(result.get("symbols"), list):
                result_count = len(result["symbols"])
            elif result is None:
                result_count = 0
            else:
                result_count = 1
            self._tool_calls.append({
                "tool": str(name),
                "input": _safe_input(tool_input or {}),
                "resultCount": result_count,
            })

    def metadata(self) -> list[dict[str, Any]]:
        """Return source references safe for answer/state persistence."""
        with self._lock:
            return [{key: value for key, value in item.items() if key != "content"} for item in self._references]

    def runtime_references(self) -> list[dict[str, Any]]:
        """Return references including source content for the current response."""
        with self._lock:
            values = []
            for item in self._references:
                value = dict(item)
                value["evidenceId"] = item["referenceId"]
                value["location"] = {
                    "file": item.get("file", ""),
                    "startLine": item.get("startLine"),
                    "endLine": item.get("endLine"),
                }
                value["symbol"] = item.get("qualifiedName")
                value["relationType"] = "SOURCE_READ"
                values.append(value)
            return values

    def valid_reference_ids(self) -> set[str]:
        with self._lock:
            return {str(item["referenceId"]) for item in self._references} | set(self._business_evidence_ids)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_input(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep tool traces useful without allowing source text into telemetry."""
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key.casefold() in {"content", "excerpt", "original_text", "source"}:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = item
        elif isinstance(item, list):
            result[str(key)] = [str(value)[:120] for value in item[:24]]
        else:
            result[str(key)] = str(item)[:240]
    return result

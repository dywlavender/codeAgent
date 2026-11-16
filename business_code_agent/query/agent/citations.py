"""Runtime-neutral citation checks against delivered read results.

A verifier only proves that a cited path and line interval were present in
tool output delivered during this run. It is not semantic proof that the code
supports the claim.
"""

from __future__ import annotations

import re


CITATION = re.compile(
    r"([A-Za-z0-9_./-]+\.(?:java|kt|scala|groovy|py|go|rs|cs|cpp|cxx|c|hpp|hxx|h|"
    r"jsx|js|tsx|ts|vue|html|xml|sql|yml|yaml|properties|json|toml|gradle|md|txt))"
    r"(?![A-Za-z0-9])(?::(\d+)(?:-(\d+))?)?"
)


def verify_references(answer: str, reads, *, workspace=None) -> dict:
    """Check cited paths and line ranges against this run's delivered reads."""
    by_path: dict[str, list[dict]] = {}
    for data in reads:
        if not isinstance(data, dict) or data.get("error") or not data.get("content"):
            continue
        path = data.get("path")
        if not path:
            continue
        by_path.setdefault(str(path), []).append(data)
    root = str(workspace).rstrip("/") if workspace else ""
    references = []
    for match in CITATION.finditer(answer or ""):
        cited = match.group(1)
        if root:
            candidate = cited if cited.startswith("/") else "/" + cited
            if candidate.startswith(root + "/"):
                cited = candidate[len(root) + 1:]
        cited = cited.lstrip("/")
        if cited.startswith("./"):
            cited = cited[2:]
        line = int(match.group(2)) if match.group(2) else None
        line_end = int(match.group(3)) if match.group(3) else line
        matched_path = match_reference_path(cited, by_path)
        confirmed = bool(matched_path and line is not None
                         and delivered(by_path[matched_path], line, line_end))
        references.append({
            "path": cited,
            "kind": reference_kind(matched_path or cited),
            "line": line,
            "lineEnd": line_end,
            "matchedPath": matched_path,
            "fileRead": matched_path is not None,
            "confirmed": confirmed,
        })
    return {
        "semanticProof": False,
        "references": references,
        "unconfirmed": [reference_text(reference) for reference in references
                        if not reference["confirmed"]],
        "readFiles": sorted(by_path),
    }


def match_reference_path(cited: str, by_path) -> str | None:
    if cited in by_path:
        return cited
    candidates = [path for path in by_path if path.endswith("/" + cited)]
    if len(candidates) == 1:
        return candidates[0]
    if "..." not in cited and "…" not in cited:
        return None
    pattern = cited.replace("…", "...")
    if not pattern.replace(".", "").strip("/"):
        return None
    expression = re.escape(pattern).replace(r"\.\.\.", ".*")
    matcher = re.compile(r"(?:^|/)" + expression + r"$")
    matches = [path for path in by_path if matcher.search(path)]
    return matches[0] if len(matches) == 1 else None


def reference_kind(path: str) -> str:
    if path.startswith("knowledge/business-context/"):
        return "business_context"
    if path.startswith("requirements/"):
        return "requirements"
    return "source"


def reference_text(reference) -> str:
    path, line, line_end = reference["path"], reference["line"], reference["lineEnd"]
    if line is None:
        return path
    return f"{path}:{line}" if line_end == line else f"{path}:{line}-{line_end}"


def delivered(read_results, start, end) -> bool:
    """Whether the cited line interval was fully present in delivered output."""
    low, high = sorted((start, end))
    intervals = sorted(
        (item["lineStart"], item["lineEnd"]) for item in read_results
        if item.get("lineStart") is not None and item.get("lineEnd") is not None)
    cursor = low
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = max(cursor, interval_end + 1)
        if cursor > high:
            return True
    return False

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
MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)\)")
_URL = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)
_ABSOLUTE_PREFIXES = ("private/", "tmp/", "Users/", "var/", "home/", "opt/", "root/",
                      "workspaces/")
_MATERIAL_ROOTS = ("repos/", "requirements/", "knowledge/")


def canonical_citation_path(value: str, workspace=None) -> str:
    """Return a project-relative path when a citation can be normalized.

    Besides stripping the run workspace, this recovers absolute paths that lost
    a segment or repeated ``repos/...`` after the model copied a temporary path.
    Unrelated relative paths are returned unchanged.
    """
    text = str(value or "").replace("\\", "/").strip().strip("<>").strip("`")
    if not text:
        return text
    if text.startswith("./"):
        text = text[2:]
    root = str(workspace or "").replace("\\", "/").rstrip("/") if workspace else ""
    if root:
        for prefix in (root + "/", root.lstrip("/") + "/"):
            if prefix != "/" and text.startswith(prefix):
                text = text[len(prefix):]
                break
    absolute_like = (text.startswith("/") or text.startswith(_ABSOLUTE_PREFIXES)
                     or "/workspaces/" in text)
    if absolute_like:
        for marker in _MATERIAL_ROOTS:
            index = text.rfind(marker)
            if index >= 0:
                text = text[index:]
                break
        else:
            text = text.lstrip("/")
    # A duplicated workspace prefix can survive the strip above and leave
    # repos/<project>/.../repos/<repo>/...; keep the innermost material root.
    if text.startswith("repos/"):
        prefix, marker, rest = text.partition("/repos/")
        if marker and ("/CONV-" in prefix or prefix.rstrip("/").endswith("/pi")):
            text = "repos/" + rest
    return text


def match_reference_path(cited: str, by_path) -> str | None:
    if not cited:
        return None
    if cited in by_path:
        return cited
    candidates = [path for path in by_path if path.endswith("/" + cited)]
    if len(candidates) == 1:
        return candidates[0]
    reverse = [path for path in by_path if cited.endswith("/" + path)]
    if len(reverse) == 1:
        return reverse[0]
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
    if path.startswith("knowledge/system-guides/"):
        return "system_guide"
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


def _citation(match, source: str) -> dict:
    line = int(match.group(2)) if match.group(2) else None
    return {
        "path": match.group(1),
        "line": line,
        "lineEnd": int(match.group(3)) if match.group(3) else line,
        "source": source,
    }


def _inside_url(text: str, start: int) -> bool:
    return "://" in text[max(0, start - 12):start]


def _citation_groups(answer: str) -> list[list[dict]]:
    """Group candidate citations so one markdown link yields one reference.

    A link target is preferred; its label remains as a fallback when the target
    is malformed or cannot be matched to delivered output.
    """
    answer = answer or ""
    links = list(MARKDOWN_LINK.finditer(answer))
    groups = []
    for link in links:
        label = link.group("label") or ""
        target = (link.group("target") or "").strip()
        candidates = []
        if target and not _URL.match(target):
            match = CITATION.search(target)
            if match:
                candidates.append(_citation(match, "link"))
        match = CITATION.search(label)
        if match:
            candidates.append(_citation(match, "label"))
        if candidates:
            groups.append(candidates)
    spans = [link.span() for link in links]
    for match in CITATION.finditer(answer):
        if any(start <= match.start() < end for start, end in spans):
            continue
        if _inside_url(answer, match.start()):
            continue
        groups.append([_citation(match, "text")])
    return groups


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

    records: dict[tuple, dict] = {}
    for group in _citation_groups(answer or ""):
        chosen = None
        for candidate in group:
            canonical = canonical_citation_path(candidate["path"], workspace)
            if not canonical:
                continue
            matched = match_reference_path(canonical, by_path)
            if matched:
                chosen = (candidate, canonical, matched)
                break
        if chosen is None:
            candidate = next((item for item in group if item["path"]), group[0])
            canonical = canonical_citation_path(candidate["path"], workspace)
            chosen = (candidate, canonical, None)
        candidate, canonical, matched = chosen
        line = candidate["line"]
        line_end = candidate["lineEnd"]
        if line is None:
            fallback = next((item for item in group if item["line"] is not None), None)
            if fallback:
                line, line_end = fallback["line"], fallback["lineEnd"]
        confirmed = bool(matched and line is not None
                         and delivered(by_path[matched], line, line_end))
        key = (matched or canonical, line, line_end)
        raw_paths = [item["path"] for item in group if item["path"]]
        record = records.get(key)
        if record is None:
            records[key] = {
                "path": canonical or candidate["path"],
                "kind": reference_kind(matched or canonical or ""),
                "line": line,
                "lineEnd": line_end,
                "matchedPath": matched,
                "fileRead": matched is not None,
                "confirmed": confirmed,
                "rawPaths": raw_paths,
            }
        else:
            record["rawPaths"] = sorted(set(record["rawPaths"]) | set(raw_paths))
            if confirmed and not record["confirmed"]:
                record.update({"confirmed": True, "matchedPath": matched,
                               "fileRead": True, "path": canonical or candidate["path"]})
    references = list(records.values())
    return {
        "semanticProof": False,
        "references": references,
        "unconfirmed": [reference_text(reference) for reference in references
                        if not reference["confirmed"]],
        "readFiles": sorted(by_path),
    }


def normalize_answer_paths(answer: str, reads=(), *, workspace=None) -> str:
    """Rewrite temporary absolute citation paths to project-relative paths.

    Only non-canonical paths (absolute, lost-root or ``./``) are rewritten, so
    short labels and normal relative citations remain unchanged.
    """
    text = str(answer or "")
    known = {str(data.get("path")) for data in reads or ()
             if isinstance(data, dict) and data.get("path")}
    replacements = []
    for match in CITATION.finditer(text):
        if _inside_url(text, match.start()):
            continue
        raw = match.group(1)
        non_canonical = (raw.startswith(("/", "./"))
                         or raw.startswith(_ABSOLUTE_PREFIXES)
                         or "/workspaces/" in raw)
        if not non_canonical:
            continue
        canonical = canonical_citation_path(raw, workspace)
        if not canonical:
            continue
        matched = match_reference_path(canonical, known) if known else None
        replacement = matched or canonical
        if replacement and replacement != raw:
            replacements.append((match.start(1), match.end(1), replacement))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text

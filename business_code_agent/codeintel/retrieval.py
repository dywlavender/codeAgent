"""A reproducible text retrieval baseline over an explicitly prepared corpus.

This module never reads an answer rubric, generates AST, or calls a model.
Other retrievers can return the same passage format to the evaluation layer.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

SOURCE_SUFFIXES = {".java", ".kt", ".scala", ".py", ".js", ".jsx", ".ts", ".tsx",
                   ".vue", ".html", ".xml", ".sql", ".md", ".txt", ".json",
                   ".yaml", ".yml", ".properties", ".toml", ".gradle"}
IGNORED_DIRS = {".git", ".data", "node_modules", "target", "build", "dist", ".venv",
                "__pycache__", ".idea", ".gradle", "vendor"}


def terms(text: str) -> list[str]:
    """Keep identifiers, their parts, and Chinese bigrams without a dependency."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    values = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", text.casefold())
    result = []
    for value in values:
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            result.extend(value[i:i + 2] for i in range(len(value) - 1))
            if len(value) == 1:
                result.append(value)
        else:
            result.append(value)
    return result


def collect_documents(
    sources: list[dict], *, previous=None, observations=None
) -> tuple[list[dict], list[dict]]:
    """Read originals; explicit incremental builds may reuse unchanged files."""
    documents, skipped = [], []
    for source in sources:
        root = Path(source["root"]).resolve()
        if not root.is_dir():
            raise ValueError(f"资料目录不存在：{source['id']}")
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix.lower() not in SOURCE_SUFFIXES or name.startswith("."):
                    continue
                relative = path.relative_to(root).as_posix()
                if not path.resolve().is_relative_to(root):
                    skipped.append({"sourceId": source["id"], "path": relative, "reason": "outside_source"})
                    continue
                stat = path.stat()
                if stat.st_size > 2_000_000:
                    skipped.append({"sourceId": source["id"], "path": relative, "reason": "file_too_large"})
                    continue
                try:
                    key = source['id'] + '/' + relative
                    stamp = [stat.st_mtime_ns, stat.st_size]
                    if (previous and previous.get('sourceObservations', {}).get(key) == stamp
                            and key in previous['files']):
                        content = previous['files'][key]
                    else:
                        content = path.read_text(encoding="utf-8-sig")
                except (UnicodeError, OSError) as exc:
                    skipped.append({"sourceId": source["id"], "path": relative, "reason": type(exc).__name__})
                    continue
                if observations is not None:
                    observations[key] = stamp
                documents.append({"sourceId": source["id"], "kind": source["kind"],
                                  "path": relative, "content": content})
    return documents, skipped


def passages(documents: list[dict], *, max_lines: int = 70) -> list[dict]:
    """Preserve Markdown sections; use bounded overlapping source windows.

    Source windows are a deliberately simple baseline, not a language parser.
    The surrounding declarations and conditions remain available in the corpus.
    """
    result = []
    for document in documents:
        lines = document["content"].splitlines()
        boundaries = [0]
        if document["path"].endswith(".md"):
            boundaries += [i for i, line in enumerate(lines) if i and re.match(r"^#{1,6}\s", line)]
        boundaries.append(len(lines))
        for begin, end in zip(boundaries, boundaries[1:]):
            start = begin
            while start < end:
                stop = min(start + max_lines, end)
                content = "\n".join(lines[start:stop])
                if content.strip():
                    result.append({"id": f"{document['sourceId']}:{document['path']}:{start + 1}-{stop}",
                                   "sourceId": document["sourceId"], "kind": document["kind"],
                                   "path": document["path"], "lineStart": start + 1, "lineEnd": stop,
                                   "content": content})
                if stop == end:
                    break
                start = stop - 12
    return result


class TextRetriever:
    """BM25 baseline; lexical retrieval is not advertised as semantic search."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.postings = defaultdict(list)
        self.lengths = []
        for number, item in enumerate(items):
            counts = Counter(terms(item["path"] + "\n" + item["content"]))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((number, count))
        self.average = sum(self.lengths) / max(1, len(items))

    def search(self, question: str, *, limit: int = 40) -> list[dict]:
        scores = defaultdict(float)
        count = len(self.items)
        for term in set(terms(question)):
            posting = self.postings.get(term, [])
            weight = math.log(1 + (count - len(posting) + 0.5) / (len(posting) + 0.5))
            for number, frequency in posting:
                length_factor = 1.2 * (0.25 + 0.75 * self.lengths[number] / max(1, self.average))
                scores[number] += weight * frequency * 2.2 / (frequency + length_factor)
        ordered = sorted(scores, key=lambda number: (-scores[number], self.items[number]["id"]))[:limit]
        return [{**self.items[number], "score": round(scores[number], 5)} for number in ordered]


CONTEXT_POLICIES = ("ranked", "diverse", "focused", "whole")
CONTEXT_POLICY_VERSIONS = {"ranked": 1, "diverse": 1, "focused": 2, "whole": 1}


def candidate_chars(candidates: list[dict]) -> int:
    return sum(len(f"[{p['kind']}] {p['sourceId']}/{p['path']}:{p['lineStart']}-{p['lineEnd']}\n") + len(p["content"])
               for p in candidates) + max(0, len(candidates) - 1) * 2


def candidate_prefix(candidates: list[dict], budget: int) -> list[dict]:
    """Keep a ranked prefix within a raw candidate-text budget, without cuts."""
    result = []
    used = 0
    for item in candidates:
        size = candidate_chars([item]) + (2 if result else 0)
        if used + size > budget:
            break
        result.append(item)
        used += size
    return result


def _diverse_order(candidates: list[dict]) -> list[dict]:
    """Balance supplied relevance and text overlap, without evidence labels.

    This experimental ordering only sees the supplied passages. It performs
    no search, source expansion, model call, or structure generation.
    """
    if not candidates:
        return []
    counts = [Counter(terms(item["content"])) for item in candidates]
    frequencies = Counter(term for count in counts for term in count)
    vectors = []
    for count in counts:
        weights = {term: (1 + math.log(frequency)) * math.log(1 + len(counts) / frequencies[term])
                   for term, frequency in count.items()}
        norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1
        vectors.append({term: weight / norm for term, weight in weights.items()})
    scores = [float(item.get("score", 1 / math.log2(index + 2))) for index, item in enumerate(candidates)]
    maximum = max(scores) or 1
    relevance = [score / maximum for score in scores]
    remaining = set(range(len(candidates)))
    similarity = [0.0] * len(candidates)
    ordered = []
    while remaining:
        chosen = max(remaining, key=lambda index: (.7 * relevance[index] - .3 * similarity[index], -index))
        remaining.remove(chosen)
        ordered.append(candidates[chosen])
        for index in remaining:
            overlap = sum(weight * vectors[index].get(term, 0) for term, weight in vectors[chosen].items())
            similarity[index] = max(similarity[index], overlap)
    return ordered


def _merge_sections(items: list[dict]) -> tuple[list[dict], str]:
    """Join selected adjacent lines; never fill a gap with unselected text."""
    files = {}
    for item in items:
        key = (item["sourceId"], item["path"])
        entry = files.setdefault(key, {"item": item, "lines": {}})
        entry["lines"].update(enumerate(item["content"].split("\n"), item["lineStart"]))
    selected, blocks = [], []
    for entry in files.values():
        item, source_lines = entry["item"], entry["lines"]
        positions = sorted(source_lines)
        begin = 0
        while begin < len(positions):
            end = begin + 1
            while end < len(positions) and positions[end] == positions[end - 1] + 1:
                end += 1
            first, last = positions[begin], positions[end - 1]
            body = "\n".join(source_lines[n] for n in positions[begin:end])
            selected.append({**item, "id": f"{item['sourceId']}:{item['path']}:{first}-{last}",
                             "lineStart": first, "lineEnd": last, "content": body})
            blocks.append(f"[{item['kind']}] {item['sourceId']}/{item['path']}:{first}-{last}\n{body}")
            begin = end
    return selected, "\n\n".join(blocks)


def _focused_context(candidates: list[dict], budget: int, question: str) -> tuple[list[dict], str]:
    """Choose blank-line sections within candidates, without parsing symbols.

    Original candidate positions remain the search boundary. Paragraphs are
    selection units, not claims that complete methods or evidence were found.
    """
    files = {}
    maximum = max((float(item.get("score", 1 / math.log2(i + 2))) for i, item in enumerate(candidates)), default=1) or 1
    for index, item in enumerate(candidates):
        key = (item["sourceId"], item["path"])
        entry = files.setdefault(key, {"item": item, "lines": {}, "priors": {}})
        prior = float(item.get("score", 1 / math.log2(index + 2))) / maximum
        for number, line in enumerate(item["content"].split("\n"), item["lineStart"]):
            entry["lines"][number] = line
            entry["priors"][number] = max(entry["priors"].get(number, 0), prior)
    units = []
    for entry in files.values():
        item, source_lines = entry["item"], entry["lines"]
        span = []

        def append_span():
            if not span or not any(source_lines[n].strip() for n in span):
                return
            first, last = span[0], span[-1]
            units.append({**item, "id": f"{item['sourceId']}:{item['path']}:{first}-{last}",
                          "lineStart": first, "lineEnd": last,
                          "content": "\n".join(source_lines[n] for n in span),
                          "score": max(entry["priors"][n] for n in span)})
        for number in sorted(source_lines):
            if span and (number != span[-1] + 1 or
                         (not source_lines[span[-1]].strip() and source_lines[number].strip())):
                append_span()
                span = []
            span.append(number)
        append_span()
    matches = TextRetriever(units).search(question, limit=len(units)) if units else []
    match_scores = {item["id"]: item["score"] for item in matches}
    highest = max(match_scores.values(), default=1) or 1
    for item in units:
        relevance = .5 * item["score"] + .5 * match_scores.get(item["id"], 0) / highest
        # Locator cost prevents isolated short lines from looking free.
        item["score"] = relevance / math.sqrt(max(200, len(item["content"]) + len(item["path"]) + 40))
    ordered = _diverse_order(units)
    chosen, selected, context = [], [], ""
    pending = ordered
    while pending:
        rejected = []
        for item in pending:
            proposed, text = _merge_sections([*chosen, item])
            if len(text) <= budget:
                chosen.append(item)
                selected, context = proposed, text
            else:
                rejected.append(item)
        if len(rejected) == len(pending):
            break
        pending = rejected
    return selected, context


def pack_context(candidates: list[dict], budget: int, *, policy: str = "ranked", question: str = "") -> tuple[list[dict], str]:
    """Account for headers and text; optionally prefer diverse candidates."""
    if policy not in CONTEXT_POLICIES:
        raise ValueError("未知的上下文选择策略")
    if policy == "focused":
        return _focused_context(candidates, budget, question)
    if policy == "whole":
        chosen, selected, context = [], [], ""
        for item in candidates:
            proposed, text = _merge_sections([*chosen, item])
            if len(text) <= budget:
                chosen.append(item)
                selected, context = proposed, text
        # Merged ranges can contain several complete units. A single method
        # name would incorrectly describe that combined source region.
        for item in selected:
            for field in ("symbolName", "methodLineStart", "methodLineEnd", "structureVersionId", "unitType"):
                item.pop(field, None)
        return selected, context
    ordered = _diverse_order(candidates) if policy == "diverse" else candidates
    selected, blocks = [], []
    seen: set[tuple[str, str, int]] = set()
    used = 0
    for item in ordered:
        lines = item["content"].splitlines()
        offset = 0
        while offset < len(lines):
            line_number = item["lineStart"] + offset
            key = (item["sourceId"], item["path"], line_number)
            if key in seen:
                offset += 1
                continue
            start = offset
            while offset < len(lines) and (item["sourceId"], item["path"], item["lineStart"] + offset) not in seen:
                offset += 1
            available = budget - used
            # Reserve an accurate locator for the largest whole-line prefix.
            for stop in range(offset, start, -1):
                first, last = item["lineStart"] + start, item["lineStart"] + stop - 1
                body = "\n".join(lines[start:stop])
                header = f"[{item['kind']}] {item['sourceId']}/{item['path']}:{first}-{last}\n"
                block = header + body
                size = len(block) + (2 if blocks else 0)
                if size <= available:
                    selected.append({**item, "lineStart": first, "lineEnd": last, "content": body})
                    blocks.append(block)
                    used += size
                    seen.update((item["sourceId"], item["path"], number) for number in range(first, last + 1))
                    break
    return selected, "\n\n".join(blocks)

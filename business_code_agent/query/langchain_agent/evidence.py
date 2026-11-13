"""Reference actual delivered lines; never turn navigation into implementation proof."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Source:
    id: str
    path: str
    kind: str
    lines: dict[int, str]
    notice: str = ""

    def render(self):
        positions = sorted(self.lines)
        body = "\n".join(f"{n}: {self.lines[n]}" for n in positions)
        return f"[{self.id}][{self.kind}] {self.path}:{positions[0]}-{positions[-1]} {self.notice}\n{body}"


@dataclass(frozen=True)
class EvidenceCatalog:
    project_id: str
    sources: tuple[Source, ...]
    conflicts: tuple[str, ...] = ()

    def merged(self):
        """Keep each delivered line once; discontinuities remain explicit."""
        groups = {}
        for source in self.sources:
            groups.setdefault((source.path, source.kind), []).append(source)
        sources, conflicts = [], list(self.conflicts)
        for (path, kind), blocks in groups.items():
            rows, conflict = {}, False
            for block in blocks:
                for number, value in block.lines.items():
                    if number in rows and rows[number] != value:
                        conflict = True
                    rows[number] = value
            if conflict:
                conflicts.append(path)
                continue
            positions = sorted(rows)
            gaps = any(b > a + 1 for a, b in zip(positions, positions[1:]))
            notice = "[非连续原文；空缺行未交付]" if gaps else ""
            sources.append(Source(f"S{len(sources) + 1}", path, kind, rows, notice))
        return EvidenceCatalog(self.project_id, tuple(sources), tuple(dict.fromkeys(conflicts)))

    def bounded(self, char_limit):
        sources, used, omitted = [], 0, 0
        for source in reversed(self.sources):
            size = len(source.render()) + 2
            if used + size > char_limit:
                omitted += 1
                continue
            sources.append(source)
            used += size
        values = tuple(Source(f"S{i + 1}", s.path, s.kind, s.lines, s.notice) for i, s in enumerate(reversed(sources)))
        return EvidenceCatalog(self.project_id, values, self.conflicts), omitted

    @classmethod
    def from_text(cls, project_id, text):
        result = []
        path = None
        kind = None
        rows = {}
        notice = ""

        def append():
            if path and rows:
                result.append(
                    Source(f"S{len(result) + 1}", path, kind, dict(rows), notice)
                )

        for line in text.splitlines():
            header = re.match(r"^\[([^\]]+)\] (.+?):(\d+)-(\d+)(.*)$", line)
            if header:
                append()
                label, path, _, _, notice = header.groups()
                rows = {}
                if label == "source" and not path.startswith("repos/"):
                    path = "repos/" + path
                if label == "business_context" and path.startswith("business-context/"):
                    path = "knowledge/" + path
                if path.startswith(("knowledge/generated-code-map/", "knowledge/ast/")):
                    kind = "navigation"
                elif path.startswith("requirements/") or label in {
                    "requirement",
                    "需求原文",
                }:
                    kind = "requirement"
                elif path.startswith("knowledge/") or label in {
                    "business_context",
                    "业务补充资料",
                }:
                    kind = "business_context"
                elif path.lower().endswith((".md", ".txt")):
                    kind = "project_document"
                else:
                    kind = "code"
                continue
            row = re.match(r"^(\d+): ?(.*)$", line)
            if row and path:
                rows[int(row[1])] = row[2]
        append()
        return cls(project_id, tuple(result))

    def render(self):
        return "\n\n".join(s.render() for s in self.sources)

    def resolve(self, ref):
        source = next((s for s in self.sources if s.id == ref.source), None)
        if source is None or ref.end < ref.start:
            return None
        # One file may have been delivered in several Read/SearchCode blocks.
        # The source ID identifies the file; lines may come from another actual
        # block of that same file. Never fetch absent lines from the filesystem.
        delivered = {}
        for block in self.sources:
            if block.path != source.path or block.kind != source.kind:
                continue
            for n, value in block.lines.items():
                if ref.start <= n <= ref.end:
                    delivered.setdefault(n, set()).add(value)
        if any(len(delivered.get(n, ())) != 1 for n in range(ref.start, ref.end + 1)):
            return None
        rows = {n: next(iter(delivered[n])) for n in range(ref.start, ref.end + 1)}
        original = "\n".join(rows.values())
        if ref.quote and ref.quote not in original:
            return None
        return Source(source.id, source.path, source.kind, rows, source.notice)

    def excerpt(self, ref):
        source = self.resolve(ref)
        return (
            "\n".join(source.lines[n] for n in range(ref.start, ref.end + 1))
            if source else ""
        )

    def citation(self, ref):
        source = self.resolve(ref)
        return f"`{source.path}:{ref.start}`" if source else ""

"""Link findings and necessary connections to Read results from this turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath

from langchain_core.messages import ToolMessage

from .session import _covered, _merge_ranges, _read_payload


@dataclass(frozen=True)
class Evidence:
    id: str
    reference: str
    kind: str
    read_call_ids: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    objective_id: str
    claim: str
    kind: str
    evidence: tuple[str, ...]
    rejected: tuple[str, ...]
    gap: str


@dataclass(frozen=True)
class NecessaryRelation:
    id: str
    objective_id: str
    relationship: str
    basis: str
    connection: tuple[str, ...] = ()

    @property
    def status(self):
        return "supported" if self.connection else "partial"


class EvidenceLedger:
    def __init__(self, materials):
        self.materials = {source.path: source.kind for source in materials if source.available}
        self.evidence: dict[str, Evidence] = {}
        self.findings: dict[str, list[Finding]] = {}
        self.relations: dict[str, NecessaryRelation] = {}
        self._reads = {}

    def observe(self, messages):
        """Use only current-turn Read bodies actually included in a model request."""
        self._reads = {}
        for message in messages:
            if not isinstance(message, ToolMessage) or message.name != "Read":
                continue
            data = _read_payload(message.content)
            if not data or not data.get("content"):
                continue
            start, end = data.get("lineStart"), data.get("lineEnd")
            if isinstance(start, int) and isinstance(end, int) and 1 <= start <= end:
                self._reads.setdefault(data["path"], []).append((start, end, message.tool_call_id))

    def reference(self, location, *, kinds):
        """Bind an exact virtual path and complete line range without reopening a file."""
        path, separator, span = location.strip().rpartition(":")
        first, dash, last = span.partition("-")
        if not separator or not first.isdecimal() or (dash and not last.isdecimal()):
            return None
        start, end = int(first), int(last or first)
        if not 1 <= start <= end:
            return None
        kind = next((kind for mount, kind in self.materials.items() if path.startswith(mount + "/")), None)
        if kind == "source" and PurePosixPath(path).name.lower().startswith("readme"):
            kind = "navigation"
        if kind not in kinds:
            return None
        reference = f"{path}:{start}" + (f"-{end}" if start != end else "")
        if reference in self.evidence:
            return self.evidence[reference].id
        reads = [(low, high, call_id) for low, high, call_id in self._reads.get(path, ())
                 if low <= end and high >= start]
        if not _covered(_merge_ranges((low, high) for low, high, _ in reads), start, end):
            return None
        evidence = Evidence(f"E{len(self.evidence) + 1}", reference, kind,
                            tuple(dict.fromkeys(call_id for _, _, call_id in reads)))
        self.evidence[reference] = evidence
        return evidence.id

    def bind(self, locations, *, kinds):
        accepted, rejected = [], []
        for location in dict.fromkeys(locations):
            evidence_id = self.reference(location, kinds=kinds)
            if evidence_id:
                accepted.append(evidence_id)
            else:
                rejected.append(location)
        return tuple(dict.fromkeys(accepted)), tuple(rejected)

    def add_relation(self, objective_id, relationship, basis):
        existing = next((relation for relation in self.relations.values()
                         if relation.objective_id == objective_id and relation.relationship == relationship), None)
        if existing:
            return existing
        relation = NecessaryRelation(f"R{len(self.relations) + 1}", objective_id, relationship, basis)
        self.relations[relation.id] = relation
        return relation

    def resolve_relation(self, relation, connection):
        if connection:
            self.relations[relation.id] = replace(relation, connection=connection)

    def snapshot(self):
        return {
            "evidence": [asdict(item) for item in self.evidence.values()],
            "findings": [asdict(item) for findings in self.findings.values() for item in findings],
            "relations": [{**asdict(item), "status": item.status} for item in self.relations.values()],
        }

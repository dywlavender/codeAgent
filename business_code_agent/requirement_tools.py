from __future__ import annotations

from .requirement.service import RequirementService


class RequirementTools:
    """Digest-first tool boundary for the future query agent."""

    def __init__(self, db):
        self.service = RequirementService(db)

    def search_requirements(self, query: str) -> list[dict]:
        results = self.service.search(query)
        if results:
            return results
        from .tools import EvidenceTools
        return EvidenceTools(self.service.db).search_requirements(query)

    def get_requirement_digest(self, requirement_id: str, version: int | None = None) -> dict:
        try:
            return self.service.get_digest(requirement_id, version)
        except KeyError:
            from .tools import EvidenceTools
            return EvidenceTools(self.service.db).get_requirement_digest(requirement_id)

    def get_requirement_rule(self, requirement_id: str, rule_id: str | None = None, version: int | None = None):
        return self.service.get_rule(requirement_id, rule_id, version)

    def read_requirement_chunk(self, requirement_id: str, chunk_id: str) -> dict:
        try:
            return self.service.read_chunk(requirement_id, chunk_id)
        except KeyError:
            from .tools import EvidenceTools
            return EvidenceTools(self.service.db).read_requirement_chunk(requirement_id, chunk_id)

    def find_requirements_by_object(self, business_object: str) -> list[dict]:
        return self.service.find_by("object", business_object)

    def find_requirements_by_process(self, process: str) -> list[dict]:
        return self.service.find_by("process", process)

    def find_requirements_by_field(self, field: str) -> list[dict]:
        return self.service.find_by("field", field)

    def find_related_requirements(self, requirement_id: str) -> list[dict]:
        detail = self.service.get(requirement_id)
        return [item for item in detail["relations"] if item["target_type"] == "REQUIREMENT"]

    def find_requirement_code_relations(self, requirement_id: str, version: int | None = None) -> list[dict]:
        return self.service.code_relations(requirement_id, version)

    def get_requirement_history(self, requirement_id: str) -> list[dict]:
        return self.service.history(requirement_id)

"""Query policy and the public configuration saved with each run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping

from .materials import MATERIAL_KINDS, MaterialManifest


ToolPolicy = Literal["required", "optional", "disabled"]


@dataclass(frozen=True)
class AgentProfile:
    id: str
    runtime: str
    investigation: str
    answer_mode: str = "plain"
    materials: tuple[str, ...] = MATERIAL_KINDS
    file_tools: ToolPolicy = "required"
    search_code: ToolPolicy = "optional"
    contract_trace: ToolPolicy = "disabled"
    business_context: ToolPolicy = "disabled"


CORE = AgentProfile("core-v1", "langchain-direct", "direct",
                    materials=("source", "requirements"),
                    file_tools="required", search_code="disabled", contract_trace="disabled")
CORE_BUSINESS = AgentProfile("core-business-v1", "langchain-direct", "direct",
                             materials=("source", "requirements", "business_context"),
                             file_tools="required", search_code="disabled",
                             contract_trace="disabled", business_context="optional")
PI = AgentProfile("pi-v1", "pi", "pi", materials=MATERIAL_KINDS,
                  file_tools="required", search_code="disabled",
                  contract_trace="disabled", business_context="optional")
STANDARD = AgentProfile("standard-v1", "langchain", "agent_direct")
COORDINATED_LEGACY = AgentProfile("coordinated-legacy-v1", "langchain", "coordinated",
                                  contract_trace="optional")
CLAUDE_COMPAT = AgentProfile("claude-compat-v1", "claude", "claude_cli", search_code="disabled")


def legacy_langchain_profile(workflow, answer_mode, search_code, contract_trace) -> AgentProfile:
    if workflow not in {"basic", "tracked", "coordinated"}:
        raise ValueError("WORKFLOW 仅支持 basic、tracked 或 coordinated")
    if answer_mode not in {"facts", "plain", "contracts"}:
        raise ValueError("ANSWER_MODE 仅支持 facts、plain 或 contracts")
    if answer_mode == "contracts":
        if contract_trace == "disabled":
            raise ValueError("contracts 答题模式需要启用工程契约索引")
        contract_trace = "required"
    return replace(COORDINATED_LEGACY, id=f"{workflow}-legacy-v1", investigation=workflow,
                   answer_mode=answer_mode, search_code=search_code, contract_trace=contract_trace)


def resolve_profile(environment: Mapping[str, str]) -> AgentProfile:
    """Resolve offline/evaluation profiles from explicit configuration.

    The normal online entry uses :func:`resolve_online_profile` and always runs
    Pi. This mapping remains for historical evaluation and for callers that
    construct a legacy runtime explicitly.
    """
    name = environment.get("BUSINESS_CODE_AGENT_PROFILE", "").strip()
    if name:
        profiles = {profile.id: profile for profile in (
            CORE, CORE_BUSINESS, PI, STANDARD, COORDINATED_LEGACY, CLAUDE_COMPAT)}
        if name not in profiles:
            raise ValueError(f"未知的 Agent Profile：{name}")
        return profiles[name]
    runtime = environment.get("BUSINESS_CODE_AGENT_RUNTIME", "pi").strip().lower()
    if runtime == "pi":
        return PI
    if runtime == "claude":
        return CLAUDE_COMPAT
    if runtime != "langchain":
        raise ValueError("BUSINESS_CODE_AGENT_RUNTIME 仅支持 pi、langchain 或 claude")

    workflow = environment.get("BUSINESS_CODE_AGENT_WORKFLOW", "coordinated")
    coordinated = workflow == "coordinated"
    retrieval = environment.get("BUSINESS_CODE_AGENT_RETRIEVAL", "auto" if coordinated else "files").lower()
    contracts = environment.get("BUSINESS_CODE_AGENT_CONTRACTS", "auto" if coordinated else "off").lower()
    search_policy = {"files": "disabled", "auto": "optional", "hybrid": "required"}
    contract_policy = {"off": "disabled", "auto": "optional", "on": "required"}
    if retrieval not in search_policy or contracts not in contract_policy:
        raise ValueError("RETRIEVAL 仅支持 files、auto、hybrid，CONTRACTS 仅支持 off、auto、on")
    return legacy_langchain_profile(workflow, environment.get("BUSINESS_CODE_AGENT_ANSWER_MODE", "plain"),
                                    search_policy[retrieval], contract_policy[contracts])


def resolve_online_profile(environment: Mapping[str, str]) -> AgentProfile:
    """The normal online API entry is always Pi; legacy env cannot override it."""
    return PI


@dataclass(frozen=True)
class ToolStatus:
    capability: str
    policy: ToolPolicy
    status: str
    names: tuple[str, ...] = ()
    version: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"capability": self.capability, "policy": self.policy, "status": self.status,
                "names": list(self.names), "version": self.version, "reason": self.reason}


def execution_snapshot(profile: AgentProfile | None, materials: MaterialManifest, tools=(), *, settings=None) -> dict:
    settings = settings or {}
    return {
        "profileId": profile.id if profile else None,
        "investigation": profile.investigation if profile else None,
        "answerMode": profile.answer_mode if profile else None,
        "model": settings.get("requestedModel"),
        "settings": settings,
        "materials": [source.to_dict() for source in materials.sources],
        "tools": [tool.to_dict() for tool in tools],
    }

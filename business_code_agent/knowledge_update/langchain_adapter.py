from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .analysis_models import ImpactCandidate, UpdateAnalysis, UpdateSource, snapshot_evidence


class ModelInvocationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.0
    timeout: float = 60.0
    max_retries: int = 2

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelConfig":
        provider = str(value.get("provider", "")).strip()
        name = str(value.get("name", "")).strip()
        if not provider or not name:
            raise ValueError("model.provider and model.name are required")
        api_key_env = str(value.get("apiKeyEnv") or value.get("api_key_env") or "").strip() or None
        if "apiKey" in value or "api_key" in value:
            raise ValueError("model credentials must be referenced with apiKeyEnv")
        return cls(
            provider=provider,
            name=name,
            base_url=str(value.get("baseUrl") or value.get("base_url") or "").strip() or None,
            api_key_env=api_key_env,
            temperature=float(value.get("temperature", 0.0)),
            timeout=float(value.get("timeout", 60.0)),
            max_retries=int(value.get("maxRetries", value.get("max_retries", 2))),
        )


_MODEL_ENV_PREFIX = "BUSINESS_CODE_MODEL_"
_MODEL_API_KEY_ENV = f"{_MODEL_ENV_PREFIX}API_KEY"


def model_config_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Build the shared model configuration from the highest-priority env source.

    A configured ``BUSINESS_CODE_MODEL_*`` variable is enough to opt into the
    environment-based model configuration. The API key remains in the process
    environment and is referenced through ``apiKeyEnv`` so it never enters a
    JSON payload or a persisted Agent state.
    """

    values = os.environ if environ is None else environ
    if not any(key.startswith(_MODEL_ENV_PREFIX) for key in values):
        return None
    config: dict[str, Any] = {
        "enabled": _env_bool(values, "ENABLED", True),
        "provider": _env_text(values, "PROVIDER", "openai"),
        "name": _env_text(values, "NAME", "gpt-4.1-mini"),
        "apiKeyEnv": _MODEL_API_KEY_ENV,
        "temperature": _env_float(values, "TEMPERATURE", 0.0),
        "timeout": _env_float(values, "TIMEOUT", 60.0),
        "maxRetries": _env_int(values, "MAX_RETRIES", 2),
    }
    base_url = _env_text(values, "BASE_URL", "")
    if base_url:
        config["baseUrl"] = base_url
    return config


def _env_text(values: Mapping[str, str], suffix: str, default: str) -> str:
    value = str(values.get(f"{_MODEL_ENV_PREFIX}{suffix}", default)).strip()
    return value or default


def _env_bool(values: Mapping[str, str], suffix: str, default: bool) -> bool:
    name = f"{_MODEL_ENV_PREFIX}{suffix}"
    raw = str(values.get(name, str(default))).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _env_float(values: Mapping[str, str], suffix: str, default: float) -> float:
    name = f"{_MODEL_ENV_PREFIX}{suffix}"
    try:
        return float(values.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_int(values: Mapping[str, str], suffix: str, default: int) -> int:
    name = f"{_MODEL_ENV_PREFIX}{suffix}"
    try:
        return int(values.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def init_configured_chat_model(config: ModelConfig):
    """Create a provider-neutral LangChain 1.2 chat model from safe configuration."""
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:  # pragma: no cover - exercised when optional runtime is absent
        raise RuntimeError("LangChain 1.2 is required for model-backed knowledge analysis") from exc
    options: dict[str, Any] = {
        "temperature": config.temperature,
        "timeout": config.timeout,
        "max_retries": config.max_retries,
    }
    if config.base_url:
        options["base_url"] = config.base_url
    if config.api_key_env:
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ValueError(f"model credential environment variable is not set: {config.api_key_env}")
        options["api_key"] = api_key
    return init_chat_model(config.name, model_provider=config.provider, **options)


class LangChainUpdateAnalyzer:
    """LangChain 1.2 structured-output adapter for the domain update orchestrator."""

    def __init__(self, model: Any, *, agent_factory: Callable[..., Any] | None = None):
        self.model = model
        self._agent_factory = agent_factory

    @classmethod
    def from_config(
        cls,
        value: Mapping[str, Any],
        *,
        model_factory: Callable[[ModelConfig], Any] = init_configured_chat_model,
        agent_factory: Callable[..., Any] | None = None,
    ) -> "LangChainUpdateAnalyzer":
        config = ModelConfig.from_mapping(value)
        return cls(model_factory(config), agent_factory=agent_factory)

    def analyze(self, source: UpdateSource, candidates: Sequence[ImpactCandidate]) -> UpdateAnalysis:
        try:
            schema = _update_analysis_schema()
            factory = self._agent_factory
            if factory is None:
                from langchain.agents import create_agent
                factory = create_agent
            agent = factory(
                model=self.model,
                tools=[],
                response_format=schema,
                system_prompt=_SYSTEM_PROMPT,
            )
            context = {
                "source": {
                    "type": source.source_type.value,
                    "id": source.source_id,
                    "content": source.content[:24000],
                    "evidence_ids": list(source.evidence_ids),
                    "metadata": {
                        key: value for key, value in source.metadata.items()
                        if not str(key).startswith("_")
                    },
                },
                "affected_function_candidates": [item.to_dict() for item in candidates],
            }
            result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}]})
            structured = result.get("structured_response") if isinstance(result, Mapping) else None
            if structured is None:
                raise ValueError("knowledge update model did not return structured_response")
            payload = structured.model_dump(mode="python") if hasattr(structured, "model_dump") else structured
            if not isinstance(payload, Mapping):
                raise ValueError("knowledge update structured_response is invalid")
            return UpdateAnalysis.from_mapping(
                payload,
                allowed_evidence=set(source.evidence_ids) | set().union(
                    *(snapshot_evidence(item.current) for item in candidates), set()
                ),
                allowed_targets={item.function_id for item in candidates},
            )
        except ModelInvocationError:
            raise
        except Exception as exc:
            raise ModelInvocationError("knowledge update model analysis failed") from exc


def _update_analysis_schema():
    try:
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pydantic is required for structured knowledge analysis") from exc

    class Scenario(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str | None = None
        name: str
        summary: str = ""
        evidence_ids: list[str] = Field(default_factory=list)

    class Rule(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str | None = None
        statement: str
        conditions: list[str] = Field(default_factory=list)
        result: str = ""
        evidence_ids: list[str] = Field(default_factory=list)

    class Entry(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str | None = None
        entry_type: str
        label: str
        target_type: str = ""
        target_id: str = ""
        locator: str
        status: str = "ACTIVE"
        evidence_ids: list[str] = Field(default_factory=list)

    class DataImpact(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str | None = None
        object_type: str
        object_name: str
        operation: str
        before_state: str = ""
        after_state: str = ""
        description: str = ""
        evidence_ids: list[str] = Field(default_factory=list)

    class FunctionSnapshotSchema(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str
        domain: str = ""
        summary: str
        evidence_ids: list[str] = Field(default_factory=list)
        scenarios: list[Scenario] = Field(default_factory=list)
        rules: list[Rule] = Field(default_factory=list)
        entries: list[Entry] = Field(default_factory=list)
        data_impacts: list[DataImpact] = Field(default_factory=list)

    class ProposalItemSchema(BaseModel):
        model_config = ConfigDict(extra="forbid")
        item_type: str
        target_type: str
        target_id: str | None = None
        before: Any = None
        after: Any = None
        rationale: str
        confidence: float = Field(ge=0.0, le=1.0)
        evidence_ids: list[str] = Field(default_factory=list)

    class UpdateAnalysisSchema(BaseModel):
        model_config = ConfigDict(extra="forbid")
        title: str
        action: str
        target_function_id: str | None = None
        base_version_id: str | None = None
        summary: str
        proposed_snapshot: FunctionSnapshotSchema
        items: list[ProposalItemSchema] = Field(min_length=1)
        conflicts: list[str] = Field(default_factory=list)
        unknowns: list[str] = Field(default_factory=list)

    return UpdateAnalysisSchema


_SYSTEM_PROMPT = """You are the semantic-analysis component of a knowledge governance system.
Compare one bounded source with the supplied published business-function candidates.
Return only the requested structured response. Code describes observed implementation,
requirements describe intended behavior, and documents or feedback may conflict with both.
Never present an inference as confirmed. Cite only supplied evidence IDs. Select a target only
from supplied candidates. Prefer a compact business snapshot: function, scenarios, core rules,
entries, and business-level data impacts. Do not copy full source text or detailed call graphs.
You may propose CREATE, UPDATE, or RETIRE. You cannot approve or publish a proposal.
"""

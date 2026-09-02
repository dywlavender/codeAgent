from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    name: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.0
    timeout: float = 60.0
    max_retries: int = 2
    # DeepSeek exposes its reasoning switch as a provider-specific
    # ``extra_body`` field.  ``None`` means use the adapter default; explicit
    # values are ``enabled`` or ``disabled``.
    thinking: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelConfig":
        provider = str(value.get("provider", "")).strip()
        name = str(value.get("name", "")).strip()
        if not provider or not name:
            raise ValueError("model.provider and model.name are required")
        api_key_env = str(value.get("apiKeyEnv") or value.get("api_key_env") or "").strip() or None
        if "apiKey" in value or "api_key" in value:
            raise ValueError("model credentials must be referenced with apiKeyEnv")
        thinking_value = value.get("thinking")
        thinking = str(thinking_value).strip().lower() if thinking_value is not None else None
        if thinking and thinking not in {"enabled", "disabled"}:
            raise ValueError("model.thinking must be enabled or disabled")
        return cls(provider, name, str(value.get("baseUrl") or value.get("base_url") or "").strip() or None,
                   api_key_env, float(value.get("temperature", 0.0)), float(value.get("timeout", 60.0)),
                   int(value.get("maxRetries", value.get("max_retries", 2))), thinking)


_PREFIX = "BUSINESS_CODE_MODEL_"


def model_config_from_environment(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    values = os.environ if environ is None else environ
    if not any(key.startswith(_PREFIX) for key in values):
        return None
    config: dict[str, Any] = {
        "enabled": _bool(values, "ENABLED", True), "provider": _text(values, "PROVIDER", "openai"),
        "name": _text(values, "NAME", "gpt-4.1-mini"), "apiKeyEnv": f"{_PREFIX}API_KEY",
        "temperature": _float(values, "TEMPERATURE", 0.0), "timeout": _float(values, "TIMEOUT", 60.0),
        "maxRetries": _int(values, "MAX_RETRIES", 2),
    }
    base_url = _text(values, "BASE_URL", "")
    if base_url:
        config["baseUrl"] = base_url
    thinking = _text(values, "THINKING", "")
    if thinking:
        config["thinking"] = thinking
    return config


def _text(values, suffix, default):
    return str(values.get(f"{_PREFIX}{suffix}", default)).strip() or default


def _bool(values, suffix, default):
    name = f"{_PREFIX}{suffix}"; raw = str(values.get(name, str(default))).strip().lower()
    if raw in {"1", "true", "yes", "on"}: return True
    if raw in {"0", "false", "no", "off"}: return False
    raise ValueError(f"{name} must be true or false")


def _float(values, suffix, default):
    name = f"{_PREFIX}{suffix}"
    try: return float(values.get(name, default))
    except (TypeError, ValueError) as exc: raise ValueError(f"{name} must be a number") from exc


def _int(values, suffix, default):
    name = f"{_PREFIX}{suffix}"
    try: return int(values.get(name, default))
    except (TypeError, ValueError) as exc: raise ValueError(f"{name} must be an integer") from exc


def init_configured_chat_model(config: ModelConfig):
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise RuntimeError("LangChain 1.2 is required for model-backed analysis") from exc
    options: dict[str, Any] = {"temperature": config.temperature, "timeout": config.timeout, "max_retries": config.max_retries}
    if config.base_url: options["base_url"] = config.base_url
    if config.api_key_env:
        api_key = os.environ.get(config.api_key_env)
        if not api_key: raise ValueError(f"model credential environment variable is not set: {config.api_key_env}")
        options["api_key"] = api_key
    thinking = config.thinking
    if thinking is None and _is_deepseek_openai_endpoint(config):
        # DeepSeek enables thinking by default.  The LangChain structured
        # output agent must send a forced ``tool_choice`` and the OpenAI
        # compatibility wrapper does not preserve DeepSeek's
        # ``reasoning_content`` field.  Disable thinking for this adapter by
        # default; callers can explicitly opt in with BUSINESS_CODE_MODEL_THINKING.
        thinking = "disabled"
    if thinking is not None and _is_deepseek_openai_endpoint(config):
        options["extra_body"] = {"thinking": {"type": thinking}}
    return init_chat_model(config.name, model_provider=config.provider, **options)


def _is_deepseek_openai_endpoint(config: ModelConfig) -> bool:
    """Whether this config targets DeepSeek through the OpenAI-compatible API."""
    if config.provider.casefold() != "openai" or not config.base_url:
        return False
    hostname = (urlparse(config.base_url).hostname or "").casefold().rstrip(".")
    return hostname == "deepseek.com" or hostname.endswith(".deepseek.com")

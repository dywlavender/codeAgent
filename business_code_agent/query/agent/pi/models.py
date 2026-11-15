"""Read Pi's own model catalog through RPC for the online model selector.

The catalog belongs to Pi: providers, credentials and available models are
resolved by the CLI, not by CodeAgent. Only safe display fields leave this
module so provider endpoints and credentials never reach the browser.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from business_code_agent.query.runtime import RuntimeErrorBase

from .process import PiProcess, PiProcessError


MODEL_FIELDS = ("id", "name", "provider", "contextWindow", "maxTokens", "reasoning", "input")


def full_model_id(model: Mapping[str, Any]) -> str:
    identifier = str(model.get("id") or "").strip()
    provider = str(model.get("provider") or "").strip()
    if not identifier:
        return ""
    return f"{provider}/{identifier}" if provider else identifier


def normalize_model(model: Mapping[str, Any]) -> dict[str, Any] | None:
    identifier = str(model.get("id") or "").strip()
    if not identifier:
        return None
    provider = str(model.get("provider") or "").strip()
    inputs = model.get("input") if isinstance(model.get("input"), list) else []
    value = {
        "id": full_model_id(model),
        "name": str(model.get("name") or identifier),
        "provider": provider,
        "contextWindow": model.get("contextWindow"),
        "maxTokens": model.get("maxTokens"),
        "reasoning": bool(model.get("reasoning")),
        "input": [str(item) for item in inputs if isinstance(item, (str, int))][:4],
    }
    return {key: value[key] for key in MODEL_FIELDS if value.get(key) not in (None, "")}


def normalize_catalog(data: Mapping[str, Any], *, configured_model: str = "",
                      actual_model: str = "") -> dict[str, Any]:
    models = [normalize_model(item) for item in (data.get("models") or [])
              if isinstance(item, Mapping)]
    items = [item for item in models if item]
    identifiers = {item["id"] for item in items}
    configured = str(configured_model or "").strip()
    return {
        "items": items,
        "defaultModel": actual_model or None,
        "configuredModel": configured if configured in identifiers else None,
        "selectedModel": configured if configured in identifiers else (actual_model or None),
    }


def read_model_catalog(command: Sequence[str], environment: Mapping[str, str], *,
                       configured_model: str = "", timeout: float = 20.0) -> dict[str, Any]:
    """Ask a short-lived Pi RPC process for its models and current default."""
    if not command:
        raise RuntimeErrorBase("Pi 命令为空，无法读取模型列表")
    with tempfile.TemporaryDirectory(prefix="codeagent-pi-models-") as folder:
        process = PiProcess(
            [*command, "--mode", "rpc", "--session-dir", folder,
             "--no-extensions", "--no-context-files", "--no-mcp", "--no-skills",
             "--no-prompt-templates", "--no-themes", "--no-approve"],
            cwd=Path(folder), environment=dict(environment))
        try:
            process.start()
            models = _request(process, "get_available_models", timeout=timeout)
            state = _request(process, "get_state", timeout=timeout)
        except PiProcessError as exc:
            raise RuntimeErrorBase(f"无法启动 Pi 读取模型列表：{exc}") from exc
        finally:
            process.close()
    model_data = models.get("data") if isinstance(models.get("data"), Mapping) else {}
    state_data = state.get("data") if isinstance(state.get("data"), Mapping) else {}
    actual = state_data.get("model") if isinstance(state_data.get("model"), Mapping) else {}
    return normalize_catalog(model_data, configured_model=configured_model,
                             actual_model=full_model_id(actual))


def _request(process: PiProcess, kind: str, *, timeout: float) -> Mapping[str, Any]:
    request_id = f"{kind}-{uuid.uuid4().hex}"
    process.send({"id": request_id, "type": kind})
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeErrorBase(f"Pi 未在 {timeout:g} 秒内返回{kind}")
        record = process.receive(timeout=min(0.2, remaining))
        if record is None:
            continue
        if record.get("type") == "response" and record.get("id") == request_id:
            if not record.get("success"):
                raise RuntimeErrorBase(
                    f"Pi 拒绝{kind}：{record.get('error') or 'unknown error'}")
            return record
        if record.get("type") == "extension_ui_request":
            try:
                process.send({"type": "extension_ui_response", "id": record.get("id"),
                              "cancelled": True})
            except PiProcessError:
                pass

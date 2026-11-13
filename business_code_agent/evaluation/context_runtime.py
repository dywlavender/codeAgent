"""An OpenAI-compatible transport for fixed-context diagnosis only."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from business_code_agent.query.runtime import RuntimeResult

CONTEXT_RULES = ("你是代码问答助手。只能依据本次给出的原文回答。重要实现结论引用原文位置，"
                 "说明关键条件。证据不足时指出缺少什么，不补写实现。")


def configured_connection() -> dict:
    enabled = os.environ.get("BUSINESS_CODE_MODEL_ENABLED", "").lower() in {"true", "1", "yes"}
    provider = os.environ.get("BUSINESS_CODE_MODEL_PROVIDER", "").lower()
    base = os.environ.get("BUSINESS_CODE_MODEL_BASE_URL", "").rstrip("/")
    parsed = urlparse(base)
    endpoint = f"{parsed.scheme}://{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}{parsed.path}" if base else None
    return {"enabled": enabled, "provider": provider,
            "model": os.environ.get("BUSINESS_CODE_MODEL_NAME", ""), "endpoint": endpoint,
            "outputTokenLimit": 4096, "spendLimitSupported": provider != "openai"}


class CompatibleContextRuntime:
    """Stateless HTTP request: no tools, files, history, or source lookup."""

    def __init__(self, *, model="configured-default", environment=None):
        env = os.environ if environment is None else environment
        self.model = env.get("BUSINESS_CODE_MODEL_NAME", "") if model == "configured-default" else model
        self.url = str(env.get("BUSINESS_CODE_MODEL_BASE_URL", "")).rstrip("/") + "/chat/completions"
        self.api_key = env.get("BUSINESS_CODE_MODEL_API_KEY", "")
        self.temperature = float(env.get("BUSINESS_CODE_MODEL_TEMPERATURE", ".5"))
        self.timeout = float(env.get("BUSINESS_CODE_MODEL_TIMEOUT", "120"))
        self.max_tokens = 4096
        if not self.model or not self.api_key or not self.url.startswith(("https://", "http://")):
            raise ValueError("固定证据接口缺少 BUSINESS_CODE_MODEL_NAME、BASE_URL 或 API_KEY")

    def build_command(self, question, *, workspace, **kwargs):
        return ["HTTP", "POST", self.url, "--model", self.model, "--max-output-tokens", str(self.max_tokens)]

    def ask(self, question, *, workspace, cancel_check=None, **kwargs):
        if cancel_check and cancel_check():
            return RuntimeResult("", None, status="cancelled")
        payload = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                   "stream": False, "messages": [{"role": "system", "content": CONTEXT_RULES},
                                                  {"role": "user", "content": question}]}
        req = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise ValueError(f"模型兼容接口返回 HTTP {status}，请检查模型名称、地址及认证") from exc
        except URLError as exc:
            raise ValueError(f"模型兼容接口连接失败：{exc.reason}") from exc
        choices = data.get("choices") or []
        answer = (choices[0].get("message") or {}).get("content") if choices else None
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("模型兼容接口未返回回答正文")
        usage = dict(data.get("usage") or {})
        usage["reportedModels"] = [data["model"]] if data.get("model") else []
        usage["outputTokenLimit"] = self.max_tokens
        return RuntimeResult(answer, None, usage=usage)

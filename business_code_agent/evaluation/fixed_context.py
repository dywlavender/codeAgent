"""Answer an immutable context with no tools and no resumed conversation."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from business_code_agent.query.claude_runtime import ClaudeCodeRuntime

from .retrieval import check_project, read_json, write_json
from .context_runtime import CompatibleContextRuntime, configured_connection


class FixedContextRuntime(ClaudeCodeRuntime):
    def __init__(self, *, model: str, budget_usd=0.5, connection_settings=None, **kwargs):
        # Keep the user's model connection while excluding instructions/hooks.
        if connection_settings is None:
            path = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "settings.json"
            connection_settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        allowed = {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"}
        environment = {key: value for key, value in (connection_settings.get("env") or {}).items()
                       if isinstance(value, str) and key not in os.environ and
                       (key in allowed or re.fullmatch(r"ANTHROPIC_DEFAULT_[A-Z]+_MODEL(?:_NAME)?", key))}
        environment.update(kwargs.pop("environment", {}) or {})
        super().__init__(read_tools=(), environment=environment, **kwargs)
        self.model = (connection_settings.get("model") or model) if model == "configured-default" else model
        self.budget_usd = float(budget_usd)

    def build_command(self, question, *, workspace, session_id=None, repositories=None):
        if session_id:
            raise ValueError("固定证据实验不能续用会话")
        command = [self.command, "--print", "--output-format", "stream-json", "--verbose",
                   "--permission-mode", "dontAsk", "--tools", "", "--no-session-persistence",
                   "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                   "--disable-slash-commands", "--max-budget-usd", str(self.budget_usd),
                   "--system-prompt", "你是代码问答助手。只能根据本次给出的原文回答，不能搜索或读取其他资料。"
                   "重要结论引用原文位置，说明关键条件。证据不足时指出缺少什么，不补写实现。"]
        if self.model != "configured-default":
            command += ["--model", self.model]
        return command + ["-p", question]


def answer_context(output: Path, project_id: str, case_id: str, *, strategy="lexical", budget=12000,
                   model="configured-default", budget_usd=0.5, runtime=None, cancel_check=None) -> dict:
    protocol = check_project(output, project_id)
    case = next((c for c in protocol["suite"]["cases"] if c["id"] == case_id), None)
    if case is None:
        raise ValueError("当前批次没有这道题")
    # Resolve the strategy through the existing summary rather than arbitrary paths.
    summaries = {p.parent.name for p in (output / "strategies").glob("*/summary.json")}
    if strategy not in summaries or type(budget) is not int:
        raise ValueError("检索策略或预算不存在")
    context_path = output / "strategies" / strategy / f"{case_id}-{budget}.json"
    if not context_path.is_file():
        raise ValueError("没有相同版本与预算的检索上下文")
    row = read_json(context_path)
    model = str(model or "").strip()
    if not model or not 0 < float(budget_usd) <= 10:
        raise ValueError("需要模型名称和有效的单次支出上限（不超过 10）")
    # Only the question and frozen selected original text cross this boundary.
    prompt = f"问题：{case['question']}\n\n本轮证据原文：\n{row['context']}\n\n请用中文回答。"
    target = output / "answers" / f"answer-{time.time_ns()}"
    target.mkdir(parents=True)
    request = {"projectId": project_id, "snapshotId": protocol["snapshotId"], "caseId": case_id,
               "strategy": strategy, "budgetChars": budget, "contextChars": row["contextChars"],
               "retrievalStrategy": row.get("retrievalStrategy", strategy),
               "retrievalUnit": row.get("retrievalUnit", "windows"),
               "structureVersionId": row.get("structureVersionId"),
               "contextPolicy": row.get("contextPolicy", "ranked"),
               "contextPolicyVersion": row.get("contextPolicyVersion"),
               "requestedModel": model, "budgetUsd": float(budget_usd), "status": "running",
               "contextSource": context_path.relative_to(output).as_posix(), "toolsEnabled": False}
    connection = configured_connection()
    use_compatible = connection["enabled"] and connection["provider"] == "openai"
    request["connection"] = connection if use_compatible else {"provider": "claude_cli"}
    request["spendLimitSupported"] = not use_compatible
    if use_compatible:
        request["budgetUsd"] = None
    write_json(target / "result.json", request)
    (target / "prompt.txt").write_text(prompt, encoding="utf-8")
    started = time.monotonic()
    try:
        runtime = runtime or (CompatibleContextRuntime(model=model) if use_compatible else
                              FixedContextRuntime(model=model, budget_usd=budget_usd, timeout_seconds=180))
        request["resolvedModel"] = getattr(runtime, "model", model)
        # Keep the model outside the source tree and the batch's gold annotations.
        with TemporaryDirectory(prefix="fixed-code-context-") as workspace:
            command = runtime.build_command(prompt, workspace=workspace)
            write_json(target / "command.json", command)
            result = runtime.ask(prompt, workspace=workspace, cancel_check=cancel_check)
        request.update(status=result.status, answer=result.answer, usage=result.usage,
                       reportedModels=result.usage.get("reportedModels", []))
    except Exception as exc:
        request.update(status="failed", error=str(exc), answer="")
    request["elapsedSeconds"] = round(time.monotonic() - started, 3)
    write_json(target / "result.json", request)
    return request

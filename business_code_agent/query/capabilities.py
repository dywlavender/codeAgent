"""Minimal read-only capability registration for core-v1."""

from __future__ import annotations

from dataclasses import dataclass

from business_code_agent.query.context import RunContext
from business_code_agent.query.profile import ToolStatus
from business_code_agent.query.tools.files import CoreFiles


@dataclass(frozen=True)
class PreparedCapabilities:
    tools: tuple
    instructions: tuple[tuple[str, str], ...]
    statuses: tuple[ToolStatus, ...]
    files: CoreFiles | None = None


class FileCapability:
    id = "file"

    def status(self, context: RunContext) -> ToolStatus:
        missing = [source.repository_id or source.label
                   for source in context.materials
                   if source.kind == "source" and not source.available]
        if missing:
            return ToolStatus("file_tools", "required", "unavailable",
                              reason="授权仓库不可用：" + "、".join(missing))
        if not any(source.kind == "source" and source.available for source in context.materials):
            return ToolStatus("file_tools", "required", "unavailable",
                              reason="没有可授权的源码仓库")
        return ToolStatus("file_tools", "required", "ready", ("Read", "Glob", "Grep"))

    def tools(self, context: RunContext) -> tuple:
        return tuple(CoreFiles(context.workspace, context.materials).tools())

    def instructions(self, context: RunContext) -> str:
        mounts = "、".join(source.path for source in context.materials
                           if source.kind == "source" and source.available)
        return (
            f"可读取的源码根：{mounts or '无'}；需求目录为 requirements/。"
            "所有路径必须使用这些根下的相对路径。Glob/Grep 只用于定位，"
            "关键实现结论必须 Read 并引用真实路径与行号。"
        )


def prepare_capabilities(context: RunContext, *, providers=None) -> PreparedCapabilities:
    """Bind tools from ready providers; callers may inject test providers."""
    providers = tuple(providers) if providers is not None else (FileCapability(),)
    tools, instructions, statuses = [], [], []
    files = None
    for provider in providers:
        status = provider.status(context)
        statuses.append(status)
        if status.status != "ready":
            continue
        for tool in provider.tools(context):
            if any(existing.name == tool.name for existing in tools):
                raise ValueError(f"工具名重复：{tool.name}")
            tools.append(tool)
        guidance = provider.instructions(context)
        if guidance:
            instructions.append((provider.id, guidance))
        if isinstance(provider, FileCapability):
            files = CoreFiles(context.workspace, context.materials)
    for capability, policy in (
        ("search_code", context.profile.search_code),
        ("contract_trace", context.profile.contract_trace),
    ):
        if policy == "disabled":
            statuses.append(ToolStatus(capability, "disabled", "disabled"))
    for capability in ("business_context", "ast", "code_map", "hybrid_search"):
        statuses.append(ToolStatus(capability, "disabled", "disabled"))
    return PreparedCapabilities(tuple(tools), tuple(instructions), tuple(statuses), files)

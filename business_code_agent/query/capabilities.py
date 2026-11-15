"""Minimal read-only capability registration for core-v1."""

from __future__ import annotations

from dataclasses import dataclass

from business_code_agent.query.context import RunContext
from business_code_agent.query.profile import ToolStatus
from business_code_agent.query.tools.files import CoreFiles


PI_TOOL_NAMES = ("read", "grep", "find", "ls", "codemode")

_PI_MODE_KINDS = {
    "none": {"source", "requirements"},
    "backbone": {"source", "requirements", "business_context"},
    "ast": {"source", "requirements", "ast"},
    "combined": {"source", "requirements", "business_context", "code_map", "ast"},
}


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
            "所有路径必须使用这些根下的相对路径。"
            "已知文件时直接 Read；已知类名或文件名用 Glob（支持 * 与 **，不支持 {a,b} 花括号）。"
            "只有关键词且范围较大时，先用 Grep(output_mode='files') 找候选文件，再缩小 path 用 "
            "Grep(output_mode='matches', context=2) 定位行号，最后 Read 核实。"
            "Grep 仅支持 matches 与 files 两种 output_mode。不要求每道题依次调用所有工具。"
            "Glob/Grep 只用于定位，关键实现结论必须 Read 并引用真实路径与行号。"
        )


class BusinessContextCapability:
    """Expose optional project knowledge through the existing file tools."""

    id = "business_context"

    @staticmethod
    def _source(context: RunContext):
        return next((source for source in context.materials
                     if source.kind == "business_context"), None)

    def status(self, context: RunContext) -> ToolStatus:
        policy = context.profile.business_context
        if policy == "disabled":
            return ToolStatus("business_context", "disabled", "disabled")
        source = self._source(context)
        if source is None or not source.available:
            return ToolStatus("business_context", policy, "missing",
                              reason="业务补充知识目录缺失或为空")
        return ToolStatus("business_context", policy, "ready", version=source.version,
                          reason="复用 Read/Glob/Grep 按需读取")

    def tools(self, context: RunContext) -> tuple:
        return ()

    def instructions(self, context: RunContext) -> str:
        source = self._source(context)
        if source is None or not source.available:
            return ""
        return (
            f"业务补充知识位于 {source.path}/，用于解释项目术语、系统职责、"
            "隐式跨系统关系和数据生命周期。按问题需要主动 Glob/Grep/Read 相关文档；"
            "业务资料只作为业务约定和调查线索，不能当作源码已经实现，"
            "关键行为仍须读取源码确认。文档提到但本轮未授权的仓库或系统，"
            "只能记录为待核实关系，不能访问其源码。"
        )


class PiFileCapability:
    """Authorize Pi's native read-only tools against the mounted workspace."""

    id = "file"

    def __init__(self, version: str | None = None):
        self.version = version

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
        return ToolStatus("file_tools", "required", "ready", PI_TOOL_NAMES,
                          version=self.version)

    def tools(self, context: RunContext) -> tuple:
        return ()

    def instructions(self, context: RunContext) -> str:
        return (
            "Pi 原生工具：read 读取文件，grep 检索文本，find 按名称/路径查找，"
            "ls 浏览目录，codemode 可在沙箱脚本中并行调用这些工具并过滤输出。"
            "工具由你按问题自主选择，不要求固定顺序；关键实现结论必须用 read 核实。"
        )


class PiBusinessContextCapability(BusinessContextCapability):
    id = "business_context"

    def instructions(self, context: RunContext) -> str:
        source = self._source(context)
        if source is None or not source.available:
            return ""
        return (
            f"业务补充知识位于 {source.path}/，用于解释项目术语、系统职责、"
            "隐式跨系统关系和数据生命周期。按问题需要主动使用 grep/find/read 查找相关文档；"
            "业务资料只作为业务约定和调查线索，不能当作源码已经实现，"
            "关键行为仍须读取源码确认。文档提到但本轮未授权的仓库或系统，"
            "只能记录为待核实关系，不能访问其源码。"
        )


def prepare_pi_capabilities(context: RunContext, *, version: str | None = None,
                            mode: str | None = None,
                            providers=None) -> PreparedCapabilities:
    """Describe Pi's native tools without supplying LangChain tool objects."""

    expected = _PI_MODE_KINDS.get(mode or "combined", _PI_MODE_KINDS["combined"])
    selected = [PiFileCapability(version)]
    if (context.profile.business_context != "disabled"
            and "business_context" in expected):
        selected.append(PiBusinessContextCapability())
    providers = tuple(selected) if providers is None else tuple(providers)
    instructions, statuses = [], []
    available = {source.kind for source in context.materials if source.available}
    for provider in providers:
        status = provider.status(context)
        statuses.append(status)
        guidance = provider.instructions(context)
        if guidance:
            instructions.append((provider.id, guidance))
    for capability, kind, policy in (
        ("business_context", "business_context", context.profile.business_context),
        ("ast", "ast", "optional"),
        ("code_map", "code_map", "optional"),
        ("hybrid_search", "hybrid_search", "disabled"),
    ):
        if any(status.capability == capability for status in statuses):
            continue
        if policy == "disabled" or kind not in expected:
            statuses.append(ToolStatus(capability, "disabled", "disabled"))
        elif kind in available:
            statuses.append(ToolStatus(capability, policy, "ready"))
        else:
            statuses.append(ToolStatus(capability, policy, "missing",
                                       reason=f"{kind} 资料在本轮不可用"))
    for capability, policy in (
        ("search_code", context.profile.search_code),
        ("contract_trace", context.profile.contract_trace),
    ):
        statuses.append(ToolStatus(capability, policy, "disabled"))
    return PreparedCapabilities((), tuple(instructions), tuple(statuses))


def prepare_capabilities(context: RunContext, *, providers=None) -> PreparedCapabilities:
    """Bind tools from ready providers; callers may inject test providers."""
    if providers is None:
        selected = [FileCapability()]
        if context.profile.business_context != "disabled":
            selected.append(BusinessContextCapability())
        providers = tuple(selected)
    else:
        providers = tuple(providers)
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
        if not any(status.capability == capability for status in statuses):
            statuses.append(ToolStatus(capability, "disabled", "disabled"))
    return PreparedCapabilities(tuple(tools), tuple(instructions), tuple(statuses), files)

"""只读业务知识 MCP 服务：把工程资料以四个只读工具暴露给通用 Agent。

一次启动只绑定一个工程（:class:`ProjectContext`），不做按调用切换工程。工具
不写文件、不建索引、不调用模型，只读取现有配置和 Markdown 资料。MCP SDK 是
可选依赖，因此本模块顶层不导入 ``mcp``；缺少 SDK 时由 :func:`serve` 或
:func:`build_server` 抛出 :class:`McpSdkUnavailable` 给出可执行的安装提示。
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from business_code_agent import __version__
from business_code_agent.knowledge.business_context import BusinessContextFiles
from business_code_agent.platform.projects import ProjectContext, ProjectRegistry
from business_code_agent.query.navigation import build_navigation
from business_code_agent.query.system_guides import SYSTEM_GUIDES_MOUNT, guide_catalog, guide_target
from business_code_agent.query.workspace import WorkspaceManager, _safe_name

__all__ = ["McpSdkUnavailable", "TOOL_ARGUMENTS", "bind_project", "build_server", "serve"]

_MISSING_SDK = (
    "未安装 MCP Python SDK，无法启动只读业务知识服务。请先安装可选依赖后重试：\n"
    '    pip install "business-code-agent[mcp]"\n'
    "在源码目录中也可以执行：\n"
    '    python -m pip install -e ".[mcp]"'
)

#: 每个工具声明的参数名。未知参数会被 :class:`_StrictArguments` 拒绝，而不是被
#: SDK 生成的参数模型静默丢弃，这样通用 Agent 猜错参数名时能立刻看到原因。
TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "get_navigation": (),
    "get_system_guide": ("systemId", "applicationId"),
    "search_business_knowledge": ("query", "limit"),
    "read_business_knowledge": ("path",),
}

INSTRUCTIONS = (
    "只读业务知识服务，已在启动时绑定一个工程。四个工具分别提供工程导航、系统导航"
    "Markdown 原文、业务补充知识检索和单篇业务补充知识全文。工具只读，不修改文件、"
    "数据库或工作台数据。\n"
    "边界：导航来自工程配置，只用于定位范围和查找文件，不证明代码调用或页面已实现；"
    "业务补充知识只记录源码难以推断的项目特有语义、边界和术语，不能当成本轮源码已实现"
    "的证据。需要确认当前实现时仍要读源码。\n"
    "切换工程需要以另一个工程重新启动本服务，工具调用不能切换。"
)

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50
_HIT_CHARS = 200
_HITS_PER_DOCUMENT = 3
_word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_cjk = re.compile(r"[\u3400-\u9fff]+")
_heading = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*$")


class McpSdkUnavailable(RuntimeError):
    """MCP SDK 未安装，或安装的版本不提供所需接口。"""


@dataclass(frozen=True)
class _Sdk:
    server_class: Any
    tool_error: type
    call_tool_result: type
    text_content: type


def _load_sdk() -> _Sdk:
    """延迟导入 MCP SDK，并把它对外的接口收在一处。"""
    try:
        server_module = import_module("mcp.server.mcpserver")
        types_module = import_module("mcp.types")
        exception_module = import_module("mcp.server.mcpserver.exceptions")
        server_class = server_module.MCPServer
        tool_error = exception_module.ToolError
        call_tool_result = types_module.CallToolResult
        text_content = types_module.TextContent
    except (ImportError, AttributeError) as exc:
        raise McpSdkUnavailable(_MISSING_SDK) from exc
    return _Sdk(
        server_class=server_class,
        tool_error=tool_error,
        call_tool_result=call_tool_result,
        text_content=text_content,
    )


class _StrictArguments:
    """在 ``tools/call`` 阶段拒绝未声明的参数。

    SDK 用 ``create_model`` 生成的参数模型没有 ``extra="forbid"``，多传的字段会
    被静默丢弃。通用 Agent 猜错参数名时因此会以为调用成功，实际用了默认值。
    """

    def __init__(self, sdk: _Sdk):
        self._sdk = sdk

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Any]) -> Any:
        if getattr(ctx, "method", None) == "tools/call":
            params = ctx.params if isinstance(ctx.params, dict) else {}
            accepted = TOOL_ARGUMENTS.get(str(params.get("name") or ""))
            arguments = params.get("arguments")
            if accepted is not None and isinstance(arguments, dict):
                unknown = sorted(set(arguments) - set(accepted))
                if unknown:
                    listed = "、".join(accepted) or "无"
                    text = f"未知参数：{'、'.join(unknown)}。该工具只接受参数：{listed}。"
                    return self._sdk.call_tool_result(
                        content=[self._sdk.text_content(type="text", text=text)],
                        is_error=True,
                    )
        return await call_next(ctx)


def _legacy_context(config_path: Path) -> ProjectContext:
    """按兼容单工程模式构造上下文，数据库位置只用于补齐工程目录。"""
    path = config_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"工程配置文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    startup = payload.get("startup") if isinstance(payload, dict) else None
    configured = str((startup if isinstance(startup, dict) else {}).get("database") or ".data/knowledge.db")
    database = Path(configured).expanduser()
    return ProjectContext.legacy(database if database.is_absolute() else path.parent / database, path)


def bind_project(
    *,
    project_config: str | Path | None = None,
    project_registry: str | Path | None = None,
    project_id: str | None = None,
) -> ProjectContext:
    """解析本次服务要绑定的工程。注册表模式优先，其次兼容单工程配置。"""
    if project_registry:
        registry = ProjectRegistry(project_registry)
        return registry.get(project_id) if project_id else registry.default()
    if project_id:
        raise ValueError("--project-id 需要与 --project-registry 一起使用")
    if not project_config:
        raise ValueError("serve-mcp 需要 --project-config 或 --project-registry")
    return _legacy_context(Path(project_config))


@dataclass(frozen=True)
class _Knowledge:
    """启动时固定的工程知识视图，避免每次工具调用重新读取配置。"""

    context: ProjectContext
    config: dict
    sources: dict[str, Path]
    mounts: dict[str, str]
    navigation: dict
    business_root: Path

    @classmethod
    def bind(cls, context: ProjectContext) -> "_Knowledge":
        if not context.config_path:
            raise ValueError("该工程没有登记配置文件，无法读取业务知识")
        workspace = WorkspaceManager(None, project_config=context.config_path)
        sources = dict(workspace.repository_sources())
        mounts = {key: f"repos/{_safe_name(key)}" for key in sources}
        return cls(
            context=context,
            config=workspace.config,
            sources=sources,
            mounts=mounts,
            navigation=build_navigation(
                workspace.config, mounts, context.project_id, context.project_name,
            ),
            # 与工作台一致：注册工程读平台目录，兼容单工程回落到配置中的资料目录。
            business_root=(context.business_context_root if context.registered
                           else workspace.business_context_root),
        )

    @property
    def guides_root(self) -> Path:
        return self.context.system_guides_root

    def business_files(self) -> BusinessContextFiles:
        return BusinessContextFiles(self.business_root)

    def guides(self) -> tuple[BusinessContextFiles, list[dict]]:
        if self.guides_root.is_symlink():
            raise ValueError("系统导航目录须是当前工程的实际目录")
        files = BusinessContextFiles(self.guides_root)
        return files, guide_catalog(self.config, self.navigation, set(files.list()["items"]))

    def overview(self) -> dict:
        applications = []
        for application in self.navigation["applications"]:
            source = self.sources[application["repositoryId"]]
            relative = PurePosixPath(application["mount"]).relative_to(
                self.mounts[application["repositoryId"]])
            location = source.joinpath(*relative.parts)
            applications.append({
                "id": application["id"],
                "name": application["name"],
                "systemId": application["systemId"],
                "systemName": application["systemName"],
                "type": application["type"],
                "repositoryId": application["repositoryId"],
                "path": str(location),
                "exists": location.is_dir(),
            })
        repositories = [
            {
                "id": key,
                "mount": self.mounts[key],
                "path": str(source),
                "exists": source.is_dir(),
            }
            for key, source in self.sources.items()
        ]
        systems = []
        for system in self.config.get("systems", []):
            key = system.get("id")
            systems.append({
                "id": key,
                "name": system.get("name") or key,
                "applications": [item["id"] for item in applications if item["systemId"] == key],
            })
        return {
            "project": {"id": self.context.project_id, "name": self.context.project_name},
            "systems": systems,
            "applications": applications,
            "repositories": repositories,
        }


def _query_terms(query: str) -> list[str]:
    """把检索词拆成 ASCII 词和中文二元组，兼顾标识符和中文短语。"""
    terms: list[str] = []
    for word in _word.findall(query):
        if len(word) > 1:
            terms.append(word.casefold())
    for run in _cjk.findall(query):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms)) or [query.strip().casefold()]


def _clip(text: str) -> str:
    value = text.strip()
    return value if len(value) <= _HIT_CHARS else value[:_HIT_CHARS] + "…"


def _title(text: str) -> str | None:
    for line in text.splitlines():
        match = _heading.match(line.lstrip("\ufeff"))
        if match:
            return match[1]
    return None


def _documents(files: BusinessContextFiles) -> list[str]:
    return list(files.list()["items"])


def _declare_tools(view: _Knowledge, tool_error: type):
    """构造四个只读工具。工具体只用位置固定的简单参数，便于通用 Agent 调用。"""

    def get_navigation() -> dict:
        return view.overview()

    def get_system_guide(systemId: str, applicationId: str | None = None) -> dict:
        try:
            files, catalog = view.guides()
        except ValueError as exc:
            raise tool_error(str(exc)) from exc
        if not any(item["id"] == systemId for item in catalog):
            registered = "、".join(f"{item['id']}（{item['name']}）" for item in catalog) or "无"
            raise tool_error(f"未登记的 systemId：{systemId}。本工程已登记的系统：{registered}。")
        try:
            target, _ = guide_target(catalog, systemId, applicationId)
        except ValueError as exc:
            raise tool_error(str(exc)) from exc
        published = bool(target["published"])
        content = None
        if published:
            try:
                content = files.read(target["path"])["content"]
            except (OSError, UnicodeDecodeError) as exc:
                raise tool_error(f"系统导航文档读取失败：{target['path']}（{exc}）") from exc
        result = {
            "projectId": view.context.project_id,
            "systemId": systemId,
            "applicationId": applicationId,
            "name": target.get("name") or target["id"],
            "path": target["path"],
            "workspacePath": f"{SYSTEM_GUIDES_MOUNT}/{target['path']}",
            "published": published,
            "content": content,
        }
        if applicationId is None:
            result["applications"] = [
                {
                    "id": item["id"],
                    "name": item.get("name") or item["id"],
                    "path": item["path"],
                    "published": item["published"],
                }
                for item in target["applications"]
            ]
        if not published:
            result["hint"] = (
                "该导航文档尚未发布。请在资料中心的系统导航页生成，或在工程数据目录的 "
                f"{SYSTEM_GUIDES_MOUNT} 下创建该 Markdown 文件。"
            )
        return result

    def search_business_knowledge(query: str, limit: int = _DEFAULT_LIMIT) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise tool_error("query 不能为空，请给出要查找的业务术语或问题关键词。")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise tool_error("limit 必须是正整数。")
        limit = max(1, min(limit, _MAX_LIMIT))
        files = view.business_files()
        terms = _query_terms(query)
        root = view.business_root
        names = _documents(files)
        matches = []
        for name in names:
            try:
                text = files.read(name)["content"]
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            occurrences = 0
            matched: list[str] = []
            hits: list[dict] = []
            for number, line in enumerate(text.splitlines(), 1):
                folded = line.casefold()
                found = [term for term in terms if term and term in folded]
                if not found:
                    continue
                occurrences += len(found)
                for term in found:
                    if term not in matched:
                        matched.append(term)
                if len(hits) < _HITS_PER_DOCUMENT:
                    hits.append({"line": number, "text": _clip(line)})
            if not hits:
                continue
            matches.append({
                "path": name,
                "title": _title(text),
                "score": len(matched) * 10 + occurrences,
                "matchedTerms": matched,
                "hits": hits,
            })
        matches.sort(key=lambda item: (-item["score"], item["path"]))
        result: dict[str, Any] = {
            "query": query,
            "root": str(root),
            "searchedDocuments": len(names),
            "totalMatches": len(matches),
            "items": matches[:limit],
        }
        if not names:
            result["hint"] = ("当前工程还没有业务补充知识文档。"
                              if not root.is_dir() else "业务补充知识目录中没有 Markdown 文档。")
        return result

    def read_business_knowledge(path: str) -> dict:
        if not isinstance(path, str) or not path.strip():
            raise tool_error("path 不能为空，请给出业务补充知识文档的相对路径。")
        files = view.business_files()
        try:
            document = files.read(path)
        except FileNotFoundError as exc:
            raise tool_error(
                f"业务补充知识中没有该文档：{path}。可先用 search_business_knowledge 查找。") from exc
        except IsADirectoryError as exc:
            raise tool_error(f"path 指向目录而不是 Markdown 文件：{path}") from exc
        except UnicodeDecodeError as exc:
            raise tool_error(f"文档不是 UTF-8 文本：{path}") from exc
        except (ValueError, OSError) as exc:
            raise tool_error(str(exc)) from exc
        return {
            "path": document["name"],
            "title": _title(document["content"]),
            "root": str(view.business_root),
            "modifiedAt": document["modifiedAt"],
            "sections": document["sections"],
            "content": document["content"],
        }

    return [
        ("get_navigation",
         "返回当前工程的系统、应用、仓库及其真实文件系统位置。用于确定有哪些系统和应用、"
         "各自源码目录在哪里。导航来自工程配置，只用于定位，不证明调用关系或页面已实现。",
         get_navigation),
        ("get_system_guide",
         "返回某个系统或应用的导航 Markdown 原文（systemId 必填，applicationId 可选）。"
         "该文档描述代码在哪里、按什么规则查找入口和例外。未发布的文档返回 published=false "
         "和 content=null。应用视角只含当前应用规则，需要系统通用规则时另行按 systemId 获取。",
         get_system_guide),
        ("search_business_knowledge",
         "在业务补充知识中检索，返回命中的文档路径、相关度和命中行摘要。首版为词面检索，"
         "不含向量检索；命中只是候选，确认结论需要再用 read_business_knowledge 读全文。",
         search_business_knowledge),
        ("read_business_knowledge",
         "读取一篇业务补充知识 Markdown 全文，path 使用 search_business_knowledge 返回的"
         "相对路径。业务补充知识只记录源码难以推断的语义、边界和术语，不能证明当前代码已实现。",
         read_business_knowledge),
    ]


def build_server(context: ProjectContext) -> Any:
    """按已绑定的工程构造 MCP 服务实例；缺少 SDK 时抛 :class:`McpSdkUnavailable`。"""
    sdk = _load_sdk()
    view = _Knowledge.bind(context)
    declarations = _declare_tools(view, sdk.tool_error)
    for name, _description, function in declarations:
        actual = tuple(inspect.signature(function).parameters)
        if actual != TOOL_ARGUMENTS.get(name):
            # 声明参数与未知参数校验必须一致，否则会误拒合法参数。
            raise RuntimeError(f"工具 {name} 的参数声明不一致：{actual}")
    server = sdk.server_class(
        name="business-code-agent-knowledge",
        version=__version__,
        instructions=INSTRUCTIONS,
        middleware=[_StrictArguments(sdk)],
    )
    for name, description, function in declarations:
        server.tool(name=name, description=description)(function)
    return server


def serve(
    *,
    project_config: str | Path | None = None,
    project_registry: str | Path | None = None,
    project_id: str | None = None,
) -> None:
    """以 stdio 传输启动只读业务知识服务。

    stdout 只承载 JSON-RPC，启动信息和日志都写到 stderr。
    """
    context = bind_project(
        project_config=project_config,
        project_registry=project_registry,
        project_id=project_id,
    )
    server = build_server(context)
    print(
        f"[INFO] 只读业务知识 MCP 服务已启动：{context.project_name}"
        f"（{context.project_id}），传输 stdio",
        file=sys.stderr,
    )
    server.run(transport="stdio")

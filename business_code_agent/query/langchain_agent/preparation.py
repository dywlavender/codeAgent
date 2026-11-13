"""Resolve configured tool capabilities against already prepared project data."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from sqlite3 import DatabaseError

from business_code_agent.codeintel.contracts.query import ContractIndex

from business_code_agent.query.navigation import read_navigation
from business_code_agent.query.profile import ToolStatus
from business_code_agent.query.runtime import RunPreparation
from .contracts import CONTRACT_TOOL_NAMES
from .files import SourceFiles


@dataclass(frozen=True)
class LangChainPreparation(RunPreparation):
    files: SourceFiles
    file_tools: tuple
    contract_index: ContractIndex | None
    search_code: object | None


def prepare_tools(profile, workspace, materials, *, project_id, index_root, contract_root, code_search_db):
    files = SourceFiles(workspace, manifest=materials)
    file_tools = tuple(files.tools()) if profile.file_tools != "disabled" else ()
    statuses = [ToolStatus("file_tools", profile.file_tools, "ready" if file_tools else "disabled",
                           tuple(tool.name for tool in file_tools))]

    search_code = None
    policy = profile.search_code
    if policy == "disabled":
        statuses.append(ToolStatus("search_code", policy, "disabled"))
    else:
        try:
            if index_root is None or not (index_root / "index.json").is_file():
                raise ValueError("混合检索索引尚未显式准备")
            if any(find_spec(name) is None for name in ("numpy", "onnxruntime", "tokenizers")):
                raise ValueError("缺少混合检索运行依赖")
            from business_code_agent.codeintel.hybrid.search import SearchSnapshot, search_tool

            snapshot = SearchSnapshot.open(index_root, project_id)
            search_code = search_tool(snapshot, files)
        except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
            statuses.append(ToolStatus("search_code", policy, "unavailable", reason=str(exc)))
        else:
            statuses.append(ToolStatus("search_code", policy, "ready", (search_code.name,), snapshot.version))

    contract_index = None
    policy = profile.contract_trace
    if policy == "disabled":
        statuses.append(ToolStatus("contract_trace", policy, "disabled"))
    else:
        try:
            if contract_root is None or not (contract_root / "contracts.sqlite").is_file():
                raise ValueError("契约索引尚未显式准备，请先执行 prepare_contract_index.py")
            owner = project_id or read_navigation(workspace, files.roots).get("projectId")
            contract_index = ContractIndex(contract_root, owner, files)
            version = contract_index.snapshot["version"]
        except (DatabaseError, OSError, ValueError, KeyError, TypeError) as exc:
            contract_index = None
            statuses.append(ToolStatus("contract_trace", policy, "unavailable", reason=str(exc)))
        else:
            statuses.append(ToolStatus("contract_trace", policy, "ready", CONTRACT_TOOL_NAMES, version))

    if profile.investigation in {"tracked", "coordinated"}:
        statuses.append(ToolStatus("investigation", "required", "ready", ("Investigation",)))
    if code_search_db is not None:
        exists = code_search_db.is_file()
        statuses.append(ToolStatus("symbol_search", "required", "ready" if exists else "unavailable",
                                   ("SearchSymbols",) if exists else (),
                                   reason=None if exists else "显式指定的代码符号索引不存在"))
    return LangChainPreparation(materials, tuple(statuses), files, file_tools, contract_index, search_code)

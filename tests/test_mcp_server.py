"""只读业务知识 MCP 服务的工具行为、参数校验与 CLI 接线测试。

MCP SDK 是可选依赖：需要它的用例在缺少 SDK 时跳过，其余用例通过构造工程夹具
验证四个工具的输出形状、错误分支和路径安全边界。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from business_code_agent.mcp.server import (
    INSTRUCTIONS,
    TOOL_ARGUMENTS,
    McpSdkUnavailable,
    bind_project,
    build_server,
)

try:  # MCP SDK 是可选依赖，缺失时只跳过依赖它的用例。
    import mcp.server.mcpserver  # noqa: F401
    HAS_SDK = True
except ImportError:  # pragma: no cover - 取决于当前环境是否安装可选依赖
    HAS_SDK = False

ROOT = Path(__file__).resolve().parents[1]
require_sdk = unittest.skipUnless(HAS_SDK, "需要可选依赖 mcp（pip install -e '.[mcp]'）")

FIXTURE_DOCUMENTS = {
    "project-overview.md": "# 工程总览\n\n提款业务位于渠道系统与中台之间。\n",
    "withdraw-flow.md": (
        "# 提款流程\n\n## 主流程\n\n提款申请先由渠道 H5 提交，"
        "再由渠道服务转交中台处理。\n\n## 例外\n\n额度不足时直接拒绝。\n"
    ),
    "glossary.md": "# 术语表\n\n额度冻结：放款前的预占动作。\n",
}
FIXTURE_CONFIG = {
    "project": {"id": "mcp-fixture", "name": "MCP 夹具工程"},
    "startup": {"database": ".data/knowledge.db"},
    "knowledge": {"businessContextRoot": "knowledge/business-context"},
    "systems": [{"id": "channel", "name": "渠道系统"}, {"id": "middle", "name": "贷款中台系统"}],
    "repositories": [
        {"id": "channel-repo", "localPath": "repo-channel"},
        {"id": "middle-repo", "localPath": "repo-middle"},
    ],
    "applications": [
        {"id": "channel-h5", "name": "渠道 H5", "systemId": "channel",
         "repositoryId": "channel-repo", "sourceRoot": "src", "type": "frontend"},
        {"id": "middle-api", "name": "中台接口", "systemId": "middle",
         "repositoryId": "middle-repo", "sourceRoot": "src", "type": "backend"},
    ],
}


def write_project(root: Path, *, knowledge: dict | None = None) -> Path:
    """写一个最小工程：两个系统、三份业务补充知识、尚未发布的系统导航目录。"""
    for name in ("repo-channel", "repo-middle"):
        (root / name / "src").mkdir(parents=True, exist_ok=True)
        (root / name / "src" / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    folder = root / "knowledge" / "business-context"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in FIXTURE_DOCUMENTS.items():
        (folder / name).write_text(text, encoding="utf-8")
    path = root / "project.config.json"
    path.write_text(
        json.dumps({**FIXTURE_CONFIG, "knowledge": knowledge or FIXTURE_CONFIG["knowledge"]}),
        encoding="utf-8",
    )
    return path


@require_sdk
class McpToolTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.config_path = write_project(self.root)
        self.context = bind_project(project_config=self.config_path)
        self.server = build_server(self.context)

    def call(self, name: str, arguments: dict) -> tuple[bool, str]:
        """直接调用工具，把有意抛出的 ToolError 转成错误文本。"""
        try:
            result = asyncio.run(self.server.call_tool(name, arguments))
        except Exception as exc:  # ToolError 在请求链路外会原样抛出
            self.assertEqual("ToolError", type(exc).__name__, str(exc))
            return True, str(exc).split(": ", 1)[-1]
        return bool(result.is_error), result.content[0].text

    def payload(self, name: str, arguments: dict) -> dict:
        error, text = self.call(name, arguments)
        self.assertFalse(error, text)
        return json.loads(text)

    def test_tools_are_exactly_the_four_read_only_names(self):
        tools = asyncio.run(self.server.list_tools())
        self.assertEqual(sorted(TOOL_ARGUMENTS), sorted(tool.name for tool in tools))
        for tool in tools:
            self.assertTrue(tool.description and tool.description.strip(), tool.name)
            self.assertEqual(list(TOOL_ARGUMENTS[tool.name]),
                             list(tool.input_schema.get("properties") or {}), tool.name)
        navigation = next(tool for tool in tools if tool.name == "get_navigation")
        self.assertEqual([], navigation.input_schema.get("required", []))

    def test_binding_requires_exactly_one_project_source(self):
        self.assertFalse(self.context.registered)
        self.assertEqual("mcp-fixture", self.context.project_id)
        for arguments in ({}, {"project_id": "mcp-fixture"}):
            with self.assertRaises(ValueError):
                bind_project(**arguments)
        with self.assertRaises(ValueError):
            bind_project(project_config=self.root / "missing.json")

    def test_get_navigation_returns_configured_topology_and_real_locations(self):
        data = self.payload("get_navigation", {})
        self.assertEqual({"id": "mcp-fixture", "name": "MCP 夹具工程"}, data["project"])
        self.assertEqual(["channel", "middle"], [item["id"] for item in data["systems"]])
        self.assertEqual(["channel-h5", "middle-api"], [item["id"] for item in data["applications"]])
        self.assertEqual(["FRONTEND", "BACKEND"], [item["type"] for item in data["applications"]])
        self.assertEqual({"channel-repo", "middle-repo"},
                         {item["id"] for item in data["repositories"]})
        # 应用位置由配置的仓库目录与 sourceRoot 组成，必须是真实存在的绝对路径。
        for item in data["applications"] + data["repositories"]:
            location = Path(item["path"])
            self.assertTrue(location.is_absolute(), item)
            self.assertTrue(location.is_dir(), item)
            self.assertTrue(item["exists"], item)
        self.assertEqual((self.root / "repo-channel" / "src").as_posix(),
                         Path(data["applications"][0]["path"]).as_posix())

    def test_get_system_guide_reports_unpublished_documents_without_content(self):
        data = self.payload("get_system_guide", {"systemId": "channel"})
        self.assertEqual("channel/SYSTEM.md", data["path"])
        self.assertFalse(data["published"])
        self.assertIsNone(data["content"])
        self.assertEqual(["channel-h5"], [item["id"] for item in data["applications"]])
        self.assertIn("hint", data)

    def test_get_system_guide_returns_published_system_and_application_markdown(self):
        guides = self.root / ".data" / "knowledge" / "system-guides"
        documents = {
            "channel/SYSTEM.md": "# 渠道导航\n\n前端入口在 src 下。\n",
            "channel/applications/channel-h5.md": "# 渠道 H5 导航\n\n页面在 src/pages。\n",
        }
        for name, text in documents.items():
            (guides / name).parent.mkdir(parents=True, exist_ok=True)
            (guides / name).write_text(text, encoding="utf-8")
        system = self.payload("get_system_guide", {"systemId": "channel"})
        self.assertTrue(system["published"])
        self.assertEqual(documents["channel/SYSTEM.md"], system["content"])
        self.assertEqual("knowledge/system-guides/channel/SYSTEM.md", system["workspacePath"])
        self.assertEqual(["channel-h5"], [item["id"] for item in system["applications"]])
        self.assertTrue(system["applications"][0]["published"])
        application = self.payload(
            "get_system_guide", {"systemId": "channel", "applicationId": "channel-h5"})
        self.assertTrue(application["published"])
        self.assertEqual(documents["channel/applications/channel-h5.md"], application["content"])
        self.assertNotIn("applications", application)

    def test_get_system_guide_rejects_unknown_and_mismatched_selection(self):
        error, text = self.call("get_system_guide", {"systemId": "nginx"})
        self.assertTrue(error)
        self.assertIn("未登记的 systemId：nginx", text)
        self.assertIn("channel（渠道系统）", text)
        error, text = self.call("get_system_guide",
                                {"systemId": "channel", "applicationId": "middle-api"})
        self.assertTrue(error)
        self.assertIn("应用不属于所选系统", text)

    def test_search_matches_documents_and_honours_limit(self):
        data = self.payload("search_business_knowledge", {"query": "提款"})
        self.assertEqual(3, data["searchedDocuments"])
        self.assertEqual(2, data["totalMatches"])
        first = data["items"][0]
        self.assertEqual("withdraw-flow.md", first["path"])
        self.assertEqual("提款流程", first["title"])
        self.assertEqual(["提款"], first["matchedTerms"])
        self.assertEqual(2, len(first["hits"]))
        self.assertIn("提款", first["hits"][0]["text"])
        limited = self.payload("search_business_knowledge", {"query": "提款", "limit": 1})
        self.assertEqual(2, limited["totalMatches"])
        self.assertEqual(["withdraw-flow.md"], [item["path"] for item in limited["items"]])
        none = self.payload("search_business_knowledge", {"query": "量子计算"})
        self.assertEqual((0, []), (none["totalMatches"], none["items"]))

    def test_search_caps_hit_lines_and_matches_ascii_identifiers(self):
        folder = self.root / "knowledge" / "business-context"
        (folder / "api.md").write_text(
            "# 接口\n\nWithdrawApply 是提款申请接口。\n"
            + "".join(f"\nWithdrawApply 说明第 {index} 段。\n" for index in range(1, 6)),
            encoding="utf-8",
        )
        data = self.payload("search_business_knowledge", {"query": "WithdrawApply"})
        self.assertEqual(["api.md"], [item["path"] for item in data["items"]])
        self.assertEqual(["withdrawapply"], data["items"][0]["matchedTerms"])
        self.assertEqual(3, len(data["items"][0]["hits"]))
        self.assertEqual([3, 5, 7], [hit["line"] for hit in data["items"][0]["hits"]])

    def test_search_reports_empty_catalog_without_failing(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        config_path = write_project(root, knowledge={"businessContextRoot": "knowledge/absent"})
        server = build_server(bind_project(project_config=config_path))
        listing = json.loads(asyncio.run(
            server.call_tool("search_business_knowledge", {"query": "提款"})).content[0].text)
        self.assertEqual(0, listing["searchedDocuments"])
        self.assertEqual([], listing["items"])
        self.assertIn("hint", listing)

    def test_registered_project_reads_platform_knowledge_not_the_source_config(self):
        from business_code_agent.platform.projects import ProjectRegistry

        registry_root = self.root / "platform"
        registered = ProjectRegistry(registry_root).register(self.config_path)
        self.assertTrue(registered.registered)
        bound = bind_project(project_registry=registry_root, project_id=registered.project_id)
        self.assertTrue(bound.registered)
        self.assertEqual(registered.data_root, bound.data_root)
        server = build_server(bound)

        empty = json.loads(asyncio.run(
            server.call_tool("search_business_knowledge", {"query": "提款"})).content[0].text)
        # 注册工程的业务补充知识来自平台数据目录，尚未导入时为空。
        self.assertEqual(0, empty["searchedDocuments"])
        self.assertEqual(str(registered.business_context_root), empty["root"])

        registered.business_context_root.mkdir(parents=True, exist_ok=True)
        (registered.business_context_root / "adr.md").write_text(
            "# 决策\n\n提款流程按系统拆分。\n", encoding="utf-8")
        filled = json.loads(asyncio.run(
            server.call_tool("search_business_knowledge", {"query": "提款"})).content[0].text)
        self.assertEqual(["adr.md"], [item["path"] for item in filled["items"]])

    def test_read_business_knowledge_returns_full_markdown(self):
        data = self.payload("read_business_knowledge", {"path": "withdraw-flow.md"})
        self.assertEqual("withdraw-flow.md", data["path"])
        self.assertEqual("提款流程", data["title"])
        source = self.root / "knowledge" / "business-context" / "withdraw-flow.md"
        self.assertEqual(source.read_text(encoding="utf-8"), data["content"])
        self.assertEqual(["提款流程", "主流程", "例外"], [s["title"] for s in data["sections"]])
        self.assertTrue(data["modifiedAt"])
        self.assertEqual(str((self.root / "knowledge" / "business-context")), data["root"])

    def test_read_business_knowledge_reads_nested_documents(self):
        nested = self.root / "knowledge" / "business-context" / "flows" / "detail.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("# 明细\n\n正文。\n", encoding="utf-8")
        data = self.payload("read_business_knowledge", {"path": "flows/detail.md"})
        self.assertEqual("flows/detail.md", data["path"])
        self.assertEqual("# 明细\n\n正文。\n", data["content"])

    def test_read_business_knowledge_rejects_unsafe_and_missing_paths(self):
        for path in ("../escape.md", "/etc/passwd.md", "flow.txt", "", "nested/../../x.md",
                     "a//b.md", "c:\\windows\\x.md"):
            error, text = self.call("read_business_knowledge", {"path": path})
            self.assertTrue(error, f"{path!r} 应被拒绝")
            self.assertTrue(text.strip(), path)
        error, text = self.call("read_business_knowledge", {"path": "missing.md"})
        self.assertTrue(error)
        self.assertIn("search_business_knowledge", text)

    def test_search_rejects_blank_query_and_bad_limit(self):
        for arguments in ({"query": "   "}, {"query": ""}, {"query": "提款", "limit": "many"}):
            error, text = self.call("search_business_knowledge", arguments)
            self.assertTrue(error, arguments)
            self.assertTrue(text.strip())

    def test_search_clamps_limit_into_the_allowed_range(self):
        for limit, expected in ((0, 1), (-5, 1), (999, 50), (2, 2)):
            data = self.payload("search_business_knowledge", {"query": "提款", "limit": limit})
            self.assertLessEqual(len(data["items"]), expected, limit)
        capped = self.payload("search_business_knowledge", {"query": "提款", "limit": 999})
        self.assertEqual(2, len(capped["items"]))


@require_sdk
class McpStdioTest(unittest.TestCase):
    """真实 stdio 会话：验证握手、工具清单、调用结果和 stdout 只承载 JSON-RPC。"""

    def session(self, config_path: Path):
        process = subprocess.Popen(
            [sys.executable, "-m", "business_code_agent.cli", "serve-mcp",
             "--project-config", str(config_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.addCleanup(process.communicate)
        self.addCleanup(process.kill)
        return process

    def exchange(self, process, payload: dict) -> dict:
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        self.assertTrue(line, "服务没有在 stdout 输出 JSON-RPC")
        return json.loads(line)

    def call(self, process, identifier: int, name: str, arguments: dict) -> dict:
        return self.exchange(process, {"jsonrpc": "2.0", "id": identifier,
                                       "method": "tools/call",
                                       "params": {"name": name, "arguments": arguments}})["result"]

    def test_stdio_session_lists_and_calls_every_tool(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        process = self.session(write_project(root))
        initialized = self.exchange(process, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "unit-test", "version": "0"}},
        })["result"]
        self.assertEqual("2025-06-18", initialized["protocolVersion"])
        self.assertEqual("business-code-agent-knowledge", initialized["serverInfo"]["name"])
        process.stdin.write(json.dumps({"jsonrpc": "2.0",
                                        "method": "notifications/initialized"}) + "\n")
        process.stdin.flush()

        listed = self.exchange(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(sorted(TOOL_ARGUMENTS),
                         sorted(tool["name"] for tool in listed["result"]["tools"]))

        navigation = self.call(process, 3, "get_navigation", {})
        self.assertFalse(navigation["isError"])
        self.assertEqual("mcp-fixture",
                         json.loads(navigation["content"][0]["text"])["project"]["id"])
        search = self.call(process, 4, "search_business_knowledge", {"query": "提款", "limit": 1})
        self.assertFalse(search["isError"])
        self.assertEqual("withdraw-flow.md",
                         json.loads(search["content"][0]["text"])["items"][0]["path"])
        guide = self.call(process, 5, "get_system_guide", {"systemId": "channel"})
        self.assertFalse(guide["isError"])
        self.assertFalse(json.loads(guide["content"][0]["text"])["published"])
        document = self.call(process, 6, "read_business_knowledge", {"path": "withdraw-flow.md"})
        self.assertFalse(document["isError"])
        self.assertIn("提款申请先由渠道 H5 提交",
                      json.loads(document["content"][0]["text"])["content"])

        rejected = self.call(process, 7, "search_business_knowledge",
                             {"query": "提款", "bogus": 1})
        self.assertTrue(rejected["isError"])
        self.assertIn("未知参数：bogus", rejected["content"][0]["text"])
        unscoped = self.call(process, 8, "get_navigation", {"unknown": True})
        self.assertTrue(unscoped["isError"])
        self.assertIn("只接受参数：无", unscoped["content"][0]["text"])
        missing = self.call(process, 9, "get_system_guide", {"systemId": "nginx"})
        self.assertTrue(missing["isError"])
        self.assertIn("未登记的 systemId", missing["content"][0]["text"])

        process.stdin.close()
        self.assertEqual(0, process.wait(timeout=60))
        # stdout 只承载 JSON-RPC；启动信息写入 stderr。
        self.assertIn("stdio", process.stderr.read())

    def test_cli_rejects_ambiguous_project_options(self):
        cases = [
            (["serve-mcp"], "需要 --project-config 或 --project-registry"),
            (["serve-mcp", "--project-config", "a.json", "--project-registry", "b"],
             "只能二选一"),
            (["serve-mcp", "--project-id", "x"],
             "--project-id 需要与 --project-registry 一起使用"),
        ]
        for arguments, expected in cases:
            completed = subprocess.run(
                [sys.executable, "-m", "business_code_agent.cli", *arguments],
                capture_output=True, text=True, cwd=str(ROOT), timeout=180,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            self.assertNotEqual(0, completed.returncode, arguments)
            self.assertIn(expected, completed.stderr, arguments)

    def test_unreadable_project_config_fails_with_readable_error(self):
        completed = subprocess.run(
            [sys.executable, "-m", "business_code_agent.cli", "serve-mcp",
             "--project-config", "no-such-project.config.json"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("工程配置文件不存在", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


class McpOptionalDependencyTest(unittest.TestCase):
    def test_missing_sdk_reports_install_hint(self):
        from business_code_agent.mcp import server as module

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        config_path = write_project(root)
        hidden = {name: value for name, value in sys.modules.items()
                  if name == "mcp" or name.startswith("mcp.")}
        for name in hidden:
            del sys.modules[name]
        sys.modules["mcp"] = None  # 让后续 import 立即失败，模拟未安装 SDK
        try:
            with self.assertRaises(McpSdkUnavailable) as caught:
                module.build_server(bind_project(project_config=config_path))
        finally:
            sys.modules.pop("mcp", None)
            sys.modules.update(hidden)
        self.assertIn("business-code-agent[mcp]", str(caught.exception))
        self.assertIn("pip install", str(caught.exception))

    def test_importing_the_package_never_requires_the_sdk(self):
        completed = subprocess.run(
            [sys.executable, "-c",
             "import business_code_agent.mcp as package; print(package.__doc__.splitlines()[0])"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("只读业务知识", completed.stdout)

    def test_instructions_describe_read_only_boundaries(self):
        self.assertIn("只读", INSTRUCTIONS)
        self.assertIn("不证明", INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()

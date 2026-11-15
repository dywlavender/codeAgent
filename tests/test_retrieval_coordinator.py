import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from business_code_agent.codeintel.contracts.index import prepare_index
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.coordinator import RetrievalCoordinator, request_needs
from business_code_agent.query.langchain_agent.files import SourceFiles
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog
from business_code_agent.query.navigation import NAVIGATION_FILE, build_navigation
from business_code_agent.query.service import _default_runtime


ROOT = Path(__file__).resolve().parents[1]


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class RetrievalCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "CLAUDE.md").write_text("只读源码，区分原文与导航。")

    def prepare_contracts(self, extra_endpoints=0):
        demo = Path(__file__).resolve().parents[1] / "examples/contract-flow"
        for repo in ("channel", "middle"):
            shutil.copytree(demo / repo, self.workspace / "repos" / repo)
        for i in range(extra_endpoints):
            (self.workspace / "repos/middle" / f"Extra{i}.java").write_text(
                f'package demo.middle; import java.util.Map; import org.springframework.web.bind.annotation.*; '
                f'@RestController class Extra{i} {{ @GetMapping("/extra{i}") Map detail() {{ return Map.of("value", {i}); }} }}')
        config = {"applications": [{"id": r, "name": r, "repositoryId": r, "type": "BACKEND"} for r in ("channel", "middle")]}
        sources = [{"id": r, "kind": "source", "root": str(self.workspace / "repos" / r)} for r in ("channel", "middle")]
        index = self.root / "contract-index"
        snapshot = prepare_index(index, sources, config, "A")
        cid = next(k for k, e in snapshot["entries"].items() if e["symbol"] == "demo.channel.Channel.detail")
        return index, cid

    def completed_tools(self, result):
        return [e["payload"] for e in result.events if e["eventType"] == "tool" and e["payload"]["status"] == "completed"]

    def test_directory_discovery_and_overlapping_grep_hits_do_not_trigger_redundant_reads(self):
        page = self.workspace / "repos/web/page.ts"
        page.parent.mkdir(parents=True)
        (self.workspace / "repos/api").mkdir()
        (self.workspace / "repos/core").mkdir()
        page.write_text("\n".join("bankCard" if i in {10, 43, 44} else "other" for i in range(1, 80)))
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[
                {"id": "dirs", "name": "Glob", "args": {"pattern": "repos/*", "kind": "directories"}},
                {"id": "hits", "name": "Grep", "args": {"pattern": "bankCard", "path": "repos"}},
            ]),
            AIMessage(content="已读取命中的原文"),
        ])
        result = LangChainRuntime(model=model, environment={}).ask("定位相关源码", workspace=self.workspace)
        reads = [
            event["payload"] for event in result.events
            if event["eventType"] == "tool" and event["payload"]["name"] == "Read"
            and event["payload"]["status"] == "running"
        ]
        self.assertEqual([{"path": "repos/web/page.ts", "offset": 2, "limit": 78}], [read["input"] for read in reads])

    def prepare_withdraw_materials(self):
        apps = [
            {"id": "channel-h5", "name": "贷款渠道 H5", "systemId": "channel", "type": "FRONTEND"},
            {"id": "channel-service", "name": "贷款渠道服务", "systemId": "channel", "type": "BACKEND"},
            {"id": "loan-middle", "name": "贷款中台", "systemId": "middle", "type": "BACKEND"},
        ]
        mounts = {}
        for app in apps:
            repo = app["id"]
            app["repositoryId"] = repo
            path = self.workspace / "repos" / repo
            path.parent.mkdir(exist_ok=True)
            shutil.copytree(ROOT / "examples" / repo, path)
            mounts[repo] = f"repos/{repo}"
        business = self.workspace / "knowledge/business-context"
        business.mkdir(parents=True)
        shutil.copyfile(ROOT / "knowledge/baseline/withdraw-flow.md", business / "withdraw-flow.md")
        navigation = build_navigation({"applications": apps}, mounts, "A", "提款演示")
        (self.workspace / NAVIGATION_FILE).write_text(json.dumps(navigation, ensure_ascii=False))

    def test_material_question_prefetches_bound_entry_and_checks_each_evidence_role(self):
        self.prepare_withdraw_materials()
        channel = "repos/channel-service/src/main/java/demo/channel"
        middle = "repos/loan-middle/src/main/java/demo/middle"

        def ref(path, start, end, symbol):
            return {"path": path, "start": start, "end": end, "symbol": symbol}

        public = ref(f"{channel}/controller/ChannelWithdrawController.java", 63, 66, "applyWithdraw")
        remote = ref(f"{middle}/controller/MiddleWithdrawController.java", 76, 79, "applyWithdraw")
        page = ref("repos/channel-h5/src/pages/WithdrawApply.vue", 66, 78, "submitWithdraw")
        client = ref(f"{channel}/client/LoanMiddleClient.java", 13, 41, "applyWithdraw")
        forward = ref(f"{channel}/service/ChannelWithdrawService.java", 49, 51, "applyWithdraw")
        request_channel = ref(f"{channel}/model/WithdrawApplyRequest.java", 3, 13, "WithdrawApplyRequest")
        request_middle = ref(f"{middle}/model/WithdrawApplyRequest.java", 3, 13, "WithdrawApplyRequest")
        validation = ref(f"{middle}/service/WithdrawApplicationService.java", 9, 26, "validatePreconditions")
        evidence = {
            "material-entry": [public, remote],
            "material-submission": [public, page],
            "material-request": [public, remote, request_channel, request_middle],
            "material-validation": [remote, validation],
            "material-connection": [public, forward, client, remote],
        }
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[
                {"id": "evidence", "name": "Investigation", "args": {"tasks": [
                    {"id": key, "status": "supported", "refs": refs} for key, refs in evidence.items()
                ]}},
                {"id": "unrelated", "name": "Read", "args": {"path": f"{middle}/model/TaxAuthorizationRequest.java"}},
            ]),
            AIMessage(content="当前演示工程的提款申请提交没有影像输入要求。"),
        ])
        result = LangChainRuntime(model=model, environment={}, project_id="A").ask(
            "提款需要身份证影像吗？", workspace=self.workspace)
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        first = trace["calls"][0]
        read_paths = {
            json.loads(message["content"])["path"]
            for message in first["messages"] if message.get("name") == "Read"
        }
        self.assertEqual({ref["path"] for refs in evidence.values() for ref in refs}
                         | {"knowledge/business-context/withdraw-flow.md"}, read_paths)
        self.assertEqual(2, result.usage["modelCalls"])
        self.assertFalse(result.usage["separateFinalization"])
        plan = result.usage["retrievalPlan"]
        self.assertEqual(("提款", "身份证影像"), (plan["material"]["operation"], plan["material"]["material"]))
        self.assertEqual([public, remote], plan["material"]["selectedEntry"])
        tasks = {task["id"]: task for task in plan["tasks"]}
        self.assertEqual([public, remote], tasks["entry"]["evidence"])
        for key in evidence.keys() - {"material-entry"}:
            self.assertEqual("evidence_ready", tasks[key]["status"])
        self.assertNotIn("检索仍有缺口", result.answer)

    def test_material_question_with_unrelated_entry_reference_finalizes_with_gaps(self):
        self.prepare_withdraw_materials()
        unrelated = "repos/loan-middle/src/main/java/demo/middle/model/TaxAuthorizationRequest.java"
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[
                {"id": "read", "name": "Read", "args": {"path": unrelated}},
                {"id": "evidence", "name": "Investigation", "args": {"tasks": [
                    {"id": "material-entry", "status": "supported", "refs": [
                        {"path": unrelated, "start": 3, "end": 6, "symbol": "TaxAuthorizationRequest"},
                    ]},
                ]}},
            ]),
            AIMessage(content="草稿"),
            AIMessage(content="当前资料可核对提交路径，材料要求需结合该入口的实际校验。"),
        ])
        result = LangChainRuntime(model=model, environment={}, project_id="A").ask(
            "提款需要身份证影像吗？", workspace=self.workspace)
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        self.assertEqual(["investigation", "investigation", "answer"], [call["phase"] for call in trace["calls"]])
        self.assertEqual([], trace["calls"][-1]["tools"])
        self.assertEqual([], result.usage["retrievalPlan"]["material"]["selectedEntry"])
        self.assertIn("核对所选入口实际执行的校验条件", result.answer)

    def test_material_requirement_original_can_be_confirmed_when_code_entry_is_missing(self):
        directory = self.workspace / "requirements"
        directory.mkdir()
        (directory / "acceptance.md").write_text("提款申请需要上传身份证影像。\n")
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "requirements", "name": "Investigation", "args": {"tasks": [{
                    "id": "material-requirement", "status": "supported",
                    "refs": [{"path": "requirements/acceptance.md", "start": 1, "end": 1}],
                }]},
            }]),
            AIMessage(content="需求明确，代码入口未确认。"),
            AIMessage(content="需求要求提供身份证影像。当前实现缺少源码依据。"),
        ])
        result = LangChainRuntime(model=model, environment={}).ask(
            "按需求，提款需要身份证影像吗？", workspace=self.workspace)
        tasks = {task["id"]: task for task in result.usage["retrievalPlan"]["tasks"]}
        self.assertEqual("evidence_ready", tasks["material-requirement"]["status"])
        self.assertEqual("open", tasks["material-request"]["status"])
        self.assertTrue(result.usage["separateFinalization"])

    def test_known_local_file_is_read_without_contract_or_broad_search(self):
        path = self.workspace / "repos/local/math.py"
        path.parent.mkdir(parents=True)
        path.write_text("def add(a, b):\n    return a + b\n")
        runtime = LangChainRuntime(model=ToolModel(responses=[AIMessage(content="返回两数之和")]), environment={})
        result = runtime.ask("解释 repos/local/math.py 这个函数", workspace=self.workspace)
        tools = self.completed_tools(result)
        self.assertEqual(["Read"], [t["name"] for t in tools])
        self.assertEqual("workflow", tools[0]["origin"])
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        self.assertEqual(1, len(trace["calls"]))
        self.assertIn("return a + b", str(trace["calls"][0]["messages"]))
        self.assertEqual("source_read", result.usage["retrievalPlan"]["tasks"][0]["status"])

    def test_explicit_endpoint_gets_connections_fields_and_branches_before_model(self):
        index, _ = self.prepare_contracts()
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("查询不能解析")), \
             patch("business_code_agent.codeintel.contracts.query.ContractIndex.find", return_value=[]):
            result = LangChainRuntime(model=ToolModel(responses=[AIMessage(content="按已交付分支解释，页面尚未确认")]),
                                      environment={}, contract_root=index, project_id="A").ask(
                "GET /orders/{id} 为什么状态没有区分，页面要怎么改", workspace=self.workspace)
        names = [t["name"] for t in self.completed_tools(result)]
        self.assertIn("TraceResponseFields", names)
        self.assertIn("TraceBusinessRules", names)
        self.assertEqual(1, result.usage["modelCalls"])
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        inputs = str(trace["calls"][0]["messages"])
        self.assertIn("HTTP_RESPONSE", inputs)
        self.assertIn("detail.booking.retryCount", inputs)
        self.assertIn("TraceBusinessRules", inputs)
        consumer = next(t for t in result.usage["retrievalPlan"]["tasks"] if t["id"] == "consumer")
        self.assertEqual("unavailable", consumer["status"])
        self.assertIn("仍未确认", consumer["note"])
        self.assertTrue(any(e["eventType"] == "retrieval" for e in trace["events"]))

    def test_model_selected_contract_creates_new_program_tasks_mid_investigation(self):
        index, cid = self.prepare_contracts()
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{
            "id": "select", "name": "TraceContract", "args": {"contract_id": cid}}]),
            AIMessage(content="依据分支与返回数据解释")])
        result = LangChainRuntime(model=model, environment={}, contract_root=index, project_id="A", max_calls=4).ask(
            "为什么展示状态相同", workspace=self.workspace)
        automatic = [t for t in self.completed_tools(result) if t.get("origin") == "workflow"]
        self.assertIn("TraceResponseFields", [t["name"] for t in automatic])
        self.assertIn("TraceBusinessRules", [t["name"] for t in automatic])
        trace = json.loads(Path(result.usage["tracePath"]).read_text())
        self.assertEqual(2, len(trace["calls"]))
        self.assertIn("TraceResponseFields", str(trace["calls"][1]["messages"]))

    def test_two_explicit_interfaces_keep_both_retrieval_targets(self):
        index, _ = self.prepare_contracts()
        result = LangChainRuntime(model=ToolModel(responses=[AIMessage(content="分别核对两个接口")]),
            environment={}, contract_root=index, project_id="A").ask(
                "比较 GET /orders/{id} 和 GET /internal/orders/{id} 返回哪些字段", workspace=self.workspace)
        contracts = [t for t in result.usage["retrievalPlan"]["tasks"] if t["id"].startswith("contract:")]
        self.assertEqual(2, len(contracts))
        self.assertEqual(1, result.usage["modelCalls"])

    def test_single_ranked_candidate_is_not_automatically_a_confirmed_entry(self):
        files = SourceFiles(self.workspace)
        coordinator = RetrievalCoordinator("查询贷款进度", files, {"applications": []}, {"FindCapability"})
        messages = [HumanMessage(content="查询贷款进度"), ToolMessage(name="FindCapability", tool_call_id="find", content=json.dumps({
            "candidates": [{"contractId": "C1", "route": "/orders/{id}", "symbol": "Channel.detail",
                            "location": {"path": "repos/channel/Channel.java", "start": 1, "end": 3}, "score": 100}]}))]
        coordinator.observe(messages)
        self.assertFalse(coordinator.bindings)
        self.assertFalse(coordinator.snapshot()["semanticProof"])

    def test_stale_index_creates_current_source_read_without_reindexing(self):
        index, _ = self.prepare_contracts()
        path = self.workspace / "repos/channel/application.yml"
        path.write_text(path.read_text().replace("8092", "9999"))
        with patch("business_code_agent.codeintel.contracts.index.parse_java", side_effect=AssertionError("不能重建")):
            result = LangChainRuntime(model=ToolModel(responses=[AIMessage(content="配置已经变化，连接未确认")]),
                                      environment={}, contract_root=index, project_id="A").ask(
                "GET /orders/{id} 返回哪些字段", workspace=self.workspace)
        reads = [t for t in self.completed_tools(result) if t["name"] == "Read"]
        self.assertTrue(any("9999" in t["output"] for t in reads))
        plan = result.usage["retrievalPlan"]
        self.assertTrue(any(t["status"] == "partial" for t in plan["tasks"]))

    def test_question_requirements_are_combined_and_field_list_skips_business_branches(self):
        needs = request_needs("跨系统状态为什么异常，页面如何改")
        self.assertTrue(all(needs[k] for k in ("relations", "business", "consumer", "changes")))
        self.assertFalse(request_needs("接口返回哪些字段及字段类型")["business"])
        self.assertTrue(request_needs("提款是否必须上传身份证影像？")["material"])
        self.assertFalse(request_needs("这个接口需要修改配置文件吗？")["material"])

    def test_auto_index_selection_does_not_create_missing_index(self):
        from business_code_agent.query.materials import MaterialManifest

        with patch.dict(os.environ, {"BUSINESS_CODE_AGENT_RUNTIME": "langchain"}, clear=True):
            runtime = _default_runtime(index_root=self.root / "retrieval-index", project_id="A")
        preparation = runtime.prepare(workspace=self.workspace, materials=MaterialManifest.from_workspace(self.workspace))
        statuses = {tool.capability: (tool.policy, tool.status) for tool in preparation.tool_statuses}
        self.assertEqual(("optional", "unavailable"), statuses["search_code"])
        self.assertEqual(("optional", "unavailable"), statuses["contract_trace"])
        self.assertIsNone(preparation.search_code)
        self.assertIsNone(preparation.contract_index)
        self.assertFalse((self.root / "retrieval-index").exists())

    def test_current_search_original_is_reused_without_redundant_read(self):
        path = self.workspace / "repos/local/math.py"
        path.parent.mkdir(parents=True)
        path.write_text("def add(a, b):\n    return a + b\n")
        coordinator = RetrievalCoordinator("加法如何实现", SourceFiles(self.workspace), {"applications": []}, {"Read", "SearchCode"})
        coordinator.next_batch()
        coordinator.observe([HumanMessage(content="加法如何实现"), ToolMessage(name="SearchCode", tool_call_id="search",
            content=json.dumps({"locations": [{"path": "repos/local/math.py", "lineStart": 1, "lineEnd": 2, "nextLine": None}]}))])
        self.assertFalse(coordinator.next_batch())

    def test_removed_answer_context_is_not_reported_as_still_delivered(self):
        coordinator = RetrievalCoordinator("解释函数", SourceFiles(self.workspace), {"applications": []}, {"Read"})
        coordinator.task("entry", "读取入口", status="source_read", evidence=[{"path": "repos/a.java", "start": 1, "end": 3}])
        coordinator.revalidate(EvidenceCatalog("A", ()))
        self.assertTrue(coordinator.tasks["entry"]["contextMissing"])
        self.assertIn("未保留全部原文", coordinator.qualify("已有回答"))

    def test_end_of_file_read_satisfies_a_larger_repeated_read(self):
        path = self.workspace / "repos/local/math.py"
        path.parent.mkdir(parents=True)
        path.write_text("def add(a, b):\n    return a + b\n")
        files = SourceFiles(self.workspace)
        coordinator = RetrievalCoordinator("解释函数", files, {"applications": []}, {"Read"})
        coordinator.observe([HumanMessage(content="解释函数"), ToolMessage(name="Read", tool_call_id="read",
                             content=json.dumps(files.read("repos/local/math.py", limit=20)))])
        coordinator.enqueue("Read", {"path": "repos/local/math.py", "offset": 1, "limit": 100}, "重复读取")
        self.assertFalse(coordinator.next_batch())

    def test_program_actions_and_explicit_entry_targets_have_no_total_cap(self):
        index, _ = self.prepare_contracts(extra_endpoints=2)
        from business_code_agent.query.langchain_agent.contracts import ContractState
        files = SourceFiles(self.workspace)
        nav = {"applications": []}
        contracts = ContractState(index, "A", files, "", nav)
        coordinator = RetrievalCoordinator("比较 GET /orders/{id}、GET /internal/orders/{id}、GET /extra0、GET /extra1 的返回字段",
            files, nav, {t.name for t in [*files.tools(), *contracts.tools()]}, contracts)
        self.assertEqual(4, len(coordinator.bindings))
        for i in range(40):
            coordinator.enqueue("Grep", {"pattern": f"value-{i}"}, "继续按线索检索")
        dispatched = []
        while batch := coordinator.next_batch():
            dispatched.extend(batch)
        self.assertGreater(len(dispatched), 24)
        self.assertIsNone(coordinator.snapshot()["actionBudget"])


if __name__ == "__main__":
    unittest.main()

"""Background evaluation batches: paired execution, cancel, reviews, recovery."""
from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from business_code_agent.evaluation.service import FINISHED_STATUSES, EvaluationError, EvaluationService
from business_code_agent.query_agent.runtime import RuntimeResult


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def make_project(root: Path) -> Path:
    """A tiny project: two repos, an overview, a flow doc and requirements."""
    config = root / "project.config.json"
    for repo in ("repo-a", "repo-b"):
        target = root / repo
        (target / "src").mkdir(parents=True)
        (target / "README.md").write_text(f"# {repo}\n", encoding="utf-8")
        (target / "src" / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    baseline = root / "knowledge" / "baseline"
    baseline.mkdir(parents=True)
    (baseline / "project-overview.md").write_text("# 项目总览\n", encoding="utf-8")
    (baseline / "withdraw-flow.md").write_text("# 提款流程\n", encoding="utf-8")
    (root / "requirements").mkdir()
    (root / "requirements" / "需求.md").write_text("需求原文\n", encoding="utf-8")
    config.write_text(json.dumps({
        "project": {"id": "demo", "name": "演示项目"},
        "repositories": [
            {"id": "a", "localPath": "repo-a"},
            {"id": "b", "localPath": "repo-b"},
        ],
        "knowledge": {"baselineRoot": "knowledge/baseline"},
    }, ensure_ascii=False), encoding="utf-8")
    return config


def suite_payload(**overrides):
    payload = {
        "name": "演示回归题库",
        "scope": "演示项目；已知题库",
        "origin": "known_regression",
        "cases": [
            {"id": "flow", "question": "说明流程", "checks": ["入口正确", "阶段齐全"],
             "category": "business_flow"},
            {"id": "coupon", "question": "调用链", "checks": ["链路完整"], "category": "code_control"},
        ],
    }
    payload.update(overrides)
    return payload


class ScriptedRuntime:
    """Answer questions and judge reviews without any model service."""

    runtime_name = "SCRIPTED"

    def __init__(self, answers=None, *, release=None, judge_ok=True, judge_release=None):
        self.answers = answers or {}
        self.release = release
        self.judge_ok = judge_ok
        self.judge_release = judge_release
        self.judge_entered = threading.Event()
        self.judge_cancel_seen = False

    def build_command(self, question, *, workspace, session_id=None):
        return ["claude", "-p", question]

    def ask(self, question, *, workspace, session_id=None, event_callback=None, cancel_check=None, repositories=None):
        if question.startswith("你是代码问答评审"):
            self.judge_entered.set()
            if self.judge_release is not None:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if cancel_check and cancel_check():
                        self.judge_cancel_seen = True
                        return RuntimeResult("", "judge-session", status="cancelled")
                    if self.judge_release.is_set():
                        break
                    time.sleep(0.02)
                if cancel_check and cancel_check():
                    self.judge_cancel_seen = True
                    return RuntimeResult("", "judge-session", status="cancelled")
                if not self.judge_release.is_set():
                    raise RuntimeError("judge was never released")
            if not self.judge_ok:
                return RuntimeResult("这不是 JSON", "judge-session")
            payload = json.loads(question.split("\n", -1)[-1])
            candidate = payload["candidateAnswer"]
            return RuntimeResult(json.dumps(self._review(candidate, len(payload["checks"]))), "judge-session")
        if self.release is not None:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if cancel_check and cancel_check():
                    return RuntimeResult("", "session", status="cancelled")
                if self.release.is_set():
                    break
                time.sleep(0.02)
            if cancel_check and cancel_check():
                return RuntimeResult("", "session", status="cancelled")
            if not self.release.is_set():
                raise RuntimeError("runtime was never released")
        answer = f"答案正文：{question[:20]}"
        if event_callback:
            event_callback({"sequence": 1, "eventType": "tool", "payload": {
                "id": "t1", "name": "Read", "status": "completed",
                "input": {"file_path": str(workspace) + "/src/Main.java"}, "output": "class Main {}"}})
        return RuntimeResult(answer, "answer-session",
                             usage={"input_tokens": 10, "output_tokens": 5})

    @staticmethod
    def _review(candidate, count=1):
        check = {"met": True, "candidateQuote": candidate[:12], "reason": "引用一致",
                 "evidence": "src/Main.java:1"}
        return {"checks": [dict(check) for _ in range(count)], "issues": []}


class EvaluationServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = make_project(self.root)

    def tearDown(self):
        if hasattr(self, "service"):
            for handle in list(self.service._active.values()):
                handle["cancel"].set()
                handle["thread"].join(timeout=5)
        self._tmp.cleanup()

    def make_service(self, runtime, **kwargs):
        defaults = dict(project_config=str(self.config), data_root=str(self.root / ".data" / "evaluations"),
                        timeout_seconds=5, workers=2)
        defaults.update(kwargs)
        service = EvaluationService(runtime_factory=lambda job=None: runtime, **defaults)
        # AST is now a user-triggered preparation step; tests make that step
        # explicit instead of relying on batch startup to generate it.
        service.ast_service.generate()
        self.assertTrue(wait_for(lambda: service.ast_service.status().get("status") == "available"))
        return service

    def start_and_wait(self, service, payload=None, expect="completed"):
        body = {"suiteId": self.create_suite(service), "repeats": 1}
        body.update(payload or {})
        started = service.start(body)
        eval_id = started["evaluationId"]
        self.assertTrue(wait_for(lambda: service.get_evaluation(eval_id)["status"]
                                 in ("completed", "cancelled", "failed", "interrupted")))
        # 等批次线程完全退出，避免后台线程与用例清理竞争。
        self.assertTrue(wait_for(lambda: eval_id not in service._active))
        state = service.get_evaluation(eval_id)
        self.assertEqual(expect, state["status"], state.get("error"))
        return eval_id, state

    def create_suite(self, service, **overrides):
        return service.create_suite(suite_payload(**overrides))["id"]

    def test_example_import_uses_project_declared_path(self):
        example = self.root / "fixtures" / "cases.json"
        example.parent.mkdir(parents=True)
        example.write_text(json.dumps({
            "name": "配置示例",
            "cases": [{"id": "declared", "question": "配置问题", "referenceAnswer": "参考", "checks": ["结论"]}],
        }, ensure_ascii=False), encoding="utf-8")
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["evaluation"] = {"examples": ["fixtures/cases.json"]}
        self.config.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        service = EvaluationService(project_config=str(self.config), data_root=str(self.root / ".data" / "evaluations"))
        imported = service.create_suite({"example": True})
        self.assertEqual("配置示例", imported["name"])
        self.assertEqual("declared", imported["cases"][0]["id"])

    def test_start_completes_with_paired_state_and_report(self):
        self.service = self.make_service(ScriptedRuntime())
        eval_id, state = self.start_and_wait(self.service)
        self.assertEqual(6, state["progress"]["answers"]["total"])
        self.assertEqual(6, state["progress"]["answers"]["completed"])
        self.assertEqual({"total": 6, "completed": 6, "usable": 6, "failed": 0}, state["progress"]["judge"])
        rows = {(row["caseId"], row["repeat"]) for row in state["rows"]}
        self.assertEqual({("flow", 1), ("coupon", 1)}, rows)
        for row in state["rows"]:
            self.assertEqual({"code_only", "optional", "ast"}, set(row["runs"]))
        summary = state["summaries"]["model"]
        self.assertEqual(2, summary["qualityBlocks"])
        self.assertEqual(2, summary["costBlocks"])
        self.assertEqual(3, summary["aggregates"]["code_only"]["score"])
        self.assertEqual(3, summary["aggregates"]["code_only"]["possible"])
        results = json.loads((self.service.root / eval_id / "results.json").read_text())
        self.assertEqual(6, len(results))
        self.assertIsNotNone(results[0]["toolTrace"][0]["firstEventSeconds"])
        self.assertTrue((self.service.root / eval_id / "report.md").is_file())

    def test_auto_judge_requires_fixed_rubric_and_answer_only_is_explicit(self):
        self.service = self.make_service(ScriptedRuntime())
        suite_id = self.create_suite(self.service, cases=[
            {"id": "without-rubric", "question": "没有要点的问题", "referenceAnswer": "参考答案", "checks": []},
        ])
        with self.assertRaisesRegex(EvaluationError, "没有评分要点"):
            self.service.start({"suiteId": suite_id, "repeats": 1, "judge": True})
        started = self.service.start({"suiteId": suite_id, "repeats": 1, "judge": False})
        eval_id = started["evaluationId"]
        self.assertTrue(wait_for(lambda: self.service.get_evaluation(eval_id)["status"] in FINISHED_STATUSES))
        self.assertTrue(wait_for(lambda: eval_id not in self.service._active))
        state = self.service.get_evaluation(eval_id)
        self.assertEqual("completed", state["status"])
        self.assertIsNone(state["progress"]["judge"])
        self.assertEqual(3, state["progress"]["answers"]["completed"])
        self.assertEqual(0, state["summaries"]["model"]["qualityBlocks"])

    def test_retry_failed_keeps_exact_failed_arm_and_frozen_inputs(self):
        class JobRuntime(ScriptedRuntime):
            def __init__(self, job):
                super().__init__()
                self.job = job or {}

            def ask(self, question, **kwargs):
                if not question.startswith("你是代码问答评审") and self.job.get("case", {}).get("id") == "flow" \
                        and self.job.get("arm") == "ast":
                    raise RuntimeError("controlled answer failure")
                return super().ask(question, **kwargs)

        self.service = EvaluationService(
            project_config=str(self.config), data_root=str(self.root / ".data" / "evaluations"),
            timeout_seconds=5, workers=2,
            runtime_factory=lambda job=None: JobRuntime(job),
        )
        self.service.ast_service.generate()
        self.assertTrue(wait_for(lambda: self.service.ast_service.status().get("status") == "available"))
        suite_id = self.create_suite(self.service)
        started = self.service.start({"suiteId": suite_id, "repeats": 1,
                                      "astVersionId": self.service.ast_service.status()["currentVersionId"]})
        eval_id = started["evaluationId"]
        self.assertTrue(wait_for(lambda: self.service.get_evaluation(eval_id)["status"] in FINISHED_STATUSES))
        self.assertTrue(wait_for(lambda: eval_id not in self.service._active))
        original_dir = self.service.root / eval_id
        original_protocol = json.loads((original_dir / "protocol.json").read_text(encoding="utf-8"))
        frozen_text = (original_dir / "inputs" / "repo-0" / "src" / "Main.java").read_text(encoding="utf-8")
        self.assertEqual("failed", next(row for row in self.service.get_evaluation(eval_id)["rows"]
                                         if row["caseId"] == "flow")["runs"]["ast"]["status"])
        suite_path = self.service._suite_path(suite_id)
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        suite["cases"][0]["question"] = "当前题库已被修改"
        suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
        (self.root / "repo-a" / "src" / "Main.java").write_text("class Main { int current; }\n", encoding="utf-8")

        retry = self.service.retry_failed(eval_id)
        retry_id = retry["evaluationId"]
        retry_protocol = json.loads((self.service.root / retry_id / "protocol.json").read_text(encoding="utf-8"))
        self.assertEqual(["flow-r1-ast"], [f"{job['case']}-r{job['repeat']}-{job['arm']}"
                                            for job in retry_protocol["jobs"]])
        self.assertEqual(["flow"], [case["id"] for case in retry_protocol["cases"]])
        self.assertEqual(original_protocol["referencePreparation"], retry_protocol["referencePreparation"])
        self.assertEqual(frozen_text, (self.service.root / retry_id / "inputs" / "repo-0" / "src" / "Main.java").read_text(encoding="utf-8"))
        self.assertEqual("失败任务重试", self.service.get_evaluation(retry_id)["suite"]["name"].split(" · ")[-1])
        self.assertEqual(eval_id, self.service.get_evaluation(retry_id)["settings"]["retryOf"])
        self.assertEqual("failed", next(row for row in self.service.get_evaluation(eval_id)["rows"]
                                        if row["caseId"] == "flow")["runs"]["ast"]["status"])
        self.assertTrue(wait_for(lambda: retry_id not in self.service._active))

    def test_five_arm_batch_shares_ast_and_isolates_business_flows(self):
        self.service = self.make_service(ScriptedRuntime())
        ast_version_id = self.service.ast_service.status()["currentVersionId"]
        eval_id, state = self.start_and_wait(self.service, {"comparison": "diagnostic_ast",
                                                             "astVersionId": ast_version_id})
        self.assertEqual(5, len(state["arms"]))
        batch = self.service.root / eval_id
        ast = batch / "inputs/baselines/ast"
        hybrid = batch / "inputs/baselines/overview_ast"
        self.assertEqual((ast / "ast/repo-0-index.md").read_text(),
                         (hybrid / "ast/repo-0-index.md").read_text())
        self.assertFalse((hybrid / "withdraw-flow.md").exists())
        self.assertIn("项目总览", (hybrid / "project-overview.md").read_text())

    def test_abcde_batch_freezes_each_material_arm(self):
        business_context = self.root / "knowledge" / "business-context"
        code_map = self.root / "knowledge" / "generated-code-map"
        business_context.mkdir(parents=True)
        code_map.mkdir(parents=True)
        (business_context / "boundaries.md").write_text("业务补充知识\n", encoding="utf-8")
        (code_map / "project-index.md").write_text("自动代码地图\n", encoding="utf-8")
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["knowledge"].update({
            "businessContextRoot": "knowledge/business-context",
            "baselineRoot": "knowledge/baseline",
            "codeMapRoot": "knowledge/generated-code-map",
        })
        self.config.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.service = self.make_service(ScriptedRuntime())
        eval_id, state = self.start_and_wait(self.service, {"comparison": "abcde"})
        self.assertEqual({"raw", "old_baseline", "code_map", "business_context", "code_map_context"},
                         set(state["arms"]))
        self.assertEqual(10, state["progress"]["answers"]["completed"])
        batch = self.service.root / eval_id
        self.assertTrue((batch / "inputs/baselines/old_baseline/project-overview.md").is_file())
        self.assertTrue((batch / "inputs/baselines/code_map/project-index.md").is_file())
        self.assertTrue((batch / "inputs/baselines/business_context/boundaries.md").is_file())
        self.assertTrue((batch / "inputs/baselines/code_map_context/business-context/boundaries.md").is_file())
        self.assertTrue((batch / "inputs/baselines/code_map_context/generated-code-map/project-index.md").is_file())
        summary = state["summaries"]["model"]
        self.assertEqual(2, summary["qualityBlocks"])
        self.assertEqual(8, len(summary["pairs"]))

    def test_diagnostic_batch_freezes_only_overview_and_preserves_requirements(self):
        self.service = self.make_service(ScriptedRuntime())
        eval_id, state = self.start_and_wait(self.service, {"comparison": "diagnostic"})
        self.assertEqual("diagnostic", state["settings"]["comparison"])
        self.assertEqual({"code_only", "overview", "optional"}, set(state["arms"]))
        root = self.service.root / eval_id
        self.assertEqual(["project-overview.md"], sorted(p.name for p in (root / "inputs/baselines/overview").iterdir()))
        self.assertTrue((root / "inputs/baselines/optional/withdraw-flow.md").is_file())
        self.assertTrue((root / "inputs/requirements/需求.md").is_file())
        self.assertTrue((root / "investigation-review.json").is_file())
        summary = json.loads((root / "diagnosis-summary.json").read_text())
        self.assertEqual("overview", summary["fullVsOverview"]["controlArm"])

    def test_review_source_never_borrows_model_scores(self):
        self.service = self.make_service(ScriptedRuntime())
        eval_id, state = self.start_and_wait(self.service)
        flow_runs = state["rows"][0]["runs"]
        answer_ids = sorted(run["answerId"] for run in flow_runs.values())
        self.service.save_review(eval_id, answer_ids[0],
                                 {"checks": [1, 1], "issues": ["一条笔误"], "note": "人工复核"})
        state = self.service.get_evaluation(eval_id)
        review_summary = state["summaries"]["review"]
        self.assertEqual(0, review_summary["qualityBlocks"])  # 配对双方都有复核才计分
        model_summary = state["summaries"]["model"]
        self.assertEqual(2, model_summary["qualityBlocks"])
        self.service.save_review(eval_id, answer_ids[1], {"checks": [1, 0], "issues": []})
        self.service.save_review(eval_id, answer_ids[2], {"checks": [0, 1], "issues": []})
        state = self.service.get_evaluation(eval_id)
        review_summary = state["summaries"]["review"]
        self.assertEqual(1, review_summary["qualityBlocks"])
        self.assertEqual(2, review_summary["aggregates"]["optional"]["possible"])
        detail = self.service.get_case(eval_id, "flow", 1)
        revisions = detail["runs"]["code_only"]["reviews"]
        self.assertEqual(1, len(revisions))
        self.assertEqual("user", revisions[0]["operatorType"])

    def test_save_review_validates_length_and_operator(self):
        self.service = self.make_service(ScriptedRuntime())
        eval_id, state = self.start_and_wait(self.service)
        answer_id = state["rows"][0]["runs"]["code_only"]["answerId"]
        with self.assertRaises(EvaluationError):
            self.service.save_review(eval_id, answer_id, {"checks": [1]})
        with self.assertRaises(EvaluationError):
            self.service.save_review(eval_id, answer_id,
                                     {"checks": [1, 1], "operatorType": "import"})
        result = self.service.save_review(eval_id, answer_id,
                                          {"checks": [1, None], "operatorType": "import",
                                           "operator": "执行助手A"})
        self.assertEqual("partial", result["reviewStatus"])
        revisions = self.service.get_case(eval_id, "flow", 1)["runs"]["code_only"]["reviews"]
        self.assertEqual("执行助手A", revisions[0]["operator"])
        self.assertEqual("import", revisions[0]["operatorType"])

    def test_failed_judge_keeps_answer_and_requires_review(self):
        self.service = self.make_service(ScriptedRuntime(judge_ok=False))
        eval_id, state = self.start_and_wait(self.service)
        self.assertEqual(6, state["progress"]["judge"]["failed"])
        for row in state["rows"]:
            for run in row["runs"].values():
                self.assertEqual("failed", run["modelStatus"])
                self.assertEqual("none", run["reviewStatus"])  # 人工复核状态与模型初评分离
        summary = state["summaries"]["model"]
        self.assertEqual(0, summary["qualityBlocks"])
        detail = self.service.get_case(eval_id, "flow", 1)
        self.assertIn("答案正文", detail["runs"]["code_only"]["answer"])

    def test_cancel_stops_scheduling_and_keeps_results(self):
        release = threading.Event()
        self.service = self.make_service(ScriptedRuntime(release=release), workers=1)
        started = self.service.start({"suiteId": self.create_suite(self.service), "repeats": 1})
        eval_id = started["evaluationId"]
        duplicate = self.service.start({"suiteId": "suite-whatever", "repeats": 1})
        self.assertEqual(eval_id, duplicate["evaluationId"])
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(wait_for(lambda: any(job["status"] == "running"
                                             for job in self.service._load_state(eval_id)["jobs"])))
        cancelled = self.service.cancel(eval_id)
        self.assertIn(cancelled["status"], ("cancelling", "cancelled"))
        release.set()
        self.assertTrue(wait_for(lambda: self.service.get_evaluation(eval_id)["status"]
                                 in ("completed", "cancelled", "failed", "interrupted")))
        state = self.service.get_evaluation(eval_id)
        self.assertEqual("cancelled", state["status"])
        self.assertEqual(0, state["progress"]["answers"]["completed"])
        self.assertEqual(6, state["progress"]["answers"]["cancelled"])
        results = json.loads((self.service.root / eval_id / "results.json").read_text())
        self.assertEqual(6, len(results))
        self.assertTrue(all(item["status"] in ("cancelled", "skipped") for item in results))

    def test_restart_marks_running_batch_interrupted(self):
        release = threading.Event()
        self.service = self.make_service(ScriptedRuntime(release=release), workers=1)
        started = self.service.start({"suiteId": self.create_suite(self.service)})
        eval_id = started["evaluationId"]
        # 等首个任务的 on_start 落盘，模拟批次运行中被重启
        self.assertTrue(wait_for(lambda: any(job["status"] == "running"
                                             for job in self.service._load_state(eval_id)["jobs"])))
        recovered = EvaluationService(project_config=str(self.config),
                                      data_root=str(self.root / ".data" / "evaluations"),
                                      timeout_seconds=5,
                                      runtime_factory=lambda job=None: ScriptedRuntime())
        self.assertTrue(wait_for(lambda: recovered.get_evaluation(eval_id)["status"] == "interrupted"))
        state = recovered.get_evaluation(eval_id)
        self.assertIn("服务重启", "".join(state["notes"]))
        self.assertEqual("interrupted", recovered.cancel(eval_id)["status"])
        release.set()

    def test_suite_revisions_and_project_binding(self):
        self.service = self.make_service(ScriptedRuntime())
        suite_id = self.create_suite(self.service)
        suite = self.service.update_suite(suite_id, {"cases": suite_payload()["cases"] + [
            {"id": "boundary", "question": "边界", "checks": ["确认无实现"]}]})
        self.assertEqual(2, suite["revision"])
        self.assertEqual(3, len(suite["cases"]))
        revisions = sorted((self.service.suites_root / suite_id / "revisions").glob("*.json"))
        self.assertEqual(["1.json"], [path.name for path in revisions])
        # 绑定其他项目配置的题库不能导入
        foreign = self.root / "foreign"
        foreign.mkdir()
        foreign_config = foreign / "project.config.json"
        foreign_config.write_text(json.dumps({"project": {"id": "other"}}), encoding="utf-8")
        import_file = foreign / "suite.json"
        import_file.write_text(json.dumps({**suite_payload(), "projectConfig": "project.config.json"}),
                               encoding="utf-8")
        with self.assertRaises(EvaluationError):
            self.service.create_suite({"importPath": str(import_file)})
        # 同项目的相对路径导入成功
        local_file = self.root / "local-suite.json"
        local_file.write_text(json.dumps({**suite_payload(name="本地导入"), "projectConfig": "project.config.json"}),
                              encoding="utf-8")
        imported = self.service.create_suite({"importPath": str(local_file)})
        self.assertEqual(str(self.config.resolve()), imported["projectConfig"])
        self.assertEqual("import:local-suite.json", imported["source"])

    def test_setup_reports_baseline_and_last_used_suite(self):
        self.service = self.make_service(ScriptedRuntime())
        suite_id = self.create_suite(self.service)
        self.service.start({"suiteId": suite_id, "repeats": 1})
        setup = self.service.setup()
        self.assertTrue(setup["baseline"]["hasOverview"])
        self.assertIn("项目总览", setup["baseline"]["note"])
        self.assertTrue(setup["cli"]["available"] or setup["cli"]["command"] == "claude")
        self.assertEqual(suite_id, setup["lastUsedSuiteId"])
        self.assertTrue(setup["suites"])

    def test_start_requires_suite_and_validates_case_subset(self):
        self.service = self.make_service(ScriptedRuntime())
        with self.assertRaises(EvaluationError):
            self.service.start({})
        suite_id = self.create_suite(self.service)
        with self.assertRaises(EvaluationError):
            self.service.start({"suiteId": suite_id, "caseIds": ["missing"]})
        started = self.service.start({"suiteId": suite_id, "repeats": 1, "caseIds": ["flow"]})
        self.assertFalse(started["duplicate"])
        eval_id = started["evaluationId"]
        self.assertTrue(wait_for(lambda: self.service.get_evaluation(eval_id)["status"] == "completed"))
        self.assertTrue(wait_for(lambda: eval_id not in self.service._active))
        state = self.service.get_evaluation(eval_id)
        self.assertEqual({"flow"}, {row["caseId"] for row in state["rows"]})

    def test_cancel_reaches_running_judge(self):
        runtime = ScriptedRuntime(judge_release=threading.Event())
        self.service = self.make_service(runtime, workers=1)
        started = self.service.start({"suiteId": self.create_suite(self.service), "repeats": 1})
        eval_id = started["evaluationId"]
        # 等第一个答案完成并进入初评调用
        self.assertTrue(wait_for(lambda: runtime.judge_entered.is_set()))
        self.service.cancel(eval_id)
        self.assertTrue(wait_for(lambda: self.service.get_evaluation(eval_id)["status"]
                                 in ("completed", "cancelled", "failed", "interrupted")))
        self.assertTrue(wait_for(lambda: eval_id not in self.service._active))
        self.assertTrue(runtime.judge_cancel_seen, "取消信号必须传入评分调用")
        self.assertEqual("cancelled", self.service.get_evaluation(eval_id)["status"])
        results = json.loads((self.service.root / eval_id / "results.json").read_text())
        judged = [item for item in results if item.get("review")]
        self.assertTrue(judged)
        self.assertEqual("cancelled", judged[0]["review"]["status"])
        # 已完成答案保留，可查看
        answer = (self.service.root / eval_id / "runs" / judged[0]["id"] / "answer.md").read_text()
        self.assertIn("答案正文", answer)
        state = self.service.get_evaluation(eval_id)
        self.assertEqual(0, state["progress"]["judge"]["usable"])
        self.assertEqual(0, state["progress"]["judge"]["failed"])

    def test_rejudge_preserves_human_review_and_coverage(self):
        runtime = ScriptedRuntime(judge_ok=False)
        self.service = self.make_service(runtime)
        eval_id, state = self.start_and_wait(self.service)
        answer_id = state["rows"][0]["runs"]["code_only"]["answerId"]
        self.service.save_review(eval_id, answer_id, {"checks": [1, 1], "issues": []})
        runtime.judge_ok = True
        self.service.rejudge(eval_id)
        self.assertTrue(wait_for(lambda: self.service._load_state(eval_id).get("rejudge", {}).get("status")
                                 in ("completed", "cancelled", "failed")))
        self.assertTrue(wait_for(lambda: eval_id not in self.service._active))
        state = self.service.get_evaluation(eval_id)
        # 重评不清零人工复核：覆盖数与逐题状态保持一致
        self.assertEqual("completed", state["rejudge"]["status"])
        self.assertEqual(1, state["reviewCoverage"]["reviewed"])
        run = next(row for row in state["rows"] if row["caseId"] == "flow")["runs"]["code_only"]
        self.assertEqual("reviewed", run["reviewStatus"])
        self.assertEqual("completed", run["modelStatus"])
        revisions = self.service.get_case(eval_id, "flow", 1)["runs"]["code_only"]["reviews"]
        self.assertEqual(1, len(revisions))

    def test_rejudge_replaces_model_review_and_archives_old(self):
        runtime = ScriptedRuntime(judge_ok=False)
        self.service = self.make_service(runtime)
        eval_id, state = self.start_and_wait(self.service)
        answer_id = state["rows"][0]["runs"]["code_only"]["answerId"]
        runtime.judge_ok = True
        result = self.service.rejudge(eval_id)
        self.assertFalse(result["duplicate"])
        self.assertTrue(wait_for(lambda: self.service._load_state(eval_id).get("rejudge", {}).get("status")
                                 in ("completed", "cancelled", "failed")))
        state = self.service.get_evaluation(eval_id)
        self.assertEqual("completed", state["rejudge"]["status"])
        self.assertEqual(6, state["progress"]["judge"]["usable"])
        history = self.service.get_case(eval_id, "flow", 1)["runs"]["code_only"]["reviewHistory"]
        self.assertEqual(1, len(history))
        self.assertEqual("failed", history[0]["status"])


if __name__ == "__main__":
    unittest.main()

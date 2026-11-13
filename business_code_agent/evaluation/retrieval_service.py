"""Project-owned retrieval experiments, composed beside answer evaluation."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime

from .fixed_context import answer_context
from .context_runtime import configured_connection
from .retrieval import (check_project, evaluate, freeze_structure, prepare, read_json,
                        structure_metadata, validate_suite, write_json)


class RetrievalEvaluationService:
    def __init__(self, context, *, runtime_factory=None):
        self.context = context
        self.root = context.evaluations_root / "retrieval"
        self.suites_root = context.evaluation_suites_root / "retrieval"
        self.runtime_factory = runtime_factory
        self._lock = threading.RLock()
        self._active = {}
        for path in self.root.glob("retrieval-*/state.json"):
            state = read_json(path)
            if state.get("status") == "running":
                write_json(path, {**state, "status": "interrupted", "error": "服务重启，已保留冻结输入与已有结果。"})

    def _directory(self, run_id):
        if not re.fullmatch(r"retrieval-[A-Za-z0-9_-]+", str(run_id or "")):
            raise ValueError("检索批次编号不合法")
        path = self.root / run_id
        if not path.is_dir():
            raise KeyError(run_id)
        if (path / "protocol.json").is_file():
            check_project(path, self.context.project_id)
        elif read_json(path / "state.json").get("projectId") != self.context.project_id:
            raise ValueError("不能读取其他工程的检索批次")
        return path

    def list_suites(self):
        items = []
        for path in sorted(self.suites_root.glob("*.json")):
            suite = validate_suite(read_json(path), self.context.project_id)
            items.append({"id": path.stem, "name": suite.get("name", path.stem),
                          "caseCount": len(suite["cases"]), "annotationStatus": suite.get("annotationStatus", "draft"),
                          "importedAt": suite.get("importedAt", path.stat().st_mtime)})
        items.sort(key=lambda item: (item["annotationStatus"] != "superseded", item["importedAt"]), reverse=True)
        return {"projectId": self.context.project_id, "items": items, "connection": configured_connection()}

    def import_suite(self, suite):
        validate_suite(suite, self.context.project_id)
        suite_id = str(suite.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", suite_id):
            raise ValueError("题库需要只含字母、数字、下划线和连字符的 id")
        with self._lock:
            path = self.suites_root / f"{suite_id}.json"
            if path.exists():
                raise ValueError("题库编号已存在，请用新的版本编号保留旧标注")
            write_json(path, {**suite, "importedAt": time.time()})
        return {"id": suite_id, "caseCount": len(suite["cases"])}

    def list_runs(self):
        items = []
        for path in sorted(self.root.glob("retrieval-*/state.json"), reverse=True):
            state = read_json(path)
            if state.get("projectId") == self.context.project_id:
                items.append(state)
        return {"items": items}

    def get_run(self, run_id):
        directory = self._directory(run_id)
        state = read_json(directory / "state.json")
        strategies = [read_json(p) for p in sorted((directory / "strategies").glob("*/summary.json"))]
        answers = [read_json(p) for p in sorted((directory / "answers").glob("*/result.json"))]
        structures = [item for p in sorted((directory / "structures").glob("index-*.meta.json"), reverse=True)
                      if (item := read_json(p)).get("projectId") == self.context.project_id and item.get("snapshotId") == run_id]
        if not (directory / "protocol.json").is_file():
            return {**state, "strategies": strategies, "answers": answers, "structures": structures, "report": ""}
        protocol = check_project(directory, self.context.project_id)
        report = directory / "report.md"
        return {**state, "strategies": strategies, "answers": answers, "structures": structures,
                "annotationSource": protocol["annotationSource"],
                "annotationStatus": protocol["annotationStatus"], "preparationSeconds": protocol["preparationSeconds"],
                "documents": protocol["documents"], "skippedFiles": len(protocol["skipped"]),
                "report": report.read_text(encoding="utf-8") if report.is_file() else ""}

    def get_case(self, run_id, case_id, strategy, budget):
        directory = self._directory(run_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(case_id)) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(strategy)):
            raise ValueError("案例或策略编号不合法")
        path = directory / "strategies" / strategy / f"{case_id}-{int(budget)}.json"
        if not path.is_file():
            raise KeyError(case_id)
        return read_json(path)

    def compare_context(self, run_id, payload):
        """Synchronous local comparison over a completed frozen candidate set."""
        with self._lock:
            directory = self._directory(run_id)
            if read_json(directory / "state.json")["status"] != "completed":
                raise ValueError("请等待检索批次完成")
            source = str(payload.get("sourceStrategy") or "lexical")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", source):
                raise ValueError("候选来源策略不合法")
            source_path = directory / "strategies" / source / "summary.json"
            if not source_path.is_file():
                raise ValueError("候选来源策略不存在")
            summary = read_json(source_path)
            split = payload.get("split", "development")
            if split not in {None, "development", "holdout"}:
                raise ValueError("题目集合不合法")
            strategy = "context-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            return evaluate(directory, self.context.project_id,
                            strategy=strategy, candidate_source=source,
                            context_policy=payload.get("contextPolicy", "focused"),
                            budgets=payload.get("budgets", [12000]),
                            candidate_limit=summary["candidateLimit"], split=split)

    def save_structure(self, run_id):
        with self._lock:
            directory = self._directory(run_id)
            if read_json(directory / "state.json")["status"] != "completed":
                raise ValueError("请等待检索批次完成")
            return structure_metadata(freeze_structure(self.context, directory))

    def compare_methods(self, run_id, payload):
        with self._lock:
            directory = self._directory(run_id)
            if read_json(directory / "state.json")["status"] != "completed":
                raise ValueError("请等待检索批次完成")
            split = payload.get("split", "development")
            if split not in {None, "development", "holdout"}:
                raise ValueError("题目集合不合法")
            return evaluate(directory, self.context.project_id,
                            strategy="methods-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                            retrieval_unit="methods", structure_version=payload.get("structureVersionId"),
                            context_policy=payload.get("contextPolicy", "ranked"),
                            comparison_source=payload.get("compareTo", "lexical"),
                            budgets=payload.get("budgets", [12000]),
                            candidate_limit=payload.get("candidateLimit", 40), split=split,
                            match_candidate_budget=payload.get("matchCandidateBudget", False))

    def start(self, payload):
        suite_id = str(payload.get("suiteId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", suite_id):
            raise ValueError("请选择已导入的检索题库")
        path = self.suites_root / f"{suite_id}.json"
        if not path.is_file():
            raise KeyError(suite_id)
        if read_json(path).get("annotationStatus") == "superseded":
            raise ValueError("该标注版本已被修订，请选择新的题库版本")
        budgets = payload.get("budgets", [4000, 12000])
        if not isinstance(budgets, list) or not budgets or any(type(x) is not int or not 500 <= x <= 100000 for x in budgets):
            raise ValueError("字符预算应为 500—100000 的整数列表")
        candidate_limit = payload.get("candidateLimit", 40)
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 500:
            raise ValueError("候选数量应为 1—500")
        run_id = "retrieval-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        directory = self.root / run_id
        # The background worker freezes before retrieval; GET/boot never builds data.
        directory.mkdir(parents=True)
        state = {"id": run_id, "projectId": self.context.project_id, "suiteId": suite_id,
                 "status": "running", "phase": "preparing", "budgets": budgets,
                 "createdAt": datetime.now().isoformat(), "error": None}
        write_json(directory / "state.json", state)

        def run():
            try:
                state_file = read_json(directory / "state.json")
                prepare(self.context, read_json(path), directory)
                write_json(directory / "state.json", {**state_file, "phase": "retrieving"})
                evaluate(directory, self.context.project_id, budgets=budgets, candidate_limit=candidate_limit)
                state.update(status="completed", phase="completed")
            except Exception as exc:
                state.update(status="failed", error=str(exc))
            finally:
                write_json(directory / "state.json", state)
        thread = threading.Thread(target=run, daemon=True, name=run_id)
        self._active[run_id] = thread
        thread.start()
        return {"id": run_id, "status": "running"}

    def start_answer(self, run_id, payload):
        directory = self._directory(run_id)
        state = read_json(directory / "state.json")
        if state["status"] != "completed":
            raise ValueError("请等待检索批次完成")
        case_id = str(payload.get("caseId") or "")
        strategy = str(payload.get("strategy") or "lexical")
        budget = payload.get("budget", 12000)
        self.get_case(run_id, case_id, strategy, budget)
        model = str(payload.get("model") or "configured-default").strip()
        budget_usd = float(payload.get("budgetUsd", 0.5))
        if not model or not 0 < budget_usd <= 10:
            raise ValueError("模型或单次支出上限不合法")
        with self._lock:
            if (self._active.get(f"answer-{run_id}") and self._active[f"answer-{run_id}"].is_alive()):
                raise ValueError("本批次已有固定证据答题进行中")
            runtime = self.runtime_factory(model) if self.runtime_factory else None
            thread = threading.Thread(target=answer_context, args=(directory, self.context.project_id, case_id),
                                      kwargs={"strategy": strategy, "budget": budget, "model": model,
                                              "budget_usd": budget_usd, "runtime": runtime}, daemon=True)
            self._active[f"answer-{run_id}"] = thread
            thread.start()
        return {"status": "running", "requestedModel": model}

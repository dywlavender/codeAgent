"""Background evaluation batches for the effect-verification page.

One service instance owns every batch of the current project. Batches run on
background threads independent of HTTP requests, persist their state after
every job so a page refresh or service restart can recover, and keep completed
results when a user stops a batch or a call fails.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .harness import EvaluationRuntime, write_json
from .report import build_view_report, generate_report
from .runner import (ARM_DESCRIPTIONS_ABC, ARMS_ABC, freeze_batch, load_protocol, rejudge_output,
                     run_batch, summarize_pairs, validate_suite)

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"running", "cancelling"}
FINISHED_STATUSES = {"completed", "cancelled", "failed", "interrupted"}
SUITE_ORIGINS = {"real": "真实历史问题", "known_regression": "已知回归题", "synthetic": "合成场景"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, value) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:40] or "suite"


class EvaluationError(ValueError):
    """A request cannot start or complete; the message is shown to the user."""


class EvaluationService:
    def __init__(self, *, project_config=None, data_root=None, runtime_factory=None,
                 timeout_seconds=240, workers=2, command="claude"):
        self.project_config = Path(project_config).expanduser().resolve() if project_config else None
        if data_root:
            self.root = Path(data_root).expanduser().resolve()
        elif self.project_config:
            self.root = self.project_config.parent / ".data" / "evaluations"
        else:
            self.root = Path.cwd() / ".data" / "evaluations"
        self.suites_root = self.root.parent / "evaluation-suites"
        # Large repos make judge sessions slower than answer sessions; the
        # defaults stay backwards compatible, deployments can raise both.
        self.timeout_seconds = float(
            os.environ.get("BUSINESS_CODE_EVALUATION_TIMEOUT_SECONDS", timeout_seconds))
        self.workers = int(os.environ.get("BUSINESS_CODE_EVALUATION_WORKERS", workers))
        self.command = command
        self.runtime_factory = runtime_factory or (lambda job=None: EvaluationRuntime(timeout_seconds=self.timeout_seconds))
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self.project_id, self.project_name = self._project_identity()
        self.root.mkdir(parents=True, exist_ok=True)
        self.suites_root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted()

    def _project_identity(self) -> tuple[str | None, str | None]:
        if not self.project_config or not self.project_config.is_file():
            return None, None
        try:
            config = json.loads(self.project_config.read_text(encoding="utf-8"))
            project = config.get("project") or {}
            project_id = str(project.get("id") or "").strip() or None
            project_name = str(project.get("name") or "").strip() or project_id
            return project_id, project_name
        except (OSError, ValueError):
            return None, None

    # ------------------------------------------------------------------ index

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _read_index(self) -> list:
        return _load_json(self._index_path(), [])

    def _write_index(self, items) -> None:
        _atomic_write_json(self._index_path(), items)

    def _index_update(self, eval_id, **fields) -> None:
        with self._lock:
            items = self._read_index()
            for item in items:
                if item.get("id") == eval_id:
                    item.update(fields)
                    break
            else:
                items.append({"id": eval_id, **fields})
            self._write_index(items)

    def _recover_interrupted(self) -> None:
        """A batch from a previous process can never resume its calls."""
        with self._lock:
            items = self._read_index()
            changed = False
            for item in items:
                if item.get("status") in ACTIVE_STATUSES:
                    state = self._load_state(item["id"])
                    if state.get("status") in ACTIVE_STATUSES:
                        state["status"] = "interrupted"
                        state["finishedAt"] = state.get("finishedAt") or _now()
                        state.setdefault("notes", []).append(
                            "服务重启打断了该批次；已完成结果保留，未自动追加任何模型调用。")
                        self._save_state(item["id"], state)
                        item["status"] = "interrupted"
                        item["finishedAt"] = state["finishedAt"]
                        changed = True
            if changed:
                self._write_index(items)

    # ------------------------------------------------------------------ state

    def _state_path(self, eval_id: str) -> Path:
        return self.root / eval_id / "state.json"

    def _load_state(self, eval_id: str) -> dict:
        if not re.fullmatch(r"eval-[a-zA-Z0-9_-]+", eval_id or ""):
            raise EvaluationError("评测任务编号不合法")
        state = _load_json(self._state_path(eval_id), None)
        if state is None:
            raise KeyError(eval_id)
        return state

    def _save_state(self, eval_id: str, state: dict) -> None:
        _atomic_write_json(self._state_path(eval_id), state)

    def _batch_dir(self, eval_id: str) -> Path:
        if not re.fullmatch(r"eval-[a-zA-Z0-9_-]+", eval_id or ""):
            raise EvaluationError("评测任务编号不合法")
        return self.root / eval_id

    # ----------------------------------------------------------------- suites

    def _suite_path(self, suite_id: str) -> Path:
        if not re.fullmatch(r"suite-[a-zA-Z0-9_-]+", suite_id or ""):
            raise EvaluationError("题库编号不合法")
        return self.suites_root / suite_id / "suite.json"

    def _read_suite(self, suite_id: str) -> dict:
        suite = _load_json(self._suite_path(suite_id), None)
        if suite is None:
            raise KeyError(suite_id)
        return suite

    def _validate_suite_binding(self, suite_data: dict, source_path: Path | None = None) -> str:
        """Suites are bound to one project config; a mismatched import is rejected."""
        raw = suite_data.get("projectConfig")
        if raw:
            candidate = Path(str(raw)).expanduser()
            resolved = candidate if candidate.is_absolute() else (source_path.parent / candidate).resolve() if source_path else candidate.resolve()
        elif self.project_config:
            resolved = self.project_config
        else:
            raise EvaluationError("当前服务未配置项目，无法绑定题库")
        if self.project_config and resolved != self.project_config:
            raise EvaluationError(f"题库绑定的项目配置是 {resolved.name}，与当前项目不一致")
        return str(resolved)

    def _normalize_suite(self, suite_id: str, data: dict, *, keep_meta: dict | None = None) -> dict:
        cases = data.get("cases") if isinstance(data.get("cases"), list) else []
        normalized_cases = []
        for case in cases:
            if not isinstance(case, dict):
                raise EvaluationError("cases 每一项必须是对象")
            normalized_cases.append({
                "id": str(case.get("id") or "").strip(),
                "question": str(case.get("question") or "").strip(),
                "checks": [str(check) for check in (case.get("checks") or [])],
                "category": str(case.get("category") or "uncategorized"),
                "notes": str(case.get("notes") or "") or None,
            })
        suite = {
            "id": suite_id,
            "name": str(data.get("name") or "").strip() or "未命名题库",
            "scope": str(data.get("scope") or "").strip(),
            "origin": data.get("origin") if data.get("origin") in SUITE_ORIGINS else "synthetic",
            "projectConfig": data.get("projectConfig"),
            "cases": normalized_cases,
            "revision": int(keep_meta.get("revision", 1)) if keep_meta else 1,
            "createdAt": (keep_meta or {}).get("createdAt", _now()),
            "updatedAt": _now(),
            "source": data.get("source") or (keep_meta or {}).get("source") or "manual",
        }
        return suite

    def list_suites(self) -> dict:
        items = []
        for directory in sorted(self.suites_root.iterdir()) if self.suites_root.is_dir() else []:
            if not directory.is_dir():
                continue
            suite = _load_json(directory / "suite.json", None)
            if suite:
                items.append({"id": suite["id"], "name": suite["name"], "scope": suite.get("scope", ""),
                              "origin": suite.get("origin"), "originLabel": SUITE_ORIGINS.get(suite.get("origin")),
                              "revision": suite.get("revision", 1), "caseCount": len(suite.get("cases") or []),
                              "cases": suite.get("cases") or [],
                              "updatedAt": suite.get("updatedAt"), "projectConfig": suite.get("projectConfig")})
        items.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
        last_used = _load_json(self.suites_root / "last-used.json", {}) or {}
        return {"items": items, "lastUsedSuiteId": last_used.get("suiteId")}

    def create_suite(self, payload: dict) -> dict:
        payload = payload or {}
        with self._lock:
            if payload.get("importPath"):
                return self._import_suite(str(payload["importPath"]))
            suite_id = f"suite-{_safe_slug(payload.get('name') or 'suite')}-{uuid.uuid4().hex[:6]}"
            suite = self._normalize_suite(suite_id, payload)
            suite["projectConfig"] = self._validate_suite_binding({"projectConfig": suite.get("projectConfig")})
            self._write_suite(suite)
            return suite

    def _import_suite(self, import_path: str) -> dict:
        path = Path(import_path).expanduser()
        if not path.is_absolute():
            base = self.project_config.parent if self.project_config else Path.cwd()
            path = (base / path).resolve()
        if not path.is_file():
            raise EvaluationError(f"找不到要导入的题库文件: {import_path}")
        data = _load_json(path, None)
        if data is None:
            raise EvaluationError("题库文件不是合法 JSON")
        suite_id = f"suite-{_safe_slug(data.get('name') or path.stem)}-{uuid.uuid4().hex[:6]}"
        suite = self._normalize_suite(suite_id, data)
        suite["projectConfig"] = self._validate_suite_binding(data, source_path=path)
        suite["source"] = f"import:{path.name}"
        validate_suite({**suite, "cases": suite["cases"]})
        self._write_suite(suite)
        return suite

    def update_suite(self, suite_id: str, payload: dict) -> dict:
        with self._lock:
            current = self._read_suite(suite_id)
            revisions = self.suites_root / suite_id / "revisions"
            revisions.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(revisions / f"{current.get('revision', 1)}.json", current)
            merged = {**current, **(payload or {})}
            merged.pop("revision", None)
            merged.pop("id", None)
            suite = self._normalize_suite(suite_id, merged, keep_meta=current)
            suite["revision"] = int(current.get("revision", 1)) + 1
            suite["projectConfig"] = current.get("projectConfig")
            if suite.get("cases"):
                validate_suite({"cases": suite["cases"]})
            self._write_suite(suite)
            return suite

    def _write_suite(self, suite: dict) -> None:
        directory = self.suites_root / suite["id"]
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(directory / "suite.json", suite)

    # ------------------------------------------------------------------ setup

    def _readiness(self) -> dict:
        repositories = []
        requirements = {"root": None, "readable": False}
        baseline = {"root": None, "readable": False, "documents": [], "hasOverview": False}
        try:
            from .runner import resolve_project_sources
            manager, repository_sources, baseline_root, requirements_root = resolve_project_sources(self.project_config)
            repositories = [{"id": rid, "path": str(path), "readable": path.is_dir()}
                            for rid, path in repository_sources]
            requirements = {"root": str(requirements_root), "readable": requirements_root.is_dir()}
            documents = []
            if baseline_root.is_dir():
                for path in sorted(baseline_root.rglob("*.md")):
                    documents.append({"name": str(path.relative_to(baseline_root)),
                                      "bytes": path.stat().st_size})
            baseline = {"root": str(baseline_root), "readable": baseline_root.is_dir(),
                        "documents": documents,
                        "hasOverview": any(doc["name"] == "project-overview.md" for doc in documents)}
        except Exception as exc:
            logger.warning("评测就绪检查失败: %s", exc)
            baseline["error"] = str(exc)
        cli_version = None
        available = bool(shutil.which(self.command))
        if available:
            try:
                cli_version = subprocess.check_output([self.command, "--version"], text=True).strip()
            except (OSError, subprocess.SubprocessError):
                available = False
        return {"repositories": repositories, "requirements": requirements, "baseline": baseline,
                "cli": {"command": self.command, "available": available, "version": cli_version}}

    def setup(self) -> dict:
        suites = self.list_suites()
        readiness = self._readiness()
        baseline = readiness["baseline"]
        if not baseline.get("readable"):
            baseline["note"] = "业务基线目录不可读，无法提供知识主干资料。"
        elif not baseline["hasOverview"]:
            baseline["note"] = "未提供 project-overview.md，运行时不会自动注入项目总览，仅按需读取流程文档。"
        else:
            baseline["note"] = "运行时自动提供项目总览，业务流程文档按需读取。"
        active = self.active_evaluation()
        return {
            "project": {"id": self.project_id, "name": self.project_name,
                        "config": str(self.project_config) if self.project_config else None},
            "suites": suites["items"],
            "lastUsedSuiteId": suites["lastUsedSuiteId"],
            "baseline": baseline,
            "repositories": readiness["repositories"],
            "requirements": readiness["requirements"],
            "cli": readiness["cli"],
            "runtime": {"source": "本地 Claude Code 当前模型设置；实际响应模型名称记录在批次报告中",
                        "timeoutSeconds": self.timeout_seconds, "workers": self.workers,
                        "comparison": {"arms": list(ARMS_ABC), "descriptions": ARM_DESCRIPTIONS_ABC}},
            "activeEvaluationId": active,
        }

    def active_evaluation(self) -> str | None:
        with self._lock:
            for eval_id, handle in self._active.items():
                if handle["thread"].is_alive():
                    return eval_id
            for item in self._read_index():
                if item.get("status") in ACTIVE_STATUSES:
                    return item["id"]
        return None

    # ------------------------------------------------------------------ start

    def start(self, payload: dict) -> dict:
        payload = payload or {}
        with self._lock:
            # 重复点击直接返回已有任务，不做任何校验或新调用。
            active = self.active_evaluation()
            if active:
                return {"evaluationId": active, "duplicate": True, "status": "running"}
        suite_id = str(payload.get("suiteId") or "").strip()
        if not suite_id:
            raise EvaluationError("请先选择验证题库")
        repeats = int(payload.get("repeats") or 2)
        if not 1 <= repeats <= 5:
            raise EvaluationError("轮次必须在 1 到 5 之间")
        judge = bool(payload.get("judge", True))
        suite = self._read_suite(suite_id)
        cases = validate_suite({"cases": suite.get("cases") or []})
        case_ids = None
        if payload.get("caseIds"):
            case_ids = [str(item) for item in payload["caseIds"]]
            unknown = set(case_ids) - {case["id"] for case in cases}
            if unknown:
                raise EvaluationError(f"题目子集包含题库中不存在的 id: {', '.join(sorted(unknown))}")
            cases = [case for case in cases if case["id"] in set(case_ids)]
        with self._lock:
            if self.active_evaluation():
                return {"evaluationId": self.active_evaluation(), "duplicate": True, "status": "running"}
            readiness = self._readiness()
            if not readiness["cli"]["available"]:
                raise EvaluationError(f"没有找到本地模型命令 {self.command}，请先安装并登录 Claude Code。")
            if not readiness["repositories"] or any(not repo["readable"] for repo in readiness["repositories"]):
                raise EvaluationError("项目源码仓库不可读，请检查项目配置与仓库同步。")
            if not readiness["baseline"]["readable"]:
                raise EvaluationError("业务基线目录不可读，知识主干资料缺失，无法开始对照。")
            if not readiness["baseline"]["documents"]:
                raise EvaluationError("业务基线目录没有任何 Markdown 文档，有主干组将没有可对照的知识。")
            stamp = datetime.now().strftime("eval-%Y%m%d-%H%M%S")
            eval_id = stamp
            number = 1
            while (self.root / eval_id).exists():
                number += 1
                eval_id = f"{stamp}-{number}"
            batch_dir = self.root / eval_id
            try:
                frozen = freeze_batch(
                    batch_dir, suite=suite, cases=cases, project_config=self.project_config,
                    arms=list(ARMS_ABC), arm_descriptions=dict(ARM_DESCRIPTIONS_ABC),
                    comparison="abc", repeats=repeats, case_ids=None, judge=judge,
                    workers=self.workers, timeout_seconds=self.timeout_seconds,
                    suite_ref={"id": suite["id"], "revision": suite.get("revision", 1),
                               "name": suite["name"], "origin": suite.get("origin"), "scope": suite.get("scope", "")})
            except (ValueError, OSError) as exc:
                shutil.rmtree(batch_dir, ignore_errors=True)
                raise EvaluationError(str(exc)) from exc
            jobs = []
            for job in frozen["protocol"]["jobs"]:
                jobs.append({"answerId": f"{job['case']}-r{job['repeat']}-{job['arm']}",
                             "caseId": job["case"], "repeat": job["repeat"], "arm": job["arm"],
                             "status": "pending", "elapsedSeconds": None, "toolCalls": 0,
                             "toolErrors": 0, "reviewStatus": "none", "modelStatus": "none"})
            state = {
                "id": eval_id, "status": "running", "phase": "answering",
                "createdAt": _now(), "startedAt": _now(), "finishedAt": None, "error": None,
                "cancelRequested": False,
                "suite": frozen["protocol"].get("suiteRef") or {"id": suite_id, "name": suite["name"],
                                                               "revision": suite.get("revision", 1)},
                "settings": {"repeats": repeats, "judge": judge, "caseIds": case_ids,
                             "questions": len(cases), "workers": self.workers,
                             "timeoutSeconds": self.timeout_seconds, "comparison": "ab",
                             "sourceRepoCount": len(frozen["protocol"].get("sourceRepositories", [])),
                             "baselineDocCount": len(frozen.get("baselineDocuments", {}))},
                "arms": dict(ARM_DESCRIPTIONS_ABC),
                "cases": [{"id": case["id"], "question": case["question"],
                           "category": case.get("category") or "未分类",
                           "checkCount": len(case["checks"])} for case in frozen["protocol"]["cases"]],
                "jobs": jobs,
                "progress": self._progress(jobs, judge),
                "notes": ["评分与答题分别记录；失败答题不评分，评分失败不记零分。",
                          "结构化知识图谱不参与本轮对照。"],
            }
            self._save_state(eval_id, state)
            self._index_update(eval_id, id=eval_id, status="running", createdAt=state["createdAt"],
                               suite=state["suite"], settings=state["settings"],
                               questions=len(cases), repeats=repeats, judge=judge)
            _atomic_write_json(self.suites_root / "last-used.json",
                               {"suiteId": suite_id, "revision": suite.get("revision", 1), "at": _now()})
            thread = threading.Thread(target=self._run_batch, args=(eval_id,), daemon=True,
                                      name=f"evaluation-{eval_id}")
            self._active[eval_id] = {"thread": thread, "cancel": threading.Event(), "mode": "batch"}
            thread.start()
            return {"evaluationId": eval_id, "duplicate": False, "status": "running"}

    @staticmethod
    def _progress(jobs, judge) -> dict:
        answers = {
            "total": len(jobs),
            "completed": sum(1 for job in jobs if job["status"] == "completed"),
            "success": sum(1 for job in jobs if job["status"] == "completed"),
            "failed": sum(1 for job in jobs if job["status"] == "failed"),
            "cancelled": sum(1 for job in jobs if job["status"] in ("cancelled", "skipped")),
            "running": sum(1 for job in jobs if job["status"] == "running"),
        }
        judge_progress = None
        if judge:
            reviewable = [job for job in jobs if job["status"] == "completed"]
            judge_progress = {
                "total": len(jobs),
                "completed": sum(1 for job in reviewable if job["modelStatus"] == "completed"),
                "usable": sum(1 for job in reviewable if job["modelStatus"] == "completed"),
                "failed": sum(1 for job in reviewable if job["modelStatus"] == "failed"),
            }
        return {"answers": answers, "judge": judge_progress}

    @staticmethod
    def _phase(jobs, judge) -> str:
        if any(job["status"] in ("pending", "running") for job in jobs):
            return "answering"
        if judge and any(job["status"] == "completed" and job["modelStatus"] in ("none", "pending")
                         for job in jobs):
            return "judging"
        return "reporting"

    def _run_batch(self, eval_id: str) -> None:
        state = self._load_state(eval_id)
        judge = bool(state["settings"].get("judge"))

        def on_start(job):
            with self._lock:
                state = self._load_state(eval_id)
                if state["status"] not in ACTIVE_STATUSES:
                    return  # 已被打断/停止的批次不再改写状态。
                for record in state["jobs"]:
                    if record["answerId"] == f"{job['case']}-r{job['repeat']}-{job['arm']}":
                        record["status"] = "running"
                        record["startedAt"] = _now()
                state["progress"] = self._progress(state["jobs"], judge)
                state["phase"] = self._phase(state["jobs"], judge)
                self._save_state(eval_id, state)

        def on_finish(value):
            with self._lock:
                state = self._load_state(eval_id)
                if state["status"] not in ACTIVE_STATUSES:
                    return  # 已被打断/停止的批次不再改写状态。
                self._apply_result(state, value)
                state["progress"] = self._progress(state["jobs"], judge)
                state["phase"] = self._phase(state["jobs"], judge)
                self._save_state(eval_id, state)

        cancel_event = self._active.get(eval_id, {}).get("cancel")
        try:
            run_batch(self._batch_dir(eval_id), workers=self.workers, judge=judge,
                      runtime_factory=self.runtime_factory,
                      cancel_check=cancel_event.is_set if cancel_event else None,
                      on_start=on_start, on_finish=on_finish)
            with self._lock:
                state = self._load_state(eval_id)
                if state["status"] in ACTIVE_STATUSES:
                    state["status"] = "cancelled" if (cancel_event and cancel_event.is_set()) else "completed"
                    state["phase"] = "reporting"
                    self._save_state(eval_id, state)
                generate_report(self._batch_dir(eval_id))
                state = self._load_state(eval_id)
                state["phase"] = "done" if state["status"] in FINISHED_STATUSES else state.get("phase")
                state["finishedAt"] = state.get("finishedAt") or _now()
                self._save_state(eval_id, state)
                self._index_update(eval_id, status=state["status"], finishedAt=state["finishedAt"],
                                   progress=state["progress"])
        except Exception as exc:
            logger.exception("评测批次失败: %s", eval_id)
            with self._lock:
                state = self._load_state(eval_id)
                state["status"] = "failed"
                state["error"] = str(exc)
                state["finishedAt"] = _now()
                self._save_state(eval_id, state)
                self._index_update(eval_id, status="failed", finishedAt=state["finishedAt"])
        finally:
            with self._lock:
                self._active.pop(eval_id, None)

    def _apply_result(self, state: dict, value: dict) -> None:
        for record in state["jobs"]:
            if record["answerId"] != value.get("id"):
                continue
            status = value.get("status") or "failed"
            record["status"] = status
            record["finishedAt"] = _now()
            record["elapsedSeconds"] = value.get("elapsedSeconds")
            record["toolCalls"] = value.get("toolCalls", 0)
            record["toolErrors"] = value.get("toolErrors", 0)
            if value.get("error"):
                record["error"] = str(value["error"])[:500]
            review = value.get("review") or {}
            if record["status"] == "completed" and state["settings"].get("judge"):
                review_status = review.get("status")
                # 已停止的初评既不是可用也不是失败；人工复核状态不在这里改写。
                record["modelStatus"] = ("completed" if review_status == "completed"
                                         else "none" if review_status == "cancelled" else "failed")
            return

    # ------------------------------------------------------------------- read

    def list_evaluations(self) -> dict:
        items = []
        for entry in self._read_index():
            item = dict(entry)
            state = _load_json(self._state_path(entry["id"]), None)
            if state:
                item["status"] = state["status"]
                item["phase"] = state.get("phase")
                item["progress"] = state.get("progress")
                item["finishedAt"] = state.get("finishedAt")
            if item.get("status") in FINISHED_STATUSES:
                item["summaries"] = self._summaries(entry["id"], brief=True)
            items.append(item)
        items.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return {"items": items, "activeEvaluationId": self.active_evaluation()}

    def _results(self, eval_id: str) -> list:
        return _load_json(self._batch_dir(eval_id) / "results.json", []) or []

    def _reviews(self, eval_id: str) -> dict:
        return _load_json(self._batch_dir(eval_id) / "reviews.json", {}) or {}

    def _cases_by_id(self, protocol: dict) -> dict:
        return {case["id"]: case for case in protocol.get("cases", [])}

    def _summaries(self, eval_id: str, *, brief=False) -> dict | None:
        batch_dir = self._batch_dir(eval_id)
        results_path = batch_dir / "results.json"
        protocol_path = batch_dir / "protocol.json"
        if not results_path.is_file() or not protocol_path.is_file():
            return None
        protocol = load_protocol(batch_dir)
        results = self._results(eval_id)
        cases = self._cases_by_id(protocol)
        arms = list(protocol.get("arms") or ARMS_ABC)
        reviews = self._reviews(eval_id)
        summaries = {
            "model": summarize_pairs(results, cases, arms, source="model"),
            "review": summarize_pairs(results, cases, arms, source="review", reviews=reviews),
        }
        if brief:
            for source, value in summaries.items():
                aggregates = value["aggregates"]
                summaries[source] = {
                    "qualityBlocks": value["qualityBlocks"], "costBlocks": value["costBlocks"],
                    "score": {arm: (f"{agg['score']}/{agg['possible']}" if agg["score"] is not None else None)
                              for arm, agg in aggregates.items()},
                    "meanSeconds": {arm: agg["meanSeconds"] for arm, agg in aggregates.items()},
                }
        return summaries

    def get_evaluation(self, eval_id: str) -> dict:
        state = self._load_state(eval_id)
        with self._lock:
            handle = self._active.get(eval_id)
        payload = {
            "id": eval_id,
            "status": state["status"],
            "phase": state.get("phase"),
            "createdAt": state.get("createdAt"),
            "startedAt": state.get("startedAt"),
            "finishedAt": state.get("finishedAt"),
            "error": state.get("error"),
            "cancelRequested": bool(state.get("cancelRequested")) or bool(handle and handle["cancel"].is_set()),
            "suite": state.get("suite"),
            "settings": state.get("settings"),
            "arms": state.get("arms"),
            "cases": state.get("cases"),
            "progress": state.get("progress"),
            "notes": state.get("notes") or [],
            "rejudge": state.get("rejudge"),
            "elapsedSeconds": self._elapsed(state),
        }
        ordered_rows = []
        index = {}
        reviews = self._reviews(eval_id)
        for case in state.get("cases", []):
            for repeat in range(1, int(state["settings"].get("repeats", 1)) + 1):
                row = {"caseId": case["id"], "category": case["category"], "question": case["question"],
                       "repeat": repeat, "runs": {}}
                index[(case["id"], repeat)] = row
                ordered_rows.append(row)
        for job in state.get("jobs", []):
            row = index.get((job["caseId"], job["repeat"]))
            if row is None:
                row = {"caseId": job["caseId"], "category": "未分类", "question": "", "repeat": job["repeat"],
                       "runs": {}}
                index[(job["caseId"], job["repeat"])] = row
                ordered_rows.append(row)
            # 复核状态以 reviews.json 为准：重评或旧数据残留都不会误导覆盖数。
            revisions = (reviews.get(job["answerId"]) or {}).get("revisions") or []
            if revisions:
                latest_checks = revisions[-1].get("checks") or []
                review_status = "reviewed" if all(value in (0, 1) for value in latest_checks) else "partial"
            else:
                review_status = "none"
            row["runs"][job["arm"]] = {
                "answerId": job["answerId"], "status": job["status"],
                "startedAt": job.get("startedAt"), "finishedAt": job.get("finishedAt"),
                "elapsedSeconds": job.get("elapsedSeconds"), "toolCalls": job.get("toolCalls", 0),
                "toolErrors": job.get("toolErrors", 0), "modelStatus": job.get("modelStatus", "none"),
                "reviewStatus": review_status, "error": job.get("error"),
            }
        for row in ordered_rows:
            row["scoreDelta"] = None
        payload["rows"] = ordered_rows
        if state["status"] in FINISHED_STATUSES:
            payload["summaries"] = self._summaries(eval_id)
            payload["reviewCoverage"] = self._review_coverage(ordered_rows)
        return payload

    @staticmethod
    def _review_coverage(rows: list) -> dict:
        completed_runs = [run for row in rows for run in row["runs"].values()
                          if run.get("status") == "completed"]
        return {"answers": len(completed_runs),
                "reviewed": sum(1 for run in completed_runs if run.get("reviewStatus") == "reviewed"),
                "modelUsable": sum(1 for run in completed_runs if run.get("modelStatus") == "completed")}

    @staticmethod
    def _elapsed(state: dict) -> float | None:
        started = state.get("startedAt")
        if not started:
            return None
        try:
            start = datetime.fromisoformat(started)
        except ValueError:
            return None
        end = state.get("finishedAt")
        end_time = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        return round((end_time - start).total_seconds(), 1)

    # ------------------------------------------------------------------ cancel

    def cancel(self, eval_id: str) -> dict:
        state = self._load_state(eval_id)
        with self._lock:
            handle = self._active.get(eval_id)
            if state["status"] == "running":
                state["status"] = "cancelling"
                state["cancelRequested"] = True
                self._save_state(eval_id, state)
                self._index_update(eval_id, status="cancelling")
            if handle:
                handle["cancel"].set()
                if handle.get("mode") == "rejudge":
                    state = self._load_state(eval_id)
                    state.setdefault("rejudge", {})["status"] = "cancelling"
                    self._save_state(eval_id, state)
            elif state["status"] in ACTIVE_STATUSES:
                state["status"] = "interrupted"
                state["finishedAt"] = _now()
                self._save_state(eval_id, state)
                self._index_update(eval_id, status="interrupted", finishedAt=state["finishedAt"])
        return {"evaluationId": eval_id, "status": state["status"]}

    # ------------------------------------------------------------------ detail

    def get_case(self, eval_id: str, case_id: str, repeat: int) -> dict:
        protocol = load_protocol(self._batch_dir(eval_id))
        cases = self._cases_by_id(protocol)
        if case_id not in cases:
            raise KeyError(case_id)
        case = cases[case_id]
        reviews = self._reviews(eval_id)
        runs = {}
        for arm in protocol.get("arms") or ARMS_ABC:
            answer_id = f"{case_id}-r{repeat}-{arm}"
            result = _load_json(self._batch_dir(eval_id) / "runs" / answer_id / "result.json", None)
            if result is None:
                runs[arm] = {"answerId": answer_id, "status": "pending"}
                continue
            runs[arm] = {
                "answerId": answer_id,
                "status": result.get("status"),
                "answer": result.get("answer") or "",
                "error": result.get("error"),
                "elapsedSeconds": result.get("elapsedSeconds"),
                "toolCalls": result.get("toolCalls", 0),
                "toolErrors": result.get("toolErrors", 0),
                "baselineReadCalls": result.get("baselineReadCalls", 0),
                "baselineContentCalls": result.get("baselineContentCalls", 0),
                "usage": result.get("usage") or {},
                "metadata": result.get("metadata") or {},
                "modelReview": result.get("review"),
                "reviewHistory": self._review_history(eval_id, answer_id),
                "reviews": (reviews.get(answer_id) or {}).get("revisions") or [],
                "trace": self._trim_trace(result.get("toolTrace") or []),
            }
        return {"case": {"id": case_id, "question": case["question"], "checks": case["checks"],
                         "category": case.get("category")},
                "repeat": repeat,
                "runs": runs,
                "sources": [{"id": item["snapshot"], "repositoryId": item["id"], "snapshot": item["snapshot"]}
                            for item in protocol.get("sourceRepositories", [])]}

    def _review_history(self, eval_id: str, answer_id: str) -> list:
        """Archived model review revisions created by rejudge runs."""
        history_dir = self._batch_dir(eval_id) / "runs" / answer_id / "review-history"
        entries = []
        if not history_dir.is_dir():
            return entries
        for directory in sorted(history_dir.iterdir(), key=lambda path: path.name):
            review = _load_json(directory / "review.json", None)
            if review is None:
                continue
            checks = review.get("checks") or []
            entries.append({"version": directory.name, "status": review.get("status"),
                            "source": "rejudge", "score": sum(1 for check in checks if check.get("met"))
                            if review.get("status") == "completed" else None,
                            "total": len(checks) if review.get("status") == "completed" else None})
        return entries

    def _trim_trace(self, trace: list) -> list:
        trimmed = []
        for entry in trace:
            input_value = entry.get("input")
            text = json.dumps(input_value, ensure_ascii=False) if input_value is not None else ""
            trimmed.append({
                "id": entry.get("id"), "name": entry.get("name"), "status": entry.get("status"),
                "input": text[:400], "error": (entry.get("error") or "")[:400] if entry.get("error") else None,
                "firstEventSeconds": entry.get("firstEventSeconds"),
                "lastEventSeconds": entry.get("lastEventSeconds"),
            })
        return trimmed

    # ----------------------------------------------------------------- reviews

    def save_review(self, eval_id: str, answer_id: str, payload: dict) -> dict:
        payload = payload or {}
        state = self._load_state(eval_id)
        job = next((item for item in state.get("jobs", []) if item["answerId"] == answer_id), None)
        protocol = load_protocol(self._batch_dir(eval_id))
        cases = self._cases_by_id(protocol)
        if job is None or job["caseId"] not in cases:
            raise KeyError(answer_id)
        case = cases[job["caseId"]]
        checks = payload.get("checks")
        if not isinstance(checks, list) or len(checks) != len(case["checks"]) \
                or any(value not in (0, 1, None) for value in checks):
            raise EvaluationError("复核必须为每个检查项给出 满足(1)/不满足(0)/待核查(null)")
        issues = payload.get("issues") or []
        if not isinstance(issues, list) or any(not isinstance(item, str) for item in issues):
            raise EvaluationError("issues 必须是字符串列表")
        operator_type = payload.get("operatorType") or "user"
        if operator_type not in ("user", "import"):
            raise EvaluationError("operatorType 只能是 user 或 import")
        operator = str(payload.get("operator") or ("当前用户" if operator_type == "user" else "")).strip()
        if operator_type == "import" and not operator:
            raise EvaluationError("导入的执行助手复核必须注明操作者名称，不能标成用户亲自确认")
        revision = {
            "version": 1, "operatorType": operator_type, "operator": operator or None,
            "createdAt": _now(), "checks": checks, "issues": issues,
            "note": str(payload.get("note") or ""),
        }
        with self._lock:
            reviews = self._reviews(eval_id)
            entry = reviews.setdefault(answer_id, {"revisions": []})
            revision["version"] = len(entry["revisions"]) + 1
            entry["revisions"].append(revision)
            _atomic_write_json(self._batch_dir(eval_id) / "reviews.json", reviews)
            state = self._load_state(eval_id)
            for item in state.get("jobs", []):
                if item["answerId"] == answer_id:
                    item["reviewStatus"] = "reviewed" if all(value in (0, 1) for value in checks) else "partial"
            self._save_state(eval_id, state)
        return {"answerId": answer_id, "review": revision,
                "reviewStatus": "reviewed" if all(value in (0, 1) for value in checks) else "partial"}

    # ----------------------------------------------------------------- rejudge

    def rejudge(self, eval_id: str) -> dict:
        state = self._load_state(eval_id)
        if state["status"] in ACTIVE_STATUSES:
            raise EvaluationError("当前批次仍在运行，不能重新初评")
        results = self._results(eval_id)
        if not any(item.get("status") == "completed" for item in results):
            raise EvaluationError("没有已完成的答案可以重新初评")
        with self._lock:
            active = self.active_evaluation()
            if active:
                raise EvaluationError("已有评测任务在运行，请等待完成或先停止。")
            if state.get("rejudge", {}).get("status") == "running":
                return {"evaluationId": eval_id, "rejudge": state["rejudge"], "duplicate": True}
            state["rejudge"] = {"status": "running", "completed": 0, "total": sum(
                1 for item in results if item.get("status") == "completed")}
            self._save_state(eval_id, state)
            thread = threading.Thread(target=self._run_rejudge, args=(eval_id,), daemon=True,
                                      name=f"rejudge-{eval_id}")
            self._active[eval_id] = {"thread": thread, "cancel": threading.Event(), "mode": "rejudge"}
            thread.start()
            return {"evaluationId": eval_id, "rejudge": state["rejudge"], "duplicate": False}

    def _run_rejudge(self, eval_id: str) -> None:
        handle = self._active.get(eval_id)
        cancel_event = handle["cancel"] if handle else threading.Event()

        def on_finish(value):
            with self._lock:
                state = self._load_state(eval_id)
                self._apply_result(state, value)
                state.setdefault("rejudge", {})["completed"] = state["rejudge"].get("completed", 0) + 1
                state["progress"] = self._progress(state["jobs"], state["settings"].get("judge"))
                self._save_state(eval_id, state)

        try:
            rejudge_output(self._batch_dir(eval_id), workers=self.workers,
                           runtime_factory=self.runtime_factory, cancel_check=cancel_event.is_set,
                           on_finish=on_finish)
            with self._lock:
                state = self._load_state(eval_id)
                state["rejudge"]["status"] = "cancelled" if cancel_event.is_set() else "completed"
                state["rejudge"]["finishedAt"] = _now()
                self._save_state(eval_id, state)
        except Exception as exc:
            logger.exception("重新初评失败: %s", eval_id)
            with self._lock:
                state = self._load_state(eval_id)
                state["rejudge"] = {"status": "failed", "error": str(exc)}
                self._save_state(eval_id, state)
        finally:
            with self._lock:
                self._active.pop(eval_id, None)

    # ----------------------------------------------------------------- sources

    def read_source(self, eval_id: str, source_id: str, path: str, start: int = 1, end: int | None = None) -> dict:
        protocol = load_protocol(self._batch_dir(eval_id))
        snapshots = {item["snapshot"]: item["id"] for item in protocol.get("sourceRepositories", [])}
        if source_id == "requirements":
            base = self._batch_dir(eval_id) / "inputs" / "requirements"
            repository_id = "requirements"
        elif source_id in snapshots:
            base = self._batch_dir(eval_id) / "inputs" / source_id
            repository_id = snapshots[source_id]
        else:
            raise KeyError(source_id)
        if not base.is_dir():
            raise KeyError(source_id)
        relative = str(path or "").strip()
        prefix = f"{source_id}/"  # 评审引文常带快照前缀，如 repo-0/src/...
        if relative.startswith(prefix):
            relative = relative[len(prefix):]
        target = (base / relative).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            raise EvaluationError("源码路径越出本轮快照范围")
        if not target.is_file():
            raise KeyError(relative)
        text = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start or 1))
        end = min(len(text), int(end or start + 399), start + 399)
        return {"repositoryId": repository_id, "snapshot": source_id, "path": relative,
                "totalLines": len(text), "start": start, "end": end,
                "lines": [{"n": number, "text": text[number - 1]}
                          for number in range(start, min(end, len(text)) + 1)]}

    # ------------------------------------------------------------------ report

    def export_report(self, eval_id: str, source: str = "review") -> dict:
        if source not in ("model", "review"):
            raise EvaluationError("评分来源只能是 model 或 review")
        protocol = load_protocol(self._batch_dir(eval_id))
        results = self._results(eval_id)
        cases = self._cases_by_id(protocol)
        state = self._load_state(eval_id)
        notes = list(state.get("notes") or [])
        markdown, summary = build_view_report(protocol=protocol, results=results, cases=cases,
                                              source=source, reviews=self._reviews(eval_id), notes=notes)
        return {"evaluationId": eval_id, "source": source, "markdown": markdown, "summary": summary}

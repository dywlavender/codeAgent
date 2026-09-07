"""Batch orchestration shared by the evaluation CLI and the workbench service.

One batch freezes sources, rubric and settings before the first model call,
then runs paired answer jobs (optionally with per-answer model judging).
Cancellation stops scheduling new jobs, aborts the calls this batch owns and
keeps every completed result.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from ..query_agent.workspace import WorkspaceManager
from .ast_docs import generate_ast_documents
from .harness import (EvaluationRuntime, judge_answer, judge_workspace_for, metadata_capture,
                      run_answer_job, write_json)
from .scoring import human_review_score, model_review_score

ARMS_AB = ("code_only", "optional")
ARM_DESCRIPTIONS_AB = {
    "code_only": "原始代码、README、需求",
    "optional": "同一资料 + 当前生产接入方式的知识主干",
}
AST_ARM_DESCRIPTION = "同一资料 + AST 代码解析结构地图（tree-sitter 机械生成，无业务解释）"
ARMS_ABC = ("code_only", "optional", "ast")
ARM_DESCRIPTIONS_ABC = {**ARM_DESCRIPTIONS_AB, "ast": AST_ARM_DESCRIPTION}
COMPARISON_NOTES = {
    "ab": "结构化图谱不参与该对照；结果只反映文档主干的整体接入效果。",
    "abc": "三臂对照：文档主干与 AST 代码解析地图分别与无主干组对比；结构化图谱不参与。",
}


def validate_suite(suite):
    """Return the validated case list or raise ``ValueError`` with a readable reason."""
    if not isinstance(suite, dict):
        raise ValueError("题库必须是 JSON 对象")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("题库必须包含至少一个问题")
    ids = [str(case.get("id") or "") for case in cases]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", case_id) for case_id in ids):
        raise ValueError("题目 id 只能包含字母、数字、下划线和连字符")
    if len(ids) != len(set(ids)):
        raise ValueError("题目 id 必须唯一")
    for case in cases:
        if not str(case.get("question") or "").strip():
            raise ValueError(f"题目 {case.get('id')} 缺少 question")
        checks = case.get("checks")
        if not isinstance(checks, list) or not checks or any(not str(check or "").strip() for check in checks):
            raise ValueError(f"题目 {case.get('id')} 需要非空 checks 判分标准")
    return cases


def resolve_project_sources(project_config):
    """Resolve repositories, baseline and requirement roots from a project config."""
    manager = WorkspaceManager(project_config=project_config)
    repository_sources = manager._repository_sources()
    if not repository_sources or any(not path.is_dir() for _, path in repository_sources):
        raise ValueError("请先准备项目配置中的本地代码仓库")
    baseline_root = manager._configured_path(
        (manager.config.get("knowledge") or {}).get("baselineRoot") or "knowledge/baseline")
    requirements_config = manager.config.get("requirements")
    requirements_root = manager._configured_path(
        manager.config.get("requirementsRoot") or manager.config.get("requirementRoot")
        or (requirements_config.get("root") if isinstance(requirements_config, dict) else None)
        or "requirements")
    return manager, repository_sources, baseline_root, requirements_root


def baseline_documents(baseline_root):
    if not baseline_root.is_dir():
        return {}
    return {str(path.relative_to(baseline_root)): path.read_text(encoding="utf-8")
            for path in sorted(baseline_root.rglob("*.md"))}


def _arm_documents(arm, documents, inputs):
    """Per-arm reference documents: none, the human baseline, or the AST map."""
    if arm == "code_only":
        return {}
    if arm == "ast":
        return generate_ast_documents(inputs)
    return documents


def plan_jobs(cases, *, arms, repeats, case_ids=None, start_repeat=1, blocks=None, limit_jobs=None):
    jobs = []
    for repeat in range(start_repeat, start_repeat + repeats):
        for number, case in enumerate(cases):
            if case_ids and case["id"] not in case_ids:
                continue
            if blocks and f"{case['id']}:{repeat}" not in blocks:
                continue
            ordered = list(arms)
            random.Random(20260905 + repeat * 10 + number).shuffle(ordered)
            jobs.extend({"repeat": repeat, "arm": arm, "case": case["id"]} for arm in ordered)
    if limit_jobs:
        if limit_jobs < len(arms) or limit_jobs % len(arms):
            raise ValueError("limit_jobs 必须保留完整配对，为组数的正整数倍")
        jobs = jobs[:limit_jobs]
    if not jobs:
        raise ValueError("没有匹配的评测任务")
    return jobs


def freeze_batch(output, *, suite, cases, project_config, arms=ARMS_ABC, arm_descriptions=None,
                 variants=None, comparison="abc", repeats=2, case_ids=None, judge=True, workers=2,
                 start_repeat=1, blocks=None, limit_jobs=None, suite_ref=None, timeout_seconds=240):
    """Create the output directory, freeze sources/rubric and write ``protocol.json``."""
    arm_descriptions = arm_descriptions or {arm: ARM_DESCRIPTIONS_ABC.get(arm, arm) for arm in arms}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manager, repository_sources, baseline_root, requirements_root = resolve_project_sources(project_config)
    documents = baseline_documents(baseline_root)
    inputs = output / "inputs"
    repository_names = []
    for number, (repository_id, source) in enumerate(repository_sources):
        name = f"repo-{number}"
        repository_names.append((repository_id, name))
        shutil.copytree(source, inputs / name,
                        ignore=shutil.ignore_patterns(".git", "node_modules", "target", ".venv", "__pycache__"))
    if requirements_root.is_dir():
        shutil.copytree(requirements_root, inputs / "requirements")
    variants = variants if variants is not None else {
        arm: _arm_documents(arm, documents, inputs) for arm in arms}
    for arm, files in variants.items():
        for name, content in files.items():
            target = inputs / "baselines" / arm / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    planned = plan_jobs(cases, arms=arms, repeats=repeats, case_ids=case_ids, start_repeat=start_repeat,
                        blocks=blocks, limit_jobs=limit_jobs)
    try:
        cli_version = subprocess.check_output(["claude", "--version"], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        cli_version = None
    write_json(output / "protocol.json", {
        "suite": suite,
        "suiteRef": suite_ref,
        "sourceRepositories": [{"id": rid, "snapshot": name} for rid, name in repository_names],
        "judgeEnabled": judge,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "jobs": planned,
        "comparison": comparison,
        "arms": arm_descriptions,
        "workers": workers,
        "timeoutSeconds": timeout_seconds,
        "perRunCliBudgetUsd": 1,
        "questionSuffix": True,
        "cliVersion": cli_version,
        "notes": COMPARISON_NOTES.get(comparison, ""),
        "review": "独立会话模型初评（若启用），同一网关模型，不是独立人工盲评；未评分不能判定效果。",
    })
    return {"protocol": json.loads((output / "protocol.json").read_text(encoding="utf-8")),
            "repositoryNames": repository_names, "baselineDocuments": documents}


def load_protocol(output):
    return json.loads((Path(output) / "protocol.json").read_text(encoding="utf-8"))


def _default_runtime_factory(protocol):
    timeout = float(protocol.get("timeoutSeconds") or 240)
    return lambda job=None: EvaluationRuntime(timeout_seconds=timeout)


def run_batch(output, *, workers=None, judge=None, runtime_factory=None, cancel_check=None,
              on_start=None, on_finish=None):
    """Execute every planned job; keep completed results on failure or cancel."""
    output = Path(output)
    protocol = load_protocol(output)
    cases = {case["id"]: case for case in protocol["cases"]}
    inputs = output / "inputs"
    repository_names = [(item["id"], item["snapshot"]) for item in protocol["sourceRepositories"]]
    judge_enabled = protocol.get("judgeEnabled", False) if judge is None else judge
    worker_count = int(protocol.get("workers") or 2) if workers is None else workers
    runtime_factory = runtime_factory or _default_runtime_factory(protocol)
    cancelled = (lambda: bool(cancel_check and cancel_check())) if cancel_check else (lambda: False)

    def payload(job):
        return {"runId": f"{job['case']}-r{job['repeat']}-{job['arm']}",
                "case": cases[job["case"]], "arm": job["arm"], "repeat": job["repeat"]}

    results = []
    finished_ids = set()
    pending = list(protocol["jobs"])
    with metadata_capture(), ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {}
        while pending or futures:
            while pending and len(futures) < worker_count and not cancelled():
                job = pending.pop(0)
                if on_start:
                    on_start(job)
                future = pool.submit(run_answer_job, payload(job), output=output, inputs=inputs,
                                     repository_names=repository_names, judge_enabled=judge_enabled,
                                     runtime_factory=runtime_factory, cancel_check=cancel_check)
                futures[future] = job
            if not futures:
                break
            done, _ = wait(list(futures), timeout=0.2, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                job = futures.pop(future)
                try:
                    value = future.result()
                except Exception as exc:  # A crashed job must not sink the batch.
                    value = {"id": payload(job)["runId"], "questionId": job["case"], "arm": job["arm"],
                             "repeat": job["repeat"], "status": "failed", "error": str(exc),
                             "toolCalls": 0, "toolErrors": 0, "toolTrace": []}
                results.append(value)
                finished_ids.add(value["id"])
                if on_finish:
                    on_finish(value)
    for job in pending:  # Never started after a cancel; not answer failures.
        value = {"id": payload(job)["runId"], "questionId": job["case"], "arm": job["arm"],
                 "repeat": job["repeat"], "status": "skipped", "error": "批次已停止，任务未开始",
                 "toolCalls": 0, "toolErrors": 0, "toolTrace": []}
        results.append(value)
        finished_ids.add(value["id"])
        if on_finish:
            on_finish(value)
    order = {f"{j['case']}-r{j['repeat']}-{j['arm']}": index for index, j in enumerate(protocol["jobs"])}
    results.sort(key=lambda value: order.get(value.get("id"), len(order)))
    write_json(output / "results.json", results)
    return results


def rejudge_output(output, *, workers=2, runtime_factory=None, cancel_check=None, on_finish=None):
    """Re-review frozen answers without new answer attempts; keep old reviews."""
    from .report import generate_report

    output = Path(output)
    history = output / "review-runs"
    history.mkdir(exist_ok=True)
    revision = history / str(len(list(history.iterdir())) + 1)
    revision.mkdir()
    write_json(revision / "protocol.json", {"createdAt": datetime.now(timezone.utc).isoformat(),
                "workers": workers, "mode": "rejudge", "source": "frozen inputs and answers"})
    protocol = load_protocol(output)
    results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in protocol["cases"]}
    inputs = output / "inputs"
    runtime_factory = runtime_factory or _default_runtime_factory(protocol)
    cancelled = (lambda: bool(cancel_check and cancel_check())) if cancel_check else (lambda: False)
    with metadata_capture(), tempfile.TemporaryDirectory(prefix="backbone-review-") as folder:
        base = Path(folder)
        repositories = []
        for repository in protocol["sourceRepositories"]:
            target = base / "code" / repository["snapshot"]
            shutil.copytree(inputs / repository["snapshot"], target)
            repositories.append({"id": repository["id"], "localPath": str(target)})
        if (inputs / "requirements").is_dir():
            shutil.copytree(inputs / "requirements", base / "requirements")
        workspace = judge_workspace_for(base, repositories, inputs)

        def run(value):
            if value.get("status") != "completed" or cancelled():
                return value
            run_dir = output / "runs" / value["id"]
            if value.get("review"):
                archive = run_dir / "review-history"
                archive.mkdir(exist_ok=True)
                version = archive / str(len(list(archive.iterdir())) + 1)
                version.mkdir()
                write_json(version / "review.json", value["review"])
                for path in run_dir.glob("judge-*"):
                    shutil.copy2(path, version / path.name)
            value["review"] = judge_answer(cases[value["questionId"]], value.get("answer", ""),
                                           workspace.path, runtime_factory(None), run_dir,
                                           cancel_check=cancel_check)
            write_json(run_dir / "result.json", value)
            if on_finish:
                on_finish(value)
            return value

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, results))
    write_json(output / "results.json", results)
    generate_report(output)
    return results


def latest_review(entry):
    """Latest review revision record, or ``None``."""
    if not entry:
        return None
    revisions = entry.get("revisions") or []
    return revisions[-1] if revisions else None


def summarize_pairs(results, cases, arms, *, source="model", reviews=None):
    """Paired aggregates for one explicit scoring source.

    ``model`` uses completed model reviews; ``review`` uses human/imported
    records only and never falls back to model scores. Cost counts blocks
    completed in every arm; quality counts blocks with a usable score from the
    chosen source in every arm, so the two denominators may differ.
    """
    indexed = {(r["questionId"], r["repeat"], r["arm"]): r for r in results}
    arm_list = list(arms)
    blocks = sorted({(r["questionId"], r["repeat"]) for r in results})
    complete = [b for b in blocks if all(indexed.get((*b, arm), {}).get("status") == "completed"
                                         for arm in arm_list)]

    def score(result):
        if source == "review":
            entry = latest_review((reviews or {}).get(result.get("id")))
            return human_review_score(entry, cases[result["questionId"]]) if entry else None
        return model_review_score(result, cases[result["questionId"]])

    eligible = [b for b in complete if all(score(indexed[(*b, arm)]) is not None for arm in arm_list)]
    aggregates = {}
    for arm in arm_list:
        runs = [indexed[(*b, arm)] for b in complete]
        scores = [score(indexed[(*b, arm)]) for b in eligible]
        aggregates[arm] = {
            "meanSeconds": round(mean(r["elapsedSeconds"] for r in runs), 2) if runs else None,
            "toolCalls": sum(r.get("toolCalls", 0) for r in runs),
            "toolErrors": sum(r.get("toolErrors", 0) for r in runs),
            "score": sum(s[0] for s in scores) if scores else None,
            "possible": sum(s[1] for s in scores) if scores else None,
            "issues": sum(s[2] for s in scores) if scores else None,
        }
    pairs = []
    for block in complete:
        control = indexed[(*block, "code_only")]
        control_score = score(control)
        for arm in arm_list:
            if arm == "code_only":
                continue
            treatment = indexed[(*block, arm)]
            treatment_score = score(treatment)
            pairs.append({
                "questionId": block[0], "repeat": block[1], "arm": arm,
                "category": cases[block[0]].get("category", "未分类"),
                "scored": bool(control_score and treatment_score),
                "scoreDelta": treatment_score[0] - control_score[0] if control_score and treatment_score else None,
                "issuesDelta": treatment_score[2] - control_score[2] if control_score and treatment_score else None,
                "controlScore": f"{control_score[0]}/{control_score[1]}" if control_score else None,
                "treatmentScore": f"{treatment_score[0]}/{treatment_score[1]}" if treatment_score else None,
                "toolDelta": treatment.get("toolCalls", 0) - control.get("toolCalls", 0),
                "secondsDelta": round(treatment["elapsedSeconds"] - control["elapsedSeconds"], 2),
            })
    return {
        "scoreSource": source,
        "plannedBlocks": len(blocks),
        "costBlocks": len(complete),
        "qualityBlocks": len(eligible),
        "aggregates": aggregates,
        "pairs": pairs,
    }

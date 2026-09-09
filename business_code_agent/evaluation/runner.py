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
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from ..query_agent.workspace import WorkspaceManager
from .ast_docs import generate_ast_documents  # compatibility export; web batches receive a manual AST version
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
ARMS_ABCDE = ("raw", "old_baseline", "code_map", "business_context", "code_map_context")
ARM_DESCRIPTIONS_ABCDE = {
    "raw": "A：源码、README、需求原文",
    "old_baseline": "B：A + 历史完整 baseline（仅历史对照）",
    "code_map": "C：A + 自动 Code Map（结构导航）",
    "business_context": "D：A + 分类后的 Business Context",
    "code_map_context": "E：A + Code Map + Business Context",
}
ARMS_DIAGNOSTIC = ("code_only", "overview", "optional")
ARM_DESCRIPTIONS_DIAGNOSTIC = {**ARM_DESCRIPTIONS_AB, "overview": "同一资料 + 仅项目总览（不提供流程主干）"}
COMPARISONS = {"ab": ARM_DESCRIPTIONS_AB, "abc": ARM_DESCRIPTIONS_ABC,
               "abcde": ARM_DESCRIPTIONS_ABCDE,
               "diagnostic": {arm: ARM_DESCRIPTIONS_DIAGNOSTIC[arm] for arm in ARMS_DIAGNOSTIC}}
COMPARISONS["diagnostic_ast"] = {**COMPARISONS["diagnostic"], "ast": AST_ARM_DESCRIPTION,
    "overview_ast": "同一资料 + 相同业务总览 + AST结构地图（不提供人工流程文档）"}
COMPARISON_NOTES = {
    "diagnostic_ast": "五组诊断：独立AST与无主干比较；总览+AST与仅总览比较，隔离结构地图的增量作用。",
    "diagnostic": "无主干、仅总览、完整主干；分别比较定位收益与流程文档的额外收益。",
    "ab": "结构化图谱不参与该对照；结果只反映文档主干的整体接入效果。",
    "abc": "三臂对照：文档主干与 AST 代码解析地图分别与无主干组对比；结构化图谱不参与。",
    "abcde": "五组资料对照：A 原始资料、B 历史完整主干、C 自动 Code Map、D Business Context、E 两者组合。五组使用相同冻结源码、需求、题库和评分要点。",
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
        if not isinstance(checks, list) or any(not str(check or "").strip() for check in checks):
            raise ValueError(f"题目 {case.get('id')} 的 checks 必须是字符串数组；没有评分要点时可留空")
    return cases


def resolve_project_sources(project_config, *, business_context_root=None, code_map_root=None,
                            baseline_root=None, requirements_root=None):
    """Resolve repositories and material roots for one frozen project input."""
    if business_context_root is not None and baseline_root is not None:
        raise ValueError("business_context_root 与 baseline_root 不能同时提供")
    manager = WorkspaceManager(
        project_config=project_config,
        business_context_root=business_context_root,
        code_map_root=code_map_root,
        baseline_root=baseline_root,
        requirements_root=requirements_root,
    )
    repository_sources = manager._repository_sources()
    if not repository_sources or any(not path.is_dir() for _, path in repository_sources):
        raise ValueError("请先准备项目配置中的本地代码仓库")
    resolved_baseline_root = manager.business_context_root
    requirements_config = manager.config.get("requirements")
    resolved_requirements_root = manager.requirements_root or manager._configured_path(
        manager.config.get("requirementsRoot") or manager.config.get("requirementRoot")
        or (requirements_config.get("root") if isinstance(requirements_config, dict) else None)
        or "requirements")
    return manager, repository_sources, resolved_baseline_root, resolved_requirements_root


def baseline_documents(baseline_root):
    if not baseline_root.is_dir():
        return {}
    return {str(path.relative_to(baseline_root)): path.read_text(encoding="utf-8")
            for path in sorted(baseline_root.rglob("*.md"))}


def _arm_documents(arm, documents, inputs, ast_documents=None):
    """Per-arm reference documents: none, the human baseline, or the AST map."""
    if arm in {"code_only", "raw"}:
        return {}
    if arm == "overview":
        if not documents.get("project-overview.md", "").strip():
            raise ValueError("仅总览对照需要非空 project-overview.md")
        return {"project-overview.md": documents["project-overview.md"] +
                "\n\n## 本轮资料范围\n\n本轮提供业务总览，未提供总览中链接的人工流程主干文件；"
                "这些链接仅保留项目认知背景，不是本轮可读取入口，请从授权源码确认流程。"
                "若本轮还提供机械结构资料，其入口会单独列在下方。\n"}
    if arm in ("ast", "overview_ast"):
        if ast_documents is None:
            raise ValueError("AST 实验必须引用已经手动生成的资料版本")
        generated = ast_documents
        if arm == "ast":
            return generated
        overview = _arm_documents("overview", documents, inputs)["project-overview.md"]
        return {**{key: value for key, value in generated.items() if key != "project-overview.md"},
                "ast-overview.md": generated["project-overview.md"],
                "project-overview.md": overview + "\n\n## 可选代码结构资料\n\n[AST结构导航](ast-overview.md)：仅含机械提取的代码结构，可按需读取，实际行为以源码为准。\n"}
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


def freeze_batch(output, *, suite, cases, project_config, project_id=None, arms=ARMS_ABC, arm_descriptions=None,
                 variants=None, comparison="abc", repeats=2, case_ids=None, judge=True, workers=2,
                 start_repeat=1, blocks=None, limit_jobs=None, suite_ref=None, timeout_seconds=240,
                 ast_version_id=None, ast_generation_seconds=None, business_context_root=None,
                 code_map_root=None, baseline_root=None, requirements_root=None):
    """Create the output directory, freeze sources/rubric and write ``protocol.json``."""
    arm_descriptions = arm_descriptions or {arm: ARM_DESCRIPTIONS_ABC.get(arm, arm) for arm in arms}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manager, repository_sources, resolved_baseline_root, resolved_requirements_root = resolve_project_sources(
        project_config, business_context_root=business_context_root, code_map_root=code_map_root,
        baseline_root=baseline_root, requirements_root=requirements_root)
    documents = baseline_documents(resolved_baseline_root)
    inputs = output / "inputs"
    repository_names = []
    for number, (repository_id, source) in enumerate(repository_sources):
        name = f"repo-{number}"
        repository_names.append((repository_id, name))
        shutil.copytree(source, inputs / name,
                        ignore=shutil.ignore_patterns(".git", "node_modules", "target", ".venv", "__pycache__"))
    if resolved_requirements_root.is_dir():
        shutil.copytree(resolved_requirements_root, inputs / "requirements")
    if variants is None and any(a in arms for a in ("ast", "overview_ast")):
        raise ValueError("AST 实验必须引用已经手动生成的资料版本，不能在实验启动时生成")
    variants = variants if variants is not None else {
        arm: _arm_documents(arm, documents, inputs, None) for arm in arms}
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
        "projectId": project_id,
        "suite": suite,
        "suiteRef": suite_ref,
        "sourceRepositories": [{"id": rid, "snapshot": name} for rid, name in repository_names],
        "referencePreparation": {"astGenerationSeconds": ast_generation_seconds,
            "astGenerationInBatch": False, "astVersionId": ast_version_id,
            "astGeneratorVersion": 2 if ast_version_id else None,
            "arms": {arm: {"documents": len(files), "characters": sum(len(v) for v in files.values()),
                            "overviewCharacters": len(files.get("project-overview.md", ""))} for arm, files in variants.items()}},
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

    def check_values(result):
        case = cases[result["questionId"]]
        if source == "review":
            entry = latest_review((reviews or {}).get(result.get("id")))
            values = (entry or {}).get("checks") if entry else None
            return list(values) if values is not None and all(value in (0, 1) for value in values) else None
        review = result.get("review") or {}
        if review.get("status") != "completed":
            return None
        checks = review.get("checks") or []
        return [1 if item.get("met") else 0 for item in checks] if len(checks) == len(case.get("checks") or []) else None

    def check_kind(label):
        text = str(label or "")
        if re.search(r"依据|引用|源码|文件|行号|证据", text):
            return "evidence"
        if re.search(r"覆盖|阶段|流程|链路|入口|职责|环节", text):
            return "coverage"
        return "core"

    eligible = [b for b in complete if all(score(indexed[(*b, arm)]) is not None for arm in arm_list)]
    completed_by_arm = {
        arm: [b for b in blocks if indexed.get((*b, arm), {}).get("status") == "completed"]
        for arm in arm_list
    }
    aggregates = {}
    for arm in arm_list:
        runs = [indexed[(*b, arm)] for b in completed_by_arm[arm]]
        scores = [score(indexed[(*b, arm)]) for b in eligible]
        aggregates[arm] = {
            "meanSeconds": round(mean(r["elapsedSeconds"] for r in runs), 2) if runs else None,
            "toolCalls": sum(r.get("toolCalls", 0) for r in runs),
            "toolErrors": sum(r.get("toolErrors", 0) for r in runs),
            "score": sum(s[0] for s in scores) if scores else None,
            "possible": sum(s[1] for s in scores) if scores else None,
            "issues": sum(s[2] for s in scores) if scores else None,
        }
        metrics = {kind: {"score": 0, "possible": 0} for kind in ("core", "coverage", "evidence")}
        for block in eligible:
            result = indexed[(*block, arm)]
            values = check_values(result)
            if values is None:
                continue
            labels = cases[block[0]].get("checks") or []
            for index, value in enumerate(values):
                kind = check_kind(labels[index] if index < len(labels) else "")
                metrics[kind]["score"] += int(value)
                metrics[kind]["possible"] += 1
        aggregates[arm]["metrics"] = metrics
    pairs = []

    def append_pairs(control_arm, treatment_arm):
        if control_arm not in arm_list or treatment_arm not in arm_list:
            return
        # Pair eligibility belongs to this comparison. A failed third arm
        # must not erase a valid control/treatment pair.
        pair_blocks = [block for block in blocks
                       if indexed.get((*block, control_arm), {}).get("status") == "completed"
                       and indexed.get((*block, treatment_arm), {}).get("status") == "completed"]
        for block in pair_blocks:
            control = indexed[(*block, control_arm)]
            treatment = indexed[(*block, treatment_arm)]
            control_score = score(control)
            treatment_score = score(treatment)
            control_seconds = control.get("elapsedSeconds")
            treatment_seconds = treatment.get("elapsedSeconds")
            pairs.append({
                "questionId": block[0], "repeat": block[1], "arm": treatment_arm,
                "controlArm": control_arm,
                "category": cases[block[0]].get("category", "未分类"),
                "scored": bool(control_score and treatment_score),
                "scoreDelta": treatment_score[0] - control_score[0] if control_score and treatment_score else None,
                "issuesDelta": treatment_score[2] - control_score[2] if control_score and treatment_score else None,
                "controlScore": f"{control_score[0]}/{control_score[1]}" if control_score else None,
                "treatmentScore": f"{treatment_score[0]}/{treatment_score[1]}" if treatment_score else None,
                "toolDelta": treatment.get("toolCalls", 0) - control.get("toolCalls", 0),
                "secondsDelta": round(treatment_seconds - control_seconds, 2)
                if treatment_seconds is not None and control_seconds is not None else None,
            })

    if "raw" in arm_list:
        for arm in arm_list:
            if arm != "raw":
                append_pairs("raw", arm)
    elif "code_only" in arm_list:
        for arm in arm_list:
            if arm != "code_only":
                append_pairs("code_only", arm)
    append_pairs("optional", "ast")
    return {
        "scoreSource": source,
        "plannedBlocks": len(blocks),
        "costBlocks": len(complete),
        "qualityBlocks": len(eligible),
        "aggregates": aggregates,
        "pairs": pairs,
    }

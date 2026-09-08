"""Real Claude Code comparisons for baseline availability and backbone rewrites.

The execution logic lives in ``business_code_agent.evaluation`` so the CLI and
the effect-verification page share one harness. This file keeps the command
line interface and freezes itself plus ``backbone_report.py`` into every new
result directory for later inspection.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from business_code_agent.evaluation import (ARMS_AB, ARM_DESCRIPTIONS_AB, freeze_batch, generate_report,
                                            rejudge_output, run_batch, validate_suite)


def _comparison_setup(args, documents):
    """Arm list, frozen baseline variants and descriptions for one comparison mode."""
    if args.comparison in ("diagnostic", "abc", "diagnostic_ast"):
        from business_code_agent.evaluation.runner import COMPARISONS
        descriptions = COMPARISONS[args.comparison]
        return list(descriptions), None, dict(descriptions)
    if args.comparison == "rewrite":
        return (["code_only", "legacy", "revised"],
                {"code_only": {}, "legacy": {"withdraw-flow.md": args.legacy_baseline.read_text(encoding="utf-8")},
                 "revised": dict(documents)},
                {"code_only": "代码及原仓库 README", "legacy": "相同代码与 README + 冻结的旧版基线，可选读取",
                 "revised": "相同代码与 README + 新版项目总览（运行时提供）及业务流程（按需读取）"})
    if args.comparison == "ab":
        return (list(ARMS_AB), {"code_only": {}, "optional": dict(documents)}, dict(ARM_DESCRIPTIONS_AB))
    return (["code_only", "optional", "preloaded"],
            {"code_only": {}, "optional": dict(documents), "preloaded": dict(documents)},
            {"code_only": "代码及原仓库 README", "optional": "当前业务资料，按生产方式提供总览及按需文档",
             "preloaded": "相同资料，另把全部基线文档注入上下文；仅诊断内容效用"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="新建结果目录，默认使用时间戳")
    parser.add_argument("--rejudge", type=Path, help="仅对已有结果重新评审，保留旧评分")
    parser.add_argument("--rubric-suite", type=Path, help="重评时使用修订题库；必须同时指定独立 --output")
    parser.add_argument("--suite", type=Path, help="题库 JSON（含 projectConfig、cases）")
    parser.add_argument("--judge", action="store_true", help="独立会话读取源码进行模型初评")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit-jobs", type=int)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--start-repeat", type=int, default=1)
    parser.add_argument("--blocks", nargs="+", help="Optional case:repeat blocks, each including all comparison arms")
    parser.add_argument("--comparison", choices=["ab", "abc", "diagnostic", "diagnostic_ast", "current", "rewrite"], default="ab")
    parser.add_argument("--legacy-baseline", type=Path, help="Frozen baseline file to compare against a rewrite")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers 必须大于 0")
    if args.rejudge:
        target = args.rejudge.resolve()
        if args.rubric_suite:
            if not args.output:
                parser.error("修订标准重评必须指定独立 --output，以保留旧批次")
            from business_code_agent.evaluation.diagnosis import prepare_reassessment
            revised = json.loads(args.rubric_suite.read_text(encoding="utf-8"))
            target = prepare_reassessment(target, args.output, revised, args.cases)
        elif args.cases:
            parser.error("重评子集需要 --rubric-suite 和独立 --output")
        if args.prepare_only:
            print(target)
            return 0
        results = rejudge_output(target, workers=args.workers)
        return 2 if any(r.get("status") != "completed" or r.get("review", {}).get("status") != "completed"
                        for r in results) else 0
    if args.comparison == "rewrite" and not args.legacy_baseline:
        parser.error("--comparison rewrite requires --legacy-baseline")
    if args.repeats < 1:
        parser.error("repeats 必须大于 0")
    root = Path(__file__).resolve().parents[1]
    suite_path = (args.suite or root / "evaluations/withdraw.json").resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = validate_suite(suite)
    if args.cases and set(args.cases) - {case["id"] for case in cases}:
        parser.error("--cases 包含题库不存在的 id")
    config_path = (suite_path.parent / suite["projectConfig"]).resolve()
    try:
        from business_code_agent.evaluation.runner import baseline_documents, resolve_project_sources
        _, _, baseline_root, _ = resolve_project_sources(config_path)
        if not baseline_root.is_dir():
            parser.error("业务基线目录不存在")
        documents = baseline_documents(baseline_root)
        arms, variants, arm_descriptions = _comparison_setup(args, documents)
        ast_version_id = None
        ast_generation_seconds = None
        if any(arm in ("ast", "overview_ast") for arm in arms) and variants is None:
            from business_code_agent.evaluation.ast_service import AstService
            ast_current = AstService(project_config=config_path).current_version()
            if not ast_current:
                parser.error("AST 对照必须先在 AST 管理页手动生成可用版本")
            ast_version_id, ast_metadata, ast_root = ast_current
            ast_generation_seconds = ast_metadata.get("generationSeconds")
            ast_documents = {str(path.relative_to(ast_root)): path.read_text(encoding="utf-8")
                             for path in ast_root.rglob("*.md")}
            variants = {}
            for arm in arms:
                if arm == "code_only":
                    variants[arm] = {}
                elif arm == "optional":
                    variants[arm] = documents
                elif arm == "overview":
                    variants[arm] = {"project-overview.md": documents.get("project-overview.md", "") +
                                     "\n\n## 本轮资料范围\n\n本轮只提供业务总览，未提供人工流程主干文件；请从源码确认实现。\n"}
                elif arm == "ast":
                    variants[arm] = ast_documents
                elif arm == "overview_ast":
                    overview = (documents.get("project-overview.md", "") +
                                "\n\n## 本轮资料范围\n\n本轮只提供业务总览，未提供人工流程主干文件；请从源码确认实现。\n")
                    variants[arm] = {**{key: value for key, value in ast_documents.items()
                                       if key != "project-overview.md"},
                                     "ast-overview.md": ast_documents.get("project-overview.md", ""),
                                     "project-overview.md": overview +
                                     "\n\n## 可选代码结构资料\n\n[AST结构导航](ast-overview.md)：仅含机械提取的代码结构，实际行为以源码为准。\n"}
                else:
                    variants[arm] = documents
        output = Path(args.output or root / ".data/evaluations"
                      / datetime.now().strftime("backbone-%Y%m%d-%H%M%S")).resolve()
        freeze_batch(output, suite=suite, cases=cases, project_config=config_path,
                     arms=arms, arm_descriptions=arm_descriptions, variants=variants,
                     comparison=args.comparison, repeats=args.repeats,
                     case_ids=set(args.cases) if args.cases else None, judge=args.judge,
                     workers=args.workers, start_repeat=args.start_repeat, blocks=args.blocks,
                     limit_jobs=args.limit_jobs, ast_version_id=ast_version_id,
                     ast_generation_seconds=ast_generation_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    # Freeze the harness scripts alongside the data for later inspection.
    for script in (Path(__file__), Path(__file__).with_name("backbone_report.py")):
        shutil.copy2(script, output / script.name)
    if args.prepare_only:
        print(output, flush=True)
        return 0

    def on_finish(value):
        print(json.dumps({k: value.get(k) for k in ("id", "status", "elapsedSeconds", "toolCalls",
                                                    "toolErrors", "baselineReadCalls", "error")},
                         ensure_ascii=False), flush=True)

    results = run_batch(output, workers=args.workers, judge=args.judge, on_finish=on_finish)
    generate_report(output)
    print(f"报告：{output / 'report.md'}", flush=True)
    if any(result.get("status") != "completed" or (args.judge and result.get("review", {}).get("status") != "completed")
           for result in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

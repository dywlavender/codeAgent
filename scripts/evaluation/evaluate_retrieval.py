"""Freeze and measure evidence retrieval, or answer an existing frozen context."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from business_code_agent.evaluation.fixed_context import answer_context
from business_code_agent.codeintel.retrieval import CONTEXT_POLICIES
from business_code_agent.evaluation.retrieval import evaluate, freeze_structure, prepare, read_json, write_json
from business_code_agent.evaluation.retrieval_service import RetrievalEvaluationService
from business_code_agent.platform.projects import ProjectContext, ProjectRegistry
from business_code_agent.platform.env import load_env_file


def main():
    load_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path)
    parser.add_argument("--project-registry", type=Path)
    parser.add_argument("--project-id")
    parser.add_argument("--suite", type=Path, help="带原文证据标注的题库")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batch", type=Path, help="复用已经冻结的批次")
    parser.add_argument("--budgets", type=int, nargs="+", default=[4000, 12000])
    parser.add_argument("--candidate-limit", type=int, default=40)
    parser.add_argument("--split", choices=["development", "holdout"])
    parser.add_argument("--strategy", default="lexical")
    parser.add_argument("--context-policy", choices=CONTEXT_POLICIES, default="ranked")
    parser.add_argument("--reuse-candidates", help="复用同一冻结批次已保存的候选策略，不重新检索")
    parser.add_argument("--freeze-structure", action="store_true", help="显式保存已有代码索引的方法位置，不生成 AST")
    parser.add_argument("--retrieval-unit", choices=("windows", "methods"), default="windows")
    parser.add_argument("--structure-version", help="引用本批次已保存的方法位置版本")
    parser.add_argument("--compare-to", help="与本批次同题同预算的已有策略配对比较，可使用不同候选")
    parser.add_argument("--match-candidate-budget", action="store_true", help="按对照策略每题的候选原文规模限制新候选，不读取标注")
    parser.add_argument("--candidates", type=Path, help="外部检索器的冻结候选，仅含位置，不含替代原文")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--answer", help="对已有案例的固定上下文答题；不允许继续搜索")
    parser.add_argument("--context-budget", type=int, default=12000)
    parser.add_argument("--model", default="configured-default")
    parser.add_argument("--budget-usd", type=float, default=0.5)
    args = parser.parse_args()
    if args.project_registry:
        if not args.project_id:
            parser.error("注册表模式需要 --project-id")
        context = ProjectRegistry(args.project_registry).get(args.project_id)
    else:
        if not args.project_config:
            parser.error("需要 --project-config 或 --project-registry")
        config = args.project_config.resolve()
        context = ProjectContext.legacy(config.parent / ".data/knowledge.db", config)
    try:
        if args.batch:
            output = args.batch.resolve()
        else:
            if not args.suite:
                parser.error("新批次需要 --suite")
            run_id = "retrieval-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            output = (args.output or context.evaluations_root / "retrieval" / run_id).resolve()
            suite = read_json(args.suite)
            service = RetrievalEvaluationService(context)
            existing = {x["id"] for x in service.list_suites()["items"]}
            if suite.get("id") not in existing:
                service.import_suite(suite)
            prepare(context, suite, output)
            write_json(output / "state.json", {"id": output.name, "projectId": context.project_id,
                       "suiteId": suite["id"], "status": "prepared", "budgets": args.budgets})
        if args.freeze_structure:
            structure = freeze_structure(context, output)
            print(json.dumps({"versionId": structure["versionId"], "files": len(structure["files"]),
                              "methods": structure["methodCount"], "excluded": len(structure["excluded"]),
                              "preparationSeconds": structure["preparationSeconds"]}, ensure_ascii=False))
            return 0
        if args.prepare_only:
            print(output)
            return 0
        if args.answer:
            result = answer_context(output, context.project_id, args.answer, strategy=args.strategy,
                                    budget=args.context_budget, model=args.model, budget_usd=args.budget_usd)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "completed" else 2
        summary = evaluate(output, context.project_id, budgets=args.budgets,
                           candidate_limit=args.candidate_limit, split=args.split, strategy=args.strategy,
                           imported=read_json(args.candidates) if args.candidates else None,
                           context_policy=args.context_policy, candidate_source=args.reuse_candidates,
                           retrieval_unit=args.retrieval_unit, structure_version=args.structure_version,
                           comparison_source=args.compare_to, match_candidate_budget=args.match_candidate_budget)
        state_path = output / "state.json"
        state = read_json(state_path) if state_path.is_file() else {"id": output.name, "projectId": context.project_id}
        write_json(state_path, {**state, "status": summary["status"]})
        print((output / "report.md").read_text(encoding="utf-8"))
        print(f"结果目录：{output}")
        return 0
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

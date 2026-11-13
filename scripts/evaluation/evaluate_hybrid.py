"""Measure hybrid retrieval on frozen development evidence, without chat calls."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from business_code_agent.codeintel.hybrid.search import HybridSearch, assemble
from business_code_agent.evaluation.retrieval import evidence_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rerank-budget-ms", type=int, default=200)
    parser.add_argument("--case", action="append", dest="cases")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果已经存在，请使用新输出文件")
    protocol = json.loads((args.batch / "protocol.json").read_text(encoding="utf-8"))
    corpus = json.loads((args.batch / "corpus.json").read_text(encoding="utf-8"))
    search = HybridSearch(args.index_root)
    if search.index["projectId"] != protocol["projectId"]:
        parser.error("工程不一致")
    original = {
        d["sourceId"] + "/" + d["path"]: d["content"] for d in corpus["documents"]
    }
    if original != search.index["files"]:
        parser.error("当前索引原文与冻结批次不同，不能混用版本对照")
    # Load and warm local models separately from per-question latency.
    search.embedder.encode(["warmup"])
    search.reranker.run([("warmup", "public void run() {}")])
    rows = []
    for case in protocol["suite"]["cases"]:
        if case["split"] != "development":
            continue
        if args.cases and case["id"] not in args.cases:
            continue
        result = search.search(case["question"], budget_ms=args.rerank_budget_ms)
        candidates = result.pop("candidates")
        context = assemble(candidates)
        old = json.loads(
            (args.batch / "strategies/lexical" / f"{case['id']}-12000.json").read_text(
                encoding="utf-8"
            )
        )
        row = {
            "caseId": case["id"],
            "candidateCount": len(candidates),
            "candidateMetrics": evidence_metrics(case["evidence"], candidates),
            "contextMetrics": evidence_metrics(case["evidence"], context["selected"]),
            "baselineContextMetrics": old["contextMetrics"],
            "retrieval": result,
            "context": context["content"],
            "selected": context["selected"],
        }
        rows.append(row)
        print(
            case["id"],
            row["candidateMetrics"]["coverage"],
            row["contextMetrics"]["coverage"],
            result["timings"],
            flush=True,
        )
    summary = {
        "projectId": protocol["projectId"],
        "snapshotId": protocol["snapshotId"],
        "indexVersion": search.index["version"],
        "split": "development",
        "questions": len(rows),
        "candidateLimit": 50,
        "rerankBudgetMs": args.rerank_budget_ms,
        "contextChars": 12000,
        "contextLines": 300,
        "candidateCoverage": mean(r["candidateMetrics"]["coverage"] for r in rows),
        "contextCoverage": mean(r["contextMetrics"]["coverage"] for r in rows),
        "rerankApplied": sum(r["retrieval"]["rerankApplied"] for r in rows),
        "medianRerankMs": median(r["retrieval"]["timings"]["rerankMs"] for r in rows),
        "improved": sum(
            r["contextMetrics"]["coverage"] > r["baselineContextMetrics"]["coverage"]
            for r in rows
        ),
        "regressed": sum(
            r["contextMetrics"]["coverage"] < r["baselineContextMetrics"]["coverage"]
            for r in rows
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2)
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

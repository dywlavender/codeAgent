"""Source-preserving rubric revisions and investigation review artifacts."""

import json
import shutil
from pathlib import Path

from .harness import write_json


def prepare_reassessment(source, destination, suite, case_ids=None):
    """Reuse answers only when question text matches; never edit the old batch."""
    from .runner import validate_suite

    source, destination = Path(source).resolve(), Path(destination).resolve()
    revised = {c["id"]: c for c in validate_suite(suite)}
    protocol = json.loads((source / "protocol.json").read_text())
    old = {c["id"]: c for c in protocol["cases"]}
    selected = set(case_ids) if case_ids else set(old) & set(revised)
    if not selected or selected - set(old) or selected - set(revised):
        raise ValueError("重评题目必须同时存在于旧批次和修订题库")
    for key in selected:
        if old[key]["question"] != revised[key]["question"]:
            raise ValueError(f"{key} 问题正文变化，不能复用旧答案")
    if destination == source or source in destination.parents:
        raise ValueError("重评目录必须独立于原批次")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "inputs", destination / "inputs")
    protocol["reassessment"] = {"source": str(source), "mode": "same answers, revised rubric",
                                "originalCases": [old[k] for k in old if k in selected]}
    protocol["cases"] = [revised[k] for k in old if k in selected]
    protocol["suite"] = {**suite, "cases": protocol["cases"]}
    protocol["jobs"] = [j for j in protocol["jobs"] if j["case"] in selected]
    results = []
    for result in json.loads((source / "results.json").read_text()):
        if result["questionId"] not in selected:
            continue
        run_dir = destination / "runs" / result["id"]
        run_dir.mkdir(parents=True)
        for name in ("answer.md", "events.jsonl", "command.json", "CLAUDE.md"):
            previous = source / "runs" / result["id"] / name
            if previous.is_file():
                shutil.copy2(previous, run_dir / name)
        if result.get("review"):
            write_json(run_dir / "original-review.json", result.pop("review"))
        write_json(run_dir / "result.json", result)
        results.append(result)
    write_json(destination / "protocol.json", protocol)
    write_json(destination / "results.json", results)
    return destination


def investigation_artifacts(output, protocol, results):
    """Export ordered evidence; semantic judgments remain explicit review fields."""
    output = Path(output)
    template = {}
    traces = {}
    cases = {c["id"]: c for c in protocol["cases"]}
    for result in results:
        rows = []
        for index, tool in enumerate(result.get("toolTrace", []), 1):
            rows.append({"step": index, "tool": tool.get("name"),
                         "input": tool.get("input"), "status": tool.get("status"),
                         "seconds": tool.get("firstEventSeconds"),
                         "output": tool.get("output")})
        traces[result["id"]] = rows
        template[result["id"]] = {
            "category": cases[result["questionId"]].get("category", "uncategorized"),
            "operatorType": None, "firstCorrectLocationStep": None,
            "unrelatedSearchSteps": None, "missingKeySteps": None,
            "evidence": "", "note": "步骤从1开始；null为未复核，空数组表示已复核且无遗漏。不得把首次Read自动当成正确定位。",
        }
    write_json(output / "investigation-traces.json", traces)
    target = output / "investigation-review.json"
    if not target.exists():
        write_json(target, template)


def diagnosis_summaries(results, cases, arms, *, source="model", reviews=None):
    from .runner import summarize_pairs

    groups = {}
    for category in sorted({c.get("category", "uncategorized") for c in cases.values()}):
        subset = {key: c for key, c in cases.items() if c.get("category", "uncategorized") == category}
        groups[category] = summarize_pairs([r for r in results if r["questionId"] in subset],
                                           subset, arms, source=source, reviews=reviews)
    def compare(control, treatment):
        if control not in arms or treatment not in arms:
            return None
        # The existing pair engine expects a control named code_only. Rename only
        # temporary rows, preserving ids for review lookup and all original data.
        rows = [{**r, "arm": "code_only" if r["arm"] == control else "optional"}
                for r in results if r["arm"] in (control, treatment)]
        incremental = summarize_pairs(rows, cases, ["code_only", "optional"], source=source, reviews=reviews)
        incremental["controlArm"] = control
        incremental["treatmentArm"] = treatment
        return incremental
    usage = {}
    for arm in arms:
        rows = [r for r in results if r["arm"] == arm and r.get("status") == "completed"]
        observed = [r for r in rows if "referenceUsage" in r]
        usage[arm] = {"completed": len(rows), "observed": len(observed),
                      "overviewInjected": sum(bool(r["referenceUsage"].get("overviewInjected")) for r in observed),
                      "astContentCalls": sum(r["referenceUsage"].get("astContentCalls", 0) for r in observed),
                      "baselineContentCalls": sum(r.get("baselineContentCalls", 0) for r in rows)}
    return {"categories": groups, "fullVsOverview": compare("overview", "optional"),
            "astVsControl": compare("code_only", "ast"),
            "astVsOverview": compare("overview", "overview_ast"),
            "astVsFull": compare("optional", "overview_ast"), "referenceUsage": usage}


def diagnosis_markdown(diagnosis):
    lines = ["", "## 按调查类型分层", "",
             "| 类型 | 组别 | 可评分配对 | 检查项 | 平均秒 | 工具调用 |",
             "| --- | --- | ---: | --- | ---: | ---: |"]
    for category, summary in diagnosis["categories"].items():
        for arm, row in summary["aggregates"].items():
            score = "待评" if row["score"] is None else f'{row["score"]}/{row["possible"]}'
            lines.append(f'| {category} | {arm} | {summary["qualityBlocks"]} | {score} | {row["meanSeconds"]} | {row["toolCalls"]} |')
    for key, title in [("fullVsOverview", "完整主干相对仅总览"), ("astVsControl", "独立AST相对无主干"),
                       ("astVsOverview", "总览＋AST相对仅总览"), ("astVsFull", "总览＋AST相对完整主干")]:
        incremental = diagnosis.get(key)
        if not incremental:
            continue
        lines += ["", f"## {title}", "",
                  f'独立配对：成本 {incremental["costBlocks"]}，评分 {incremental["qualityBlocks"]}；差值为 {incremental["treatmentArm"]} 减 {incremental["controlArm"]}。',
                  "", "| 题目 | 分数差 | 秒数差 | 工具差 |", "| --- | ---: | ---: | ---: |"]
        for row in incremental["pairs"]:
            lines.append(f'| {row["questionId"]} / {row["repeat"]} | {row["scoreDelta"] if row["scored"] else "待评"} | {row["secondsDelta"]} | {row["toolDelta"]} |')
    lines += ["", "## 资料实际接入与使用", "", "读取过不等于有效；旧结果没有记录时标为未观测。",
              "", "| 组别 | 完成数 | 接入可观测数 | 总览注入数 | 主干正文访问 | AST正文访问 |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for arm, row in diagnosis.get("referenceUsage", {}).items():
        lines.append(f'| {arm} | {row["completed"]} | {row["observed"]} | {row["overviewInjected"] if row["observed"] else "未观测"} | {row["baselineContentCalls"]} | {row["astContentCalls"] if row["observed"] else "未观测"} |')
    return "\n".join(lines)

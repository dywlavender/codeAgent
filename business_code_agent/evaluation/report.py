"""Paired report files. Model reviews are provisional, never ground truth."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from .scoring import parse_review
from .runner import summarize_pairs
from .diagnosis import investigation_artifacts, diagnosis_summaries, diagnosis_markdown


def reviewed(result, cases, manual):
    """Legacy mixed-source scorer: manual review overrides the model review.

    Used by the CLI ``--reviews`` flow. The workbench page must instead use
    :func:`runner.summarize_pairs`, which keeps scoring sources separate.
    """
    if result["id"] in manual:
        entry = manual[result["id"]]
        checks = entry.get("checks", [])
        if len(checks) == len(cases[result["questionId"]]["checks"]) and all(type(x) is int and x in (0, 1) for x in checks):
            return sum(checks), len(checks), len(entry.get("issues", [])), "复核记录"
    model = result.get("review", {})
    if model.get("status") == "completed":
        try:
            checked = parse_review(json.dumps(model), len(cases[result["questionId"]]["checks"]),
                                   result.get("answer") if model.get("version") == 2 else None)
            return sum(c["met"] for c in checked["checks"]), len(checked["checks"]), len(checked["issues"]), "模型初评"
        except (ValueError, TypeError, KeyError):
            pass
    return None


def generate_report(output, manual_path=None):
    """Regenerate ``report.md``/``summary.json`` for a finished run directory."""
    output = Path(output)
    protocol = json.loads((output / "protocol.json").read_text(encoding="utf-8"))
    results = json.loads((output / "results.json").read_text(encoding="utf-8"))
    manual = json.loads(Path(manual_path).read_text(encoding="utf-8")) if manual_path else {}
    cases = {c["id"]: c for c in protocol["cases"]}
    indexed = {(r["questionId"], r["repeat"], r["arm"]): r for r in results}
    if len(indexed) != len(results):
        raise ValueError("结果存在重复的题目/轮次/组别")
    arms = list(protocol["arms"])
    blocks = sorted({(j["case"], j["repeat"]) for j in protocol["jobs"]})
    complete = [b for b in blocks if all(indexed.get((*b, arm), {}).get("status") == "completed" for arm in arms)]
    eligible = [b for b in complete if all(reviewed(indexed[(*b, arm)], cases, manual) is not None for arm in arms)]
    lines = ["# 知识主干对照报告", "",
             protocol.get("suite", {}).get("scope", "按本轮题库范围解释结果。"), "",
             f"计划 {len(blocks)} 个配对区组；全部组运行成功 {len(complete)}；全部组有评分 {len(eligible)}。",
             "运行失败不记为答案错误；质量只比较各组均有评分的相同题目及轮次。", "",
             "| 组别 | 完成配对平均秒 | 工具调用总数 | 工具错误 | 质量得分 | 额外问题条数 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    aggregates = {}
    for arm in arms:
        runs = [indexed[(*b, arm)] for b in complete]
        scores = [reviewed(indexed[(*b, arm)], cases, manual) for b in eligible]
        seconds = round(mean(r["elapsedSeconds"] for r in runs), 2) if runs else None
        tools = sum(r.get("toolCalls", 0) for r in runs)
        errors = sum(r.get("toolErrors", 0) for r in runs)
        earned, total, issues = (sum(s[i] for s in scores) for i in range(3))
        aggregates[arm] = {"meanSeconds": seconds, "toolCalls": tools, "toolErrors": errors,
                           "score": earned, "possible": total, "issues": issues}
        lines.append(f"| {arm} | {seconds if seconds is not None else '—'} | {tools} | {errors} | "
                     f"{str(earned) + '/' + str(total) if scores else '待评'} | {issues if scores else '待评'} |")
    lines += ["", "## 每题配对", "", "| 题目 / 轮次 / 类型 | 对比组 | 分数差 | 问题数差 | 工具次数差 | 秒数差 |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    control_arm = "raw" if "raw" in arms else "code_only"
    pairs = []
    for arm in arms:
        if arm == control_arm:
            continue
        pair_blocks = [block for block in blocks
                       if indexed.get((*block, control_arm), {}).get("status") == "completed"
                       and indexed.get((*block, arm), {}).get("status") == "completed"]
        for block in pair_blocks:
            control = indexed[(*block, control_arm)]
            treatment = indexed[(*block, arm)]
            a = reviewed(control, cases, manual)
            b = reviewed(treatment, cases, manual)
            pair = {"questionId": block[0], "repeat": block[1], "arm": arm,
                    "controlArm": control_arm,
                    "category": cases[block[0]].get("category", "未分类"),
                    "scoreDelta": b[0] - a[0] if a and b else None,
                    "issuesDelta": b[2] - a[2] if a and b else None,
                    "toolDelta": treatment.get("toolCalls", 0) - control.get("toolCalls", 0),
                    "secondsDelta": round(treatment["elapsedSeconds"] - control["elapsedSeconds"], 2)}
            pairs.append(pair)
            lines.append(f"| {block[0]} / {block[1]} / {pair['category']} | {arm} | "
                         f"{pair['scoreDelta'] if a and b else '待评'} | {pair['issuesDelta'] if a and b else '待评'} | "
                         f"{pair['toolDelta']} | {pair['secondsDelta']} |")
    methods = sorted({reviewed(indexed[(*b, arm)], cases, manual)[3] for b in eligible for arm in arms})
    lines += ["", "本报告质量评分来源：" + ("、".join(methods) if methods else "待评") + "。"]
    lines += ["", f"差值 = 各对照组减 {control_arm} 组；分数增加较好，错误和成本减少较好。", "", "## 结论边界", "",
              "若有未评分或失败配对，本轮验证不完整。得分、额外错误与成本必须共同查看；报告不把少调用或读取过主干判为有效。",
              "自动评分是同一模型服务的新会话初评；不主动提供待评答案的组名和检索轨迹，但答案本身可能透露来源，不能视为严格盲评。",
              "各组评分者均可读取同一份冻结源码、需求及所有版本业务文档，以核查引用。它可能误判；人工复核评分依据后可用 --reviews 重生成报告。评分成本不计入答题耗时和工具数。",
              "未自动判定“无关检索”及“首次正确定位”：工具调用总数不能替代这两个指标。轨迹保留供复核。",
              "本流程比较配置的资料组合效果；自动 Code Map 与 Business Context 的贡献分别解释，不把资料被读取本身当作收益。", "", "## 答案与评分", ""]
    review_template = {}
    for r in results:
        lines.append(f"- {r['id']}：{r.get('status')}；[答案](runs/{r['id']}/answer.md)，"
                     f"[运行与评分数据](runs/{r['id']}/result.json)")
        review_template[r["id"]] = {"checks": [None] * len(cases[r["questionId"]]["checks"]),
                                    "issues": [], "note": "逐项填 0 或 1；未复核保持 null"}
    models = sorted({model for r in results for model in r.get("metadata", {}).get("reportedModels", [])})
    lines += ["", "模型报告的名称：" + ("、".join(models) if models else "未记录") + "。本地网关的上游身份未独立核实。"]
    prep = protocol.get("referencePreparation") or {}
    if prep:
        lines += ["", "## 资料准备成本", "", f"AST生成耗时：{prep.get('astGenerationSeconds')} 秒（独立生成，不计入答题耗时；版本：{prep.get('astVersionId') or '未使用'}）。",
                  "", "| 组别 | 文档数 | 总字符数 | 自动总览字符数 |", "| --- | ---: | ---: | ---: |"]
        for arm, item in prep.get("arms", {}).items():
            lines.append(f"| {arm} | {item['documents']} | {item['characters']} | {item['overviewCharacters']} |")
    diagnosis = diagnosis_summaries(results, cases, arms)
    investigation_artifacts(output, protocol, results)
    lines += ["", "以下分层与增量统计使用模型初评，人工复核请使用页面复核视图导出。", diagnosis_markdown(diagnosis)]
    write_diagnosis = output / "diagnosis-summary.json"
    write_diagnosis.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "summary.json").write_text(json.dumps({"completeBlocks": len(complete), "reviewedBlocks": len(eligible),
        "aggregates": aggregates, "pairs": pairs}, ensure_ascii=False, indent=2), encoding="utf-8")
    template = output / "review-template.json"
    if not template.exists():
        template.write_text(json.dumps(review_template, ensure_ascii=False, indent=2), encoding="utf-8")
    return aggregates


SOURCE_LABELS = {"model": "模型初评", "review": "复核记录"}


def build_view_report(*, protocol, results, cases, source, reviews=None, notes=None):
    """Markdown export for one explicit scoring source, without writing files."""
    arms = list(protocol["arms"])
    summary = summarize_pairs(results, cases, arms, source=source, reviews=reviews)
    suite = protocol.get("suite", {})
    lines = [f"# 知识主干对照报告（{SOURCE_LABELS.get(source, source)}视图）", "",
             suite.get("scope", "按本轮题库范围解释结果。"), "",
             f"计划 {summary['plannedBlocks']} 个配对区组；全部组运行成功 {summary['costBlocks']}；"
             f"当前评分来源可用 {summary['qualityBlocks']}。质量与成本的分母可能不同。", "",
             "| 指标 | " + " | ".join(arms) + " |",
             "| --- | " + " | ".join("---:" for _ in arms) + " |"]
    for label, key, formatter in (
        ("检查项满足数", "score", lambda value: "待评" if value is None else str(value)),
        ("平均答题耗时（秒）", "meanSeconds", lambda value: "—" if value is None else str(value)),
        ("工具调用总数", "toolCalls", str),
        ("工具错误", "toolErrors", str),
        ("额外问题条数", "issues", lambda value: "待评" if value is None else str(value)),
    ):
        cells = []
        for arm in arms:
            value = summary["aggregates"][arm][key]
            if key == "score" and value is not None:
                value = f"{value}/{summary['aggregates'][arm]['possible']}"
            cells.append(formatter(value))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## 每题配对", "",
              "| 题目 / 轮次 / 类型 | 对比组 | 分数差 | 问题数差 | 工具次数差 | 秒数差 |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for pair in summary["pairs"]:
        lines.append(f"| {pair['questionId']} / {pair['repeat']} / {pair['category']} | {pair['arm']} | "
                     f"{pair['scoreDelta'] if pair['scored'] else '待评'} | "
                     f"{pair['issuesDelta'] if pair['scored'] else '待评'} | {pair['toolDelta']} | {pair['secondsDelta']} |")
    control_arm = "raw" if "raw" in arms else "code_only"
    lines += ["", "## 结论边界", "",
              f"差值 = 各对照组减 {control_arm} 组；分数增加较好，错误和成本减少较好。单轮或小样本不构成稳定结论。",
              "模型初评是同一模型服务的新会话评分，存在漏判与引文改写，需人工复核后才能作为验收依据。",
              "未自动判定“无关检索”及“首次正确定位”；工具调用总数不能替代这两个指标。",
              "本流程比较配置的资料组合效果；自动 Code Map 与 Business Context 的贡献分别解释，不把资料被读取本身当作收益。"]
    diagnosis = diagnosis_summaries(results, cases, arms, source=source, reviews=reviews)
    lines.append(diagnosis_markdown(diagnosis))
    summary["diagnosis"] = diagnosis
    if notes:
        lines += ["", "## 备注", ""] + [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n", summary

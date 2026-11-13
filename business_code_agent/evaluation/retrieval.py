"""Frozen, answer-independent evidence retrieval evaluation."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from business_code_agent.codeintel.retrieval import (CONTEXT_POLICIES, CONTEXT_POLICY_VERSIONS, TextRetriever,
                                                    candidate_chars, candidate_prefix, collect_documents, pack_context, passages)
from business_code_agent.codeintel.structure import existing_method_ranges, method_passages
from business_code_agent.query.workspace import WorkspaceManager

ROLES = {"implementation", "condition", "outcome", "business_semantics", "relation", "navigation"}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_selection(candidates: list[dict], selected: list[dict]) -> None:
    """Verify selected line ranges against candidate text, including empty lines."""
    available = {(p["sourceId"], p["path"], n): line for p in candidates
                 for n, line in enumerate(p["content"].split("\n"), p["lineStart"])}
    for item in selected:
        lines = item["content"].split("\n")
        if len(lines) != item["lineEnd"] - item["lineStart"] + 1 or any(
                available.get((item["sourceId"], item["path"], n)) != line
                for n, line in enumerate(lines, item["lineStart"])):
            raise ValueError("选入上下文的原文或行号与候选不一致")


def validate_suite(suite: dict, project_id: str) -> dict:
    if suite.get("projectId") != project_id:
        raise ValueError("检索题库所属工程与当前工程不一致")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("检索题库至少需要一道带证据标注的题目")
    ids = set()
    for case in cases:
        case_id = case.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id) or case_id in ids:
            raise ValueError("检索案例编号应唯一且只含字母、数字、下划线或连字符")
        ids.add(case_id)
        if not str(case.get("question") or "").strip() or not case.get("evidence"):
            raise ValueError(f"{case_id} 缺少问题或证据标注")
        if case.get("split") not in {"development", "holdout"}:
            raise ValueError(f"{case_id} 必须指定 development 或 holdout")
        groups = set()
        for group in case["evidence"]:
            if not group.get("id") or group["id"] in groups or group.get("role") not in ROLES:
                raise ValueError(f"{case_id} 证据编号或角色不合法")
            groups.add(group["id"])
            if not group.get("alternatives"):
                raise ValueError(f"{case_id} 每项证据至少需要一种可核对的证明路径")
            for alternative in group["alternatives"]:
                if not isinstance(alternative, list) or not alternative:
                    raise ValueError(f"{case_id} 证明路径不能为空")
                for anchor in alternative:
                    if not all(isinstance(anchor.get(k), str) and anchor[k].strip()
                               for k in ("sourceId", "path", "quote")):
                        raise ValueError(f"{case_id} 证据需提供 sourceId、path 和原文 quote")
    return suite


def project_sources(context) -> list[dict]:
    if not context.config_path:
        raise ValueError("检索诊断需要项目配置")
    manager = WorkspaceManager(project_config=context.config_path)
    sources = [{"id": rid, "kind": "source", "root": str(path)}
               for rid, path in manager._repository_sources()]
    business = context.business_context_root if context.registered else manager.business_context_root
    requirements = context.requirements_root if context.registered else manager.requirements_root
    for source_id, kind, path in (("business-context", "business_context", business),
                                  ("requirements", "requirement", requirements)):
        if path and path.is_dir():
            if source_id in {s["id"] for s in sources}:
                raise ValueError(f"仓库编号与资料编号冲突：{source_id}")
            sources.append({"id": source_id, "kind": kind, "root": str(path)})
    return sources


def resolve_evidence(suite: dict, documents: list[dict]) -> dict:
    """Require exact, unambiguous source quotes before a batch can be scored."""
    suite = json.loads(json.dumps(suite))
    lookup = {(d["sourceId"], d["path"]): d for d in documents}
    for case in suite["cases"]:
        for group in case["evidence"]:
            for alternative in group["alternatives"]:
                for anchor in alternative:
                    document = lookup.get((anchor["sourceId"], anchor["path"]))
                    if not document:
                        raise ValueError(f"{case['id']} 证据文件不在本工程资料中：{anchor['path']}")
                    content, quote = document["content"], anchor["quote"]
                    positions = [m.start() for m in re.finditer(re.escape(quote), content)]
                    if anchor.get("lineStart"):
                        positions = [p for p in positions if content.count("\n", 0, p) + 1 == anchor["lineStart"]]
                    if len(positions) != 1:
                        raise ValueError(f"{case['id']} 引文不存在或不唯一，请修订标注：{anchor['path']} {quote[:45]}")
                    position = positions[0]
                    anchor["lineStart"] = content.count("\n", 0, position) + 1
                    anchor["lineEnd"] = anchor["lineStart"] + quote.count("\n")
                    anchor["kind"] = document["kind"]
    return suite


def prepare(context, suite: dict, output: Path) -> dict:
    validate_suite(suite, context.project_id)
    if output.exists() and any(p.name != "state.json" for p in output.iterdir()):
        raise ValueError("结果目录已存在，请创建独立批次")
    if (output / "state.json").is_file() and read_json(output / "state.json").get("projectId") != context.project_id:
        raise ValueError("结果目录所属工程不一致")
    started = time.monotonic()
    sources = project_sources(context)
    documents, skipped = collect_documents(sources)
    resolved = resolve_evidence(suite, documents)
    output.mkdir(parents=True, exist_ok=True)
    protocol = {"version": 1, "kind": "retrieval", "projectId": context.project_id,
                "snapshotId": output.name, "createdAt": datetime.now(timezone.utc).isoformat(),
                "suite": resolved, "sources": sources, "documents": len(documents),
                "skipped": skipped,
                "annotationStatus": suite.get("annotationStatus", "draft"),
                "annotationSource": suite.get("annotationSource", "unspecified")}
    write_json(output / "corpus.json", {"projectId": context.project_id, "documents": documents})
    protocol["preparationSeconds"] = round(time.monotonic() - started, 3)
    write_json(output / "protocol.json", protocol)
    return protocol


def check_project(output: Path, project_id: str) -> dict:
    protocol = read_json(output / "protocol.json")
    if protocol.get("projectId") != project_id:
        raise ValueError("不能读取或续用其他工程的检索批次")
    return protocol


def freeze_structure(context, output: Path) -> dict:
    """Explicitly save existing index positions; never create or update AST."""
    protocol = check_project(output, context.project_id)
    corpus = read_json(output / "corpus.json")
    if corpus.get("projectId") != context.project_id:
        raise ValueError("冻结资料所属工程不一致")
    before = time.monotonic()
    structure = existing_method_ranges(context.db_path, corpus["documents"], protocol["sources"])
    version_id = "index-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snapshot = {**structure, "projectId": context.project_id, "snapshotId": protocol["snapshotId"],
                "versionId": version_id, "createdAt": datetime.now(timezone.utc).isoformat(),
                "preparationSeconds": round(time.monotonic() - before, 4)}
    write_json(output / "structures" / f"{version_id}.json", snapshot)
    write_json(output / "structures" / f"{version_id}.meta.json", structure_metadata(snapshot))
    return snapshot


def structure_metadata(structure: dict) -> dict:
    return {**{k: structure[k] for k in ("projectId", "snapshotId", "versionId", "createdAt", "origin", "language", "preparationSeconds", "methodCount")},
            "fileCount": len(structure["files"]), "excludedCount": len(structure["excluded"])}


def load_structure(output: Path, project_id: str, version_id: str) -> dict:
    protocol = check_project(output, project_id)
    if not re.fullmatch(r"index-[A-Za-z0-9_-]+", str(version_id or "")):
        raise ValueError("请选择本批次已保存的方法位置版本")
    path = output / "structures" / f"{version_id}.json"
    if not path.is_file():
        raise ValueError("本批次没有该方法位置版本；不会自动生成结构资料")
    structure = read_json(path)
    if structure.get("projectId") != project_id or structure.get("snapshotId") != protocol["snapshotId"]:
        raise ValueError("方法位置资料的工程或冻结版本不一致")
    return structure


def evidence_metrics(groups: list[dict], selected: list[dict]) -> dict:
    lines = set()
    for item in selected:
        lines.update((item["sourceId"], item["path"], n)
                     for n in range(item["lineStart"], item["lineEnd"] + 1))

    def covered(anchor):
        return all((anchor["sourceId"], anchor["path"], n) in lines
                   for n in range(anchor["lineStart"], anchor["lineEnd"] + 1))

    checks = [{"id": group["id"], "role": group["role"],
               "covered": any(all(covered(a) for a in alternative) for alternative in group["alternatives"])}
              for group in groups]
    relations = [g for g in checks if g["role"] == "relation"]
    return {"covered": sum(g["covered"] for g in checks), "required": len(checks),
            "coverage": sum(g["covered"] for g in checks) / len(checks),
            "annotatedEvidenceComplete": all(g["covered"] for g in checks),
            "relationCoverage": sum(g["covered"] for g in relations) / len(relations) if relations else None,
            "groups": checks}


def materialize_candidates(raw: list[dict], documents: list[dict], limit: int) -> list[dict]:
    """Imported results select frozen original text, never supplied replacement text."""
    lookup = {(d["sourceId"], d["path"]): d for d in documents}
    result = []
    for item in raw[:limit]:
        document = lookup.get((item.get("sourceId"), item.get("path")))
        if document is None:
            raise ValueError("候选文件不在冻结资料中")
        lines = document["content"].splitlines()
        start, end = item.get("lineStart"), item.get("lineEnd")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError("候选行号超出冻结文件范围")
        result.append({"id": f"{document['sourceId']}:{document['path']}:{start}-{end}",
                       "sourceId": document["sourceId"], "path": document["path"], "kind": document["kind"],
                       "lineStart": start, "lineEnd": end, "content": "\n".join(lines[start - 1:end])})
    return result


def evaluate(output: Path, project_id: str, *, budgets=(4000, 12000), candidate_limit=40,
             split=None, imported=None, strategy="lexical", context_policy="ranked",
             candidate_source=None, retrieval_unit="windows", structure_version=None,
             comparison_source=None, match_candidate_budget=False, cancel_check=None) -> dict:
    started = time.monotonic()
    protocol = check_project(output, project_id)
    if not budgets or any(type(n) is not int or not 500 <= n <= 100000 for n in budgets):
        raise ValueError("上下文预算应为 500—100000 的整数字符数")
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 500:
        raise ValueError("候选数量应为 1—500")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", strategy):
        raise ValueError("检索策略名称不合法")
    if context_policy not in CONTEXT_POLICIES:
        raise ValueError("未知的上下文选择策略")
    if retrieval_unit not in {"windows", "methods"}:
        raise ValueError("未知的检索单元")
    if structure_version is not None and (candidate_source is not None or imported is not None):
        raise ValueError("复用或导入候选时不能另换结构版本")
    if structure_version is not None and retrieval_unit != "methods":
        raise ValueError("方法位置版本只能与方法检索单元一起使用")
    if type(match_candidate_budget) is not bool:
        raise ValueError("候选原文预算匹配必须是布尔值")
    if match_candidate_budget and (not comparison_source or candidate_source is not None or imported is not None):
        raise ValueError("候选原文预算对照需要原生检索和明确的对照策略")
    if candidate_source is not None and (imported is not None or not re.fullmatch(r"[A-Za-z0-9_-]+", str(candidate_source))):
        raise ValueError("复用候选需指定本批次的一项策略，不能同时导入候选")
    target = output / "strategies" / strategy
    if target.exists():
        raise ValueError("同一冻结批次的策略结果已存在，请使用新的策略编号")
    corpus = read_json(output / "corpus.json")
    if corpus.get("projectId") != project_id:
        raise ValueError("冻结资料所属工程不一致")
    if imported is not None and (imported.get("projectId") != project_id
                                 or imported.get("snapshotId") != protocol["snapshotId"]):
        raise ValueError("导入候选的工程或冻结版本不一致")
    if imported is None and candidate_source is None and retrieval_unit == "windows" and strategy not in {"lexical", "lexical_compact"}:
        raise ValueError("自定义检索策略必须显式导入候选结果")
    cases = [c for c in protocol["suite"]["cases"] if split is None or c["split"] == split]
    if not cases:
        raise ValueError("所选题目集合为空")
    replay, baseline_rows = {}, {}
    source_summary = None
    if candidate_source is not None:
        source_root = output / "strategies" / candidate_source
        if not (source_root / "summary.json").is_file():
            raise ValueError("候选来源策略不存在或尚未完成")
        source_summary = read_json(source_root / "summary.json")
        if source_summary.get("projectId") != project_id or source_summary.get("snapshotId") != protocol["snapshotId"]:
            raise ValueError("候选来源的工程或冻结版本不一致")
        if source_summary.get("status") != "completed" or source_summary["candidateLimit"] != candidate_limit:
            raise ValueError("复用需使用已完成策略，并保持原候选数量设置")
        for case in cases:
            items = [r for r in source_summary["items"] if r["caseId"] == case["id"]]
            if not items:
                raise ValueError(f"候选来源缺少案例：{case['id']}")
            for item in items:
                baseline_rows[case["id"], item["budgetChars"]] = read_json(
                    source_root / f"{case['id']}-{item['budgetChars']}.json")
            replay[case["id"]] = baseline_rows[case["id"], items[0]["budgetChars"]]
    comparison_strategy = comparison_source or candidate_source
    if comparison_source is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(comparison_source)):
            raise ValueError("对照策略编号不合法")
        source_root = output / "strategies" / comparison_source
        if not (source_root / "summary.json").is_file():
            raise ValueError("对照策略不存在")
        compared = read_json(source_root / "summary.json")
        if compared.get("projectId") != project_id or compared.get("snapshotId") != protocol["snapshotId"]:
            raise ValueError("对照策略的工程或冻结版本不一致")
        keys = {(r["caseId"], r["budgetChars"]) for r in compared["items"]}
        for case in cases:
            for budget in budgets:
                if (case["id"], budget) not in keys:
                    raise ValueError("对照策略缺少相同问题与预算的结果")
                baseline_rows[case["id"], budget] = read_json(source_root / f"{case['id']}-{budget}.json")
    load_seconds = time.monotonic() - started
    index_started = time.monotonic()
    passage_lines = 30 if strategy == "lexical_compact" else 70
    structure = None
    retriever = None
    if imported is None and candidate_source is None:
        if retrieval_unit == "methods":
            structure = load_structure(output, project_id, structure_version)
            items = method_passages(corpus["documents"], structure, max_lines=passage_lines)
        else:
            items = passages(corpus["documents"], max_lines=passage_lines)
        retriever = TextRetriever(items)
    elif source_summary:
        retrieval_unit = source_summary.get("retrievalUnit", "windows")
        structure_version = source_summary.get("structureVersionId")
    index_seconds = time.monotonic() - index_started
    retrieval_strategy = (source_summary.get("retrievalStrategy", source_summary["strategy"]) if source_summary else
                          ("external" if imported is not None else ("lexical_methods" if retrieval_unit == "methods" else strategy)))
    target.mkdir(parents=True)
    rows = []
    for case in cases:
        if cancel_check and cancel_check():
            break
        before = time.monotonic()
        if candidate_source is not None:
            original = replay[case["id"]]["candidates"]
            candidates = materialize_candidates(original, corpus["documents"], candidate_limit)
            for current, saved in zip(candidates, original):
                for field in ("score", "unitType", "symbolName", "methodLineStart", "methodLineEnd", "structureVersionId"):
                    if field in saved:
                        current[field] = saved[field]
            if candidates != original:
                raise ValueError("已保存候选与冻结原文不一致，不能作为同候选对照")
        elif imported is None:
            candidates = retriever.search(case["question"], limit=candidate_limit)
        else:
            if case["id"] not in imported.get("cases", {}):
                raise ValueError(f"导入候选缺少案例：{case['id']}")
            candidates = materialize_candidates(imported["cases"][case["id"]], corpus["documents"], candidate_limit)
        candidate_budget = None
        if match_candidate_budget:
            candidate_budget = candidate_chars(baseline_rows[case["id"], budgets[0]]["candidates"])
            candidates = candidate_prefix(candidates, candidate_budget)
        elif candidate_source is not None:
            candidate_budget = replay[case["id"]].get("candidateBudgetChars")
        retrieval_seconds = time.monotonic() - before
        for budget in sorted(set(budgets)):
            assembly_started = time.monotonic()
            selected, context = pack_context(candidates, budget, policy=context_policy, question=case["question"])
            validate_selection(candidates, selected)
            assembly_seconds = time.monotonic() - assembly_started
            external_cost = (imported or {}).get("timings", {}).get(case["id"], {})
            candidate_metrics = evidence_metrics(case["evidence"], candidates)
            context_metrics = evidence_metrics(case["evidence"], selected)
            baseline = baseline_rows.get((case["id"], budget))
            comparison = None
            if baseline is not None:
                previous = {g["id"] for g in baseline["contextMetrics"]["groups"] if g["covered"]}
                current = {g["id"] for g in context_metrics["groups"] if g["covered"]}
                comparison = {"strategy": comparison_strategy, "candidatesUnchanged": candidates == baseline["candidates"],
                              "candidateCoverageBefore": baseline["candidateMetrics"]["coverage"],
                              "candidateCoverageDelta": candidate_metrics["coverage"] - baseline["candidateMetrics"]["coverage"],
                              "coverageBefore": baseline["contextMetrics"]["coverage"],
                              "coverageDelta": context_metrics["coverage"] - baseline["contextMetrics"]["coverage"],
                              "gainedGroups": sorted(current - previous), "lostGroups": sorted(previous - current)}
            origin = replay.get(case["id"])
            row = {"caseId": case["id"], "question": case["question"], "split": case["split"],
                   "category": case.get("category", "uncategorized"), "strategy": strategy, "budgetChars": budget,
                   "retrievalStrategy": retrieval_strategy, "contextPolicy": context_policy,
                   "retrievalUnit": retrieval_unit, "structureVersionId": structure_version,
                   "contextPolicyVersion": CONTEXT_POLICY_VERSIONS[context_policy],
                   "candidateSource": candidate_source, "comparison": comparison,
                   "candidateCount": len(candidates), "candidateChars": candidate_chars(candidates),
                   "candidateBudgetChars": candidate_budget,
                   "contextChars": len(context),
                   "retrievalSeconds": (origin.get("retrievalSeconds") if origin is not None else
                                        (round(retrieval_seconds, 4) if imported is None else external_cost.get("retrievalSeconds"))),
                   "candidateReplaySeconds": round(retrieval_seconds, 4) if origin is not None else None,
                   "externalRetrievalUsage": (origin.get("externalRetrievalUsage") if origin is not None else
                                              (external_cost.get("usage") if imported is not None else None)),
                   "contextAssemblySeconds": round(assembly_seconds, 4),
                   "candidateMetrics": candidate_metrics,
                   "contextMetrics": context_metrics,
                   "oversizedCandidateUnits": sum(len(p["content"]) + len(f"[{p['kind']}] {p['sourceId']}/{p['path']}:{p['lineStart']}-{p['lineEnd']}\n") > budget for p in candidates),
                   "candidates": candidates, "selected": selected, "context": context,
                   "sufficiencyReview": None}
            rows.append(row)
            write_json(target / f"{case['id']}-{budget}.json", row)
    summary = {"projectId": project_id, "snapshotId": protocol["snapshotId"], "strategy": strategy,
               "retrievalStrategy": retrieval_strategy, "contextPolicy": context_policy, "candidateSource": candidate_source,
               "comparisonSource": comparison_strategy,
               "candidateBudgetSource": comparison_source if match_candidate_budget else (source_summary or {}).get("candidateBudgetSource"),
               "retrievalUnit": retrieval_unit, "structureVersionId": structure_version,
               "structureFiles": len(structure["files"]) if structure else (source_summary or {}).get("structureFiles"),
               "structureMethods": structure["methodCount"] if structure else (source_summary or {}).get("structureMethods"),
               "contextPolicyVersion": CONTEXT_POLICY_VERSIONS[context_policy],
               "status": "completed" if len(rows) == len(cases) * len(set(budgets)) else "cancelled",
               "annotationStatus": protocol["annotationStatus"], "candidateLimit": candidate_limit,
               "passageLines": source_summary.get("passageLines") if source_summary else (passage_lines if imported is None else None),
               "loadSeconds": round(load_seconds, 4), "indexSeconds": round(index_seconds, 4),
               "totalSeconds": round(time.monotonic() - started, 4),
               "items": [{k: v for k, v in r.items() if k not in {"candidates", "selected", "context"}} for r in rows]}
    write_json(target / "summary.json", summary)
    write_report(output)
    return summary


def write_report(output: Path) -> str:
    protocol = read_json(output / "protocol.json")
    lines = ["# 检索诊断", "", f"工程：{protocol['projectId']}；冻结版本：{protocol['snapshotId']}。",
             f"标注状态：{protocol['annotationStatus']}；来源：{protocol['annotationSource']}。", "",
             "词项检索是独立基线，不代表现有 Agent 的完整调查效果。标注覆盖不是上下文充分性或答案正确率；未标注内容不能自动判为无关。",
             "准备耗时与索引耗时单列；字符预算包括引用头和原文。复用候选保留原检索耗时，读取候选耗时另记，不代表重新执行了检索。", "",
             "lexical 使用最多 70 行的源码窗口；lexical_compact 使用最多 30 行。lexical_methods 使用已保存的方法位置，其他资料保留文本窗口。", "",
             "| 策略 | 检索 | 组装 | 集合 | 字符预算 | 题数 | 候选证据覆盖 | 上下文证据覆盖 | 全部标注覆盖题数 |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for path in sorted((output / "strategies").glob("*/summary.json")):
        summary = read_json(path)
        keys = sorted({(r["split"], r["budgetChars"]) for r in summary["items"]})
        for split, budget in keys:
            rows = [r for r in summary["items"] if (r["split"], r["budgetChars"]) == (split, budget)]
            candidate = sum(r["candidateMetrics"]["coverage"] for r in rows) / len(rows)
            context = sum(r["contextMetrics"]["coverage"] for r in rows) / len(rows)
            complete = sum(r["contextMetrics"]["annotatedEvidenceComplete"] for r in rows)
            lines.append(f"| {summary['strategy']} | {summary.get('retrievalStrategy', summary['strategy'])} | {summary.get('contextPolicy', 'ranked')} | {split} | {budget} | {len(rows)} | {candidate:.1%} | {context:.1%} | {complete} |")
    for path in sorted((output / "strategies").glob("*/summary.json")):
        summary = read_json(path)
        pairs = [r for r in summary["items"] if r.get("comparison")]
        if not pairs:
            continue
        lines.extend(["", f"## {summary['strategy']} 与 {summary.get('comparisonSource') or summary['candidateSource']} 的逐题对照", "",
                      "新增与丢失均指已标注证据组；候选不变仍需单独检查材料充分性。", "",
                      "| 案例 | 字符预算 | 候选相同 | 覆盖变化 | 新增标注 | 丢失标注 |",
                      "| --- | ---: | --- | ---: | --- | --- |"])
        for row in pairs:
            pair = row["comparison"]
            lines.append(f"| {row['caseId']} | {row['budgetChars']} | {'是' if pair['candidatesUnchanged'] else '否'} | {pair['coverageDelta']:+.1%} | {', '.join(pair['gainedGroups']) or '—'} | {', '.join(pair['lostGroups']) or '—'} |")
    text = "\n".join(lines) + "\n"
    (output / "report.md").write_text(text, encoding="utf-8")
    return text

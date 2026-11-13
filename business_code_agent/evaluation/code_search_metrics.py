"""阶段 0 结构化检索对照指标：引用有效性、未找到声明与双臂配对汇总。

指标定义（docs/spec/retrieval-evaluation.md 的“结构化检索对照”）：
- 引用有效性：回答中 ``path:line`` / ``path:start-end`` 引用必须在冻结源码
  内真实存在且行号在文件范围内；程序可校验存在性，相关性仍属评审判断。
- token 与轮次：来自运行结果 usage 与模型调用数，不另行计算。
- ``no_results`` 声明一致性：回答是否声明“未找到”，与该轮检索观察（如
  SearchSymbols 空结果、Grep no_matches）并列记录，正确性由评审复核。
"""

from __future__ import annotations

import re
from pathlib import Path

# path:line and path:start-end citations as agents emit them; paths may
# contain dots, dashes and slashes. Line numbers must be plain digits.
_CITATION_RE = re.compile(
    r"([\w.\-/\u4e00-\u9fff]+\.(?:java|xml|js|jsx|ts|tsx|md|py)):(\d+)(?:-(\d+))?"
)
_NO_RESULTS_RE = re.compile(
    r"(未找到|没有找到|未检索到|未发现相关|代码库中不存在|结构索引中不存在|无法定位到)"
)


def citation_report(answer: str, source_root: str | Path) -> dict:
    """Check every ``file:line`` citation in *answer* against frozen sources.

    Paths are resolved under *source_root* directly first, then by longest
    suffix match, so workspace-relative citations (``repos/<id>/...``) and
    batch-frozen copies (``repo-0/...``) both validate. A citation is valid
    when the file exists and both cited lines fall inside the file; a missing
    file or out-of-range line counts as invalid. Text outside citations is
    never treated as a citation.
    """
    root = Path(source_root) if source_root else None
    if root is None or not root.is_dir():
        # Without frozen sources nothing can be verified; report honestly
        # rather than treating every citation as invalid.
        return {"cited": 0, "valid": 0, "invalid": [], "note": "no-source-root"}
    suffixes: dict[str, Path] = {}
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                suffixes.setdefault(path.name, path)
                parts = path.relative_to(root).parts
                for depth in range(1, len(parts) + 1):
                    suffixes.setdefault("/".join(parts[-depth:]), path)
    cited = valid = 0
    invalid = []
    for match in _CITATION_RE.finditer(str(answer or "")):
        relative, start_text, end_text = match.group(1), match.group(2), match.group(3)
        cited += 1
        path = root / relative
        if not path.is_file():
            path = suffixes.get(relative)
        if path is None or not path.is_file():
            invalid.append({"citation": match.group(0), "reason": "file-missing"})
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            invalid.append({"citation": match.group(0), "reason": "file-unreadable"})
            continue
        start, end = int(start_text), int(end_text or start_text)
        if start < 1 or end > len(lines) or end < start:
            invalid.append({"citation": match.group(0),
                            "reason": "line-out-of-range", "fileLines": len(lines)})
            continue
        valid += 1
    return {"cited": cited, "valid": valid, "invalid": invalid}


def no_results_claim(answer: str) -> bool:
    """Whether the answer claims it could not find the asked-for code."""
    return bool(_NO_RESULTS_RE.search(str(answer or "")))


def code_search_comparison(runs: list[dict]) -> dict:
    """Pair baseline and experiment runs per case and report deltas.

    Each run record: ``{"caseId", "arm" ("baseline"|"experiment"),
    "answer", "sourceRoot", "totalTokens", "modelCalls", "noResultEvidence"}``.
    Paired deltas only cover cases present in both arms; missing arms are
    reported as unpaired, never averaged away.
    """
    by_case: dict[str, dict[str, dict]] = {}
    for run in runs:
        arm = run.get("arm")
        if arm not in {"baseline", "experiment"}:
            raise ValueError("arm 必须是 baseline 或 experiment")
        by_case.setdefault(str(run.get("caseId")), {})[arm] = run

    pairs = []
    for case_id, arms in sorted(by_case.items()):
        if len(arms) < 2:
            continue
        base, exp = arms["baseline"], arms["experiment"]
        base_report = citation_report(base.get("answer"), base.get("sourceRoot"))
        exp_report = citation_report(exp.get("answer"), exp.get("sourceRoot"))
        pairs.append({
            "caseId": case_id,
            "tokensDelta": (exp.get("totalTokens") or 0) - (base.get("totalTokens") or 0),
            "callsDelta": (exp.get("modelCalls") or 0) - (base.get("modelCalls") or 0),
            "citations": {"baseline": base_report, "experiment": exp_report},
            "noResults": {"baseline": no_results_claim(base.get("answer")),
                          "experiment": no_results_claim(exp.get("answer")),
                          "experimentHadEvidence": bool(exp.get("noResultEvidence"))},
        })
    unpaired = sorted(case_id for case_id, arms in by_case.items() if len(arms) < 2)
    totals = {
        "cases": len(pairs),
        "tokensDeltaSum": sum(item["tokensDelta"] for item in pairs),
        "callsDeltaSum": sum(item["callsDelta"] for item in pairs),
        "citationInvalid": {arm: sum(item["citations"][arm]["cited"] - item["citations"][arm]["valid"]
                                     for item in pairs) for arm in ("baseline", "experiment")},
    }
    return {"pairs": pairs, "unpaired": unpaired, "totals": totals}

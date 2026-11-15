"""Run Pi through the production QueryService and freeze answers plus traces.

The checks in the suite are evaluator inputs only.  They are never added to the
question sent to Pi.  This script does not judge correctness by itself; it
records the evidence needed to review key-relation coverage, search behaviour
and answer grounding after the fact.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from business_code_agent.platform.env import load_env_file
from business_code_agent.query.agent.pi import PiAgentRuntime
from business_code_agent.query.profile import PI
from business_code_agent.query.service import QueryService
from business_code_agent.storage.schema import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "._-" else "-" for char in str(value))
    return text.strip(".-") or "run"


def _project_config(suite_path: Path, suite: dict, override: Path | None) -> Path:
    if override:
        return override.expanduser().resolve()
    value = suite.get("projectConfig")
    if not value:
        raise ValueError("题库未声明 projectConfig，请使用 --project-config 指定")
    return (suite_path.parent / value).resolve()


def _project_id(config: Path) -> str:
    value = _load(config)
    return str((value.get("project") or {}).get("id") or "").strip() or "evaluation"


def analyze_trace(trace: dict) -> dict:
    """Deterministic investigation metrics; never a semantic correctness score."""
    run = trace.get("run") or {}
    usage = run.get("usage") or {}
    events = trace.get("events") or trace.get("runtimeEvents") or []
    workspace = str(run.get("workspace") or "").rstrip("/")

    def relative(value: str) -> str:
        path = str(value or "").replace("\\", "/")
        if workspace and path.startswith(workspace + "/"):
            path = path[len(workspace) + 1:]
        if path.startswith("./"):
            path = path[2:]
        return path

    tools: dict[str, dict] = {}
    ordered: list[dict] = []
    round_no = None
    for event in events:
        kind = event.get("eventType")
        payload = event.get("payload") or {}
        if kind == "model":
            round_no = payload.get("attempt") or round_no
            continue
        if kind != "tool":
            continue
        call_id = str(payload.get("id") or "")
        if not call_id:
            continue
        call = tools.get(call_id)
        if call is None:
            call = {
                "id": call_id,
                "round": round_no,
                "name": str(payload.get("name") or "").lower(),
                "input": payload.get("input") or {},
                "status": payload.get("status"),
                "output": None,
                "durationMs": payload.get("durationMs"),
            }
            tools[call_id] = call
            ordered.append(call)
        else:
            call["status"] = payload.get("status") or call["status"]
            call["durationMs"] = payload.get("durationMs", call.get("durationMs"))
        if payload.get("output") is not None:
            call["output"] = str(payload.get("output") or "")

    reads: dict[str, dict] = {}
    search_calls = []
    candidates: dict[str, dict] = {}
    repeated = {}
    empty_searches = 0
    tool_errors = 0
    for call in ordered:
        if call["status"] == "error":
            tool_errors += 1
        if call["status"] not in {"completed", "error"}:
            continue
        name, arguments = call["name"], call["input"] or {}
        key = json.dumps([name, arguments], ensure_ascii=False, sort_keys=True)
        repeated[key] = repeated.get(key, 0) + 1
        if name == "read" and call["status"] == "completed":
            path = relative(arguments.get("path") or "")
            output = call.get("output") or ""
            if not path or not output:
                continue
            try:
                start = max(1, int(arguments.get("offset", 1)))
            except (TypeError, ValueError):
                start = 1
            lines = output.splitlines()
            entry = reads.setdefault(path, {
                "path": path, "firstRound": call["round"], "rounds": [],
                "ranges": [], "contentChars": 0})
            if call["round"] not in entry["rounds"]:
                entry["rounds"].append(call["round"])
            if lines:
                entry["ranges"].append([start, start + len(lines) - 1])
            entry["contentChars"] += len(output)
            continue
        if name not in {"grep", "find"} or call["status"] != "completed":
            continue
        output = call.get("output") or ""
        found = []
        for line in output.splitlines():
            text = line.strip()
            if not text or text.startswith(("No matches", "no matches", "Found ", "（", "(")):
                continue
            path = text.split(":", 1)[0].strip() if name == "grep" else text
            path = relative(path)
            if not path or path.startswith(("No ", "error", "Error")):
                continue
            if name == "grep" and (":" not in text or "/" not in path and "." not in path):
                continue
            found.append(path)
        search_calls.append({
            "round": call["round"],
            "name": name,
            "input": arguments,
            "candidates": len(set(found)),
            "_paths": sorted(set(found)),
        })
        if not found:
            empty_searches += 1
        for path in found:
            candidates.setdefault(path, {
                "path": path, "tool": name, "round": call["round"]})

    read_paths = set(reads)

    def was_read(path: str) -> bool:
        return any(path == read or path.endswith("/" + read) or read.endswith("/" + path)
                   for read in read_paths)

    unread = [path for path in candidates if not was_read(path)]
    searches_without_read = 0
    for call in search_calls:
        paths = call.pop("_paths")
        read_candidates = sum(1 for path in paths if was_read(path))
        call["readCandidates"] = read_candidates
        if paths and not read_candidates:
            searches_without_read += 1
    repeated_extra = sum(count - 1 for count in repeated.values() if count > 1)

    def roots(paths):
        counts = {}
        for path in paths:
            first = path.split("/", 1)[0]
            counts[first] = counts.get(first, 0) + 1
        return counts

    repos = {}
    for path, entry in reads.items():
        parts = path.split("/")
        if len(parts) > 1 and parts[0] == "repos":
            repo = repos.setdefault(parts[1], {"reads": 0, "searches": 0})
            repo["reads"] += 1
    for call in search_calls:
        scope = str((call["input"] or {}).get("path") or "")
        parts = scope.split("/")
        if len(parts) > 1 and parts[0] == "repos":
            repo = repos.setdefault(parts[1], {"reads": 0, "searches": 0})
            repo["searches"] += 1

    references = (usage.get("verification") or {})
    reference_items = references.get("references") or []
    return {
        "modelCalls": usage.get("modelCalls") or len(trace.get("calls") or []),
        "toolCalls": len(ordered),
        "toolErrors": tool_errors,
        "uniqueReadFiles": len(reads),
        "readCalls": sum(1 for call in ordered if call["name"] == "read"),
        "searchCalls": len(search_calls),
        "emptySearches": empty_searches,
        "candidateFiles": len(candidates),
        "unreadCandidates": unread[:30],
        "unreadCandidateCount": len(unread),
        "repeatedToolCalls": repeated_extra,
        "searchesWithoutRead": searches_without_read,
        "invalidInvestigationProxy": repeated_extra + empty_searches + searches_without_read,
        "repos": repos,
        "materialReads": roots(path for path in reads if not path.startswith("repos/")),
        "references": {
            "total": len(reference_items),
            "confirmed": sum(1 for item in reference_items if item.get("confirmed")),
            "unconfirmed": references.get("unconfirmed") or [],
            "readFiles": references.get("readFiles") or [],
            "semanticProof": references.get("semanticProof"),
        },
        "totalTokens": usage.get("totalTokens"),
        "cost": usage.get("cost"),
        "durationMs": run.get("durationMs"),
        "stopReason": usage.get("stopReason"),
        "reads": [
            {"path": path, **entry}
            for path, entry in sorted(reads.items(),
                                      key=lambda item: (item[1].get("firstRound") or 9999,
                                                        item[0]))
        ],
        "searches": search_calls,
        "toolTimeline": [
            {"round": call["round"], "name": call["name"], "input": call["input"],
             "status": call["status"],
             "outputChars": len(call.get("output") or "")}
            for call in ordered
        ],
    }


def run_one(case: dict, *, mode: str, repeat: int, model: str | None,
            project_config: Path, project_id: str, timeout: float,
            thinking: str | None, output: Path) -> dict:
    run_id = f"{_safe_name(case['id'])}-{mode}-r{repeat}"
    started_at = _now()
    with tempfile.TemporaryDirectory(prefix="pi-eval-") as folder:
        root = Path(folder)
        db_path = root / "query.db"
        connect(str(db_path)).close()
        env = dict(os.environ)
        env["BUSINESS_CODE_PI_TIMEOUT"] = str(timeout)
        runtime = PiAgentRuntime(
            profile=PI, environment=env, model=model, timeout=timeout,
            session_root=root / "pi-sessions", thinking=thinking)
        service = QueryService(
            connect(str(db_path)), db_path=str(db_path),
            project_config=str(project_config), project_id=project_id,
            workspace_root=root / "workspaces", trace_root=root / "traces",
            retrieval_root=root / "retrieval-index", runtime=runtime)
        started = time.monotonic()
        try:
            result = service.query(case["question"], mode=mode, model=model)
        except Exception as exc:
            result = {"status": "failed", "errorType": type(exc).__name__,
                      "error": str(exc)[:500], "answer": "", "events": [],
                      "usage": {}, "execution": {}}
        finally:
            service.db.close()
        elapsed = round(time.monotonic() - started, 2)
        trace_source = Path((result.get("usage") or {}).get("traceArchive") or "")
        if not trace_source.is_file():
            trace_source = max((root / "traces").glob("*.json"),
                               key=lambda item: item.stat().st_mtime_ns, default=None)
        trace = {}
        if trace_source and Path(trace_source).is_file():
            trace = _load(Path(trace_source))
            shutil.copyfile(trace_source, output / "trace.json")
        events = result.pop("events", [])
        (output / "events.jsonl").write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
            encoding="utf-8")
        (output / "answer.md").write_text(str(result.get("answer") or ""), encoding="utf-8")
        metrics = analyze_trace(trace) if trace else {}
    record = {
        "caseId": case["id"],
        "category": case.get("category"),
        "question": case["question"],
        "checks": case.get("checks") or [],
        "mode": mode,
        "repeat": repeat,
        "status": result.get("status"),
        "answer": result.get("answer") or "",
        "error": result.get("error"),
        "errorType": result.get("errorType"),
        "runId": result.get("runId"),
        "conversationId": result.get("conversationId"),
        "startedAt": result.get("startedAt") or started_at,
        "completedAt": result.get("completedAt"),
        "elapsedSeconds": elapsed,
        "modelRequested": (result.get("execution") or {}).get("settings", {}).get(
            "requestedModel") or model,
        "modelActual": (result.get("usage") or {}).get("actualModel"),
        "piVersion": (result.get("usage") or {}).get("piVersion"),
        "modeLabel": result.get("modeLabel"),
        "execution": result.get("execution"),
        "usage": result.get("usage"),
        "metrics": metrics,
        "traceFile": "trace.json" if trace else None,
        "eventsFile": "events.jsonl",
        "answerFile": "answer.md",
    }
    _write(output / "result.json", record)
    (output / "review-template.json").write_text(json.dumps({
        "caseId": case["id"], "mode": mode, "repeat": repeat,
        "question": case["question"], "checks": case.get("checks") or [],
        "checksReview": [{"met": None, "candidateQuote": "", "reason": "", "evidence": ""}
                         for _ in case.get("checks") or []],
        "issues": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def summarize(batch: Path) -> dict:
    results = []
    for path in sorted(batch.glob("runs/*/result.json")):
        result = _load(path)
        trace = path.parent / "trace.json"
        if trace.is_file():
            result["metrics"] = analyze_trace(_load(trace))
        results.append(result)
    summary = {
        "generatedAt": _now(),
        "batch": str(batch),
        "runs": [{key: result.get(key) for key in (
            "caseId", "category", "mode", "repeat", "status", "runId",
            "modelRequested", "modelActual", "piVersion", "elapsedSeconds")}
                 | {"metrics": result.get("metrics") or {}}
                 for result in results],
    }
    _write(batch / "summary.json", summary)
    lines = ["# Pi 真实问答评测", "",
             f"- 批次：`{batch.name}`",
             f"- 生成时间：{summary['generatedAt']}",
             f"- 运行数：{len(results)}", "",
             "> 工具/搜索指标是确定性计数或启发式代理，不是答案正确性评分；"
             "结论可信度仍需按 checks 阅读答案与 Trace。", ""]
    groups = {}
    for result in results:
        groups.setdefault(result["caseId"], []).append(result)
    for case_id, rows in groups.items():
        first = rows[0]
        lines.extend([f"## {case_id}", "", f"**问题**：{first['question']}", ""])
        lines.extend(["| 组 | 重复 | 状态 | 模型 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 候选/未读 | 空搜索 | 无读取搜索 | 重复调用 | Token | 耗时 s | 引用确认 |",
                      "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for row in rows:
            metrics = row.get("metrics") or {}
            refs = metrics.get("references") or {}
            lines.append(
                f"| {row['mode']} | {row['repeat']} | {row['status']} | "
                f"{row.get('modelActual') or row.get('modelRequested') or '—'} | "
                f"{metrics.get('modelCalls', '—')} | {metrics.get('toolCalls', '—')} | "
                f"{metrics.get('uniqueReadFiles', '—')} | {metrics.get('searchCalls', '—')} | "
                f"{metrics.get('candidateFiles', '—')}/{metrics.get('unreadCandidateCount', '—')} | "
                f"{metrics.get('emptySearches', '—')} | {metrics.get('searchesWithoutRead', '—')} | "
                f"{metrics.get('repeatedToolCalls', '—')} | "
                f"{metrics.get('totalTokens', '—')} | {row.get('elapsedSeconds', '—')} | "
                f"{refs.get('confirmed', '—')}/{refs.get('total', '—')} |")
        lines.extend(["", "**评测检查项（仅评测器）**", ""])
        lines.extend(f"{index}. {check}" for index, check in enumerate(first.get("checks") or [], 1))
        for row in rows:
            lines.extend(["", f"### {row['mode']} / 第 {row['repeat']} 次", "",
                          f"- 状态：{row['status']}；Run：`{row.get('runId') or '—'}`；"
                          f"实际模型：{row.get('modelActual') or '—'}；Pi：{row.get('piVersion') or '—'}"])
            if row.get("error"):
                lines.append(f"- 错误：{row['errorType']}：{row['error']}")
            metrics = row.get("metrics") or {}
            if metrics.get("unreadCandidates"):
                lines.append("- 未读候选（启发式）：" + "、".join(
                    str(item) for item in metrics["unreadCandidates"][:10]))
            timeline = metrics.get("toolTimeline") or []
            if timeline:
                lines.append("- 工具时序：" + "；".join(
                    f"R{call.get('round') or '?'} {call['name']}"
                    f" {json.dumps(call.get('input') or {}, ensure_ascii=False)[:80]}"
                    f" → {call['status']}" for call in timeline))
            lines.extend(["", (row.get("answer") or "（无答案）").strip(), ""])
    report = "\n".join(lines) + "\n"
    (batch / "summary.md").write_text(report, encoding="utf-8")
    return summary


def command_run(args) -> int:
    suites = []
    cases_by_id = {}
    project_config = None
    for value in args.suite:
        suite_path = value.expanduser().resolve()
        suite = _load(suite_path)
        for case in suite.get("cases") or []:
            if case["id"] in cases_by_id:
                raise ValueError("多个题库存在同名案例：" + case["id"])
            cases_by_id[case["id"]] = case
        resolved = _project_config(suite_path, suite, args.project_config)
        if project_config is not None and resolved != project_config:
            raise ValueError("多个题库指向不同 projectConfig，不能合并为同一批次")
        project_config = resolved
        suites.append({"path": str(suite_path), "name": suite.get("name")})
    if args.case:
        missing = [case_id for case_id in args.case if case_id not in cases_by_id]
        if missing:
            raise ValueError("题库中不存在案例：" + "、".join(missing))
        cases = [cases_by_id[case_id] for case_id in args.case]
    else:
        cases = list(cases_by_id.values())
    modes = [value.strip() for value in args.modes.split(",") if value.strip()]
    if not modes:
        raise ValueError("--modes 不能为空")
    project_id = _project_id(project_config)
    output = args.output.expanduser().resolve()
    plan = [(case, mode, repeat) for case in cases for mode in modes
            for repeat in range(1, args.repeats + 1)]
    if args.plan:
        print(json.dumps({"projectId": project_id, "runs": [
            f"{case['id']}/{mode}/r{repeat}" for case, mode, repeat in plan]},
            ensure_ascii=False, indent=2))
        return 0
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"输出目录非空，请保留原批次并使用新目录：{output}")
    output.mkdir(parents=True, exist_ok=True)
    _write(output / "batch.json", {
        "suites": suites, "projectConfig": str(project_config),
        "projectId": project_id, "cases": [case["id"] for case in cases],
        "modes": modes, "repeats": args.repeats, "requestedModel": args.model,
        "timeoutSeconds": args.timeout, "thinking": args.thinking,
        "startedAt": _now(),
    })
    for index, (case, mode, repeat) in enumerate(plan, 1):
        folder = output / "runs" / f"{_safe_name(case['id'])}-{_safe_name(mode)}-r{repeat}"
        folder.mkdir(parents=True, exist_ok=True)
        print(f"[{index}/{len(plan)}] {case['id']} mode={mode} repeat={repeat}", flush=True)
        record = run_one(case, mode=mode, repeat=repeat, model=args.model,
                         project_config=project_config, project_id=project_id,
                         timeout=args.timeout, thinking=args.thinking, output=folder)
        print(json.dumps({"status": record["status"], "runId": record["runId"],
                          "actualModel": record["modelActual"],
                          "answerChars": len(record.get("answer") or ""),
                          "elapsedSeconds": record["elapsedSeconds"]},
                         ensure_ascii=False), flush=True)
    summarize(output)
    print(f"批次完成：{output / 'summary.md'}", flush=True)
    return 0


def command_report(args) -> int:
    batch = args.batch.expanduser().resolve()
    if not (batch / "runs").is_dir():
        raise ValueError(f"批次目录没有 runs：{batch}")
    summarize(batch)
    print(f"已更新：{batch / 'summary.md'}")
    return 0


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="按题库运行真实 Pi 问答")
    run.add_argument("--suite", type=Path, action="append", required=True,
                     help="题库文件，可重复；多个题库必须指向同一 projectConfig")
    run.add_argument("--case", action="append", help="案例 ID，可重复；省略表示全部")
    run.add_argument("--modes", default="none", help="逗号分隔，如 none,backbone")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--model", default=os.environ.get("BUSINESS_CODE_PI_MODEL") or None)
    run.add_argument("--project-config", type=Path)
    run.add_argument("--timeout", type=float, default=900)
    run.add_argument("--thinking")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--plan", action="store_true", help="只打印运行计划，不调用模型")
    report = sub.add_parser("report", help="从已有 runs 重新生成汇总")
    report.add_argument("--batch", type=Path, required=True)
    args = parser.parse_args()
    try:
        return command_run(args) if args.action == "run" else command_report(args)
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

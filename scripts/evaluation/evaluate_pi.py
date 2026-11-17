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
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from business_code_agent.platform.env import load_env_file
from business_code_agent.query.agent.citations import (canonical_citation_path,
                                                       match_reference_path,
                                                       verify_references)
from business_code_agent.query.agent.pi import PiAgentRuntime
from business_code_agent.query.agent.pi.events import answer_body
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


_GREP_LINE = re.compile(r"^(?P<path>.+?)(?P<sep>[:-])(?P<line>\d+)(?P=sep)")
_FILE_NAME = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,12}$")


def _candidate_path(line: str, tool: str, relative) -> str | None:
    """Extract a file path from one grep/find output line.

    Grep context lines use ``path-260- content``; only the leading path may be
    treated as a candidate, never the content after the line number.
    """
    text = str(line or "").strip()
    if not text or text.startswith(("No matches", "no matches", "Found ", "（", "(")):
        return None
    if tool == "grep":
        match = _GREP_LINE.match(text)
        if not match:
            return None
        path = match.group("path")
    else:
        path = text
    path = relative(path)
    if (not path or path.startswith(("No ", "error", "Error"))
            or any(char.isspace() for char in path)
            or not _FILE_NAME.search(path)):
        return None
    return path


def format_reference_text(entry: dict) -> str:
    path = str(entry.get("path") or "")
    line = entry.get("line")
    if line is None:
        return path
    try:
        start = int(line)
        end = int(entry.get("lineEnd") or line)
    except (TypeError, ValueError):
        return path
    return f"{path}:{start}" if end == start else f"{path}:{start}-{end}"


def _matches_key(path: str, key: str) -> bool:
    value = str(path or "")
    key = str(key or "").strip()
    return bool(key) and (value == key or value.endswith("/" + key)
                          or value.rsplit("/", 1)[-1] == key)


def load_keys(path: Path | None) -> dict[str, list[str]]:
    if not path:
        return {}
    value = _load(path)
    if not isinstance(value, dict) or any(
            not isinstance(items, list) or any(not isinstance(item, str) for item in items)
            for items in value.values()):
        raise ValueError("关键文件映射必须是 {caseId: [path, ...]} 的 JSON 对象")
    return value


def analyze_trace(trace: dict, keys=()) -> dict:
    """Deterministic investigation metrics; never a semantic correctness score."""
    run = trace.get("run") or {}
    usage = run.get("usage") or {}
    events = trace.get("events") or trace.get("runtimeEvents") or []
    workspace = str(run.get("workspace") or "").rstrip("/")

    def relative(value: str) -> str:
        return canonical_citation_path(value, workspace)

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
    read_outputs: list[dict] = []
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
                read_outputs.append({"path": path, "content": output,
                                     "lineStart": start,
                                     "lineEnd": start + len(lines) - 1})
            entry["contentChars"] += len(output)
            continue
        if name not in {"grep", "find"} or call["status"] != "completed":
            continue
        output = call.get("output") or ""
        found = []
        for line in output.splitlines():
            path = _candidate_path(line, name, relative)
            if path:
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
    answer = str(run.get("answer") or "")
    if answer:
        # Re-parse the archived answer body so old traces use the same
        # deduplication and path rules; goal-coverage citations are checked
        # separately and must not inflate answer reference counts.
        reference_items = verify_references(
            answer_body(answer), read_outputs, workspace=workspace)["references"]
    else:
        reference_items = references.get("references") or []
    search_lines: dict[str, set[int]] = {}
    for call in ordered:
        if call["name"] != "grep" or call["status"] != "completed":
            continue
        for line in (call.get("output") or "").splitlines():
            match = _GREP_LINE.match(line.strip())
            if match:
                path = relative(match.group("path"))
                if path and not any(char.isspace() for char in path):
                    search_lines.setdefault(path, set()).add(int(match.group("line")))

    known_paths = set(reads) | set(search_lines)

    def resolve_path(value: str) -> str:
        value = relative(value)
        if not value:
            return value
        return match_reference_path(value, known_paths) or value

    reference_counts = {"confirmed": 0, "searchSeen": 0, "fileReadNotRange": 0,
                        "unseen": 0, "pathOnly": 0}
    format_counts = {"absolute": 0, "unrooted": 0, "duplicated": 0,
                     "unresolved": 0, "malformed": 0}
    workspace_prefix = f"{workspace}/" if workspace else ""

    def read_covers(ranges, start: int, end: int) -> bool:
        cursor = start
        for low, high in sorted((int(a), int(b)) for a, b in ranges):
            if high < cursor:
                continue
            if low > cursor:
                return False
            cursor = high + 1
            if cursor > end:
                return True
        return False

    def path_flags(raw: str) -> set[str]:
        text = str(raw or "")
        flags = set()
        if (text.startswith("/") or text.startswith(
                ("private/", "tmp/", "Users/", "var/", "home/", "opt/", "root/",
                 "workspaces/"))
                or "/workspaces/" in text):
            flags.add("absolute")
            prefixes = tuple(prefix for prefix in (
                workspace_prefix, workspace_prefix.lstrip("/")) if prefix)
            if prefixes and not text.startswith(prefixes):
                flags.add("unrooted")
        if any(text.count(marker) > 1
               for marker in ("repos/", "requirements/", "knowledge/")):
            flags.add("duplicated")
        canonical = canonical_citation_path(text, workspace)
        if (canonical == text and "absolute" in flags) or canonical.startswith(
                ("private/", "tmp/", "Users/", "var/", "home/", "opt/", "root/",
                 "workspaces/")):
            flags.add("unresolved")
        return flags

    deduped: dict[tuple, dict] = {}
    for item in reference_items:
        raw_values = [str(value) for value in item.get("rawPaths") or [] if value]
        raw = str(item.get("path") or "")
        if not raw_values and raw:
            raw_values = [raw]
        path = resolve_path(str(item.get("matchedPath") or raw))
        line = item.get("line")
        try:
            start = int(line) if line is not None else None
            end = int(item.get("lineEnd") or line) if line is not None else None
        except (TypeError, ValueError):
            start = end = None
        key = (path, start, end)
        entry = deduped.setdefault(key, {
            "path": path, "line": line, "lineEnd": item.get("lineEnd"),
            "fileRead": bool(item.get("fileRead")), "confirmed": False,
            "rawPaths": set(),
        })
        entry["rawPaths"].update(raw_values)
        entry["fileRead"] = entry["fileRead"] or bool(item.get("fileRead"))
        entry["confirmed"] = entry["confirmed"] or bool(item.get("confirmed"))

    unseen_references = []
    for entry in deduped.values():
        path = entry["path"]
        line = entry.get("line")
        if line is None:
            reference_counts["pathOnly"] += 1
            continue
        try:
            start = int(line)
            end = int(entry.get("lineEnd") or line)
        except (TypeError, ValueError):
            reference_counts["pathOnly"] += 1
            continue
        flags = set()
        for raw in entry["rawPaths"]:
            flags |= path_flags(raw)
        for flag in flags:
            format_counts[flag] += 1
        if flags & {"unrooted", "duplicated", "unresolved"}:
            format_counts["malformed"] += 1
        read_entry = reads.get(path)
        if read_entry and read_covers(read_entry.get("ranges") or [], start, end):
            reference_counts["confirmed"] += 1
            continue
        if any(start <= number <= end for number in search_lines.get(path) or ()):
            reference_counts["searchSeen"] += 1
            continue
        if entry.get("fileRead") or path in reads:
            reference_counts["fileReadNotRange"] += 1
        else:
            reference_counts["unseen"] += 1
        unseen_references.append(f"{path}:{start}" if end == start
                                 else f"{path}:{start}-{end}")
    key_rows = []
    key_round = None
    for key in keys:
        matched = [entry for path, entry in reads.items() if _matches_key(path, key)]
        cited = [entry["path"] for entry in deduped.values()
                 if _matches_key(entry["path"], key)]
        key_rows.append({
            "key": key,
            "read": bool(matched),
            "firstRound": min((entry.get("firstRound") for entry in matched
                               if entry.get("firstRound") is not None), default=None),
            "ranges": [item for entry in matched for item in entry.get("ranges") or []][:12],
            "cited": bool(cited),
        })
    if key_rows and all(item["read"] for item in key_rows):
        key_round = max(item["firstRound"] or 0 for item in key_rows)
    last_key_round = max((item["firstRound"] or 0 for item in key_rows if item["read"]),
                         default=None)
    tools_after_keys = sum(1 for call in ordered if key_round is not None
                           and (call.get("round") or 0) > key_round)
    searches_after_keys = sum(1 for call in search_calls if key_round is not None
                              and (call.get("round") or 0) > key_round)
    tools_after_last_key = sum(1 for call in ordered if last_key_round is not None
                               and (call.get("round") or 0) > last_key_round)
    searches_after_last_key = sum(1 for call in search_calls if last_key_round is not None
                                  and (call.get("round") or 0) > last_key_round)
    stopping = {
        "postKeyToolCalls": 0,
        "postKeyNovelCalls": 0,
        "postKeyRedundantCalls": 0,
        "postKeyUnusedSearchCalls": 0,
        "postKeyUnclassifiedCalls": 0,
        "postKeyNewFiles": 0,
        "postKeyNewReadCalls": 0,
        "postKeyNovelCandidatePaths": 0,
        "postKeyUnusedCandidatePaths": 0,
    }
    seen_ranges: dict[str, list[list[int]]] = {}
    seen_paths: set[str] = set()
    read_paths_seen: set[str] = set()
    for call in ordered:
        after_key = (last_key_round is not None
                     and (call.get("round") or 0) > last_key_round)
        if call["status"] == "error":
            if after_key:
                stopping["postKeyToolCalls"] += 1
                stopping["postKeyUnclassifiedCalls"] += 1
            continue
        if call["status"] != "completed":
            continue
        name, arguments = call["name"], call["input"] or {}
        if name == "read":
            path = relative(arguments.get("path") or "")
            output = call.get("output") or ""
            if not path or not output:
                continue
            try:
                start = max(1, int(arguments.get("offset", 1)))
            except (TypeError, ValueError):
                start = 1
            lines = output.splitlines()
            ranges = [[start, start + len(lines) - 1]] if lines else []
            uncovered = [item for item in ranges
                         if not read_covers(seen_ranges.get(path) or [], *item)]
            if after_key:
                stopping["postKeyToolCalls"] += 1
                if uncovered:
                    stopping["postKeyNovelCalls"] += 1
                    stopping["postKeyNewReadCalls"] += 1
                    if path not in read_paths_seen:
                        stopping["postKeyNewFiles"] += 1
                else:
                    stopping["postKeyRedundantCalls"] += 1
            seen_ranges.setdefault(path, []).extend(ranges)
            seen_paths.add(path)
            read_paths_seen.add(path)
            continue
        if name in {"grep", "find"}:
            found = []
            for line in (call.get("output") or "").splitlines():
                candidate = _candidate_path(line, name, relative)
                if candidate:
                    found.append(candidate)
            new_paths = [path for path in found if path not in seen_paths]
            used_paths = [path for path in new_paths if path in read_paths]
            unused_paths = [path for path in new_paths if path not in read_paths]
            if after_key:
                stopping["postKeyToolCalls"] += 1
                if used_paths:
                    stopping["postKeyNovelCalls"] += 1
                    stopping["postKeyNovelCandidatePaths"] += len(used_paths)
                elif unused_paths:
                    stopping["postKeyUnusedSearchCalls"] += 1
                    stopping["postKeyUnusedCandidatePaths"] += len(unused_paths)
                else:
                    stopping["postKeyRedundantCalls"] += 1
            seen_paths.update(found)
            continue
        if after_key:
            stopping["postKeyToolCalls"] += 1
            stopping["postKeyUnclassifiedCalls"] += 1
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
            "total": len(deduped),
            "confirmed": reference_counts["confirmed"],
            "searchSeen": reference_counts["searchSeen"],
            "fileReadNotRange": reference_counts["fileReadNotRange"],
            "unseen": reference_counts["unseen"],
            "pathOnly": reference_counts["pathOnly"],
            "formatIssues": format_counts,
            "unresolvedFormat": format_counts["unresolved"],
            "unseenReferences": unseen_references[:30],
            "unconfirmed": [format_reference_text(entry) for entry in deduped.values()
                            if not entry["confirmed"]][:30],
            "readFiles": references.get("readFiles") or [],
            "semanticProof": references.get("semanticProof"),
        },
        "totalTokens": usage.get("totalTokens"),
        "cost": usage.get("cost"),
        "durationMs": run.get("durationMs"),
        "stopReason": usage.get("stopReason"),
        "keyFiles": key_rows,
        "keyFilesRead": sum(1 for item in key_rows if item["read"]),
        "keyFilesTotal": len(key_rows),
        "allKeyFilesRound": key_round,
        "lastKeyFileRound": last_key_round,
        "toolsAfterAllKeys": tools_after_keys,
        "searchesAfterAllKeys": searches_after_keys,
        "modelCallsAfterAllKeys": ((round_no - key_round)
                                   if round_no is not None and key_round is not None
                                   else None),
        "toolsAfterLastKey": tools_after_last_key,
        "searchesAfterLastKey": searches_after_last_key,
        "modelCallsAfterLastKey": ((round_no - last_key_round)
                                   if round_no is not None and last_key_round is not None
                                   else None),
        "stopping": stopping,
        "goalCoverage": ((usage.get("investigation") or {}).get("goalCoverage")
                          if isinstance(usage.get("investigation"), dict) else None),
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
            thinking: str | None, keys, output: Path) -> dict:
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
        metrics = analyze_trace(trace, keys) if trace else {}
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
        "reviewSchemaVersion": 2,
        "reviewStatus": "unreviewed",
        "caseId": case["id"], "mode": mode, "repeat": repeat,
        "question": case["question"], "checks": case.get("checks") or [],
        "checksReview": [{
            "index": index,
            "check": check,
            "verdict": None,
            "answerQuotes": [],
            "evidence": [],
            "reason": "",
        } for index, check in enumerate(case.get("checks") or [], 1)],
        "issues": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def summarize(batch: Path, keys_by_case: dict[str, list[str]] | None = None) -> dict:
    keys_by_case = keys_by_case or {}
    results = []
    for path in sorted(batch.glob("runs/*/result.json")):
        result = _load(path)
        trace = path.parent / "trace.json"
        if trace.is_file():
            result["metrics"] = analyze_trace(
                _load(trace), keys_by_case.get(result["caseId"]) or ())
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
    batch_label = (f"{batch.parent.name}/{batch.name}"
                   if batch.name == "raw-traces" else batch.name)
    lines = ["# Pi 真实问答评测", "",
             f"- 批次：`{batch_label}`",
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
        lines.extend(["| 组 | 重复 | 状态 | 模型 | 关键文件 | 末键轮次 | 末键后工具/搜索 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 候选/未读 | 空搜索 | 无读取搜索 | 重复调用 | Token | 耗时 s | 引用 总数/确认/搜索见/未交付 | 格式 绝对/缺段/重复/未解析/错误 |",
                      "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for row in rows:
            metrics = row.get("metrics") or {}
            refs = metrics.get("references") or {}
            key_text = (f"{metrics.get('keyFilesRead', 0)}/{metrics.get('keyFilesTotal', 0)}"
                        if metrics.get("keyFilesTotal") else "—")
            not_delivered = ((refs.get("fileReadNotRange") or 0) + (refs.get("unseen") or 0)
                             if refs else None)
            lines.append(
                f"| {row['mode']} | {row['repeat']} | {row['status']} | "
                f"{row.get('modelActual') or row.get('modelRequested') or '—'} | "
                f"{key_text} | {metrics.get('lastKeyFileRound', '—')} | "
                f"{metrics.get('toolsAfterLastKey', '—')}/{metrics.get('searchesAfterLastKey', '—')} | "
                f"{metrics.get('modelCalls', '—')} | {metrics.get('toolCalls', '—')} | "
                f"{metrics.get('uniqueReadFiles', '—')} | {metrics.get('searchCalls', '—')} | "
                f"{metrics.get('candidateFiles', '—')}/{metrics.get('unreadCandidateCount', '—')} | "
                f"{metrics.get('emptySearches', '—')} | {metrics.get('searchesWithoutRead', '—')} | "
                f"{metrics.get('repeatedToolCalls', '—')} | "
                f"{metrics.get('totalTokens', '—')} | {row.get('elapsedSeconds', '—')} | "
                f"{refs.get('total', '—')}/{refs.get('confirmed', '—')}/"
                f"{refs.get('searchSeen', '—')}/"
                f"{not_delivered if not_delivered is not None else '—'} | "
                f"{(refs.get('formatIssues') or {}).get('absolute', '—')}/"
                f"{(refs.get('formatIssues') or {}).get('unrooted', '—')}/"
                f"{(refs.get('formatIssues') or {}).get('duplicated', '—')}/"
                f"{(refs.get('formatIssues') or {}).get('unresolved', '—')}/"
                f"{(refs.get('formatIssues') or {}).get('malformed', '—')} |")
        lines.extend(["", "**评测检查项（仅评测器）**", ""])
        lines.extend(f"{index}. {check}" for index, check in enumerate(first.get("checks") or [], 1))
        for row in rows:
            lines.extend(["", f"### {row['mode']} / 第 {row['repeat']} 次", "",
                          f"- 状态：{row['status']}；Run：`{row.get('runId') or '—'}`；"
                          f"实际模型：{row.get('modelActual') or '—'}；Pi：{row.get('piVersion') or '—'}"])
            if row.get("error"):
                lines.append(f"- 错误：{row['errorType']}：{row['error']}")
            metrics = row.get("metrics") or {}
            missing = [item["key"] for item in metrics.get("keyFiles") or [] if not item["read"]]
            if missing:
                lines.append("- 未读取的关键文件：" + "、".join(missing))
            if metrics.get("allKeyFilesRound") is not None:
                lines.append(
                    f"- 关键文件齐备轮次：{metrics['allKeyFilesRound']}；之后工具 "
                    f"{metrics.get('toolsAfterAllKeys')} 次、搜索 "
                    f"{metrics.get('searchesAfterAllKeys')} 次、模型调用 "
                    f"{metrics.get('modelCallsAfterAllKeys')} 次")
            elif metrics.get("lastKeyFileRound") is not None:
                lines.append(
                    f"- 最后关键文件轮次：{metrics['lastKeyFileRound']}；之后工具 "
                    f"{metrics.get('toolsAfterLastKey')} 次、搜索 "
                    f"{metrics.get('searchesAfterLastKey')} 次、模型调用 "
                    f"{metrics.get('modelCallsAfterLastKey')} 次")
            stopping = metrics.get("stopping") or {}
            classified = ((stopping.get("postKeyNovelCalls") or 0)
                          + (stopping.get("postKeyUnusedSearchCalls") or 0)
                          + (stopping.get("postKeyRedundantCalls") or 0))
            if classified:
                lines.append(
                    f"- 末键后证据：可判定工具 {classified} 次（新增 "
                    f"{stopping.get('postKeyNovelCalls')}／新候选未读 "
                    f"{stopping.get('postKeyUnusedSearchCalls')}／未新增 "
                    f"{stopping.get('postKeyRedundantCalls')}），其中新读文件 "
                    f"{stopping.get('postKeyNewFiles')}、新行区间读取 "
                    f"{stopping.get('postKeyNewReadCalls')}、被读取的新候选路径 "
                    f"{stopping.get('postKeyNovelCandidatePaths')}、未读的新候选路径 "
                    f"{stopping.get('postKeyUnusedCandidatePaths')}；ls/codemode 等未分类 "
                    f"{stopping.get('postKeyUnclassifiedCalls')} 次")
            coverage = metrics.get("goalCoverage")
            if isinstance(coverage, dict):
                if coverage.get("total"):
                    complete = coverage.get("complete")
                    lines.append(
                        f"- 目标覆盖：{coverage['total']} 项；已核实 "
                        f"{coverage.get('verified', 0)}、部分 {coverage.get('partial', 0)}、"
                        f"未核实 {coverage.get('unverified', 0)}；模型声明完整："
                        f"{'是' if complete else '否' if complete is False else '未声明'}")
                else:
                    lines.append("- 目标覆盖：模型未声明目标清单")
            refs = metrics.get("references") or {}
            issues = refs.get("formatIssues") or {}
            if refs.get("total"):
                lines.append(
                    f"- 引用（去重）：{refs.get('total')} 条；读取确认 "
                    f"{refs.get('confirmed')}、搜索可见 {refs.get('searchSeen')}、"
                    f"读取文件但行区间未交付 {refs.get('fileReadNotRange')}、"
                    f"未出现 {refs.get('unseen')}、无行号 {refs.get('pathOnly')}；"
                    f"绝对临时路径 {issues.get('absolute', 0)} 条（缺段 "
                    f"{issues.get('unrooted', 0)}、重复根 {issues.get('duplicated', 0)}）、"
                    f"无法归一化 {issues.get('unresolved', 0)} 条；"
                    f"其中格式错误 {issues.get('malformed', 0)} 条")
            if refs.get("unseenReferences"):
                lines.append("- 未交付引用（读取原文与搜索输出均未出现）：" + "、".join(
                    str(item) for item in refs["unseenReferences"][:10]))
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


def check_resume(output: Path, expected: dict) -> None:
    """Validate that an existing batch can be continued with the same inputs."""
    path = output / "batch.json"
    if not path.is_file():
        raise ValueError("--resume 需要批次目录中的 batch.json")
    stored = _load(path)
    mismatched = [key for key, value in expected.items() if stored.get(key) != value]
    if mismatched:
        raise ValueError("--resume 配置与已有批次不一致：" + "、".join(mismatched))


VERDICTS = {"pass": "通过", "partial": "部分通过", "fail": "未通过"}


def check_review(review: dict, checks: list[str], answer: str | None = None) -> list[dict]:
    """Validate review structure only.

    This checks schema version, item/check alignment, verdict vocabulary, quote
    presence in the supplied answer text, and that evidence looks like project
    relative paths. It does not open evidence files or Trace records, so it
    cannot confirm that cited lines exist or that the quoted source supports
    the verdict. The verdict itself remains a manual judgement.
    """
    if review.get("reviewSchemaVersion") != 2:
        raise ValueError("复核文件 reviewSchemaVersion 必须为 2")
    items = review.get("checksReview") or []
    if len(items) != len(checks):
        raise ValueError(f"复核项数量 {len(items)} 与检查项数量 {len(checks)} 不一致")
    for index, (item, check) in enumerate(zip(items, checks), 1):
        if item.get("index") not in (None, index):
            raise ValueError(f"第 {index} 个复核项编号不一致")
        if item.get("check") and item["check"] != check:
            raise ValueError(f"第 {index} 个复核项与题库检查项不一致")
        if item.get("verdict") not in VERDICTS:
            raise ValueError(f"第 {index} 个检查项未判定，只能是 pass/partial/fail")
        quotes = [str(value).strip() for value in item.get("answerQuotes") or []
                  if str(value).strip()]
        evidence = [str(value).strip() for value in item.get("evidence") or []
                    if str(value).strip()]
        if item["verdict"] in {"pass", "partial"} and not quotes:
            raise ValueError(f"第 {index} 个检查项缺少答案原句")
        if item["verdict"] in {"pass", "partial"} and not evidence:
            raise ValueError(f"第 {index} 个检查项缺少源码/资料依据")
        if any(value.startswith("/") for value in evidence):
            raise ValueError(f"第 {index} 个检查项的证据必须是工程相对路径")
        if answer is not None:
            normalized = str(answer).replace("\r\n", "\n")
            for quote in quotes:
                if quote not in normalized:
                    raise ValueError(
                        f"第 {index} 个检查项的答案原句未在 answer.md 中找到：{quote[:40]}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"第 {index} 个检查项缺少说明")
    return items


def _review_markdown(result: dict, items: list[dict]) -> str:
    counts = {key: sum(1 for item in items if item["verdict"] == key) for key in VERDICTS}
    lines = [
        f"# 逐项检查复核：{result['caseId']} / {result['mode']} / r{result['repeat']}",
        "",
        f"- Run：`{result.get('runId') or '—'}`；模型：{result.get('modelActual') or '—'}；"
        f"状态：{result.get('status') or '—'}",
        f"- 结论：通过 {counts['pass']}、部分通过 {counts['partial']}、"
        f"未通过 {counts['fail']}",
        "",
    ]
    for index, item in enumerate(items, 1):
        lines.extend([
            f"## {index}. {item['check']}",
            "",
            f"**结论：{VERDICTS[item['verdict']]}**",
            "",
            "答案原句：",
            *[f"> {quote}" for quote in item.get("answerQuotes") or []],
            "",
            "源码/资料依据：",
            *[f"- {value}" for value in item.get("evidence") or []],
            "",
            f"说明：{item.get('reason')}",
            "",
        ])
    issues = result.get("issues") or []
    if issues:
        lines.extend(["## 其它问题", "", *[f"- {issue}" for issue in issues], ""])
    return "\n".join(lines)


def _batch_review_markdown(rows: list[tuple[dict, list[dict]]]) -> str:
    lines = ["# Pi 逐项检查复核汇总", "",
             "| 案例 | 模式 | 重复 | 通过 | 部分通过 | 未通过 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for result, items in rows:
        counts = {key: sum(1 for item in items if item["verdict"] == key)
                  for key in VERDICTS}
        lines.append(f"| {result['caseId']} | {result['mode']} | {result['repeat']} | "
                     f"{counts['pass']} | {counts['partial']} | {counts['fail']} |")
    lines.append("")
    for result, items in rows:
        lines.extend([f"## {result['caseId']} / {result['mode']} / r{result['repeat']}", ""])
        for index, item in enumerate(items, 1):
            quote = " … ".join(item.get("answerQuotes") or [])
            evidence = "；".join(item.get("evidence") or [])
            lines.append(f"{index}. **{VERDICTS[item['verdict']]}** — {item['check']}")
            lines.append(f"   - 答案：{quote or '—'}")
            lines.append(f"   - 依据：{evidence or '—'}")
            lines.append(f"   - 说明：{item.get('reason')}")
        lines.append("")
    return "\n".join(lines)


def command_review(args) -> int:
    batch = args.batch.expanduser().resolve()
    rows = []
    for folder in sorted(batch.glob("runs/*")):
        result_path = folder / "result.json"
        if not result_path.is_file():
            continue
        result = _load(result_path)
        review_path = folder / "review.json"
        if not review_path.is_file():
            if args.allow_unreviewed:
                continue
            raise ValueError(f"缺少已完成的 review.json：{folder}")
        review = _load(review_path)
        items = check_review(review, result.get("checks") or [],
                             answer=result.get("answer") or "")
        result["issues"] = review.get("issues") or []
        (folder / "checks.md").write_text(_review_markdown(result, items), encoding="utf-8")
        rows.append((result, items))
    if not rows:
        raise ValueError("没有可生成复核报告的运行")
    (batch / "checks.md").write_text(_batch_review_markdown(rows), encoding="utf-8")
    print(f"已生成：{batch / 'checks.md'}")
    return 0


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
    keys_by_case = load_keys(args.keys_file)
    output = args.output.expanduser().resolve()
    plan = [(case, mode, repeat) for case in cases for mode in modes
            for repeat in range(1, args.repeats + 1)]
    if args.plan:
        print(json.dumps({"projectId": project_id, "runs": [
            f"{case['id']}/{mode}/r{repeat}" for case, mode, repeat in plan]},
            ensure_ascii=False, indent=2))
        return 0
    if output.exists() and any(output.iterdir()):
        if not args.resume:
            raise ValueError(f"输出目录非空，请保留原批次并使用新目录：{output}")
        check_resume(output, {
            "projectConfig": str(project_config), "projectId": project_id,
            "cases": [case["id"] for case in cases], "modes": modes,
            "repeats": args.repeats, "requestedModel": args.model,
            "timeoutSeconds": args.timeout, "thinking": args.thinking,
        })
    output.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        _write(output / "batch.json", {
            "suites": suites, "projectConfig": str(project_config),
            "projectId": project_id, "cases": [case["id"] for case in cases],
            "modes": modes, "repeats": args.repeats, "requestedModel": args.model,
            "timeoutSeconds": args.timeout, "thinking": args.thinking,
            "keysFile": str(args.keys_file) if args.keys_file else None,
            "startedAt": _now(),
        })
    for index, (case, mode, repeat) in enumerate(plan, 1):
        folder = output / "runs" / f"{_safe_name(case['id'])}-{_safe_name(mode)}-r{repeat}"
        if args.resume and (folder / "result.json").is_file():
            print(f"[{index}/{len(plan)}] 已完成，跳过：{folder.name}", flush=True)
            continue
        if folder.is_dir() and any(folder.iterdir()):
            raise ValueError(f"运行目录存在未完成文件，请人工确认后再续跑：{folder}")
        folder.mkdir(parents=True, exist_ok=True)
        print(f"[{index}/{len(plan)}] {case['id']} mode={mode} repeat={repeat}", flush=True)
        record = run_one(case, mode=mode, repeat=repeat, model=args.model,
                         project_config=project_config, project_id=project_id,
                         timeout=args.timeout, thinking=args.thinking,
                         keys=keys_by_case.get(case["id"]) or (), output=folder)
        print(json.dumps({"status": record["status"], "runId": record["runId"],
                          "actualModel": record["modelActual"],
                          "answerChars": len(record.get("answer") or ""),
                          "elapsedSeconds": record["elapsedSeconds"]},
                         ensure_ascii=False), flush=True)
    summarize(output, keys_by_case)
    print(f"批次完成：{output / 'summary.md'}", flush=True)
    return 0


def command_report(args) -> int:
    batch = args.batch.expanduser().resolve()
    if not (batch / "runs").is_dir():
        raise ValueError(f"批次目录没有 runs：{batch}")
    summarize(batch, load_keys(args.keys_file))
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
    run.add_argument("--modes", default="none", help="逗号分隔，如 none,backbone,ast")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--model", default=os.environ.get("BUSINESS_CODE_PI_MODEL") or None)
    run.add_argument("--project-config", type=Path)
    run.add_argument("--keys-file", type=Path,
                     help="人工指定的关键文件映射，只用于评测统计")
    run.add_argument("--timeout", type=float, default=900)
    run.add_argument("--thinking")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--resume", action="store_true",
                     help="续跑已有批次，只执行缺少 result.json 的运行")
    run.add_argument("--plan", action="store_true", help="只打印运行计划，不调用模型")
    report = sub.add_parser("report", help="从已有 runs 重新生成汇总")
    report.add_argument("--batch", type=Path, required=True)
    report.add_argument("--keys-file", type=Path)
    review = sub.add_parser(
        "review",
        help="校验逐项复核并生成 checks.md",
        description="校验 review.json 的结构、判定、答案原句和工程相对证据路径，"
                    "并生成 checks.md。不核对证据文件/行是否存在，也不判断语义正确性。")
    review.add_argument("--batch", type=Path, required=True)
    review.add_argument("--allow-unreviewed", action="store_true",
                        help="跳过还没有 review.json 的运行")
    args = parser.parse_args()
    actions = {"run": command_run, "report": command_report, "review": command_review}
    try:
        return actions[args.action](args)
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Offline diagnosis for Direct runtime trace archives.

Reads existing ``query-traces/<runId>.json`` files, reconstructs the model/tool
timeline, and reports key-file coverage, repository expansion, search candidates
that were never read, and the evidence available when budget finalization began.

This script never calls a model and never writes into a project workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _events(trace):
    return trace.get("runtimeEvents") or trace.get("events") or []


def _tool_calls(trace):
    current_round = None
    calls = {}
    ordered = []
    for event in _events(trace):
        kind = event.get("eventType")
        payload = event.get("payload") or {}
        if kind == "model":
            current_round = payload.get("attempt") or current_round
            continue
        if kind != "tool":
            continue
        call_id = str(payload.get("id") or "")
        if not call_id:
            continue
        entry = calls.get(call_id)
        if entry is None:
            entry = {
                "id": call_id,
                "round": current_round,
                "name": payload.get("name"),
                "input": payload.get("input") or {},
                "status": payload.get("status"),
                "elapsedMs": event.get("elapsedMs"),
                "output": None,
            }
            calls[call_id] = entry
            ordered.append(entry)
        else:
            entry["status"] = payload.get("status") or entry["status"]
            entry["elapsedMs"] = event.get("elapsedMs")
        if payload.get("output") is not None:
            entry["output"] = payload["output"]
    return ordered


def _json_output(tool):
    try:
        data = json.loads(tool.get("output") or "")
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and not data.get("error") else None


def _read_payload(tool):
    data = _json_output(tool)
    if not data or not data.get("path"):
        return None
    return {
        "path": data["path"],
        "lineStart": data.get("lineStart"),
        "lineEnd": data.get("lineEnd"),
        "totalLines": data.get("totalLines"),
        "contentChars": len(str(data.get("content") or "")),
    }


def _candidate_paths(tool):
    name = tool.get("name")
    data = _json_output(tool) or {}
    if name == "Grep":
        mode = str((tool.get("input") or {}).get("output_mode") or "matches")
        if mode == "files":
            return [item.get("path") for item in data.get("files") or [] if item.get("path")]
        return [item.get("path") for item in data.get("matches") or [] if item.get("path")]
    if name == "Glob":
        return [path for path in data.get("paths") or [] if path]
    return []


def _repo(path):
    parts = str(path or "").split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "repos" else ""


def _matches_key(path, key):
    path = str(path or "")
    key = str(key or "").strip()
    if not key:
        return False
    return path == key or path.endswith("/" + key) or path.rsplit("/", 1)[-1] == key


def analyze(trace, keys=()):
    run = trace.get("run") or {}
    usage = run.get("usage") or {}
    references = usage.get("references") or []
    cited = {}
    for reference in references:
        matched = reference.get("matchedPath") or reference.get("path")
        if matched:
            cited[str(matched)] = cited.get(str(matched), 0) + 1

    tools = _tool_calls(trace)
    model_calls = sorted(trace.get("calls") or [], key=lambda item: item.get("attempt") or 0)
    reads = {}
    candidates = []
    search_calls = []
    repo_rounds = {}
    for tool in tools:
        if tool.get("status") not in {"completed", "error"}:
            continue
        round_no = tool.get("round")
        name = tool.get("name")
        arguments = tool.get("input") or {}
        touched = []
        if name == "Read":
            payload = _read_payload(tool)
            if payload:
                path = payload["path"]
                touched.append(path)
                entry = reads.setdefault(path, {
                    "path": path, "firstRound": round_no, "rounds": [],
                    "ranges": [], "contentChars": 0})
                if round_no not in entry["rounds"]:
                    entry["rounds"].append(round_no)
                start, end = payload.get("lineStart"), payload.get("lineEnd")
                if isinstance(start, int) and isinstance(end, int) and end >= start:
                    entry["ranges"].append([start, end])
                entry["contentChars"] += payload.get("contentChars") or 0
        elif name in {"Grep", "Glob"}:
            paths = [path for path in _candidate_paths(tool) if path]
            touched.extend(paths)
            search_calls.append({
                "round": round_no,
                "name": name,
                "pattern": arguments.get("pattern"),
                "path": arguments.get("path") or "",
                "glob": arguments.get("glob") or "",
                "mode": arguments.get("output_mode") or ("matches" if name == "Grep" else "files"),
                "candidates": len(paths),
                "truncated": bool((_json_output(tool) or {}).get("truncated")),
            })
            for path in paths:
                candidates.append({"path": path, "round": round_no, "tool": name})
        for path in touched:
            repo = _repo(path)
            if not repo:
                continue
            entry = repo_rounds.setdefault(repo, {"repo": repo, "firstRound": round_no, "calls": 0})
            entry["calls"] += 1
            if entry["firstRound"] is None or (round_no or 9999) < (entry["firstRound"] or 9999):
                entry["firstRound"] = round_no

    key_rows = []
    for key in keys:
        matching = [entry for path, entry in reads.items() if _matches_key(path, key)]
        matching_cited = [path for path in cited if _matches_key(path, key)]
        key_rows.append({
            "key": key,
            "read": bool(matching),
            "firstRound": min((entry["firstRound"] for entry in matching
                               if entry["firstRound"] is not None), default=None),
            "ranges": [item for entry in matching for item in entry["ranges"]][:20],
            "cited": bool(matching_cited),
            "citedPaths": matching_cited,
        })

    candidate_paths = []
    seen = set()
    for candidate in candidates:
        path = candidate["path"]
        if path in seen:
            continue
        seen.add(path)
        candidate_paths.append(candidate)
    unread = [item for item in candidate_paths if item["path"] not in reads]

    final_call = model_calls[-1] if model_calls else {}
    checkpoint_calls = [call for call in model_calls if call.get("phase") == "checkpoint"]
    checkpoint_round = checkpoint_calls[0].get("attempt") if checkpoint_calls else None
    post_tools = [tool for tool in tools
                  if checkpoint_round is not None and tool.get("round") is not None
                  and tool["round"] > checkpoint_round
                  and tool.get("status") in {"completed", "error"}]
    post_read_paths = sorted({
        entry["path"] for entry in reads.values()
        if checkpoint_round is not None and entry["firstRound"] is not None
        and entry["firstRound"] > checkpoint_round})
    post_new_repos = sorted(
        repo for repo, entry in repo_rounds.items()
        if checkpoint_round is not None and entry["firstRound"] is not None
        and entry["firstRound"] > checkpoint_round)
    reads_before_final = [
        path for path, entry in reads.items()
        if entry["firstRound"] is not None
        and (final_call.get("attempt") is None or entry["firstRound"] < final_call.get("attempt"))
    ]
    key_missing_at_final = [row["key"] for row in key_rows if not row["read"]]
    total_calls = len(model_calls)
    half = ((total_calls + 1) // 2) if total_calls else None
    reads_after_half = [
        path for path, entry in reads.items()
        if half is not None and entry["firstRound"] is not None and entry["firstRound"] > half
    ]

    cited_repos = {_repo(path) for path in cited if _repo(path)}
    key_repos = {_repo(path) for path in reads if _repo(path)}
    suspicious = []
    for repo, entry in sorted(repo_rounds.items()):
        if repo in key_repos and repo in cited_repos:
            continue
        if repo not in cited_repos:
            suspicious.append({
                "repo": repo,
                "firstRound": entry["firstRound"],
                "calls": entry["calls"],
                "reason": "该仓库的读取未出现在最终引用中",
            })

    return {
        "runId": trace.get("runId") or run.get("runId"),
        "question": run.get("question"),
        "status": trace.get("status") or run.get("status"),
        "stopReason": trace.get("stopReason") or usage.get("stopReason"),
        "answerChars": len(str(run.get("answer") or "")),
        "modelCalls": usage.get("modelCalls") if usage.get("modelCalls") is not None else total_calls,
        "toolCalls": usage.get("toolCalls"),
        "durationMs": run.get("durationMs"),
        "modelRounds": [
            {
                "attempt": call.get("attempt"),
                "phase": call.get("phase"),
                "status": call.get("status"),
                "durationMs": call.get("durationMs"),
                "tools": [tool.get("name") for tool in _response_tools(call)],
                "outputChars": len(str((_response_text(call) or ""))),
            }
            for call in model_calls
        ],
        "tools": [
            {
                "round": tool.get("round"),
                "name": tool.get("name"),
                "input": tool.get("input"),
                "status": tool.get("status"),
            }
            for tool in tools
            if tool.get("status") in {"completed", "error"}
        ],
        "searchCalls": search_calls,
        "reads": [
            {
                "path": entry["path"],
                "firstRound": entry["firstRound"],
                "rounds": entry["rounds"],
                "ranges": entry["ranges"][:20],
                "contentChars": entry["contentChars"],
                "cited": cited.get(entry["path"], 0),
            }
            for entry in sorted(reads.values(), key=lambda item: (item["firstRound"] or 9999, item["path"]))
        ],
        "keyFiles": key_rows,
        "repos": [repo_rounds[repo] for repo in sorted(repo_rounds)],
        "candidates": {
            "total": len(candidate_paths),
            "read": len(candidate_paths) - len(unread),
            "unread": unread[:30],
        },
        "finalization": {
            "lastAttempt": final_call.get("attempt"),
            "lastPhase": final_call.get("phase"),
            "lastStatus": final_call.get("status"),
            "readsBeforeFinal": len(reads_before_final),
            "halfOfCalls": half,
            "readsFirstReadAfterHalf": len(reads_after_half),
            "keyFilesMissingAtFinal": key_missing_at_final,
            "citedFiles": len(cited),
        },
        "checkpoint": {
            "round": checkpoint_round,
            "postToolCalls": len(post_tools),
            "postReadFiles": len(post_read_paths),
            "postNewRepos": post_new_repos,
            "answerAfterCheckpoint": bool(
                checkpoint_round is not None
                and final_call.get("attempt") is not None
                and final_call.get("attempt") > checkpoint_round),
        },
        "references": {
            "total": len(references),
            "confirmed": sum(1 for reference in references if reference.get("confirmed")),
        },
        "suspiciousExpansions": suspicious,
    }


def _response_tools(call):
    for response in call.get("responses") or []:
        return response.get("toolCalls") or []
    return []


def _response_text(call):
    for response in call.get("responses") or []:
        return response.get("content") or ""
    return ""


def render(result):
    lines = [
        f"# Trace 诊断：{result['runId']}",
        "",
        f"- 状态：{result['status']}；stopReason：{result['stopReason'] or '无'}；"
        f"模型调用：{result['modelCalls']}；工具调用：{result['toolCalls']}；"
        f"耗时：{result['durationMs']} ms；答案：{result['answerChars']} 字",
        f"- 问题：{result['question'] or ''}",
        f"- 引用确认：{result['references']['confirmed']}/{result['references']['total']}",
        "",
        "## 轮次时序",
        "",
        "| 轮次 | 阶段 | 工具 | 输出字数 | 耗时 ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for round_row in result["modelRounds"]:
        lines.append(
            f"| {round_row['attempt']} | {round_row['phase']} | "
            f"{', '.join(round_row['tools']) or '—'} | {round_row['outputChars']} | "
            f"{round_row['durationMs'] or ''} |")
    lines.extend(["", "## 关键文件", "",
                  "| 文件 | 是否 Read | 首次轮次 | 已读区间 | 是否引用 |",
                  "| --- | --- | --- | --- | --- |"])
    for row in result["keyFiles"]:
        ranges = ", ".join(f"{start}-{end}" for start, end in row["ranges"]) or "—"
        lines.append(
            f"| {row['key']} | {'是' if row['read'] else '否'} | "
            f"{row['firstRound'] or '—'} | {ranges} | {'是' if row['cited'] else '否'} |")
    lines.extend(["", "## 仓库扩展", "",
                  "| 仓库 | 首轮 | 工具次数 |", "| --- | --- | --- |"])
    for repo in result["repos"]:
        lines.append(f"| {repo['repo']} | {repo['firstRound'] or '—'} | {repo['calls']} |")
    lines.extend(["", "## 搜索与核实差异", "",
                  f"- 候选文件（Grep/Glob 返回）：{result['candidates']['total']}；"
                  f"实际 Read：{result['candidates']['read']}；未读：{result['candidates']['total'] - result['candidates']['read']}"])
    for item in result["candidates"]["unread"][:12]:
        lines.append(f"- 未读候选：{item['path']}（{item['tool']}，第 {item['round']} 轮）")
    lines.extend(["", "## 搜索调用", "",
                  "| 轮次 | 工具 | 模式 | 范围 | 候选数 |",
                  "| --- | --- | --- | --- | --- |"])
    for call in result["searchCalls"]:
        scope = call["path"] or "(全授权范围)"
        lines.append(
            f"| {call['round']} | {call['name']} | {call['mode']} | "
            f"{scope}{' / ' + call['pattern'] if call['pattern'] else ''} | {call['candidates']} |")
    final = result["finalization"]
    lines.extend(["", "## 预算/收尾证据", "",
                  f"- 最后调用：第 {final['lastAttempt']} 轮，phase={final['lastPhase']}，"
                  f"status={final['lastStatus']}",
                  f"- 收尾前已读文件：{final['readsBeforeFinal']}；"
                  f"调用进度后半程首次读取的文件：{final['readsFirstReadAfterHalf']}",
                  f"- 收尾时未读取的关键文件：{', '.join(final['keyFilesMissingAtFinal']) or '无'}",
                  f"- 已引用文件：{final['citedFiles']}"])
    checkpoint = result.get("checkpoint") or {}
    if checkpoint.get("round"):
        lines.extend(["", "## 检查点后扩展", "",
                      f"- 检查点轮次：{checkpoint['round']}；"
                      f"之后工具调用：{checkpoint['postToolCalls']}；"
                      f"新读文件：{checkpoint['postReadFiles']}；"
                      f"新增仓库：{', '.join(checkpoint['postNewRepos']) or '无'}",
                      f"- 检查点后是否继续调查到答案："
                      f"{'是' if checkpoint['answerAfterCheckpoint'] else '否'}"])
    if result["suspiciousExpansions"]:
        lines.extend(["", "## 可疑范围扩展（启发式）", ""])
        for item in result["suspiciousExpansions"]:
            lines.append(
                f"- {item['repo']}：首轮 {item['firstRound']}，{item['calls']} 次工具调用；{item['reason']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", nargs="+", help="trace JSON 文件或包含 trace 的目录")
    parser.add_argument("--keys", default="", help="关键文件，逗号分隔；可用完整路径或文件名")
    parser.add_argument("--json", action="store_true", help="输出分析 JSON")
    parser.add_argument("--output", default="", help="写入 Markdown/JSON 文件")
    args = parser.parse_args()

    keys = [value.strip() for value in args.keys.split(",") if value.strip()]
    paths = []
    for value in args.trace:
        path = Path(value)
        paths.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    results = []
    for path in paths:
        trace = _load(path)
        result = analyze(trace, keys)
        result["tracePath"] = str(path)
        results.append(result)
    if args.json:
        text = json.dumps(results, ensure_ascii=False, indent=2)
    else:
        text = "\n\n---\n\n".join(render(result) for result in results)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()

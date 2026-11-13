"""Run a bounded real Agent question through QueryService, preserving its trace."""

from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from business_code_agent.platform.env import load_env_file
from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.service import QueryService
from business_code_agent.storage.schema import connect


def main():
    load_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--index-root", type=Path)
    parser.add_argument("--contract-root", type=Path, help="只读已显式准备的契约索引，不生成 AST")
    parser.add_argument("--code-index-db", type=Path,
                        help="显式绑定一个代码符号索引库（如 AST 版本的 index.db），注册 SearchSymbols 精确检索工具")
    parser.add_argument("--mode", choices=("combined", "ast", "none", "backbone"), default="backbone",
                        help="资料模式；结构化检索对照用 combined 并配合 --code-index-db")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-seconds", type=float, default=300, help="仅本次有界评测的总时长上限")
    parser.add_argument("--answer-mode", choices=("plain", "facts", "contracts"), default="plain")
    parser.add_argument("--workflow-mode", choices=("basic", "tracked", "coordinated"), default="basic",
                        help="basic 保留历史对照；coordinated 使用程序检索调度")
    parser.add_argument("--trace-output", type=Path, help="保存逐轮公开请求／响应，默认与结果同名的 .trace.json")
    args = parser.parse_args()
    trace_output = args.trace_output or args.output.with_suffix(".trace.json")
    if args.output.exists() or trace_output.exists():
        parser.error("结果已存在，请使用新输出文件，保留原始批次")
    # Only the question is passed to the Agent. Evidence labels and expected
    # answers remain outside the runtime and its authorized source mounts.
    question = next(
        c["question"]
        for c in json.loads(args.suite.read_text(encoding="utf-8"))["cases"]
        if c["id"] == args.case
    )
    context = ProjectContext.legacy(
        args.project_config.parent / ".data/knowledge.db", args.project_config
    )
    env = dict(os.environ)
    env.update(
        {
            "BUSINESS_CODE_MODEL_ENABLE_THINKING": "false",
            "BUSINESS_CODE_AGENT_OUTPUT_TOKENS": "2048",
            "BUSINESS_CODE_MODEL_TEMPERATURE": "0",
        }
    )
    with tempfile.TemporaryDirectory(prefix="langchain-eval-") as directory:
        db_path = Path(directory) / "query.db"
        with (
            sqlite3.connect(context.db_path) as source,
            sqlite3.connect(db_path) as target,
        ):
            source.backup(target)
        db = connect(db_path)
        runtime = LangChainRuntime(
            environment=env, max_calls=args.max_calls, max_seconds=args.max_seconds, index_root=args.index_root,
            project_id=context.project_id, answer_mode=args.answer_mode,
            workflow_mode=args.workflow_mode,
            contract_root=args.contract_root,
            code_search_db=args.code_index_db,
        )
        service = QueryService(
            db,
            db_path=str(db_path),
            project_config=args.project_config,
            workspace_root=Path(directory) / "workspaces",
            runtime=runtime,
            project_id=context.project_id,
        )
        try:
            result = service.query(question, mode=args.mode)
        except Exception as exc:
            result = {
                "status": "failed",
                "errorType": type(exc).__name__,
                "error": str(exc)[:300],
            }
        traces = list((Path(directory) / "workspaces").rglob("*.trace.json"))
        if traces:
            trace_output.parent.mkdir(parents=True, exist_ok=True)
            source_trace = max(traces, key=lambda p: p.stat().st_mtime_ns)
            shutil.copyfile(source_trace, trace_output)
            result.setdefault("usage", {})["tracePath"] = str(trace_output)
        result["experiment"] = {
            "caseId": args.case,
            "retrieval": "hybrid" if args.index_root else "files",
            "answerMode": args.answer_mode,
            "workflowMode": args.workflow_mode,
            "mode": args.mode,
            "codeIndexDb": str(args.code_index_db) if args.code_index_db else None,
            "contractIndex": str(args.contract_root) if args.contract_root else None,
            "thinking": False,
            "maxCalls": args.max_calls,
            "maxSeconds": args.max_seconds,
            "maxOutputTokensPerCall": 2048,
            "requestedModel": env.get("BUSINESS_CODE_MODEL_NAME"),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: result.get(k) for k in ["status", "answer", "usage", "errorType"]},
                ensure_ascii=False,
            )
        )
        db.close()


if __name__ == "__main__":
    main()

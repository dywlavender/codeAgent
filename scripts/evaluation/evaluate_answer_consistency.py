"""Freeze actual delivered Agent evidence, then replay one no-tool answer call."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import ToolMessage

from business_code_agent.platform.env import load_env_file
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.langchain_agent.answering import RULES, complete_response, request_messages
from business_code_agent.query.langchain_agent.context import answer_evidence
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog


def prepare(source_run, suite, case_id, project_id):
    if source_run["projectId"] != project_id or suite["projectId"] != project_id:
        raise ValueError("原运行或题库不属于指定工程")
    if source_run["experiment"]["caseId"] != case_id:
        raise ValueError("原运行的问题与指定案例不同")
    # Only select the original question. Never pass evidence labels, rubrics or
    # old answers to the answer model, including in the conversation background.
    question = next(c["question"] for c in suite["cases"] if c["id"] == case_id)
    history = "用户：" + question
    messages = [
        ToolMessage(content=p["output"], name=p["name"], tool_call_id=p["id"])
        for event in source_run["events"]
        if event["eventType"] == "tool"
        and (p := event["payload"])["status"] == "completed"
    ]
    # Reproduce v2's final original-text selection exactly, rather than selecting
    # different facts or following source files that may have changed since then.
    evidence, omitted = answer_evidence(messages, 64000 - len(history) - len(question) - 1200)
    catalog = EvidenceCatalog.from_text(project_id, evidence)
    delivered_rows = len(re.findall(r"^\d+: ", evidence, flags=re.M))
    if not catalog.sources or sum(len(s.lines) for s in catalog.sources) != delivered_rows:
        raise ValueError("原文行解析不完整，不能开始固定证据复验")
    incoming = request_messages(question, catalog, history)
    input_chars = sum(len(m.content) for m in incoming)
    if input_chars > 64000:
        raise ValueError("复验输入超过 64,000 字符，不能静默删减原文")
    return {
        "protocolVersion": 1,
        "projectId": project_id,
        "caseId": case_id,
        "question": question,
        "history": history,
        "systemRules": RULES,
        "evidenceText": evidence,
        "evidenceChars": len(evidence),
        "inputChars": input_chars,
        "sourceBlocks": len(catalog.sources),
        "deliveredRows": delivered_rows,
        "omittedOriginalBlocks": omitted,
        "retrieval": source_run["experiment"]["retrieval"],
        "requestedModel": source_run["experiment"]["requestedModel"],
    }


def replay(pack, project_id):
    if pack["projectId"] != project_id:
        raise ValueError("冻结资料不属于指定工程")
    if pack.get("systemRules", RULES) != RULES:
        raise ValueError("答题提示在冻结后已变化，需重新准备新批次")
    load_env_file()
    env = dict(os.environ)
    if env.get("BUSINESS_CODE_MODEL_NAME") != pack["requestedModel"]:
        raise ValueError("模型与冻结协议不一致，请先核对本轮验证范围")
    env.update({
        "BUSINESS_CODE_MODEL_ENABLE_THINKING": "false",
        "BUSINESS_CODE_AGENT_OUTPUT_TOKENS": "2048",
        "BUSINESS_CODE_MODEL_TEMPERATURE": "0",
        "BUSINESS_CODE_MODEL_TIMEOUT": "120",
    })
    catalog = EvidenceCatalog.from_text(project_id, pack["evidenceText"])
    incoming = request_messages(pack["question"], catalog, pack["history"])
    if sum(len(m.content) for m in incoming) != pack["inputChars"]:
        raise ValueError("答题协议在冻结后已变化，需重新准备而不是直接复验")
    # One request, zero retry, no tools, no source mounts and no index operation.
    chat = LangChainRuntime(environment=env, max_calls=1).chat_model()
    started = time.monotonic()
    try:
        message = chat.invoke(incoming)
    except Exception as exc:
        return {
            "projectId": project_id, "caseId": pack["caseId"],
            "status": "failed", "modelCallsAttempted": 1,
            "requestedModel": env["BUSINESS_CODE_MODEL_NAME"],
            "durationSeconds": round(time.monotonic() - started, 3),
            "errorType": type(exc).__name__, "error": str(exc)[:300],
        }
    answer, audit = complete_response(message, catalog)
    return {
        "projectId": project_id,
        "caseId": pack["caseId"],
        "status": "completed",
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "experiment": {
            "type": "frozen-evidence-answer-consistency",
            "retrieval": pack["retrieval"],
            "requestedModel": env["BUSINESS_CODE_MODEL_NAME"],
            "modelCalls": 1,
            "maxOutputTokens": 2048,
            "thinking": False,
            "temperature": 0,
            "retries": 0,
            "inputChars": pack["inputChars"],
            "sourceBlocks": pack["sourceBlocks"],
            "deliveredRows": pack["deliveredRows"],
            "baselineAnswerProvided": False,
            "referenceLabelsProvided": False,
        },
        "durationSeconds": round(time.monotonic() - started, 3),
        "answer": answer,
        "rawModelResponse": message.text,
        "usage": message.usage_metadata,
        "reportedModel": message.response_metadata.get("model_name"),
        "finishReason": message.response_metadata.get("finish_reason"),
        "verification": audit,
    }


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    freeze = sub.add_parser("prepare", help="只冻结已有原文，不调用模型")
    freeze.add_argument("--source-run", type=Path, required=True)
    freeze.add_argument("--suite", type=Path, required=True)
    freeze.add_argument("--case", required=True)
    freeze.add_argument("--project-id", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run", help="按冻结协议调用一次模型，不重试")
    run.add_argument("--pack", type=Path, required=True)
    run.add_argument("--project-id", required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果已存在，请保留原记录并使用新文件")
    if args.action == "prepare":
        result = prepare(
            json.loads(args.source_run.read_text(encoding="utf-8")),
            json.loads(args.suite.read_text(encoding="utf-8")),
            args.case,
            args.project_id,
        )
        result["sourceRun"] = str(args.source_run)
    else:
        result = replay(json.loads(args.pack.read_text(encoding="utf-8")), args.project_id)
        result["frozenPack"] = str(args.pack)
    write_new(args.output, result)
    print(json.dumps({k: result.get(k) for k in (
        ("projectId", "caseId", "inputChars", "sourceBlocks", "deliveredRows")
        if args.action == "prepare" else
        ("status", "answer", "reportedModel", "usage", "durationSeconds", "finishReason", "errorType")
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()

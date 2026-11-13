"""Reassemble a saved contract answer locally, without tools or model calls."""

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage
from business_code_agent.query.langchain_agent.contract_answer import complete_response
from business_code_agent.query.langchain_agent.evidence import EvidenceCatalog


def replay(trace):
    call = next(c for c in reversed(trace["calls"]) if c["phase"] == "answer")
    original = call["messages"][-1]["content"]
    encoded = original.split("已核对契约及原文：\n", 1)[1]
    bundles, _ = json.JSONDecoder().raw_decode(encoded)
    rendered = original.split("已读取的补充原文：\n", 1)[1]
    ids = re.findall(r"^\[(S\d+)\]\[", rendered, flags=re.M)
    text = re.sub(r"^\[S\d+\]\[([^\]]+)\] ", r"[\1] ", rendered, flags=re.M)
    catalog = EvidenceCatalog.from_text(trace["projectId"], text)
    if ids != [s.id for s in catalog.sources]:
        raise ValueError("冻结原文的来源编号无法按原顺序恢复")
    response = call["responses"][-1]
    if response.get("toolCalls"):
        raise ValueError("冻结收尾响应含工具调用")
    answer, audit = complete_response(AIMessage(content=response["content"]), bundles, catalog, question=trace["question"])
    return {"type": "offline_contract_reassembly", "projectId": trace["projectId"],
            "rawModelResponse": response["content"], "answer": answer,
            "verification": audit, "additionalModelCalls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("结果已存在，请保留原始结果并使用新的输出路径")
    result = replay(json.loads(args.trace.read_text(encoding="utf-8")))
    result["sourceTrace"] = str(args.trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "additionalModelCalls": 0, "verification": {
        key: result["verification"][key] for key in ("issues", "businessEvidenceCount", "businessEvidenceLines")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Small real-model code-only/baseline comparison; no automatic quality score."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business_code_agent.query_agent.claude_runtime import ClaudeCodeRuntime
from business_code_agent.query_agent.workspace import WorkspaceManager

QUESTIONS = [
    ("coupon", "H5 查询优惠券后，渠道后端调用中台哪个接口？给出完整调用链和关键参数。",
     "GET /api/withdraw/coupons → channel controller/service → LoanMiddleClient → GET /middle/withdraw/coupons → MiddleWithdrawController → WithdrawQueryService.queryCoupons；customerId、amount；查询返回固定列表。"),
    ("guarantee", "提款时可选择的担保公司由哪个系统提供？H5 到具体实现的调用链是什么？",
     "中台提供；H5 /api/withdraw/guarantee-companies，经渠道转发到 /middle/withdraw/guarantee-companies，由 WithdrawQueryService.queryGuaranteeCompanies 返回固定两家。"),
    ("sequence", "提款前有哪些业务步骤，渠道和中台分别承担什么职责？请区分业务流程与代码实际行为。",
     "额度、优惠券、还款方式、还款日、担保公司、银行卡、签合同、纳税授权、提交；渠道接入转发，中台实现；H5 是独立按钮，不是自动串行编排。"),
    ("preconditions", "如果没签合同或没做纳税授权，当前代码一定能阻止提款吗？具体检查了什么，又没有检查什么？",
     "中台检查 contractId/taxAuthorizationId 非 null；不验证真实性、有效性或状态，空字符串也不被该条件拒绝；H5 写死 ID，不足以证明真实流程完成。"),
    ("repayment", "可选还款方式与还款日分别是什么？最终提交时后端会校验值属于这些选项吗？",
     "EQUAL_INSTALLMENT、INTEREST_FIRST；5/10/15/20/25；apply 只检 repaymentMethod/repaymentDay 非 null，无枚举成员校验。"),
    ("enforcement", "系统是否强制用户依次完成额度、优惠券、还款方式、还款日、担保公司、银行卡查询后，才能签约和提款？",
     "不强制全部顺序；H5 独立点击函数；申请只查合同/税授权/银行卡/还款方式/还款日非 null；业务推荐顺序不等于代码执行约束。"),
]


class EvaluationRuntime(ClaudeCodeRuntime):
    def build_command(self, question, *, workspace, session_id=None):
        command = super().build_command(question, workspace=workspace)
        # Keep the production read-only tools and dontAsk permissions. Avoid
        # personal project/MCP config and persisted sessions contaminating runs.
        command[1:1] = ["--no-session-persistence", "--setting-sources", "user",
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                        "--max-budget-usd", "1"]
        return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    questions = QUESTIONS[args.start:args.start + args.limit]
    (output / "rubric.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="baseline-evaluation-") as folder:
        temporary = Path(folder)
        def run(job):
            number, arm, item = job
            key, question, _ = item
            base = temporary / f"{number}-{arm}"
            base.mkdir()
            repositories = []
            for name in ("channel-h5", "channel-service", "loan-middle"):
                target = base / "code" / name
                shutil.copytree(root / "examples" / name, target,
                                ignore=shutil.ignore_patterns(".git", "node_modules", "target"))
                repositories.append({"id": name, "localPath": str(target)})
            if arm == "baseline":
                shutil.copytree(root / "knowledge" / "baseline", base / "baseline")
            config = base / "project.json"
            config.write_text(json.dumps({"project": {"id": "evaluation", "name": "贷款提款"},
                "repositories": repositories, "knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            workspace = WorkspaceManager(project_config=config).ensure()
            started = time.monotonic()
            value = {"questionId": key, "arm": arm, "question": question}
            try:
                result = EvaluationRuntime(timeout_seconds=180).ask(
                    question + "\n请用中文简洁回答（不超过600字），给出关键文件及行号。仅依据当前工作区明确列出的资料，不访问其他目录。",
                    workspace=str(workspace.path))
                value.update(result.to_dict())
                tools = {event["payload"]["id"]: event["payload"] for event in result.events
                         if event["eventType"] == "tool"}
                value["toolCalls"] = len(tools)
                value["toolErrors"] = sum(t.get("status") == "error" for t in tools.values())
            except Exception as exc:
                value.update(status="failed", error=str(exc))
            value["elapsedSeconds"] = round(time.monotonic() - started, 2)
            (output / f"{key}-{arm}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: value.get(k) for k in ("questionId", "arm", "status", "elapsedSeconds", "toolCalls", "error")}, ensure_ascii=False), flush=True)
            return value
        jobs = [(number, arm, item) for number, item in enumerate(questions)
                for arm in (("code_only", "baseline") if number % 2 == 0 else ("baseline", "code_only"))]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, jobs))
        (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

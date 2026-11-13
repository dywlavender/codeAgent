"""Prepare isolated UI records with a local fake model; never builds an index."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.api.server import make_server
from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.langchain_agent import LangChainRuntime
from business_code_agent.query.service import QueryService, QueryRuntimeError
from business_code_agent.storage.schema import connect


class LocalModel(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


class FailedModel(LocalModel):
    async def _agenerate(self, *args, **kwargs):
        raise RuntimeError("本地模拟的模型请求失败，用于验证失败轨迹回看。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--contract-index", required=True, help="已经准备的当前工程索引；此脚本不生成索引")
    parser.add_argument("--port", type=int, default=18083)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    context = ProjectContext.legacy(db_path, args.project_config)
    db = connect(str(db_path))
    records = []
    for failure in (False, True):
        model = FailedModel(responses=[]) if failure else LocalModel(responses=[AIMessage(
            content="本轮使用本地模拟模型，验证了程序检索和公开调用轨迹的保存。已取得的源码见工具记录，业务答题质量未在本轮评估。",
            response_metadata={"model_name": "local-ui-fixture", "finish_reason": "stop"})])
        runtime = LangChainRuntime(model=model, environment={"BUSINESS_CODE_MODEL_NAME": "local-ui-fixture"},
                                  contract_root=args.contract_index, project_id=context.project_id)
        service = QueryService(db, db_path=str(db_path), project_config=args.project_config,
            project_id=context.project_id, workspace_root=context.workspace_root,
            trace_root=context.data_root / "query-traces", runtime=runtime)
        question = ("失败轨迹回看" if failure else "检索调度与轨迹回看") + "：GET /api/drawdowns/{channelRefNo} 为什么状态一直显示处理中，页面应该如何区分？"
        try:
            records.append(service.query(question))
        except QueryRuntimeError:
            records.append(service.list_runs(1)[0])
    (db_path.parent / "preview-runs.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    db.close()
    print(json.dumps({"projectId": context.project_id, "runs": [r.get("runId") or r["id"] for r in records],
                      "model": "local-ui-fixture", "externalModelCalls": 0, "port": args.port}, ensure_ascii=False), flush=True)
    if not args.prepare_only:
        os.environ["BUSINESS_CODE_MODEL_ENABLED"] = "false"
        make_server(str(db_path), port=args.port, project_config=args.project_config).serve_forever()


if __name__ == "__main__":
    main()

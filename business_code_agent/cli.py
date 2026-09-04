from __future__ import annotations

import argparse
import logging
import os
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .code_intelligence import JavaIndexer
from .env import EnvFileError, load_env_file
from .requirements import RequirementBuilder
from .schema import connect


def _configure_logging() -> None:
    level_name = os.environ.get("BUSINESS_CODE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init_db = sub.add_parser("init-db", help="创建或迁移知识库，不导入任何示例数据")
    init_db.add_argument("--db", required=True)
    ingest = sub.add_parser("ingest-repo")
    ingest.add_argument("repo")
    ingest.add_argument("--db", required=True)
    ingest.add_argument("--repository-id", default="repo-main")
    sync = sub.add_parser("sync-project", help="按项目配置同步 Git 仓库并增量索引")
    sync.add_argument("--config", required=True)
    sync.add_argument("--db", required=True)
    sync.add_argument("--offline", action="store_true", help="只索引配置中的包内源码，不调用 Git 或网络")
    baseline = sub.add_parser("baseline-refresh", help="导入自然语言业务基线和调查入口")
    baseline.add_argument("--config", required=True)
    baseline.add_argument("--db", required=True)
    baseline.add_argument(
        "--parser", choices=("model", "markdown"), default="model",
        help="结构化方式：默认使用配置的大模型；markdown 仅在明确需要本地解析时使用",
    )
    req = sub.add_parser("ingest-requirement")
    req.add_argument("path")
    req.add_argument("--db", required=True)
    req.add_argument("--id", dest="requirement_id")
    req.add_argument("--title")
    req.add_argument("--version", default="1")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--db", required=True)
    ask.add_argument("--project-config")
    discover = sub.add_parser("discover")
    discover.add_argument("--db", required=True)
    discover.add_argument("--limit", type=int)
    discover.add_argument("--include-declared", action="store_true")
    discover.add_argument("--json", action="store_true")
    explain = sub.add_parser("explain")
    explain.add_argument("field")
    explain.add_argument("--db", required=True)
    explain.add_argument("--json", action="store_true")
    analyze = sub.add_parser("analyze-repo")
    analyze.add_argument("repo")
    analyze.add_argument("--db")
    analyze.add_argument("--repository-id", default="repo-main")
    analyze.add_argument("--limit", type=int, default=50)
    analyze.add_argument("--include-declared", action="store_true")
    analyze.add_argument("--json", action="store_true")
    req_import = sub.add_parser("requirement-import")
    req_import.add_argument("path")
    req_import.add_argument("--db", required=True)
    req_import.add_argument("--id", dest="requirement_id")
    req_import.add_argument("--title")
    req_enrich = sub.add_parser("requirement-enrich")
    req_enrich.add_argument("requirement_id")
    req_enrich.add_argument("--db", required=True)
    req_get = sub.add_parser("requirement-get")
    req_get.add_argument("requirement_id")
    req_get.add_argument("--db", required=True)
    req_get.add_argument("--version", type=int)
    req_chunk = sub.add_parser("requirement-chunk")
    req_chunk.add_argument("requirement_id")
    req_chunk.add_argument("chunk_id")
    req_chunk.add_argument("--db", required=True)
    req_search = sub.add_parser("requirement-search")
    req_search.add_argument("query")
    req_search.add_argument("--db", required=True)
    req_history = sub.add_parser("requirement-history")
    req_history.add_argument("requirement_id")
    req_history.add_argument("--db", required=True)
    req_changes = sub.add_parser("requirement-changes")
    req_changes.add_argument("requirement_id")
    req_changes.add_argument("--db", required=True)
    serve_requirement = sub.add_parser("serve-requirement")
    serve_requirement.add_argument("--db", required=True)
    serve_requirement.add_argument("--host", default="127.0.0.1")
    serve_requirement.add_argument("--port", type=int, default=8081)
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--db", required=True)
    query.add_argument("--project-config", help="项目和仓库配置，用于生成 Claude 工作区")
    query_run = sub.add_parser("query-run")
    query_run.add_argument("run_id")
    query_run.add_argument("--db", required=True)
    serve_query = sub.add_parser("serve-query")
    serve_query.add_argument("--db", required=True)
    serve_query.add_argument("--host", default="127.0.0.1")
    serve_query.add_argument("--port", type=int, default=8082)
    serve_query.add_argument("--project-config", help="项目和仓库配置，用于生成 Claude 工作区")
    args = parser.parse_args()
    try:
        load_env_file()
    except EnvFileError as exc:
        parser.error(str(exc))
    if args.command == "serve-query":
        _warn_runtime_configuration()
    if args.command == "init-db":
        db = connect(args.db)
        db.close()
        print(json.dumps({"db": str(Path(args.db).resolve()), "mode": "EMPTY", "initialized": True}, ensure_ascii=False))
    elif args.command == "ingest-repo":
        print(JavaIndexer(connect(args.db)).ingest(args.repo, args.repository_id))
    elif args.command == "sync-project":
        from .project_sync import ProjectSyncError, sync_project
        try:
            result = sync_project(args.config, args.db, offline=args.offline)
        except ProjectSyncError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "baseline-refresh":
        from .knowledge_update.baseline_service import BaselineKnowledgeService
        service = BaselineKnowledgeService(connect(args.db), project_config=args.config)
        print(json.dumps(service.refresh(parser=args.parser), ensure_ascii=False, indent=2))
    elif args.command == "ingest-requirement":
        builder = RequirementBuilder(connect(args.db))
        if Path(args.path).suffix.lower() == ".json":
            print(builder.ingest(args.path))
        elif args.requirement_id:
            print(builder.ingest_text(
                args.path, args.requirement_id, title=args.title, version=args.version,
            ))
        else:
            parser.error("非 JSON 需求原文必须提供 --id")
    elif args.command == "ask":
        from .query_agent.service import QueryService
        db = connect(args.db)
        try:
            print(json.dumps(QueryService(db, db_path=args.db, project_config=args.project_config).query(args.question), ensure_ascii=False, indent=2))
        finally:
            db.close()
    elif args.command == "discover":
        from .discovery import RepositoryAnalyzer
        result = RepositoryAnalyzer(connect(args.db)).discover(args.limit, include_declared=args.include_declared)
        _print_analysis(result, args.json)
    elif args.command == "explain":
        from .discovery import RepositoryAnalyzer
        result = RepositoryAnalyzer(connect(args.db)).explain(args.field)
        _print_explanation(result, args.json)
    elif args.command == "analyze-repo":
        from .discovery import RepositoryAnalyzer
        if args.db:
            db = connect(args.db)
            JavaIndexer(db).ingest(args.repo, args.repository_id)
            result = RepositoryAnalyzer(db).discover(args.limit, include_declared=args.include_declared)
        else:
            with tempfile.TemporaryDirectory() as folder:
                db = connect(str(Path(folder) / "analysis.db"))
                JavaIndexer(db).ingest(args.repo, args.repository_id)
                result = RepositoryAnalyzer(db).discover(args.limit, include_declared=args.include_declared)
        _print_analysis(result, args.json)
    elif args.command.startswith("requirement-"):
        from .requirement.service import RequirementService
        service = RequirementService(connect(args.db))
        if args.command == "requirement-import":
            result = service.import_document(args.path, requirement_id=args.requirement_id, title=args.title)
        elif args.command == "requirement-enrich":
            result = service.enrich(args.requirement_id)
        elif args.command == "requirement-get":
            result = service.get(args.requirement_id, args.version)
        elif args.command == "requirement-chunk":
            result = service.read_chunk(args.requirement_id, args.chunk_id)
        elif args.command == "requirement-search":
            result = service.search(args.query)
        elif args.command == "requirement-history":
            result = service.history(args.requirement_id)
        else:
            result = service.changes(args.requirement_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "serve-requirement":
        from .requirement.api import serve
        serve(args.db, args.host, args.port)
    elif args.command == "query":
        from .query_agent.service import QueryService
        db = connect(args.db)
        try:
            print(json.dumps(
                QueryService(
                    db, db_path=args.db, project_config=args.project_config,
                ).query(args.question),
                ensure_ascii=False,
                indent=2,
            ))
        finally:
            db.close()
    elif args.command == "query-run":
        from .query_agent.service import QueryService
        print(json.dumps(QueryService(connect(args.db), db_path=args.db).get_run(args.run_id), ensure_ascii=False, indent=2))
    elif args.command == "serve-query":
        from .query_agent.api import serve
        serve(args.db, args.host, args.port, project_config=args.project_config)


def _print_analysis(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    overview = result["overview"]
    print(f"仓库：{overview['files']} 文件，{overview['symbols']} Symbol，{overview['facts']} Fact，解析错误 {overview['parse_errors']}")
    print("模块：" + "，".join(f"{name}({count})" for name, count in overview["modules"].items()))
    knowledge = overview["knowledge_sources"]
    print(f"知识源：需求 {knowledge['requirements']}，业务知识 {knowledge['business_knowledge']}（均为可选增强）")
    print("字段候选：")
    if not result["candidates"]:
        print("  暂无读写/校验活动；可加 --include-declared 查看纯声明字段")
    for item in result["candidates"]:
        print(
            f"  {item['field']}: {item['classification']} | "
            f"写 {len(item['writes'])} / 读 {len(item['reads'])} / 校验 {len(item['checks'])} | "
            f"模块 {','.join(item['modules']) or '-'}"
        )
    if result["declared_only_omitted"]:
        print(f"已隐藏 {result['declared_only_omitted']} 个纯声明字段；使用 --include-declared 查看")


def _print_explanation(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"字段：{result['field']} | 证据等级：{result['explanation_level']} | {result['classification']}")
    for label, key in (("写入", "writes"), ("读取", "reads"), ("校验", "checks")):
        print(f"{label}：")
        for item in result[key]:
            print(f"  {item['qualified_name']} — {item['locator']}:{item['line_start']} [{item['fact_type']}]")
        if not result[key]:
            print("  未发现")
    if result["requirements"]:
        print("需求：" + "，".join(result["requirements"]))
    if result["business_knowledge"]:
        print("业务知识：" + "，".join(result["business_knowledge"]))
    for gap in result["gaps"]:
        print(f"缺口：{gap}")


def _warn_runtime_configuration() -> None:
    """Make a missing Claude Code executable visible at server startup."""
    command = os.environ.get("CLAUDE_CODE_COMMAND", "claude").strip() or "claude"
    if not shutil.which(command) and not Path(command).is_file():
        print(
            f"[WARN] 未找到 Claude Code CLI（{command}）；查询请求会失败。"
            "请安装 Claude Code，或设置 CLAUDE_CODE_COMMAND。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()

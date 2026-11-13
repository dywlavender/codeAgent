"""Explicitly prepare/update a project's Spring contract SQLite index."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from business_code_agent.platform.projects import ProjectContext, ProjectRegistry
from business_code_agent.evaluation.retrieval import project_sources
from business_code_agent.codeintel.contracts.index import prepare_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path)
    parser.add_argument("--project-registry", type=Path)
    parser.add_argument("--project-id")
    parser.add_argument("--repository-id", action="append")
    parser.add_argument("--index-root", type=Path)
    parser.add_argument("--reparse", action="store_true", help="显式重解析已有原文，用于解析器升级")
    args = parser.parse_args()
    if args.project_registry:
        context = ProjectRegistry(args.project_registry).get(args.project_id)
    elif args.project_config:
        context = ProjectContext.legacy(args.project_config.parent / ".data/knowledge.db", args.project_config)
    else:
        parser.error("需要工程配置或注册表")
    result = prepare_index(args.index_root or context.data_root / "contract-index", project_sources(context),
                           json.loads(context.config_path.read_text(encoding="utf-8-sig")), context.project_id, args.repository_id, reparse=args.reparse)
    print(json.dumps({"projectId": result["projectId"], "version": result["version"], "files": len(result["files"]),
                      "entries": len(result["entries"]), "parseIssues": result["issues"],
                      "update": {**result["update"], "changes": len(result["update"]["changes"])}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

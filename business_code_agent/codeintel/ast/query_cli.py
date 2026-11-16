"""Private stdin transport for the Pi extension's read-only AST queries."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .material import AstMaterial
from .reader import AstQueryError, AstReader, AstRepository


def main() -> None:
    scope = json.loads(os.environ["CODEAGENT_PI_AST_SCOPE"])
    version_id = scope["versionId"]
    repositories = tuple(AstRepository(item["repositoryId"], Path(item["root"]), item["mount"])
                         for item in scope["repositories"])
    material = AstMaterial(project_config=scope["projectConfig"], data_root=scope["astRoot"])
    reader = AstReader(material, version_id, repositories, project_id=scope["projectId"])
    operation = sys.argv[1]
    params = json.load(sys.stdin)
    try:
        if operation == "find_symbol":
            result = reader.find_symbol(params.get("query"), params.get("repositoryId"), params.get("limit", 20))
        elif operation == "get_symbol":
            result = reader.get_symbol(params.get("symbolId"))
        elif operation == "find_relations":
            result = reader.find_relations(params.get("symbolId"), params.get("relationType"),
                                           params.get("direction", "outgoing"), params.get("limit", 20))
        else:
            raise AstQueryError("invalid_input", "不支持的结构查询")
    except AstQueryError as exc:
        result = {"status": exc.status, "kind": "navigation", "versionId": version_id,
                  "count": 0, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()

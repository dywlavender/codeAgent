from __future__ import annotations

import json
import hmac
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..schema import connect
from .service import QueryService


def make_server(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8082,
    *,
    project_config: str | None = None,
):
    static_root = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    admin_access = _admin_access(project_config)
    if host not in {"127.0.0.1", "localhost", "::1"} and not admin_access["token"]:
        raise ValueError("non-loopback binding requires admin.apiTokenEnv and its environment variable")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path):
            if not path.is_file():
                self._json(404, {"error": "workbench is not built; run npm run build in frontend"})
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache" if path.name == "index.html" else "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def _require_admin(self):
            expected = admin_access["token"]
            if expected is None:
                return True
            authorization = self.headers.get("Authorization", "")
            supplied = authorization.removeprefix("Bearer ").strip()
            if supplied and hmac.compare_digest(supplied, expected):
                return True
            self._json(401, {"error": "administrator credential required"})
            return False

        def do_POST(self):
            service = None
            try:
                path = urlparse(self.path).path
                if path == "/api/knowledge-admin/proposals/generate":
                    if not self._require_admin():
                        return
                    from ..knowledge_update.service import KnowledgeAdminService
                    service = KnowledgeAdminService(connect(db_path), project_config=project_config)
                    body = self._body()
                    self._json(201, service.generate(body, created_by=admin_access["name"]))
                    return
                if path == "/api/knowledge-admin/generate":
                    if not self._require_admin():
                        return
                    from ..knowledge_update.service import KnowledgeAdminService
                    service = KnowledgeAdminService(connect(db_path), project_config=project_config)
                    body = self._body()
                    self._json(201, service.generate_from_sources(body, created_by=admin_access["name"]))
                    return
                review_match = re.fullmatch(r"/api/knowledge-admin/proposals/([^/]+)/review", path)
                if review_match:
                    if not self._require_admin():
                        return
                    from ..knowledge_update.service import KnowledgeAdminService
                    service = KnowledgeAdminService(connect(db_path), project_config=project_config)
                    body = self._body()
                    self._json(200, service.review(
                        review_match.group(1), str(body.get("action") or ""),
                        reviewer=admin_access["name"],
                        comment=str(body.get("comment") or ""),
                    ))
                    return
                feedback_match = re.fullmatch(r"/api/query/([^/]+)/feedback", path)
                if feedback_match:
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    body = self._body()
                    self._json(201, service.record_feedback(
                        feedback_match.group(1), str(body.get("rating") or ""), str(body.get("comment") or "")
                    ))
                    return
                if path != "/api/query":
                    self._json(404, {"error": "not found"})
                    return
                service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                body = self._body()
                self._json(200, service.query(
                    body["question"], conversation_id=body.get("conversationId"), history=body.get("history", []),
                ))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": "internal query service error", "type": type(exc).__name__})
            finally:
                if service:
                    service.db.close()

        def do_GET(self):
            service = None
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/workspace":
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    self._json(200, {**service.workspace_summary(), "adminAuthRequired": admin_access["token"] is not None})
                    return
                if parsed.path == "/api/runs":
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    limit = parse_qs(parsed.query).get("limit", ["30"])[0]
                    self._json(200, {"items": service.list_runs(int(limit))})
                    return
                if parsed.path == "/api/code/search":
                    from ..tools import EvidenceTools
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json(200, {"items": EvidenceTools(service.db).search_code(query or "_", 50) if query else []})
                    return
                if parsed.path == "/api/functions":
                    from ..business_tools import BusinessTools
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json(200, {"items": BusinessTools(service.db).search_business_knowledge(query)})
                    return
                if parsed.path == "/api/requirements":
                    from ..requirement.service import RequirementService
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json(200, {"items": RequirementService(service.db).search(query)})
                    return
                if parsed.path == "/api/knowledge-graph":
                    from ..knowledge_graph import KnowledgeGraphService
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    params = parse_qs(parsed.query)
                    query = params.get("q", [""])[0]
                    node_type = params.get("type", [""])[0]
                    self._json(200, KnowledgeGraphService(service.db).search(query, node_type))
                    return
                if parsed.path in {
                    "/api/knowledge-admin/pending",
                    "/api/knowledge-admin/functions",
                    "/api/knowledge-admin/proposals",
                }:
                    if not self._require_admin():
                        return
                    from ..knowledge_update.service import KnowledgeAdminService
                    service = KnowledgeAdminService(connect(db_path), project_config=project_config)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    if parsed.path.endswith("/pending"):
                        items = service.list_pending(query)
                    elif parsed.path.endswith("/functions"):
                        items = service.list_functions(query)
                    else:
                        items = service.list_proposals(query)
                    self._json(200, {"items": items})
                    return
                proposal_match = re.fullmatch(r"/api/knowledge-admin/proposals/([^/]+)", parsed.path)
                if proposal_match:
                    if not self._require_admin():
                        return
                    from ..knowledge_update.service import KnowledgeAdminService
                    service = KnowledgeAdminService(connect(db_path), project_config=project_config)
                    self._json(200, service.get_proposal(proposal_match.group(1)))
                    return
                match = re.fullmatch(r"/api/query/([^/]+)", parsed.path)
                if match:
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    self._json(200, service.get_run(match.group(1)))
                    return
                symbol_match = re.fullmatch(r"/api/code/symbol/([^/]+)", parsed.path)
                if symbol_match:
                    from ..tools import EvidenceTools
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    tools = EvidenceTools(service.db)
                    detail = tools.read_source(symbol_match.group(1))
                    detail["relations"] = tools.get_symbol_relations(symbol_match.group(1))
                    self._json(200, detail)
                    return
                if not parsed.path.startswith("/api/"):
                    relative = parsed.path.lstrip("/")
                    target = (static_root / relative).resolve() if relative else static_root / "index.html"
                    if static_root.resolve() not in target.parents or not target.is_file():
                        target = static_root / "index.html"
                    self._file(target)
                    return
                service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                self._json(404, {"error": "not found"})
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            finally:
                if service:
                    service.db.close()

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8082,
    *,
    project_config: str | None = None,
):
    make_server(db_path, host, port, project_config=project_config).serve_forever()


def _admin_access(project_config: str | None) -> dict:
    if not project_config:
        return {"token": None, "name": "local-admin"}
    path = Path(project_config)
    if not path.is_file():
        return {"token": None, "name": "local-admin"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    admin = payload.get("admin") or {}
    if not isinstance(admin, dict):
        raise ValueError("project admin configuration must be an object")
    variable = str(admin.get("apiTokenEnv") or "").strip()
    if not variable:
        return {"token": None, "name": str(admin.get("name") or "local-admin")}
    token = os.environ.get(variable)
    if not token:
        raise ValueError(f"administrator credential environment variable is not set: {variable}")
    return {"token": token, "name": str(admin.get("name") or "knowledge-admin")}

"""HTTP API for the Claude Code backed query workbench."""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..schema import connect
from .service import QueryRuntimeError, QueryService


logger = logging.getLogger(__name__)


def _user_facing_internal_error(exc: Exception) -> str:
    """Map runtime failures to short, actionable messages for the browser."""
    messages = []
    current = exc
    for _ in range(6):
        if current is None:
            break
        messages.append(str(current or ""))
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    detail = " ".join(messages).casefold()
    if "insufficient_quota" in detail or "quota exhausted" in detail or "free quota" in detail:
        return "模型调用失败：当前模型账号额度已用尽，请更换有额度的 API Key，或检查 Claude Code 登录状态。"
    if "thinking mode" in detail and "tool_choice" in detail:
        return "模型调用失败：当前模型的思考模式不支持工具调用，请关闭思考模式后重试；如使用旧兼容接口，可设置 BUSINESS_CODE_MODEL_THINKING=disabled。"
    if "sqlite objects created in a thread" in detail:
        return "查询服务失败：数据库连接发生跨线程使用。请重启工作台后重试。"
    if "accessdenied.unpurchased" in detail or "access to model denied" in detail or "model denied" in detail:
        return "模型调用失败：当前 API Key 未开通所选模型，请检查 Claude Code 的模型权限。"
    if "没有找到 claude code cli" in detail or "no such file or directory" in detail:
        return "查询服务未找到 Claude Code CLI，请先安装并确认 claude 命令在 PATH 中。"
    if "workspace" in detail and ("不存在" in detail or "cannot" in detail or "无法" in detail):
        return "查询服务无法准备项目工作区，请检查项目配置中的仓库和知识目录。"
    if "timeout" in detail or "timed out" in detail or "超时" in detail:
        return "模型调用失败：请求 Claude Code 超时，请检查网络或增大 CLAUDE_CODE_TIMEOUT。"
    if "api key" in detail or "unauthorized" in detail or "401" in detail or "authentication" in detail:
        return "模型调用失败：Claude Code 未通过认证，请运行 claude auth 或配置 API 凭据。"
    if "permission" in detail or "access denied" in detail:
        return "模型调用失败：Claude Code 没有访问当前工作区的权限，请检查登录状态和目录权限。"
    if isinstance(exc, QueryRuntimeError):
        return "查询服务失败：Claude Code 未能完成本次回答，请查看服务日志后重试。"
    return "查询服务失败，请查看服务日志后重试。"


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
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _start_sse(self):
            # Each query is a finite stream. Explicitly close the HTTP/1.0
            # response after the result so clients waiting for EOF do not hang.
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _sse(self, event: str, payload):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

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
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return value

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
            stream_started = False
            try:
                path = urlparse(self.path).path
                if path == "/api/knowledge/baselines/refresh":
                    if not self._require_admin():
                        return
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(connect(db_path), project_config=project_config)
                    body = self._body()
                    self._json(200, service.refresh(parser=str(body.get("parser") or "model")))
                    return

                feedback_match = re.fullmatch(r"/api/query/([^/]+)/feedback", path)
                if feedback_match:
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    body = self._body()
                    self._json(201, service.record_feedback(
                        feedback_match.group(1), str(body.get("rating") or ""), str(body.get("comment") or "")
                    ))
                    return

                if path not in {"/api/query", "/api/query/stream"}:
                    self._json(404, {"error": "not found"})
                    return

                body = self._body()
                question = body.get("question")
                conversation_id = body.get("conversationId")
                service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                if path == "/api/query/stream":
                    stream_started = True
                    self._start_sse()
                    try:
                        result = service.query(
                            question,
                            conversation_id=conversation_id,
                            event_callback=lambda event: self._sse("event", event),
                        )
                        self._sse("result", result)
                    except Exception as exc:
                        logger.exception("查询流内部错误: %s", type(exc).__name__)
                        try:
                            self._sse("error", {"error": _user_facing_internal_error(exc)})
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                    return

                self._json(200, service.query(question, conversation_id=conversation_id))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                if stream_started:
                    try:
                        self._sse("error", {"error": str(exc)})
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                else:
                    self._json(400, {"error": str(exc)})
            except Exception as exc:
                logger.exception("查询服务内部错误: %s", type(exc).__name__)
                if stream_started:
                    try:
                        self._sse("error", {"error": _user_facing_internal_error(exc)})
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                else:
                    self._json(500, {"error": _user_facing_internal_error(exc), "type": type(exc).__name__})
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
                if parsed.path == "/api/knowledge-graph":
                    from ..knowledge_graph import KnowledgeGraphService
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    params = parse_qs(parsed.query)
                    self._json(200, KnowledgeGraphService(service.db).search(params.get("q", [""])[0], params.get("type", [""])[0]))
                    return
                if parsed.path == "/api/knowledge/entities":
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(connect(db_path), project_config=project_config)
                    params = parse_qs(parsed.query)
                    query = params.get("q", [""])[0]
                    entity_type = params.get("type", [""])[0]
                    self._json(200, {"items": service.list_entities(query, entity_type), "relations": service.list_relations(query)})
                    return
                entity_match = re.fullmatch(r"/api/knowledge/entities/([^/]+)", parsed.path)
                if entity_match:
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(connect(db_path), project_config=project_config)
                    self._json(200, service.get_entity(entity_match.group(1)))
                    return
                relation_match = re.fullmatch(r"/api/knowledge/relations/([^/]+)", parsed.path)
                if relation_match:
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(connect(db_path), project_config=project_config)
                    self._json(200, service.get_relation(relation_match.group(1)))
                    return
                run_match = re.fullmatch(r"/api/query/([^/]+)", parsed.path)
                if run_match:
                    service = QueryService(connect(db_path), db_path=db_path, project_config=project_config)
                    self._json(200, service.get_run(run_match.group(1)))
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
                self._json(404, {"error": "not found"})
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            finally:
                if service:
                    service.db.close()

        def log_message(self, format, *args):
            logger.debug("HTTP %s - %s", self.address_string(), format % args)

    return ThreadingHTTPServer((host, port), Handler)


def serve(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8082,
    *,
    project_config: str | None = None,
):
    server = make_server(db_path, host, port, project_config=project_config)
    logger.info("工作台已启动: http://%s:%s/  (db=%s)", host, port, db_path)
    if project_config:
        logger.info("项目配置: %s", project_config)
    server.serve_forever()


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

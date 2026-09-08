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
from urllib.parse import parse_qs, unquote, urlparse

from ..schema import connect
from ..evaluation.service import EvaluationService
from ..evaluation.ast_service import AstService
from ..project_context import ProjectContext, ProjectRegistry, ProjectRegistryError
from .service import QueryBusyError, QueryRuntimeError, QueryService


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
    evaluation_service: EvaluationService | None = None,
    project_registry: ProjectRegistry | str | Path | None = None,
    project_id: str | None = None,
):
    static_root = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    registry = (project_registry if isinstance(project_registry, ProjectRegistry)
                else ProjectRegistry(project_registry) if project_registry else None)
    legacy_context = ProjectContext.legacy(db_path, project_config)
    if registry:
        try:
            default_context = registry.default(project_id)
        except ProjectRegistryError:
            default_context = None
    else:
        default_context = legacy_context
    admin_access = _admin_access(str(default_context.config_path) if default_context and default_context.config_path else project_config)
    if host not in {"127.0.0.1", "localhost", "::1"} and not admin_access["token"]:
        raise ValueError("non-loopback binding requires admin.apiTokenEnv and its environment variable")
    evaluation_services: dict[str, EvaluationService] = {}
    ast_services: dict[str, AstService] = {}
    admin_accesses: dict[str, dict] = {legacy_context.project_id: admin_access}
    if default_context:
        admin_accesses.setdefault(default_context.project_id, admin_access)

    def context_for(project_key: str | None) -> ProjectContext:
        if registry:
            return registry.get(project_key or (default_context.project_id if default_context else ""))
        if project_key and project_key != legacy_context.project_id:
            raise KeyError(project_key)
        return legacy_context

    def evaluation_for(context: ProjectContext) -> EvaluationService:
        if not context.registered and evaluation_service is not None:
            return evaluation_service
        key = context.project_id
        if key not in evaluation_services:
            evaluation_services[key] = EvaluationService(
                project_config=str(context.config_path) if context.config_path else None,
                data_root=context.evaluations_root,
                ast_data_root=context.ast_root,
                business_context_root=context.business_context_root if context.registered else None,
                code_map_root=context.code_map_root,
                requirements_root=context.requirements_root if context.registered else None,
            )
        return evaluation_services[key]

    def ast_for(context: ProjectContext) -> AstService:
        key = context.project_id
        if key not in ast_services:
            ast_services[key] = AstService(
                project_config=str(context.config_path) if context.config_path else None,
                data_root=context.ast_root,
            )
        return ast_services[key]

    class Handler(BaseHTTPRequestHandler):
        def _project_route(self, parsed):
            """Return the project-relative path and its immutable context.

            Registered deployments accept the explicit URL form
            ``/api/projects/<projectId>/...`` as well as the ``projectId``
            query/header form used by the existing browser client.  The URL
            form is canonical; the other two keep old links and SSE clients
            working while the UI migrates.
            """
            path = parsed.path
            if path in {"/api/projects", "/api/projects/"}:
                return "/api/projects", None
            project_match = re.fullmatch(r"/api/projects/([^/]+)(/.*)?", path)
            if project_match:
                project_key = unquote(project_match.group(1))
                if registry:
                    context = context_for(project_key)
                else:
                    if project_key != legacy_context.project_id:
                        raise ValueError("当前服务未启用工程注册表，且工程 ID 与兼容工程不一致")
                    context = legacy_context
                relative = project_match.group(2) or "/api/project"
                if relative == "/api":
                    relative = "/api/project"
                elif not relative.startswith("/api/"):
                    relative = f"/api{relative}"
                return relative, context
            if not path.startswith("/api/"):
                return path, None
            query_project = parse_qs(parsed.query).get("projectId", [None])[0]
            header_project = self.headers.get("X-Project-Id")
            project_key = query_project or header_project
            if registry and not project_key:
                raise ValueError("注册表模式的工程请求必须明确携带 projectId 或 X-Project-Id")
            return path, context_for(project_key or (default_context.project_id if default_context else None))

        def _project_admin(self, context):
            key = context.project_id if context else legacy_context.project_id
            if key not in admin_accesses:
                config_path = str(context.config_path) if context and context.config_path else project_config
                admin_accesses[key] = _admin_access(config_path)
            return admin_accesses[key]

        def _query_service(self, context):
            return QueryService(
                connect(str(context.db_path)),
                db_path=str(context.db_path),
                project_config=str(context.config_path) if context.config_path else None,
                workspace_root=context.workspace_root,
                ast_data_root=context.ast_root,
                business_context_root=context.business_context_root if context.registered else None,
                code_map_root=context.code_map_root,
                requirements_root=context.requirements_root if context.registered else None,
                project_id=context.project_id,
                project_scoped=context.registered,
            )

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

        def _require_admin(self, context):
            expected = self._project_admin(context)["token"]
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
                parsed = urlparse(self.path)
                if parsed.path == "/api/projects":
                    if not registry:
                        self._json(400, {"error": "当前启动方式未启用工程注册表"})
                        return
                    body = self._body()
                    config_path = str(body.get("configPath") or body.get("projectConfig") or "").strip()
                    if not config_path:
                        raise ValueError("configPath is required")
                    context = registry.register(
                        config_path,
                        data_root=body.get("dataRoot"),
                        name=body.get("name"),
                    )
                    self._json(201, {"project": context.to_dict(), "projects": registry.list()})
                    return

                path, context = self._project_route(parsed)
                db_path = str(context.db_path)
                project_config = str(context.config_path) if context.config_path else None
                evaluations = evaluation_for(context)
                ast = ast_for(context)
                if path == "/api/knowledge/baselines/refresh":
                    if not self._require_admin(context):
                        return
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(
                        connect(db_path), project_config=project_config,
                        business_context_root=context.business_context_root if context.registered else None,
                    )
                    body = self._body()
                    self._json(200, service.refresh(parser=str(body.get("parser") or "model")))
                    return

                if path == "/api/ast/generate":
                    if not self._require_admin(context):
                        return
                    self._json(202, ast.generate())
                    return

                cancel_match = re.fullmatch(r"/api/query/([^/]+)/cancel", path)
                if cancel_match:
                    service = self._query_service(context)
                    self._json(200, service.cancel_run(cancel_match.group(1)))
                    return
                feedback_match = re.fullmatch(r"/api/query/([^/]+)/feedback", path)
                if feedback_match:
                    service = self._query_service(context)
                    body = self._body()
                    self._json(201, service.record_feedback(
                        feedback_match.group(1), str(body.get("rating") or ""), str(body.get("comment") or "")
                    ))
                    return

                if path == "/api/evaluations":
                    if not self._require_admin(context):
                        return
                    body = self._body()
                    result = evaluations.start(body)
                    self._json(202 if not result.get("duplicate") else 200, result)
                    return
                if path == "/api/evaluation-suites":
                    if not self._require_admin(context):
                        return
                    body = self._body()
                    self._json(201, evaluations.create_suite(body))
                    return
                evaluation_action = re.fullmatch(r"/api/evaluations/([^/]+)/(cancel|rejudge|retry)", path)
                if evaluation_action:
                    if not self._require_admin(context):
                        return
                    eval_id, action = evaluation_action.group(1), evaluation_action.group(2)
                    body = self._body() if action == "rejudge" else {}
                    result = (evaluations.cancel(eval_id) if action == "cancel"
                              else evaluations.rejudge(eval_id) if action == "rejudge"
                              else evaluations.retry_failed(eval_id))
                    self._json(202, result)
                    return

                if path not in {"/api/query", "/api/query/stream"}:
                    self._json(404, {"error": "not found"})
                    return

                body = self._body()
                question = body.get("question")
                conversation_id = body.get("conversationId")
                scope = body.get("scope")
                mode = body.get("mode")
                if scope is not None and not isinstance(scope, dict):
                    raise ValueError("scope 必须是对象，形如 {systemIds: [], repositoryIds: []}")
                service = self._query_service(context)
                if path == "/api/query/stream":
                    stream_started = True
                    self._start_sse()
                    try:
                        result = service.query(
                            question,
                            conversation_id=conversation_id,
                            scope=scope,
                            mode=mode,
                            event_callback=lambda event: self._sse("event", event),
                            run_callback=lambda value: self._sse("run", value),
                        )
                        self._sse("result", result)
                    except QueryBusyError as exc:
                        self._sse("error", {"error": str(exc), "runId": exc.run_id, "code": "CONVERSATION_BUSY"})
                    except Exception as exc:
                        logger.exception("查询流内部错误: %s", type(exc).__name__)
                        try:
                            self._sse("error", {"error": _user_facing_internal_error(exc)})
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                    return

                self._json(200, service.query(question, conversation_id=conversation_id, scope=scope, mode=mode))
            except QueryBusyError as exc:
                self._json(409, {"error": str(exc), "runId": exc.run_id, "code": "CONVERSATION_BUSY"})
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

        def do_PUT(self):
            try:
                parsed = urlparse(self.path)
                path, context = self._project_route(parsed)
                db_path = str(context.db_path)
                project_config = str(context.config_path) if context.config_path else None
                evaluations = evaluation_for(context)
                suite_match = re.fullmatch(r"/api/evaluation-suites/([^/]+)", path)
                if suite_match:
                    if not self._require_admin(context):
                        return
                    body = self._body()
                    self._json(200, evaluations.update_suite(suite_match.group(1), body))
                    return
                review_match = re.fullmatch(r"/api/evaluations/([^/]+)/reviews/([^/]+)", path)
                if review_match:
                    if not self._require_admin(context):
                        return
                    body = self._body()
                    self._json(200, evaluations.save_review(review_match.group(1), review_match.group(2), body))
                    return
                self._json(404, {"error": "not found"})
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                logger.exception("评测接口内部错误: %s", type(exc).__name__)
                self._json(500, {"error": _user_facing_internal_error(exc), "type": type(exc).__name__})

        def do_GET(self):
            service = None
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/projects":
                    if registry:
                        self._json(200, {"items": registry.list()})
                    else:
                        self._json(200, {"items": [{**legacy_context.to_dict(), "registered": False}]})
                    return
                path, context = self._project_route(parsed)
                if path == "/api/project":
                    self._json(200, context.to_dict())
                    return
                if context is None:
                    # Static files do not belong to a project context.
                    if not path.startswith("/api/"):
                        relative = path.lstrip("/")
                        target = (static_root / relative).resolve() if relative else static_root / "index.html"
                        if static_root.resolve() not in target.parents or not target.is_file():
                            target = static_root / "index.html"
                        self._file(target)
                        return
                    raise ValueError("未选择工程")
                db_path = str(context.db_path)
                project_config = str(context.config_path) if context.config_path else None
                evaluations = evaluation_for(context)
                ast = ast_for(context)
                admin_access = self._project_admin(context)
                if path == "/api/evaluations/setup":
                    setup = evaluations.setup()
                    setup["adminAuthRequired"] = admin_access["token"] is not None
                    self._json(200, setup)
                    return
                if path == "/api/ast":
                    self._json(200, ast.status())
                    return
                if path == "/api/ast/documents":
                    version_id = parse_qs(parsed.query).get("versionId", [None])[0]
                    self._json(200, ast.list_documents(version_id))
                    return
                if path == "/api/ast/document":
                    params = parse_qs(parsed.query)
                    self._json(200, ast.read_document(params.get("versionId", [""])[0], params.get("path", [""])[0]))
                    return
                if path == "/api/evaluation-suites":
                    self._json(200, evaluations.list_suites())
                    return
                if path == "/api/evaluations":
                    self._json(200, evaluations.list_evaluations())
                    return
                case_match = re.fullmatch(r"/api/evaluations/([^/]+)/cases/([^/]+)/repeats/(\d+)", path)
                if case_match:
                    self._json(200, evaluations.get_case(case_match.group(1), case_match.group(2),
                                                        int(case_match.group(3))))
                    return
                source_match = re.fullmatch(r"/api/evaluations/([^/]+)/sources/([^/]+)", path)
                if source_match:
                    params = parse_qs(parsed.query)
                    self._json(200, evaluations.read_source(
                        source_match.group(1), source_match.group(2),
                        params.get("path", [""])[0],
                        int(params.get("start", ["1"])[0]),
                        int(params.get("end", ["0"])[0]) or None,
                    ))
                    return
                report_match = re.fullmatch(r"/api/evaluations/([^/]+)/report", path)
                if report_match:
                    source = parse_qs(parsed.query).get("source", ["review"])[0]
                    self._json(200, evaluations.export_report(report_match.group(1), source))
                    return
                evaluation_match = re.fullmatch(r"/api/evaluations/([^/]+)", path)
                if evaluation_match:
                    self._json(200, evaluations.get_evaluation(evaluation_match.group(1)))
                    return
                if path == "/api/workspace":
                    service = self._query_service(context)
                    self._json(200, {**service.workspace_summary(), "projectId": context.project_id,
                                     "projectName": context.project_name,
                                     "adminAuthRequired": admin_access["token"] is not None})
                    return
                if path == "/api/conversations":
                    service = self._query_service(context)
                    params = parse_qs(parsed.query)
                    self._json(200, service.list_conversations(int(params.get("limit", ["20"])[0]), params.get("cursor", [None])[0]))
                    return
                conversation_match = re.fullmatch(r"/api/conversations/([^/]+)", path)
                if conversation_match:
                    service = self._query_service(context)
                    self._json(200, service.get_conversation(conversation_match.group(1)))
                    return
                if path == "/api/runs":
                    service = self._query_service(context)
                    limit = parse_qs(parsed.query).get("limit", ["30"])[0]
                    self._json(200, {"items": service.list_runs(int(limit))})
                    return
                if path == "/api/code/search":
                    from ..tools import EvidenceTools
                    service = self._query_service(context)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    self._json(200, {"items": EvidenceTools(service.db).search_code(query or "_", 50) if query else []})
                    return
                if path == "/api/knowledge-graph":
                    from ..knowledge_graph import KnowledgeGraphService
                    service = self._query_service(context)
                    params = parse_qs(parsed.query)
                    self._json(200, KnowledgeGraphService(service.db).search(params.get("q", [""])[0], params.get("type", [""])[0]))
                    return
                if path == "/api/knowledge/entities":
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(
                        connect(db_path), project_config=project_config,
                        business_context_root=context.business_context_root if context.registered else None,
                    )
                    params = parse_qs(parsed.query)
                    query = params.get("q", [""])[0]
                    entity_type = params.get("type", [""])[0]
                    self._json(200, {"items": service.list_entities(query, entity_type), "relations": service.list_relations(query)})
                    return
                entity_match = re.fullmatch(r"/api/knowledge/entities/([^/]+)", path)
                if entity_match:
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(
                        connect(db_path), project_config=project_config,
                        business_context_root=context.business_context_root if context.registered else None,
                    )
                    self._json(200, service.get_entity(entity_match.group(1)))
                    return
                relation_match = re.fullmatch(r"/api/knowledge/relations/([^/]+)", path)
                if relation_match:
                    from ..knowledge_update.baseline_service import BaselineKnowledgeService
                    service = BaselineKnowledgeService(
                        connect(db_path), project_config=project_config,
                        business_context_root=context.business_context_root if context.registered else None,
                    )
                    self._json(200, service.get_relation(relation_match.group(1)))
                    return
                run_match = re.fullmatch(r"/api/query/([^/]+)", path)
                if run_match:
                    service = self._query_service(context)
                    self._json(200, service.get_run(run_match.group(1)))
                    return
                symbol_match = re.fullmatch(r"/api/code/symbol/([^/]+)", path)
                if symbol_match:
                    from ..tools import EvidenceTools
                    service = self._query_service(context)
                    tools = EvidenceTools(service.db)
                    detail = tools.read_source(symbol_match.group(1))
                    detail["relations"] = tools.get_symbol_relations(symbol_match.group(1))
                    self._json(200, detail)
                    return
                if not path.startswith("/api/"):
                    relative = path.lstrip("/")
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

        def do_DELETE(self):
            service = None
            try:
                parsed = urlparse(self.path)
                path, context = self._project_route(parsed)
                conversation_match = re.fullmatch(r"/api/conversations/([^/]+)", path)
                if conversation_match:
                    service = self._query_service(context)
                    self._json(200, service.delete_conversation(conversation_match.group(1)))
                    return
                self._json(404, {"error": "not found"})
            except QueryBusyError as exc:
                self._json(409, {"error": str(exc), "runId": exc.run_id, "code": "CONVERSATION_BUSY"})
            except KeyError:
                self._json(404, {"error": "会话不存在或已删除"})
            except Exception as exc:
                logger.exception("删除会话内部错误: %s", type(exc).__name__)
                self._json(500, {"error": _user_facing_internal_error(exc), "type": type(exc).__name__})
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
    project_registry: ProjectRegistry | str | Path | None = None,
    project_id: str | None = None,
):
    server = make_server(
        db_path, host, port, project_config=project_config,
        project_registry=project_registry, project_id=project_id,
    )
    logger.info("工作台已启动: http://%s:%s/  (db=%s)", host, port, db_path)
    if project_config:
        logger.info("项目配置: %s", project_config)
    if project_registry:
        logger.info("工程注册表: %s", project_registry)
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

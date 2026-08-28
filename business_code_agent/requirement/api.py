from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..schema import connect
from .service import RequirementService


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8081) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _service(self):
            return RequirementService(connect(db_path))

        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def do_POST(self):
            self._json(410, {
                "error": "requirement write API is disabled; use the administrator CLI import command",
            })

        def do_GET(self):
            service = None
            try:
                service = self._service()
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/api/requirements/search":
                    self._json(200, {"items": service.search(parse_qs(parsed.query).get("q", [""])[0])})
                    return
                chunk = re.fullmatch(r"/api/requirements/([^/]+)/chunks/([^/]+)", path)
                code = re.fullmatch(r"/api/requirements/([^/]+)/code-relations", path)
                changes = re.fullmatch(r"/api/requirements/([^/]+)/changes", path)
                detail = re.fullmatch(r"/api/requirements/([^/]+)", path)
                if chunk:
                    self._json(200, service.read_chunk(chunk.group(1), chunk.group(2)))
                elif code:
                    self._json(200, {"items": service.code_relations(code.group(1))})
                elif changes:
                    self._json(200, {"items": service.changes(changes.group(1))})
                elif detail:
                    self._json(200, service.get(detail.group(1)))
                else:
                    self._json(404, {"error": "not found"})
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": "internal requirement service error", "type": type(exc).__name__})
            finally:
                if service:
                    service.db.close()

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8081):
    make_server(db_path, host, port).serve_forever()

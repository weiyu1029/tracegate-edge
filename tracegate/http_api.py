"""Zero-dependency HTTP/JSON adapter for the TraceGate service."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .dashboard import DASHBOARD_HTML
from .errors import DomainError
from .outbox import OutboxWorker
from .service import TraceGateService
from .util import canonical_json


UNIT_ROUTE_RE = re.compile(r"^/api/v1/units/([^/]+)(/trace)?$")


def make_edge_handler(
    service: TraceGateService,
    *,
    worker: OutboxWorker | None = None,
    dashboard_html: str | None = None,
    demo_mes_controller: Callable[[bool], bool] | None = None,
    demo_reset_controller: Callable[[], None] | None = None,
    allow_demo_controls: bool = False,
) -> type[BaseHTTPRequestHandler]:
    page = (dashboard_html or DASHBOARD_HTML).encode("utf-8")

    class EdgeHandler(BaseHTTPRequestHandler):
        server_version = "TraceGate-Edge/0.2"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(
                f"[edge {self.log_date_time_string()}] {self.address_string()} {fmt % args}",
                flush=True,
            )

        def _base_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")

        def send_json(self, body: dict[str, Any], status: int = 200) -> None:
            encoded = canonical_json(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self._base_headers()
            self.end_headers()
            self.wfile.write(encoded)

        def send_page(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'",
            )
            self._base_headers()
            self.end_headers()
            self.wfile.write(page)

        def read_json(self) -> dict[str, Any]:
            content_type = (
                self.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type != "application/json":
                raise DomainError(
                    "UNSUPPORTED_MEDIA_TYPE",
                    "Content-Type must be application/json",
                    415,
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise DomainError(
                    "INVALID_REQUEST", "Invalid Content-Length", 400
                ) from exc
            if not 1 <= length <= 131072:
                raise DomainError(
                    "INVALID_REQUEST", "JSON body must be 1-131072 bytes", 400
                )
            try:
                body = json.loads(
                    self.rfile.read(length),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"Non-finite number is not valid JSON: {value}")
                    ),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise DomainError("INVALID_JSON", "Request body is not valid JSON", 400) from exc
            if not isinstance(body, dict):
                raise DomainError("INVALID_REQUEST", "JSON object required", 400)
            return body

        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self.send_page()
                    return
                if parsed.path == "/api/v1/health":
                    body = service.health()
                    self.send_json(body, 200 if body["ok"] else 503)
                    return
                if parsed.path == "/api/v1/metrics":
                    self.send_json(service.metrics())
                    return
                if parsed.path == "/api/v1/outbox":
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["100"])[0])
                    except ValueError:
                        limit = 100
                    self.send_json(service.list_outbox(limit=limit))
                    return
                match = UNIT_ROUTE_RE.fullmatch(parsed.path)
                if match:
                    acn = unquote(match.group(1))
                    body = service.get_trace(acn) if match.group(2) else service.get_unit(acn)
                    self.send_json(body)
                    return
                self._not_found()
            except DomainError as exc:
                self.send_json(exc.as_dict(), exc.status)
            except Exception as exc:
                self._internal_error(exc)

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                body = self.read_json()
                if parsed.path == "/api/v1/mac/check":
                    self.send_json(service.check_mac(body), 200)
                    return
                if parsed.path == "/api/v1/key-parts/collect":
                    self.send_json(service.collect_key_part(body), 201)
                    return
                if parsed.path == "/api/v1/stations/complete":
                    response = service.complete_station(body)
                    if worker:
                        worker.wake()
                    self.send_json(response, 201)
                    return
                if parsed.path == "/api/v1/repairs/actions":
                    self.send_json(service.record_repair_action(body), 201)
                    return
                if parsed.path == "/api/v1/outbox/replay":
                    response = service.replay_outbox(body)
                    if worker:
                        worker.wake()
                    self.send_json(response)
                    return
                if parsed.path == "/api/v1/demo/reset":
                    if not allow_demo_controls:
                        raise DomainError(
                            "DEMO_CONTROL_UNAVAILABLE",
                            "Reset is available only in combined demo mode",
                            409,
                        )
                    quiesced = worker is None or worker.quiesce(timeout=5.0)
                    if not quiesced:
                        raise DomainError(
                            "DEMO_RESET_BUSY",
                            "Delivery is still in progress; retry reset after it finishes",
                            409,
                        )
                    try:
                        response = service.reset_demo()
                        if demo_reset_controller:
                            demo_reset_controller()
                    finally:
                        if worker:
                            worker.resume()
                    self.send_json(response)
                    return
                if parsed.path == "/api/v1/demo/mes":
                    online = body.get("online")
                    if not isinstance(online, bool):
                        raise DomainError(
                            "INVALID_REQUEST", "online must be true or false", 400
                        )
                    if not demo_mes_controller:
                        raise DomainError(
                            "DEMO_CONTROL_UNAVAILABLE",
                            "MES control is available only in combined demo mode",
                            409,
                        )
                    value = demo_mes_controller(online)
                    if online and worker:
                        worker.wake()
                    self.send_json({"ok": True, "online": value})
                    return
                self._not_found()
            except DomainError as exc:
                self.send_json(exc.as_dict(), exc.status)
            except Exception as exc:
                self._internal_error(exc)

        def _not_found(self) -> None:
            self.send_json(
                {
                    "ok": False,
                    "error": {"code": "NOT_FOUND", "message": "Route not found"},
                },
                404,
            )

        def _internal_error(self, error: Exception) -> None:
            print(f"[edge] internal error: {type(error).__name__}: {error}", flush=True)
            self.send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Unexpected server error",
                    },
                },
                500,
            )

    return EdgeHandler


def create_edge_server(
    service: TraceGateService,
    host: str = "0.0.0.0",
    port: int = 8080,
    *,
    worker: OutboxWorker | None = None,
    dashboard_html: str | None = None,
    demo_mes_controller: Callable[[bool], bool] | None = None,
    demo_reset_controller: Callable[[], None] | None = None,
    allow_demo_controls: bool = False,
) -> ThreadingHTTPServer:
    handler = make_edge_handler(
        service,
        worker=worker,
        dashboard_html=dashboard_html,
        demo_mes_controller=demo_mes_controller,
        demo_reset_controller=demo_reset_controller,
        allow_demo_controls=allow_demo_controls,
    )
    return ThreadingHTTPServer((host, port), handler)

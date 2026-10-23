"""Independent idempotent receiver used to demonstrate upstream outages."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .util import canonical_json, utc_now


class MockMesStore:
    REQUIRED_RECEIPT_COLUMNS = {
        "event_id",
        "idempotency_key",
        "acn",
        "station_code",
        "result",
        "payload",
        "received_at",
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._online = True
        self._lock = threading.Lock()
        with contextlib.closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            self._migrate_receipts(connection)

    @classmethod
    def _migrate_receipts(cls, connection: sqlite3.Connection) -> None:
        """Create the receiver schema while preserving incompatible v0.1 data."""

        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='receipts'"
            ).fetchone()
            if existing:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(receipts)")
                }
                compatible = cls.REQUIRED_RECEIPT_COLUMNS.issubset(
                    columns
                ) and cls._event_id_is_unique(connection)
                if not compatible:
                    suffix = 1
                    while True:
                        legacy_name = f"receipts_legacy_v1_{suffix}"
                        collision = connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (legacy_name,),
                        ).fetchone()
                        if not collision:
                            break
                        suffix += 1
                    # legacy_name is generated above and never contains user input.
                    connection.execute(
                        f'ALTER TABLE receipts RENAME TO "{legacy_name}"'
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    event_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    acn TEXT NOT NULL,
                    station_code TEXT NOT NULL,
                    result TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _event_id_is_unique(connection: sqlite3.Connection) -> bool:
        """Return whether the existing receipts table enforces unique event IDs."""

        table_info = connection.execute("PRAGMA table_info(receipts)").fetchall()
        primary_key = [
            row["name"]
            for row in sorted(table_info, key=lambda row: int(row["pk"]))
            if int(row["pk"]) > 0
        ]
        if primary_key == ["event_id"]:
            return True

        for index in connection.execute("PRAGMA index_list(receipts)").fetchall():
            if not int(index["unique"]) or int(index["partial"]):
                continue
            index_name = str(index["name"]).replace('"', '""')
            indexed_columns = [
                row["name"]
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            ]
            if indexed_columns == ["event_id"]:
                return True
        return False

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def online(self) -> bool:
        with self._lock:
            return self._online

    def set_online(self, online: bool) -> bool:
        with self._lock:
            self._online = online
            return self._online

    def receive(
        self, event: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if not self.online():
            raise MockMesOffline("Synthetic MES outage is active")
        required = ("event_id", "acn", "station_code", "result", "occurred_at")
        missing = [field for field in required if not event.get(field)]
        if missing:
            raise ValueError(f"Missing event fields: {', '.join(missing)}")
        event_id = str(event["event_id"])
        if idempotency_key != event_id:
            raise ValueError("Idempotency-Key must match event_id")
        received_at = utc_now()
        event_payload = canonical_json(event)
        with contextlib.closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO receipts(
                    event_id,idempotency_key,acn,station_code,result,payload,received_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    idempotency_key,
                    str(event["acn"]),
                    str(event["station_code"]),
                    str(event["result"]),
                    event_payload,
                    received_at,
                ),
            )
            duplicate = cursor.rowcount == 0
            if duplicate:
                row = connection.execute(
                    "SELECT payload,received_at FROM receipts WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if row["payload"] != event_payload:
                    raise MockMesConflict(
                        "event_id was already received with a different payload"
                    )
                received_at = row["received_at"]
        return {
            "ok": True,
            "event_id": event_id,
            "duplicate": duplicate,
            "received_at": received_at,
        }

    def receipts(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with contextlib.closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id,acn,station_code,result,received_at
                FROM receipts ORDER BY received_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        with contextlib.closing(self.connect()) as connection:
            return int(connection.execute("SELECT count(*) FROM receipts").fetchone()[0])

    def reset(self) -> None:
        """Clear only synthetic receipts for a new combined-demo run."""

        with contextlib.closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM receipts")


class MockMesOffline(RuntimeError):
    pass


class MockMesConflict(RuntimeError):
    pass


class UnsupportedMediaType(ValueError):
    pass


def make_mock_mes_handler(store: MockMesStore) -> type[BaseHTTPRequestHandler]:
    class MockMesHandler(BaseHTTPRequestHandler):
        server_version = "TraceGate-Mock-MES/0.2"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(
                f"[mock-mes {self.log_date_time_string()}] "
                f"{self.address_string()} {fmt % args}",
                flush=True,
            )

        def send_json(self, body: dict[str, Any], status: int = 200) -> None:
            encoded = canonical_json(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def read_json(self) -> dict[str, Any]:
            content_type = (
                self.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type != "application/json":
                raise UnsupportedMediaType("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid Content-Length") from exc
            if not 1 <= length <= 131072:
                raise ValueError("JSON body must be 1-131072 bytes")
            try:
                body = json.loads(
                    self.rfile.read(length),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"Non-finite number is not valid JSON: {value}")
                    ),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError("Invalid JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            return body

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/v1/health":
                self.send_json(
                    {
                        "ok": True,
                        "service": "synthetic-mock-mes",
                        "online": store.online(),
                        "receipt_count": store.count(),
                        "time": utc_now(),
                    }
                )
                return
            if parsed.path == "/api/v1/receipts":
                query = parse_qs(parsed.query)
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError:
                    limit = 100
                self.send_json({"ok": True, "receipts": store.receipts(limit)})
                return
            self.send_json(
                {"ok": False, "error": {"code": "NOT_FOUND", "message": "Not found"}},
                404,
            )

        def do_POST(self) -> None:
            try:
                if self.path == "/api/v1/trace-events":
                    event = self.read_json()
                    response = store.receive(
                        event, self.headers.get("Idempotency-Key", "")
                    )
                    self.send_json(response, 200)
                    return
                if self.path == "/api/v1/demo/mes":
                    body = self.read_json()
                    online = body.get("online")
                    if not isinstance(online, bool):
                        raise ValueError("online must be true or false")
                    self.send_json({"ok": True, "online": store.set_online(online)})
                    return
                self.send_json(
                    {
                        "ok": False,
                        "error": {"code": "NOT_FOUND", "message": "Not found"},
                    },
                    404,
                )
            except MockMesOffline as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": {"code": "MES_OFFLINE", "message": str(exc)},
                    },
                    503,
                )
            except MockMesConflict as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": {"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
                    },
                    409,
                )
            except UnsupportedMediaType as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": {
                            "code": "UNSUPPORTED_MEDIA_TYPE",
                            "message": str(exc),
                        },
                    },
                    415,
                )
            except ValueError as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": {"code": "INVALID_REQUEST", "message": str(exc)},
                    },
                    400,
                )
            except Exception as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
                    },
                    500,
                )

    return MockMesHandler


def create_mock_mes_server(
    store: MockMesStore, host: str = "127.0.0.1", port: int = 8081
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_mock_mes_handler(store))

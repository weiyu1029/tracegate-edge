from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tracegate.db import Database
from tracegate.http_api import create_edge_server
from tracegate.service import TraceGateService


class HttpApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Database(Path(self.temp.name) / "edge.db")
        database.migrate(seed=True)
        self.service = TraceGateService(database)
        try:
            self.server = create_edge_server(self.service, "127.0.0.1", 0)
        except PermissionError as exc:
            self.skipTest(f"Local socket binding is disabled by this test sandbox: {exc}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_health_metrics_unit_trace_and_outbox_routes(self) -> None:
        for path in (
            "/api/v1/health",
            "/api/v1/metrics",
            "/api/v1/units/ACN-DEMO-0001",
            "/api/v1/units/ACN-DEMO-0001/trace",
            "/api/v1/outbox",
        ):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body["ok"], path)

    def test_http_route_validation_returns_structured_error(self) -> None:
        status, body = self.post(
            "/api/v1/stations/complete",
            {
                "request_id": "http-invalid-route-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-FT",
                "result": "PASS",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "ROUTE_VIOLATION")

    def test_mutation_requires_json_content_type(self) -> None:
        request = urllib.request.Request(
            self.base + "/api/v1/stations/complete",
            data=b"{}",
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            with error:
                self.assertEqual(error.code, 415)
                body = json.load(error)
        else:
            self.fail("Expected HTTP 415")
        self.assertEqual(body["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")

    def test_invalid_utf8_and_non_finite_numbers_are_rejected_as_json_errors(self) -> None:
        for raw in (b"\xff", b'{"measurement":NaN}'):
            request = urllib.request.Request(
                self.base + "/api/v1/stations/complete",
                data=raw,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                with error:
                    self.assertEqual(error.code, 400)
                    body = json.load(error)
            else:
                self.fail("Expected malformed JSON to be rejected")
            self.assertEqual(body["error"]["code"], "INVALID_JSON")

    def test_mac_keypart_station_and_replay_routes_exist(self) -> None:
        status, body = self.post(
            "/api/v1/stations/complete",
            {
                "request_id": "http-intake-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])
        status, body = self.post(
            "/api/v1/mac/check",
            {
                "request_id": "http-mac-check-0001",
                "acn": "ACN-DEMO-0001",
                "mac_address": "02-54-47-00-01-01",
                "interface_name": "wlan0",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, body = self.post(
            "/api/v1/outbox/replay",
            {"request_id": "http-replay-0001"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()

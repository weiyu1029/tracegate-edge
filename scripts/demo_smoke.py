#!/usr/bin/env python3
"""Smoke-test TraceGate or run its deterministic synthetic portfolio journey.

Examples:
  python scripts/demo_smoke.py --wait 30
  python scripts/demo_smoke.py --wait 30 --journey
  python scripts/demo_smoke.py --wait 30 --journey --outage-demo --benchmark 50
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HttpFailure(RuntimeError):
    status: int
    payload: dict[str, Any]

    def __str__(self) -> str:
        error = self.payload.get("error", {})
        return (
            f"HTTP {self.status}: {error.get('code', 'UNKNOWN')} - "
            f"{error.get('message', '')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="TraceGate base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--mes-base-url",
        default="http://127.0.0.1:8081",
        help="Synthetic mock MES URL used by --outage-demo",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="Read-only path to verify; may repeat (default: /api/v1/health)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=0,
        metavar="SECONDS",
        help="Wait up to this many seconds for services to become ready",
    )
    parser.add_argument("--timeout", type=float, default=3, help="Per-request timeout")
    parser.add_argument(
        "--journey",
        action="store_true",
        help="Reset fixtures and run MAC, BOM, fail, repair, retest, and pack flow",
    )
    parser.add_argument(
        "--outage-demo",
        action="store_true",
        help="Keep mock MES offline during the journey, then prove outbox recovery",
    )
    parser.add_argument(
        "--delivery-wait",
        type=float,
        default=30,
        help="Seconds to wait for all outbox records to become delivered",
    )
    parser.add_argument(
        "--benchmark",
        type=int,
        default=0,
        metavar="REQUESTS",
        help="Measure this many local unit-read requests after validation",
    )
    return parser.parse_args()


def request_json(
    method: str,
    url: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "tracegate-smoke/2"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise RuntimeError(f"expected application/json, got {content_type}")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise RuntimeError("expected a JSON object")
            return decoded
    except urllib.error.HTTPError as exc:
        try:
            decoded = json.loads(exc.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            decoded = {"error": {"code": "HTTP_ERROR", "message": str(exc)}}
        if not isinstance(decoded, dict):
            decoded = {"error": {"code": "HTTP_ERROR", "message": str(decoded)}}
        raise HttpFailure(exc.code, decoded) from exc


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def wait_for_json(url: str, timeout: float, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        try:
            return request_json("GET", url, timeout)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"service did not become ready: {url}: {exc}") from exc
            time.sleep(0.5)


def expect_error(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    expected_code: str,
    timeout: float,
) -> None:
    try:
        request_json("POST", join_url(base_url, path), timeout, payload)
    except HttpFailure as exc:
        actual = exc.payload.get("error", {}).get("code")
        if actual != expected_code:
            raise AssertionError(f"expected {expected_code}, got {actual}: {exc}") from exc
        print(f"PASS guarded rejection: {expected_code}")
        return
    raise AssertionError(f"expected {expected_code}, but request succeeded")


class Journey:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url
        self.timeout = timeout
        self.run_id = uuid.uuid4().hex[:10]

    def request_id(self, label: str) -> str:
        return f"demo-{self.run_id}-{label}"

    def get(self, path: str) -> dict[str, Any]:
        return request_json("GET", join_url(self.base_url, path), self.timeout)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return request_json("POST", join_url(self.base_url, path), self.timeout, payload)

    def complete(
        self,
        label: str,
        station_code: str,
        result: str = "PASS",
        *,
        reason_code: str = "OK",
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.post(
            "/api/v1/stations/complete",
            {
                "request_id": self.request_id(label),
                "acn": "ACN-DEMO-0001",
                "station_code": station_code,
                "result": result,
                "reason_code": reason_code,
                "evidence": evidence or {"source": "synthetic-smoke"},
            },
        )
        event = response["event"]
        transition = response["transition"]
        print(
            f"PASS {station_code} {result}: sequence "
            f"{transition['from_sequence']} -> {transition['to_sequence']} "
            f"(attempt {event['attempt_no']})"
        )
        return response

    def run(self) -> dict[str, Any]:
        self.post("/api/v1/demo/reset", {"scope": "synthetic-fixtures"})
        print("PASS reset synthetic fixtures")
        self.complete("intake", "ST-IN")

        expect_error(
            self.base_url,
            "/api/v1/mac/check",
            {
                "request_id": self.request_id("wrong-mac"),
                "acn": "ACN-DEMO-0001",
                "interface_name": "wlan0",
                "mac_address": "02:54:47:00:02:01",
            },
            "ASSOCIATION_MISMATCH",
            self.timeout,
        )
        mac_request = {
            "request_id": self.request_id("correct-mac"),
            "acn": "ACN-DEMO-0001",
            "interface_name": "wlan0",
            "mac_address": "02:54:47:00:01:01",
        }
        self.post("/api/v1/mac/check", mac_request)
        replay = self.post("/api/v1/mac/check", mac_request)
        if replay.get("idempotent_replay") is not True:
            raise AssertionError("same request_id did not produce an idempotent replay")
        print("PASS MAC association and idempotent replay")
        self.complete("identity", "ST-ID")

        expect_error(
            self.base_url,
            "/api/v1/key-parts/collect",
            {
                "request_id": self.request_id("invalid-part"),
                "acn": "ACN-DEMO-0001",
                "part_no": "UNLISTED-X999",
                "kpsn": "KPSN-INVALID-0001",
            },
            "INVALID_SUBSTITUTE",
            self.timeout,
        )
        key_parts = (
            ("controller", "CTRL-A101", "KPSN-CTRL-0001"),
            ("sensor-a", "SENSOR-T200", "KPSN-SENSOR-0001"),
            ("sensor-b", "SENSOR-T201", "KPSN-SENSOR-0002"),
        )
        for label, part_no, kpsn in key_parts:
            self.post(
                "/api/v1/key-parts/collect",
                {
                    "request_id": self.request_id(label),
                    "acn": "ACN-DEMO-0001",
                    "part_no": part_no,
                    "kpsn": kpsn,
                },
            )
        print("PASS approved substitutes and exact BOM quantities")
        self.complete("key-parts", "ST-KP")

        failed_test = self.complete(
            "functional-fail",
            "ST-FT",
            "FAIL",
            reason_code="TEST_VOLTAGE_LOW",
            evidence={
                "source": "synthetic-smoke",
                "description": "Synthetic voltage check below demo threshold",
            },
        )
        defect = failed_test.get("defect")
        if not defect:
            raise AssertionError("failed station did not open a defect")
        self.post(
            "/api/v1/repairs/actions",
            {
                "request_id": self.request_id("repair-action"),
                "acn": "ACN-DEMO-0001",
                "defect_id": defect["defect_id"],
                "action_code": "RESEAT-CONNECTOR",
                "notes": "Synthetic connector reseat and verification",
                "technician": "demo-operator",
            },
        )
        print("PASS verified repair action")
        self.complete("repair", "ST-RP")
        self.complete("functional-retest", "ST-FT")
        self.complete("pack", "ST-PK")
        self.complete("route-complete", "ST-OUT")

        unit = self.get("/api/v1/units/ACN-DEMO-0001")
        trace = self.get("/api/v1/units/ACN-DEMO-0001/trace")
        if unit["unit"]["status"] != "COMPLETE":
            raise AssertionError(f"unexpected final WIP state: {unit['unit']['status']}")
        if len(trace["station_events"]) != 8:
            raise AssertionError("expected exactly eight station events in the demo journey")
        if len(trace["repair_actions"]) != 1:
            raise AssertionError("expected one repair action")
        print("PASS route complete with inspectable genealogy and repair history")
        return trace


def set_mock_mes(base_url: str, online: bool, timeout: float) -> None:
    response = request_json(
        "POST",
        join_url(base_url, "/api/v1/demo/mes"),
        timeout,
        {"online": online},
    )
    if response.get("online") is not online:
        raise AssertionError("mock MES did not change outage state")
    print(f"PASS mock MES {'online' if online else 'offline'}")


def wait_for_delivery(base_url: str, timeout: float, wait_seconds: float) -> int:
    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        outbox = request_json(
            "GET", join_url(base_url, "/api/v1/outbox?limit=100"), timeout
        )
        rows = outbox.get("outbox", [])
        if rows and all(row.get("status") == "DELIVERED" for row in rows):
            return len(rows)
        if time.monotonic() >= deadline:
            states = [row.get("status") for row in rows]
            raise RuntimeError(f"outbox did not drain before deadline: {states}")
        time.sleep(0.5)


def benchmark_reads(base_url: str, timeout: float, count: int) -> dict[str, float | int]:
    if count <= 0:
        return {}
    samples_ms: list[float] = []
    target = join_url(base_url, "/api/v1/units/ACN-DEMO-0001")
    for _ in range(count):
        started = time.perf_counter()
        request_json("GET", target, timeout)
        samples_ms.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples_ms)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    result: dict[str, float | int] = {
        "requests": len(ordered),
        "min_ms": round(ordered[0], 3),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(ordered[-1], 3),
    }
    print(f"MEASURED benchmark {json.dumps(result, sort_keys=True)}")
    return result


def main() -> int:
    args = parse_args()
    if args.outage_demo and not args.journey:
        print("error: --outage-demo requires --journey", file=sys.stderr)
        return 2
    paths = args.path or ["/api/v1/health"]
    base_url = args.base_url.rstrip("/")

    try:
        for path in paths:
            target = join_url(base_url, path)
            payload = wait_for_json(target, args.timeout, args.wait)
            if payload.get("ok") is not True:
                raise RuntimeError(f"health response was not ok: {payload}")
            print(f"PASS {target}")

        if args.outage_demo:
            wait_for_json(
                join_url(args.mes_base_url, "/api/v1/health"),
                args.timeout,
                args.wait,
            )
            set_mock_mes(args.mes_base_url, False, args.timeout)

        trace: dict[str, Any] | None = None
        if args.journey:
            trace = Journey(base_url, args.timeout).run()

        delivered = 0
        if args.outage_demo:
            pending = request_json(
                "GET", join_url(base_url, "/api/v1/outbox?limit=100"), args.timeout
            ).get("outbox", [])
            if not pending or any(row.get("status") == "DELIVERED" for row in pending):
                raise AssertionError("outage demo expected locally retained undelivered events")
            print(f"PASS outage retained {len(pending)} event(s) locally")
            set_mock_mes(args.mes_base_url, True, args.timeout)
            request_json(
                "POST",
                join_url(base_url, "/api/v1/outbox/replay"),
                args.timeout,
                {"request_id": f"demo-{uuid.uuid4().hex[:10]}-replay"},
            )
            delivered = wait_for_delivery(base_url, args.timeout, args.delivery_wait)
            print(f"PASS recovery delivered {delivered} unique event(s)")

        benchmark = benchmark_reads(base_url, args.timeout, args.benchmark)
        summary = {
            "ok": True,
            "journey": bool(trace),
            "station_events": len(trace["station_events"]) if trace else 0,
            "timeline_items": len(trace["timeline"]) if trace else 0,
            "outage_recovered_events": delivered,
            "benchmark": benchmark,
        }
        print(f"SUMMARY {json.dumps(summary, sort_keys=True)}")
        return 0
    except (AssertionError, HttpFailure, OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        if args.outage_demo:
            try:
                set_mock_mes(args.mes_base_url, True, args.timeout)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Black-box integration tests against a real ASP.NET Core process and temporary SQLite DB.
Run after dotnet build: python3 -m unittest discover -s tests -v
No third-party Python packages, external services, or production data are required.
"""
import concurrent.futures
import csv
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DLL = ROOT / "src/TraceGate.MesLab/bin/Debug/net10.0/TraceGate.MesLab.dll"


def key():
    return str(uuid.uuid4())


class MesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DLL.exists():
            raise RuntimeError("Build first: dotnet build src/TraceGate.MesLab")
        cls.temp = tempfile.TemporaryDirectory(prefix="tracegate-mes-lab-tests-")
        cls.db = str(Path(cls.temp.name) / "test.db")
        cls.start_server()

    @classmethod
    def start_server(cls):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(MES_DB_PATH=cls.db, MES_SEED_DEMO="false", ASPNETCORE_URLS=cls.base,
                   ASPNETCORE_ENVIRONMENT="Production", DOTNET_CLI_TELEMETRY_OPTOUT="1")
        cls.log = open(Path(cls.temp.name) / "server.log", "a", encoding="utf-8")
        cls.proc = subprocess.Popen(["dotnet", str(DLL)], cwd=ROOT / "src/TraceGate.MesLab",
                                    env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if cls.proc.poll() is not None:
                cls.log.flush()
                raise RuntimeError(Path(cls.temp.name, "server.log").read_text())
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=1) as res:
                    if res.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        cls.stop_server()
        raise RuntimeError("Server did not become healthy")

    @classmethod
    def stop_server(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        cls.log.close()

    @classmethod
    def tearDownClass(cls):
        cls.stop_server()
        cls.temp.cleanup()

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode("utf-8-sig")
            content = json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw
            return response.status, content, response.headers

    def wo(self, qty=5):
        no = "WO-" + uuid.uuid4().hex[:12].upper()
        payload = dict(workOrderNo=no, productCode="DEMO-CONTROLLER", plannedQuantity=qty,
                       operator="Demo Operator", requestId=key())
        status, obj, _ = self.request("POST", "/api/work-orders", payload)
        self.assertEqual(status, 201, obj)
        return no

    def register(self, wo, sn=None, request_id=None):
        sn = sn or "SN-" + uuid.uuid4().hex[:12].upper()
        payload = dict(workOrderNo=wo, serialNumber=sn, operator="Demo Operator", requestId=request_id or key())
        return sn, self.request("POST", "/api/units/register", payload), payload

    def unit(self, qty=5):
        wo = self.wo(qty)
        sn, response, _ = self.register(wo)
        self.assertEqual(response[0], 201, response[1])
        return wo, sn

    def report(self, sn, station, result="PASS", reason="", request_id=None):
        return self.request("POST", f"/api/units/{sn}/station-events", dict(
            station=station, result=result, reason=reason, operator="Demo Operator", requestId=request_id or key()))

    def trace(self, sn):
        status, trace, _ = self.request("GET", f"/api/units/{sn}/trace")
        self.assertEqual(status, 200, trace)
        return trace

    def test_01_health_and_route(self):
        self.assertEqual(self.request("GET", "/api/health")[0], 200)
        status, meta, _ = self.request("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertTrue(meta["syntheticData"])
        self.assertEqual([s["code"] for s in meta["route"]], ["ASSEMBLY", "TEST", "PACK"])

    def test_02_validation_does_not_create_order(self):
        for qty in (0, -1, 1001):
            status, error, _ = self.request("POST", "/api/work-orders", dict(
                workOrderNo="WO-BAD", productCode="DEMO", plannedQuantity=qty, operator="Demo", requestId=key()))
            self.assertEqual(status, 400, error)
        self.assertEqual(self.request("GET", "/api/units/UNKNOWN/trace")[0], 404)

    def test_03_serial_number_is_globally_unique(self):
        wo, sn = self.unit()
        other = self.wo()
        _, result, _ = self.register(other, sn)
        self.assertEqual(result[0], 409, result[1])
        self.assertEqual(self.trace(sn)["unit"]["workOrderNo"], wo)

    def test_04_capacity_is_enforced(self):
        wo, _ = self.unit(1)
        _, result, _ = self.register(wo)
        self.assertEqual(result[0], 409, result[1])

    def test_05_skip_station_rejected_without_event(self):
        _, sn = self.unit()
        before = self.trace(sn)
        status, error, _ = self.report(sn, "PACK")
        self.assertEqual(status, 409, error)
        after = self.trace(sn)
        self.assertEqual(before["events"], after["events"])
        self.assertEqual(after["unit"]["nextStation"], "ASSEMBLY")

    def test_06_complete_route_and_block_repeat(self):
        _, sn = self.unit()
        for station in ("ASSEMBLY", "TEST", "PACK"):
            status, result, _ = self.report(sn, station)
            self.assertEqual(status, 200, result)
        trace = self.trace(sn)
        self.assertEqual(trace["unit"]["status"], "Completed")
        self.assertIsNone(trace["unit"]["nextStation"])
        self.assertEqual(len(trace["events"]), 4)
        self.assertEqual(self.report(sn, "PACK")[0], 409)
        self.assertEqual(len(self.trace(sn)["events"]), 4)

    def test_07_fail_hold_release_retest(self):
        _, sn = self.unit()
        self.assertEqual(self.report(sn, "ASSEMBLY")[0], 200)
        self.assertEqual(self.report(sn, "TEST", "FAIL", "Synthetic voltage check failed")[0], 200)
        trace = self.trace(sn)
        self.assertEqual(trace["unit"]["status"], "Held")
        self.assertEqual(trace["unit"]["nextStation"], "TEST")
        self.assertEqual(self.report(sn, "TEST")[0], 409)
        status, result, _ = self.request("POST", f"/api/units/{sn}/release-hold", dict(
            operator="Demo Supervisor", reason="Demo fixture checked", requestId=key()))
        self.assertEqual(status, 200, result)
        self.assertEqual(self.trace(sn)["unit"]["nextStation"], "TEST")
        self.assertEqual(self.report(sn, "PACK")[0], 409)
        self.assertEqual(self.report(sn, "TEST")[0], 200)
        self.assertEqual(self.report(sn, "PACK")[0], 200)
        self.assertEqual(len(self.trace(sn)["events"]), 6)

    def test_08_fail_and_release_require_reason(self):
        _, sn = self.unit()
        self.assertEqual(self.report(sn, "ASSEMBLY", "FAIL")[0], 400)
        self.assertEqual(self.report(sn, "ASSEMBLY", "FAIL", "Fixture failure")[0], 200)
        status, _, _ = self.request("POST", f"/api/units/{sn}/release-hold", dict(
            operator="Demo", reason="", requestId=key()))
        self.assertEqual(status, 400)
        self.assertEqual(self.trace(sn)["unit"]["status"], "Held")

    def test_09_cannot_release_ready_unit(self):
        _, sn = self.unit()
        status, _, _ = self.request("POST", f"/api/units/{sn}/release-hold", dict(
            operator="Demo", reason="No existing hold", requestId=key()))
        self.assertEqual(status, 409)
        self.assertEqual(len(self.trace(sn)["events"]), 1)

    def test_10_idempotent_registration_and_payload_conflict(self):
        wo = self.wo()
        sn, first, payload = self.register(wo)
        second = self.request("POST", "/api/units/register", payload)
        self.assertEqual(second[0], first[0])
        self.assertEqual(second[1], first[1])
        self.assertEqual(second[2].get("Idempotent-Replayed"), "true")
        self.assertEqual(len(self.trace(sn)["events"]), 1)
        payload["serialNumber"] += "X"
        self.assertEqual(self.request("POST", "/api/units/register", payload)[0], 409)

    def test_11_idempotent_station_report(self):
        _, sn = self.unit()
        request_id = key()
        first = self.report(sn, "ASSEMBLY", request_id=request_id)
        second = self.report(sn, "ASSEMBLY", request_id=request_id)
        self.assertEqual(first[1], second[1])
        self.assertEqual(second[0], 200)
        self.assertEqual(len(self.trace(sn)["events"]), 2)
        self.assertEqual(self.report(sn, "TEST", request_id=request_id)[0], 409)

    def test_12_invalid_station_result_and_operator(self):
        _, sn = self.unit()
        self.assertEqual(self.report(sn, "UNKNOWN")[0], 400)
        self.assertEqual(self.report(sn, "ASSEMBLY", "MAYBE")[0], 400)
        status, _, _ = self.request("POST", f"/api/units/{sn}/station-events", dict(
            station="ASSEMBLY", result="PASS", operator="", reason="", requestId=key()))
        self.assertEqual(status, 400)
        self.assertEqual(len(self.trace(sn)["events"]), 1)

    def test_13_sql_characters_do_not_break_database(self):
        wo = self.wo()
        _, result, _ = self.register(wo, "SN-';DROP TABLE units;--")
        self.assertEqual(result[0], 400)
        self.assertEqual(self.request("GET", "/api/work-orders")[0], 200)

    def test_14_concurrent_registration_cannot_exceed_capacity(self):
        wo = self.wo(3)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.register(wo)[1][0], range(8)))
        self.assertEqual(results.count(201), 3, results)
        self.assertEqual(results.count(409), 5, results)
        status, units, _ = self.request("GET", f"/api/work-orders/{wo}/units")
        self.assertEqual(status, 200)
        self.assertEqual(len(units["items"]), 3)

    def test_15_concurrent_retries_create_one_event(self):
        wo = self.wo(1)
        sn = "SN-" + uuid.uuid4().hex[:12].upper()
        payload = dict(workOrderNo=wo, serialNumber=sn, operator="Demo", requestId=key())
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.request("POST", "/api/units/register", payload), range(6)))
        self.assertTrue(all(r[0] == 201 for r in results), results)
        self.assertTrue(all(r[1] == results[0][1] for r in results))
        self.assertEqual(len(self.trace(sn)["events"]), 1)

    def test_16_csv_roundtrip_and_formula_safety(self):
        wo, sn = self.unit()
        self.assertEqual(self.report(sn, "ASSEMBLY", "FAIL", '=HYPERLINK("demo","fake"), synthetic')[0], 200)
        status, text, headers = self.request("GET", f"/api/events/export.csv?workOrderNo={wo}")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type"))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        cells = [cell for row in rows[1:] for cell in row]
        self.assertTrue(any("HYPERLINK" in cell for cell in cells))
        self.assertFalse(any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in cells))

    def test_17_dashboard_matches_orders(self):
        _, summary, _ = self.request("GET", "/api/dashboard")
        _, orders, _ = self.request("GET", "/api/work-orders")
        self.assertEqual(summary["workOrders"], len(orders["items"]))
        self.assertEqual(summary["units"], sum(w["registeredQuantity"] for w in orders["items"]))
        self.assertEqual(summary["completedUnits"], sum(w["completedQuantity"] for w in orders["items"]))
        self.assertEqual(summary["heldUnits"], sum(w["heldQuantity"] for w in orders["items"]))

    def test_18_event_log_blocks_update_and_delete(self):
        _, sn = self.unit()
        with sqlite3.connect(self.db) as connection:
            for sql in ("UPDATE station_events SET reason='changed' WHERE serial_number=?",
                        "DELETE FROM station_events WHERE serial_number=?"):
                with self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(sql, (sn,))
                connection.rollback()
        self.assertEqual(len(self.trace(sn)["events"]), 1)

    def test_19_data_and_idempotency_survive_restart(self):
        wo = self.wo()
        sn, first, payload = self.register(wo)
        self.assertEqual(self.report(sn, "ASSEMBLY")[0], 200)
        before = self.trace(sn)
        type(self).stop_server()
        type(self).start_server()
        self.assertEqual(self.trace(sn), before)
        replay = self.request("POST", "/api/units/register", payload)
        self.assertEqual(replay[0], 201)
        self.assertEqual(replay[1], first[1])
        self.assertEqual(len(self.trace(sn)["events"]), 2)

    def test_20_event_failure_rolls_back_unit_state(self):
        _, sn = self.unit()
        before = self.trace(sn)
        # Inject a storage failure after the state UPDATE, before the event INSERT.
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TRIGGER test_fail_insert BEFORE INSERT ON station_events "
                               "WHEN NEW.serial_number='" + sn + "' AND NEW.event_type='StationPassed' "
                               "BEGIN SELECT RAISE(ABORT, 'Injected integration test failure'); END")
        request_id = key()
        try:
            status, error, _ = self.report(sn, "ASSEMBLY", request_id=request_id)
            self.assertEqual(status, 500, error)
            self.assertNotIn("Injected", json.dumps(error))
            self.assertEqual(self.trace(sn), before)
        finally:
            with sqlite3.connect(self.db) as connection:
                connection.execute("DROP TRIGGER test_fail_insert")
        # A failed transaction must not poison the retry key.
        self.assertEqual(self.report(sn, "ASSEMBLY", request_id=request_id)[0], 200)
        self.assertEqual(len(self.trace(sn)["events"]), 2)

    def test_21_identifier_normalization_and_missing_key(self):
        wo = self.wo()
        sn = "SN-" + uuid.uuid4().hex[:12].upper()
        payload = dict(workOrderNo=" " + wo.lower() + " ", serialNumber=" " + sn.lower() + " ",
                       operator="Demo", requestId=key())
        status, unit, _ = self.request("POST", "/api/units/register", payload)
        self.assertEqual(status, 201, unit)
        self.assertEqual(unit["serialNumber"], sn)
        self.assertEqual(unit["workOrderNo"], wo)
        payload["requestId"] = key()
        payload["serialNumber"] = sn
        self.assertEqual(self.request("POST", "/api/units/register", payload)[0], 409)
        payload["serialNumber"] += "X"
        del payload["requestId"]
        self.assertEqual(self.request("POST", "/api/units/register", payload)[0], 400)

    def test_22_malformed_requests_have_json_error(self):
        status, error, _ = self.request("GET", "/api/events?limit=abc")
        self.assertEqual(status, 400)
        self.assertIsInstance(error, dict)
        self.assertIn("error", error)
        self.assertEqual(self.request("GET", "/api/events?limit=1001")[0], 400)
        self.assertEqual(self.request("GET", "/api/not-real")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)

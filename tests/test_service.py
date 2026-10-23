from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracegate.db import Database
from tracegate.errors import DomainError
from tracegate.service import TraceGateService


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "tracegate.db")
        self.db.migrate(seed=True)
        self.service = TraceGateService(self.db)
        self.counter = 0

    def request_id(self, label: str) -> str:
        self.counter += 1
        return f"test-{label}-{self.counter:04d}"

    def complete(
        self,
        acn: str,
        station: str,
        *,
        result: str = "PASS",
        reason_code: str | None = None,
        evidence: dict | None = None,
    ) -> dict:
        payload = {
            "request_id": self.request_id(station),
            "acn": acn,
            "station_code": station,
            "result": result,
            "evidence": evidence or {},
        }
        if reason_code:
            payload["reason_code"] = reason_code
        return self.service.complete_station(payload)

    def advance_to_identity(self, acn: str) -> None:
        self.complete(acn, "ST-IN")

    def advance_to_key_parts(self, acn: str, mac: str) -> None:
        self.advance_to_identity(acn)
        self.service.check_mac(
            {
                "request_id": self.request_id("mac"),
                "acn": acn,
                "mac_address": mac,
                "interface_name": "wlan0",
            }
        )
        self.complete(acn, "ST-ID")

    def collect_required_parts(self, acn: str, suffix: str) -> None:
        parts = (
            ("CTRL-A100", f"KPSN-CTRL-{suffix}"),
            ("SENSOR-T200", f"KPSN-SENSOR-{suffix}-A"),
            ("SENSOR-T201", f"KPSN-SENSOR-{suffix}-B"),
        )
        for part_no, kpsn in parts:
            self.service.collect_key_part(
                {
                    "request_id": self.request_id("kp"),
                    "acn": acn,
                    "part_no": part_no,
                    "kpsn": kpsn,
                }
            )

    def test_demo_seed_exposes_route_and_identity(self) -> None:
        response = self.service.get_unit("acn-demo-0001")
        self.assertTrue(response["ok"])
        self.assertEqual(response["unit"]["operation_code"], "REGISTER")
        self.assertEqual(response["unit"]["attempt_no"], 1)
        self.assertEqual(len(response["interfaces"]), 2)

    def test_migration_and_seed_are_repeatable_without_erasing_wip(self) -> None:
        self.complete("ACN-DEMO-0001", "ST-IN")
        self.db.migrate(seed=True)
        unit = self.service.get_unit("ACN-DEMO-0001")["unit"]
        self.assertEqual(unit["current_sequence"], 20)
        migration = self.db.query_one(
            "SELECT count(*) AS n FROM schema_migrations WHERE version=1"
        )
        self.assertEqual(migration["n"], 1)

    def test_station_completion_is_idempotent(self) -> None:
        payload = {
            "request_id": "idem-station-0001",
            "acn": "ACN-DEMO-0001",
            "station_code": "ST-IN",
            "result": "PASS",
            "evidence": {"fixture": "synthetic"},
        }
        first = self.service.complete_station(payload)
        second = self.service.complete_station(payload)
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])
        self.assertTrue(second["idempotent_replay"])
        count = self.db.query_one("SELECT count(*) AS n FROM station_events")
        self.assertEqual(count["n"], 1)

        changed = {**payload, "result": "FAIL"}
        with self.assertRaises(DomainError) as context:
            self.service.complete_station(changed)
        self.assertEqual(context.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_event_identity_changes_if_payload_changes_after_demo_reset(self) -> None:
        request_id = "reset-collision-0001"
        first = self.service.complete_station(
            {
                "request_id": request_id,
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        self.service.reset_demo()
        second = self.service.complete_station(
            {
                "request_id": request_id,
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "FAIL",
                "reason_code": "SYNTHETIC_RESET_TEST",
            }
        )
        self.assertNotEqual(first["event"]["event_id"], second["event"]["event_id"])

    def test_route_is_fail_closed_until_mac_is_verified(self) -> None:
        acn = "ACN-DEMO-0001"
        self.advance_to_identity(acn)
        with self.assertRaises(DomainError) as context:
            self.complete(acn, "ST-ID")
        self.assertEqual(context.exception.code, "REQUIREMENT_NOT_SATISFIED")
        unit = self.service.get_unit(acn)["unit"]
        self.assertEqual(unit["station_code"], "ST-ID")

    def test_valid_format_but_wrong_mac_association_is_rejected(self) -> None:
        acn = "ACN-DEMO-0001"
        self.advance_to_identity(acn)
        with self.assertRaises(DomainError) as context:
            self.service.check_mac(
                {
                    "request_id": "wrong-association-0001",
                    "acn": acn,
                    "mac_address": "02:54:47:00:02:01",
                    "interface_name": "wlan0",
                }
            )
        self.assertEqual(context.exception.code, "ASSOCIATION_MISMATCH")
        attempts = self.db.query_one(
            "SELECT count(*) AS n FROM scan_attempts WHERE reason_code='ASSOCIATION_MISMATCH'"
        )
        self.assertEqual(attempts["n"], 1)

    def test_bom_quantity_and_unique_kpsn_are_enforced(self) -> None:
        acn1 = "ACN-DEMO-0001"
        acn2 = "ACN-DEMO-0002"
        self.advance_to_key_parts(acn1, "02:54:47:00:01:01")
        self.advance_to_key_parts(acn2, "02:54:47:00:02:01")
        self.service.collect_key_part(
            {
                "request_id": "shared-kpsn-bind-0001",
                "acn": acn1,
                "part_no": "CTRL-A100",
                "kpsn": "KPSN-SHARED-0001",
            }
        )
        with self.assertRaises(DomainError) as context:
            self.service.collect_key_part(
                {
                    "request_id": "shared-kpsn-bind-0002",
                    "acn": acn2,
                    "part_no": "CTRL-A100",
                    "kpsn": "KPSN-SHARED-0001",
                }
            )
        self.assertEqual(context.exception.code, "KPSN_ALREADY_BOUND")

        with self.assertRaises(DomainError) as incomplete:
            self.complete(acn1, "ST-KP")
        self.assertEqual(incomplete.exception.code, "QUANTITY_MISMATCH")

    def test_complete_route_including_failure_repair_and_retest(self) -> None:
        acn = "ACN-DEMO-0001"
        self.advance_to_key_parts(acn, "02:54:47:00:01:01")
        self.collect_required_parts(acn, "0001")
        self.complete(acn, "ST-KP")

        failed = self.complete(
            acn,
            "ST-FT",
            result="FAIL",
            reason_code="SYNTHETIC_TEST_FAIL",
            evidence={"description": "Injected demo failure", "measurement": 9.7},
        )
        defect_id = failed["defect"]["defect_id"]
        repair_unit = self.service.get_unit(acn)["unit"]
        self.assertEqual(repair_unit["operation_code"], "REPAIR")
        self.assertEqual(repair_unit["attempt_no"], 1)

        with self.assertRaises(DomainError) as missing_action:
            self.complete(acn, "ST-RP")
        self.assertEqual(missing_action.exception.code, "REPAIR_ACTION_REQUIRED")

        self.service.record_repair_action(
            {
                "request_id": "repair-action-0001",
                "acn": acn,
                "defect_id": defect_id,
                "action_code": "RESEAT_CONNECTOR",
                "notes": "Synthetic repair: reseated demo connector and inspected contacts.",
                "technician": "portfolio-demo",
            }
        )
        repaired = self.complete(acn, "ST-RP")
        self.assertEqual(repaired["transition"]["next_attempt_no"], 2)
        retest_unit = self.service.get_unit(acn)["unit"]
        self.assertEqual(retest_unit["operation_code"], "FUNCTION_TEST")
        self.assertEqual(retest_unit["attempt_no"], 2)
        defect = self.db.query_one("SELECT status FROM defects WHERE defect_id=?", (defect_id,))
        self.assertEqual(defect["status"], "REPAIRED")

        passed_retest = self.complete(acn, "ST-FT")
        self.assertEqual(passed_retest["event"]["attempt_no"], 2)
        self.complete(acn, "ST-PK")
        self.complete(acn, "ST-OUT")
        final = self.service.get_unit(acn)["unit"]
        self.assertEqual(final["status"], "COMPLETE")
        self.assertEqual(final["product_status"], "COMPLETE")
        self.assertIsNone(final["current_sequence"])

        counts = self.db.query_one(
            """
            SELECT (SELECT count(*) FROM station_events) AS events,
                   (SELECT count(*) FROM delivery_outbox) AS outbox
            """
        )
        self.assertEqual(counts["events"], 8)
        self.assertEqual(counts["events"], counts["outbox"])
        trace = self.service.get_trace(acn)
        self.assertEqual(len(trace["station_events"]), 8)
        self.assertTrue(any(item["type"] == "repair_action" for item in trace["timeline"]))
        defect_item = next(
            item for item in trace["timeline"] if item["id"] == defect_id
        )
        repair_item = next(
            item
            for item in trace["timeline"]
            if item["type"] == "repair_action"
        )
        repair_pass_item = next(
            item
            for item in trace["timeline"]
            if item["type"] == "station_event" and item["summary"] == "ST-RP PASS"
        )
        self.assertEqual(defect_item["summary"], "SYNTHETIC_TEST_FAIL OPENED")
        self.assertLess(defect_item["audit_sequence"], repair_item["audit_sequence"])
        self.assertLess(repair_item["audit_sequence"], repair_pass_item["audit_sequence"])
        current_defect = next(
            item for item in trace["defects"] if item["defect_id"] == defect_id
        )
        self.assertEqual(current_defect["status"], "REPAIRED")

    def test_route_violation_does_not_change_wip(self) -> None:
        with self.assertRaises(DomainError) as context:
            self.complete("ACN-DEMO-0001", "ST-FT")
        self.assertEqual(context.exception.code, "ROUTE_VIOLATION")
        unit = self.service.get_unit("ACN-DEMO-0001")["unit"]
        self.assertEqual(unit["current_sequence"], 10)
        self.assertEqual(self.service.metrics()["station_events"], {})


if __name__ == "__main__":
    unittest.main()

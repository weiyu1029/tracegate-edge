from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tracegate.db as db_module
from tracegate.db import Database
from tracegate.errors import DomainError
from tracegate.mock_mes import MockMesStore
from tracegate.outbox import OutboxDispatcher
from tracegate.service import TraceGateService


class LocalDispatcher(OutboxDispatcher):
    def __init__(self, database: Database, store: MockMesStore, **kwargs):
        super().__init__(database, "local://synthetic-mes", **kwargs)
        self.store = store

    def _send(self, item):
        self.store.receive(json.loads(item["payload"]), item["event_id"])


class HardeningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.db = Database(base / "edge.db")
        self.db.migrate(seed=True)
        self.service = TraceGateService(self.db)
        self.store = MockMesStore(base / "mes.db")

    def create_event(self, request_id: str = "hardening-event-0001") -> str:
        result = self.service.complete_station(
            {
                "request_id": request_id,
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        return result["event"]["event_id"]

    def assert_domain_code(self, expected: str, callback) -> None:
        with self.assertRaises(DomainError) as context:
            callback()
        self.assertEqual(context.exception.code, expected)

    def test_mutations_fail_closed_for_planning_and_disposition_states(self) -> None:
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE work_orders SET status='PLANNED' WHERE work_order_id='WO-DEMO-1001'"
            )
        self.assert_domain_code(
            "WORK_ORDER_NOT_RELEASED",
            lambda: self.service.complete_station(
                {
                    "request_id": "planned-work-order-0001",
                    "acn": "ACN-DEMO-0001",
                    "station_code": "ST-IN",
                    "result": "PASS",
                }
            ),
        )

        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE work_orders SET status='RELEASED' WHERE work_order_id='WO-DEMO-1001'"
            )
            connection.execute(
                "UPDATE routes SET status='RETIRED' WHERE route_id='ROUTE-DEMO-A'"
            )
        self.assert_domain_code(
            "ROUTE_NOT_ACTIVE",
            lambda: self.service.collect_key_part(
                {
                    "request_id": "retired-route-0001",
                    "acn": "ACN-DEMO-0002",
                    "part_no": "CTRL-A100",
                    "kpsn": "KPSN-RETIRED-0001",
                }
            ),
        )

        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE routes SET status='ACTIVE' WHERE route_id='ROUTE-DEMO-A'"
            )
            connection.execute(
                "UPDATE products SET status='HOLD' WHERE acn='ACN-DEMO-0003'"
            )
            connection.execute(
                "UPDATE wip_state SET status='HOLD' WHERE acn='ACN-DEMO-0003'"
            )
        self.assert_domain_code(
            "UNIT_ON_HOLD",
            lambda: self.service.check_mac(
                {
                    "request_id": "held-unit-check-0001",
                    "acn": "ACN-DEMO-0003",
                    "mac_address": "02:54:47:00:03:01",
                    "interface_name": "wlan0",
                }
            ),
        )

        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE products SET status='SCRAPPED' WHERE acn='ACN-DEMO-0002'"
            )
        self.assert_domain_code(
            "UNIT_NOT_ACTIVE",
            lambda: self.service.record_repair_action(
                {
                    "request_id": "scrapped-repair-0001",
                    "acn": "ACN-DEMO-0002",
                    "defect_id": "def-does-not-exist",
                    "action_code": "INSPECT",
                    "notes": "Synthetic inspection",
                }
            ),
        )

    def test_unknown_mac_and_wrong_interface_are_distinct_failures(self) -> None:
        self.service.complete_station(
            {
                "request_id": "identity-intake-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        self.assert_domain_code(
            "UNKNOWN_MAC",
            lambda: self.service.check_mac(
                {
                    "request_id": "unknown-mac-check-0001",
                    "acn": "ACN-DEMO-0001",
                    "mac_address": "02:54:47:FF:FF:FF",
                    "interface_name": "wlan0",
                }
            ),
        )
        self.assert_domain_code(
            "WRONG_INTERFACE",
            lambda: self.service.check_mac(
                {
                    "request_id": "wrong-interface-0001",
                    "acn": "ACN-DEMO-0001",
                    "mac_address": "02:54:47:00:01:01",
                    "interface_name": "eth0",
                }
            ),
        )

    def test_unapproved_part_and_bom_over_quantity_are_rejected(self) -> None:
        acn = "ACN-DEMO-0001"
        self.service.complete_station(
            {
                "request_id": "bom-intake-0001",
                "acn": acn,
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        self.service.check_mac(
            {
                "request_id": "bom-mac-check-0001",
                "acn": acn,
                "mac_address": "02:54:47:00:01:01",
                "interface_name": "wlan0",
            }
        )
        self.service.complete_station(
            {
                "request_id": "bom-identity-0001",
                "acn": acn,
                "station_code": "ST-ID",
                "result": "PASS",
            }
        )
        self.assert_domain_code(
            "INVALID_SUBSTITUTE",
            lambda: self.service.collect_key_part(
                {
                    "request_id": "unapproved-part-0001",
                    "acn": acn,
                    "part_no": "UNLISTED-X999",
                    "kpsn": "KPSN-UNAPPROVED-0001",
                }
            ),
        )
        self.service.collect_key_part(
            {
                "request_id": "controller-bind-0001",
                "acn": acn,
                "part_no": "CTRL-A100",
                "kpsn": "KPSN-CONTROLLER-0001",
            }
        )
        self.assert_domain_code(
            "QUANTITY_EXCEEDED",
            lambda: self.service.collect_key_part(
                {
                    "request_id": "controller-overqty-0001",
                    "acn": acn,
                    "part_no": "CTRL-A101",
                    "kpsn": "KPSN-CONTROLLER-0002",
                }
            ),
        )

    def test_overlapping_bom_groups_fail_closed_instead_of_picking_one(self) -> None:
        acn = "ACN-DEMO-0001"
        self.service.complete_station(
            {
                "request_id": "ambiguous-bom-intake-0001",
                "acn": acn,
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        self.service.check_mac(
            {
                "request_id": "ambiguous-bom-mac-0001",
                "acn": acn,
                "mac_address": "02:54:47:00:01:01",
                "interface_name": "wlan0",
            }
        )
        self.service.complete_station(
            {
                "request_id": "ambiguous-bom-identity-0001",
                "acn": acn,
                "station_code": "ST-ID",
                "result": "PASS",
            }
        )
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO kp_group_members(group_id,part_no,substitute_rank) VALUES(?,?,?)",
                ("KP-SENSOR", "CTRL-A100", 2),
            )

        with self.assertRaises(DomainError) as context:
            self.service.collect_key_part(
                {
                    "request_id": "ambiguous-bom-bind-0001",
                    "acn": acn,
                    "part_no": "CTRL-A100",
                    "kpsn": "KPSN-AMBIGUOUS-0001",
                }
            )
        self.assertEqual(context.exception.code, "AMBIGUOUS_BOM_GROUP")
        self.assertEqual(
            context.exception.details["group_ids"],
            ["KP-CONTROLLER", "KP-SENSOR"],
        )
        self.assertEqual(
            self.db.query_one(
                "SELECT count(*) AS n FROM key_part_bindings WHERE kpsn=?",
                ("KPSN-AMBIGUOUS-0001",),
            )["n"],
            0,
        )

    def test_station_result_and_reason_code_cannot_contradict_each_other(self) -> None:
        cases = (
            ("reason-fail-ok-0001", "FAIL", "OK"),
            ("reason-pass-failure-0001", "PASS", "SYNTHETIC_TEST_NG"),
            ("reason-pass-malformed-0001", "PASS", "NOT SAFE!"),
        )
        for request_id, result, reason_code in cases:
            with self.subTest(result=result, reason_code=reason_code):
                with self.assertRaises(DomainError) as context:
                    self.service.complete_station(
                        {
                            "request_id": request_id,
                            "acn": "ACN-DEMO-0001",
                            "station_code": "ST-IN",
                            "result": result,
                            "reason_code": reason_code,
                        }
                    )
                self.assertEqual(context.exception.code, "INVALID_REASON_CODE")

        unit = self.service.get_unit("ACN-DEMO-0001")["unit"]
        self.assertEqual(unit["current_sequence"], 10)
        self.assertEqual(self.service.metrics()["station_events"], {})

        accepted = self.service.complete_station(
            {
                "request_id": "reason-default-pass-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
            }
        )
        self.assertEqual(accepted["event"]["reason_code"], "OK")

        failed = self.service.complete_station(
            {
                "request_id": "reason-default-fail-0001",
                "acn": "ACN-DEMO-0002",
                "station_code": "ST-IN",
                "result": "FAIL",
            }
        )
        self.assertEqual(failed["event"]["reason_code"], "STATION_REJECT")

    def test_trace_timeline_includes_rejected_attempt_and_defect(self) -> None:
        self.assert_domain_code(
            "ROUTE_VIOLATION",
            lambda: self.service.complete_station(
                {
                    "request_id": "rejected-station-0001",
                    "acn": "ACN-DEMO-0001",
                    "station_code": "ST-FT",
                    "result": "PASS",
                }
            ),
        )
        self.service.complete_station(
            {
                "request_id": "accepted-failure-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "FAIL",
                "reason_code": "SYNTHETIC_INTAKE_FAIL",
            }
        )
        timeline = self.service.get_trace("ACN-DEMO-0001")["timeline"]
        kinds = [item["type"] for item in timeline]
        self.assertIn("rejected_scan", kinds)
        self.assertIn("defect", kinds)
        self.assertEqual(kinds.count("rejected_scan"), 1)

    def test_same_millisecond_events_keep_local_commit_order(self) -> None:
        fixed_time = "2026-01-01T00:00:00.000Z"
        with mock.patch("tracegate.service.utc_now", return_value=fixed_time):
            first = self.service.complete_station(
                {
                    "request_id": "same-ms-intake-0001",
                    "acn": "ACN-DEMO-0001",
                    "station_code": "ST-IN",
                    "result": "PASS",
                }
            )["event"]["event_id"]
            self.service.check_mac(
                {
                    "request_id": "same-ms-mac-0001",
                    "acn": "ACN-DEMO-0001",
                    "mac_address": "02:54:47:00:01:01",
                    "interface_name": "wlan0",
                }
            )
            second = self.service.complete_station(
                {
                    "request_id": "same-ms-identity-0001",
                    "acn": "ACN-DEMO-0001",
                    "station_code": "ST-ID",
                    "result": "PASS",
                }
            )["event"]["event_id"]

        trace = self.service.get_trace("ACN-DEMO-0001")
        trace_ids = [
            event["event_id"]
            for event in trace["station_events"]
        ]
        self.assertEqual(trace_ids, [first, second])
        self.assertEqual(
            [item["type"] for item in trace["timeline"]],
            ["station_event", "verification", "station_event"],
        )
        sequences = [item["audit_sequence"] for item in trace["timeline"]]
        self.assertEqual(sequences, sorted(sequences))
        claimed = LocalDispatcher(self.db, self.store).claim_batch()
        self.assertEqual([item["event_id"] for item in claimed], [first, second])

    def test_outbox_statuses_stay_consistent_across_claim_replay_and_delivery(self) -> None:
        event_id = self.create_event()
        dispatcher = LocalDispatcher(self.db, self.store, lease_seconds=5)
        claimed = dispatcher.claim_batch()
        self.assertEqual(len(claimed), 1)
        states = self.db.query_one(
            """
            SELECT o.status AS outbox_status,e.delivery_status AS event_status
            FROM delivery_outbox o JOIN station_events e USING(event_id)
            WHERE o.event_id=?
            """,
            (event_id,),
        )
        self.assertEqual(states, {"outbox_status": "IN_FLIGHT", "event_status": "IN_FLIGHT"})

        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE delivery_outbox SET locked_until=0 WHERE event_id=?", (event_id,)
            )
        replay = self.service.replay_outbox(
            {"request_id": "hardening-replay-0001", "event_id": event_id}
        )
        self.assertEqual(replay["requeued"], 1)
        pending = self.db.query_one(
            """
            SELECT o.status AS outbox_status,e.delivery_status AS event_status
            FROM delivery_outbox o JOIN station_events e USING(event_id)
            WHERE o.event_id=?
            """,
            (event_id,),
        )
        self.assertEqual(pending, {"outbox_status": "PENDING", "event_status": "PENDING"})
        delivered = dispatcher.deliver_once()
        self.assertEqual(delivered["delivered"], 1)
        final = self.db.query_one(
            """
            SELECT o.status AS outbox_status,e.delivery_status AS event_status
            FROM delivery_outbox o JOIN station_events e USING(event_id)
            WHERE o.event_id=?
            """,
            (event_id,),
        )
        self.assertEqual(final, {"outbox_status": "DELIVERED", "event_status": "DELIVERED"})

    def test_expired_worker_cannot_overwrite_newer_lease(self) -> None:
        event_id = self.create_event()
        worker_a = LocalDispatcher(self.db, self.store, lease_seconds=5)
        worker_b = LocalDispatcher(self.db, self.store, lease_seconds=5)
        claim_a = worker_a.claim_batch()[0]
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE delivery_outbox SET locked_until=0 WHERE event_id=?", (event_id,)
            )
        claim_b = worker_b.claim_batch()[0]
        self.assertEqual(worker_a._mark_failure(claim_a, RuntimeError("late")), "STALE")
        worker_b._send(claim_b)
        self.assertTrue(worker_b._mark_delivered(claim_b))
        row = self.db.query_one(
            """
            SELECT o.status,o.attempts,e.delivery_status
            FROM delivery_outbox o JOIN station_events e USING(event_id)
            WHERE o.event_id=?
            """,
            (event_id,),
        )
        self.assertEqual(row["status"], "DELIVERED")
        self.assertEqual(row["delivery_status"], "DELIVERED")
        self.assertEqual(row["attempts"], 1)

    def test_committed_outbox_survives_service_and_dispatcher_restart(self) -> None:
        event_id = self.create_event()
        reopened_db = Database(self.db.path)
        restarted = LocalDispatcher(reopened_db, self.store)
        self.assertEqual(restarted.deliver_once()["delivered"], 1)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(
            reopened_db.query_one(
                "SELECT status FROM delivery_outbox WHERE event_id=?", (event_id,)
            )["status"],
            "DELIVERED",
        )

    def test_station_transaction_rolls_back_if_outbox_insert_fails(self) -> None:
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                """
                CREATE TRIGGER synthetic_outbox_failure
                BEFORE INSERT ON delivery_outbox
                BEGIN SELECT RAISE(ABORT, 'synthetic outbox failure'); END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.complete_station(
                {
                    "request_id": "rollback-injection-0001",
                    "acn": "ACN-DEMO-0001",
                    "station_code": "ST-IN",
                    "result": "PASS",
                }
            )
        counts = self.db.query_one(
            """
            SELECT (SELECT count(*) FROM station_events) AS events,
                   (SELECT count(*) FROM delivery_outbox) AS outbox,
                   (SELECT count(*) FROM idempotency_records) AS idempotency,
                   (SELECT count(*) FROM audit_log) AS audit
            """
        )
        self.assertEqual(
            counts,
            {"events": 0, "outbox": 0, "idempotency": 0, "audit": 0},
        )
        unit = self.service.get_unit("ACN-DEMO-0001")["unit"]
        self.assertEqual(unit["current_sequence"], 10)
        self.assertEqual(unit["wip_version"], 1)

    def test_demo_reset_is_atomic_if_reseeding_fails(self) -> None:
        event_id = self.create_event("reset-atomic-event-0001")
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                """
                CREATE TRIGGER synthetic_seed_failure
                BEFORE INSERT ON routes
                BEGIN SELECT RAISE(ABORT, 'synthetic seed failure'); END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.reset_demo()
        unit = self.service.get_unit("ACN-DEMO-0001")["unit"]
        self.assertEqual(unit["current_sequence"], 20)
        self.assertEqual(
            self.db.query_one(
                "SELECT count(*) AS n FROM station_events WHERE event_id=?", (event_id,)
            )["n"],
            1,
        )

    def test_failed_schema_migration_rolls_back_schema_and_version_record(self) -> None:
        path = Path(self.temp.name) / "migration-failure.db"
        failing = (
            (
                99,
                "synthetic_failure",
                "CREATE TABLE should_rollback(value TEXT); INVALID SQL;",
            ),
        )
        with mock.patch.object(db_module, "MIGRATIONS", failing):
            with self.assertRaises(sqlite3.OperationalError):
                Database(path).migrate(seed=False)
        with contextlib.closing(sqlite3.connect(path)) as connection:
            partial = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
            ).fetchone()
            version = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version=99"
            ).fetchone()
        self.assertIsNone(partial)
        self.assertIsNone(version)

    def test_mock_store_migrates_legacy_receipts_without_dropping_them(self) -> None:
        path = Path(self.temp.name) / "legacy-mes.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE receipts(
                    event_id TEXT PRIMARY KEY,barcode TEXT,station TEXT,step TEXT,
                    decision TEXT,received_at TEXT,payload TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                ("legacy-1", "DEMO", "S1", "10", "PASS", "yesterday", "{}"),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = MockMesStore(path)
        self.assertEqual(migrated.count(), 0)
        with contextlib.closing(sqlite3.connect(path)) as check:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("receipts", tables)
            self.assertIn("receipts_legacy_v1_1", tables)
            self.assertEqual(
                check.execute("SELECT count(*) FROM receipts_legacy_v1_1").fetchone()[0],
                1,
            )

    def test_mock_store_rebuilds_receipts_when_event_id_is_not_unique(self) -> None:
        path = Path(self.temp.name) / "unsafe-mes.db"
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE receipts(
                    event_id TEXT,idempotency_key TEXT,acn TEXT,station_code TEXT,
                    result TEXT,payload TEXT,received_at TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                ("legacy-1", "legacy-1", "DEMO", "S1", "PASS", "{}", "then"),
            )

        migrated = MockMesStore(path)
        event = {
            "event_id": "event-1",
            "acn": "ACN-DEMO-0001",
            "station_code": "ST-IN",
            "result": "PASS",
            "occurred_at": "2026-01-01T00:00:00.000Z",
        }
        self.assertFalse(migrated.receive(event, "event-1")["duplicate"])
        self.assertTrue(migrated.receive(event, "event-1")["duplicate"])
        self.assertEqual(migrated.count(), 1)

        with contextlib.closing(sqlite3.connect(path)) as check:
            primary_key = {
                row[1]: row[5] for row in check.execute("PRAGMA table_info(receipts)")
            }
            self.assertEqual(primary_key["event_id"], 1)
            self.assertEqual(
                check.execute("SELECT count(*) FROM receipts_legacy_v1_1").fetchone()[0],
                1,
            )

    def test_mock_store_schema_migration_rolls_back_legacy_rename(self) -> None:
        path = Path(self.temp.name) / "atomic-mes.db"
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE receipts(event_id TEXT PRIMARY KEY,payload TEXT)"
            )
            connection.execute("INSERT INTO receipts VALUES('legacy-1','{}')")
            connection.commit()

            def deny_new_receipts_table(
                action: int,
                argument_one: str | None,
                argument_two: str | None,
                database_name: str | None,
                trigger_name: str | None,
            ) -> int:
                del argument_two, database_name, trigger_name
                if action == sqlite3.SQLITE_CREATE_TABLE and argument_one == "receipts":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(deny_new_receipts_table)
            with self.assertRaises(sqlite3.DatabaseError):
                MockMesStore._migrate_receipts(connection)
        finally:
            connection.close()

        with contextlib.closing(sqlite3.connect(path)) as check:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("receipts", tables)
            self.assertNotIn("receipts_legacy_v1_1", tables)
            self.assertEqual(
                check.execute("SELECT count(*) FROM receipts").fetchone()[0], 1
            )

    def test_demo_reset_clears_receipts_but_preserves_outage_state(self) -> None:
        self.store.set_online(False)
        self.store.reset()
        self.assertFalse(self.store.online())


if __name__ == "__main__":
    unittest.main()

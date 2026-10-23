from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tracegate.db import Database
from tracegate.mock_mes import MockMesConflict, MockMesStore
from tracegate.outbox import OutboxDispatcher, OutboxWorker
from tracegate.service import TraceGateService


class LocalDispatcher(OutboxDispatcher):
    """Exercises the durable dispatcher without opening a sandboxed socket."""

    def __init__(self, database: Database, store: MockMesStore, **kwargs):
        super().__init__(database, "local://synthetic-mes", **kwargs)
        self.store = store

    def _send(self, item):
        event = json.loads(item["payload"])
        self.store.receive(event, item["event_id"])


class OutboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.db = Database(base / "edge.db")
        self.db.migrate(seed=True)
        self.service = TraceGateService(self.db)
        self.store = MockMesStore(base / "mes.db")
        self.dispatcher = LocalDispatcher(
            self.db,
            self.store,
            timeout_seconds=1,
            max_attempts=2,
        )

    def _create_event(self) -> str:
        response = self.service.complete_station(
            {
                "request_id": "outbox-event-0001",
                "acn": "ACN-DEMO-0001",
                "station_code": "ST-IN",
                "result": "PASS",
                "evidence": {"test": True},
            }
        )
        return response["event"]["event_id"]

    def test_delivery_is_durable_and_upstream_is_idempotent(self) -> None:
        event_id = self._create_event()
        result = self.dispatcher.deliver_once()
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(self.store.count(), 1)
        edge = self.db.query_one(
            "SELECT status,attempts FROM delivery_outbox WHERE event_id=?", (event_id,)
        )
        self.assertEqual(edge["status"], "DELIVERED")
        self.assertEqual(edge["attempts"], 1)

        # Simulate an acknowledgement lost after the receiver committed. The
        # second POST is accepted as a duplicate, and receipt count remains one.
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE delivery_outbox SET status='PENDING',next_attempt_at=0,
                    locked_until=0,delivered_at=NULL WHERE event_id=?
                """,
                (event_id,),
            )
        again = self.dispatcher.deliver_once()
        self.assertEqual(again["delivered"], 1)
        self.assertEqual(self.store.count(), 1)

    def test_outage_retries_then_dead_letters_and_can_be_replayed(self) -> None:
        event_id = self._create_event()
        self.store.set_online(False)
        first = self.dispatcher.deliver_once()
        self.assertEqual(first["retried"], 1)
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE delivery_outbox SET next_attempt_at=0 WHERE event_id=?",
                (event_id,),
            )
        second = self.dispatcher.deliver_once()
        self.assertEqual(second["dead_letter"], 1)
        row = self.db.query_one(
            "SELECT status,attempts FROM delivery_outbox WHERE event_id=?", (event_id,)
        )
        self.assertEqual(row["status"], "DEAD_LETTER")
        self.assertEqual(row["attempts"], 2)

        replay = self.service.replay_outbox(
            {"request_id": "manual-replay-0001", "event_id": event_id}
        )
        self.assertEqual(replay["requeued"], 1)
        self.store.set_online(True)
        delivered = self.dispatcher.deliver_once()
        self.assertEqual(delivered["delivered"], 1)
        self.assertEqual(self.store.count(), 1)

    def test_upstream_rejects_same_event_id_with_different_payload(self) -> None:
        event_id = self._create_event()
        row = self.db.query_one(
            "SELECT payload FROM delivery_outbox WHERE event_id=?", (event_id,)
        )
        event = json.loads(row["payload"])
        self.store.receive(event, event_id)
        changed = {**event, "result": "FAIL"}
        with self.assertRaises(MockMesConflict):
            self.store.receive(changed, event_id)
        self.assertEqual(self.store.count(), 1)

    def test_manual_replay_does_not_steal_an_active_lease(self) -> None:
        event_id = self._create_event()
        claimed = self.dispatcher.claim_batch()
        self.assertEqual([item["event_id"] for item in claimed], [event_id])

        replay = self.service.replay_outbox(
            {"request_id": "manual-active-lease-0001", "event_id": event_id}
        )
        self.assertEqual(replay["requeued"], 0)
        row = self.db.query_one(
            "SELECT status FROM delivery_outbox WHERE event_id=?", (event_id,)
        )
        self.assertEqual(row["status"], "IN_FLIGHT")

        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE delivery_outbox SET locked_until=0 WHERE event_id=?",
                (event_id,),
            )
        expired = self.service.replay_outbox(
            {"request_id": "manual-expired-lease-0001", "event_id": event_id}
        )
        self.assertEqual(expired["requeued"], 1)

    def test_delivery_barrier_fails_closed_while_reset_would_be_busy(self) -> None:
        worker = OutboxWorker(self.dispatcher)
        self.assertTrue(worker.quiesce(timeout=0))
        self.assertFalse(worker.quiesce(timeout=0))
        worker.resume()
        self.assertTrue(worker.quiesce(timeout=0))
        worker.resume()


if __name__ == "__main__":
    unittest.main()

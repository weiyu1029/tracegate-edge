"""Durable store-and-forward delivery with leases and bounded backoff."""

from __future__ import annotations

import random
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .db import Database
from .util import utc_now


class OutboxDispatcher:
    """Moves committed station events to an idempotent upstream receiver."""

    def __init__(
        self,
        database: Database,
        mes_url: str,
        *,
        timeout_seconds: float = 4.0,
        max_attempts: int = 8,
        batch_size: int = 20,
        lease_seconds: float = 30.0,
    ):
        self.db = database
        self.mes_url = mes_url
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.batch_size = max(1, min(batch_size, 200))
        # A claim covers the whole sequential batch. Keep it valid for the
        # worst-case request time of every item, plus one second per item.
        minimum_batch_lease = self.batch_size * (self.timeout_seconds + 1.0)
        self.lease_seconds = max(5.0, lease_seconds, minimum_batch_lease)

    def claim_batch(self) -> list[dict[str, Any]]:
        now = time.time()
        with self.db.transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT o.rowid AS local_sequence,o.*,a.audit_sequence
                FROM delivery_outbox o
                LEFT JOIN audit_log a
                  ON a.record_type='station_event' AND a.record_id=o.event_id
                WHERE (
                    o.status IN ('PENDING','RETRY') AND o.next_attempt_at <= ?
                ) OR (
                    o.status='IN_FLIGHT' AND o.locked_until <= ?
                )
                ORDER BY coalesce(a.audit_sequence,o.rowid),o.rowid LIMIT ?
                """,
                (now, now, self.batch_size),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE delivery_outbox
                    SET status='IN_FLIGHT',locked_until=?
                    WHERE event_id=? AND (
                        status IN ('PENDING','RETRY') OR
                        (status='IN_FLIGHT' AND locked_until <= ?)
                    )
                    """,
                    (now + self.lease_seconds, row["event_id"], now),
                )
                if cursor.rowcount:
                    item = dict(row)
                    item["status"] = "IN_FLIGHT"
                    item["locked_until"] = now + self.lease_seconds
                    claimed.append(item)
                    connection.execute(
                        "UPDATE station_events SET delivery_status='IN_FLIGHT' WHERE event_id=?",
                        (row["event_id"],),
                    )
        return claimed

    def deliver_once(self) -> dict[str, int]:
        results = {
            "claimed": 0,
            "delivered": 0,
            "retried": 0,
            "dead_letter": 0,
            "stale": 0,
        }
        for item in self.claim_batch():
            results["claimed"] += 1
            try:
                self._send(item)
            except Exception as exc:  # Network and HTTP failures share durable retry.
                status = self._mark_failure(item, exc)
                if status == "STALE":
                    results["stale"] += 1
                else:
                    results["dead_letter" if status == "DEAD_LETTER" else "retried"] += 1
            else:
                if self._mark_delivered(item):
                    results["delivered"] += 1
                else:
                    results["stale"] += 1
        return results

    def _send(self, item: dict[str, Any]) -> None:
        event_id = item["event_id"]
        request = urllib.request.Request(
            self.mes_url,
            data=item["payload"].encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event_id,
                "User-Agent": "TraceGate-Edge/0.2",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"MES returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            body = exc.read(512).decode("utf-8", errors="replace")
            raise RuntimeError(f"MES returned HTTP {exc.code}: {body}") from exc

    def _mark_delivered(self, item: dict[str, Any]) -> bool:
        delivered_at = utc_now()
        with self.db.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE delivery_outbox
                SET status='DELIVERED',attempts=attempts+1,locked_until=0,
                    last_error=NULL,delivered_at=?
                WHERE event_id=? AND status='IN_FLIGHT' AND locked_until=?
                """,
                (delivered_at, item["event_id"], item["locked_until"]),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """
                UPDATE station_events
                SET delivery_status='DELIVERED',delivered_at=?
                WHERE event_id=?
                """,
                (delivered_at, item["event_id"]),
            )
        return True

    def _mark_failure(self, item: dict[str, Any], error: Exception) -> str:
        attempts = int(item["attempts"]) + 1
        status = "DEAD_LETTER" if attempts >= self.max_attempts else "RETRY"
        # Exponential delay with light jitter avoids a synchronized retry storm.
        delay = min(300.0, float(2 ** min(attempts, 8)))
        delay += random.uniform(0, min(1.0, delay * 0.1))
        next_attempt_at = 0 if status == "DEAD_LETTER" else time.time() + delay
        safe_error = f"{type(error).__name__}: {error}"[:500]
        with self.db.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE delivery_outbox
                SET status=?,attempts=?,next_attempt_at=?,locked_until=0,
                    last_error=?
                WHERE event_id=? AND status='IN_FLIGHT' AND locked_until=?
                """,
                (
                    status,
                    attempts,
                    next_attempt_at,
                    safe_error,
                    item["event_id"],
                    item["locked_until"],
                ),
            )
            if cursor.rowcount != 1:
                return "STALE"
            connection.execute(
                "UPDATE station_events SET delivery_status=? WHERE event_id=?",
                (status, item["event_id"]),
            )
        return status


class OutboxWorker:
    """Small cooperative worker used by the edge CLI."""

    def __init__(self, dispatcher: OutboxDispatcher, *, interval_seconds: float = 1.0):
        self.dispatcher = dispatcher
        self.interval_seconds = max(0.05, interval_seconds)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._delivery_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="tracegate-outbox", daemon=True
        )
        self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def quiesce(self, *, timeout: float = 5.0) -> bool:
        """Acquire the delivery barrier, or return False without changing state."""

        return self._delivery_lock.acquire(timeout=max(0.0, timeout))

    def resume(self) -> None:
        """Release a delivery barrier previously acquired by ``quiesce``."""

        self._delivery_lock.release()

    def stop(self, *, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
            try:
                with self._delivery_lock:
                    result = self.dispatcher.deliver_once()
                if result["claimed"] >= self.dispatcher.batch_size:
                    self._wake.set()
            except sqlite3.Error as exc:
                print(f"[outbox] database error: {exc}", flush=True)
            except Exception as exc:
                print(f"[outbox] unexpected error: {exc}", flush=True)

"""Manufacturing domain service for the synthetic TraceGate reference system."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from typing import Any

from . import __version__
from .db import Database
from .errors import DomainError
from .util import (
    ACN_RE,
    KPSN_RE,
    canonical_json,
    new_id,
    normalize_acn,
    normalize_kpsn,
    normalize_mac,
    payload_hash,
    require_request_id,
    stable_hash,
    utc_now,
)


class TraceGateService:
    """Coordinates route validation, trace writes, and durable delivery."""

    def __init__(self, database: Database):
        self.db = database
        self.started_at = time.monotonic()

    # ------------------------------------------------------------------ reads
    def health(self) -> dict[str, Any]:
        try:
            version = self.db.query_one(
                "SELECT max(version) AS version FROM schema_migrations"
            )
            pending = self.db.query_one(
                """
                SELECT
                  sum(CASE WHEN status IN ('PENDING','RETRY','IN_FLIGHT') THEN 1 ELSE 0 END)
                    AS pending,
                  sum(CASE WHEN status='DEAD_LETTER' THEN 1 ELSE 0 END) AS dead_letter
                FROM delivery_outbox
                """
            ) or {"pending": 0, "dead_letter": 0}
            return {
                "ok": True,
                "status": "degraded" if (pending["dead_letter"] or 0) else "healthy",
                "service": "tracegate-edge",
                "version": __version__,
                "schema_version": version["version"] if version else None,
                "database": "ok",
                "outbox": {
                    "pending": pending["pending"] or 0,
                    "dead_letter": pending["dead_letter"] or 0,
                },
                "uptime_seconds": round(time.monotonic() - self.started_at, 3),
                "time": utc_now(),
            }
        except sqlite3.Error as exc:
            return {
                "ok": False,
                "status": "unhealthy",
                "service": "tracegate-edge",
                "version": __version__,
                "database": "error",
                "error": str(exc),
                "time": utc_now(),
            }

    def metrics(self) -> dict[str, Any]:
        product_counts = self.db.query_all(
            "SELECT status, count(*) AS count FROM products GROUP BY status ORDER BY status"
        )
        wip_counts = self.db.query_all(
            "SELECT status, count(*) AS count FROM wip_state GROUP BY status ORDER BY status"
        )
        event_counts = self.db.query_all(
            "SELECT result, count(*) AS count FROM station_events GROUP BY result ORDER BY result"
        )
        outbox_counts = self.db.query_all(
            "SELECT status, count(*) AS count FROM delivery_outbox GROUP BY status ORDER BY status"
        )
        quality = self.db.query_one(
            """
            SELECT
              count(*) AS defect_count,
              sum(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_defects
            FROM defects
            """
        ) or {"defect_count": 0, "open_defects": 0}
        retry = self.db.query_one(
            """
            SELECT coalesce(sum(attempts),0) AS total_delivery_attempts,
                   coalesce(max(attempts),0) AS max_delivery_attempts
            FROM delivery_outbox
            """
        )
        return {
            "ok": True,
            "products": {row["status"]: row["count"] for row in product_counts},
            "wip": {row["status"]: row["count"] for row in wip_counts},
            "station_events": {row["result"]: row["count"] for row in event_counts},
            "outbox": {row["status"]: row["count"] for row in outbox_counts},
            "quality": {
                "defects": quality["defect_count"] or 0,
                "open_defects": quality["open_defects"] or 0,
            },
            "delivery": retry,
            "generated_at": utc_now(),
        }

    def get_unit(self, raw_acn: Any) -> dict[str, Any]:
        acn = normalize_acn(raw_acn)
        if not ACN_RE.fullmatch(acn):
            raise DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
        with contextlib.closing(self.db.connect()) as connection:
            unit = self._load_unit(connection, acn)
            if not unit:
                raise DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
            interfaces = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT interface_name,mac_address,status,bound_at
                    FROM network_interfaces WHERE acn=? ORDER BY interface_name
                    """,
                    (acn,),
                ).fetchall()
            ]
            bindings = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT kpsn,part_no,group_id,station_code,step_sequence,
                           attempt_no,bound_at
                    FROM key_part_bindings WHERE parent_acn=? ORDER BY bound_at,kpsn
                    """,
                    (acn,),
                ).fetchall()
            ]
            requirements = self._requirement_progress(connection, unit)
            defects = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT defect_id,code,description,status,created_at,closed_at
                    FROM defects WHERE acn=? ORDER BY created_at DESC
                    """,
                    (acn,),
                ).fetchall()
            ]
        return {
            "ok": True,
            "unit": unit,
            "interfaces": interfaces,
            "key_parts": bindings,
            "requirements": requirements,
            "defects": defects,
        }

    def get_trace(self, raw_acn: Any) -> dict[str, Any]:
        acn = normalize_acn(raw_acn)
        if not ACN_RE.fullmatch(acn):
            raise DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
        if not self.db.query_one("SELECT 1 AS found FROM products WHERE acn=?", (acn,)):
            raise DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
        events = self.db.query_all(
            """
            SELECT e.rowid AS local_sequence,a.audit_sequence,
                   e.event_id,e.request_id,e.sequence,
                   e.station_code,e.operation_code,
                   e.attempt_no,e.result,e.reason_code,e.message,e.evidence_json,
                   e.occurred_at,coalesce(o.status,e.delivery_status) AS delivery_status,
                   coalesce(o.delivered_at,e.delivered_at) AS delivered_at
            FROM station_events e
            LEFT JOIN delivery_outbox o ON o.event_id=e.event_id
            LEFT JOIN audit_log a
              ON a.record_type='station_event' AND a.record_id=e.event_id
            WHERE e.acn=? ORDER BY e.rowid
            """,
            (acn,),
        )
        for event in events:
            event["evidence"] = json.loads(event.pop("evidence_json"))
        verifications = self.db.query_all(
            """
            SELECT v.verification_id,v.request_id,v.verification_type,
                   v.step_sequence,v.attempt_no,v.result,v.details_json,v.created_at,
                   a.audit_sequence
            FROM verification_records v
            LEFT JOIN audit_log a
              ON a.record_type='verification' AND a.record_id=v.verification_id
            WHERE v.acn=? ORDER BY coalesce(a.audit_sequence,9223372036854775807),v.rowid
            """,
            (acn,),
        )
        for record in verifications:
            record["details"] = json.loads(record.pop("details_json"))
        key_parts = self.db.query_all(
            """
            SELECT k.binding_id,k.request_id,k.kpsn,k.part_no,k.group_id,
                   k.station_code,k.step_sequence,k.attempt_no,k.bound_at,
                   a.audit_sequence
            FROM key_part_bindings k
            LEFT JOIN audit_log a
              ON a.record_type='key_part' AND a.record_id=k.binding_id
            WHERE k.parent_acn=?
            ORDER BY coalesce(a.audit_sequence,9223372036854775807),k.rowid
            """,
            (acn,),
        )
        defects = self.db.query_all(
            """
            SELECT d.defect_id,d.event_id,d.code,d.description,d.status,
                   d.created_at,d.closed_at,a.audit_sequence
            FROM defects d
            LEFT JOIN audit_log a
              ON a.record_type='defect' AND a.record_id=d.defect_id
            WHERE d.acn=?
            ORDER BY coalesce(a.audit_sequence,9223372036854775807),d.rowid
            """,
            (acn,),
        )
        repairs = self.db.query_all(
            """
            SELECT r.action_id,r.request_id,r.defect_id,r.action_code,r.notes,
                   r.technician,r.status,r.created_at,a.audit_sequence
            FROM repair_actions r
            LEFT JOIN audit_log a
              ON a.record_type='repair_action' AND a.record_id=r.action_id
            WHERE r.acn=?
            ORDER BY coalesce(a.audit_sequence,9223372036854775807),r.rowid
            """,
            (acn,),
        )
        scans = self.db.query_all(
            """
            SELECT s.scan_id,s.request_id,s.operation,s.input_type,s.raw_value,
                   s.normalized_value,s.station_code,s.result,s.reason_code,
                   s.occurred_at,a.audit_sequence
            FROM scan_attempts s
            LEFT JOIN audit_log a
              ON a.record_type='scan_attempt' AND a.record_id=s.scan_id
            WHERE s.acn=?
            ORDER BY coalesce(a.audit_sequence,9223372036854775807),s.rowid
            """,
            (acn,),
        )
        timeline: list[dict[str, Any]] = []
        for event in events:
            timeline.append(
                {
                    "type": "station_event",
                    "at": event["occurred_at"],
                    "id": event["event_id"],
                    "local_sequence": event["local_sequence"],
                    "audit_sequence": event["audit_sequence"],
                    "summary": f"{event['station_code']} {event['result']}",
                }
            )
        for record in verifications:
            timeline.append(
                {
                    "type": "verification",
                    "at": record["created_at"],
                    "id": record["verification_id"],
                    "audit_sequence": record["audit_sequence"],
                    "summary": f"{record['verification_type']} {record['result']}",
                }
            )
        for binding in key_parts:
            timeline.append(
                {
                    "type": "key_part",
                    "at": binding["bound_at"],
                    "id": binding["binding_id"],
                    "audit_sequence": binding["audit_sequence"],
                    "summary": f"{binding['part_no']} / {binding['kpsn']}",
                }
            )
        for repair in repairs:
            timeline.append(
                {
                    "type": "repair_action",
                    "at": repair["created_at"],
                    "id": repair["action_id"],
                    "audit_sequence": repair["audit_sequence"],
                    "summary": repair["action_code"],
                }
            )
        for defect in defects:
            timeline.append(
                {
                    "type": "defect",
                    "at": defect["created_at"],
                    "id": defect["defect_id"],
                    "audit_sequence": defect["audit_sequence"],
                    # This item represents the immutable creation fact at the
                    # defect's original audit position.  The mutable current
                    # status remains available in the top-level ``defects``
                    # collection; projecting it backward here would make a
                    # later repair appear to have happened before its action.
                    "summary": f"{defect['code']} OPENED",
                }
            )
        # Successful scans already appear as richer station, verification, or
        # key-part records. Rejected attempts are distinct fail-closed evidence.
        accepted_station_requests = {event["request_id"] for event in events}
        for scan in scans:
            if (
                scan["result"] == "FAIL"
                and scan["request_id"] not in accepted_station_requests
            ):
                timeline.append(
                    {
                        "type": "rejected_scan",
                        "at": scan["occurred_at"],
                        "id": scan["scan_id"],
                        "audit_sequence": scan["audit_sequence"],
                        "summary": f"{scan['input_type']} {scan['reason_code']}",
                    }
                )
        # audit_sequence is allocated inside each local transaction and gives
        # unlike record types one causal order even when timestamps collide.
        timeline.sort(
            key=lambda item: (
                item.get("audit_sequence") is None,
                item.get("audit_sequence") or 0,
                item["at"],
                item["id"],
            )
        )
        return {
            "ok": True,
            "acn": acn,
            "timeline": timeline,
            "station_events": events,
            "verifications": verifications,
            "key_parts": key_parts,
            "defects": defects,
            "repair_actions": repairs,
            "scan_attempts": scans,
        }

    def list_outbox(self, *, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        rows = self.db.query_all(
            """
            SELECT o.event_id,a.audit_sequence,o.status,o.attempts,o.next_attempt_at,
                   o.last_error,o.created_at,o.delivered_at,
                   e.acn,e.station_code,e.result,e.occurred_at
            FROM delivery_outbox o
            JOIN station_events e ON e.event_id=o.event_id
            LEFT JOIN audit_log a
              ON a.record_type='station_event' AND a.record_id=o.event_id
            ORDER BY coalesce(a.audit_sequence,o.rowid) DESC LIMIT ?
            """,
            (safe_limit,),
        )
        return {"ok": True, "outbox": rows}

    # ------------------------------------------------------------- mutations
    def check_mac(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "mac.check"
        request_id, request_hash = self._request_identity(operation, payload)
        acn = normalize_acn(payload.get("acn"))
        raw_mac = payload.get("mac_address")
        mac = normalize_mac(raw_mac)
        interface_name = str(payload.get("interface_name", "")).strip().lower()
        error: DomainError | None = None
        response: dict[str, Any] | None = None

        with self.db.transaction(immediate=True) as connection:
            replay = self._idempotent_replay(
                connection, request_id, operation, request_hash
            )
            if replay is not None:
                return replay
            unit = self._load_unit(connection, acn)
            if not ACN_RE.fullmatch(acn):
                error = DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
            elif not unit:
                error = DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
            elif availability_error := self._unit_availability_error(unit):
                error = availability_error
            elif unit["operation_code"] != "VERIFY_MAC":
                error = DomainError(
                    "ROUTE_VIOLATION",
                    f"Unit is at {unit['station_code']}, not the MAC verification step",
                    409,
                    {"expected_station": unit["station_code"]},
                )
            elif not mac:
                error = DomainError(
                    "INVALID_FORMAT", "MAC must contain exactly 12 hexadecimal digits", 400
                )
            elif not interface_name:
                error = DomainError(
                    "INVALID_REQUEST", "interface_name is required", 400
                )
            else:
                interface = connection.execute(
                    """
                    SELECT acn,interface_name,mac_address,status
                    FROM network_interfaces WHERE mac_address=?
                    """,
                    (mac,),
                ).fetchone()
                if not interface:
                    error = DomainError(
                        "UNKNOWN_MAC", "MAC is not allocated in the synthetic pool", 404
                    )
                elif interface["acn"] != acn:
                    error = DomainError(
                        "ASSOCIATION_MISMATCH",
                        "MAC format is valid but it belongs to a different ACN",
                        409,
                    )
                elif interface["interface_name"].lower() != interface_name:
                    error = DomainError(
                        "WRONG_INTERFACE",
                        f"MAC belongs to {interface['interface_name']}, not {interface_name}",
                        409,
                    )
                elif interface["status"] != "BOUND":
                    error = DomainError(
                        "MAC_NOT_BOUND", "MAC is not in a usable bound state", 409
                    )

            now = utc_now()
            if error:
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="MAC",
                    raw_value=raw_mac,
                    normalized_value=mac,
                    acn=acn,
                    station_code=unit["station_code"] if unit else None,
                    result="FAIL",
                    reason_code=error.code,
                    occurred_at=now,
                )
                self._store_error(
                    connection, request_id, operation, request_hash, error, now
                )
            else:
                assert unit is not None
                verification_id = new_id("ver")
                details = {
                    "mac_address": mac,
                    "interface_name": interface_name,
                    "source": "synthetic-allocation",
                }
                connection.execute(
                    """
                    INSERT INTO verification_records(
                        verification_id,request_id,acn,verification_type,
                        step_sequence,attempt_no,result,details_json,created_at
                    ) VALUES(?,?,?,?,?,?, 'PASS', ?,?)
                    """,
                    (
                        verification_id,
                        request_id,
                        acn,
                        "MAC_ASSOCIATION",
                        unit["current_sequence"],
                        unit["attempt_no"],
                        canonical_json(details),
                        now,
                    ),
                )
                self._record_audit(
                    connection,
                    record_type="verification",
                    record_id=verification_id,
                    request_id=request_id,
                    acn=acn,
                    occurred_at=now,
                )
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="MAC",
                    raw_value=raw_mac,
                    normalized_value=mac,
                    acn=acn,
                    station_code=unit["station_code"],
                    result="PASS",
                    reason_code="OK",
                    occurred_at=now,
                )
                response = {
                    "ok": True,
                    "verification": {
                        "verification_id": verification_id,
                        "acn": acn,
                        "mac_address": mac,
                        "interface_name": interface_name,
                        "result": "PASS",
                        "created_at": now,
                    },
                }
                self._store_success(
                    connection, request_id, operation, request_hash, response, 200, now
                )
        if error:
            raise error
        assert response is not None
        return response

    def collect_key_part(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "key-parts.collect"
        request_id, request_hash = self._request_identity(operation, payload)
        acn = normalize_acn(payload.get("acn"))
        part_no = normalize_kpsn(payload.get("part_no"))
        kpsn = normalize_kpsn(payload.get("kpsn"))
        error: DomainError | None = None
        response: dict[str, Any] | None = None

        with self.db.transaction(immediate=True) as connection:
            replay = self._idempotent_replay(
                connection, request_id, operation, request_hash
            )
            if replay is not None:
                return replay
            unit = self._load_unit(connection, acn)
            member: sqlite3.Row | None = None
            if not ACN_RE.fullmatch(acn):
                error = DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
            elif not unit:
                error = DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
            elif availability_error := self._unit_availability_error(unit):
                error = availability_error
            elif unit["operation_code"] != "COLLECT_KP":
                error = DomainError(
                    "ROUTE_VIOLATION",
                    f"Unit is at {unit['station_code']}, not key-part collection",
                    409,
                    {"expected_station": unit["station_code"]},
                )
            elif not KPSN_RE.fullmatch(part_no) or not KPSN_RE.fullmatch(kpsn):
                error = DomainError(
                    "INVALID_FORMAT", "part_no and kpsn must be 3-128 safe characters", 400
                )
            else:
                existing = connection.execute(
                    "SELECT parent_acn,part_no FROM key_part_bindings WHERE kpsn=?",
                    (kpsn,),
                ).fetchone()
                if existing:
                    error = DomainError(
                        "KPSN_ALREADY_BOUND",
                        "KPSN is already bound and cannot be consumed twice",
                        409,
                        {"bound_acn": existing["parent_acn"]},
                    )
                else:
                    members = connection.execute(
                        """
                        SELECT br.group_id,br.required_qty,gm.substitute_rank
                        FROM bom_requirements br
                        JOIN kp_group_members gm ON gm.group_id=br.group_id
                        JOIN materials m ON m.part_no=gm.part_no AND m.active=1
                        WHERE br.work_order_id=? AND br.step_sequence=? AND gm.part_no=?
                        ORDER BY br.group_id
                        """,
                        (unit["work_order_id"], unit["current_sequence"], part_no),
                    ).fetchall()
                    if not members:
                        known = connection.execute(
                            "SELECT 1 FROM materials WHERE part_no=?", (part_no,)
                        ).fetchone()
                        error = DomainError(
                            "INVALID_SUBSTITUTE" if known else "MATERIAL_NOT_IN_BOM",
                            "Part is not an approved member of this work order's BOM group",
                            409,
                        )
                    elif len(members) > 1:
                        error = DomainError(
                            "AMBIGUOUS_BOM_GROUP",
                            "Part matches more than one required BOM group; an explicit group assignment is required",
                            409,
                            {"group_ids": [row["group_id"] for row in members]},
                        )
                    else:
                        member = members[0]
                        assert member is not None
                        count = connection.execute(
                            """
                            SELECT count(*) FROM key_part_bindings
                            WHERE parent_acn=? AND step_sequence=? AND attempt_no=?
                              AND group_id=?
                            """,
                            (
                                acn,
                                unit["current_sequence"],
                                unit["attempt_no"],
                                member["group_id"],
                            ),
                        ).fetchone()[0]
                        if count >= member["required_qty"]:
                            error = DomainError(
                                "QUANTITY_EXCEEDED",
                                f"BOM group {member['group_id']} already has its required quantity",
                                409,
                            )

            now = utc_now()
            if error:
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="KPSN",
                    raw_value=payload.get("kpsn"),
                    normalized_value=kpsn,
                    acn=acn,
                    station_code=unit["station_code"] if unit else None,
                    result="FAIL",
                    reason_code=error.code,
                    occurred_at=now,
                )
                self._store_error(
                    connection, request_id, operation, request_hash, error, now
                )
            else:
                assert member is not None and unit is not None
                binding_id = new_id("kp")
                connection.execute(
                    """
                    INSERT INTO key_part_bindings(
                        binding_id,parent_acn,kpsn,part_no,group_id,station_code,
                        step_sequence,attempt_no,request_id,bound_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        binding_id,
                        acn,
                        kpsn,
                        part_no,
                        member["group_id"],
                        unit["station_code"],
                        unit["current_sequence"],
                        unit["attempt_no"],
                        request_id,
                        now,
                    ),
                )
                self._record_audit(
                    connection,
                    record_type="key_part",
                    record_id=binding_id,
                    request_id=request_id,
                    acn=acn,
                    occurred_at=now,
                )
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="KPSN",
                    raw_value=payload.get("kpsn"),
                    normalized_value=kpsn,
                    acn=acn,
                    station_code=unit["station_code"],
                    result="PASS",
                    reason_code="OK",
                    occurred_at=now,
                )
                collected = connection.execute(
                    """
                    SELECT count(*) FROM key_part_bindings
                    WHERE parent_acn=? AND step_sequence=? AND attempt_no=?
                      AND group_id=?
                    """,
                    (
                        acn,
                        unit["current_sequence"],
                        unit["attempt_no"],
                        member["group_id"],
                    ),
                ).fetchone()[0]
                response = {
                    "ok": True,
                    "binding": {
                        "binding_id": binding_id,
                        "acn": acn,
                        "kpsn": kpsn,
                        "part_no": part_no,
                        "group_id": member["group_id"],
                        "substitute_rank": member["substitute_rank"],
                        "collected_qty": collected,
                        "required_qty": member["required_qty"],
                        "bound_at": now,
                    },
                }
                self._store_success(
                    connection, request_id, operation, request_hash, response, 201, now
                )
        if error:
            raise error
        assert response is not None
        return response

    def complete_station(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "stations.complete"
        request_id, request_hash = self._request_identity(operation, payload)
        acn = normalize_acn(payload.get("acn"))
        station_code = normalize_kpsn(payload.get("station_code"))
        result = str(payload.get("result", "")).strip().upper()
        reason_supplied = "reason_code" in payload
        reason_code = (
            normalize_kpsn(payload.get("reason_code"))
            if reason_supplied
            else ("OK" if result == "PASS" else "STATION_REJECT")
        )
        evidence = payload.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {"value": str(evidence)}
        error: DomainError | None = None
        response: dict[str, Any] | None = None

        with self.db.transaction(immediate=True) as connection:
            replay = self._idempotent_replay(
                connection, request_id, operation, request_hash
            )
            if replay is not None:
                return replay
            unit = self._load_unit(connection, acn)
            if not ACN_RE.fullmatch(acn):
                error = DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
            elif not unit:
                error = DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
            elif availability_error := self._unit_availability_error(unit):
                error = availability_error
            elif result not in ("PASS", "FAIL"):
                error = DomainError("INVALID_REQUEST", "result must be PASS or FAIL", 400)
            elif not reason_code or not (
                reason_code == "OK" or KPSN_RE.fullmatch(reason_code)
            ):
                error = DomainError(
                    "INVALID_REASON_CODE",
                    "reason_code must be 2-128 safe characters",
                    400,
                )
            elif result == "PASS" and reason_code != "OK":
                error = DomainError(
                    "INVALID_REASON_CODE",
                    "PASS requires reason_code OK",
                    400,
                )
            elif result == "FAIL" and reason_code == "OK":
                error = DomainError(
                    "INVALID_REASON_CODE",
                    "FAIL requires a non-OK reason_code",
                    400,
                )
            elif station_code != unit["station_code"]:
                error = DomainError(
                    "ROUTE_VIOLATION",
                    f"Expected {unit['station_code']}, received {station_code or '(blank)'}",
                    409,
                    {
                        "expected_station": unit["station_code"],
                        "received_station": station_code,
                    },
                )
            elif result == "PASS":
                error = self._validate_step_requirements(connection, unit)

            now = utc_now()
            if error:
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="STATION",
                    raw_value=payload.get("station_code"),
                    normalized_value=station_code,
                    acn=acn,
                    station_code=station_code or None,
                    result="FAIL",
                    reason_code=error.code,
                    occurred_at=now,
                )
                self._store_error(
                    connection, request_id, operation, request_hash, error, now
                )
            else:
                assert unit is not None
                event_id = stable_hash(
                    "tracegate-event-v2",
                    request_id,
                    request_hash,
                )
                next_sequence = (
                    unit["on_pass_sequence"]
                    if result == "PASS"
                    else unit["on_fail_sequence"]
                )
                next_attempt = unit["attempt_no"]
                product_status = "ACTIVE"
                if result == "PASS" and unit["operation_code"] == "REPAIR":
                    next_attempt += 1
                    connection.execute(
                        """
                        UPDATE defects SET status='REPAIRED',closed_at=?
                        WHERE acn=? AND status='OPEN'
                        """,
                        (now, acn),
                    )
                if next_sequence is None:
                    if result == "PASS":
                        next_status = "COMPLETE"
                        product_status = "COMPLETE"
                    else:
                        next_status = "HOLD"
                        product_status = "HOLD"
                else:
                    next_step = connection.execute(
                        """
                        SELECT operation_code FROM route_steps
                        WHERE route_id=? AND route_version=? AND sequence=?
                        """,
                        (unit["route_id"], unit["route_version"], next_sequence),
                    ).fetchone()
                    next_status = (
                        "IN_REPAIR"
                        if next_step and next_step["operation_code"] == "REPAIR"
                        else "IN_PROGRESS"
                    )
                message = (
                    f"{unit['name']} completed"
                    if result == "PASS"
                    else f"{unit['name']} rejected"
                )
                connection.execute(
                    """
                    INSERT INTO station_events(
                        event_id,request_id,acn,route_id,route_version,sequence,
                        station_code,operation_code,attempt_no,result,reason_code,
                        message,evidence_json,occurred_at,delivery_status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING')
                    """,
                    (
                        event_id,
                        request_id,
                        acn,
                        unit["route_id"],
                        unit["route_version"],
                        unit["current_sequence"],
                        station_code,
                        unit["operation_code"],
                        unit["attempt_no"],
                        result,
                        reason_code,
                        message,
                        canonical_json(evidence),
                        now,
                    ),
                )
                self._record_audit(
                    connection,
                    record_type="station_event",
                    record_id=event_id,
                    request_id=request_id,
                    acn=acn,
                    occurred_at=now,
                )
                connection.execute(
                    """
                    UPDATE wip_state
                    SET current_sequence=?,status=?,attempt_no=?,last_event_id=?,
                        version=version+1,updated_at=?
                    WHERE acn=?
                    """,
                    (next_sequence, next_status, next_attempt, event_id, now, acn),
                )
                connection.execute(
                    "UPDATE products SET status=? WHERE acn=?",
                    (product_status, acn),
                )
                defect: dict[str, Any] | None = None
                if result == "FAIL":
                    defect_id = new_id("def")
                    defect_description = str(
                        evidence.get("description", "Synthetic station failure")
                    )[:300]
                    connection.execute(
                        """
                        INSERT INTO defects(
                            defect_id,acn,event_id,code,description,status,created_at
                        ) VALUES(?,?,?,?,?,'OPEN',?)
                        """,
                        (
                            defect_id,
                            acn,
                            event_id,
                            reason_code,
                            defect_description,
                            now,
                        ),
                    )
                    self._record_audit(
                        connection,
                        record_type="defect",
                        record_id=defect_id,
                        request_id=request_id,
                        acn=acn,
                        occurred_at=now,
                    )
                    defect = {
                        "defect_id": defect_id,
                        "code": reason_code,
                        "status": "OPEN",
                    }
                event_payload = {
                    "schema": "tracegate.station-event.v2",
                    "event_id": event_id,
                    "request_id": request_id,
                    "acn": acn,
                    "work_order_id": unit["work_order_id"],
                    "route": {
                        "route_id": unit["route_id"],
                        "version": unit["route_version"],
                        "sequence": unit["current_sequence"],
                    },
                    "station_code": station_code,
                    "operation_code": unit["operation_code"],
                    "attempt_no": unit["attempt_no"],
                    "result": result,
                    "reason_code": reason_code,
                    "occurred_at": now,
                    "evidence": evidence,
                }
                connection.execute(
                    """
                    INSERT INTO delivery_outbox(
                        event_id,payload,status,attempts,next_attempt_at,locked_until,
                        last_error,created_at,delivered_at
                    ) VALUES(?,?,'PENDING',0,0,0,NULL,?,NULL)
                    """,
                    (event_id, canonical_json(event_payload), now),
                )
                self._record_scan(
                    connection,
                    request_id=request_id,
                    operation=operation,
                    input_type="STATION",
                    raw_value=payload.get("station_code"),
                    normalized_value=station_code,
                    acn=acn,
                    station_code=station_code,
                    result=result,
                    reason_code=reason_code,
                    occurred_at=now,
                )
                response = {
                    "ok": True,
                    "event": {
                        "event_id": event_id,
                        "acn": acn,
                        "station_code": station_code,
                        "operation_code": unit["operation_code"],
                        "attempt_no": unit["attempt_no"],
                        "result": result,
                        "reason_code": reason_code,
                        "occurred_at": now,
                        "delivery_status": "PENDING",
                    },
                    "transition": {
                        "from_sequence": unit["current_sequence"],
                        "to_sequence": next_sequence,
                        "wip_status": next_status,
                        "next_attempt_no": next_attempt,
                    },
                    "defect": defect,
                }
                self._store_success(
                    connection, request_id, operation, request_hash, response, 201, now
                )
        if error:
            raise error
        assert response is not None
        return response

    def record_repair_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "repairs.actions"
        request_id, request_hash = self._request_identity(operation, payload)
        acn = normalize_acn(payload.get("acn"))
        defect_id = str(payload.get("defect_id", "")).strip()
        action_code = normalize_kpsn(payload.get("action_code"))
        raw_notes = payload.get("notes", "")
        notes = raw_notes.strip()[:1000] if isinstance(raw_notes, str) else ""
        raw_technician = payload.get("technician", "demo-operator")
        technician = (
            raw_technician.strip()[:80] if isinstance(raw_technician, str) else ""
        )
        error: DomainError | None = None
        response: dict[str, Any] | None = None
        with self.db.transaction(immediate=True) as connection:
            replay = self._idempotent_replay(
                connection, request_id, operation, request_hash
            )
            if replay is not None:
                return replay
            unit = self._load_unit(connection, acn)
            defect = connection.execute(
                "SELECT * FROM defects WHERE defect_id=?", (defect_id,)
            ).fetchone()
            if not ACN_RE.fullmatch(acn):
                error = DomainError("INVALID_FORMAT", "ACN format is invalid", 400)
            elif not unit:
                error = DomainError("UNKNOWN_ACN", f"Unknown ACN: {acn}", 404)
            elif availability_error := self._unit_availability_error(unit):
                error = availability_error
            elif unit["operation_code"] != "REPAIR":
                error = DomainError(
                    "ROUTE_VIOLATION", "Repair action is only allowed at ST-RP", 409
                )
            elif not defect or defect["acn"] != acn:
                error = DomainError("UNKNOWN_DEFECT", "Open defect was not found", 404)
            elif defect["status"] != "OPEN":
                error = DomainError("DEFECT_CLOSED", "Defect is no longer open", 409)
            elif not KPSN_RE.fullmatch(action_code) or not notes or not technician:
                error = DomainError(
                    "INVALID_REQUEST",
                    "action_code, notes, and technician must be valid non-empty strings",
                    400,
                )
            now = utc_now()
            if error:
                self._store_error(
                    connection, request_id, operation, request_hash, error, now
                )
            else:
                action_id = new_id("repair")
                connection.execute(
                    """
                    INSERT INTO repair_actions(
                        action_id,defect_id,acn,request_id,action_code,notes,
                        technician,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,'VERIFIED',?)
                    """,
                    (
                        action_id,
                        defect_id,
                        acn,
                        request_id,
                        action_code,
                        notes,
                        technician,
                        now,
                    ),
                )
                self._record_audit(
                    connection,
                    record_type="repair_action",
                    record_id=action_id,
                    request_id=request_id,
                    acn=acn,
                    occurred_at=now,
                )
                response = {
                    "ok": True,
                    "repair_action": {
                        "action_id": action_id,
                        "defect_id": defect_id,
                        "acn": acn,
                        "action_code": action_code,
                        "notes": notes,
                        "technician": technician,
                        "status": "VERIFIED",
                        "created_at": now,
                    },
                }
                self._store_success(
                    connection, request_id, operation, request_hash, response, 201, now
                )
        if error:
            raise error
        assert response is not None
        return response

    def replay_outbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = "outbox.replay"
        request_id, request_hash = self._request_identity(operation, payload)
        event_id = str(payload.get("event_id", "")).strip() or None
        now = utc_now()
        lease_now = time.time()
        with self.db.transaction(immediate=True) as connection:
            replay = self._idempotent_replay(
                connection, request_id, operation, request_hash
            )
            if replay is not None:
                return replay
            parameters: tuple[Any, ...] = (lease_now,)
            event_filter = ""
            if event_id:
                event_filter = "event_id=? AND"
                parameters = (event_id, lease_now)
            eligible = connection.execute(
                f"""
                SELECT event_id FROM delivery_outbox
                WHERE {event_filter} (
                    status IN ('RETRY','DEAD_LETTER') OR
                    (status='IN_FLIGHT' AND locked_until <= ?)
                )
                """,
                parameters,
            ).fetchall()
            eligible_ids = [row["event_id"] for row in eligible]
            for offset in range(0, len(eligible_ids), 500):
                chunk = eligible_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                connection.execute(
                    f"""
                    UPDATE delivery_outbox
                    SET status='PENDING',next_attempt_at=0,locked_until=0,
                        last_error=NULL,delivered_at=NULL
                    WHERE event_id IN ({placeholders})
                    """,
                    chunk,
                )
                connection.execute(
                    f"""
                    UPDATE station_events
                    SET delivery_status='PENDING',delivered_at=NULL
                    WHERE event_id IN ({placeholders})
                    """,
                    chunk,
                )
            response = {
                "ok": True,
                "requeued": len(eligible_ids),
                "event_id": event_id,
            }
            self._store_success(
                connection, request_id, operation, request_hash, response, 200, now
            )
        return response

    def reset_demo(self) -> dict[str, Any]:
        self.db.reset_demo()
        return {
            "ok": True,
            "message": "Synthetic demo data reset",
            "units": ["ACN-DEMO-0001", "ACN-DEMO-0002", "ACN-DEMO-0003"],
        }

    # --------------------------------------------------------------- internals
    def _request_identity(
        self, operation: str, payload: dict[str, Any]
    ) -> tuple[str, str]:
        if not isinstance(payload, dict):
            raise DomainError("INVALID_REQUEST", "JSON object required", 400)
        request_id = require_request_id(payload.get("request_id"))
        if not request_id:
            raise DomainError(
                "INVALID_REQUEST_ID",
                "request_id must be 8-128 safe characters and globally unique",
                400,
            )
        try:
            request_hash = payload_hash(operation, payload)
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "INVALID_JSON",
                "Request payload must contain finite JSON-compatible values",
                400,
            ) from exc
        return request_id, request_hash

    def _idempotent_replay(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        operation: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM idempotency_records WHERE request_id=?", (request_id,)
        ).fetchone()
        if not row:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT",
                "request_id was already used with a different operation or payload",
                409,
            )
        body = json.loads(row["response_json"])
        body["idempotent_replay"] = True
        if row["status_code"] >= 400:
            error = body["error"]
            raise DomainError(
                error["code"],
                error["message"],
                row["status_code"],
                error.get("details", {}),
                idempotent_replay=True,
            )
        return body

    @staticmethod
    def _store_success(
        connection: sqlite3.Connection,
        request_id: str,
        operation: str,
        request_hash: str,
        response: dict[str, Any],
        status_code: int,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_records(
                request_id,operation,request_hash,status_code,response_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                request_id,
                operation,
                request_hash,
                status_code,
                canonical_json(response),
                now,
            ),
        )

    @staticmethod
    def _store_error(
        connection: sqlite3.Connection,
        request_id: str,
        operation: str,
        request_hash: str,
        error: DomainError,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_records(
                request_id,operation,request_hash,status_code,response_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                request_id,
                operation,
                request_hash,
                error.status,
                canonical_json(error.as_dict()),
                now,
            ),
        )

    @staticmethod
    def _record_audit(
        connection: sqlite3.Connection,
        *,
        record_type: str,
        record_id: str,
        request_id: str,
        acn: str,
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(
                record_type,record_id,request_id,acn,occurred_at
            ) VALUES(?,?,?,?,?)
            """,
            (record_type, record_id, request_id, acn or None, occurred_at),
        )

    @staticmethod
    def _record_scan(
        connection: sqlite3.Connection,
        *,
        request_id: str,
        operation: str,
        input_type: str,
        raw_value: Any,
        normalized_value: str,
        acn: str,
        station_code: str | None,
        result: str,
        reason_code: str,
        occurred_at: str,
    ) -> None:
        scan_id = new_id("scan")
        connection.execute(
            """
            INSERT INTO scan_attempts(
                scan_id,request_id,operation,input_type,raw_value,normalized_value,
                acn,station_code,result,reason_code,occurred_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scan_id,
                request_id,
                operation,
                input_type,
                None if raw_value is None else str(raw_value)[:300],
                normalized_value[:300],
                acn or None,
                station_code,
                result,
                reason_code,
                occurred_at,
            ),
        )
        TraceGateService._record_audit(
            connection,
            record_type="scan_attempt",
            record_id=scan_id,
            request_id=request_id,
            acn=acn,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _load_unit(
        connection: sqlite3.Connection, acn: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT p.acn,p.status AS product_status,p.work_order_id,wo.model_code,
                   wo.status AS work_order_status,wo.route_id,wo.route_version,
                   r.status AS route_status,w.current_sequence,w.status,
                   w.attempt_no,w.last_event_id,w.version AS wip_version,w.updated_at,
                   rs.station_code,rs.operation_code,rs.name,
                   rs.on_pass_sequence,rs.on_fail_sequence,rs.requirements_json
            FROM products p
            JOIN work_orders wo ON wo.work_order_id=p.work_order_id
            JOIN routes r ON r.route_id=wo.route_id AND r.version=wo.route_version
            JOIN wip_state w ON w.acn=p.acn
            LEFT JOIN route_steps rs
              ON rs.route_id=wo.route_id AND rs.route_version=wo.route_version
             AND rs.sequence=w.current_sequence
            WHERE p.acn=?
            """,
            (acn,),
        ).fetchone()
        if not row:
            return None
        unit = dict(row)
        unit["requirements"] = json.loads(unit.pop("requirements_json") or "[]")
        return unit

    @staticmethod
    def _unit_availability_error(unit: dict[str, Any]) -> DomainError | None:
        """Fail closed when planning or disposition state forbids mutation."""

        if unit["work_order_status"] != "RELEASED":
            return DomainError(
                "WORK_ORDER_NOT_RELEASED",
                "Work order must be RELEASED before production activity",
                409,
                {"work_order_status": unit["work_order_status"]},
            )
        if unit["route_status"] != "ACTIVE":
            return DomainError(
                "ROUTE_NOT_ACTIVE",
                "The assigned route version is not active",
                409,
                {"route_status": unit["route_status"]},
            )
        if unit["product_status"] == "HOLD" or unit["status"] == "HOLD":
            return DomainError("UNIT_ON_HOLD", "Unit is on hold", 409)
        if unit["product_status"] == "SCRAPPED":
            return DomainError("UNIT_NOT_ACTIVE", "Unit has been scrapped", 409)
        if (
            unit["product_status"] == "COMPLETE"
            or unit["status"] == "COMPLETE"
            or unit["current_sequence"] is None
        ):
            return DomainError(
                "ROUTE_COMPLETE", "Unit has already completed its route", 409
            )
        return None

    def _requirement_progress(
        self, connection: sqlite3.Connection, unit: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if unit["current_sequence"] is None:
            return []
        rows = connection.execute(
            """
            SELECT br.group_id,kg.description,br.required_qty,
                   count(kb.binding_id) AS collected_qty
            FROM bom_requirements br
            JOIN kp_groups kg ON kg.group_id=br.group_id
            LEFT JOIN key_part_bindings kb
              ON kb.parent_acn=? AND kb.step_sequence=br.step_sequence
             AND kb.attempt_no=? AND kb.group_id=br.group_id
            WHERE br.work_order_id=? AND br.step_sequence=?
            GROUP BY br.group_id,kg.description,br.required_qty
            ORDER BY br.group_id
            """,
            (
                unit["acn"],
                unit["attempt_no"],
                unit["work_order_id"],
                unit["current_sequence"],
            ),
        ).fetchall()
        return [
            {
                **dict(row),
                "satisfied": row["collected_qty"] >= row["required_qty"],
            }
            for row in rows
        ]

    def _validate_step_requirements(
        self, connection: sqlite3.Connection, unit: dict[str, Any]
    ) -> DomainError | None:
        requirements = unit["requirements"]
        if "MAC" in requirements:
            verified = connection.execute(
                """
                SELECT 1 FROM verification_records
                WHERE acn=? AND verification_type='MAC_ASSOCIATION'
                  AND step_sequence=? AND attempt_no=? AND result='PASS'
                LIMIT 1
                """,
                (unit["acn"], unit["current_sequence"], unit["attempt_no"]),
            ).fetchone()
            if not verified:
                return DomainError(
                    "REQUIREMENT_NOT_SATISFIED",
                    "A successful MAC association check is required before passing",
                    409,
                    {"requirement": "MAC"},
                )
        if "KEY_PARTS" in requirements:
            progress = self._requirement_progress(connection, unit)
            missing = [row for row in progress if not row["satisfied"]]
            if not progress or missing:
                return DomainError(
                    "QUANTITY_MISMATCH",
                    "Required key-part quantities are incomplete",
                    409,
                    {"groups": progress},
                )
        if "REPAIR_ACTION" in requirements:
            open_defects = connection.execute(
                "SELECT defect_id FROM defects WHERE acn=? AND status='OPEN'",
                (unit["acn"],),
            ).fetchall()
            if not open_defects:
                return DomainError(
                    "REQUIREMENT_NOT_SATISFIED",
                    "No open defect exists for this repair step",
                    409,
                    {"requirement": "OPEN_DEFECT"},
                )
            missing_actions = []
            for defect in open_defects:
                action = connection.execute(
                    """
                    SELECT 1 FROM repair_actions
                    WHERE defect_id=? AND status='VERIFIED' LIMIT 1
                    """,
                    (defect["defect_id"],),
                ).fetchone()
                if not action:
                    missing_actions.append(defect["defect_id"])
            if missing_actions:
                return DomainError(
                    "REPAIR_ACTION_REQUIRED",
                    "Every open defect needs a verified repair action",
                    409,
                    {"defect_ids": missing_actions},
                )
        return None

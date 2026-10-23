# API Reference

Base URL in the local demo: `http://127.0.0.1:8080`. All request and response bodies use JSON. This is a demo contract, not a versioned enterprise integration specification.

## Conventions

- Every state-changing domain command requires a caller-generated `request_id` of 8–128 safe characters.
- Every POST body must use `Content-Type: application/json`; another or missing media type returns HTTP `415` with `UNSUPPORTED_MEDIA_TYPE`.
- JSON bodies must be 1–131,072 bytes and decode as valid UTF-8 JSON objects. Invalid UTF-8, malformed JSON and non-finite numeric constants such as `NaN`/`Infinity` return HTTP `400` with `INVALID_JSON`.
- Reusing an ID with the same operation and payload returns the stored response with `idempotent_replay: true` and preserves that endpoint's original success status (for example, a create command remains `201`).
- Reusing an ID with a different payload returns HTTP `409` and `IDEMPOTENCY_CONFLICT`.
- Domain rejection uses a stable code:

```json
{
  "ok": false,
  "error": {
    "code": "ROUTE_VIOLATION",
    "message": "Human-readable explanation",
    "details": {}
  }
}
```

- Human-readable messages may change; clients should branch on `error.code`.

## Read endpoints

### `GET /api/v1/health`

Returns service, version, schema, database, uptime and outbox health. A dead-letter row degrades health; an unreadable database returns `503`.

### `GET /api/v1/metrics`

Returns current synthetic counters grouped by product/WIP/event/outbox status plus defect and retry totals. These are operational counters, not pre-defined business KPIs.

### `GET /api/v1/units/{acn}`

Returns current unit, route/WIP, interfaces, key-part bindings, BOM progress and defects.

### `GET /api/v1/units/{acn}/trace`

Returns the unit timeline: station events, validation attempts, MAC evidence, key parts and repair actions. A defect timeline item is the immutable creation fact and is therefore summarized as `OPENED`; its mutable current state (`OPEN`, `REPAIRED` or `CLOSED`) remains in the top-level `defects` collection instead of being projected backward onto the creation event. Timeline items expose local `audit_sequence` and are ordered by that sequence across record types. The sequence is scoped to this SQLite database/device; it is not a cross-node global order.

### `GET /api/v1/outbox?limit=100`

Returns recent delivery rows joined to station event identity, including the event's local `audit_sequence`. `limit` is bounded server-side.

## Domain commands

### `POST /api/v1/mac/check`

```json
{
  "request_id": "demo-correct-mac-0001",
  "acn": "ACN-DEMO-0001",
  "interface_name": "wlan0",
  "mac_address": "02:54:47:00:01:01"
}
```

Checks format, existence, ACN association, interface association and bound status. A successful verification is scoped to the unit's current step and attempt.

### `POST /api/v1/key-parts/collect`

```json
{
  "request_id": "demo-kp-controller-0001",
  "acn": "ACN-DEMO-0001",
  "part_no": "CTRL-A100",
  "kpsn": "KPSN-DEMO-CTRL-001"
}
```

Checks route position, approved BOM group membership, required quantity and global KPSN uniqueness.

### `POST /api/v1/stations/complete`

```json
{
  "request_id": "demo-ft-pass-0001",
  "acn": "ACN-DEMO-0001",
  "station_code": "ST-FT",
  "result": "PASS",
  "evidence": {
    "test_suite": "synthetic-v1"
  }
}
```

Validates current route position and step requirements. Success atomically writes the station event, WIP transition, outbox row and idempotency response. A FAIL may open a defect and follow `on_fail_sequence`.

`reason_code` is optional. If omitted, PASS defaults to `OK` and FAIL defaults to `STATION_REJECT`. If supplied, it is normalized to uppercase and must contain 2–128 safe characters. PASS accepts only `OK`; FAIL must use a non-`OK` reason. Contradictory or malformed combinations return HTTP `400` with `INVALID_REASON_CODE`.

### `POST /api/v1/repairs/actions`

```json
{
  "request_id": "demo-repair-action-0001",
  "acn": "ACN-DEMO-0001",
  "defect_id": "def_example",
  "action_code": "RESEAT_SYNTHETIC_MODULE",
  "notes": "Synthetic repair evidence only",
  "technician": "demo-operator"
}
```

Records a verified repair action against the unit's open defect. It does not silently PASS the repair station; the operator must separately complete `ST-RP`, after which the next test uses a new attempt number.

### `POST /api/v1/outbox/replay`

Replay one eligible delivery row:

```json
{
  "request_id": "demo-replay-event-0001",
  "event_id": "event-id-from-outbox"
}
```

Omit `event_id` to requeue all eligible retry/dead-letter/in-flight rows. This is an operator action and is not exposed to an AI agent.

## Demo-only commands

These routes exist for local demonstration and must not be treated as production admin APIs.

### `POST /api/v1/demo/reset`

```json
{}
```

Deletes and reseeds only this repository's synthetic records.

### `POST /api/v1/demo/mes`

Available only in combined `demo` mode:

```json
{"online": false}
```

Toggles the in-process mock receiver for an outage demonstration. For stronger evidence, run Edge and Mock MES as separate processes or hosts and stop the upstream process instead.

## Mock upstream (`:8081`)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Receiver state and unique receipt count |
| GET | `/api/v1/receipts?limit=100` | Recent unique receipts |
| POST | `/api/v1/trace-events` | Idempotent event ingest; requires `Idempotency-Key == event_id` |
| POST | `/api/v1/demo/mes` | Toggle receiver state in the standalone simulator |

## HTTP status summary

| Status | Meaning |
| --- | --- |
| `200` | Read, verification, or replay command succeeded |
| `201` | A binding/event/action command succeeded; an idempotent replay preserves this original status |
| `400` | Malformed JSON, invalid format or missing field |
| `404` | Unknown unit, MAC, defect or route |
| `409` | Valid request conflicts with current domain/idempotency state |
| `415` | POST body is not declared as `application/json` (`UNSUPPORTED_MEDIA_TYPE`) |
| `503` | Health failure or synthetic upstream unavailable |

Check the current test suite before treating this page as a frozen contract.

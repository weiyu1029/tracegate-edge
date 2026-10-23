# Local outage-recovery evidence — 2026-10-23

## Scope

This is a controlled, synthetic local demonstration of TraceGate Edge `0.2.0`.
It contains no employer, customer, factory, product, network, or device data.

- Execution environment: Darwin arm64
- Python: 3.14.7
- Data: newly created temporary SQLite databases
- Edge and mock upstream: separate local HTTP listeners
- Scenario: complete synthetic route while upstream is offline, followed by
  upstream recovery and outbox delivery

The project did not yet have a Git commit SHA when this evidence was captured.
The result must therefore be repeated after the first public-candidate commit
and on the Portenta X8 before making target-hardware claims.

## Command

The services were started with a fresh temporary data directory and isolated
localhost ports. The validation command was:

```sh
python3 scripts/demo_smoke.py \
  --base-url http://127.0.0.1:48180 \
  --mes-base-url http://127.0.0.1:48181 \
  --wait 20 \
  --journey \
  --outage-demo \
  --delivery-wait 30 \
  --benchmark 50
```

## Observed results

- Edge health check passed.
- Mock upstream was placed offline before the route started.
- Wrong ACN/MAC association was rejected.
- The correct MAC verification and its idempotent replay passed.
- An unapproved substitute was rejected.
- Approved substitute parts and exact BOM quantities passed.
- The unit followed `ST-FT FAIL → defect → repair → ST-RP PASS → ST-FT
  PASS` on attempt 2.
- The unit completed packing and the final route step.
- Eight station events remained locally retained while upstream was offline.
- After upstream recovery, eight unique events were delivered.
- Final summary reported `ok: true`, eight station events, and sixteen timeline
  items.

```json
{
  "ok": true,
  "journey": true,
  "outage_recovered_events": 8,
  "station_events": 8,
  "timeline_items": 16
}
```

## Local read measurement

Fifty synthetic unit-read requests were measured after the journey:

| Metric | Result |
| --- | ---: |
| Minimum | 1.984 ms |
| Median | 2.434 ms |
| P95 | 2.845 ms |
| Maximum | 3.393 ms |

These figures describe one local development run only. They are not Portenta
X8, production, capacity, SLA, or factory-performance claims.

## What this proves

Within this controlled run, local business transactions did not require the
mock upstream to be available. The transactional outbox retained committed
station events and delivered the same number of unique events after recovery.

## What this does not prove

- Portenta X8 behavior or performance.
- Power-loss durability.
- Full operating-system or container restart recovery.
- Multi-device ordering or high availability.
- Security for an untrusted network; authentication, TLS, and RBAC are not
  implemented.

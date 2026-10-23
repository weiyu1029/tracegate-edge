# 3–5 Minute Portfolio Demo Guide

The goal is not to click through every feature as quickly as possible. Instead, use one clear story to prove three things: **the rules block invalid operations, an outage does not lose data, and recovery does not create duplicates.** All inputs are synthetic.

## Preparation

Start the combined demo from the repository root:

```sh
TRACEGATE_DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tracegate-demo.XXXXXX")"
TRACEGATE_DATA_DIR="$TRACEGATE_DEMO_DIR" python3 -m tracegate demo
```

Open another terminal:

```sh
API=http://127.0.0.1:8080
curl -s "$API/api/v1/health" | python3 -m json.tool
```

Open `http://127.0.0.1:8080/` in a browser. The current emphasis is on the JSON API and auditable data, not on presenting a prototype as a complete commercial UI.

> `mktemp` creates a dedicated temporary data directory for this run, reducing the risk of overwriting existing data. Before recording another take, you can call `POST /api/v1/demo/reset`. When finished, stop the process first. Verify the actual path and contents of `$TRACEGATE_DEMO_DIR`, and then remove that temporary directory yourself.

To validate the complete flow without pasting each curl command individually, keep the service running and execute the following in another terminal:

```sh
python3 scripts/demo_smoke.py --wait 30 --journey
```

To include an upstream outage/recovery scenario and 50 measured local-read latency samples:

```sh
python3 scripts/demo_smoke.py --wait 30 --journey --outage-demo --benchmark 50
```

The equivalent Make targets are `make journey` and `make resilience`. The script ends with `SUMMARY {...}`. Preserve that output, and only publish figures explicitly labeled `MEASURED`.

## Video Script

### 0:00–0:30: Positioning and Architecture

Narration:

> TraceGate Edge is an independent synthetic manufacturing traceability project. The edge node enforces deterministic route, identity, material, and repair rules locally. SQLite stores current WIP, event history, and a transactional outbox. A separate mock receiver represents the upstream system. AI may explain reason codes, but it never authorizes PASS.

Show the README architecture diagram and `/api/v1/health`. Emphasize that this is not a production MES and that it contains no real factory data or internal source code.

### 0:30–1:15: Prove Fail-Closed Behavior First

Intentionally ask a new product to skip ahead to the MAC station:

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-wrong-station-0001","acn":"ACN-DEMO-0001","station_code":"ST-ID","result":"PASS"}' \
  | python3 -m json.tool
```

The response should contain `ROUTE_VIOLATION`. Then confirm that WIP remains at `ST-IN`:

```sh
curl -s "$API/api/v1/units/ACN-DEMO-0001" | python3 -m json.tool
```

The point of this scene is that the system did more than display a red message: WIP did not advance incorrectly.

### 1:15–2:00: Identity Is More Than Format

Complete intake normally:

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-register-0001","acn":"ACN-DEMO-0001","station_code":"ST-IN","result":"PASS"}' \
  | python3 -m json.tool
```

Use a syntactically valid synthetic MAC that belongs to `ACN-DEMO-0002`:

```sh
curl -s -X POST "$API/api/v1/mac/check" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-wrong-mac-0001","acn":"ACN-DEMO-0001","interface_name":"wlan0","mac_address":"02:54:47:00:02:01"}' \
  | python3 -m json.tool
```

The response should contain `ASSOCIATION_MISMATCH`. Next, use the correct binding:

```sh
curl -s -X POST "$API/api/v1/mac/check" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-correct-mac-0001","acn":"ACN-DEMO-0001","interface_name":"wlan0","mac_address":"02:54:47:00:01:01"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-identity-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-ID","result":"PASS"}' \
  | python3 -m json.tool
```

Key point: valid format does not imply a valid association. The ACN, interface, and MAC must all match.

### 2:00–3:00: Key Parts and Offline Commit

Collect one controller and two sensors. Intentionally use an approved substitute for the second sensor:

```sh
curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-controller-0001","acn":"ACN-DEMO-0001","part_no":"CTRL-A100","kpsn":"KPSN-DEMO-CTRL-001"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-sensor-0001","acn":"ACN-DEMO-0001","part_no":"SENSOR-T200","kpsn":"KPSN-DEMO-SENS-001"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/key-parts/collect" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-kp-sensor-0002","acn":"ACN-DEMO-0001","part_no":"SENSOR-T201","kpsn":"KPSN-DEMO-SENS-002"}' \
  | python3 -m json.tool
```

Now stop the synthetic upstream service. This control is available only in the combined demo:

```sh
curl -s -X POST "$API/api/v1/demo/mes" \
  -H 'Content-Type: application/json' -d '{"online":false}' \
  | python3 -m json.tool
```

Complete the material station:

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-material-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-KP","result":"PASS"}' \
  | python3 -m json.tool

curl -s "$API/api/v1/outbox" | python3 -m json.tool
```

Point out that the local station event and WIP state have committed, while delivery remains `RETRY`/`PENDING`. The system does not falsely claim that the upstream service received the event.

### 3:00–4:00: Recovery, Deduplication, and Traceability

Restore the upstream service:

```sh
curl -s -X POST "$API/api/v1/demo/mes" \
  -H 'Content-Type: application/json' -d '{"online":true}' \
  | python3 -m json.tool

sleep 3
curl -s "$API/api/v1/outbox" | python3 -m json.tool
curl -s "$API/api/v1/units/ACN-DEMO-0001/trace" | python3 -m json.tool
```

Send the exact same material-station request again:

```sh
curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-material-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-KP","result":"PASS"}' \
  | python3 -m json.tool
```

The API should return the existing result and identify the request as an idempotent replay. It must not create another event or advance WIP again.

### 4:00–5:00: Conclusion

Switch the screen to metrics and the claim–evidence matrix:

```sh
curl -s "$API/api/v1/metrics" | python3 -m json.tool
```

Narration:

> The important part is not the number of screens. It is the evidence chain: deterministic validation, atomic local state, durable retry, and idempotent receipt. The next steps are authentication, signed configuration, operational tests, and real I/O adapters—not giving an AI model permission to pass products.

## Extended Scenario: FAIL → Repair → Attempt 2 Retest

This segment is appropriate for the complete GitHub demo; it does not need to fit into the 3–5 minute video.

Create a synthetic failure at `ST-FT` and store the defect ID in a shell variable:

```sh
DEFECT_ID="$(curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-ft-fail-0001","acn":"ACN-DEMO-0001","station_code":"ST-FT","result":"FAIL","reason_code":"SYNTHETIC_TEST_NG","evidence":{"description":"Injected demo failure"}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["defect"]["defect_id"])')"
printf '%s\n' "$DEFECT_ID"
```

Record a repair action, and then complete the repair station:

```sh
curl -s -X POST "$API/api/v1/repairs/actions" \
  -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"demo-repair-action-0001\",\"acn\":\"ACN-DEMO-0001\",\"defect_id\":\"$DEFECT_ID\",\"action_code\":\"RESEAT_SYNTHETIC_MODULE\",\"notes\":\"Synthetic repair evidence only\",\"technician\":\"demo-operator\"}" \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-repair-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-RP","result":"PASS"}' \
  | python3 -m json.tool

curl -s -X POST "$API/api/v1/stations/complete" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"demo-retest-pass-0001","acn":"ACN-DEMO-0001","station_code":"ST-FT","result":"PASS","evidence":{"test_suite":"synthetic-v1"}}' \
  | python3 -m json.tool
```

The trace should preserve the attempt 1 FAIL, repair, and attempt 2 PASS. It must not overwrite the earlier failure.

## Separate Failure Domains (Recommended for the Portfolio Video)

A more credible demonstration runs the components as two processes, or even on two separate machines:

```sh
# Upstream host
TRACEGATE_MOCK_MES_HOST=0.0.0.0 python3 -m tracegate mock-mes

# Edge host; 192.0.2.10 is documentation-only—replace it with an address on your trusted LAN
TRACEGATE_MES_URL=http://192.0.2.10:8081/api/v1/trace-events \
  python3 -m tracegate edge
```

Stop the upstream process to create a real connection failure; do not rely only on a front-end toggle. Before publishing any footage, redact real LAN IP addresses and usernames.

## Demo Acceptance Checklist

- [ ] An out-of-sequence station is rejected, and WIP remains unchanged.
- [ ] A syntactically valid MAC associated with the wrong product is rejected.
- [ ] The material station cannot complete until BOM quantities are satisfied.
- [ ] While the upstream service is offline, the local event and outbox record still exist.
- [ ] After recovery, the event becomes delivered.
- [ ] Replaying the same request does not create a second event.
- [ ] The trace explains every step and reason code.
- [ ] The video contains no real data, paths, IP addresses, accounts, or secrets.

# Day 1–8 Execution Plan

This plan turns the existing TraceGate Edge `0.2.0` tree into a clean public
baseline, then validates the same synthetic MES domain model on a Portenta X8.
It must not be used to copy or describe any employer, customer, or factory
implementation.

## Current-state decision

Do not rename the current package to `v0.1-baseline`: both `pyproject.toml` and
`tracegate/__init__.py` already identify it as `0.2.0`, and the repository now
contains domain, API, outbox, dashboard, tests, Docker, and documentation work.
Use the annotated Git tag `v0.2.0` for the first public candidate. Only use
`v0.1-baseline` if an actual older snapshot is available and can be tagged
truthfully.

## Hardware scope

### Already sufficient for Day 1–8

- Mac development computer.
- Portenta X8 seated on the Portenta Max Carrier.
- A known data-capable cable for the J10 serial console.
- A working power/data connection for the X8.
- A trusted Wi-Fi access point or phone hotspot.
- An Inateck Hyper A60 wired 1D/2D barcode scanner. This is the scanner to
  integrate after the software and X8 baseline are verified; do not buy a
  second USB HID scanner.

The domain model, tests, Git history, and public-safety work happen on the Mac.
The board is a deployment target and separate failure domain, not a prerequisite
for writing the model.

### Useful next purchases, in priority order

1. One Cat5e/Cat6 Ethernet cable for stable SSH and repeatable outage tests.
2. A compatible regulated supply for the Max Carrier X1 jack only if USB-C
   power is unstable. The official range is 6–36 V and the connector is a
   5.5 mm PJ-102A jack; verify the exact plug dimensions and polarity with the
   seller before powering the board.
3. An endurance microSD card for later backup/restore experiments.
4. A heatsink only if sustained-load measurements show that it is needed.

Do not buy LoRa/cellular antennas, a SIM, 18650 battery, Pico, ESP32, FPGA,
camera, relay, or PLC hardware for Day 1–8.

## Day 1 — Freeze and verify the source tree

Work on the Mac:

```sh
cd tracegate-edge
python3 --version
make check
docker compose config --quiet
```

Expected gate:

- Python compile succeeds.
- All tests pass; a skipped network test is not accepted on the final CI run.
- `Public-release check passed` appears.
- Compose configuration is valid.

Review only the files that should be public:

```sh
find . -maxdepth 3 -type f \
  -not -path './.mypy_cache/*' \
  -not -path '*/__pycache__/*' \
  -not -path './data/*' | sort
```

Do not copy terminal transcripts, shell history, screenshots, databases, or
device logs into the repository. They may expose SSIDs, usernames, IPs, device
IDs, SSH fingerprints, or local paths.

## Day 2 — Establish the local Git baseline

The current directory is not yet its own Git repository. Initialize it only
after Day 1 passes:

```sh
git init -b main
git add .
git status --short
git diff --cached --check
```

Before committing, verify that runtime files are ignored:

```sh
git status --ignored --short
python3 scripts/public_release_check.py .
```

Then create the first truthful commit and annotated version tag:

```sh
git commit -m "feat: establish synthetic TraceGate Edge baseline"
git tag -a v0.2.0 -m "Synthetic public portfolio baseline"
git log --oneline --decorate -n 3
```

The tag is a recoverable reference point. Do not move or recreate it after it
has been pushed publicly; fixes should become `v0.2.1`.

## Day 3 — Create GitHub safely

1. In GitHub, create a new personal repository named `tracegate-edge`.
2. Create it as **Private** first.
3. Do not ask GitHub to add a README, `.gitignore`, or license because local
   versions already exist.
4. Copy the repository URL and run the commands GitHub displays, normally:

```sh
git remote add origin GITHUB_REPOSITORY_URL
git push -u origin main
git push origin v0.2.0
```

5. Open the Actions tab and require the existing CI workflow to pass.
6. Inspect the repository in GitHub's web view, including commit history.
7. Choose a license deliberately. MIT is a common portfolio choice, but do not
   imply permission to reuse the code until a license is actually committed.
8. Run the publication checklist again. Only then change visibility to Public.

Minimum provenance statement:

> Independent clean-room educational project. All identifiers, workflows, data,
> and integrations are synthetic. No employer, customer, or factory source
> code, schema, credentials, screenshots, or data are included.

## Day 4 — Verify the MES model, do not rebuild it

The repository already implements:

- Work order and released status.
- Product/ACN identity.
- Versioned route and route steps.
- Key-part groups, approved substitutes, KPSN binding, and quantity.
- Network interface and MAC association.
- Current WIP state and immutable station history.
- Defect, repair action, retest attempt, outbox, and local audit sequence.

Start the local demo:

```sh
python3 -m tracegate demo
```

In another terminal:

```sh
curl -s http://127.0.0.1:8080/api/v1/units/ACN-DEMO-0001
curl -s http://127.0.0.1:8080/api/v1/units/ACN-DEMO-0001/trace
```

Acceptance gate: the first response exposes current WIP, route, interfaces,
BOM progress, and defects; the second exposes ordered station, verification,
component, defect, and repair history.

## Day 5 — Prove the complete business journey

Run:

```sh
python3 scripts/demo_smoke.py --wait 30 --journey
```

This must prove:

1. ST-IN intake.
2. Wrong MAC is rejected.
3. Correct ACN/MAC/interface is verified.
4. Unapproved material is rejected.
5. Approved substitute and exact BOM quantity are accepted.
6. Functional test fails and opens a defect.
7. Repair evidence is recorded.
8. Retest uses a new attempt and passes.
9. Pack and route completion succeed.

Save a sanitized terminal capture only if it contains no hostname, username,
IP, SSID, fingerprint, or local path.

## Day 6 — Prove offline-first behavior

Run the combined journey:

```sh
python3 scripts/demo_smoke.py \
  --wait 30 \
  --journey \
  --outage-demo \
  --delivery-wait 30 \
  --benchmark 50
```

Acceptance gate:

- Events commit locally while the mock upstream is offline.
- WIP and station history remain queryable.
- Recovery delivers the same number of unique upstream receipts.
- No duplicate business record is created.
- Benchmark output is labelled as a measurement from that environment, not a
  production claim.

## Day 7 — Deploy the same source tree to Portenta X8

First, do not flash or upgrade the X8. Record the current state through the J10
console or SSH:

```sh
uname -a
cat /etc/os-release
docker --version
docker ps
ip -4 -o addr show wlan0
```

Sanitize the output before saving it. Current Arduino documentation recommends
a recent X8 Linux image and cites image 746 as a minimum for recent carrier
tutorials, but an upgrade can change the working containers. Back up and finish
the public baseline before attempting any firmware change.

On the Mac, substitute the current X8 address shown by `ip`:

```sh
cd ..
scp -r tracegate-edge fio@EDGE_IP:~/tracegate-edge-v0.2.0
```

On the X8:

```sh
cd ~/tracegate-edge-v0.2.0
TRACEGATE_IMAGE_ID="$(docker inspect x8-devel --format '{{.Image}}')"
docker run --rm --name tracegate-edge-v020 \
  --user "$(id -u):$(id -g)" \
  -p 8080:8080 \
  -v "$PWD:/app" \
  -w /app \
  -e TRACEGATE_DATA_DIR=/app/data \
  --entrypoint python3 \
  "$TRACEGATE_IMAGE_ID" \
  -m tracegate demo
```

From the Mac, open `http://EDGE_IP:8080/`. Restrict this to the trusted local
network because authentication and TLS are not implemented.

## Day 8 — Capture X8 evidence and close the milestone

Capture the following without exposing private network or device identifiers:

- Max Carrier + X8 powered and running.
- Browser showing the TraceGate dashboard from the X8.
- Health endpoint showing `ok: true` and version `0.2.0`.
- One complete synthetic unit trace.
- A controlled upstream outage and recovery.
- `docker ps` showing the project container, with host/device names cropped.

Create a release-evidence note containing:

- Git commit SHA and tag.
- Date and Python/container version.
- Test count and result.
- X8 image version if known.
- Exact synthetic scenario executed.
- Known limitations: trusted LAN only, no TLS/auth/RBAC, no real MES/PLC
  connector, no power-loss proof unless it was actually tested.

Milestone completion means a reviewer can clone the repository, run the same
synthetic scenario, inspect an arbitrary ACN, and distinguish current state from
immutable history and upstream delivery state.

## After Day 8

The next hardware step is to connect and verify the existing Inateck Hyper A60,
not to buy another scanner or compute board. First confirm that it behaves as a
keyboard-style USB input on the Mac, then connect it to a Max Carrier USB-A port
and capture the same scan in the TraceGate dashboard. After that works, add an
isolated I/O or protocol simulator. Only buy MCU or FPGA hardware after
measurements demonstrate a deterministic I/O or latency problem that the Linux
edge cannot solve.

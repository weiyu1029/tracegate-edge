# Security and Public Release Checklist

This is the release gate for publishing TraceGate Edge on a public GitHub repository, LinkedIn, or a portfolio site. Run it again for every release; do not assume that a new version is safe merely because the previous version was safe.

## Threat Model and Limitations

The current version is a single-user educational demonstration intended for a trusted LAN:

- No login, role-based authorization, or multifactor authentication.
- No TLS termination or production certificate management.
- No field-level encryption in the local SQLite database.
- Administrative operations such as demo reset and outbox replay are not suitable for exposure to a public network.
- The standard-library HTTP server is not an Internet-facing production server.
- The mock upstream service is a test receiver, not a production MES.

Therefore: **bind it only to a personal test network, never configure router port forwarding, and never deploy it on a public IP address.**

## Prepublication Data Review

- [ ] Every work order, ACN, serial number, KPSN, MAC address, part number, and route is fictional.
- [ ] MAC addresses use the locally administered bit, for example `02:...`.
- [ ] No real company, customer, supplier, product code name, or internal organization name appears anywhere.
- [ ] No real IP address, hostname, VPN identifier, UNC/shared path, or internal URL appears anywhere.
- [ ] No real database name, schema, table, stored procedure, or API method name appears anywhere.
- [ ] No internal source code, decompiled content, DLL/PDB file, configuration file, or proprietary screen appears anywhere.
- [ ] No production defect description, repair record, operator information, or personally identifiable log appears anywhere.
- [ ] Browser bars, terminal prompts, Wi-Fi details, IP addresses, user names, and file paths are cropped or redacted in screenshots.
- [ ] Do not commit `data/`, SQLite files, WAL/SHM files, coverage output, caches, or real logs.

## Secret Review

- [ ] Run the repository's publication check, when provided: `python3 scripts/public_release_check.py`.
- [ ] Understand the scanner boundary: it traverses `data/` and rejects database/key files and nonexample `.env` files by name, but it does not inspect binaries larger than 2 MB, non-UTF-8 files, files containing NUL bytes, or the contents of images, PDFs, and archives.
- [ ] Perform a separate metadata, OCR, and manual review of binaries, images, and PDFs that the scanner does not inspect. Use a dedicated secret/history scanner for Git history. Passing the publication check does not prove that no data has leaked.
- [ ] Also run `git grep -nEi '(password|secret|token|api[_-]?key|private[_-]?key)'` and review every match manually.
- [ ] Search for common credential formats and private-key headers, including `BEGIN .* PRIVATE KEY`, cloud access keys, and JWTs.
- [ ] Search for email addresses, phone numbers, street addresses, real user names, and absolute home-directory paths.
- [ ] Confirm that `.env`, credential stores, shell history, and IDE workspace files are not tracked.
- [ ] If a secret was ever committed, deleting the current file is not enough: rotate the secret first, then clean the Git history.

## Git Content Review

```sh
git status --short
git ls-files
git diff --check
git log --stat --oneline
```

- [ ] `.gitignore` covers `data/`, `*.db`, `*.db-*`, `*.sqlite-*`, `*.sqlite3-*`, `.env`, caches, and test artifacts.
- [ ] `git ls-files` contains no binary dumps, photographs, emails, chat exports, or temporary attachments.
- [ ] Every README command works from a clean checkout.
- [ ] Links use repository-relative paths or public documentation; no shared-chat links remain.
- [ ] Commit messages contain no customer name, incident number, internal station name, or sensitive information.
- [ ] Licensing terms have been selected; until a license is chosen, do not imply that others may reuse the work freely.

## Functional and Reliability Review

- [ ] All automated tests pass.
- [ ] A fresh data directory completes migration and seeding successfully.
- [ ] Resubmitting the same `request_id + payload` does not create a second business event.
- [ ] Reusing the same `request_id` with a different payload returns a conflict.
- [ ] Wrong-station scans, wrong ACNs, incorrect MAC relationships, unapproved parts, and duplicate KPSNs all fail closed.
- [ ] FAIL -> repair -> new attempt -> retest is covered by tests.
- [ ] When the mock upstream service is interrupted, the outbox retains events; after recovery, it produces only unique receipts.
- [ ] A component re-instantiation test proves that the same SQLite file can resume delivery. Separately, use an actual kill/restart experiment to verify that a pending event persists. The latter currently requires preserved manual evidence because an automated operating-system process-restart test has not yet been added.
- [ ] Dead letters are visible and do not allow the health endpoint to report a fully healthy state.
- [ ] Health and version information match the artifact that is actually running.

## API and Network Security Review

- [ ] Demo administrative endpoints are enabled only in development mode or clearly labeled with their risks.
- [ ] Request bodies have size limits, JSON type validation, and safe error responses.
- [ ] Every SQL statement uses parameterized queries; scanned values are never concatenated into SQL.
- [ ] User-interface output is HTML-escaped so that a barcode cannot be interpreted as HTML.
- [ ] Error responses disclose no stack trace, file path, or database content.
- [ ] The upstream URL comes from environment configuration; tokens are never written into source code.
- [ ] Bind to `0.0.0.0` only on a trusted LAN; otherwise use `127.0.0.1`.
- [ ] Never expose ports 8080 or 8081 to the Internet, and never configure public port forwarding.

## AI Agent Security Review

- [ ] The agent may call read-only health, unit, trace, and metrics APIs only.
- [ ] The agent has no tools for station completion, repair, reset, replay, or configuration writes.
- [ ] Agent responses are clearly labeled as explanations or recommendations, not manufacturing authorization.
- [ ] PASS/FAIL is determined only by deterministic service logic using data and rules.
- [ ] Remove identifiable, confidential, and nonpublic data before sending content to an external model.
- [ ] Prompt-injection content cannot change tool permissions or domain rules.

## LinkedIn Release Review

- [ ] Use the phrase "synthetic demo" or "independent educational project."
- [ ] Do not claim "production-grade," "industrial certified," or "deployed in a factory" without public, verifiable evidence.
- [ ] Reported measurements include hardware/OS, data volume, test method, and date.
- [ ] Report only the offline-event count, latency, and test count actually measured.
- [ ] The video clearly shows the upstream service stopping, the pending count increasing, restart/recovery, and receipt deduplication.
- [ ] Do not present the edge board as mandatory; explain that the project can run on a laptop and that the board supports independent failure-domain and deployment experiments.
- [ ] Do not present AI as the release authority; show it as `explain only`.

## Recommended Public Statement

This statement may appear in the README or a post:

> TraceGate Edge is an independent, synthetic educational project. It does not contain or reproduce any employer, customer, or factory source code, data, schemas, credentials, screenshots, or proprietary workflows. It is not a production MES.

## Requirements Before Productionization

Moving from a portfolio project to a real product would require, at minimum:

- Authentication, RBAC, device certificates, and key rotation.
- TLS/mTLS, network segmentation, firewalls, and a secure-update policy.
- Signed artifacts, an SBOM, and dependency/vulnerability management.
- Backup and restore procedures, storage-wear testing, and power-loss consistency testing.
- Time synchronization, audit retention, privacy controls, and data governance.
- High availability, capacity/load/long-duration soak tests, and fault drills.
- Change control, approval workflows, production runbooks, and on-call ownership.
- Security, quality, and regulatory assessments appropriate to the actual industry.

# Claim-Evidence Matrix

The fastest way for a portfolio project to lose credibility is to make claims that exceed its evidence. This matrix ties each public statement to reproducible evidence in the repository.

## Usage rules

- "Implemented" requires automated tests and a reproducible demo.
- "Measured" requires a saved environment, data volume, date, and raw results.
- Incomplete work may be described only as "designed for" or "planned" and belongs in the roadmap.
- One successful manual screen is a smoke test, not a reliability guarantee.

| Public claim | Minimum evidence | Acceptable wording | Wording to avoid |
| --- | --- | --- | --- |
| Local-first station | A valid operation commits locally while upstream is stopped, and the outbox count increases | "Built a local-first synthetic traceability station." | "Works during any network failure." |
| Durable offline queue | SQLite outbox, outage/retry test, and delivery after reconstructing `Database` and dispatcher against the same database; a full OS process restart still needs automation or retained manual evidence | "Events persist across component re-instantiation and are retried after upstream recovery." Say that events survive a process restart only after collecting real restart evidence | "No event can ever be lost." |
| Idempotent API | Same request/payload replay test and different-payload conflict test | "State-changing commands use request-level idempotency." | "Exactly-once distributed processing." |
| Idempotent upstream | Redeliver the same event and verify that receipt count does not increase | "The mock receiver deduplicates by event ID." | "Compatible with every MES." |
| Route enforcement | Normal route-order and skipped-station tests | "The domain service rejects out-of-route operations." | "Production-certified process control." |
| MAC association | Tests for malformed, unknown, other-unit, wrong-interface, and correct MAC bindings | "Separates format validation from product/interface association." | "Secure MAC provisioning system." |
| Key-part traceability | Tests for an unapproved part, approved alternate, excess quantity, and duplicate KPSN | "Models synthetic BOM groups and unique component bindings." | "Supports all enterprise BOM rules." |
| Repair/retest | FAIL -> defect -> repair -> attempt + 1 -> retest test | "Preserves failure history across repair and retest attempts." | "Full QMS/repair platform." |
| Transactional consistency | Force an outbox insert failure with a SQLite trigger and verify no half-complete event, WIP, outbox, or idempotency state remains | "A SQLite fault-injection test verifies local station state and outbox writes roll back together." | "ACID across edge and cloud." |
| Causal trace ordering | Create event -> verification -> event within the same millisecond and verify that the timeline and outbox follow the transaction-allocated `audit_sequence` | "A database-local audit sequence preserves causal ordering across synthetic record types." | "Globally ordered across devices" or "trusted timestamp." |
| Dead-letter handling | Reach the maximum attempt count, show degraded health, and exercise manual replay | "Failed delivery becomes visible and replayable." | "Self-healing without operator action." |
| Zero external Python dependencies | Clean Python execution plus dependency-file or standard-library inspection | "The runtime uses Python's standard library and SQLite." | "Zero software supply-chain risk." |
| Edge-board deployment | Start on the target board and retain health, route demo, and restart evidence | "Ran the same synthetic service on a small Linux edge device." | "The board is required for the solution." |
| AI safety boundary | No write-capable tool and a policy test or architecture review | "AI is intentionally limited to explanation." | "AI decides whether products pass." |
| Performance | Reproducible benchmark with hardware, OS, Python, data volume, and statistics | "Measured P95 X ms under Y synthetic requests on Z." | "Real-time" or "high performance" without numbers |
| Scale | Defined concurrency, data volume, duration, and error rate | "Completed a Y-event synthetic soak test." | "Factory scale" without a factory-like workload |
| Security | Threat model and authentication/TLS/RBAC tests, which are not complete | "Runs only on a trusted demo LAN." | "Secure," "production-ready," or "zero-trust." |

## Release evidence bundle

For every tag, retain the following files without real data:

```text
evidence/
  VERSION.txt
  TESTS.txt
  ENVIRONMENT.md
  DEMO_RESULTS.json
  BENCHMARK.json          # Generate only after an actual run
  SCREENSHOTS.md          # Link only to sanitized synthetic screens
```

Minimum contents:

- Commit SHA and release tag.
- Python and OS versions.
- Whether Edge and the mock receiver ran on one machine or separate machines.
- Test command, pass/fail counts, and timestamp.
- Demo counts for unique events, receipts, duplicates, pending rows, and dead-letter rows.
- For a benchmark: warm-up method, sample count, percentile calculation, and limitations.

## LinkedIn post template

The following language is safe and verifiable. Replace bracketed values with actual measurements, or delete the sentence if no measurement exists.

> I built TraceGate Edge, an independent synthetic manufacturing traceability project. It models route enforcement, ACN/MAC/component identity, repair/retest attempts, and a SQLite transactional outbox. In a controlled demo, I stopped the mock upstream, committed [N] synthetic station events locally, and delivered [N] unique receipts after recovery with [0] duplicate business records. AI is limited to explaining deterministic reason codes; it never authorizes PASS. This is an educational reference implementation, not a production MES.

Add `Pending events remained available across an Edge process restart.` only after actually stopping and restarting Edge while retaining evidence of the same database and its pending/receipt state. The repository does not currently include an automated process-restart test.

## Screenshot and video evidence sequence

1. `health` displays version, schema, and initial outbox state.
2. Correct identity validation and a normal station transition.
3. Rejection of a well-formed MAC with the wrong association.
4. Rejection of a skipped station or unsatisfied BOM requirement.
5. Mock upstream stops and the pending count increases.
6. Optional, with separately retained manual evidence: Edge restarts and pending events remain. This is not part of the current automated suite.
7. Mock upstream recovers, delivered count increases, and receipts remain deduplicated.
8. The trace timeline shows FAIL, repair, attempt 2 retest, and the final result.

Video narration must distinguish observed facts from architectural goals.

## Current status labels

Use these labels for significant README capabilities:

- `Implemented + tested`
- `Implemented; manual evidence only`
- `Designed; not yet implemented`
- `Research idea`

Do not use the ambiguous word "supported" for all four maturity levels.

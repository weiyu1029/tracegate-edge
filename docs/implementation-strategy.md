# Multi-language implementation strategy

TraceGate has one working **edge runtime implementation**: the Python reference
implementation in the repository root. The repository also contains a scoped
C# / ASP.NET Core application under `labs/dotnet-mes/`. That application is a
companion MES learning lab, not a conformant port of the edge runtime. This
distinction allows the portfolio to demonstrate multiple languages without
overstating feature parity.

## Why Python is the reference today

Python keeps the first implementation easy to inspect, run, and test on a
small Linux edge node. The runtime uses the standard library and SQLite, which
makes the system practical for an educational hardware demonstration while
keeping the domain behavior visible in code.

The Python implementation remains the source of truth until a language-neutral
contract suite exists and another implementation passes it.

## Stable boundaries

Future ports should preserve these boundaries:

1. **Domain rules** decide route eligibility, identity relationships, BOM
   requirements, defect state, repair evidence, and retest attempts.
2. **The local transaction** commits WIP, trace events, idempotency state, and
   the outbox row together.
3. **The delivery worker** performs network I/O only after the local business
   transaction commits.
4. **The upstream receiver** deduplicates by event identity and rejects an
   event ID reused with a different payload.
5. **AI remains read-only** and may explain evidence, but it never authorizes a
   station transition.

These are behavioral contracts, not Python-specific implementation details.

## Current and planned repository evolution

The current layout keeps the stable Python commands at the repository root and
isolates the smaller C# domain under `labs/`:

```text
tracegate/                     Python reference edge runtime
tests/                         Python domain, HTTP, and outbox tests
labs/dotnet-mes/               scoped C# MES learning application
docs/feature-matrix.md         explicit capability and parity limits
```

When a shared contract and a true alternative runtime exist, the repository
can evolve toward:

```text
contracts/                     language-neutral API and event schemas
conformance/                   shared fixtures and black-box behavior tests
implementations/
  python/                      current reference implementation
  typescript/                  optional operator/API implementation
  rust/                        optional resource-constrained edge runtime
  go/                          optional delivery or gateway service
hardware/                      scanner, GPIO, and board integration notes
docs/                          architecture, evidence, and decision records
```

The .NET lab stays under `labs/` unless it adopts the shared contract and passes
tests appropriate to a production component role. A future migration should be
a dedicated, tested change. Existing commands and Git history should remain
usable through wrappers or documented replacements.

## Candidate languages and their purpose

| Language | Useful project role | Evidence required before claiming support |
| --- | --- | --- |
| C# | Current work-order and operator-facing MES learning lab | Its own 22-test integration suite; full edge parity is not claimed |
| TypeScript | Operator UI, API gateway, or browser-facing tooling | Contract tests, idempotency tests, and a runnable demo |
| Rust | Small-footprint edge service or device integration | SQLite transaction parity, recovery tests, and target-board measurements |
| Go | Concurrent delivery worker or upstream gateway | Retry/lease parity, failure injection, and interoperability tests |
| C/C++ | MCU, GPIO, CAN, or scanner bridge | Hardware-in-the-loop tests and a documented safety boundary |

Except for the explicitly scoped C# lab, this table is a roadmap rather than a
statement that additional implementations already exist.

## Conformance requirements

A future implementation should not be labeled "TraceGate-compatible" until it
passes shared black-box scenarios for:

- route enforcement and fail-closed behavior;
- MAC-to-product and interface validation;
- BOM quantity, approved substitutes, and unique key-part serials;
- failure, defect, repair, new-attempt retest, and final completion;
- request idempotency and conflicting-payload rejection;
- atomic event, WIP, outbox, and idempotency writes;
- offline retention, retry, dead-letter, replay, and receiver deduplication;
- deterministic trace ordering within one edge database.

Measured performance, restart behavior, and hardware compatibility must be
reported separately for each language and target. Passing functional
conformance in one runtime does not prove the same durability or performance on
another runtime or board.

## Near-term sequence

1. Keep hardening and validating the Python reference on the Portenta X8.
2. Extract OpenAPI, event schemas, and sanitized fixtures from observed
   behavior.
3. Build a language-neutral conformance runner.
4. Connect the edge outbox to a versioned receiver and prove outage recovery
   across the process boundary.
5. Promote or add another language component only for a clearly defined role.
6. Keep the [feature matrix](feature-matrix.md) current for every language and
   component.

This sequence keeps the portfolio credible: every language demonstrates a
reasoned engineering tradeoff instead of duplicating the same code for visual
variety.

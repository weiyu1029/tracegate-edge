# Component scope and feature matrix

TraceGate contains a flagship edge runtime and a deliberately narrower companion lab. They share a manufacturing traceability theme, but they are not interchangeable implementations.

## Why they live together

The former standalone projects both modeled work orders, serial-level state, ordered routes, PASS/FAIL behavior, SQLite history, and idempotent HTTP mutations. Publishing them as two independent MES projects made the portfolio story look repetitive. The historical C# repository is therefore preserved as the archived [`tracegate-dotnet-mes-lab`](https://github.com/weiyu1029/tracegate-dotnet-mes-lab), while this repository is the canonical active portfolio.

The consolidated repository presents one TraceGate portfolio with two clearly scoped components:

- **TraceGate Edge** is the flagship Python reference runtime for a small Linux edge node.
- **TraceGate MES Lab** is a focused C# / ASP.NET Core companion lab for work-order administration and an operator-facing UI.

## Capability matrix

| Capability | Edge runtime | .NET MES lab |
| --- | --- | --- |
| Language | Python 3.10+ | C# / .NET 10 |
| Primary role | Deterministic station gateway | MES workflow learning lab |
| Target environment | Portenta X8-class Linux device or laptop | Local workstation |
| Route model | Versioned seven-step synthetic route | Fixed three-step synthetic route |
| Work-order creation | Seeded reference data | Dynamic UI and API |
| Unit registration | Seeded reference data | Dynamic UI and API |
| MAC-to-interface validation | Implemented | Not implemented |
| BOM groups and KPSN genealogy | Implemented | Not implemented |
| FAIL, defect, repair, and new attempt | Implemented | Simplified hold, release, and retest |
| Transactional outbox | Implemented | Not implemented |
| Retry, lease, dead letter, and replay | Implemented | Not implemented |
| Idempotent write requests | Implemented | Implemented |
| Append-only event protection | Application and database controls | SQLite triggers |
| Operator dashboard | Compact edge dashboard | Full responsive workspace |
| Event CSV export | Not implemented | Implemented with formula-prefix protection |
| Hardware runbook | Portenta X8 documented | Not applicable |
| Automated tests | Domain, HTTP, outbox, and hardening | 22 real-process API and SQLite tests |

## Current integration status

The applications are co-located, not yet connected. Their identifiers, routes, endpoint shapes, database schemas, and event formats differ. The .NET lab must not be described as a feature-equivalent port of the Python edge runtime.

The next integration milestone is a versioned shared event contract plus an end-to-end scenario in which TraceGate Edge queues a synthetic station event while the hub is unavailable and delivers it after recovery. Until that test exists, diagrams and demos must show the applications as separate scopes.

## Future language additions

New languages belong in the repository only when they have a clear role:

- operator clients under `clients/`;
- protocol or hardware bridges under `bridges/`;
- scoped learning applications under `labs/`;
- full alternative runtimes only after they pass the complete conformance suite.

A language name alone is not evidence of support or parity. See the [implementation strategy](implementation-strategy.md) for the evidence rules.

# TraceFlow MES import provenance

The C# application under `labs/dotnet-mes/` was consolidated from the author's former standalone repository:

- Source: <https://github.com/weiyu1029/traceflow-mes>
- Imported source commit: `9ccda92d4da194f356ba201c95053da3d347098a`
- Import date: 2026-10-23
- Original license: MIT; preserved at `labs/dotnet-mes/LICENSE`

## Changes made during consolidation

- Renamed the public product from **TraceFlow MES** to **TraceGate MES Lab**.
- Renamed the C# project and namespace from `TraceFlow.Web` to `TraceGate.MesLab`.
- Moved the self-contained application into `labs/dotnet-mes/`.
- Rewrote the lab README in English and documented its non-parity boundary.
- Omitted the former Traditional Chinese learning and demo guides from the canonical US-facing portfolio.
- Recreated public screenshots after the product rename.
- Added the lab to the canonical repository's CI without merging unrelated Git histories.

The original repository had two commits. A selective squash import was chosen to keep the canonical history and file tree readable. This note preserves the exact source reference without presenting the lab as a conformant TraceGate Edge implementation.

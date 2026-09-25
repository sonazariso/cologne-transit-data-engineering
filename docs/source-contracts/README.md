# Source Contracts

This directory will hold approved source-contract documentation. The current
Stage 02A.1 document is evidence for the Legacy implementation audit; it is
not an approved final MDD/TRIAS response contract.

Current Stage 02 evidence:

- [Stage 02A.1 — Legacy Implementation Audit](../stages/02-mdd-trias-source-contract-audit.md)

Stage 02A.2 — Controlled MDD Raw-Response Probe — is **Blocked**. Its preflight
completed, but `MDD_API_KEY` was not available in the Codex execution
environment (`MDD_API_KEY` present = **NO**). HTTP attempts = **0** and probe
execution did not occur. The blocker is API-key unavailability in the
execution environment; no source-contract conclusion was made from this
failed preflight.

The actual MDD response shape, field coverage, and raw-response retention
decision remain open until the controlled probe is completed and reviewed.

Planned documents include:

- GTFS source contract / field inventory
- MDD/TRIAS request contract
- MDD/TRIAS response field inventory
- field retention matrix: defined → returned → coverage → parsed → persisted → analytical purpose
- raw-payload retention decision

Do not treat the Legacy parser audit as proof of MDD capability or actual live
response behavior. Stage 1 and Stage 2 must establish the final contracts from
authoritative documentation, Legacy source evidence, and the controlled raw
response probe.

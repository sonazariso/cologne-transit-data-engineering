# Stage 11 — Power BI Model Completion Decision

**Date:** 2026-09-29  
**Decision:** [D-042](../DECISION-LOG.md#d-042--stage-11-power-bi-model-design)  
**Status:** `COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED`

## Decision

Stage 11 is complete as an approved semantic-model design and contract. The
authoritative design is [11-power-bi-model-design.md](11-power-bi-model-design.md).

The approved contract defines:

- Static Schedule / Network Context, Operational Evidence, and Matching / Data
  Quality subject areas;
- exact semantic table grains, including Stage 6 pattern-level schedule facts,
  `BridgeServiceDate` ownership of active Service + Date expansion, the future
  Daily Schedule projection, and the Stage 10 operational fact grain;
- one-to-many, single-direction relationships with no fact-to-fact,
  bidirectional, or unsafe text-ID joins;
- one governed Date dimension with Auto Date/Time disabled;
- a strict VRS/static identity versus canonical operational identity boundary;
- explicit DAX measure groups, denominators, NULL handling, and the
  disconnected `Estimated Delay Threshold (Minutes)` parameter;
- estimated-delay, bay, Situation, Data Quality, sampling, network-coverage,
  executive, and technical-reporting language; and
- a future PBIP + TMDL + PBIR implementation and Power BI Desktop QA
  workflow.

## Current-stage boundaries

No PostgreSQL/database work, SQL execution, source load, Power BI Desktop
execution, datasource binding, refresh, render QA, PBIP/TMDL/PBIR/PBIX
creation, or legacy-model copy occurred. Stage 10 retains its existing status:

`NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED`

The Stage 10 fact remains a future HIGH-only operational projection. Stage 9
MEDIUM/LOW/UNMATCHED decisions remain in the separate Data Quality subject
area. No Actual Physical Delay, cancellation, or causal Situation semantics
were introduced.

Stage 12 — 50-Station Selection — was not started.

## Acceptance result

All Stage 11 acceptance criteria in the design document pass. The repository
contains no fabricated `.pbip`, `.tmdl`, `.pbir`, `.pbix`, or `model.bim`
artifact. The remaining implementation and Desktop validation steps are
explicitly deferred until later authorization.

**FINAL STATUS:** `COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED`

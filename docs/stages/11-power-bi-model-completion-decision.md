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
- an explicit distinction between the semantic `Dim Scheduled Trip` projection
  and the physical Stage 6 scheduled-trip fact, preserving the approved
  semantic-to-fact paths while prohibiting a physical fact-to-fact join;
- one governed Date dimension with Auto Date/Time disabled;
- a strict VRS/static identity versus canonical operational identity boundary;
- explicit DAX measure groups, denominators, NULL handling, and the
  disconnected `Estimated Delay Threshold (Minutes)` parameter;
- a service-date limitation contract: Date-filtered matching populations and
  coverage use only valid `ServiceDate` values, while missing-service-date
  decisions remain explicit Data Quality evidence through a separate measure;
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

Sampling-era filtering is intentionally limited in the current contract:
`Dim Sampling Era` currently filters the `Fact Matching Decision` population
only through the approved `SamplingEraKey` relationship. Data Quality
classifications carried by those matching decisions inherit that context. The
separate independent `Fact Data Quality Issue` is not Sampling-Era-filtered in
the base model, and `Data Quality Issue Count` must not be described or
implemented as Sampling-Era-filterable under that model. `Dim Sampling Era` is
not an operational KPI slicer and has no approved relationship to `Fact
Operational Service Event`. A future relationship to the independent issue
fact or an operational-event projection requires explicit `SamplingEraKey`
lineage at that fact's grain, with validated projection grain, key uniqueness,
historical-era semantics, and lineage; a cross-era operational event must not
be assigned arbitrarily. Until that exists, operational KPI analysis must use
one compatible era/time period or show a clear coverage/methodology warning.

Stage 12 — 50-Station Selection — was not started.

## Acceptance result

All Stage 11 acceptance criteria in the design document pass. The repository
contains no fabricated `.pbip`, `.tmdl`, `.pbir`, `.pbix`, or `model.bim`
artifact. The remaining implementation and Desktop validation steps are
explicitly deferred until later authorization.

Date-filtered matching coverage is explicitly service-date-scoped, and report
or tooltip text must warn that decisions without a valid service date are
outside the Date slice and remain separately reported Data Quality evidence.

**FINAL STATUS:** `COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED`

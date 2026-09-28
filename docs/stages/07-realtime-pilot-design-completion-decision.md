# Stage 7 — Realtime Pilot Design Completion Decision

**Date:** `2026-09-28`  
**Decision:** [D-031](../DECISION-LOG.md#d-031--stage-7-realtime-pilot-design)  
**Final status:** `COMPLETE — APPROVED PILOT DESIGN`  
**Stage 8 status:** `NOT STARTED / NEXT`

The [Stage 7 realtime pilot design](07-realtime-pilot-design.md) is the
authoritative detailed contract. This closeout separates previously observed
source evidence, the approved bounded pilot design, deferred implementation,
and remaining pilot risks.

## VALIDATED SOURCE FACT

- The approved realtime source direction remains MDD/TRIAS as the targeted
  primary source and GTFS-RT as complementary/secondary. The two source
  namespaces and observation streams remain separate before any later
  reconciliation.
- Prior bounded MDD evidence established a working TRIAS 1.2 request path at
  `https://mdd.gorheinland.com/delfi` using `POST`, XML, and `x-api-key`
  authentication, with source response and call-chain, estimate, bay,
  operational-status, Situation, and service-information structures observed
  in the documented evidence. This is not a provider-wide coverage claim.
- The prior controlled and lifecycle evidence did not establish provider-wide
  `RecordedAtTime` availability or absence. Repeated prospective observation
  remains necessary for any later timing conclusion.
- Stage 5 already defines the compatible logical source grains for one MDD
  response snapshot, one stop-event result, one call occurrence, one Situation
  occurrence, one stop-event/Situation association, and one service-attribute
  occurrence. These are logical architecture contracts, not physical tables.
- The project-level conservative MDD ceiling remains `250,000` HTTP requests
  per month, with shared-caller usage uncertainty explicitly retained.

These facts come from the existing approved Stage 2, Stage 3, Stage 5, and
Stage 6 documents. Stage 7 did not make a network request or add new source
evidence.

## APPROVED PILOT DESIGN DECISION

The following Stage 8 design is approved and fixed:

- **Source:** MDD/TRIAS only for the primary one-source pilot. GTFS-RT is not
  included in the Stage 8 loop; Stage 9 owns cross-source matching.
- **Station:** Köln Hbf, MDD `StopPointRef` `de:05315:11201`, selected for
  source/parser/persistence validation and not for network representativeness.
- **Request:** TRIAS 1.2 `POST` to the approved `/delfi` endpoint, XML content
  type, `MDD_API_KEY` environment variable only, `arrival`,
  `IncludePreviousCalls=true`, `IncludeOnwardCalls=true`,
  `IncludeRealtimeData=true`, and current UTC request/reference timestamp.
- **Result limit:** `NumberOfResults=20` for the bounded pilot only; it is not
  an HTTP request count or a production default.
- **Time window:** omitted; semantically available but not selected for the
  pilot. `IncludeOperatingDays` is also omitted.
- **Cadence:** one request slot every 60 seconds, at most 60 minutes, and at
  most 60 scheduled logical MDD requests.
- **Concurrency:** no concurrent requests, overlap protection, no catch-up
  burst, and `SCHEDULED_SLOT_SKIPPED_OVERLAP` for an overlapping skipped slot.
- **Timeout/retry:** 30-second timeout and zero retries. Every actual attempt
  is recorded.
- **Early stop:** missing credential means zero attempts; 401/403 stops for
  authentication/authorization evidence; 429 stops for rate-limit evidence;
  three consecutive failed request slots stop the pilot. A successful request
  resets the consecutive-failure count.
- **Sampling lineage:** every observation identifies the versioned,
  non-production policy `MDD_STAGE_8_KOELN_HBF_60S_60MIN_V1`.
- **Retention:** the complete approved Stage 2 source-field selection is
  preserved, including independent timing fields, call scope, bays,
  operational flags, Situation/attribute/service-information evidence, errors,
  and parser/run lineage.
- **Timing:** `TimetabledTime`, `EstimatedTime`, `RecordedAtTime`, and
  `ObservedAt` remain distinct. Source `RecordedAtTime` is nullable, exact,
  source-only, and never synthesized; first observation lineage is retained.
- **History:** repeated observations and equal-valued later observations are
  preserved; an exact replay is idempotent without collapsing legitimate later
  observations.
- **Situation semantics:** Situation and `IncidentCall` evidence is append-only
  context, not a normalized or proven root cause. `IncidentCall` is not
  `ErrorMessage`.
- **Raw exception:** MDD pilot responses may be held temporarily outside Git
  in a mode-700 directory with mode-600 files, after an API-key-content check,
  without credentials, for at most 24 hours after the run, and must be cleaned
  up after review.
- **Pilot persistence:** protected append-only local JSONL artifacts mirror the
  approved Stage 5 source grains diagnostically. They are not PostgreSQL, a
  production database, or a mutable latest-state store.
- **Reconciliation:** parsed stop-event, call, Situation, association, and
  attribute occurrences must reconcile one-for-one or by explicit source
  occurrence/scope rules; unmapped fields become parser-gap evidence.
- **Gaps:** missingness is classified and never relabelled as on time, served,
  cancelled, no service, or no delay.
- **Actual Delay:** remains **NOT APPROVED**. A non-null `RecordedAtTime` is
  only candidate evidence and may support a diagnostic difference, not a
  production KPI.

## DEFERRED IMPLEMENTATION DECISION

The following work remains outside Stage 7 and requires later authorization or
stage ownership:

- Stage 8 host execution and all MDD requests;
- any GTFS-RT request or collection loop in the pilot;
- runtime collector, scheduler, parser, or persistence code;
- PostgreSQL installation, migration, schema/table creation, SQL, or database
  access;
- production cadence, station tiers, result-limit defaults, or retry policy;
- canonical cross-source matching and operational facts; and
- Actual Delay or prediction-accuracy production KPIs.

The local documentation checks for this closeout are not pilot execution and
do not change the deferred implementation boundary.

## OPEN PILOT RISK

- `RecordedAtTime` may be absent in the one-hour sample. That is a valid pilot
  result and does not prove provider-wide absence.
- One station and one bounded hour cannot establish Cologne-wide, mode-wide,
  operator-wide, or provider-wide coverage percentages.
- Response/application error behavior, parser gaps, persistence failures,
  response duration, event density, and cleanup behavior can only be measured
  by Stage 8 execution.
- The conservative quota ceiling is not a live account-usage measurement and
  may be shared with other callers.
- The pilot does not validate production performance, 50-station behavior,
  monthly production quota, or network reliability.
- Source Situation association remains non-causal until a later evidence model
  establishes a defensible relationship.

## Acceptance and closeout

The Stage 7 design document covers all 28 requested acceptance criteria:

1. one primary source;
2. one station and rationale;
3. exact request contract;
4. `NumberOfResults` decision;
5. explicit `TimeWindow` decision;
6. cadence, duration, and request ceiling;
7. timeout and retry behavior;
8. quota accounting;
9. overlap and catch-up behavior;
10. authentication, rate-limit, and failure stop rules;
11. source-field retention linked to earlier contracts;
12. unchanged timing semantics;
13. estimate-history preservation;
14. `RecordedAtTime` validation rules;
15. non-causal Situation/Cause semantics;
16. raw-capture purpose, retention, storage, access, and cleanup;
17. non-PostgreSQL pilot persistence;
18. parser-to-persistence reconciliation;
19. exact-replay versus repeated-observation idempotency;
20. gap/failure classification;
21. Stage 8 metrics;
22. representativeness limitations;
23. GTFS-RT exclusion;
24. Actual Delay remaining unapproved;
25. Stage 8 remaining not started;
26. no network/database/SQL/runtime execution;
27. synchronized documentation/status; and
28. passing `git diff --check`.

The final repository validation confirms the documentation and status
conditions. No host-network probe, MDD request, GTFS-RT request, SQL statement,
database access, or runtime pilot execution occurred.

## Final decision

**Stage 7 is COMPLETE as a pilot-design stage.**

The approved handoff is the bounded MDD-only Stage 8 pilot described in the
Stage 7 design document. **Stage 8 execution has NOT occurred and remains NOT
STARTED / NEXT.** Stage 6 remains `COMPLETE — APPROVED DESIGN; PHYSICAL
IMPLEMENTATION DEFERRED`, and Stage 5 remains `COMPLETE — APPROVED
ARCHITECTURE`.

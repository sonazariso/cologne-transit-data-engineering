# Stage 7 — Realtime Pilot Design Completion Decision

**Date:** `2026-09-28`  
**Decision:** [D-033](../DECISION-LOG.md#d-033--stage-7-3-second-pilot-and-operational-planning-envelope), superseding the historical policy detail in [D-032](../DECISION-LOG.md#d-032--stage-7-pilot-cadence-and-recordedattime-correction)<br>
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
  `RecordedAtTime` availability or absence, or an empirical minimum visibility
  period. Repeated prospective observation remains necessary for any later
  timing conclusion.
- Stage 5 already defines the compatible logical source grains for one MDD
  response snapshot, one stop-event result, one call occurrence, one Situation
  occurrence, one stop-event/Situation association, and one service-attribute
  occurrence. These are logical architecture contracts, not physical tables.
- The project-level conservative MDD ceiling remains `250,000` HTTP requests
  per month, with shared-caller usage uncertainty explicitly retained.
- Retained MDD lifecycle/probe evidence shows observed request elapsed times
  predominantly below the proposed 3-second interval, from sub-second
  observations to approximately 2 seconds in the retained artifacts. This is
  bounded design evidence, not a provider response-time guarantee.

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
  `IncludeRealtimeData=true`, and a fixed `PilotAnchorDepArrTime` selected at
  pilot start in UTC and reused for every request. The per-request request
  timestamp remains lineage; the `DepArrTime` anchor does not advance with
  wall-clock time.
- **Result limit:** `NumberOfResults=20` for the bounded pilot only; it is not
  an HTTP request count or a production default.
- **Time window:** omitted; semantically available but not selected for the
  pilot. `IncludeOperatingDays` is also omitted.
- **Cadence:** fixed start-to-start request slots at `T0`, `T0 + 3s`,
  `T0 + 6s`, and so on for at most 60 minutes, with at most 1200 scheduled
  logical MDD slots and 1200 normal HTTP attempts.
- **Concurrency:** no concurrent requests, overlap protection, no catch-up
  burst, and `SCHEDULED_SLOT_SKIPPED_OVERLAP` for an overlapping skipped slot.
  The 30-second timeout does not permit overlap; the scheduler remains fixed-
  start and sequential. Response completion must not add three seconds or
  shift the next fixed slot.
- **Timeout/retry:** 30-second timeout and zero retries. Every actual attempt
  is recorded.
- **Early stop:** missing credential means zero attempts; 401/403 stops for
  authentication/authorization evidence; 429 stops for rate-limit evidence;
  three consecutive failed request slots stop the pilot. A successful request
  resets the consecutive-failure count.
- **Quota:** the conservative ceiling remains `250,000` MDD HTTP requests per
  month. The operational planning envelope is `207,500–210,000` requests per
  month (`83–84%`), with `40,000–42,500` requests (`16–17%`) unallocated as
  headroom. The 1200-attempt one-hour pilot maximum is `0.48%` of the full
  ceiling and approximately `0.57–0.58%` of the operational planning
  envelope; actual consumption can be lower because skipped-overlap slots do
  not create HTTP attempts. This is a bounded diagnostic pilot cadence, not an approved
  production cadence; Stage 13 owns production station tiers, cadence,
  staggering, retry policy, overlap analysis, and the full monthly request
  projection inside that envelope.
- **Sampling lineage:** every observation identifies the versioned,
  non-production policy `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`. V1 (60 seconds)
  and V2 (10-second fixed-anchor) remain historical policy records.
- **Retention:** the complete approved Stage 2 source-field selection is
  preserved, including independent timing fields, call scope, bays,
  operational flags, Situation/attribute/service-information evidence, errors,
  and parser/run lineage.
- **Timing:** `TimetabledTime`, `EstimatedTime`, `RecordedAtTime`, and
  `ObservedAt` remain distinct. If source `RecordedAtTime` is observed once in
  any retained `THIS`, `PREVIOUS`, or `ONWARD` context for either arrival or
  departure, its exact value is written immediately to append-only pilot
  persistence with event/source identity, call scope, context, response
  identity, `ObservedAt`, and first-seen observation timestamp. It is
  nullable, source-only, never synthesized, and later `NULL` or disappearance
  cannot erase it.
- **History:** every successful 3-second response remains a distinct
  observation, including when `EstimatedTime` or `RecordedAtTime` is unchanged;
  an exact replay of the same response/run identity is idempotent without
  collapsing legitimate later observations. The first successful response is
  the initial cohort/baseline observation; later responses use the same fixed
  anchor, and result additions/removals/reordering are lifecycle evidence.
- **Situation semantics:** Situation and `IncidentCall` evidence is append-only
  context, not a normalized or proven root cause. `IncidentCall` is not
  `ErrorMessage`.
- **Raw exception:** the increased V3 response count does not change the
  approved bounded exception: MDD pilot responses remain temporary, outside Git,
  in a mode-700 directory with mode-600 files, after an API-key-content check,
  without credentials, for at most 24 hours after the run, and are deleted
  after successful Stage 8 evidence extraction/reconciliation.
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

- `RecordedAtTime` may be absent in the one-hour sample. The result
  `RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT` means absence in the observed sampling window,
  not provider-wide absence.
- Three-second polling materially reduces the risk of missing a transient
  `RecordedAtTime` occurrence but cannot mathematically guarantee capture. No
  positive lifecycle sample establishes the field's minimum visibility period.
  If Stage 8 shows a relevant source field appearing and disappearing within
  the 3-second interval, a separately approved shorter bounded validation may
  be required; this does not pre-approve a shorter production cadence.
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

## Current Stage 8 diagnostic addendum — 2026-09-29

This addendum preserves the historical Stage 7 closeout above while recording
the later Stage 8 authorization evidence. The 2026-09-28 Stage 8 attempt
returned HTTP `401` with the sanitized message that access to the data provider
was not enabled. The separate S40 diagnostic is implemented at
[`scripts/stage_08_authorization_request_contract_diagnostic.py`](../../scripts/stage_08_authorization_request_contract_diagnostic.py)
and uses only the required sequential A/B/C decision tree.

The available repository-process preflight reported `MDD_API_KEY: MISSING`, so
Control A and its conditional B/C branch were not executed. No Stage 8 request
parameter changed, the one-hour pilot was not started, the future 50-station
objective remains unchanged, and Stage 9+ work was not started. The historical
Stage 02 HTTP 200 remains historical evidence; current Stage 8 authorization
is not proven until the diagnostic can run with the restored known working
Legacy-equivalent credential.

### Current Stage 8 implementation correction — S41

The Stage 8 diagnostic now recognizes the observed German supplier-access
response using normalized stable phrases as well as the existing English
supplier-access wording. It still requires supplier-access evidence in the
message and does not classify a generic HTTP `401` as that condition. Offline
validation confirms that the exact observed German response sets
`data_supplier_access_message=true` and selects
`RESULT_SET_BREADTH_PROVIDER_ACCESS_INTERACTION` for A=`200`/B=`401`.

The diagnostic now hands permitted decisions into the existing single
`run_pilot` path with an explicit effective station, `StopPointRef`,
`NumberOfResults`, and versioned sampling/request policy. The pilot creates a
new fixed start-time `DepArrTime` anchor for itself; it never reuses a
diagnostic control timestamp. No live controls or full pilot were executed in
the repository/Codex process for this correction. That process did not inherit
`MDD_API_KEY`; the user's separate local Terminal had previously reported it
present. Those process/environment reports are not contradictory and do not
establish whether the separate Terminal credential is correct or incorrect.

# Decision Log

This file indexes approved project-level decisions. Detailed records may later be added under `docs/decisions/` when a decision needs alternatives/rationale captured separately.

| ID | Date | Decision | Status |
|---|---|---|---|
| D-001 | 2026-09-25 | New project name: **Cologne Transport Intelligence** | Approved |
| D-002 | 2026-09-25 | New GitHub repository: `sonazariso/cologne-transit-intelligence` | Approved |
| D-003 | 2026-09-25 | Legacy repository remains reference/evidence and is not bulk-copied | Approved |
| D-004 | 2026-09-25 | macOS working tree is authoritative; Windows uses VMware shared folder | Approved |
| D-005 | 2026-09-25 | Project objective is management-oriented System Performance BI | Approved |
| D-006 | 2026-09-25 | Actual Delay using validated `RecordedAtTime` evidence is the primary timing objective | Approved |
| D-007 | 2026-09-25 | Estimated timing is secondary and may support prediction-accuracy analysis | Approved |
| D-008 | 2026-09-25 | Core analytical geography is performance inside Cologne, not whole routes merely passing through | Approved |
| D-009 | 2026-09-25 | ICE/IC/NightJet/FlixTrain/Thalys are outside core KPI scope for now | Approved |
| D-010 | 2026-09-25 | No new MDD polling before Stage 1 quota/source review | Approved |
| D-011 | 2026-09-25 | Every stage requires an explicit approval gate | Approved |
| D-012 | 2026-09-25 | TRIAS 1.2 is the semantic contract for the currently audited MDD interface; see the [Stage 02 source-contract package](source-contracts/README.md) | Approved |
| D-013 | 2026-09-25 | `Estimated Delay` may be calculated only as `EstimatedTime - TimetabledTime` and must be labelled explicitly; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-014 | 2026-09-25 | Actual Delay is not approved until a validated actual-time source is observed and sufficiently validated; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-015 | 2026-09-25 | `RecordedAtTime` is the TRIAS actual/recorded-time candidate, but was not observed in Stage 02 controlled evidence; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-016 | 2026-09-25 | `NotServicedStop` is not equivalent to journey cancellation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-017 | 2026-09-25 | `PlannedBay` and `EstimatedBay` remain separate source fields; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-018 | 2026-09-25 | Situation references establish association, not causal explanation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-019 | 2026-09-25 | The tested historical StopEvent interface cannot be relied on to reconstruct past realtime/actual operational state; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-020 | 2026-09-25 | Prospective realtime collection is architecturally important; see the [collector requirements](collector-design/realtime-collector-requirements.md) | Approved |
| D-021 | 2026-09-25 | Permanent/automatic full raw MDD payload retention is not the default source-of-record strategy; see the [raw-retention decision](source-contracts/mdd-trias-raw-retention.md) | Approved |
| D-022 | 2026-09-25 | The conservative documented project request ceiling remains 250,000 requests/month until stronger provider/account evidence supersedes it; see the [Stage 02 request contract](source-contracts/mdd-trias-request-contract.md) | Approved |
| D-023 | 2026-09-26 | Realtime source roles: **MDD/TRIAS primary; GTFS-RT complementary**; source-faithful staging remains separate before future reconciliation | Approved direction |
| D-024 | 2026-09-26 | Cross-Source Reconciliation and Cologne Scope Contract | Approved design guardrail |
| D-025 | 2026-09-28 | Stage 3 formally started with a bounded Stage 3A disruption/cause evidence audit; no schema, cadence, cause-model, matching, or production decision is made before host evidence | Procedural status |
| D-026 | 2026-09-28 | Future persistence preserves source timing and disruption evidence as append-only observations: `TimetabledTime`, `EstimatedTime`, nullable source-only `RecordedAtTime`, and `ObservedAt`/`CapturedAt` remain separate; PostgreSQL is the intended future direction only | Approved design guardrail |
| D-027 | 2026-09-28 | Stage 3 is complete: Stage 3A approved from bounded evidence; Stage 3B collection/cadence policy approved without activation; Stage 3C stop-event-first reconciliation design approved without implementation | Approved stage completion |
| D-028 | 2026-09-28 | Stage 4 current official VRS Global-ID snapshot approval: Cologne scope, internal-segment boundary, cross-boundary treatment, and source-faithful mode classifier | Approved stage completion |
| D-029 | 2026-09-28 | Stage 5 database architecture: PostgreSQL 18 clean redesign, durable source-faithful layers, logical grains, lineage, and deferred physical implementation | Approved architecture |
| D-030 | 2026-09-28 | Stage 6 static warehouse and analytics design contract: Cologne-scoped grains, schedule/calendar semantics, lineage, and future reconciliation baseline; physical implementation deferred by explicit user decision | Approved design; physical implementation deferred |
| D-031 | 2026-09-28 | Stage 7 realtime pilot design: bounded MDD-only Köln Hbf pilot, fixed request/cadence/quota/raw-retention/persistence controls, and Stage 8 handoff; execution remains not started | Historical pilot design; cadence detail superseded by D-032 and later D-033 |
| D-032 | 2026-09-28 | Stage 7 pilot-design correction: bounded 10-second fixed-anchor MDD handoff for `RecordedAtTime` visibility; D-031's 60-second cadence detail is superseded while its historical record is retained | Historical pilot-design correction; superseded by D-033 |
| D-033 | 2026-09-28 | Stage 7 active correction: 3-second fixed start-to-start pilot slots, 1200-attempt maximum, and 83–84% operational MDD planning envelope with 16–17% unallocated headroom | Active pilot-design correction |
| D-034 | 2026-09-29 | S40 Stage 8 authorization/request-contract diagnostic: preserve the 2026-09-28 HTTP 401 supplier-access evidence, execute only the bounded A/B/C differential tree, and make no contract change until a live result supports one | Historical Stage 8 diagnostic gate; superseded by D-035 |
| D-035 | 2026-09-29 | Stage 8 successful Hbf differential and bounded pilot closeout: A=200 at N5, B=200 at N20, unchanged V3/N20 contract, successful 343-request pilot, and corrected diagnostic metadata semantics | Stage 8 complete; Stage 9+ not started |
| D-036 | 2026-09-29 | Stage 9 matching/data-quality foundation: versioned source-identity bridges, stop-event-first indexed matching, explicit ambiguity/coverage/lineage classifications, richer MDD identity retention, and offline regression validation | Implementation regression PASS; source-backed Stage 9 acceptance initially BLOCKED; collector activation, operational fact, Power BI, and Tiered50 decisions remain later |
| D-037 | 2026-09-29 | Stage 9 review correction: synthetic fixtures are regression evidence only; source-backed acceptance requires a retained successful Stage 8 parser bundle and date-compatible MDD-to-VRS static event population | Historical gate definition; initial Stage 9 status BLOCKED pending source-backed core match population; Stage 10+ NOT STARTED |
| D-038 | 2026-09-29 | Stage 9 ranking hardening and bounded acceptance probe | Historical initial probe BLOCKED at credential preflight; superseded by D-039 |
| D-039 | 2026-09-29 | Stage 9 acceptance-probe correction: adapt raw Stage 8 stop-event records before inspecting adapter-selected THIS scope, retain parser-versus-adapter diagnostics, and preserve separate Stage 4 reference/current VRS snapshot lineage | Bounded source-backed acceptance PASS; 60 observations and 51 core deterministic matches; Stage 10+ remain NOT STARTED |
| D-040 | 2026-09-29 | Stage 9 evidence-integrity correction: serialize the live in-memory match decisions losslessly in the row-level JSONL, require semantic JSONL-to-summary reconciliation before PASS, and retain the current VRS snapshot/version/hash | Bounded source-backed acceptance PASS after one corrected live run; 60 rows, 53 established service dates, 51 core matches, 51 MEDIUM / 9 UNMATCHED, row-level integrity PASS; Stage 10+ remain NOT STARTED |
| D-041 | 2026-09-29 | Stage 10 operational-fact contract: derive one deterministic outcome per approved HIGH-confidence canonical service event and service date, preserve explicit Stage 9 run/version lineage, first/latest and NULL timing semantics, PlannedBay/EstimatedBay evidence, and non-causal Situation linkage | NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED. The current checkout has no usable PostgreSQL/source-observation population and the current Stage 9 evidence contains zero HIGH rows |
| D-042 | 2026-09-29 | Stage 11 Power BI semantic-model design: approved subject areas, table grains, single-direction relationships, governed Date strategy, static/canonical identity boundary, explicit DAX measures, estimated-delay threshold, NULL/bay/Situation semantics, and sampling/reporting boundaries | COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED |
| D-044 | 2026-09-30 | Stage 12 fixed panel and versioning: approve `TIERED50_PANEL_V1` with 50 validated VRS parent stations, Legacy7 continuity, deterministic static evidence, current core-mode/geographic review, bounded final-panel MDD compatibility, and fixed-membership versioning | COMPLETE — APPROVED FIXED 50-STATION PANEL; Stage 13 production work and Stage 14 remain NOT STARTED |
| D-045 | 2026-09-30 | Stage 12 durable candidate-universe replay correction: retain the exact 864-candidate sanitized input population and verify the approved panel offline through the authoritative selector | COMPLETE — APPROVED FIXED 50-STATION PANEL unchanged; Stage 13 production work and Stage 14 remain NOT STARTED |
| D-046 | 2026-09-30 | Stage 13 tiered collector readiness closeout: historical record of the exact-feed density tiers, 5/15/30-minute cadence option, `NumberOfResults=30` option, 240 logical requests/hour, zero automatic retries option, cap-2 bounded concurrency option, heartbeat/claim/failure-isolation controls, and conservative Legacy7 quota treatment | SUPERSEDED / REOPENED — NOT VALID AS FINAL USER APPROVAL |
| D-047 | 2026-09-30 | Reopen Stage 13 after the required user-approval gate: retain D-046 evidence, invalidate its mixed-call forward-span metric for result-limit coverage, keep all material production choices as technically evaluated options, and gate the corrected 24-invocation diagnostic | IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED; Stage 14 remains NOT STARTED |
| D-048 | 2026-09-30 | Corrected Stage 13 result-limit diagnostic: 24 additional client invocations, 0 HTTP responses, 0 confirmed provider attempts, unknown provider quota consumption, and no final result-limit decision from live coverage | Historical corrected diagnostic result; superseded by D-049 |
| D-049 | 2026-09-30 | Explicit final approval of Stage 13 tiered collector readiness: fixed TIERED50_PANEL_V1, density ranks 1–8/9–28/29–50, 8/20/22 at 5/15/30 minutes, A20/B30/C30, concurrency 2, MaxAttempts=2 with one approved transient retry, 10% retry reserve, exact quota guardrail, phase sequence 19/18/18/18/19/18, and atomic overlap contract | COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED; Stage 14 NOT STARTED |
| D-050 | 2026-09-30 | Stage 13 post-closeout contract hardening: separate normal-observed timing from degraded heartbeat-budget feasibility, correct phase assignment to source_parent_station_id / parent StopPointRef, clarify design-time versus production phase-index materialization, and restore critical offline regressions without changing approved production values | COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED; Stage 14 NOT STARTED |

## D-023 — Realtime Source Roles — MDD Primary, GTFS-RT Complementary

**Decision:** Use MDD/TRIAS as the primary realtime source for the Cologne
Transport Intelligence project. Use GTFS-RT as a complementary realtime source
for operational states, cross-source evidence, broad coverage, and capabilities
such as `SKIPPED`, `NO_DATA`, and `ServiceAlerts`.

Neither source is currently approved as a confirmed Actual Time source. MDD
`RecordedAtTime` has the relevant TRIAS semantic but was not observed in the
bounded tested responses and six live lifecycle journeys. GTFS-RT
`StopTimeEvent.time` can mean estimated or actual, and no provider-specific
explicit actual/recorded semantic was observed.

**Rationale:** MDD is better aligned with the project's Cologne/VRS source
environment, supports station-targeted requests, exposes rich
`PreviousCall`/`ThisCall`/`OnwardCall` context, explicit timetable and estimate
times, platform/bay fields, and situation associations. GTFS-RT adds broad
operational coverage, trip and stop schedule relationships, explicit
`SKIPPED`/`NO_DATA` states, `ServiceAlerts`, independent cross-source
evidence, and demonstrated current GTFS.de identity compatibility for tested
Köln Neumarkt records. The two identifier namespaces remain distinct, and
railway Köln Hbf is unresolved in GTFS.de.

**Consequences:**

- Future collection is expected to use separate MDD and GTFS-RT source-faithful
  ingestion paths.
- `ObservedAt`/`CapturedAt` is first-class, and repeated observations must
  preserve prediction evolution rather than overwrite prior estimates.
- Source provenance and source-specific semantics must not be erased by an
  ambiguous common record.
- Prospective collection is required; retrospective recovery cannot be relied
  upon for either source's realtime history.
- No `ActualTime` or `ActualDelay` field/KPI is introduced without later
  validated semantic evidence; Actual Delay remains **NOT APPROVED**.
- Missing events or updates remain source-observation states, not automatic
  on-time, served, cancelled, skipped, or service-absent conclusions.
- Future normalization/reconciliation is a separate design problem deferred
  until source-specific staging is designed.

At the time of this decision, it was a future design direction. It did not
approve collector production code, finalize schemas or table names, or
authorize database, scheduler, Power BI, or runtime changes. See the [Stage 3
realtime collection design readiness document](stage-03/realtime-collection-design-readiness.md).

## D-024 — Cross-Source Reconciliation and Cologne Scope Contract

**Decision:**

- MDD remains the primary realtime source.
- GTFS-RT remains complementary.
- GTFS-RT is filtered to the Cologne project scope before analytical use.
- Germany-wide GTFS-RT data does not expand project scope.
- Source records are reconciled only through validated identity evidence.
- Trip-specific cross-source claims require HIGH-confidence reconciliation.
- GTFS-RT `ServiceAlert` entity scope is preserved as trip, route, stop, or
  agency context.
- Provider-reported cause/effect is not treated as proven root cause.
- Cross-boundary routes contribute only in-scope Cologne evidence to core KPIs.
- Long-distance services remain outside core KPI scope.

The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
is the detailed design guardrail. It defines the conceptual canonical stop,
line, and service-event identities; stop-event-first matching; HIGH/MEDIUM/LOW
confidence; source-faithful storage; alert association levels; MDD Situation
semantics; claim limits; and unresolved Stage 3 validation items.

**Consequences:**

- `MDD JourneyRef == GTFS-RT trip_id` is not a valid identity rule.
- A validated canonical reconciliation layer is required before trip-level
  cross-source attribution.
- An in-scope Cologne stop event does not make an entire through-service or
  cross-boundary trip a Cologne KPI subject.
- Route-, stop-, and agency-level alerts remain context unless stronger
  validated linkage exists; only a HIGH-confidence trip-level association may
  support a trip-level provider-reported cause/effect claim.
- `ReportedCause` remains distinct from `RootCause`, and MDD Situation
  association remains distinct from proven delay causality.
- MDD and GTFS-RT disagreement must be retained rather than overwritten.
- Actual Delay, Actual On-Time Performance, and Prediction Accuracy vs Actual
  remain unapproved until confirmed actual time is validated.

This is an approved design guardrail, not production implementation approval.
It does not create SQL tables, schemas, migrations, collector code, matching
algorithms, ETL, Power BI changes, scheduler jobs, or production mappings.
At the time of this decision entry, Stage 3 was **IN PROGRESS** through Stage
3A. The status was **PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY
PENDING**; the later completed discovery and current approved closeout are
recorded in the Stage 3A audit and D-027 below.

## D-025 — Stage 3A Procedural Start

Stage 3 formally started with the bounded [Stage 3A Disruption Cause & Alert
Evidence Audit](stage-03/03a-disruption-cause-alert-evidence-audit.md). The
substage measures current GTFS-RT ServiceAlert population and two sampled MDD
Situation responses using exactly one GTFS-RT GET and two MDD POST requests.

This is a procedural status entry, not a final analytical decision. The
original two-station MDD execution was complete at entry; the broader
ten-station Situation discovery was subsequently completed and is now under
review. No database schema, collector cadence, cause taxonomy, GTFS-RT
production-ingestion approval, cross-source matching, causal attribution,
scheduler, or Power BI decision is made here.

## D-026 — Future Source-Observation and Persistence Contract

The [future source-observation and persistence contract](source-contracts/future-observation-persistence-contract.md)
records the timing and historical-evidence invariant for later persistence
design:

- `TimetabledTime`, `EstimatedTime`, and source-provided `RecordedAtTime` are
  separate concepts; `RecordedAtTime` is nullable and is never derived from an
  estimate, timetable, collector timestamp, or event disappearance.
- `ObservedAt` / `CapturedAt` remains separate collector lineage. When a
  non-null source `RecordedAtTime` is first observed, its exact value and the
  conceptual first-seen observation timestamp are retained; later `NULL` or
  disappearance does not erase it.
- Repeated observations preserve estimate evolution, not only the latest
  estimate. Actual-time capture is separate from continued Situation, Cause,
  cancellation, or status tracking.
- Situation/Cause evidence is append-only historical source evidence. Earlier
  versions remain when a later response omits them; no normalized cause
  taxonomy or inferred root cause is introduced.
- PostgreSQL is recorded as the intended future persistence direction because
  it fits the desired append-only, source-faithful, later-derived architecture
  and JSONB use where justified. It does not change source availability.

This decision does not create PostgreSQL tables, migrate SQL Server assets,
authorize production persistence or scheduling, or advance Stage 3A. Actual
Delay and prediction-accuracy KPIs remain **NOT APPROVED** until source
`RecordedAtTime` is observed and validated. A future short-term raw-payload
retention option may be evaluated for forensic reprocessing, but no duration is
approved and permanent raw retention is not the default.

## D-027 — Stage 3 Completion

The explicit user approval recorded for this closeout changes Stage 3A to
**COMPLETE — APPROVED** for the bounded evidence already documented. The
approved conclusions preserve provider-reported reason text, Situation and
SituationFullRef association, IncidentCall service-information semantics,
GTFS-RT cause/effect limitations, separate timing fields, and non-causal
interpretation. It does not reinterpret the sample or prove root cause.

Stage 3B is **COMPLETE — APPROVED DESIGN CONTRACT — ACTIVATION DEFERRED**. The
approved architecture has separate MDD and GTFS-RT collection loops, a
tiered/quota-aware Sampling Policy/Era, bounded retries, explicit timeout and
lineage requirements, append-only evidence, and no replay of missed snapshots.
The historical 10/20/20 candidate is not approved as production truth.

Stage 3C is **COMPLETE — APPROVED DESIGN CONTRACT — IMPLEMENTATION NOT
STARTED**. The canonical unit is a stop event, source identifiers remain
distinct, confidence and method are explicit, Cologne scope is preserved, and
alert/Situation association is non-causal by default. Railway Köln Hbf identity
and the full Cologne bridge remain open implementation risks.

This completion does not create PostgreSQL schema, modify the database,
implement collectors or matching, modify Power BI, or start the next stage.

## Current Stage 3 status

Stage 3 is **COMPLETE** as a source-evidence and design/contract stage. The
[Stage 3 completion decision](stage-03/stage-03-completion-decision.md) is the
authoritative closeout record. Production implementation and the next project
stage remain separately gated.

## D-028 — Stage 4 Scope and Mode Contract; Current Snapshot Approval

The approved Stage 4 semantic decisions remain in force, and Stage 4 is
**COMPLETE — APPROVED** for the current official VRS Global-ID snapshot at
`https://download.vrs.de/gtfs/google_transit_goR.zip`. The snapshot carries
feed version `VERSION__20260928_0042`, validity `2026-09-02` through
`2026-12-12`, and ZIP SHA-256
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`.
The previous 2025-expired snapshot and evidence remain explicitly historical
and are not current authority.

The approved decisions are:

1. Use the direct VRS Global-ID source stop identity prefix `de:05315:` for
   Cologne scope.
2. Define geographical analytical scope at the validated stop-event first;
   do not substitute route names, agencies, modes, free text, or approximate
   geofencing for source stop identity.
3. Define a Cologne-internal segment only when both consecutive scheduled
   endpoints are validated Cologne stops.
4. Treat technical Cologne-serving trip and route labels as discovery
   identities, not as full geographical KPI scope.
5. For cross-boundary services, include only validated Cologne events and
   Cologne-internal segments in core Cologne KPI evidence.
6. Approve the deterministic source-faithful mode classifier evidenced from
   current route rows.
7. Include RE, RB, and Regional/Other Bus only through their validated
   in-Cologne events and segments.
8. Retain Rail Replacement Bus (SEV) as a separate analytical category.
9. Keep long-distance rail outside core KPI scope; retain source evidence only
   when legitimately present and labelled.
10. Keep unsupported, unknown, and ambiguous route/mode values explicit rather
    than forcing them into a core category.

The current feed had no duplicate route, stop, or trip IDs, no unknown
stop-time stop references, and no unclassified Cologne events. Its one
unsupported `route_type=4` ferry row remains explicit as `OTHER_MODE` and has
no Cologne events. This approval is static-feed scope evidence only. It does
not claim realtime operation or actual performance and does not authorize
PostgreSQL, collectors, matching, Power BI, or Stage 5 implementation.

## D-029 — Stage 5 Database Architecture

Stage 5 is **COMPLETE — APPROVED ARCHITECTURE**. PostgreSQL 18 is approved as
the future authoritative database platform, with the latest supported
PostgreSQL 18 minor selected at implementation time. The approved strategy is
a **CLEAN POSTGRESQL REDESIGN**, not a 1:1 SQL Server lift-and-shift; legacy
SQL Server remains evidence and is not mutated or copied wholesale.

The approved logical flow is:

```text
SOURCE -> ctl / src -> wrk -> core where reconciliation is needed -> dw -> analytics -> Power BI
```

The approved schemas/layers are `ctl`, `src`, `wrk`, `core`, `dw`, and
`analytics`. `src` is durable source-faithful parsed evidence, not disposable
staging. It preserves complete content-identified VRS GTFS snapshots, duplicate
source rows, NULL semantics, MDD response/stop-event/call/Situation history,
and later-approved in-scope GTFS-RT observations. `RecordedAtTime` remains
nullable source-only evidence and is never synthesized from estimates,
timetables, collector time, or disappearance.

The [Stage 5 architecture](stages/05-database-architecture.md),
[table/grain catalog](stages/05-table-grain-catalog.md), and
[completion decision](stages/05-database-architecture-completion-decision.md)
define 42 named logical entity contracts, key and data-type conventions,
idempotency, JSONB limits, evidence-driven indexes, future monthly realtime
partitioning by `ObservedAtUtc`, audit/sampling-policy lineage,
least-privilege roles, the realtime backup/recovery requirement, and the
source-lineaged legacy-import policy.

This is an architecture approval only. It created no PostgreSQL database,
schema, table, migration, SQL execution, collector, scheduler, matching,
warehouse, or Power BI implementation. Stage 6 receives the static
warehouse/analytics design and contract handoff; physical implementation
remains deferred until explicit user authorization.

## D-030 — Stage 6 Static Warehouse & Analytics Design

Stage 6 is **COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED**.
The detailed [Stage 6 design contract](stages/06-static-warehouse-analytics-design.md)
and [completion decision](stages/06-static-warehouse-analytics-completion-decision.md)
are authoritative together with the Stage 4 evidence and Stage 5 grain catalog.

The approved design decisions are:

- retain the complete approved VRS GTFS snapshot in `src`, then apply the
  Stage 4 Cologne boundary in validated/rebuildable transformations before
  producing the Cologne-focused `dw` contract;
- keep the Stage 5 logical layers and exact static entities without requiring
  Stage 9 `core` canonical objects;
- keep `FactScheduledTrip` at one Cologne-serving trip-pattern grain and
  `FactScheduledStopEvent` at one validated Cologne stop-event-pattern grain;
- keep active `Service + Date` expansion exclusively in
  `BridgeServiceDate`, with no `DateKey` in the scheduled stop-event fact;
- preserve exact GTFS time text and normalize extended-hour times to integer
  service-day seconds without wrapping values over `24:00:00`;
- apply `calendar.txt` weekday/range rules and `calendar_dates.txt` add/remove
  exceptions deterministically, including service IDs represented only by
  exceptions;
- preserve Stage 4 actual-stop-ID geography, cross-boundary treatment,
  internal-segment rule, and source-faithful mode classifier;
- define snapshot-aware static schedule/network analytics, lineage,
  content-hash idempotency, and a future source-to-warehouse acceptance
  matrix; and
- treat all current Stage 4 counts as validated source baselines, not as
  warehouse results.

The user's explicit decision defers PostgreSQL migration/runtime/database
implementation until later authorization. Stage 6 therefore created no
database, schema, table, migration, SQL, data load, network request,
collector, scheduler, matching layer, operational fact, or Power BI change.
Stage 5 remains **COMPLETE — APPROVED ARCHITECTURE**. Stage 7 was not started
by this decision; it is closed by D-031 below.

## D-031 — Stage 7 Realtime Pilot Design

**Historical record:** D-031's original 60-second cadence, moving
`DepArrTime` detail, and V1 sampling-policy identifier were superseded at that
historical point for the Stage 8 handoff by [D-032](#d-032--stage-7-pilot-cadence-and-recordedattime-correction).
D-032 was later superseded by D-033 for the active handoff. The original
decision is retained so the repository does not rewrite its decision history.

Stage 7 is **COMPLETE — APPROVED PILOT DESIGN**. It defines a bounded,
reproducible Stage 8 diagnostic handoff without executing the pilot.

The approved decisions are:

- Use **MDD/TRIAS as the only primary Stage 8 pilot source**. GTFS-RT remains
  complementary/secondary in the long-term architecture but is excluded from
  the one-source pilot; Stage 9 owns cross-source matching.
- Target **Köln Hbf**, parent MDD `StopPointRef` **`de:05315:11201`**, for
  source/parser/persistence validation only. The station is not a
  representative Cologne-wide sample.
- Use the fixed request contract: endpoint
  `https://mdd.gorheinland.com/delfi`; `POST`; TRIAS 1.2;
  `application/xml; charset=utf-8`; `x-api-key` from `MDD_API_KEY` only;
  `RequestorRef=CologneTransitIntelligence`; current approved UTC
  `DepArrTime`; `StopEventType=arrival`; `NumberOfResults=20`;
  `IncludePreviousCalls=true`; `IncludeOnwardCalls=true`; and
  `IncludeRealtimeData=true`.
- Omit `TimeWindow` for the pilot and record it as semantically available but
  not selected. Omit `IncludeOperatingDays` because it was not part of the
  validated request path. `NumberOfResults=20` is bounded-pilot-only and is
  not a production default or HTTP request count.
- Use one request slot every **60 seconds** for at most **60 minutes**, with a
  maximum of **60 scheduled logical requests**. Do not run concurrent or
  catch-up requests. If a slot overlaps the prior request, skip it and record
  `SCHEDULED_SLOT_SKIPPED_OVERLAP`.
- Apply a **30-second timeout** and **zero retries**. Record every actual HTTP
  attempt. The conservative project ceiling remains **250,000 MDD HTTP
  requests/month**; total external caller usage remains unknown.
- Before any request, check only credential presence and print
  `MDD_API_KEY: PRESENT` or `MDD_API_KEY: MISSING`. Missing credentials mean
  zero attempts. Stop immediately on 401/403 or 429. Do not retry network,
  timeout, 408, or retryable 5xx failures; stop after three consecutive failed
  request slots, with a successful request resetting the counter.
- Version observation lineage under the non-production policy
  `MDD_STAGE_8_KOELN_HBF_60S_60MIN_V1`.
- Preserve the approved source fields and exact timing semantics, including
  repeated estimate history, all call scopes, planned/estimated bays,
  independent operational flags, Situation and `SituationFullRef` evidence,
  `Attribute`/`GeneralAttribute`/`IncidentCall` evidence, populated errors,
  parser gaps, and response/run lineage.
- Keep source `RecordedAtTime` nullable, exact, and never synthesized. If it
  appears, retain its first observation lineage; treat it only as candidate
  evidence. Actual Delay remains **NOT APPROVED**. If zero occurrences are
  observed, report `RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`.
- Approve a bounded MDD raw-response exception only for Stage 8: protected
  temporary storage outside Git, mode-700 directory, mode-600 files, no
  credentials or authorization material, API-key-content check before write,
  maximum 24-hour retention after the run, and cleanup/reporting required.
- Use append-only, source-faithful local JSONL diagnostic persistence outside
  Git because PostgreSQL is deferred. Mirror the Stage 5 response snapshot,
  stop-event, call, Situation, association, and service-attribute logical
  grains. Exact response/run replay is idempotent; later scheduled
  observations remain distinct.
- Reconcile parser output to pilot persistence and distinguish `SUCCESS`,
  overlap skips, authentication/authorization failures, rate-limit responses,
  HTTP errors, network/DNS failures, timeouts, parser failures, pilot-
  persistence failures, and MDD application errors. Never turn missingness
  into on-time, served, cancelled, no-service, or no-delay facts.
- Report the required request, status, parse, persistence, call, timing,
  estimate-history, bay, flag, Situation, `IncidentCall`, attribute, error,
  unmapped-field, `RecordedAtTime`, and raw-cleanup metrics without implying
  provider-wide coverage.

The full contract is in the [Stage 7 design](stages/07-realtime-pilot-design.md)
and the [completion decision](stages/07-realtime-pilot-design-completion-decision.md).

At the time of D-031, Stage 8 remained **NOT STARTED / NEXT**. D-031 authorized no MDD request,
GTFS-RT request, network access, SQL/database access, runtime pilot, or
PostgreSQL implementation. Stage 6 remains **COMPLETE — APPROVED DESIGN;
PHYSICAL IMPLEMENTATION DEFERRED** and Stage 5 remains **COMPLETE — APPROVED
ARCHITECTURE**.

## D-032 — Stage 7 Pilot Cadence and RecordedAtTime Correction

**Historical record:** D-032 was the active correction to the bounded Stage 8
handoff at the time. It is now superseded for the active Stage 8 handoff by
[D-033](#d-033--stage-7-3-second-pilot-and-operational-planning-envelope).
Its V2 10-second fixed-anchor decision remains preserved as historical
decision context.

D-032 superseded the 60-second cadence, moving `DepArrTime` detail, and V1
policy identifier in
D-031 while retaining D-031 as historical decision context. Stage 7 remains
**COMPLETE — APPROVED PILOT DESIGN**. At the time of D-032, Stage 8 remained
**NOT STARTED / NEXT**; current Stage 8 status is recorded by D-035.

The corrected handoff is:

- Use **MDD/TRIAS only** for the one-source pilot at Köln Hbf,
  `de:05315:11201`. GTFS-RT remains excluded and Stage 9 owns later
  cross-source matching.
- At pilot start, select `PilotAnchorDepArrTime` as the UTC reference
  timestamp. Reuse that exact `DepArrTime` in every request for the one-hour
  pilot; do not advance it with wall-clock time. Retain the per-request request
  timestamp and fixed anchor as lineage.
- The first successful response is the initial cohort/baseline observation.
  Later responses use the same fixed search anchor. Result additions,
  removals, and reordering are lifecycle evidence; result positions need not
  remain identical.
- Use the unchanged request selections: TRIAS 1.2 `POST`, XML,
  `MDD_API_KEY` presence-only preflight, `arrival`,
  `NumberOfResults=20`, previous/onward/realtime flags true, and `TimeWindow`
  and `IncludeOperatingDays` omitted. The purpose of the correction is greater
  temporal observation density, not larger result-set breadth.
- Use the active non-production sampling policy
  `MDD_STAGE_8_KOELN_HBF_10S_60MIN_V2`: one scheduled logical request slot
  every 10 seconds for at most 60 minutes, at most 360 scheduled logical
  requests and 360 normal HTTP attempts. Requests remain sequential,
  non-concurrent, with no catch-up bursts and zero retries. If an active
  request runs into a later slot, skip and persist
  `SCHEDULED_SLOT_SKIPPED_OVERLAP`; do not queue or compress it.
- Keep the 30-second request timeout. It does not permit overlap even though
  it exceeds one 10-second slot interval. The revised 360-attempt ceiling is
  approximately `0.144%` of the conservative `250,000` HTTP-request monthly
  ceiling. This is a bounded diagnostic pilot cadence, not a production
  approval; Stage 13 owns production cadence/readiness analysis and
  shared-caller headroom.
- If source `RecordedAtTime` is observed once in any retained `THIS`,
  `PREVIOUS`, or `ONWARD` timing context for either arrival or departure, write
  the exact source value immediately to append-only pilot persistence with
  event/source identity, call scope, arrival/departure context, response
  identity, `ObservedAt`, and first-seen observation timestamp. Later `NULL` or
  disappearance must not erase it. Every successful 10-second response remains
  a distinct observation even when `EstimatedTime` or `RecordedAtTime` is
  unchanged; only an exact replay of the same response/run identity is
  idempotent.
- If no value is observed, report `RECORDED_AT_TIME_NOT_OBSERVED` as absence in
  the observed sampling window, not provider-wide absence. Ten-second polling
  materially reduces but cannot mathematically guarantee capture because no
  positive lifecycle sample establishes a minimum visibility period. A field
  observed to appear and disappear within the 10-second interval may require a
  separately approved shorter bounded validation; no 5-second production
  cadence is pre-approved.
- Retain the approved raw-response exception: temporary files outside Git,
  mode 600 inside a mode-700 directory, maximum 24-hour retention, and
  deletion after successful Stage 8 evidence extraction/reconciliation. This
  is not permanent raw storage.

D-032 authorizes no MDD request, GTFS-RT request, other network request, SQL or
database work, or runtime pilot code. The documentation-only correction does
not start Stage 8.

## D-033 — Stage 7 3-Second Pilot and Operational Planning Envelope

D-033 is the active documentation-only correction to the bounded Stage 8
handoff. It supersedes the V2 10-second fixed-anchor policy in D-032 while
preserving D-031 and D-032 as historical records. Stage 7 remains
**COMPLETE — APPROVED PILOT DESIGN**. At the time of D-033, Stage 8 remained
**NOT STARTED / NEXT**; its later completion is recorded by D-035.

The active decision is:

- Keep MDD/TRIAS as the only primary Stage 8 pilot source at Köln Hbf,
  `de:05315:11201`; GTFS-RT remains excluded from the pilot and Stage 9 owns
  later cross-source matching.
- Keep the already-approved fixed pilot-start `PilotAnchorDepArrTime` for the
  entire one-hour run. Reuse that exact `DepArrTime` in every request and do
  not advance it with wall-clock time.
- Keep the other request settings unchanged: TRIAS 1.2 `POST`,
  `StopEventType=arrival`, `NumberOfResults=20`,
  `IncludePreviousCalls=true`, `IncludeOnwardCalls=true`,
  `IncludeRealtimeData=true`, `TimeWindow` omitted, and
  `IncludeOperatingDays` omitted.
- Activate the new non-production sampling policy
  `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`. It schedules fixed start-to-start
  slots at `T0`, `T0 + 3s`, `T0 + 6s`, and so on for a maximum duration of 60
  minutes. The maximum is `1200` scheduled logical slots, calculated from the
  3-second interval across one hour, and `1200` normal HTTP attempts because
  retries remain zero.
- Requests remain strictly sequential and non-concurrent. The scheduler does
  not wait three seconds after response completion, shift the schedule, queue
  missed slots, or issue catch-up bursts. If a fixed slot arrives while the
  previous request is active, skip it and record
  `SCHEDULED_SLOT_SKIPPED_OVERLAP`.
- Keep the 30-second timeout and zero retries. HTTP `429` stops the pilot
  immediately and is classified as rate-limit/provider evidence.
- The cadence evidence basis is retained MDD lifecycle/probe evidence with
  observed request elapsed times predominantly below 3 seconds, ranging from
  sub-second observations to approximately 2 seconds. This supports a bounded
  3-second diagnostic interval with sequential execution and overlap skipping;
  it does not guarantee a response within 3 seconds, prevent skipped slots, or
  approve a production cadence.
- Preserve all existing `RecordedAtTime` rules. The field remains nullable,
  exact, source-only, and append-only when observed. If it appears in any
  retained `THIS`, `PREVIOUS`, or `ONWARD` arrival/departure context, persist
  the exact source value immediately with event/source identity, call scope,
  arrival/departure context, response identity, `ObservedAt`, and first-seen
  observation timestamp. Later `NULL` or disappearance must not erase it. If
  no value is observed, report
  `RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`, not
  `MDD_DOES_NOT_SUPPORT_RECORDED_AT_TIME`.
- Keep the conservative monthly ceiling exactly `250,000` MDD HTTP
  requests/month. Set the operational planning envelope at `83–84%`:
  `207,500` requests/month at 83% through `210,000` at 84%. Preserve
  `40,000–42,500` requests/month, approximately `16–17%`, as unallocated
  headroom. Stage 13 owns final production station tiers, cadence, staggering,
  retry policy, overlap analysis, and full monthly request projection, and must
  size production inside the `207,500–210,000` envelope unless the user later
  changes this decision.
- Account for the theoretical pilot maximum as `1200 / 250000 = 0.48%` of the
  full conservative ceiling and approximately `0.57–0.58%` of the operational
  planning envelope (`1200 / 210000 ≈ 0.571%`; `1200 / 207500 ≈ 0.578%`).
  `NumberOfResults=20` is not an HTTP request count. Actual consumption may be
  lower because overlapping slots are skipped.

D-033 is historical Stage 7 planning context. Stage 13's explicitly approved
lower-load design supersedes any interpretation of `207,500` as a minimum
utilization requirement; the current semantics are a `250,000` absolute
provider ceiling, a `210,000` normal operational maximum, a `207,500`
automatic-retry protection boundary, and no minimum utilization gate.

D-033 authorized no MDD request, GTFS-RT request, other network request, SQL or
database work, or runtime pilot code at that design stage. It did not start
Stage 8 at that time. Historical V1
and V2 policy decisions remain unchanged in the decision log.

## D-034 — Stage 8 Authorization/Request-Contract Diagnostic Gate

The latest 2026-09-28 Stage 8 attempt returned HTTP `401` with the observed
German provider message:

`Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`

This is preserved as historical Stage 8 failure evidence and does not replace the
earlier successful historical MDD request or prove that the current credential
value is invalid.

The separate diagnostic at
[`scripts/stage_08_authorization_request_contract_diagnostic.py`](../scripts/stage_08_authorization_request_contract_diagnostic.py)
is limited to the required decision tree:

- Control A: Köln Hbf, `de:05315:11201`, current request/`DepArrTime`, and
  `NumberOfResults=5`.
- Control B only after A=`200`, identical except `NumberOfResults=20`.
- Control C only after A=`401`, Köln Heumarkt `de:05315:11110`, otherwise
  identical to A.

The diagnostic is sequential, zero-retry, redirect-free, and uses the existing
30-second timeout. It records protected temporary evidence outside Git with
status, content type, elapsed time, response bytes, sanitized provider error
text, and parser parseability. The API-key value is never printed, logged,
hashed, fingerprinted, compared, or persisted.

The available 2026-09-29 repository/Codex process preflight at that time reported only
`MDD_API_KEY: MISSING`, so no control request was executed. The resulting
classification is `DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING`; no Stage 8 request
parameter changed and the full pilot was not started. The user's separate
local Terminal had previously reported `MDD_API_KEY: PRESENT`; these are
different process environments, not contradictory claims. The repository
process did not inherit the credential, so the live decision tree was not
executed; this does not establish whether the separate Terminal credential is
correct or incorrect.

The S41 implementation now recognizes the German supplier-access evidence via
normalized stable phrases as well as the existing English wording. A permitted
decision is handed to the same `run_pilot` path with an explicit effective
station, stop identity, result count, and versioned sampling/request policy;
the actual pilot creates its own fixed start-time `DepArrTime` anchor.

The conditional Stage 8 policy decision is fixed by evidence, not assumption:
A=`200`, B=`200` preserves V3/N20; A=`200`, B=`401` with the supplier-access
message changes only Stage 8 `NumberOfResults` to `5` under a new versioned
policy; A=`401`, C=`200` changes the bounded validation target to Heumarkt and
uses `5`; A=`401`, C=`401` stops before the pilot. Previous/This/Onward call
coverage, realtime data, parser fields, cadence, quota, cleanup, the future
50-station objective, and Stage 13 ownership of the production result-limit
decision remain unchanged. Stage 9 and later stages were not started.

## D-035 — Stage 8 Successful Differential and Pilot Closeout

The chronology in D-034 is preserved. It includes the earlier missing-key
preflight, earlier invalid/local credential-configuration attempts, and the
2026-09-28 HTTP 401 with the observed German supplier-access body. Under the
corrected local credential configuration, the bounded differential then
executed:

- Control A: Köln Hbf, `de:05315:11201`, `NumberOfResults=5`, HTTP `200`,
  parseable;
- Control B: same Köln Hbf and diagnostic `DepArrTime` anchor,
  `NumberOfResults=20`, HTTP `200`, parseable; and
- classification: `CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING`.

No request-contract reduction was required. The bounded evidence supports the
conclusion that Köln Hbf works with both result counts under the corrected
local credential configuration; therefore the earlier 401 does not support a
conclusion that Hbf/N20 or the current Hbf Stage 8 request contract requires
additional supplier authorization. This is not a claim about every MDD data
supplier.

The full pilot used Köln Hbf, `de:05315:11201`, `NumberOfResults=20`, and
`MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`. It completed with approximately
`3606.801` seconds elapsed, `1200` planned logical slots, `343` attempted
HTTP requests, `343` HTTP 200 responses, `343` successful
parser/reconciliation responses, reconciliation rate `1.0`, `6860`
`StopEventResult` occurrences, `857` designed sequential overlap skips, zero
parser/persistence/network-DNS/timeout/application failures, and successful
cleanup. `857` skipped slots are not collector failures; they are evidence for
the later Stage 13 sequential-versus-bounded-concurrency/readiness decision.

The bounded result is
`RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`; Actual Physical Delay
remains **NOT_APPROVED**. The observed parser gaps
(`affectedVehicleJourney`, `datedVehicleJourneyRef`, `destinations`,
`direction`, `operatingDaysDescription`, `origins`, `situations`,
`vehicleJourneyRef`, and `vehicleJourneys`) remain evidence for later review
and are not added to the production schema here. Stage 9+ remains not started.

The diagnostic metadata contract is corrected: successful parseable HTTP 200
controls have no failure classification (`null`), while genuine parser,
timeout, network/DNS, authentication/authorization, and other failures retain
their classifications. Normal Situation/passenger-information text does not
populate `sanitized_provider_error_text`; genuine failed-response evidence,
including the German HTTP 401 body, remains sanitized and supplier-access
detection remains distinct from generic 401. Offline validation made no MDD
request. The future 50-station objective and Stage 13 production ownership
remain unchanged.

## D-036 — Stage 9 Matching and Data-Quality Foundation

Stage 9 implements the approved Stage 3C reconciliation design as a reusable,
source-first foundation. It keeps MDD/TRIAS and GTFS-RT namespaces separate,
uses validated canonical stop/line mappings, matches at stop-event grain, and
retains a versioned decision plus ranked candidate evidence for every source
observation, including ambiguous and unmatched outcomes.

The implementation includes the deterministic offline matcher at
[`scripts/stage_09_matching_quality.py`](../scripts/stage_09_matching_quality.py),
the PostgreSQL 18 contract at
[`sql/stage_09_matching_quality.sql`](../sql/stage_09_matching_quality.sql),
and the regression suite at
[`tests/test_stage_09_matching_quality.py`](../tests/test_stage_09_matching_quality.py).
The approved Stage 4 baseline and the bounded synthetic
known-good/ambiguous/unresolved population pass offline regression with zero
network requests and zero database connections. This is not source-backed
closure evidence.

The railway Köln Hbf GTFS.de identity remains unresolved. Adjacent KVB parent
complexes are not substituted. Stage 9 does not create the operational fact,
Actual Delay, Power BI model, final 50-station panel, production collector, or
Tiered50 cutover. The future 50-station objective, 250,000-request ceiling,
Stage 13 ownership, and Stage 8 historical chronology remain unchanged.

## D-037 — Stage 9 Evidence-Gate Correction

The Stage 9 review separates `implementation_regression_status` from
`stage_9_evidence_status`. The parser-to-adapter-to-matcher path, timezone and
service-day semantics, `OperatingDayRef` separation, versioned mapping
validity, ambiguity/static-scope classifications, and SQL semantic checks are
implemented and regression-tested. The machine-readable audit is
[`docs/stages/09-matching-data-quality-evidence.json`](stages/09-matching-data-quality-evidence.json).

At the time of this evidence-gate decision, the source-backed gate was
**BLOCKED**, not complete: the repository had supporting Stage 4/VRS,
controlled MDD field-retention, and bounded GTFS.de Neumarkt evidence, but no
retained successful Stage 8 parser bundle plus independently date-compatible
MDD-to-VRS static event population. The later bounded acceptance correction
and run are recorded in D-039; Stage 10 and later stages remain **NOT STARTED**.

## D-038 — Stage 9 Ranking Hardening and Bounded Acceptance Probe

Stage 9 candidate ranking now treats raw StopSeqNumber, Direction/DirectionRef,
operator, origin/destination, and other auxiliary coincidences as evidence only
until their source/namespace semantics are explicitly validated. Validated line
mappings, same-namespace trip identity, and explicitly validated auxiliary
policies remain eligible to resolve a candidate tie. The SQL overlap-review
view now detects overlapping effective ranges across different mapping versions
and reports both versions, ranges, canonical targets, and the conflict flag.

The initial bounded source-backed acceptance probe was invoked on 2026-09-29
with the three approved Cologne targets, but safe credential preflight returned
`MDD_API_KEY: MISSING`. It made zero MDD attempts and did not download a VRS
snapshot. That historical run remains **BLOCKED**; the exact initial status is
retained in
[`09-source-backed-matching-summary.json`](stages/09-source-backed-matching-summary.json).
Stage 10 remains **NOT STARTED**, and Tiered50 was not activated.

## D-039 — Stage 9 Acceptance-Probe Correction and Bounded Source-Backed Run

The review identified that the acceptance probe was filtering raw Stage 8
`stop_event_observation` records on the Stage 9-derived
`source_attributes.selected_call_scope` field before the Stage 9 adapter had
created that field. The correction makes the authoritative path explicit:
Stage 8 `parse_response()` → `build_bundle()` → raw stop-event record →
`adapt_stage8_stop_event()` → adapter-selected THIS call → independent GTFS
service-date establishment → final Stage 9 adaptation and matching.

The regression test uses the real Stage 8 parser and bundle shape and proves
that the old raw filter rejects a record that the adapter correctly retains.
The probe now records, per request, bundle stop-event count, raw records with a
THIS call, adapted records with a selected THIS call, and adapted records
missing THIS. One bounded live execution made three sequential HTTP 200 MDD
requests with 20 records each; all 60 raw records had THIS and all 60 were
retained after adaptation. The run established 54 GTFS service dates and 51
core deterministic matches.

The run used current official VRS feed `VERSION__20260929_0106` with SHA-256
`ca64753223d3908a86b74fc322103b9e4c6c9c90275cfc5cb2c4c624b0082ee0`. It passed
all 17 Stage 4 revalidation checks. The approved Stage 4 reference snapshot
`VERSION__20260928_0042` and SHA-256
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b` remains
separate; Stage 4 semantics were revalidated for the changed official feed.
The exact run is retained in the Stage 9 summary/evidence artifacts. Stage 10
and later stages remain **NOT STARTED**.

## D-042 — Stage 11 Power BI Model Design

Stage 11 approves a semantic-model design/contract for a non-technical
operational and management audience. The design separates Static Schedule /
Network Context, Operational Evidence, and Matching / Data Quality. It fixes
the grain of each semantic table, uses one-to-many single-direction
relationships, keeps Stage 6 pattern facts separate from date-expanded Daily
Schedule output, explicitly distinguishes the semantic `Dim Scheduled Trip`
projection from the physical Stage 6 scheduled-trip fact, and prohibits
physical fact-to-fact, bidirectional, many-to-many, and unsafe text-ID
relationships. The approved semantic `Dim Scheduled Trip` paths remain in the
relationship matrix.

The decision adopts one governed Date dimension with Auto Date/Time disabled,
keeps VRS/static identities separate from Stage 9 canonical operational
identities, and defines explicit DAX measures with explicit denominators and
NULL behavior. Estimated timing remains estimated; the disconnected
`Estimated Delay Threshold (Minutes)` parameter is an analytical `0..15`
integer range with default `0`, not official punctuality policy. Bay evidence
preserves Changed/Unchanged/Unknown, and Situation evidence remains associated
and non-causal. Date-filtered matching coverage is explicitly service-date-
scoped; decisions without a valid service date remain separate Data Quality
evidence and are never assigned a date from `ObservedAt`. Matching coverage
uses all Stage 9 confidence outcomes and is not measured from the HIGH-only
Stage 10 fact.

`Dim Sampling Era` currently filters the `Fact Matching Decision` population
only through the approved `SamplingEraKey` relationship. Data Quality
classifications carried by those matching decisions inherit that context. The
separate independent `Fact Data Quality Issue` is not Sampling-Era-filtered in
the base model, and `Data Quality Issue Count` must not be described or
implemented as Sampling-Era-filterable under that model. It is not an
operational KPI slicer and has no approved relationship to `Fact Operational
Service Event`. A future relationship to the independent issue fact or a
governed operational-event projection requires explicit `SamplingEraKey`
lineage at that fact's grain, with validated projection grain, key uniqueness,
historical-era semantics, and lineage; an event whose source observations
cross an era boundary must not be assigned arbitrarily. Until then,
operational KPI analysis must constrain compatible era/time scope or show a
clear coverage/methodology warning.

The approved implementation contract is PBIP + TMDL + PBIR under source
control, with Power BI Desktop required later for binding, refresh, render QA,
interaction QA, and save round-trip validation. No PBIP/TMDL/PBIR/PBIX artifact,
Power BI Desktop execution, PostgreSQL work, SQL execution, or data load was
performed. Stage 10 retains its blocked runtime-validation status and Stage 12
was not started.

## D-043 — Stage 13 Tiered Collector Readiness Audit

Stage 13 performed an offline, evidence-based readiness audit for the future
Tiered50 collector. The historical `10/20/20` candidate reconciles to `280`
logical requests/hour, `6,720`/day, `201,600` in a 30-day month, and `208,320`
in a 31-day month. The full ceiling remains `250,000`, with normal planning at
`207,500–210,000`; retries are additional HTTP requests and
`NumberOfResults` is not a request multiplier. The candidate remains a
calculated provisional population, not an approved production cadence.

No authoritative Stage 12 completion artifact or fixed 50-station panel is
present. The bounded N5 and N20 MDD samples reached their configured limits,
so no production result limit was selected and
`PROVIDER_MAXIMUM_NOT_ESTABLISHED` remains explicit. The approved Stage 4
artifact is aggregate-only in this checkout, so station/tier static density and
forward-horizon coverage were not manufactured. Current production shared-
credential usage and SQL Server `ctl.MddCollectorRun` history are unavailable;
the historical Legacy snapshot's 35 additional attempts among 1,629 HTTP
attempts are labeled historical sensitivity only.

The deterministic five-minute phase design reconciles
`24, 23, 23, 24, 23, 23` due targets, `140` per 30-minute super-cycle, and
`280` per hour. Future execution identity is an atomic unique
`(SamplingTargetId, LogicalDueBucket)` claim; stale/incomplete runs remain
observable and missed historical buckets are never replayed. One failed target
does not abort the remaining due targets. Small bounded concurrency with a
conditional maximum of two is the design direction, but provider-safe
concurrent behavior is not measured and production concurrency is not
approved. Unbounded concurrency, Stage 14, and Tiered50 activation remain
prohibited.

**Final Stage 13 status:** `CONDITIONALLY BLOCKED —
STAGE_12_PANEL_APPROVAL_REQUIRED; PROVIDER_MAXIMUM_NOT_ESTABLISHED;
STATIC_EVENT_DENSITY_NOT_ESTABLISHED; CURRENT_PRODUCTION_MDD_AUDIT_UNAVAILABLE;
CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED`.

## D-044 — Stage 12 Fixed 50-Station Panel and Versioning

Stage 12 re-evaluated the current validated VRS Global-ID parent-station
population and approved the fixed panel version `TIERED50_PANEL_V1`. The current
official snapshot is `VERSION__20260930_0053`, retrieved at
`2026-09-30T10:42:58Z`, with ZIP SHA-256
`288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147`. It has
864 unique Cologne `location_type=1` parent-station candidates; all 864 have
scheduled child-stop evidence, with 1,146,295 parent-level scheduled events.

The identity rule remains validated source `stop_id` beginning with
`de:05315:`. Parent membership comes from the source `parent_station`
hierarchy. The seven Legacy7 IDs all resolve as current parent stations with
scheduled service and are retained unchanged. The policy combines those
continuity anchors, current core-mode coverage, a fixed secondary geographic
cell-spread pass, and an explicit lexicographic operational-richness key. It
does not use passenger counts, station fame, names, operators, coordinates, or
route labels to establish Cologne identity, and it does not use a hidden
weighted score.

All six current core modes are represented and measured. The 50-station panel
represents 25 of 57 occupied fixed geographic cells; the largest selected cell
contains 12 stations. Proximity review retained distinct parent identities in
central-station, Deutz, Mülheim, and other interchange areas when their source
IDs and scheduled services were distinct.

The final panel compatibility diagnostic made 50 sequential requests to the
approved MDD/TRIAS endpoint with one attempt per station, zero retries,
`NumberOfResults=1`, arrival scope, realtime data, and the approved
previous/onward-call behavior. All 50 were HTTP 200, parseable valid TRIAS
application responses with one returned event and matching source
`StopPointRef` evidence. No replacement was required; raw responses were not
persisted.

The versioning contract fixes membership after approval. A membership change is
not dynamic and requires a new version recording effective date/time, reason,
and predecessor. Legacy7 history is never deleted, future cutover preserves
historical sampling identities where applicable, and reporting distinguishes
panel eras. `SelectionRank` is not a production tier.

This decision does not approve production cadence, tier membership,
`NumberOfResults`, concurrency, retries, overlap claiming, a scheduler, Power
BI, or Stage 14. Stage 13 production work remains NOT STARTED; the pre-existing
Stage 13 readiness audit remains conditionally blocked.

## D-045 — Stage 12 Durable Candidate-Universe Replay Correction

The Stage 12 approval result and `TIERED50_PANEL_V1` membership remain
unchanged. The exact approved Stage 12 source snapshot was available locally
and matched feed version `VERSION__20260930_0053` and ZIP SHA-256
`288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147`. A
sanitized repository artifact now retains all 864 eligible parent-station
candidates and the exact selection-policy parameters, without raw GTFS or MDD
payloads.

The authoritative `select_panel` function replays that artifact offline and
reproduces the approved 50 IDs, ranks, phases, first 25 reserves, all seven
Legacy7 anchors, 25 selected geographic cells, and the same core-mode coverage
outcome. The Stage 12 acceptance gates now distinguish deterministic algorithm
reproducibility from durable exact-input replayability, and both are required
for COMPLETE. No new MDD request was issued. Stage 13 production work and Stage
14 remain NOT STARTED.

## D-046 — Stage 13 Tiered Collector Readiness Design Closeout (Historical; Superseded)

**Status:** `SUPERSEDED / REOPENED — NOT VALID AS FINAL USER APPROVAL`

This section preserves the historical Stage 13 closeout evidence. The prior
autonomous approval is no longer valid because the user required explicit
approval of all material design choices. D-047 is the current Stage 13 state;
no new final decision is recorded here.

The historical D-046 record documented readiness evidence for the Stage
12-approved fixed panel `TIERED50_PANEL_V1`. The exact VRS Global-ID feed is
`VERSION__20260930_0053` with ZIP SHA-256
`288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147`.
Static scheduled-arrival density was expanded over the exact feed calendar and
`calendar_dates` exceptions and measured with 5-, 10-, 15-, and 30-minute
rolling windows. Tier membership is deterministic density evidence and does
not use Stage 12 `SelectionRank` as a production tier.

The technically evaluated tier plan is 10 Tier A stations at five-minute
cadence, 20 Tier B stations at 15-minute cadence, and 20 Tier C stations at
30-minute cadence.
It reconciles to 240 logical requests/hour, 5,760/day, 172,800 in a 30-day
month, and 178,560 in a 31-day month. The six-heartbeat 30-minute distribution
is `[21, 21, 19, 20, 20, 19]`, with 120 requests per super-cycle. The
alternative historical 10/20/20 plan at 5/10/30 minutes remains a comparison
only at 280 requests/hour; the selected 15-minute middle tier preserves the
station count split while reducing quota pressure.

Bounded live diagnostics used a fixed `DepArrTime` anchor and six deterministic
high-, middle-, low-density, interchange, Legacy7, and urban bus/tram
representatives. Sequential N5/N10/N20/N30 diagnostics made 24 valid HTTP 200
requests; all tested limits saturated. A separate three-pair cap-2 diagnostic
made six valid responses without a pressure signal or failure propagation.
The technically evaluated option is `NumberOfResults=30`, while
`PROVIDER_MAXIMUM_NOT_ESTABLISHED` remains explicit because the provider's
theoretical maximum was not established. The cap-2 result is a bounded design
maximum, not a provider-wide rate-limit claim.

The normal policy is one attempt per logical target with zero automatic retries.
The full documented Legacy7 cadence is reserved conservatively at 8,640/8,928
requests for 30/31-day months because current shared-credential production
usage is unavailable; it is not represented as zero. The Stage 13 diagnostic
budget is capped at 100 attempts. Historical retry evidence is retained as
sensitivity only. Future execution identity remains the atomic
`(SamplingTargetId, LogicalDueBucket)` claim, missed buckets are never replayed,
each target records its own failure, and Legacy7 history and identities are
preserved.

**Historical Stage 13 status at the time of D-046:** `COMPLETE — APPROVED
TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED`. This historical
status is superseded and is not valid as final user approval.

This closeout does not create a scheduler, database objects, Windows scheduled
tasks, Power BI artifacts, or a collector activation, and it does not start
Stage 14. The current shared-credential audit remains a prerequisite for any
future activation decision.

## D-047 — Stage 13 Reopened for Explicit User Design Approval

The latest Stage 13 state is `IN PROGRESS — EVIDENCE READY; USER DESIGN
APPROVAL REQUIRED`. D-046 remains historical evidence only and is superseded;
its autonomous approval of cadence, result limit, concurrency, and retry policy
must not be treated as a valid final user decision.

The retained 24 sequential N5/N10/N20/N30 records and six cap-2 records remain
usable for HTTP status, parser success, returned counts, saturation, source
reference consistency, duration, and provider-pressure analysis. Their old
`forward_temporal_span_seconds` values are explicitly
`INVALID_FOR_FORWARD_COVERAGE_DECISION` because the algorithm mixed
PreviousCall, ThisCall, OnwardCall, and departure timings. The corrected metric
uses only ThisCall arrival values at or after the requested `DepArrTime` anchor.

Raw MDD responses were not retained, so the corrected coverage comparison
could not be reconstructed from the original sanitized aggregates. The user
then explicitly approved the separate gate, and exactly 24 corrected
sequential client-side request invocations were issued, increasing cumulative
client request invocations from 90 to 114. All 24 ended in sanitized
`NETWORK_OR_DNS_FAILURE` before an HTTP response; 0 HTTP responses were
received; 0 provider HTTP attempts were confirmed from response evidence; and
provider quota consumption is unknown/not confirmed. The historical 100-attempt
diagnostic ceiling is unchanged and no cap-2 rerun was performed.

## D-048 — Corrected Stage 13 NumberOfResults Diagnostic Result

The corrected implementation used only the requested StopEventResult's
THIS-call arrival timing at or after the requested `DepArrTime` anchor. The
user-approved run used six deterministic representative stations, N5/N10/N20/N30,
sequential execution, `MaxAttempts=1`, zero diagnostic retries, one fixed
comparable anchor (`2026-09-30T11:54:56Z`), no raw response persistence, and the
401/403 stop rule.

Exactly 24 additional client-side request invocations were issued. Every
invocation ended with `NETWORK_OR_DNS_FAILURE`; 0 HTTP responses were received;
0 provider HTTP attempts were confirmed from response evidence; provider quota
consumption is unknown/not confirmed; and 24 is retained separately as
`conservative_quota_accounting_attempts`. No returned result count,
saturation, THIS-call arrival, pre-anchor count, or forward-coverage value was
available. The sanitized duration and error evidence is retained. The
historical 24 sequential records remain separately preserved with
`INVALID_FOR_FORWARD_COVERAGE_DECISION`, and the six historical cap-2 records
were not rerun.

The corrected comparison therefore leaves fixed N20, fixed N30, and the
tier-specific A20/B30/C30 candidate undecided. Exact-feed static planning
supports N30 for 50/50 cadence windows and A20/B30/C30 for 50/50, but static
support does not establish realtime response coverage or a provider maximum.
No NumberOfResults decision was finalized.

The requested 10/20/20 versus 8/20/22 density-ranked comparison produces four
tier changes, not two: Köln Friesenplatz and Köln Ebertplatz move A→B, while
Köln Porz Markt and Köln Ubierring move B→C. Their canonical route refs,
modes, child-stop counts, Legacy7 status, and density boundary rationale are
retained in the machine-readable evidence.

Current user recommendations remain unapproved: review `8/20/22 @ 5/15/30`,
consider bounded concurrency 2 subject to Stage 14 dry-run validation, and
consider `MaxAttempts=2` with one bounded retry for approved transient failures.
Stage 13 remains `IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED`.

## D-049 — Explicit Final Approval of Stage 13 Tiered Collector Readiness

**Status:** COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION
NOT STARTED

The explicit approval recorded in prompts/S68.md finalizes the Stage 13
readiness design. The fixed Stage 12 panel TIERED50_PANEL_V1 remains exactly
50 stations and Stage 12 evidence is unchanged. Density ranks 1–8, 9–28, and
29–50 define Tier A/B/C counts of 8, 20, and 22 at five-, fifteen-, and
thirty-minute cadence. The approved load is 110 logical requests per
30-minute super-cycle and 220 per hour.

The approved result limits are Tier A/B/C NumberOfResults 20/30/30. Retained
station-level exact-feed evidence recalculates maximum station P95 and maximum
observed static scheduled-arrival counts as A 19/20, B 23/23, and C 29/29,
with 50/50 total P95 support. This is static planning evidence only. The
limitations PROVIDER_MAXIMUM_NOT_ESTABLISHED and
CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT remain
explicit; no provider-wide maximum, guaranteed realtime completeness,
guaranteed 20/30 availability, or complete forward-coverage claim is made.

Maximum concurrency 2 is approved as a bounded design. MaxAttempts=2 permits
one automatic retry for HTTP 408, 429, 500, 502, 503, 504 and approved
transient transport failures; Retry-After is respected for HTTP 429, while
401/403 and deterministic request, parser/schema, and TRIAS application
failures are not retryable. The normal retry reserve is 10% of nominal
Tiered50 requests. The 30-day accounting is 158,400 nominal, 15,840 reserve,
174,240 Tiered50 with reserve, 8,640 Legacy7 reserve, and 100 diagnostic
reserve, totaling 182,980. The 31-day accounting is 163,680, 16,368,
180,048, 8,928, and 100, totaling 189,076. These approved lower-load totals
are valid safely below the `210,000` normal operational maximum and the
`207,500` automatic-retry protection boundary; no minimum monthly utilization
is required. Automatic retries are blocked when projected/current monthly
consumption would exceed the `207,500` boundary.

The approved phase sequence is 19/18/18/18/19/18 with capacities A 8,
B 7/7/6, and C 4/3/4/3/4/4. The execution identity remains
(SamplingTargetId, LogicalDueBucket); claims are atomic, duplicate invocation
does not issue HTTP, missed buckets are not replayed, and one failed station
does not abort other due targets. Legacy7 history and identities remain
preserved.

The final evidence records all material design approvals as true, Stage 13
complete, production activation not started, and Stage 14 not started. No
database object, scheduler, Windows task, target row, Power BI artifact, or
Tiered50 activation was created. This closeout issued zero MDD requests and
required no DNS/live-network operation.

## D-050 — Stage 13 Contract-Hardening Correction

**Status:** COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION
NOT STARTED

D-050 is a post-closeout correctness correction. D-046 through D-049 remain
historical decision records and are not rewritten. The authoritative current
Stage 13 evidence now separates `NORMAL_OBSERVED_HEARTBEAT_FEASIBILITY` from
`DEGRADED_MODE_HEARTBEAT_BUDGET`. Retained successful cap-2 timing supports
normal observed feasibility (`9,585.91 ms` P95 estimate and `9,771.31 ms`
observed-max estimate), but it is not a degraded-mode guarantee. The
theoretical maximum normal batch has 19 due targets, concurrency 2, a
30-second per-attempt timeout, and MaxAttempts 2: 38 timeout-heavy attempts
produce 570 seconds of wall-time pressure at cap-2, exceeding the 300-second
heartbeat if unconstrained.

The bounded degraded-mode contract is explicit: a dispatcher owns only its
current logical heartbeat; it must not extend work into later logical buckets;
it must not start an initial attempt or retry without sufficient remaining
budget; in-flight work obeys the heartbeat deadline/cancellation contract;
budget exhaustion is recorded as `SKIPPED_HEARTBEAT_BUDGET` or
`RETRY_SKIPPED_HEARTBEAT_BUDGET`; those outcomes do not trigger catch-up and
are collection-quality outcomes rather than service-operational conclusions;
and one target's exhausted budget does not abort unrelated targets. HTTP 429
continues to respect `Retry-After` but cannot sleep or retry past the current
deadline. Stage 14 must determine and validate a concrete nonzero
persistence/cleanup safety margin with end-to-end dry-run measurements; Stage
13 assigns no arbitrary duration.

The authoritative phase-assignment identity is corrected to
`source_parent_station_id / parent StopPointRef`, with hash
`SHA-256(panel version + tier + source_parent_station_id)`. All 50 approved
phase indexes reproduce exactly; future database-generated `SamplingTargetId`
values cannot change them. The production overlap/claim identity remains
`(SamplingTargetId, LogicalDueBucket)`. Phase assignment is finalized in Stage
13, but `production_phase_index_persisted = false` and
`phase_index_must_be_persisted_in_stage14 = true`; the status remains
`DESIGN_ONLY_STAGE14_MATERIALIZATION_REQUIRED`.

The offline regression suite restores independent coverage for THIS-call
pre-anchor timing, post-DNS smoke-gate transport failure, degraded heartbeat
budget/deadline behavior, and parent-StopPointRef phase identity, including
the existing DNS-zero-request and 401/403 safeguards. No MDD request, DNS
lookup, or live-network operation was issued for this correction. The approved
`TIERED50_PANEL_V1`, 8/20/22 tiers, 5/15/30 cadence, A20/B30/C30 result limits,
MaximumConcurrency 2, MaxAttempts 2, one approved transient retry, 10% retry
reserve, quota boundary, phase sequence, no-catch-up rule, and Legacy7
continuity remain unchanged. Stage 14 remains `NOT STARTED`.

The same post-closeout cleanup makes the quota semantics explicit: `250,000`
is the absolute provider ceiling, `210,000` is the normal operational maximum,
and `207,500` is the automatic-retry protection boundary. Stage 7's historical
`207,500–210,000` / 83–84% planning target is retained as history, but the
explicitly approved Stage 13 lower-load design supersedes any interpretation
of `207,500` as a minimum utilization requirement. The approved `182,980` and
`189,076` conservative totals are valid safely-below-boundary designs.

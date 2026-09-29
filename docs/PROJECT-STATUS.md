# Project Status

**Project:** Cologne Transport Intelligence
**Repository:** `sonazariso/cologne-transit-intelligence`
**Status date:** 2026-09-29

## Authoritative status

| Stage | Name | Status |
|---|---|---|
| 0 | Project Definition | APPROVED |
| 1 | Source Inventory & Permission | APPROVED |
| 2 | MDD/TRIAS Source-Contract Audit | APPROVED |
| 3 | Static GTFS Profile / Realtime Evidence Validation | COMPLETE |
| 4 | Cologne Scope & Mode Classification | COMPLETE — APPROVED |
| 5 | Database Architecture | COMPLETE — APPROVED ARCHITECTURE |
| 6 | Static Warehouse & Analytics Design | COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED |
| 7 | Realtime Pilot Design | COMPLETE — APPROVED PILOT DESIGN |
| 8 | Small Realtime Pilot | BLOCKED — DIFFERENTIAL AUTHORIZATION DIAGNOSTIC NOT COMPLETED |
| 9 | Matching & Data Quality | Not started |
| 10 | Operational Fact | Not started |
| 11 | Power BI Model | Not started |
| 12 | 50-Station Selection | Not started |
| 13 | Tiered Collector Readiness | Not started |
| 14 | Controlled 50-Station Cutover | Not started |
| 15 | Post-Cutover Validation | Not started |
| 16 | Revisit Timing/Data Coverage Issues | Not started |
| 17 | Actual Physical Delay Productionization | Not started |

### Current Stage 3 substage

- **Stage 3 — COMPLETE** as a source-evidence, collection/cadence-design, and
  cross-source-matching-design stage.
- **Stage 3A — Disruption Cause & Alert Evidence Audit — COMPLETE — APPROVED.**
- **Stage 3B — Collection Strategy & Cadence — COMPLETE — APPROVED DESIGN;
  ACTIVATION DEFERRED.**
- **Stage 3C — Cross-Source Matching Design — COMPLETE — APPROVED DESIGN;
  IMPLEMENTATION NOT STARTED.**
- Stage 3 completion does not authorize production implementation,
  database/schema work, collector changes, scheduler changes, Power BI work,
  or the next project stage.

See the [Stage 3A audit document](stage-03/03a-disruption-cause-alert-evidence-audit.md).
See the [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
for the authoritative closeout record.

### Stage 4 closeout

- **Stage 4 — COMPLETE — APPROVED.** The current official VRS Global-ID ZIP
  was retrieved once, checked for service-calendar freshness, and audited
  locally. The previous stale snapshot is retained as historical/legacy
  evidence and is not current authority.
- Current approval snapshot: feed version `VERSION__20260928_0042`, validity
  `2026-09-02` through `2026-12-12`, and ZIP SHA-256
  `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`.
- The validated Cologne identity is the direct source rule
  `stop_id LIKE 'de:05315:%'`; parent-station relationships do not override
  the actual stop ID referenced by `stop_times.txt`.
- Cologne scope is measured at stop-event and both-endpoints internal-segment
  grain. Route/trip serving labels are discovery identities, not full
  geographical KPI scope.
- The approved mode inventory is Stadtbahn/Tram, Urban/KVB Bus, S-Bahn, RE,
  RB, Regional/Other Bus, and separate SEV, with long-distance/unsupported
  values outside core scope. Cross-boundary services contribute only their
  validated Cologne events and segments.
- The [Stage 4 completion document](stages/04-cologne-scope-mode-classification.md)
  and [Stage 4 evidence](stages/04-cologne-scope-mode-evidence.json) contain
  the snapshot metadata, counts, classifier examples, anomalies, and
  reconciliation checks. The historical/legacy evidence is preserved in
  [the separately named historical JSON](stages/04-cologne-scope-mode-evidence-historical-2026-09-28.json).
- Stage 4 completion did not authorize physical PostgreSQL, warehouse,
  collector, scheduler, matching, or Power BI implementation. Stage 5 has now
  completed its architecture approval; Stage 6's design/contract closeout is
  recorded below and physical implementation remains deferred.

### Stage 5 closeout

- **Stage 5 — COMPLETE — APPROVED ARCHITECTURE.** PostgreSQL 18 is the future
  authoritative platform direction, with the latest supported PostgreSQL 18
  minor selected at implementation time. The approved migration strategy is a
  clean PostgreSQL redesign, not a 1:1 SQL Server lift-and-shift.
- The approved logical layers are `ctl`, `src`, `wrk`, `core`, `dw`, and
  `analytics`. `src` is durable source-faithful evidence; `wrk`, `core`, `dw`,
  and `analytics` are derived or validated layers with explicit ownership and
  lineage boundaries.
- The [Stage 5 architecture document](stages/05-database-architecture.md),
  [42-entry table/grain catalog](stages/05-table-grain-catalog.md), and
  [completion decision](stages/05-database-architecture-completion-decision.md)
  are authoritative. They approve the static GTFS content-hash snapshot
  strategy, append-only MDD observation history, bounded GTFS-RT persistence,
  keys/types/idempotency, evidence-driven indexes, future monthly realtime
  partitioning, control/sampling lineage, least-privilege roles, and the
  backup/recovery requirement.
- Stage 5 created no database, schema, table, migration, SQL execution,
  collector, scheduler, matching, warehouse, or Power BI implementation.
  Its static warehouse/analytics handoff is now completed as a design
  contract; physical implementation remains deferred until explicit user
  authorization.

### Stage 6 closeout

- **Stage 6 — COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED.**
  The [static warehouse and analytics design](stages/06-static-warehouse-analytics-design.md)
  finalizes the source-to-Cologne boundary, exact static dimension/bridge/fact
  grains, GTFS extended-hour time normalization, `calendar.txt` and
  `calendar_dates.txt` activation semantics, Stage 4 scope/mode application,
  cross-boundary behavior, static analytics outputs, snapshot lineage,
  idempotency, and the future implementation acceptance matrix.
- The current Stage 4 values are source baselines only: 3,137 Cologne stops,
  1,144,295 Cologne scheduled stop events, 1,082,490 internal segments,
  61,805 Cologne-serving trips, 39,566 Cologne-internal trips, 22,239
  cross-boundary trips, 1,606 touch-only diagnostics, and 137
  Cologne-serving routes. They are not claims about a warehouse that exists
  today.
- `FactScheduledTrip` and `FactScheduledStopEvent` remain pattern-level;
  `BridgeServiceDate` owns active Service + Date expansion, and `DateKey` does
  not enter the scheduled stop-event fact grain. Stage 6 does not depend on
  Stage 9 `core` objects.
- PostgreSQL migration/runtime, database/schema/table/migration creation, SQL,
  data loading, network retrieval, realtime work, matching, operational facts,
  and Power BI work did not occur. The [Stage 6 completion decision](stages/06-static-warehouse-analytics-completion-decision.md)
  records the closeout. Stage 7 is now separately closed as **COMPLETE —
  APPROVED PILOT DESIGN**; Stage 8 is currently blocked at its authorization
  diagnostic gate.

### Stage 7 closeout

- **Stage 7 — COMPLETE — APPROVED PILOT DESIGN.** The [realtime pilot design](stages/07-realtime-pilot-design.md)
  fixes a bounded, reproducible MDD/TRIAS-only Stage 8 pilot for Köln Hbf,
  `de:05315:11201`, with the active V3 policy
  `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`: fixed start-to-start 3-second slots for
  at most 60 minutes, at most 1200 scheduled logical slots and 1200 normal
  HTTP attempts, a fixed pilot-start `DepArrTime` anchor, `NumberOfResults=20`,
  `TimeWindow` omitted, a 30-second timeout, zero retries, sequential
  non-concurrent execution, and overlap-slot skipping. The bounded cadence
  reduces but cannot mathematically guarantee capture of a transient source
  `RecordedAtTime`.
- The pilot preserves the approved source-field retention, timing, estimate
  history, call scope, bay, operational-flag, Situation, service-information,
  error, parser-lineage, gap, idempotency, and reconciliation requirements,
  including immediate append-only persistence of any observed source
  `RecordedAtTime` and protection from later NULL erasure. The conservative
  monthly ceiling remains 250,000 requests; the operational planning envelope
  is 207,500–210,000 requests/month (83–84%), with 40,000–42,500 requests
  (16–17%) preserved as unallocated headroom. The 1200-attempt pilot maximum
  is 0.48% of the full ceiling and approximately 0.57–0.58% of the planning
  envelope. Stage 13 owns production cadence/readiness analysis and must size
  normal production traffic inside that envelope. It uses protected temporary
  raw responses only under the bounded 24-hour Stage 8 exception and a
  diagnostic append-only JSONL harness because PostgreSQL remains deferred.
- GTFS-RT is not part of the one-source pilot; Stage 9 owns cross-source
  matching. Actual Delay remains **NOT APPROVED**. One-station evidence is not
  Cologne-wide coverage.
- The [Stage 7 completion decision](stages/07-realtime-pilot-design-completion-decision.md)
  remains authoritative for the design closeout. That closeout itself
  performed no MDD or GTFS-RT request, SQL/database access, runtime pilot, or
  PostgreSQL implementation; the later Stage 8 HTTP attempt and current
  diagnostic gate are recorded below.

### Stage 8 authorization/request-contract diagnostic gate

- **Stage 8 — BLOCKED — DIFFERENTIAL AUTHORIZATION DIAGNOSTIC NOT COMPLETED.**
  The Stage 8 runtime exists at
  [scripts/stage_08_mdd_realtime_pilot.py](../scripts/stage_08_mdd_realtime_pilot.py),
  and the separate bounded diagnostic exists at
  [scripts/stage_08_authorization_request_contract_diagnostic.py](../scripts/stage_08_authorization_request_contract_diagnostic.py).
- The historical 2026-09-28 Stage 8 HTTP attempt returned `401` with the
  The observed response was the German supplier-access message beginning
  `Sie haben keinen Zugriff auf Daten diesen Datenlieferanten`. This is
  preserved as current Stage 8 failure evidence; it does not prove that the
  credential itself is invalid or that MDD is unusable.
- The diagnostic execution available for this update reported only
  `MDD_API_KEY: MISSING`, so Controls A/B/C made zero HTTP requests. Its
  classification is `DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING`; no request
  parameter change is evidence-supported and the full pilot was not started.
  The runtime is now ready to automatically hand a permitted decision to the
  same pilot path with an explicit effective station, `StopPointRef`,
  `NumberOfResults`, and versioned sampling/request policy. Offline
  validation covers Hbf/N20/V3, Hbf/N5, Heumarkt/N5, and the no-pilot branch.
- The current diagnostic correction selects one fixed
  `DiagnosticAnchorDepArrTime` across its executed A/B/C controls while
  retaining each control's independent actual `RequestTimestamp`. Its process
  result now reflects the returned full-pilot status: a permitted-but-blocked
  or failed pilot is non-zero, and only `stage_8_status=COMPLETE` is zero.
  Diagnostic temporary evidence is cleaned after a successful pilot only when
  the pilot's extraction, reconciliation, and cleanup gates are complete;
  blocked or failed evidence remains protected and pending under the existing
  maximum-24-hour boundary.
- The repository/Codex execution process did not inherit `MDD_API_KEY`. The
  user's separate local Terminal had previously reported `MDD_API_KEY: PRESENT`;
  these are different process environments, not contradictory claims. No
  credential values were exposed or compared, and this does not establish
  whether the separate Terminal credential is correct or incorrect.
- The earlier missing-key preflight remains historical evidence in the
  [Stage 8 record](stages/08-small-realtime-pilot-blocked.md), not the current
  sole explanation for the later HTTP 401.
- Stage 9 and all later stages remain not started and untouched.

## Post-Stage-02 validation

Existing GTFS-RT validation:

- Current-feed inventory: **COMPLETED**.
- Static identity bridge: **BOUNDED/PARTIAL**; current Neumarkt identity
  validated, railway Köln Hbf unresolved.
- GTFS-RT bounded lifecycle validation: **COMPLETE**.
- GTFS-RT historical availability audit: **BOUNDED VALIDATION COMPLETE**;
  both the initial ±7-day and the separate ±45-day metadata-only exact-URL
  Wayback tests returned zero captures at the 3-month and 6-month targets.
- MDD/TRIAS near-event lifecycle validation: **BOUNDED VALIDATION COMPLETE**;
  six live journeys were reviewed (three at Köln Hbf and three at Köln
  Neumarkt), with no `RecordedAtTime` observed in the bounded observations.

- **GTFS-RT lifecycle validation — BOUNDED LIFECYCLE VALIDATION COMPLETE;
  NEUMARKT SAMPLES #1 AND #2 RECORDED.** The bounded, host-run-only tooling,
  inventory, and two lifecycle samples are documented in [the GTFS-RT audit](source-contracts/gtfs-rt-source-feasibility-audit.md).
  The [GTFS.de static identity bridge](source-contracts/gtfsde-static-identity-bridge-audit.md)
  remains **PARTIAL** because railway Köln Hbf is unresolved; that identity gap
  does not invalidate the completed two-sample Neumarkt lifecycle evidence.
- **GTFS-RT historical availability audit — BOUNDED VALIDATION COMPLETE.** S19
  and S19.1 tested only exact-URL Internet Archive / Wayback metadata around
  the 3-month and 6-month calendar horizons. The ±7-day run and the expanded
  ±45-day metadata-only run each returned zero captures for both horizons.
  This does not establish provider history or continuous coverage and does not
  approve GTFS-RT as a production source.
- This validation did not reopen or invalidate Stage 2 or approve GTFS-RT as a
  production source. Stage 3 is complete as a design stage; no production
  implementation is authorized.

### Post-Stage-02 source validation package

| Package | Current status |
|---|---|
| MDD/TRIAS source contract | **COMPLETED / APPROVED** |
| GTFS-RT current-feed feasibility | **BOUNDED VALIDATION COMPLETE** |
| GTFS.de identity bridge | **BOUNDED / PARTIAL** — Neumarkt current identity validated; railway Köln Hbf unresolved |
| GTFS-RT lifecycle validation | **BOUNDED VALIDATION COMPLETE** |
| GTFS-RT historical availability | **BOUNDED VALIDATION COMPLETE** — zero exact-URL captures in both ±7-day and ±45-day metadata runs |
| Realtime source-role direction | **APPROVED DIRECTION** — MDD primary; GTFS-RT complementary |
| Cross-source reconciliation and Cologne scope contract | **APPROVED DESIGN CONTRACT** — implementation not started |
| Stage 3 collection/cadence contract | **COMPLETE — APPROVED DESIGN** — activation deferred |
| Stage 3 cross-source matching design | **COMPLETE — APPROVED DESIGN** — implementation not started |
| Confirmed Actual Time | **UNRESOLVED / UNAVAILABLE** |
| Actual Delay KPI | **NOT APPROVED** |

### Stage 2 substage status

- Stage 02A.1 — Legacy Implementation Audit: **COMPLETED** (read-only source
  evidence documented).
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **COMPLETED**.

  The existing successful host-probe artifact was audited without another MDD
  request. The normalized request to `https://mdd.gorheinland.com/delfi` used
  XML/TRIAS 1.2 and returned HTTP **200** with
  `application/json;charset=UTF-8`; the body is JSON. It contains five
  `stopEventResult` records, timetable and estimate timestamps, identity,
  bay, operational-flag, and situation evidence. `RecordedAtTime`, direct
  delay, cancellation, and explicit platform-change fields were not present
  in this response.

  The artifact is temporary evidence at
  `/private/tmp/cologne-transit-stage-02a2-hhqob8km.raw`, outside this
  repository. It was not copied or committed. The audit found no unresolved
  technical blocker for this controlled response.

- Stage 02A.3 — Historical Actual-Time Availability Validation: **COMPLETED**.
  The previous host run already completed one independent HTTP request at each
  of 1, 7, 30, 90, and 180 days before execution. S16 made zero MDD HTTP
  requests and reanalysed only the five existing raw artifacts. The corrected
  result is historical timetable-only data at 1D/7D/30D and no event at 90D/
  180D; no `EstimatedTime` or `RecordedAtTime` was observed.

Current authoritative transport and authorization state:

- `HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS`.
- The historical Stage 02 comparison established that the then-tested macOS
  value matched the then-tested Windows/Legacy value; it does not prove that
  the current process has that value.
- The historical Stage 02 controlled request returned HTTP 200. The later
  2026-09-28 Stage 8 attempt returned HTTP 401 with the sanitized
  supplier-access message. Current Stage 8 authorization is therefore **NOT
  PROVEN** pending the S40 differential diagnostic.
- The available 2026-09-29 process preflight reported `MDD_API_KEY: MISSING`.
  No credential value or credential-derived information was inspected or
  exposed.
- The S14 technical-closure package remains the approved Stage 02 baseline.
  Stage 02 received explicit user approval in S17 after S16 completed the
  deferred historical actual-time gate evidence.

The earlier S10/S11/S12 transport and DNS findings remain in the Stage 02 audit
as historical chronology, not as current blockers. S13 made zero MDD requests,
executed zero probes, executed no SQL, and changed no database, Legacy
repository, Task Scheduler, or Power BI state.

The durable [realtime collector requirements](collector-design/realtime-collector-requirements.md)
are approved Stage 02/Stage 7 design inputs with implementation deferred. The
separate [near-event RecordedAtTime lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is a **POST-STAGE-02 COLLECTOR-DESIGN VALIDATION**; its bounded host evidence
is documented and does not replace the future Stage 8 pilot.

## Current authoritative decisions

- The project is management-oriented BI supported by data engineering.
- The umbrella management concept is **System Performance**.
- Analytical geography is public transport performance **inside Cologne**; a route merely passing through Cologne does not automatically make the whole route part of the core KPI scope.
- Primary timing objective: Actual Delay based on validated `RecordedAtTime` evidence.
- Stage 02 allows **Estimated Delay** only when timetable and estimate times
  are both present, comparable, and explicitly labelled. It does not approve
  an Actual Delay or prediction-accuracy KPI.
- `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis.
- MDD/TRIAS is the approved primary realtime source direction for the Cologne
  project; GTFS-RT is complementary/secondary for operational states,
  cross-source evidence, and broad coverage.
- The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
  makes GTFS-RT analytical scope Cologne-only; Germany-wide feed coverage does
  not expand the project scope. Only validated in-scope Cologne stop, service,
  and alert evidence may enter analytical scope.
- Cross-boundary routes contribute only in-scope Cologne evidence to core KPIs;
  an in-scope stop event is not an in-scope full trip. ICE, IC/EC, NightJet,
  and equivalent long-distance services remain outside core KPI scope.
- `MDD JourneyRef` and GTFS-RT `trip_id` must not be treated as equal. Future
  trip-level cross-source attribution requires a canonical reconciliation layer
  and HIGH-confidence matching, while GTFS-RT alert scope remains distinct.
- `ReportedCause` is provider-reported evidence, not `RootCause`; MDD
  Situation references and cross-source agreement do not by themselves prove
  causality. Source provenance and disagreement must be preserved.
- MDD and GTFS-RT must initially remain source-faithful and separate. Future
  reconciliation is deferred until source-specific staging is designed.
- `ObservedAt`/`CapturedAt` is a first-class future collection attribute;
  repeated observations must preserve estimate evolution rather than only the
  latest state.
- The [future source-observation and persistence contract](source-contracts/future-observation-persistence-contract.md)
  makes `TimetabledTime`, `EstimatedTime`, source-only nullable
  `RecordedAtTime`, and `ObservedAt`/`CapturedAt` independent concepts.
  `RecordedAtTime` is never synthesized; a first-seen non-null value remains
  retained if a later response is NULL or omits the event.
- Situation/Cause evidence is future append-only historical evidence: observed
  versions, source paths, text, reason/effect fields, validity, and association
  scope are preserved rather than reduced to one mutable cause value.
- PostgreSQL is the intended future persistence direction for the redesigned
  append-only observation architecture. Stage 5 now approves PostgreSQL 18 as
  the future authoritative platform direction and the clean redesign, but no
  migration, production PostgreSQL tables, or persistence implementation has
  been created. Database choice does not change source availability.
- The audited successful response did not contain `RecordedAtTime` or a direct
  delay value; this does not establish general MDD absence.
- `RecordedAtTime` is a TRIAS 1.2 optional actual-time field. It was not
  observed in the five tested historical responses, and 1D/7D/30D were
  timetable-only after corrected date alignment. This is not a provider-wide
  absence claim; representative coverage, reliability, and operational
  suitability remain later Stage 8/16 questions.
- `NotServicedStop` is a stop-level flag, not journey-level cancellation.
  `PlannedBay` and `EstimatedBay` remain separate, and situation association
  is not causal evidence.
- Full raw MDD responses are not a permanent or automated source of record by
  default; temporary diagnostic artifacts remain outside source control.
- Permanent or automatic full raw GTFS-RT protobuf retention is likewise not
  the default; any bounded automatic capture requires a later explicit gate.
- Core approved Stage 4 modes: Stadtbahn/Tram, urban/KVB bus, S-Bahn, RE, RB,
  and Regional/Other Bus, always limited to validated in-Cologne events and
  segments.
- Stage 4 validated the RE, RB, and Regional/Other Bus service-level scope;
  their external trip portions remain outside Cologne KPI scope.
- SEV is retained as a separate analytical category, and long-distance rail
  remains outside core KPI scope.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.
- Stage 02 is **APPROVED**; Stage 03 is **COMPLETE** as a design/contract
  stage.
- The [Stage 3 realtime collection design readiness document](stage-03/realtime-collection-design-readiness.md)
  now records **STAGE 3 COMPLETE — 3A APPROVED — 3B/3C DESIGN COMPLETE** and
  carries the implementation boundary forward; it does not approve production
  activation.
- The [Stage 3A disruption/cause evidence audit](stage-03/03a-disruption-cause-alert-evidence-audit.md)
  is **COMPLETE — APPROVED** for the bounded evidence already collected. Its
  conclusions remain evidence-only and non-causal.
- The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
  is the approved Stage 3C design contract; it does not approve production
  implementation or matching code.
- The [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
  records the approved collection/cadence policy, matching design, acceptance
  checks, and carried-forward implementation risks.

## Former open critical questions and S15/S16 refinement

The S14 baseline reclassified the questions below. S15 intentionally reopened
only the historical actual-time availability evidence question, and S16
completed its offline audit without invalidating the S14 semantic or
source-contract conclusions.

1. `RecordedAtTime` semantic role — **RESOLVED_FOR_STAGE_02**. In the five
   tested historical responses, `RecordedAtTime` was not observed, and the
   1D/7D/30D responses were timetable-only after corrected date alignment.
   This is evidence for the tested requests, not a provider-wide absence
   claim. Representative population coverage, reliability, and operational
   suitability remain **DEFERRED_TO_STAGE_8** and **DEFERRED_TO_STAGE_16**.
2. Current MDD permission/access boundary — **RESOLVED_FOR_STAGE_02**. Access
   works; registration/activation, provider terms, and agreed use remain
   documented constraints. Future publication or redistribution requires a
   separate provider-terms check.
3. Quota — **RESOLVED_FOR_STAGE_02** at the conservative-contract level. The
   documented ceiling and shared-caller caveat are captured; operational
   budgeting is **DEFERRED_TO_STAGE_7** and **DEFERRED_TO_STAGE_13**.
4. Authoritative GTFS feed and coverage/update policy — **DEFERRED_TO_STAGE_3**;
   it is not a Stage 02 blocker.
5. Future retained TRIAS fields — **RESOLVED_FOR_STAGE_02** by the field-
   retention matrix; implementation/schema design belongs to later approved
   stages.
6. Full raw-payload retention — **RESOLVED_FOR_STAGE_02** by the approved
   raw-retention decision; Stage 7 now approves only the bounded Stage 8
   temporary exception with protected storage and maximum 24-hour retention.

**FINAL STAGE 02 APPROVAL: APPROVED.**

Stage 02 approval does not approve Actual Delay, a production collector change,
a new polling schedule, or any permanent raw-payload retention policy. The five
host requests were a bounded diagnostic/source-contract validation completed
before S16; S16 itself performed zero MDD HTTP requests. The later lifecycle
validation is separately tracked as post-approval evidence.

## Stage 02A.3 S16 corrected offline result

The raw artifacts at
`/private/tmp/cologne-transit-stage-02a3-3qdsj02c` were all readable JSON:

| Artifact | Bytes | Results | Corrected result |
|---|---:|---:|---|
| `01-1d.raw` | 56,216 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `02-7d.raw` | 55,838 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `03-30d.raw` | 54,340 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `04-90d.raw` | 429 | 0 | `HISTORY_NO_EVENT`; populated `STOPEVENT_NOEVENTFOUND` |
| `05-180d.raw` | 429 | 0 | `HISTORY_NO_EVENT`; populated `STOPEVENT_NOEVENTFOUND` |

The original 1D/7D/30D summaries reported `ThisCall` timetable counts but
`null` min/max values and `INCONCLUSIVE` alignment because the provider
lexical value ended in `Z[GMT]`. The `Z` is explicit UTC; `[GMT]` is a
provider suffix. S16 now strips only that observed suffix before parsing and
preserves timezone-less values as unresolved rather than assigning a guessed
offset. Calendar comparison is explicitly **DATE-ALIGNMENT ONLY**.

The original `response_error_message_count` and
`result_error_message_count` counts came from counting empty `ErrorMessage`
containers. S16 counts only populated messages: both counts are zero for
1D/7D/30D, while the populated response-level message and
`STOPEVENT_NOEVENTFOUND` evidence remain for 90D/180D.

No historical response contained `EstimatedTime`, `RecordedAtTime`, a
recorded/actual timing candidate, or another actual-looking timestamp. The
responses did contain delivery `responseTimestamp` values and processing
`calcTime`; neither is treated as vehicle timing. `estimatedBay` was present
as empty arrays in the result structures and is not an estimated arrival time.

At the tested 1-, 7-, and 30-day horizons, the MDD StopEvent interface
returned historical scheduled events but did not return `EstimatedTime` or
`RecordedAtTime` in those responses. Retrospective StopEvent retrieval
therefore cannot currently be relied on to reconstruct operational actual
delay. A prospective collector remains important for preserving future
realtime evidence, and any later actual-delay capability still requires a
validated actual-time source. Collector cadence and schema are deferred to
later stages.

No StopEvent result was returned for the specific tested Köln Hbf arrival
request at the 90-day or 180-day target times. This does not establish a
retention cutoff or a provider-wide history boundary. No additional
historical request is required for the current Stage 02 source-contract
decision.

The point-in-time Legacy Collector quota audit snapshot at **2026-09-25
11:23:41 UTC** recorded **1,599** collector executions, **1,629** HTTP
attempts, and **35** additional retry attempts. Against the documented
monthly quota of **250,000 requests**, that is approximately **0.6516%**
recorded collector usage, with approximately **99.3484%** remaining against
that documented quota. This proves recorded consumption by the Legacy
Collector at that audit snapshot only; it does not prove total account-level
MDD consumption if another caller shares the same API key or account.

Do not implement around these deferred items before their designated approval
gates.

See the [Stage 02 source-contract package](source-contracts/README.md) and
[detailed Stage 02 synthesis](stages/02-mdd-trias-source-contract-audit.md)
for the full evidence layers, retention matrix, analytical permission matrix,
and acceptance criteria.

## Post-Stage-02 handoff and validation

- Stage 02 approval synchronization and approved decisions are recorded in the
  [Decision Log](DECISION-LOG.md).
- Collector implementation is deferred, and later collector-design stages must
  begin with the [realtime collector requirements](collector-design/realtime-collector-requirements.md).
- The [RecordedAtTime lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
  is complete as bounded post-Stage-02 evidence: three Köln Hbf and three Köln
  Neumarkt journeys were reviewed, with no `RecordedAtTime` observed. It does
  not make a provider-wide absence claim or reopen Stage 02.
- The [GTFS-RT historical availability audit](source-contracts/gtfs-rt-historical-availability-audit.md)
  is `BOUNDED VALIDATION COMPLETE`; the tested ±7-day and ±45-day metadata
  windows recovered zero exact-URL captures at both requested horizons.

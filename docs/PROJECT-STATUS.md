# Project Status

**Project:** Cologne Transport Intelligence
**Repository:** `sonazariso/cologne-transit-intelligence`
**Status date:** 2026-09-28

## Authoritative status

| Stage | Name | Status |
|---|---|---|
| 0 | Project Definition | APPROVED |
| 1 | Source Inventory & Permission | APPROVED |
| 2 | MDD/TRIAS Source-Contract Audit | APPROVED |
| 3 | Static GTFS Profile / Realtime Evidence Validation | IN PROGRESS |
| 4 | Cologne Scope & Mode Classification | Not started |
| 5 | Database Architecture | Not started |
| 6 | Static Warehouse & Analytics | Not started |
| 7 | Realtime Pilot Design | Not started |
| 8 | Small Realtime Pilot | Not started |
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

- **Stage 3 — IN PROGRESS.**
- **Stage 3A — Disruption Cause & Alert Evidence Audit — PARTIAL HOST EVIDENCE
  — MDD SITUATION DISCOVERY PENDING.**
- Stage 3A is a bounded host-run evidence audit only. It does not start
  production implementation, database/schema work, collector changes,
  scheduler changes, Power BI work, or cross-source matching.

See the [Stage 3A audit document](stage-03/03a-disruption-cause-alert-evidence-audit.md).

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
  production source. Stage 3 is now in progress only through the bounded Stage
  3A evidence audit; no production implementation is authorized.

### Post-Stage-02 source validation package

| Package | Current status |
|---|---|
| MDD/TRIAS source contract | **COMPLETED / APPROVED** |
| GTFS-RT current-feed feasibility | **BOUNDED VALIDATION COMPLETE** |
| GTFS.de identity bridge | **BOUNDED / PARTIAL** — Neumarkt current identity validated; railway Köln Hbf unresolved |
| GTFS-RT lifecycle validation | **BOUNDED VALIDATION COMPLETE** |
| GTFS-RT historical availability | **BOUNDED VALIDATION COMPLETE** — zero exact-URL captures in both ±7-day and ±45-day metadata runs |
| Realtime source-role direction | **APPROVED DIRECTION** — MDD primary; GTFS-RT complementary |
| Cross-source reconciliation and Cologne scope contract | **APPROVED DESIGN GUARDRAIL** — implementation not started |
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
- `MDD_API_KEY VALUE COMPARISON: SAME`.
- `CURRENT MDD AUTHORIZATION: WORKING`.
- The earlier 401 is historical evidence and is strongly explained by the
  earlier different macOS credential value; no provider-side entitlement
  change is claimed.
- The S14 technical-closure package remains the approved Stage 02 baseline.
  Stage 02 received explicit user approval in S17 after S16 completed the
  deferred historical actual-time gate evidence.

The earlier S10/S11/S12 transport and DNS findings remain in the Stage 02 audit
as historical chronology, not as current blockers. S13 made zero MDD requests,
executed zero probes, executed no SQL, and changed no database, Legacy
repository, Task Scheduler, or Power BI state.

The durable [realtime collector requirements](collector-design/realtime-collector-requirements.md)
are approved Stage 02 design inputs with implementation deferred. The separate
[near-event RecordedAtTime lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is a **POST-STAGE-02 COLLECTOR-DESIGN VALIDATION** with host execution pending;
it is not a Stage 02 blocker.

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
- Core starting modes: Stadtbahn/Tram, urban/KVB bus, and S-Bahn.
- RE, RB, and regional/other bus require Stage 4 service-level scope validation.
- SEV is retained as a separate analytical category.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.
- Stage 02 is **APPROVED**; Stage 03 is **IN PROGRESS** through Stage 3A.
- The [Stage 3 realtime collection design readiness document](stage-03/realtime-collection-design-readiness.md)
  now records **STAGE 3 IN PROGRESS — STAGE 3A PARTIAL HOST EVIDENCE — MDD
  SITUATION DISCOVERY PENDING** and
  continues to record guardrails only; it does not approve production
  implementation.
- The [Stage 3A disruption/cause evidence audit](stage-03/03a-disruption-cause-alert-evidence-audit.md)
  is **PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING**. No analytical decision is final before its
  bounded host evidence is reviewed.
- The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
  is an approved design guardrail for subsequent Stage 3 design work; it does
  not approve production implementation or cross-source matching.

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
   raw-retention decision; any automated exception requires a later Stage 7
   gate.

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

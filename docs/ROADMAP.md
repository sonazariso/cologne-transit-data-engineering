# Restart Roadmap

Every stage follows the same control pattern:

**Goal → Current Evidence → Questions/Assumptions → Options → Risks → Tests → Decision → Approval Gate**

No stage is considered authoritative merely because a similar solution existed in the legacy repository.

## Stage 0 — Project Definition — APPROVED
Define business questions, portfolio goal, geography, modes, System Performance, audience, and explicit exclusions.

## Stage 1 — Source Inventory & Permission — APPROVED
Re-establish GTFS and MDD source ownership, URLs/contracts, feed coverage, update frequency, API quota, permission/retention conditions, TRIAS version, endpoint contract, and current legacy quota consumption. **No new polling is required to perform the initial quota audit.**

## Stage 2 — MDD/TRIAS Source-Contract Audit — APPROVED; 02A.1/02A.2/02A.3 COMPLETED
The S14 technical-closure package remains the approved baseline. The user
approved Stage 02 explicitly in S17 after Stage 02A.3 — Historical Actual-Time
Availability Validation was completed. The previous host run completed one
independent request at each of 1D, 7D, 30D, 90D, and 180D, using a 12:00
Europe/Berlin target, no retries, and temporary raw artifacts outside the
repository. S16 made zero MDD HTTP requests and corrected the analysis offline
using those five artifacts.

The corrected evidence classifies 1D/7D/30D as historical timetable-only:
their returned `ThisCall` timetable dates match the requested dates, but no
`EstimatedTime` or `RecordedAtTime` is present. The 90D/180D responses contain
no StopEvent result and populated `STOPEVENT_NOEVENTFOUND` evidence for the
specific tested Köln Hbf arrival request. This does not establish a retention
cutoff. Retrospective retrieval cannot currently be relied on to reconstruct
operational actual delay; a prospective collector remains important, and a
validated actual-time source is still required. Cadence and schema remain
deferred to later stages.

The approved source-contract package compares official TRIAS 1.2 semantics, the
successful controlled MDD response, Legacy parser candidates, and Legacy
persistence requirements. It documents `RecordedAtTime`, estimate ranges,
errors/no-realtime evidence, cancellation/status fields, `OperatingDayRef`,
`StopSeqNumber`, origin/destination, call structure, `NumberOfResults`,
`TimeWindow`, provider limits, and raw-payload retention. The historical S15
gate did not approve Actual Delay, a production polling schedule, or a
collector/schema change; the S17 approval preserves those constraints.

The [approved collector requirements](collector-design/realtime-collector-requirements.md)
are a later-stage handoff, not implementation. The [near-event RecordedAtTime
lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is separately tracked as `POST-STAGE-02 COLLECTOR-DESIGN VALIDATION` with host
execution pending and does not reopen Stage 02.

The [GTFS-RT Source Feasibility Audit](source-contracts/gtfs-rt-source-feasibility-audit.md)
is a separate post-Stage-02 validation. Its current-feed inventory is complete
from supplied host evidence, and its two-sample Köln Neumarkt lifecycle check
is **BOUNDED LIFECYCLE VALIDATION COMPLETE**. The [GTFS.de static identity bridge](source-contracts/gtfsde-static-identity-bridge-audit.md)
remains partial because railway Köln Hbf is unresolved. The bounded result does
not reopen Stage 02, approve GTFS-RT as a production source, or authorize
production implementation.

The [GTFS-RT Historical Availability Audit](source-contracts/gtfs-rt-historical-availability-audit.md)
is the separate S19 post-Stage-02 validation and is **BOUNDED VALIDATION
COMPLETE**. The initial ±7-day exact-URL test and the separate ±45-day
metadata-only test each returned zero captures at the 3-month and 6-month
targets. These results do not establish provider-supported or continuous
history or change the collector design; current disruption evidence is now
tracked separately under Stage 3A.

The bounded MDD/TRIAS near-event review covered six live journeys (three at
Köln Hbf and three at Köln Neumarkt). No `RecordedAtTime` was observed in the
tested `ThisCall`, `PreviousCall`, or `OnwardCall` contexts. Prediction
evolution was observed in some journeys, so prospective collection remains
necessary; this is not a provider-wide absence claim.

The approved realtime source-role direction is **MDD/TRIAS primary** and
**GTFS-RT complementary/secondary**. Future collection must preserve each
source's semantics and provenance separately before any normalization or
reconciliation. Confirmed Actual Time remains unavailable and Actual Delay is
not approved.

The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
is an approved design guardrail for the next realtime design work. GTFS-RT
analytical scope is Cologne-only even though the source feed is Germany-wide;
only validated Cologne stop, service, and alert evidence may enter the
analytical scope. Cross-boundary routes contribute only in-scope Cologne
evidence to core KPIs, long-distance rail remains outside core KPI scope, and
`MDD JourneyRef` must not be equated with GTFS-RT `trip_id`. Trip-level
cross-source cause/effect attribution requires HIGH-confidence reconciliation,
while alert scope and source provenance remain distinct.

## Stage 3 — Static GTFS Profile / Realtime Evidence Validation — COMPLETE

Stage 3 was completed as a bounded evidence, collection-design, and
reconciliation-design stage. The
approved source direction remains MDD/TRIAS **PRIMARY REALTIME** and GTFS-RT
**COMPLEMENTARY REALTIME**. No production implementation or activation is
authorized by this stage.

### Stage 3A — Disruption Cause & Alert Evidence Audit — COMPLETE — APPROVED

The [Stage 3A audit](stage-03/03a-disruption-cause-alert-evidence-audit.md)
measures populated GTFS-RT ServiceAlert cause/effect/text/scope evidence in
one current snapshot and MDD Situation content/association evidence in fixed
diagnostic station responses. The completed MDD-only discovery used ten MDD
POST requests with no retries, concurrency, or fallback endpoint and no
GTFS-RT request.

The [Stage 3 realtime collection design readiness document](stage-03/realtime-collection-design-readiness.md)
records **STAGE 3 COMPLETE — 3A APPROVED — 3B/3C DESIGN COMPLETE**. It
remains a design handoff and does not constitute a production collector,
schema, scheduler, or Power BI approval.

The initial two-station MDD execution and the separate host-only ten-station
MDD Situation discovery are complete. The discovery JSONL was reviewed
offline; it made no GTFS-RT request. The explicit user approval closes the
bounded Stage 3A evidence gate.

### Stage 3B — Collection Strategy & Cadence — COMPLETE — APPROVED DESIGN; ACTIVATION DEFERRED

The [Stage 3B collection and cadence contract](stage-03/03b-collection-strategy-cadence-contract.md)
approves separate MDD and GTFS-RT collection loops, a tiered quota-aware
Sampling Policy/Era, bounded retries, overlap protection, and independent
source/collector timestamps. It does not approve the historical 10/20/20
schedule, final station tiers, final intervals, or production activation.

### Stage 3C — Cross-Source Matching Design — COMPLETE — APPROVED DESIGN HANDOFF

The [Stage 3C cross-source matching design](stage-03/03c-cross-source-matching-design.md)
defines stop-event-first reconciliation, canonical service-event dimensions,
HIGH/MEDIUM/LOW-or-UNMATCHED confidence, auditable match methods, Cologne
scope, and non-causal alert/Situation association. It preserves the unresolved
railway Köln Hbf identity and does not implement matching.

The [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
is the authoritative closeout record. Stage 4 was subsequently revalidated and
approved under its own current static-source validation gate below.

Authoritative-feed coverage and final collector readiness remain later work.
The cross-source contract was the Stage 3C design handoff; Stage 9 now owns the
implemented matching/data-quality foundation described in
[the Stage 9 record](stages/09-matching-data-quality.md).

## Stage 4 — Cologne Scope & Mode Classification — COMPLETE — APPROVED

The current official VRS Global-ID GTFS snapshot from
`https://download.vrs.de/gtfs/google_transit_goR.zip` was validated with the
direct `stop_id` prefix rule `de:05315:`. Its feed version is
`VERSION__20260928_0042`, its validity is `2026-09-02` through `2026-12-12`,
and its ZIP SHA-256 is
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`. The
freshness classification is `CURRENT_OR_OPERATIONALLY_RELEVANT` for the
2026-09-28 project date. The approved analytical scope is stop-event-first; an
internal segment requires both consecutive endpoints to be validated Cologne
stops. Source route/trip identity remains separate from geographical KPI
scope, so cross-boundary services contribute only their Cologne events and
Cologne-internal segments.

The current snapshot contains 3,137 distinct Cologne stop IDs, 1,144,295
Cologne scheduled stop events, 1,082,490 internal segments, 61,805
Cologne-serving trips, 39,566 Cologne-internal trips, 22,239 cross-boundary
trips, 1,606 touch-only trips, and 137 Cologne-serving route IDs. The approved
source-faithful classifier includes Stadtbahn/Tram, Urban/KVB Bus, S-Bahn, RE,
RB, and Regional/Other Bus as core modes for in-Cologne evidence; SEV remains
a separate category; long-distance and unresolved/unsupported values remain
outside core scope. The observed `route_type=4` ferry row is retained as
explicit `OTHER_MODE` and has no Cologne events.

The [Stage 4 completion document](stages/04-cologne-scope-mode-classification.md)
and [current deterministic evidence JSON](stages/04-cologne-scope-mode-evidence.json)
are authoritative. The [historical/legacy evidence JSON](stages/04-cologne-scope-mode-evidence-historical-2026-09-28.json)
is preserved for comparison and is not current authority. Stage 4 does not
implement a database, collector, matching layer, warehouse, or Power BI model.

## Stage 5 — Database Architecture — COMPLETE — APPROVED ARCHITECTURE
PostgreSQL 18 is approved as the future authoritative platform direction,
using the latest supported PostgreSQL 18 minor available at implementation
time. The approved strategy is a clean PostgreSQL redesign with `ctl`, `src`,
`wrk`, `core`, `dw`, and `analytics` layers. The Stage 5 architecture,
42-entry table/grain catalog, and completion decision define source-faithful
boundaries, static content-hash snapshots, MDD observation history, the bounded
GTFS-RT persistence boundary, keys/types/history/idempotency, indexing,
partition direction, lineage, security, backup/recovery, and legacy import
policy. No PostgreSQL database, schema, table, migration, SQL execution, or
runtime implementation was created. Stage 6 receives the static
warehouse/analytics design and contract handoff; physical implementation
remains deferred until explicit user authorization.

## Stage 6 — Static Warehouse & Analytics Design — COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED
The [Stage 6 design contract](stages/06-static-warehouse-analytics-design.md)
defines the Cologne-focused static dimensions, service-date bridge,
pattern-level scheduled facts, GTFS extended-hour normalization, Stage 4
scope/mode application, static analytics outputs, snapshot lineage, and future
source-to-warehouse acceptance matrix. No PostgreSQL object, SQL execution,
network request, or source load is part of this stage.

## Stage 7 — Realtime Pilot Design — COMPLETE — APPROVED PILOT DESIGN

The [Stage 7 realtime pilot design](stages/07-realtime-pilot-design.md) and
[completion decision](stages/07-realtime-pilot-design-completion-decision.md)
fix a bounded, reproducible Stage 8 handoff without executing it. MDD/TRIAS is
the only primary pilot source; GTFS-RT remains complementary and is excluded
from the one-source loop because Stage 9 owns cross-source matching.

The approved target is Köln Hbf, `de:05315:11201`. The exact pilot request uses
TRIAS 1.2 `POST`, a fixed pilot-start `DepArrTime` anchor reused for every
request, `NumberOfResults=20`, `arrival`, previous/onward/realtime flags true,
`TimeWindow` omitted, and `IncludeOperatingDays` omitted. The active V3
sampling policy is fixed start-to-start slots every 3 seconds for at most 60
minutes and at most 1200 scheduled logical slots and 1200 normal HTTP attempts,
with a 30-second timeout, zero retries, sequential/non-concurrent execution,
no catch-up bursts, explicit overlap skips, quota accounting, and early-stop
rules. The 1200-attempt ceiling is 0.48% of the conservative 250,000-request
monthly ceiling and approximately 0.57–0.58% of the 207,500–210,000-request
operational planning envelope (83–84%). Preserve 40,000–42,500 requests/month
(16–17%) as unallocated headroom; normal production planning must not use the
full ceiling. This remains a bounded diagnostic cadence, not production
approval, and Stage 13 owns production station tiers, cadence, staggering,
retry policy, overlap analysis, and full monthly projection. A bounded 24-hour
temporary raw-response exception and a protected append-only JSONL diagnostic
persistence harness are approved; PostgreSQL remains deferred. Retained MDD
lifecycle/probe evidence shows response elapsed times predominantly below 3
seconds, from sub-second observations to approximately 2 seconds, but does
not guarantee every response completes within 3 seconds. Three-second sampling
reduces but cannot mathematically guarantee capture of a transient
`RecordedAtTime`. Actual Delay remains unapproved.

## Stage 8 — Small Realtime Pilot — COMPLETE

Authoritative runtime status: `stage_8_status = COMPLETE`.

Stage 8 owns host execution of the approved MDD-only pilot. The historical
chronology remains in the [Stage 8 record](stages/08-small-realtime-pilot-blocked.md):
an earlier missing-key preflight made zero requests; earlier invalid/local
credential-configuration attempts included a different-credential macOS HTTP
401 and a later corrected Legacy-equivalent configuration; and a later
2026-09-28 attempt returned HTTP `401` with the observed German
supplier-access message.

Under the corrected local credential configuration, the bounded differential
diagnostic then succeeded at Köln Hbf (`de:05315:11201`): Control A with
`NumberOfResults=5` returned HTTP `200` and a parseable response, and Control B
with `NumberOfResults=20` returned HTTP `200` and a parseable response using
the same diagnostic `DepArrTime` anchor. The classification was
`CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING`. No request-contract
reduction was required. The evidence supports the bounded conclusion that
the earlier 401 does not show that Hbf/N20 requires additional supplier
authorization; it is not a claim about every MDD data supplier.

The full effective Hbf/N20/V3 pilot completed successfully with approximately
`3606.801` seconds elapsed, `1200` planned logical slots, `343` attempted
requests, `343` HTTP 200 responses, `343` successful parser/reconciliation
responses, a `1.0` reconciliation rate, `6860` `StopEventResult` occurrences,
zero parser/persistence/network-DNS/timeout/application failures, and
`SUCCEEDED` cleanup. It skipped `857` overlapping fixed slots by design. The
collector remained sequential and did not queue or catch up those slots; they
are not collector failures. This is evidence for the later Stage 13
sequential-versus-bounded-concurrency/readiness decision, which is not
performed here.

The bounded result is exactly
`RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`; no provider-wide absence
claim is made. Actual Physical Delay remains **NOT_APPROVED**. The live
parser-gap evidence includes the currently unmapped source fields
`affectedVehicleJourney`, `datedVehicleJourneyRef`, `destinations`,
`direction`, `operatingDaysDescription`, `origins`, `situations`,
`vehicleJourneyRef`, and `vehicleJourneys`; these remain evidence for later
matching/data-quality review and are not added to the production schema here.
Situation parsing/persistence remains enabled.

Successful-control metadata no longer receives a failure classification, and
normal Situation/passenger-information text no longer populates
`sanitized_provider_error_text`; genuine failed-response error evidence,
including the observed German 401 body, remains available for supplier-access
detection. Offline validation for these corrections used no MDD request.
The future 50-station objective is unchanged, Stage 13 retains ownership of
future Tiered50 production result-limit/cadence/concurrency decisions, and at
this Stage 8 closeout point Stage 9 remained not started.

## Stage 9 — Matching & Data Quality — COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS

The [Stage 9 implementation and validation record](stages/09-matching-data-quality.md),
the [offline matcher](../scripts/stage_09_matching_quality.py), and the
[evidence artifact](stages/09-matching-data-quality-evidence.json) separate
implementation regression from source-backed acceptance. The regression
validates stop-event-first matching, explicit Stage 8 adapter projection,
timezone/DST service-day alignment, mapping validity, ambiguity handling,
scope handling, and lineage. The corrected bounded source-backed gate passed
after three sequential HTTP 200 MDD responses: 60 parsed/adapted observations
were retained, 53 service dates were independently established, and 51 core
deterministic matches were produced, with 51 `MEDIUM` and 9 `UNMATCHED`
decisions. The durable row-level evidence passed exact JSONL-to-summary
integrity reconciliation. The current VRS hierarchy passed all 17 Stage 4
revalidation checks; railway Köln Hbf remains unresolved and no adjacent KVB
identity is substituted. The exact run is in
`stages/09-source-backed-matching-summary.json`, with sanitized row-level
evidence in `stages/09-source-backed-matching-evidence.jsonl`. The current Stage
10 status is **NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED**; Stage 11 is now closed as an approved design; Stage 12 is now **COMPLETE — APPROVED FIXED 50-STATION PANEL**; and Stage 13 is now closed as an approved readiness design while production remains not started.

## Stage 10 — Operational Fact — NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED
The source-controlled PostgreSQL `dw.fact_operational_service_event` contract
uses the canonical service-event identity, explicit Stage 9 run/version
selection, HIGH-only eligibility, the derived
`wrk.v_stage10_operational_observation_input` evidence boundary,
first/latest timing, NULL handling, PlannedBay/EstimatedBay evidence,
non-causal Situation linkage, and repeat-refresh validation. Completion remains
blocked because this checkout has no usable PostgreSQL/source-observation
population and the current Stage 9 source-backed artifact contains zero HIGH
rows. Stage 11 is now closed as an approved design; Stage 12 was not started at
that historical Stage 9 closeout, and the later Stage 13 audit is recorded
separately below.

## Stage 11 — Power BI Model — COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED

The [Stage 11 semantic-model design](stages/11-power-bi-model-design.md) and
[completion decision](stages/11-power-bi-model-completion-decision.md) approve
the three subject areas, semantic table grains, single-direction relationship
matrix, governed Date strategy, static VRS/canonical operational identity
boundary, explicit DAX measure groups, estimated-delay threshold and NULL
semantics, bay/Situation terminology, service-date-scoped matching coverage
with explicit missing-service-date evidence, sampling/network-coverage language,
and executive versus technical reporting boundaries. `Dim Sampling Era`
currently filters the `Fact Matching Decision` population only. Data Quality
classifications carried by those matching decisions inherit that context; the
separate `Fact Data Quality Issue` and `Data Quality Issue Count` are not
Sampling-Era-filtered in the base model. `Dim Sampling Era` is not an
operational KPI slicer and has no approved relationship to `Fact Operational
Service Event`. A future relationship to the independent issue fact or an
operational-event projection
requires explicit `SamplingEraKey` lineage at that fact's grain and validated
projection grain, key uniqueness, historical-era semantics, and lineage; it
must handle cross-era events without arbitrary assignment. Until then,
operational KPI analysis must constrain compatible era/time scope or show a
clear coverage/methodology warning. The future implementation contract is PBIP +
TMDL + PBIR with Power BI Desktop binding and QA, but no Power BI artifact or
Desktop execution is part of this stage.

## Stage 12 — 50-Station Selection — COMPLETE — APPROVED FIXED 50-STATION PANEL

The [selection document](stages/12-50-station-selection.md), [completion decision](stages/12-50-station-selection-completion-decision.md), [sanitized evidence](stages/12-50-station-selection-evidence.json), and [complete candidate-universe replay artifact](stages/12-50-station-candidate-evidence.json) approve `TIERED50_PANEL_V1`: exactly 50 unique validated VRS parent stations, all seven Legacy7 continuity anchors, measured current core-mode representation, a fixed geographic-diversity review, and 50 successful bounded sequential MDD compatibility results. The candidate artifact reproduces the approved panel offline from the exact Stage 12 source metrics without network, the original ZIP, MDD, or credentials. Membership is fixed after approval; later changes require a new version with effective time, reason, and predecessor.

Stage 4 semantics were approved against `VERSION__20260928_0042`; Stage 12
selection used the later `VERSION__20260930_0053` snapshot. Parent, child-stop,
and event-count differences are snapshot evolution, while Stage 4 identity and
classification semantics were re-applied to Stage 12. Stage 13 readiness design
is complete; Stage 13 production work and Stage 14 remain **NOT STARTED**.

## Stage 13 — Tiered Collector Readiness — COMPLETE — APPROVED DESIGN; PRODUCTION NOT STARTED

The [readiness audit](stages/13-tiered-collector-readiness.md), [completion decision](stages/13-tiered-collector-readiness-completion-decision.md), and [evidence artifact](stages/13-tiered-collector-readiness-evidence.json) close the exact-feed readiness design for `TIERED50_PANEL_V1`. The panel has 10 Tier A stations at five-minute cadence, 20 Tier B stations at 15-minute cadence, and 20 Tier C stations at 30-minute cadence. Density uses scheduled child-stop arrivals across 5/10/15/30-minute windows and does not use Stage 12 `SelectionRank` as a tier.

The approved request design is `NumberOfResults=30`, based on 24 sequential
N5/N10/N20/N30 diagnostics across six deterministic representatives, with
three cap-2 pairs. All tested limits saturated; the provider theoretical
maximum remains unknown. The design reconciles 240 logical requests/hour,
5,760/day, 172,800/30-day month, and 178,560/31-day month, with zero automatic
retries, a conservative Legacy7 reserve of 8,640/8,928 requests, and no more
than 100 Stage 13 diagnostic attempts. The heartbeat distribution is
`[21, 21, 19, 20, 20, 19]`; atomic target/bucket claims, no catch-up,
failure isolation, and Legacy7 history preservation remain mandatory.

The cap-2 result is a bounded design decision, not a provider-wide rate-limit
claim. Current shared-credential production usage remains unavailable and is
not treated as zero. Stage 13 does not authorize a scheduler, collector
activation, database objects, Power BI work, Windows tasks, or Stage 14 cutover.

## Stage 14 — Controlled 50-Station Cutover
Preserve legacy history and IDs, add only approved targets, record sampling-era change, dry-run first, and do not fabricate missed snapshots.

## Stage 15 — Post-Cutover Validation
Monitor from day one and define when the new sampling era has enough data for broader interpretation.

## Stage 16 — Revisit Timing/Data Coverage Issues
Re-analyse unavailable timing, `RecordedAtTime` coverage, representative
field coverage, and static-coverage gaps using the richer source contract and
new data rather than guesses.

## Stage 17 — Actual Physical Delay Productionization
Productionize Actual Delay only if Stage 2/8 evidence proves a valid recorded/actual timing source and later matching/fact semantics support it. Stage 0 sets Actual Delay as the priority objective; this stage is where it becomes a production KPI after evidence is sufficient.

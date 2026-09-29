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

## Stage 9 — Matching & Data Quality — COMPLETE — FOUNDATION IMPLEMENTED

The [Stage 9 implementation and validation record](stages/09-matching-data-quality.md)
and [offline matcher](../scripts/stage_09_matching_quality.py) validate stop,
route, service-date, trip/event identity, candidate richer identifiers, static
coverage, field coverage, repeated observations, alert scope, and ambiguous
cases using known-good and deliberately unresolved populations. The
source-controlled PostgreSQL contract is in
`sql/stage_09_matching_quality.sql`. Railway Köln Hbf remains unresolved in
the bounded GTFS.de bridge, and the implementation does not substitute an
adjacent KVB identity. Stage 10 and later stages remain separate.

## Stage 10 — Operational Fact
Approve fact grain, consolidation semantics, first/latest/actual timing semantics, NULL handling, platform evidence, situation evidence, and lineage.

## Stage 11 — Power BI Model
Build only after a stable semantic layer. Review relationships, DAX, thresholds, terminology, executive reporting, drill-down, and data-quality context.

## Stage 12 — 50-Station Selection
Re-evaluate and approve a defensible fixed monitoring panel and versioning policy.

## Stage 13 — Tiered Collector Readiness
Audit cadence, `NumberOfResults` coverage, event density, execution duration,
retries, quota, phase staggering, concurrency, overlap protection, and all
active callers sharing the credential before activation.

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

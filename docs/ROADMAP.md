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

### Stage 3C — Cross-Source Matching Design — COMPLETE — APPROVED DESIGN; IMPLEMENTATION NOT STARTED

The [Stage 3C cross-source matching design](stage-03/03c-cross-source-matching-design.md)
defines stop-event-first reconciliation, canonical service-event dimensions,
HIGH/MEDIUM/LOW-or-UNMATCHED confidence, auditable match methods, Cologne
scope, and non-causal alert/Situation association. It preserves the unresolved
railway Köln Hbf identity and does not implement matching.

The [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
is the authoritative closeout record. Stage 4 was subsequently completed and
approved under its own static-source validation gate below.

Authoritative-feed coverage, canonical bridge construction, final collector
readiness, and implementation validation remain later work. The cross-source
contract is now the Stage 3C design handoff and does not authorize
implementation.

## Stage 4 — Cologne Scope & Mode Classification — COMPLETE — APPROVED

The official VRS Global-ID GTFS snapshot was validated with the direct
`stop_id` prefix rule `de:05315:`. The approved analytical scope is
stop-event-first; an internal segment requires both consecutive endpoints to
be validated Cologne stops. Source route/trip identity remains separate from
geographical KPI scope, so cross-boundary services contribute only their
Cologne events and Cologne-internal segments.

The current snapshot contains 862 distinct Cologne stop IDs, 448,755 Cologne
scheduled stop events, 421,999 internal segments, 26,692 Cologne-serving
trips, 13,268 Cologne-internal trips, 13,424 cross-boundary trips, 1,187
touch-only trips, and 136 Cologne-serving route IDs. The approved source-
faithful classifier includes Stadtbahn/Tram, Urban/KVB Bus, S-Bahn, RE, RB,
and Regional/Other Bus as core modes for in-Cologne evidence; SEV remains a
separate category; long-distance and unresolved/unsupported values remain
outside core scope.

The [Stage 4 completion document](stages/04-cologne-scope-mode-classification.md)
and [deterministic evidence JSON](stages/04-cologne-scope-mode-evidence.json)
are authoritative. Stage 4 does not implement a database, collector,
matching layer, warehouse, or Power BI model.

## Stage 5 — Database Architecture — NOT STARTED / NEXT
The intended future database direction is PostgreSQL for an append-only,
source-faithful observation layer followed by derived analytical state. Approve
grain, schemas/layers, source-faithful vs derived data, keys, history strategy,
indexes, and audit strategy before building. This direction does not change
source availability; no PostgreSQL migration or production tables are approved
before this stage.

## Stage 6 — Static Warehouse & Analytics
Build static dimensions/facts/views incrementally and reconcile source-to-warehouse counts.

## Stage 7 — Realtime Pilot Design
Approve target rationale, request contract, `NumberOfResults`, `TimeWindow` if
supported, retry/timeout, quota budget, any justified bounded raw capture, and
audit. Collector implementation begins by reviewing
[docs/collector-design/realtime-collector-requirements.md](collector-design/realtime-collector-requirements.md).
Validate `RecordedAtTime` population in the pilot; the separate
[post-Stage-02 lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is additional design evidence, not a Stage 02 blocker. Stage 02 does not
approve a production retry policy or new polling schedule.

## Stage 8 — Small Realtime Pilot
Start with one station. Validate every selected field end-to-end: raw response
→ parser → persistence, including `RecordedAtTime` population and call/bay/
situation scope. Expand only after correctness is proven.

## Stage 9 — Matching & Data Quality
Validate stop, route, service-date, trip/event identity, candidate richer
identifiers, static coverage, field coverage, and ambiguous cases using
known-good populations.

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

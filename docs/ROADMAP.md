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
from supplied host evidence, and lifecycle validation is **IN PROGRESS** after
one recorded Köln Neumarkt sample. The [GTFS.de static identity bridge](source-contracts/gtfsde-static-identity-bridge-audit.md)
remains partial because railway Köln Hbf is unresolved. It does not reopen
Stage 02 or start Stage 3.

## Stage 3 — Static GTFS Profile — NEXT / NOT STARTED
Resolve the authoritative GTFS feed and its coverage/update policy; this is
deferred from Stage 02.
Re-profile raw files, counts, duplicates, missing values, date coverage, stop hierarchy, routes, agencies, and modes from the current authoritative feed.

## Stage 4 — Cologne Scope & Mode Classification
Define and validate the technical rule for “inside Cologne,” distinguish route identity from in-city service/event scope, and approve the mode classification.

## Stage 5 — Database Architecture
Approve grain, schemas/layers, source-faithful vs derived data, keys, collation, history strategy, indexes, and audit strategy before building.

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

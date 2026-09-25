# Restart Roadmap

Every stage follows the same control pattern:

**Goal → Current Evidence → Questions/Assumptions → Options → Risks → Tests → Decision → Approval Gate**

No stage is considered authoritative merely because a similar solution existed in the legacy repository.

## Stage 0 — Project Definition — APPROVED
Define business questions, portfolio goal, geography, modes, System Performance, audience, and explicit exclusions.

## Stage 1 — Source Inventory & Permission
Re-establish GTFS and MDD source ownership, URLs/contracts, feed coverage, update frequency, API quota, permission/retention conditions, TRIAS version, endpoint contract, and current legacy quota consumption. **No new polling is required to perform the initial quota audit.**

## Stage 2 — MDD/TRIAS Source-Contract Audit
Before realtime schema design, compare official TRIAS semantics, actual MDD raw responses, parser candidates, and persistence requirements. Priority fields include `RecordedAtTime`, estimate ranges, errors/no-realtime evidence, cancellation/status fields, OperatingDayRef, StopSeqNumber, origin/destination, route/via, call structure, NumberOfResults, TimeWindow, and provider limits. Decide raw-payload retention here.

## Stage 3 — Static GTFS Profile
Re-profile raw files, counts, duplicates, missing values, date coverage, stop hierarchy, routes, agencies, and modes from the current authoritative feed.

## Stage 4 — Cologne Scope & Mode Classification
Define and validate the technical rule for “inside Cologne,” distinguish route identity from in-city service/event scope, and approve the mode classification.

## Stage 5 — Database Architecture
Approve grain, schemas/layers, source-faithful vs derived data, keys, collation, history strategy, indexes, and audit strategy before building.

## Stage 6 — Static Warehouse & Analytics
Build static dimensions/facts/views incrementally and reconcile source-to-warehouse counts.

## Stage 7 — Realtime Pilot Design
Approve target rationale, request contract, NumberOfResults, TimeWindow if supported, retry/timeout, quota budget, raw retention, and audit.

## Stage 8 — Small Realtime Pilot
Start with one station. Validate every selected field end-to-end: raw response → parser → persistence. Expand only after correctness is proven.

## Stage 9 — Matching & Data Quality
Validate stop, route, service-date, trip/event identity, candidate richer identifiers, static coverage, and ambiguous cases using known-good populations.

## Stage 10 — Operational Fact
Approve fact grain, consolidation semantics, first/latest/actual timing semantics, NULL handling, platform evidence, situation evidence, and lineage.

## Stage 11 — Power BI Model
Build only after a stable semantic layer. Review relationships, DAX, thresholds, terminology, executive reporting, drill-down, and data-quality context.

## Stage 12 — 50-Station Selection
Re-evaluate and approve a defensible fixed monitoring panel and versioning policy.

## Stage 13 — Tiered Collector Readiness
Audit cadence, NumberOfResults coverage, event density, execution duration, retries, quota, phase staggering, concurrency, and overlap protection before activation.

## Stage 14 — Controlled 50-Station Cutover
Preserve legacy history and IDs, add only approved targets, record sampling-era change, dry-run first, and do not fabricate missed snapshots.

## Stage 15 — Post-Cutover Validation
Monitor from day one and define when the new sampling era has enough data for broader interpretation.

## Stage 16 — Revisit Timing/Data Coverage Issues
Re-analyse unavailable timing and static-coverage gaps using the richer source contract and new data rather than guesses.

## Stage 17 — Actual Physical Delay Productionization
Productionize Actual Delay only if Stage 2/8 evidence proves a valid recorded/actual timing source and later matching/fact semantics support it. Stage 0 sets Actual Delay as the priority objective; this stage is where it becomes a production KPI after evidence is sufficient.

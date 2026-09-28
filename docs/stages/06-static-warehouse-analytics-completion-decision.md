# Stage 6 — Static Warehouse & Analytics Completion Decision

**Date:** 2026-09-28
**Decision:** [D-030](../DECISION-LOG.md#d-030--stage-6-static-warehouse--analytics-design)
**Final status:** COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED
**Next stage:** Stage 7 — NOT STARTED / NEXT

This closeout classifies the Stage 6 static warehouse and analytics contract.
The [Stage 6 design document](06-static-warehouse-analytics-design.md) is the
authoritative detailed contract. Stage 5 remains the authoritative logical
architecture and table/grain handoff.

## VALIDATED SOURCE FACT

- The current Stage 4 authority is the VRS Global-ID snapshot with feed
  version `VERSION__20260928_0042`, validity `2026-09-02` through
  `2026-12-12`, retrieval timestamp `2026-09-28T13:44:03Z`, and ZIP SHA-256
  `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6`.
- The complete source baseline contains 33 `agency.txt` rows, 957 `routes.txt`
  rows, 30,364 `stops.txt` rows, 126,160 `trips.txt` rows, 2,943,746
  `stop_times.txt` rows, 4,616 `calendar.txt` rows, 124,061
  `calendar_dates.txt` rows, 0 `frequencies.txt` rows, 1,167,322
  `shapes.txt` rows, and 1,691 `transfers.txt` rows. The source also records
  9,903 parent-station rows and 20,461 physical stop-position rows within
  `stops.txt`.
- `feed_info.txt` metadata identifies publisher `VRS`, feed version
  `VERSION__20260928_0042`, and validity `2026-09-02` through `2026-12-12`.
  Stage 4 does not expose a comparable row count for that file, so no such
  assertion is made.
- The Stage 4 Cologne rule is the actual source `stop_id` prefix
  `de:05315:`. Parent stations do not override the actual stop ID referenced
  by `stop_times.txt`.
- Current Cologne baselines are 3,137 stop IDs, 1,144,295 scheduled stop
  events, 1,082,490 internal segments, 61,805 Cologne-serving trip patterns,
  39,566 Cologne-internal trips, 22,239 cross-boundary trips, 1,606
  touch-only diagnostics, and 137 Cologne-serving routes.
- The current mode evidence reconciles all 1,144,295 Cologne events and all
  1,082,490 internal segments, with zero unresolved Cologne scheduled events.
- These are validated source baselines and future implementation targets. No
  statement above claims that a PostgreSQL warehouse currently contains them.

## APPROVED DESIGN DECISION

- Stage 6 uses the Stage 5 logical layers `ctl`, `src`, `wrk`, `core`, `dw`,
  and `analytics` without redesigning Stage 5.
- `src` retains the complete approved VRS snapshot. The Cologne-focused
  `dw` boundary is applied only through validated, rebuildable transformations.
- `dw.dim_date` is one calendar date; `dw.dim_agency` is one validated source
  agency identity per approved snapshot; `dw.dim_mode` is one approved
  analytical mode; `dw.dim_stop` is one validated VRS stop identity per
  snapshot with Stage 4 hierarchy/scope; `dw.dim_route` is one validated VRS
  route identity per snapshot; and `dw.dim_service` is one `service_id` per
  snapshot.
- `dw.bridge_service_date` is exactly one active Service + Date pair. It owns
  `calendar.txt` weekday/range expansion and `calendar_dates.txt` add/remove
  exceptions.
- `dw.fact_scheduled_trip` is one Cologne-serving scheduled trip pattern, not
  one row per active date. It retains `COLOGNE_INTERNAL` or `CROSS_BOUNDARY`
  scope and a separate Stage 4 touch-only diagnostic status.
- `dw.fact_scheduled_stop_event` is one validated Cologne stop event in a trip
  pattern. It is also pattern-level, has no active-service `DateKey`, and
  retains exact source time text plus integer seconds from the GTFS service-day
  start.
- GTFS times beyond 24 hours are retained and normalized as service-day
  seconds without wrapping. Invalid times remain explicit source evidence and
  fail validation for the validated warehouse contract.
- The internal-segment derivation is rebuildable from adjacent ordered trip
  events where both endpoint stop IDs are Cologne stops. No physical segment
  fact is introduced.
- The static analytics contract contains network, mode, route, trip, stop
  position, parent station, active date, and daily scheduled trip profiles at
  explicit snapshot-aware grains. These are schedule/network outputs, not
  realtime or actual-performance measures.
- Every future object remains attributable to `FeedSnapshotKey`, source URL,
  retrieval time, ZIP hash, feed version/validity, parser/load lineage, and
  source rows. The same content hash reuses one snapshot identity and a
  different hash creates a new snapshot/version.
- The future physical implementation acceptance matrix covers snapshot
  identity, all explicitly counted source-file populations and `feed_info.txt`
  metadata, Cologne stops/routes/trips/events, internal segments, trip-scope
  populations, mode totals, duplicate safety, service dates, pattern grain,
  date-expansion safety, and extended-hour time safety.

## DEFERRED IMPLEMENTATION DECISION

The user's explicit decision defers all physical PostgreSQL implementation
until later authorization. Stage 6 therefore does not:

- install PostgreSQL or Docker;
- create a database, schema, table, view, function, index, or migration;
- execute DDL, DML, SQL reconciliation, or a data load;
- connect to SQL Server or import legacy data;
- perform a new network request or retrieve another GTFS feed;
- implement MDD, GTFS-RT, collectors, schedulers, or source-event matching;
- implement `core` canonical mappings or the operational fact; or
- modify Power BI.

The future physical implementation order is a handoff contract only. It must
start with the explicitly selected content hash, preserve complete `src`
source rows, build `wrk` validations, build `dw` at the approved grains,
derive static analytics, run the matrix, and pass a further approval gate.

## OPEN IMPLEMENTATION RISK

- Physical parser/load behavior, exact constraints, and measured performance
  remain unimplemented.
- Source duplicates, malformed extended-hour values, and contradictory
  calendar exceptions require explicit future validation outcomes.
- Canonical stop/line identity and cross-source reconciliation remain Stage 9
  risks; they are not prerequisites for the source-first static warehouse.
- Actual/recorded timing remains unvalidated for operational analytics, so
  Actual Delay is not approved.
- A future feed hash/version change requires a new snapshot baseline and a new
  reconciliation run; current numbers must not be reused as timeless truth.

## ACCEPTANCE RESULT AND CLOSEOUT

All 22 Stage 6 design acceptance criteria are **PASS**. The documentation
contract is complete, all static grains and controls are explicit, and the
Stage 4 baseline is clearly separated from future warehouse results.

**FINAL DECISION:** Stage 6 is **COMPLETE — APPROVED DESIGN; PHYSICAL
IMPLEMENTATION DEFERRED**.

Stage 5 remains **COMPLETE — APPROVED ARCHITECTURE**. Stage 7 is **NOT STARTED
/ NEXT**. This closeout does not authorize or begin Stage 7.

No PostgreSQL/database/SQL changes, network requests, collector changes,
scheduler changes, source-event matching, operational-fact work, or Power BI
changes occurred. Stage 7 was not started.

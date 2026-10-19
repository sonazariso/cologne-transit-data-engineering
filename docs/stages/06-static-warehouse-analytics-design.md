# Stage 6 — Static Warehouse & Analytics Design

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Status:** COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED
**Decision:** [D-030](../DECISION-LOG.md#d-030--stage-6-static-warehouse--analytics-design)
**Date:** 2026-09-28
**Authoritative inputs:** [Stage 4 scope and mode classification](04-cologne-scope-mode-classification.md), [Stage 4 current evidence](04-cologne-scope-mode-evidence.json), [Stage 5 database architecture](05-database-architecture.md), and the [Stage 5 table/grain catalog](05-table-grain-catalog.md)

This document completes Stage 6 as a static warehouse and analytics design /
contract stage. It does not create a PostgreSQL database, schema, table,
view, migration, SQL statement, runtime, or data load.

## 1. Purpose

Stage 6 defines the exact static transformation contract from the approved
VRS GTFS source snapshot to a Cologne-focused analytical warehouse and its
first schedule/network analytics outputs. It answers:

1. which retained GTFS rows become Cologne analytical entities;
2. the exact grain of every static dimension, bridge, and fact;
3. how complete source retention becomes Cologne-scoped `dw` content;
4. how Stage 4 geography and mode classification are applied;
5. how route, trip, stop, service, and calendar relationships are represented;
6. how GTFS times beyond `24:00:00` are preserved and normalized;
7. how `calendar.txt` and `calendar_dates.txt` produce active service dates;
8. how feed snapshot and parser lineage remains available;
9. which static analytics outputs are approved; and
10. which numerical controls a future physical implementation must pass.

Static GTFS describes scheduled structure and service-calendar intent. It does
not prove actual operation, actual delay, actual cancellation, operational
reliability, or physical service delivery.

## 2. Explicit user decision and stage boundary

The user explicitly decided to continue the project stages while deferring
PostgreSQL migration/runtime/database implementation until a later explicit
authorization.

Therefore Stage 6 is complete only as a **STATIC WAREHOUSE & ANALYTICS DESIGN /
CONTRACT** stage. The physical implementation is not part of this approval.

### Stage 6 includes

- exact static warehouse scope and inclusion rules;
- dimensions, bridge, and scheduled-fact grains;
- source-to-warehouse mappings and lineage requirements;
- calendar and service-date semantics;
- GTFS extended-hour time normalization semantics;
- Stage 4 Cologne geography and mode application;
- rebuildable `wrk` transformation contracts;
- logical static analytics outputs and grains;
- idempotency and snapshot/version rules;
- future implementation validation and reconciliation controls; and
- a future physical implementation order for later authorization.

### Stage 6 explicitly excludes

- PostgreSQL installation, Docker, a database server, schemas, tables, views,
  functions, indexes, constraints, migrations, DDL, or DML;
- loading or executing GTFS in a database;
- connecting to SQL Server or migrating legacy data;
- network retrieval of a new VRS snapshot;
- MDD, GTFS-RT, realtime collectors, schedulers, or source-event matching;
- `core` canonical identity implementation;
- an operational fact, Actual Delay, or actual on-time KPI;
- Power BI or semantic-model changes; and
- Stage 7 work.

PostgreSQL migration and runtime implementation remain **DEFERRED UNTIL
EXPLICIT USER AUTHORIZATION**.

## 3. Authoritative source snapshot

No new network request is part of Stage 6. The current Stage 4 approval
evidence is the sole static snapshot authority for the design baseline.

| Snapshot attribute | Approved current value |
|---|---|
| Source profile | VRS Global-ID GTFS |
| Source URL | `https://download.vrs.de/gtfs/google_transit_goR.zip` |
| Retrieval timestamp | `2026-09-28T13:44:03Z` |
| Feed version | `VERSION__20260928_0042` |
| Feed validity | `2026-09-02` through `2026-12-12` |
| ZIP SHA-256 | `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6` |
| Freshness classification | `CURRENT_OR_OPERATIONALLY_RELEVANT` |
| Future identity | One `FeedSnapshotKey` for the approved content hash |

The hash, not a retrieval timestamp alone, is the future immutable source
identity. A `FeedSnapshotKey` is a design contract here; no physical key is
created by Stage 6.

### 3.1 Validated source baseline

The following are validated Stage 4 source populations for the selected
snapshot. They are **future implementation reconciliation targets**, not
claims about rows currently present in a warehouse.

| Source population | Current Stage 4 baseline |
|---|---:|
| Agencies | 33 |
| Route rows / distinct `route_id` values | 957 / 957 |
| Stop rows / distinct `stop_id` values | 30,364 / 30,364 |
| Parent-station rows (`location_type=1`) | 9,903 |
| Physical stop-position rows (`location_type=0`) | 20,461 |
| Trip rows / distinct `trip_id` values | 126,160 / 126,160 |
| `stop_times` rows | 2,943,746 |
| `calendar.txt` rows | 4,616 |
| `calendar_dates.txt` rows | 124,061 |
| `frequencies.txt` rows | 0 |
| `shapes.txt` rows | 1,167,322 |
| `transfers.txt` rows | 1,691 |

The current source evidence reports zero missing agency, route, stop, or trip
identifiers, zero unknown `stop_times` stop references, and zero duplicate
route, stop, or trip identifiers. A future load must still validate these
conditions and must not rely on them as universal source guarantees.

`feed_info.txt` is validated through the source metadata in Section 3:
publisher `VRS`, feed version `VERSION__20260928_0042`, and validity
`2026-09-02` through `2026-12-12`. Stage 4 does not expose a comparable
`feed_info.txt` row count, so this contract makes no row-count assertion for
that file.

### 3.2 Validated Cologne source baseline

Stage 4 defines the binding source-identity rule:

```text
validated Cologne stop_id starts with: de:05315:
```

The current approved snapshot contains these Cologne-scope baselines:

| Cologne population | Current Stage 4 baseline |
|---|---:|
| Cologne stop rows / distinct Cologne stop IDs | 3,137 / 3,137 |
| Cologne parent-station rows | 864 |
| Cologne physical stop-position rows | 2,273 |
| Cologne scheduled stop events | 1,144,295 |
| Cologne-internal consecutive segments | 1,082,490 |
| Cologne-serving trips | 61,805 |
| Cologne-internal trips | 39,566 |
| Cross-boundary Cologne trips | 22,239 |
| Cross-boundary trips with an internal Cologne segment | 20,633 |
| Touch-only Cologne trips | 1,606 |
| Cologne-serving routes | 137 |
| Unresolved stop-scope trips | 0 |
| Unclassified Cologne scheduled stop events | 0 |

The formal `cross-boundary` and `touch-only` populations are not all mutually
exclusive. The current mutually exclusive trip buckets reconcile as:

| Exclusive trip bucket | Current Stage 4 baseline |
|---|---:|
| Cologne-internal with an internal segment | 39,566 |
| Cross-boundary with an internal segment | 20,633 |
| Cross-boundary touch-only | 1,606 |
| **Total Cologne-serving trip patterns** | **61,805** |

These values are source evidence and future controls. The statement “the
warehouse contains” must not be used for them until a separately authorized
physical implementation has loaded and passed the controls below.

### 3.3 Current mode evidence baseline

Stage 6 uses only the Stage 4 classifier. The following values are copied from
the current Stage 4 evidence by mode and remain source baselines.

| Analytical mode | Scope status | Source route rows | Cologne events | Internal segments | Serving trips | Serving routes | Cross-boundary trips | Touch-only trips |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Stadtbahn / Tram | `CORE_KPI` | 18 | 507,354 | 481,918 | 25,436 | 12 | 9,435 | 102 |
| Urban / KVB Bus | `CORE_KPI` | 58 | 580,028 | 554,111 | 25,917 | 58 | 2,916 | 0 |
| S-Bahn | `CORE_KPI` | 6 | 26,345 | 23,802 | 2,543 | 4 | 2,228 | 58 |
| Regional Express (RE) | `CORE_KPI` | 11 | 4,650 | 3,190 | 1,460 | 8 | 1,456 | 112 |
| Regional Bahn (RB) | `CORE_KPI` | 14 | 6,061 | 4,519 | 1,542 | 7 | 1,542 | 66 |
| Regional / Other Bus | `CORE_KPI` | 849 | 19,857 | 14,950 | 4,907 | 48 | 4,662 | 1,268 |
| Rail Replacement Bus (SEV) | `SEPARATE_ANALYTICAL` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Long-distance | `OUTSIDE_CORE` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Other Rail | `OUTSIDE_CORE` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `OTHER_MODE` | `OUTSIDE_CORE` | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Explicit unresolved/unclassified buckets | `UNCLASSIFIED` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

The mode event buckets reconcile to 1,144,295 Cologne scheduled stop events
and the mode segment buckets reconcile to 1,082,490 internal segments. Mode
classification never expands geographical scope.

## 4. Source-to-warehouse boundary

The approved transformation boundary is:

```text
FULL VRS STATIC SOURCE IN src
            ↓
validated static transformations in wrk
            ↓
STAGE 4 COLOGNE SCOPE AND MODE CLASSIFICATION
            ↓
STATIC COLOGNE WAREHOUSE IN dw
            ↓
STATIC SCHEDULE/NETWORK ANALYTICS
```

### 4.1 Complete source retention

`src` retains the complete approved VRS snapshot, including non-Cologne stops,
routes, trips, and stop events. External portions are necessary context for
cross-boundary trips and must remain source-faithful. Source rows preserve
`FeedSnapshotKey`, source row lineage, exact source text, source IDs, and
unknown/NULL semantics.

The `src` layer is not filtered to Cologne and is not replaced by `dw`.
Duplicate source rows or source identifiers remain visible in `src` for
validation. `wrk` is rebuildable and cannot be the only home of irreplaceable
source evidence.

### 4.2 Cologne-focused analytical warehouse

The Stage 6 `dw` contract is Cologne-focused:

- `dw.dim_stop` contains validated Cologne source stop identities and their
  hierarchy classifications. Parent stations and physical stop positions are
  distinct rows where source `location_type` distinguishes them.
- `dw.dim_route` contains validated route identities attached to at least one
  Cologne-serving trip pattern and retains their source classification and
  core/separate/outside-core status. The current route baseline is 137.
- `dw.dim_service` contains service identities used by Cologne-serving trip
  patterns, with calendar lineage but without date expansion.
- `dw.fact_scheduled_trip` contains Cologne-serving trip patterns, including
  cross-boundary patterns where they have at least one validated Cologne stop
  event.
- `dw.fact_scheduled_stop_event` contains only validated Cologne scheduled
  stop events. External stop events remain in `src` and may be used in
  `wrk` to classify the trip and derive internal segments.
- `dw.bridge_service_date` contains active dates for the service identities
  represented by the static warehouse.

An external stop is never made Cologne-scoped because a route or trip enters
Cologne. An in-scope stop event is not an in-scope full trip, and a
Cologne-serving route is not a wholly Cologne route.

## 5. Exact static warehouse grains

The following contracts finalize the Stage 5 logical entities. They are not
physical table approvals.

| Logical entity | Exact grain | Stage 6 inclusion and required lineage |
|---|---|---|
| `dw.dim_date` | One calendar date | Cover the approved service/calendar range, currently `2026-09-02` through `2026-12-12`. Retain `DateValue`, year, quarter, month, month number, ISO week where used, day-of-week, day-of-week number, and weekend flag. No unapproved holiday semantics. |
| `dw.dim_agency` | One validated analytical source agency identity for one approved feed snapshot/version | Retain `FeedSnapshotKey`, source `agency_id`, source agency name, and source-row lineage for agencies participating in the Cologne warehouse. Agency identity is not geographical scope. |
| `dw.dim_mode` | One approved analytical mode | Govern the Stage 4 mode labels: six core KPI modes, separate `Rail Replacement Bus (SEV)`, outside-core long-distance/Other Rail/`OTHER_MODE`, and explicit unresolved/unclassified statuses. Do not create a route row per mode here or let mode expand geography. |
| `dw.dim_stop` | One validated VRS static GTFS stop identity for one approved feed snapshot, with Stage 4 scope and hierarchy classification | Retain `FeedSnapshotKey`, source `stop_id`, source row lineage, stop name, `location_type`, `parent_station`, source coordinates where supplied, and Cologne classification. Parent station, physical stop position/child, and other validated source location types remain distinguishable. |
| `dw.dim_route` | One validated VRS static GTFS route identity for one approved feed snapshot | Retain `FeedSnapshotKey`, source `route_id`, source row lineage, agency, route short/long names, `route_type`, Stage 4 analytical mode, classification evidence, and analytical status. Do not require `core.canonical_line`. |
| `dw.dim_service` | One validated GTFS `service_id` identity for one approved source snapshot | Retain `FeedSnapshotKey`, source `service_id`, and `calendar.txt` / `calendar_dates.txt` lineage. Do not expand active dates in this dimension. |
| `dw.bridge_service_date` | One active `Service + Date` pair for one approved feed snapshot | Materialize the authoritative active-date expansion after applying base calendar and exceptions. At this grain, duplicate service/date pairs are invalid. |
| `dw.fact_scheduled_trip` | One validated Cologne-serving scheduled GTFS trip pattern for one approved feed snapshot | Not one row per service date. Retain `FeedSnapshotKey`, source `trip_id`, route/service/mode keys, direction and shape where present, source lineage, `TripScopeClass` (`COLOGNE_INTERNAL` or `CROSS_BOUNDARY`), and an independent touch-only diagnostic flag/status. |
| `dw.fact_scheduled_stop_event` | One validated Cologne scheduled stop event in one GTFS trip pattern for one approved source snapshot | Pattern-level only. Retain `FeedSnapshotKey`, scheduled-trip key, source `trip_id`, stop/route/mode/service keys, `stop_sequence`, exact source arrival/departure text, normalized seconds, and pickup/drop-off/timepoint/source fields where present. No active service `DateKey`, realtime field, or actual-delay field. |

### 5.1 Stage 9 and later entities are not prerequisites

Stage 6 is source-first. It does not require `core.canonical_stop`,
`core.canonical_line`, `core.canonical_service_event`, or
`core.source_event_match`. Canonical identity and cross-source matching remain
Stage 9 work. Stage 6 creates no `core` objects and no realtime operational
fact; the latter remains a Stage 10 concern.

## 6. Working-layer static transformation contract

The future `wrk` layer may contain rebuildable transformations equivalent to:

1. source snapshot identity and source-row integrity validation;
2. validated Cologne stops using the actual `stop_id` prefix rule;
3. validated Cologne-serving routes and trips;
4. validated Cologne scheduled stop events;
5. Stage 4 mode assignment and explicit unresolved status;
6. GTFS arrival/departure time parsing and validation;
7. service-date activation from `calendar.txt` and `calendar_dates.txt`; and
8. adjacent-event internal-segment derivation.

These transformations must be reproducible from `src`. They may be views,
functions, or other rebuildable implementation objects in a later authorized
implementation, but Stage 6 creates none of them physically.

## 7. GTFS time normalization contract

GTFS schedule time is a service-day offset, not a PostgreSQL `time` value.
GTFS source text is retained exactly in `src` and in the validated event
contract. It must not be cast directly to a civil-day SQL time.

### 7.1 Valid parsing

The future parser accepts a non-negative hour component and exactly two-digit
minute and second components, conceptually:

```text
^([0-9]+):([0-5][0-9]):([0-5][0-9])$
```

Normalized seconds are:

```text
seconds_from_service_day_start = (hours * 3600) + (minutes * 60) + seconds
```

The source service date remains the GTFS service date even when the value is
over 24 hours. Examples:

| Source text | Normalized seconds | Interpretation |
|---|---:|---|
| `23:30:00` | 84,600 | Same service-day offset before midnight |
| `24:15:00` | 87,300 | Next civil-day clock time, still the same GTFS service day |
| `25:10:30` | 90,630 | Extended-hour service time; retain the 25-hour offset |

The implementation must not modulo or wrap `25:10:30` to `01:10:30` without
retaining its service-day offset. If a civil-clock representation is later
derived, its day offset must remain available.

### 7.2 Invalid and missing values

Malformed values, out-of-range minutes/seconds, negative values, and values
that cannot be validated must not be silently coerced. The exact source text
remains in `src`, the validation outcome is recorded, and the row is not a
validated warehouse event until an explicit source-quality decision allows it.
Missing arrival/departure values remain NULL/source evidence; they are not
converted to zero or inferred from the other event time.

No timezone is invented for GTFS schedule times. They are schedule offsets
anchored to the GTFS service date.

## 8. Calendar and service-date semantics

`dw.dim_service` represents service identity. `dw.bridge_service_date`
represents the dates on which that service is active. The bridge is not a
scheduled trip or scheduled stop-event pattern.

### 8.1 Base `calendar.txt` activation

For each `service_id` in `calendar.txt`, the future derivation considers each
date inclusively from `start_date` through `end_date` and activates the date
only when the corresponding weekday flag is true:

```text
monday, tuesday, wednesday, thursday, friday, saturday, sunday
```

The date must remain inside the source calendar validity. No holiday or
regional-calendar meaning is inferred beyond the GTFS fields.

### 8.2 `calendar_dates.txt` exceptions

After the base calendar set is derived:

- `exception_type = 1` adds service on the listed date;
- `exception_type = 2` removes service on the listed date.

Service IDs present only in `calendar_dates.txt` are supported: they begin
with an empty base set and receive only the explicit additions permitted by
the exception rows. A removal does not create service by itself.

Source exception rows, including duplicates or contradictory rows, remain
retained for validation. A future load must not silently deduplicate them. A
conflicting or otherwise ambiguous source condition must be reported and
resolved by an explicit implementation policy before it can produce a
validated bridge row. The derived bridge itself must contain at most one row
for each active `FeedSnapshotKey + ServiceKey + DateKey`.

Repeated derivation from the same source snapshot and parser version must be
deterministic. No active date may be inferred outside the explicit base
calendar and exception rules.

### 8.3 Pattern facts versus date expansion

The approved relationship is:

```text
FactScheduledTrip (trip pattern)
    -> DimService
    -> BridgeServiceDate (active Service + Date)
    -> DimDate
```

`FactScheduledStopEvent` is also a pattern fact. Stage 6 must not create a
copy of every trip or stop event for every active date. The fact's `DateKey`
is absent by contract. A future daily schedule output may join to
`BridgeServiceDate` at query/derived-output grain, but it must not change the
base fact grain.

## 9. Geography, route/trip treatment, and cross-boundary behavior

The Stage 4 semantics are binding and are not redesigned here:

- A Cologne stop is a source stop whose actual `stop_id` starts with
  `de:05315:` and exists in `stops.txt`.
- A Cologne scheduled stop event is a `stop_times.txt` row whose referenced
  actual `stop_id` is a validated Cologne stop.
- A Cologne internal segment is a pair of adjacent ordered stop events of one
  trip where both endpoint stop IDs are validated Cologne stops.
- A Cologne-serving trip has at least one Cologne scheduled stop event.
- A Cologne-serving route has at least one Cologne-serving trip.

The route/trip serving labels are discovery and identity labels only. They do
not make all route/trip stops geographical Cologne scope. `parent_station`
does not override the actual stop ID referenced by `stop_times.txt`.

For a cross-boundary trip, such as a service that connects Cologne and Bonn:

1. the complete trip pattern and all external source stop events remain in
   `src`;
2. the Cologne-serving trip pattern may exist in `dw.fact_scheduled_trip`;
3. only validated Cologne stop events enter
   `dw.fact_scheduled_stop_event`;
4. only adjacent pairs with two Cologne endpoints qualify for the rebuildable
   internal-segment derivation; and
5. external events do not silently become Cologne KPI events.

`COLOGNE_INTERNAL` and `CROSS_BOUNDARY` are the explicit trip-scope classes.
The Stage 4 touch-only population remains a separate diagnostic flag/status;
it is not forced into a new mutually exclusive geography rule.

No physical segment fact is created in Stage 6. A future `wrk` or analytics
derivation orders stop events by trip and `stop_sequence`, pairs adjacent
events, checks both endpoint scope flags, and produces the current Stage 4
baseline target of 1,082,490 internal segments.

## 10. Mode application

Stage 4 is the sole mode classifier. The future implementation must retain
source `route_type`, route names, `agency_id`, agency name, and classification
evidence while deriving the analytical label. It must not classify from a
route label alone where the Stage 4 precedence requires route type, agency,
or bounded evidence.

The approved precedence is:

1. ambiguous route/trip identity remains explicit and unclassified;
2. `route_type=0` is `Stadtbahn / Tram` for this feed profile;
3. `route_type=2` is rail, with long-distance checked first, then S-Bahn, RE,
   RB, and Other Rail;
4. `route_type=3` is bus, with SEV checked first, then validated KVB agency
   for `Urban / KVB Bus`, otherwise `Regional / Other Bus`; and
5. unsupported route types remain `OTHER_MODE`, while unresolved inheritance
   remains explicit `UNCLASSIFIED`.

Every validated Cologne scheduled stop event must reconcile to exactly one
analytical mode bucket or an explicit unresolved/ambiguous bucket. The current
Stage 4 unresolved event baseline is zero. Mode status (`CORE_KPI`,
`SEPARATE_ANALYTICAL`, `OUTSIDE_CORE`, or `UNCLASSIFIED`) remains distinct
from geographical scope.

## 11. Static analytics contract

The following are logical analytical outputs. They are not physical database
views or materializations created by Stage 6.

| Logical output | Exact grain | Approved analytical intent |
|---|---|---|
| Network Baseline KPI | One approved `FeedSnapshotKey` and Cologne analytical scope | Report snapshot identity, validity, source counts, Cologne stop/event/trip/route populations, internal segments, trip-scope populations, and data-quality/control status. It is a scheduled network baseline, not an operational KPI. |
| Mode Schedule Profile | One analytical mode per approved feed snapshot | Summarize scheduled Cologne events, internal segments, serving trip patterns, serving routes, cross-boundary and touch-only diagnostics, and mode status. Include separate/outside/unresolved categories where relevant. |
| Route Schedule Profile | One Cologne-serving route identity per approved feed snapshot | Summarize route source identity, agency, mode, scheduled event volume, trip-pattern count, internal-segment volume, and cross-boundary/touch-only diagnostics. Do not describe the route as wholly inside Cologne. |
| Scheduled Trip Profile | One Cologne-serving trip pattern per approved feed snapshot | Show route, service, mode, direction/shape lineage, trip-scope class, touch-only status, Cologne event count, and internal-segment count. It is not date-expanded and not an operational outcome. |
| Stop Position Schedule Profile | One validated Cologne physical stop position per approved feed snapshot | Summarize scheduled stop-event and trip-pattern coverage by `location_type=0` stop position, with explicit parent-station reference where present. |
| Parent Station Schedule Profile | One validated Cologne parent station per approved feed snapshot | Summarize the parent station and its validated child/position relationships without merging child identities or overriding event stop IDs. |
| Active Date Profile | One active calendar date per approved feed snapshot | Summarize active service identities and schedule coverage derived through `BridgeServiceDate` and `DimDate`. It represents the service calendar, not observed service delivery. |
| Daily Scheduled Trip Profile | One `Date + Route` pair per approved feed snapshot | Join trip patterns to active service dates through `BridgeServiceDate` and summarize scheduled trip-pattern volume by date and Cologne-serving route. Date expansion belongs to this derived output only. |

Permitted terminology includes **scheduled**, **planned**, **service calendar**,
**schedule coverage**, **scheduled stop events**, **scheduled trips**, and
**scheduled service volume**. The outputs must not be named or interpreted as
actual delay, actual on-time performance, actual cancellations, operational
reliability, or actual physical service delivery.

## 12. Snapshot lineage and idempotency

Every future static dimension, bridge, fact, and analytics output must be
traceable to `FeedSnapshotKey`, and through it to:

- source URL;
- retrieval timestamp;
- ZIP SHA-256;
- feed version and validity dates;
- parser/load version;
- source-row lineage; and
- validation outcomes.

The implementation contract is:

- the same approved content hash reuses one logical source snapshot identity;
- a repeated retrieval remains audit evidence but does not create a second
  parsed source snapshot;
- a different approved content hash creates a new snapshot/version;
- rebuilding the same snapshot produces the same logical result;
- historical source evidence is never overwritten; and
- no duplicate warehouse rows are allowed at the documented grains.

The physical implementation must make duplicate execution distinguishable
from a genuinely different source snapshot. No timeless warehouse row may
lose its source snapshot attribution.

## 13. Future physical implementation acceptance matrix

The following checks are required of a later authorized physical
implementation. They are not executed by this Stage 6 design approval.

| ID | Future check | Current approved baseline / required result |
|---|---|---|
| A | Source snapshot identity | Loaded ZIP SHA-256 equals the explicitly selected Stage 4 hash `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`; the feed version and validity also match. |
| B | Source rows | All parsed source-file populations with explicit Stage 4 baseline counts reconcile to the selected evidence: 33 `agency.txt` rows / distinct agency IDs, 957 `routes.txt` rows / distinct route IDs, 30,364 `stops.txt` rows / distinct stop IDs including 9,903 parent-station rows and 20,461 physical stop-position rows, 126,160 `trips.txt` rows / distinct trip IDs, 2,943,746 `stop_times.txt` rows, 4,616 `calendar.txt` rows, 124,061 `calendar_dates.txt` rows, 0 `frequencies.txt` rows, 1,167,322 `shapes.txt` rows, and 1,691 `transfers.txt` rows, subject to preserved source-row lineage. `feed_info.txt` metadata must preserve and validate publisher `VRS`, feed version `VERSION__20260928_0042`, and validity `2026-09-02` through `2026-12-12`; no `feed_info.txt` row-count assertion is made because Stage 4 evidence does not expose one in the same manner. |
| C | Cologne stop scope | Validated `stop_id` prefix rule produces 3,137 Cologne stop IDs / rows, including 2,273 positions and 864 parent stations; actual stop IDs referenced by events control scope. |
| D | Cologne-serving route population | Exactly 137 Cologne-serving route identities under the Stage 4 rule. |
| E | Cologne-serving trip patterns | Exactly 61,805 Cologne-serving trip patterns, with no unknown trip-row or unresolved stop-scope trip in the approved baseline. |
| F | Cologne scheduled stop-event patterns | Exactly 1,144,295 validated Cologne scheduled stop-event pattern rows. |
| G | Internal segments | Rebuildable adjacent-event derivation produces exactly 1,082,490 Cologne-internal segments for the approved snapshot. |
| H | Trip-scope populations | Reconcile 39,566 Cologne-internal trips, 22,239 cross-boundary trips, 1,606 touch-only diagnostics, 20,633 cross-boundary trips with an internal segment, and the mutually exclusive total of 61,805; preserve overlap semantics. |
| I | Mode reconciliation | Sum of classified Cologne event buckets equals 1,144,295 plus any explicit unresolved/ambiguous event bucket. Current unresolved/ambiguous event count is zero. Mode segment buckets equal 1,082,490. |
| J | Duplicate safety | Duplicate source IDs or rows are not silently deduplicated. The current evidence has zero duplicate route/stop/trip IDs, but a future duplicate must remain visible and produce a documented validation outcome. |
| K | Service dates | `BridgeServiceDate` has zero duplicate `FeedSnapshotKey + ServiceKey + DateKey` rows; repeated derivation is deterministic and applies only the documented calendar rules. |
| L | Stop-event fact grain | Zero duplicate `FeedSnapshotKey + trip pattern + stop_sequence` candidates at the validated intended grain, subject to explicit source-quality handling; source duplicates remain visible in `src`. |
| M | Date expansion safety | `FactScheduledStopEvent` row count does not multiply when a service has multiple active dates. Date-expanded reporting uses `BridgeServiceDate` in a separate query/derived output. |
| N | Extended-hour time safety | Valid source times normalize to integer service-day seconds without wrapping values over 24:00; malformed values fail validation or remain explicit source evidence. |

Any failed control blocks approval of the physical load for that snapshot. A
future implementation must record measured checks through the approved `ctl`
lineage rather than presenting unverified counts as warehouse results.

## 14. Limitations and open implementation risks

- The current values are static-source baselines, not a physical warehouse
  result and not a claim of current operational service.
- The design depends on the approved VRS source identity and does not create a
  cross-provider canonical stop/line bridge.
- The complete source is retained for context, but external portions of
  cross-boundary services are not Cologne KPI events.
- No holiday semantics beyond GTFS calendar fields are approved.
- Source duplicates, malformed times, contradictory exceptions, and future
  feed changes require measured implementation validation.
- Stage 9 still owns canonical identity and source-event matching; Stage 6
  must not invent matching logic.
- Stage 10 still owns operational outcome consolidation, actual-time semantics,
  and any future Actual Delay fact.
- Stage 7 remains a separate realtime design stage and has not started.

## 15. Future implementation handoff and order

Only after explicit user authorization, a future implementation may proceed in
this order:

1. select and verify the approved artifact by content hash;
2. register the immutable source snapshot and ingestion/parser lineage in
   `ctl`;
3. parse the complete VRS files into source-faithful `src` rows;
4. validate source integrity, hierarchy, extended-hour times, calendar rules,
   Stage 4 scope, and Stage 4 mode classification in rebuildable `wrk` logic;
5. build the Stage 6 dimensions, service-date bridge, and pattern-level
   scheduled facts at the grains in this document;
6. derive the logical static analytics outputs without changing base fact
   grains;
7. run the full acceptance matrix, persist validation outcomes, and compare
   to the Stage 4 source baselines; and
8. obtain the next explicit approval before any realtime, canonical matching,
   operational fact, or Power BI work.

This sequence is a handoff contract, not authorization to execute it now.

## 16. Stage 6 acceptance criteria

| # | Criterion | Result |
|---:|---|---|
| 1 | Stage 6 is explicitly a design/contract stage under the user's deferment. | PASS |
| 2 | No physical database implementation is claimed. | PASS |
| 3 | All static warehouse entity grains are explicit. | PASS |
| 4 | Stage 6 does not depend on Stage 9 `core` objects. | PASS |
| 5 | `FactScheduledTrip` is pattern-level. | PASS |
| 6 | `FactScheduledStopEvent` is pattern-level. | PASS |
| 7 | `BridgeServiceDate` owns Service + Date expansion. | PASS |
| 8 | GTFS values greater than `24:00:00` have explicit normalization semantics. | PASS |
| 9 | `calendar.txt` and `calendar_dates.txt` activation semantics are explicit. | PASS |
| 10 | Stage 4 Cologne scope is preserved exactly. | PASS |
| 11 | Cross-boundary treatment is explicit. | PASS |
| 12 | Stage 4 mode classification remains authoritative. | PASS |
| 13 | Static analytics grains are explicit. | PASS |
| 14 | Stage 4 counts are treated as source baselines, not warehouse results. | PASS |
| 15 | Future source-to-warehouse reconciliation checks are fully specified. | PASS |
| 16 | Snapshot lineage and idempotency are explicit. | PASS |
| 17 | Static GTFS is not described as realtime or actual evidence. | PASS |
| 18 | PostgreSQL/migration/runtime implementation is explicitly deferred. | PASS |
| 19 | Stage 7 is not started. | PASS |
| 20 | No network, database, SQL, collector, scheduler, or Power BI action occurred. | PASS |
| 21 | Authoritative documentation and status files are synchronized. | PASS |
| 22 | `git diff --check` passes. | PASS |

## 17. Final Stage 6 decision

**FINAL DECISION:** Stage 6 is **COMPLETE — APPROVED DESIGN; PHYSICAL
IMPLEMENTATION DEFERRED**.

This completion means the static warehouse and analytics design/contract is
complete. Physical PostgreSQL implementation has **NOT** occurred. No database,
schema, table, migration, SQL execution, source load, network request,
collector, scheduler, matching, operational fact, or Power BI change is
authorized by this document.

Stage 5 remains **COMPLETE — APPROVED ARCHITECTURE**. Stage 7 is
**NOT STARTED / NEXT** and was not started by this closeout.

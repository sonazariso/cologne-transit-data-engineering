# Stage 5 — Database Architecture Completion Decision

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Date:** 2026-09-28
**Decision:** [D-029](../DECISION-LOG.md#d-029--stage-5-database-architecture)
**Final status:** COMPLETE — APPROVED ARCHITECTURE
**Stage 6 handoff:** Static warehouse/analytics design and contract
**Current next stage:** Stage 7 — NOT STARTED / NEXT

This closeout classifies the Stage 5 conclusions and records the boundary
between approved architecture and deferred implementation. The companion
[architecture document](05-database-architecture.md) and
[table/grain catalog](05-table-grain-catalog.md) are authoritative together
with this decision.

## APPROVED ARCHITECTURE DECISION

1. PostgreSQL is the future authoritative database platform. The target major
   direction is PostgreSQL 18, using the latest supported PostgreSQL 18 minor
   available at implementation time. No future minor Docker tag is hardcoded.
2. The migration strategy is a **CLEAN POSTGRESQL REDESIGN**, not a 1:1 SQL
   Server lift-and-shift. Legacy SQL Server remains implementation evidence.
3. The approved logical layers are `ctl`, `src`, `wrk`, `core`, `dw`, and
   `analytics`, flowing from source retrieval and durable source evidence to
   validated and business-facing derived objects.
4. `src` is durable source-faithful evidence. Observational rows are
   append-only; source duplicates, NULLs, source timestamps, source IDs, and
   later disappearance remain interpretable.
5. The complete promoted VRS GTFS snapshot is retained in `src` by immutable
   content identity. Repeated retrieval of the same ZIP/hash creates audit
   evidence but not duplicate parsed snapshot history. Raw ZIP artifacts remain
   externally reproducible and are not stored as database BLOBs.
6. MDD is modelled as immutable response, stop-event, call, Situation,
   Situation-link, and service-attribute observations. Repeated estimates and
   Situation evolution are retained; no mutable current journey row is the
   source of truth.
7. GTFS-RT remains complementary. Feed, TripUpdate, StopTimeUpdate, alert,
   and alert-entity structures are reserved for later approved in-scope
   persistence. Permanent full nationwide snapshots and a VehiclePosition
   table are not approved by Stage 5.
8. Internal/validated keys use `bigint` identity direction; source IDs remain
   `text` source columns. Source natural IDs are not universal keys, and source
   duplicates are not silently removed.
9. `timestamptz`, `date`, exact GTFS schedule text, nullable booleans,
   readable lowercase SHA-256, and bounded `jsonb` conventions are approved.
10. `TimetabledTime`, `EstimatedTime`, source-only nullable `RecordedAtTime`,
    and collector `ObservedAt` remain semantically separate. `RecordedAtTime`
    is never synthesized.
11. Static idempotency is content-hash based. Realtime idempotency prevents
    duplicate persistence of the same response/run without deduplicating
    legitimate later observations merely because payload values match.
12. JSONB is limited to bounded provider extensions, rare/unmodeled source
    fragments, and source-shaped auxiliary metadata; it does not replace
    relational fields or hold credentials.
13. Indexing is evidence-driven. Large append-only realtime tables have a
    preferred future direction of monthly range partitioning by `ObservedAtUtc`;
    static/control/dimension tables are not automatically time partitioned.
14. Control/run/validation/raw-artifact and sampling-policy lineage is required.
    Every realtime observation is attributable to source, run, sampling policy
    or era, and observed time.
15. Least-privilege role separation and a tested backup/recovery strategy are
    architectural requirements. Backup/recovery becomes a production-readiness
    gate before realtime collection starts.
16. The [42-entry table/grain catalog](05-table-grain-catalog.md) is the
    approved logical contract. Stage 6 owns the approved static
    warehouse/analytics design-contract entries, not physical objects:
    `DimStop` and `DimRoute` are source-first VRS/static GTFS dimensions and do
    not require Stage 9 `core.canonical_stop` or `core.canonical_line` objects;
    `FactScheduledStopEvent` remains pattern-level, while `BridgeServiceDate`
    carries the active `Service + Date` grain. Stage 9 owns canonical/matching
    entries, including the clarified conceptual grains for
    `CanonicalServiceEvent` and `SourceEventMatch`; Stage 10 owns the
    operational-fact placeholder.

## DEFERRED IMPLEMENTATION DECISION

The following are intentionally not implemented or finalized in Stage 5:

- PostgreSQL installation, Docker execution, database creation, schema/table
  creation, SQL execution, and migrations;
- final primary-key, foreign-key, unique-constraint, and partitioned-table
  syntax;
- future physical static warehouse objects and source-to-warehouse load logic,
  governed by the Stage 6 design contract and deferred until explicit user
  authorization;
- MDD/GTFS-RT collectors, scheduler, sampling activation, and raw-payload
  retention operations;
- canonical identity resolution and source-event matching;
- the operational realtime fact and Actual Delay semantics;
- analytics materializations and Power BI; and
- credentials, roles, grants, backup tooling, and network configuration.

The [Stage 6 design contract](06-static-warehouse-analytics-design.md) and
[completion decision](06-static-warehouse-analytics-completion-decision.md)
are the approved static warehouse/analytics handoff and govern any future
physical implementation. That physical PostgreSQL implementation remains
deferred until explicit user authorization. Stage 7 must not be treated as
started by this documentation approval.

## OPEN IMPLEMENTATION RISK

These risks remain explicit for their owning future stages:

- `RecordedAtTime` population, coverage, and operational suitability remain
  unresolved; Actual Delay is not approved.
- The GTFS-RT static identity bridge remains partial for railway Köln Hbf and
  must not be replaced by an assumed source-ID equivalence.
- Final realtime partition constraints must satisfy PostgreSQL uniqueness and
  foreign-key rules involving the observed-time partition key.
- Static and realtime volume, query selectivity, backup cost, and artifact/raw
  retention require measurement after implementation begins.
- Exact business-grain uniqueness in `core` and `dw` requires validation before
  physical constraints are applied.
- The operational fact must decide how repeated estimates, source-provided
  recorded time, statuses, and Situation evolution are consolidated without
  losing source evidence.

## VALIDATED SOURCE CONSTRAINT

The architecture must continue to honor these validated or approved source
constraints:

- Stage 4's current official VRS Global-ID snapshot is the approved static
  source direction, with the direct Cologne stop rule `stop_id LIKE
  'de:05315:%'`; parent-station relationships do not override the actual stop
  referenced by `stop_times.txt`.
- Cologne KPI geography is stop-event-first; a Cologne-internal segment
  requires both consecutive endpoints to be validated Cologne stops.
- Cross-boundary service evidence outside Cologne remains in the complete
  source snapshot but is outside core Cologne KPI scope.
- MDD/TRIAS is the approved primary realtime source direction and GTFS-RT is
  complementary; their namespaces and source semantics remain distinct.
- `MDD JourneyRef` is not GTFS-RT `trip_id`.
- `RecordedAtTime` was not observed in bounded tested evidence, but that result
  is not a provider-wide absence claim.
- `TimetabledTime`, `EstimatedTime`, `RecordedAtTime`, and `ObservedAt` are
  distinct concepts; estimates are not actuals.
- Provider-reported Situation, cause, and alert evidence is not automatically
  proven root cause.
- Full raw MDD/GTFS-RT payload retention is not the permanent default, and
  collection gaps are not automatic evidence of on-time service, cancellation,
  skipping, or absence.

## Acceptance result and closeout

All 25 Stage 5 acceptance criteria are **PASS**, including documentation
consistency checks and `git diff --check`. At Stage 5 closeout, no database
changes, SQL execution, network requests, collector changes, scheduler
changes, Power BI changes, or Stage 6 work had occurred.

**Final decision:** Stage 5 is **COMPLETE — APPROVED ARCHITECTURE**. Stage 6's
design/contract closeout is complete; its physical implementation remains
deferred until explicit user authorization. Stage 7 is **NOT STARTED / NEXT**.

# Realtime Collection Design Readiness

**Status:** `READY FOR USER REVIEW — STAGE 3 NOT STARTED`
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **NEXT / NOT STARTED**
**Purpose:** entry conditions and guardrails for a future Stage 3 design review

This document consolidates the approved post-Stage-02 source decision. It is a
readiness handoff, not an implementation design. It does not approve
collector code, database objects, schemas, migrations, scheduler jobs, final
table names, reconciliation algorithms, Power BI changes, or production
runtime changes.

## Approved source direction

The future realtime design starts from the static GTFS scheduled baseline and
keeps the realtime sources distinct:

```text
Static GTFS
  scheduled baseline
       |
       +--------------------------+
       |                          |
       v                          v
MDD / TRIAS                  GTFS-RT
PRIMARY REALTIME             COMPLEMENTARY REALTIME
       |                          |
       |                          +-- TripUpdate
       |                          +-- StopTimeUpdate
       |                          +-- SKIPPED / NO_DATA
       |                          +-- ServiceAlerts
       |
       +-- EstimatedTime
       +-- platform/bay evidence
       +-- situation evidence
       +-- PreviousCall / ThisCall / OnwardCall
                    |
                    v
       source-faithful observation layer
                    |
                    v
       future reconciliation /
       canonical analytics layer
                    |
                    v
                 Power BI
```

This is a design direction only. MDD is primary because it aligns with the
Cologne/VRS environment, supports station-targeted requests, and provides
rich call, estimate, platform/bay, and situation evidence. GTFS-RT is
complementary because it adds broad operational coverage, trip and stop
relationships, explicit `SKIPPED`/`NO_DATA` state, `ServiceAlerts`, and
independent cross-source evidence.

Neither source currently provides confirmed Actual Time. MDD
`RecordedAtTime` is the TRIAS recorded/actual candidate but was not observed
in the bounded tested responses or six live lifecycle journeys. GTFS-RT
`StopTimeEvent.time` can mean estimated or actual, and no provider-specific
explicit actual/recorded semantic was observed.

## Entry conditions and guardrails

- Source-faithful ingestion must remain separate conceptually and in future
  staging: `MddObservationCollector` for MDD and `GtfsRtObservationCollector`
  for GTFS-RT. Do not erase source provenance or blend provider semantics in
  an initial common record.
- Collection must be prospective. Retrospective MDD StopEvent queries did not
  reliably recover realtime/`RecordedAtTime`; the tested exact-URL Wayback
  scope did not recover GTFS-RT point snapshots at the 3-month or 6-month
  horizons, even with the separate ±45-day metadata window.
- `ObservedAt` / `CapturedAt` is a first-class attribute for every future
  observation. The collector must preserve repeated observations of the same
  event so estimate evolution is analytically available; it must not retain
  only the latest estimate.
- Preserve source identity and lineage, including the distinct GTFS.de and
  VRS/go.Rheinland namespaces. Current GTFS.de Neumarkt identity is bounded
  and validated; railway Köln Hbf remains unresolved. Adjacent KVB complexes
  must not be substituted for railway Köln Hbf.
- Preserve source-specific missingness. Missing GTFS-RT StopTimeUpdates and
  missing MDD events must not automatically mean on time, served, cancelled,
  skipped, or service absent.
- Do not introduce `ActualTime`, `ActualDelay`, or an Actual Delay KPI unless a
  later source-contract decision validates the actual/recorded semantic,
  event identity, coverage, and matching requirements. Actual Delay is
  currently **NOT APPROVED**.
- Do not blend MDD `EstimatedTime` or GTFS-RT `StopTimeEvent.time` into
  confirmed actual timing. Provider timestamps such as GTFS-RT
  `FeedHeader.timestamp` and `TripUpdate.timestamp` are lineage/freshness
  metadata, not arrival times.
- Do not retain permanent or automatic full raw XML, JSON, or protobuf
  payloads by default. A bounded automatic raw-capture exception requires a
  later explicit approval of purpose, retention, storage, access, and cleanup.
- Future reconciliation is deferred until source-specific staging is designed
  and reviewed. This document does not choose final schemas, table names,
  cadence, request budgets, or reconciliation algorithms.

## Unresolved issues

1. Confirmed Actual Time source is unavailable.
2. Railway Köln Hbf identity is unresolved in GTFS.de.
3. GTFS-RT historical archive was not available from the tested Wayback
   scope: zero exact-URL captures at both horizons in both bounded runs.
4. Final collector cadence and request budget are not yet designed.
5. Final database/staging schema is not yet designed.

## Stage boundary

Stage 3 remains **NEXT / NOT STARTED**. The next user decision is whether to
begin Stage 3. Until that decision, do not write collector production code,
create SQL tables or migrations, add scheduler jobs, create services, change
Power BI, modify environment variables, query external endpoints, or design
the final reconciliation layer.

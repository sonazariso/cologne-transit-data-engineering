# Realtime Collection Design Readiness

**Status:** `STAGE 3 IN PROGRESS — STAGE 3A PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING`
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **IN PROGRESS**
**Purpose:** approved entry guardrails while Stage 3A validates disruption and
alert evidence before any production design

This document consolidates the approved post-Stage-02 source decision. It is a
readiness handoff, not an implementation design. It does not approve
collector code, database objects, schemas, migrations, scheduler jobs, final
table names, reconciliation algorithms, Power BI changes, or production
runtime changes.

Stage 3A is the current bounded substage. The [Disruption Cause & Alert
Evidence Audit](03a-disruption-cause-alert-evidence-audit.md) is **PARTIAL
HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING** and measures current GTFS-RT alert
population and two sampled MDD Situation responses. Its result is an evidence
gate, not a production approval.

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

## Cross-source reconciliation and Cologne scope guardrails

The detailed contract is documented in the [cross-source reconciliation and
Cologne scope contract](../source-contracts/cross-source-reconciliation-contract.md).
Its mandatory Stage 3 entry guardrails are:

- GTFS-RT analytical scope is **Cologne/Köln only**.
- The Germany-wide GTFS-RT feed does not expand the project's analytical
  scope to Germany, NRW, the complete VRS network, or the complete GTFS.de
  network.
- Only validated in-scope Cologne stop, service, and alert evidence enters the
  analytical scope.
- External portions of through-routes and cross-boundary trips are excluded
  from Cologne KPI calculations; an in-scope stop event is not automatically
  an in-scope full trip.
- Long-distance rail, including ICE, IC/EC, NightJet, and equivalent services,
  remains outside core KPI scope and must not change core KPI denominators.
- The assumption `MDD JourneyRef == GTFS-RT trip_id` is prohibited.
- A canonical reconciliation layer is required between source-faithful
  observations and cross-source analytical associations.
- HIGH-confidence matching is required for trip-level cross-source cause or
  effect attribution. MEDIUM matches are contextual; LOW/UNMATCHED evidence is
  not service-level attribution evidence.
- GTFS-RT ServiceAlert scope must be preserved as trip, route, stop, or agency;
  these scopes are not interchangeable.
- `ReportedCause` is provider-reported evidence and is not `RootCause`.
- Cross-source evidence must preserve source provenance, source-specific
  semantics, observation time, and disagreement rather than blending or
  overwriting records.

The contract also requires that MDD Situation references remain associations,
not automatic proof of a specific delay cause or amount. Core KPI language
must remain Cologne-observed segment/service performance for cross-boundary
services, and Actual Delay remains unapproved until confirmed actual time is
validated.

## Unresolved issues

1. Confirmed Actual Time source is unavailable.
2. Railway Köln Hbf identity is unresolved in GTFS.de.
3. GTFS-RT historical archive was not available from the tested Wayback
   scope: zero exact-URL captures at both horizons in both bounded runs.
4. Final collector cadence and request budget are not yet designed.
5. Final database/staging schema is not yet designed.
6. The full Cologne GTFS.de stop-identity bridge and canonical line mapping
   are not yet implemented.
7. Cross-source service-event matching is not yet validated. The retained
   GTFS-RT snapshot has bounded cause/effect evidence, while broader MDD
   Situation discovery remains pending and neither source has representative
   repeated-sample coverage.
8. Final GTFS-RT alert persistence and Cologne-filtering strategy is not yet
   designed.

## Stage boundary

Stage 3 is **IN PROGRESS** through Stage 3A. Stage 3A remains **PARTIAL HOST
EVIDENCE — MDD SITUATION DISCOVERY PENDING** until the broader MDD-only
discovery mode is executed and its JSONL evidence is reviewed. Do not write collector production code, create SQL tables or
migrations, add scheduler jobs, create services, change Power BI, modify
environment variables, query external endpoints from Codex, or design the
final reconciliation layer as part of this substage.

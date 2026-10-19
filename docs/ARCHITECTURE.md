# Engineering architecture and scope

**Status:** engineering pilot concluded / runtime disabled. Architecture is described from the checked-in source plus dated operational records; no current live SQL connection was performed for this documentation release.

## Design objective and responsibilities

The bounded MDD/TRIAS pipeline maintains **immutable or auditable source observations**, not a computed passenger-traffic score. The provider determines what is available; the collector must preserve time/identity semantics and show gaps instead of fabricating events. SQL Server on a Windows VM is the authoritative persistent store for the completed pilot. Python on macOS implemented later collector stages; older Windows/SQLite research paths are history, not production peers. GTFS provides versioned **static reference**, not live passenger counts.

## Component map

```text
MDD/TRIAS HTTP source          VRS GTFS static context
       |                                 |
request.py / transport.py            source audits
       |                                 |
schedule.py + quota.py                    |
       |                                  |
collector.py -> trias.py -> domain.py      |
       |                                  |
SQL adapter / persisted procedure         |
       +-----------------+----------------+
                         |
         Microsoft SQL Server: CologneTransitIntelligence
          ctl.* = operational runs, attempts, quota, claims
          stg.* = snapshots and per-snapshot source evidence
          legacy static/warehouse subset = retained demonstration only
                         |
         read-only lineage/QA and bounded sample inspection
```

## Runtime modules

- `configuration.py`, `runtime_configuration.py`, `manifest.py`: guard settings, versioned panel, runtime constraints.
- `schedule.py`, `legacy7_schedule.py`, `timing.py`: deterministic bucket/phase selection, calendar boundaries, execution budget.
- `quota.py`, `quota_baseline.py`, `quota_rollover.py`, `conservative_bootstrap.py`: shared API budget and fail-closed authorization.
- `collector.py`, `transport.py`, `request.py`: execute planned target attempts; persist source outcomes without conflating skip and success.
- `trias.py`, `domain.py`, `compact_observation.py`: parse nested provider messages to source-faithful occurrences, preserving raw semantics.
- `mssql_persistence.py`, `persistence.py`, `ports.py`: SQL transaction and adapter boundaries; auxiliary SQLite is a historical research/transfer store.
- `macos_runner.py` and `deployment/launchd/`: scheduled entry and disabled templates.

## Storage boundaries and grains

- **Control `ctl`**: one run, one target attempt/audit, one HTTP attempt, quota permit or dispatcher/target claim per declared operational identity.
- **Landing `stg`**: one SnapshotId per preserved provider response, with child occurrence rows for repeated source calls and timing/situation/attribute content. The child families do not have uniform granularity.
- **Reference/warehouse remnants**: retained for a coherent ten-snapshot SQL portfolio demonstration, **not** an active service or complete dimensional analytics layer.

## Why this architecture is not a streaming platform

There is no Kafka/event bus, public service contract, durable high-availability cluster, orchestrated cloud deployment, uninterrupted scheduled production, or proved end-to-end exactly-once provider delivery. Retries plus idempotent storage support safer replay for specified identities; provider responses remain time-dependent, and missed historical snapshots cannot be recreated later.

## Boundaries of proof

A `COMPLETE` run lifecycle does not mean every due target was persisted; see target audit / `COMPLETED_WITH_COVERAGE_GAP` rules. A provider HTTP 200 does not mean the response contains an actual timestamp. A static scheduled event count is not demand. The original first production Tiered50 heartbeat failed its deadline; 50-station continuity has not been demonstrated.

# Prepared macOS direct-persistence collector

This document describes the prepared Python collector path that writes
source-faithful MDD/TRIAS evidence directly to the authoritative Microsoft
database. It is implementation-ready but not activated. The existing Windows
collector has not been disabled, no macOS scheduler has been loaded, no
Tiered50 collection has been started, and no live MDD request is authorized by
this change.

## Runtime boundary

The macOS working copy contains the parser, direct DB-API adapter, migration,
offline tests, and a disabled `launchd` definition. The adapter uses the
pinned `pymssql==2.3.2` wheel because the actual Python 3.9 x86_64 environment
has no installed ODBC client. Credentials are read only from environment
variables (`MSSQL_*` or the existing `SQL_SERVER_*` aliases); they are never
stored in the repository or emitted in diagnostics.

The future runner is fail-closed. It requires all of the following before it
can construct an enabled MDD client:

- `COLLECTOR_MAC_SQL_ENABLED=1`;
- `COLLECTOR_WINDOWS_RUNTIME_STATE=CONFIRMED_RETIRED`;
- `COLLECTOR_CUTOVER_APPROVED=1`;
- `COLLECTOR_MAC_SQL_TARGET_SCOPE=LEGACY7_EXPLICIT_APPROVAL`; and
- a valid `MDD_API_KEY`.

It then reads the unchanged seven rows from `ctl.MddRealtimeSamplingTarget`,
requires IDs 1–7 and the existing StopPointRef mapping, and uses those rows as
the only target scope. It does not use the deferred 50-target manifest as a
live target list and does not create a new sampling era.

The current runtime gate remains unresolved: historical evidence identifies
the task name `\Cologne Transit Realtime Collector`, principal
`DATAANALYST-VM\Somaye`, and the historical PowerShell entrypoint, but a
current Windows Task Scheduler state, last/next/result state, and process
inspection were not captured. The reachable SSH endpoint used during the
audit was a separate Linux host, so it was not treated as Windows evidence.

## Old and prepared flow

```text
existing Windows collector
        │
        ├── unchanged ctl.MddCollectorRun / stg.MddRealtime* history
        └── existing Power BI-facing views

prepared macOS runner (disabled)
        │  current-bucket, no catch-up, direct DB-API connection
        ▼
ctl.MddMac* claims/audit/quota ── transaction ──► stg.MddMac* source evidence
                                                        │
                                                        └── analytics.vwMddMac*
```

The migration is additive and parallel. It does not alter, backfill, delete,
truncate, reseed, or rename any existing `ctl.MddCollectorRun`, sampling
target/slot, `stg.MddRealtime*`, GTFS, warehouse, or Power BI-facing object.
Power BI is outside this work.

## Persistence contract

Every source response becomes one `stg.MddMacSourceSnapshot` plus zero or more
occurrence rows. The snapshot is the normalized, secret-scrubbed parser
document and its SHA-256; it is not a permanent raw HTTP archive. Each child
row has a foreign key to its snapshot, and each snapshot has foreign keys to
the new run and audit rows.

| Object | Grain and key | Nullable/source-history rules | Duplicate/index rule |
| --- | --- | --- | --- |
| `ctl.MddMacCollectorRun` | One dispatcher run; identity `MacCollectorRunId`, unique `RunId` | Completion/status/summary remain nullable until the run finishes | Unique `RunId`; status/started index |
| `ctl.MddMacDispatcherClaim` | One `(PanelVersion, LogicalDueBucket)` claim | Completed time is nullable; stale claims are visible, not silently removed | Composite primary key; run FK |
| `ctl.MddMacDispatcherClaimEvent` | Append-only claim attempt event | Existing status may be NULL for a first claim | Identity key; bucket/time index; run FK |
| `ctl.MddMacTargetClaim` | One `(PanelVersion, TargetIdentity, LogicalDueBucket)` target claim | Start/completion are nullable while pending | Composite primary key; run FK |
| `ctl.MddMacTargetClaimEvent` | Append-only target claim attempt event | Existing status may be NULL for a first claim | Identity key; target/bucket/time index; run FK |
| `ctl.MddMacTargetAudit` | One target outcome within a run/bucket; identity `AuditId` | HTTP/parser/persistence/error fields remain nullable when not observed | Unique `(RunId, TargetIdentity, LogicalDueBucket)`; status/time index; run FK |
| `ctl.MddMacHttpAttempt` | One actual transport attempt per audit/attempt number | HTTP status, failure, retry, and provider confirmation can be NULL when inapplicable | Unique `(AuditId, AttemptNumber)`; completion-time index; audit and quota-permit FKs |
| `ctl.MddMacQuotaPermit` | One quota reservation/finalization | Finalization time is NULL while pending | Permit GUID primary key; status is never inferred from local simulation |
| `ctl.MddMacQuotaAudit` | Append-only decision record for a run/target/bucket | Target/bucket may be NULL for run-level decisions | Identity key; run FK |
| `ctl.MddMacSamplingEra` | Append-only prepared-era/cutover evidence | Effective time may be NULL; Tiered50 `IsActive=1` is rejected by the adapter | Identity key; no automatic activation |
| `stg.MddMacSourceSnapshot` | One parsed response observation per audit; identity `SnapshotId` | Source response timestamp is lexical and nullable; `ObservedAtUtc` is collector lineage and required | Unique `AuditId`; target/observed index; run/audit FKs |
| `stg.MddMacCallObservation` | One source call occurrence: event + `CallScope` + call ordinal | Stop identity, flags, service identity, and source paths are independently nullable | Unique `(SnapshotId, EventOrdinal, CallScope, CallOrdinal)`; snapshot FK |
| `stg.MddMacTimingObservation` | One timing occurrence: event + call + `ARRIVAL`/`DEPARTURE` | Timetabled, estimated, low/high, and RecordedAtTime are independent nullable lexical source fields | Unique `(SnapshotId, EventOrdinal, CallScope, CallOrdinal, TimingContext)`; source-identity index; snapshot FK |
| `stg.MddMacSituationEvidence` | One provider Situation/reference evidence occurrence | Situation fields, validity, affected entity, and association scope remain nullable | Identity key; participant/situation/snapshot index; snapshot FK |
| `stg.MddMacServiceSectionEvidence` | One ServiceSection occurrence by event/section ordinal/source path | All section identity and mode fields remain source-nullable; semantic role is required | Unique `(SnapshotId, EventOrdinal, SectionOrdinal, SourcePath)`; source-order index; snapshot FK |
| `stg.MddMacAttributeEvidence` | One Attribute/GeneralAttribute occurrence by event/attribute ordinal/source path | Code/text/call association fields remain nullable | Unique `(SnapshotId, EventOrdinal, AttributeOrdinal, SourcePath)`; snapshot FK |
| `stg.MddMacResponseError` | One provider response error field occurrence | Error value/scope/path are required evidence, not a thrown-away message | Unique `(SnapshotId, SourcePath, ErrorFieldName)`; snapshot FK |
| `stg.MddMacParserGap` | One unmapped-but-retained source field occurrence | Source value/representation/hint/context may be NULL; field/path/status are required | Unique `(SnapshotId, SourcePath, NormalizedFieldName)`; field/snapshot index; snapshot FK |

The migration creates `stg.uspPersistMddMacSnapshot`, which accepts the
normalized child sets as JSON arrays and inserts the complete response inside
one SQL transaction. An already-persisted `AuditId` is treated as an idempotent
duplicate and returns the existing snapshot without inserting another child
set. A parser/persistence exception rolls back the snapshot and all child
rows; the collector then completes the audit as failed.

## Timing and RecordedAtTime

`TimetabledTimeSourceText`, `EstimatedTimeSourceText`, low/high estimate
fields, and `RecordedAtTimeSourceText` are persisted separately. The adapter
does not create an `ActualTime` column, derive an actual time, or use the
collector timestamp as source timing.

The source-event identity is deterministic and MDD-local. Its conservative
basis is operating day, journey, stop point, timetable, and arrival/departure
context; mutable estimates and RecordedAtTime are not key fields. Every
source occurrence is appended with snapshot, event ordinal, call scope,
arrival/departure context, lexical value, and source path. A later snapshot
with a NULL RecordedAtTime inserts a new timing row and does not overwrite the
earlier non-NULL row.

`analytics.vwMddMacRecordedAtTimeHistory` exposes both the latest source row
(which can be NULL) and the first/last non-NULL observed source values. This
preserves the distinction between “latest response omitted it” and “it was
never observed.”

## Situation, cause, and gaps

Situation/attribute data is provider-reported evidence only. The schema keeps
Situation number, participant, summary/description/detail, validity, affected
entity/journey/line JSON, source paths, associations, and source
representation. It has no `RootCause` field and the adapter never infers
cancellation, cause, or resolution from a missing event.

Multiple ServiceSections and multiple Attribute/GeneralAttribute occurrences
remain separate rows. Unmapped relevant fields are retained in
`stg.MddMacParserGap` with path, type, value/representation, mapping status,
and context. Full request headers, API keys, and permanent raw response bytes
are not persisted.

## Scheduling, retry, and retention controls

- The future scheduler interval is five minutes with `StartInterval=300`,
  `ThrottleInterval=300`, no `RunAtLoad`, and no missed-bucket replay.
- Dispatcher and target claims are durable and stale claims are observable.
- Initial quota reservations are durable. Because Windows provider-wide usage
  is still unreconciled, initial attempts can be recorded conservatively but
  automatic retries fail closed with `RETRY_DENIED_QUOTA_STATE_UNAVAILABLE`.
- A temporary raw-evidence directory is optional, owner-only (`0700`), with
  owner-only files (`0600`), secret-marker rejection, and automatic maximum
  retention of 24 hours. It is outside Git and is not a database source of
  record.

## Connectivity and validation evidence

The macOS audit used the real configured database and read-only queries for
database selection, schema/count baseline, seven target IDs, current run
state, and the latest realtime observation. At the audit point the latest
existing observation was `2026-10-07 07:43:48Z`; the existing seven target IDs
were unchanged. No MDD call was made as part of connectivity or migration
validation.

The subsequent additive-apply attempt was not completed because the
configured VM host became unreachable from the Mac (`Host is down`) before
the migration connection was opened. No partial schema-apply result is
claimed; rerun the explicit apply/validation scripts after the VM/network is
available.

The migration is applied by `scripts/apply_mssql_migration.py` only with an
explicit `--apply`. It is batch-aware, refuses a different database name,
captures pre/post row-count baselines, and rejects any decrease or target-ID
change. `scripts/validate_mssql_migration.py` provides static and read-only
live validation. The direct adapter and parser tests use fixtures and fake
connections; they do not write fixture rows into the authoritative database.

## Cutover and rollback

Cutover remains a separate user-approved decision:

1. Capture current Windows task identity/state/process evidence and resolve the
   `WINDOWS_COLLECTOR_RUNTIME_STATE_UNCONFIRMED` blocker.
2. Review the migration/schema and read-only baseline.
3. Obtain explicit approval to stop the Windows task, enable the Mac runner,
   and begin repeated collection. The runner's four environment gates must be
   changed together; the launchd artifact itself remains disabled until then.
4. Validate one manually authorized Heumarkt response, then validate the
   controlled seven-target path and quota/audit evidence.

Rollback is scheduler-level and additive: stop/unload the Mac job, leave all
new evidence intact for diagnosis, and resume the previously approved Windows
task only after an operator confirms its state. No migration rollback should
drop the new objects or delete historical rows. Existing tables and Power BI
objects are untouched, so this preparation has no destructive database
rollback step.

## Artifacts

- Migration: [`sql/mdd-mac-collector/create-source-faithful-persistence.sql`](../sql/mdd-mac-collector/create-source-faithful-persistence.sql)
- Adapter: [`src/cologne_transit_collector/mssql_persistence.py`](../src/cologne_transit_collector/mssql_persistence.py)
- Future gated runner: [`src/cologne_transit_collector/macos_runner.py`](../src/cologne_transit_collector/macos_runner.py)
- Disabled scheduler: [`deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist`](../deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist)
- Static/live migration validator: [`scripts/validate_mssql_migration.py`](../scripts/validate_mssql_migration.py)
- Additive migration runner: [`scripts/apply_mssql_migration.py`](../scripts/apply_mssql_migration.py)
- Offline contract tests: [`tests/test_mssql_persistence.py`](../tests/test_mssql_persistence.py) and [`tests/test_mssql_runtime.py`](../tests/test_mssql_runtime.py)

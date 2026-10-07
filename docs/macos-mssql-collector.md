# Prepared macOS direct-persistence collector

This document describes the prepared Python collector path that writes
source-faithful MDD/TRIAS evidence directly to the authoritative Microsoft
database. The additive SQL contract is applied and live-validated, and the
proven historical SQLite source has been imported and reconciled. The existing
Windows collector has not been disabled, so the macOS scheduler remains
disabled; no Tiered50 collection has been started and no new Mac MDD request
has been issued.

The durable historical source is explicitly
`/Users/admin/cologne-transit-data/live-research/collector.sqlite3`. Its
read-only proof is: 575,041,536 bytes, SHA-256
`0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc`,
SQLite application schema 5, role `LIVE_RESEARCH`, context
`LIVE_RESEARCH_SINGLE_1`, integrity/quick checks `ok`, and one contiguous
5.9306-hour source session from `2026-10-01T16:59:32.654121Z` through
`2026-10-01T22:55:22.809977Z`. This is historical research evidence and is
never relabeled as Mac, Windows, Legacy7, or Tiered50 production evidence.

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
- `COLLECTOR_MAC_SQL_TARGET_SCOPE=LEGACY7_EXPLICIT_APPROVAL`;
- `COLLECTOR_MAC_SQL_VALIDATION_POLICY=OFFLINE_RECONCILIATION_ONLY`; and
- a valid `MDD_API_KEY`.

It then reads the unchanged seven rows from `ctl.MddRealtimeSamplingTarget`,
requires IDs 1–7 and the existing StopPointRef mapping, and uses those rows as
the only target scope. It does not use the deferred 50-target manifest as a
live target list and does not create a new sampling era.

The current runtime gate is positively identified but not retired: Task
Scheduler operational events identify `\Cologne Transit Realtime Collector`,
principal `DATAANALYST-VM\Somaye`, a `powershell.exe` action, a five-minute
trigger, and successful result code 0. The SQL Server service account could
not enumerate or disable that user-owned task object, so the exact task was
left unchanged. The latest post-attempt SQL run was still Windows-era
`CollectorRunId 40390` at `2026-10-07T13:28:53Z`. An interactive/administrative
Windows Task Scheduler context is required before enabling launchd. Existing
SQLite evidence is reconciled offline; the first new Mac request, if later
authorized, must be an ordinary scheduler heartbeat over the existing Legacy7
scope, with no manually targeted Heumarkt request.

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

## Historical SQLite import and reconciliation

The separate importer is `scripts/import_live_research_sqlite.py`. The source
argument is mandatory; there is no source auto-discovery. Without `--apply` it
performs only read-only proofing. The apply path first runs the read-only
Windows-history overlap audit, then writes a generic import batch and durable
source-lineage rows. It never deduplicates on station plus rounded timestamp,
never overwrites Windows rows, and never relabels research rows as runtime
rows.

The proven source inventory is:

- `FULL_IMPORT`: `collector_runs` (75), `dispatcher_claims` (75),
  `dispatcher_claim_events` (75), `target_claims` (75), `claim_events` (75),
  `target_audits` (75), `http_attempts` (73), `quota_permits` (73),
  `quota_audit` (76), `parsed_snapshots` (70), `timing_observations` (72,190),
  `recorded_at_time_evidence` (0), and `service_section_observations` (1,400).
- `LINEAGE_ONLY`: `database_metadata` (6), `manifest_targets` (50),
  `sampling_eras` (0), `shared_usage` (1), `raw_evidence` (4), and SQLite's
  `sqlite_sequence` (9). The two SQLite reporting aliases are views and are
  not durable import tables; their source timing rows are imported instead.
- `NOT_IMPORTED_WITH_EXPLICIT_REASON`: none.

The exact dry-run and apply commands are:

```text
PYTHONPATH=src python3 scripts/import_live_research_sqlite.py \
  --source /Users/admin/cologne-transit-data/live-research/collector.sqlite3

PYTHONPATH=src python3 scripts/import_live_research_sqlite.py \
  --source /Users/admin/cologne-transit-data/live-research/collector.sqlite3 \
  --apply
```

Each batch is `IN_PROGRESS`, `COMPLETE`, or `FAILED`; a failed batch is never
reported as complete. The source SHA-256/table/source-key uniqueness contract
makes an exact rerun idempotent and records duplicates explicitly. Snapshot
JSON is passed through the same source-faithful SQL procedure as native Mac
evidence; timing, ServiceSection, RecordedAtTime, Situation, Attribute,
response-error, and parser-gap projections retain their source fields and
lineage.

The schema migration, fresh capacity preflight, six-hour import, exact
idempotency rerun, and read-only post-import reconciliation are complete. Four
failed import batches remain retained and auditable, including the interrupted
partial batch; no failed history was deleted. The final report records 70
snapshots, 38,120 calls, 72,190 timings, 1,400 ServiceSections, 121 parser
gaps, zero unexplained differences, and zero source `RecordedAtTime` values.
The remaining cutover blocker is Windows task retirement, not SQL row-volume
capacity. See the [operational record](operational/s98-live-research-import-and-cutover.md).

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

The configured SQL Server is reachable and the selected database is
`CologneTransitIntelligence`. The additive migration was applied in one
DB-API transaction and revalidated read-only: 49 SQL batches completed, all
23 additive tables and 3 procedures/views are present, and the existing
realtime baseline and sampling-target IDs 1–7 were unchanged. No MDD call was
made as part of connectivity, migration, source proof, or overlap validation.

The read-only overlap audit found zero Windows-era collector runs, Heumarkt
stop observations, or situation observations in the historical six-hour
interval. That is a count result only; exact equivalence, shared events, and
same-event different snapshots remain `NOT_PROVABLE` without legacy payload
hashes/source-event identities. No destructive merge or timestamp-based
deduplication was performed.

The SQL Server row volume currently has only 10,260,480 bytes available on
`C:\`; the importer requires conservative headroom of 1,150,083,072 bytes for
this 575,041,536-byte source. The import therefore remains blocked before a
new batch is opened. Existing failed attempts are recorded as `FAILED`, and
no database rows were deleted to make space.

The migration is applied by `scripts/apply_mssql_migration.py` only with an
explicit `--apply`. It is batch-aware, atomic, refuses a different database
name, validates required objects before the single commit, captures pre/post
row-count baselines, and rejects any decrease or target-ID change.
`scripts/validate_mssql_migration.py` provides static and read-only live
validation. The direct adapter, importer, and parser tests use fixtures and
fake connections; they do not write fixture rows into the authoritative
database.

## Cutover and rollback

Cutover remains a separate user-approved decision and is currently blocked:

1. Capture current Windows task identity/state/process evidence and resolve the
   `WINDOWS_COLLECTOR_RUNTIME_STATE_UNCONFIRMED` blocker.
2. Review the applied migration/schema, source proof, overlap audit, and
   read-only baseline; expand the SQL Server row volume before importing.
3. Run the importer with the explicit source path, reconcile every source
   table/evidence family to zero unexplained differences, and rerun the exact
   same source to prove idempotency.
4. Obtain explicit approval to stop the positively identified Windows task,
   enable the Mac runner, and begin repeated collection. The environment gates
   must be changed together; the launchd artifact remains disabled until then.
5. The first new MDD request must be a normal scheduled Legacy7 heartbeat.
   Offline SQLite reconciliation replaces any manual Heumarkt validation
   probe; no extra live Heumarkt request is required.

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
- Explicit SQLite importer: [`scripts/import_live_research_sqlite.py`](../scripts/import_live_research_sqlite.py)
- Offline contract tests: [`tests/test_mssql_persistence.py`](../tests/test_mssql_persistence.py), [`tests/test_mssql_runtime.py`](../tests/test_mssql_runtime.py), and [`tests/test_s91_sqlite_import.py`](../tests/test_s91_sqlite_import.py)

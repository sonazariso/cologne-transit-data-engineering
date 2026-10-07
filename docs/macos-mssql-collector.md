# Prepared macOS direct-persistence collector

This document describes the macOS Legacy7 collector path that writes
source-faithful MDD/TRIAS evidence directly to the authoritative Microsoft
database. The additive SQL contract is applied and live-validated, the proven
historical SQLite source has been imported and reconciled, and the exact
Windows task was manually disabled by the operator on 2026-10-07. The normal
Legacy7 `launchd` job is active; Tiered50 remains inactive.

The first three untouched scheduled Mac runs completed successfully:
`127809b0-70fa-4770-9943-664942be5fc3` (bucket `5971301`),
`83c3f261-5bf0-484e-8de6-b6f0e0724a49` (bucket `5971302`), and
`5a7e014c-6266-421c-b402-98c4f1cb89b6` (bucket `5971303`). Each run had
seven successful target audits, seven HTTP attempts, seven parsed snapshots,
and no duplicate target/bucket keys.

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
offline tests, and a safe-by-default tracked `launchd` template. The active
user-local LaunchAgent is generated from that template and runs from the
owner-only runtime bundle under
`~/Library/Application Support/CologneTransitIntelligence/`. The adapter uses
the pinned `pymssql==2.3.2` wheel because the actual Python 3.9 x86_64
environment has no installed ODBC client.

Non-secret SQL/endpoint settings are stored in the owner-only local runtime
configuration at
`~/Library/Application Support/CologneTransitIntelligence/runtime.json`
(mode `0600`, parent directory `0700`). `MDD_API_KEY` and the SQL password are
resolved from macOS Keychain generic-password items at runtime; they are never
stored in the repository, plist, runtime JSON, or diagnostics. Explicit
environment values remain supported as one-process overrides.

The runner is fail-closed. It requires all of the following before it
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

The exact task `\Cologne Transit Realtime Collector` is now operator-confirmed
`DISABLED`. Read-only SQL confirmation captured two post-retirement boundaries
with zero newer Windows runs or realtime observations. The proof boundary was
`2026-10-07T16:37:17.988028Z`; this is an evidence boundary, not an invented
operator-disable timestamp. The authoritative final Windows run is
`CollectorRunId 40393`, completed at `2026-10-07T13:43:56Z`, and the final
Windows observation is at `2026-10-07T13:43:55Z`. No SQL-side task disable
operation was used for the final retirement. Existing SQLite evidence was
reconciled offline; no manual Heumarkt request was made or required.

The runtime reuses the existing approved SQL login from the migration
configuration. Read-only inspection identifies that login as `sa`/sysadmin;
no login or permission grant was created or broadened. Replacing that existing
credential with a least-privilege runtime login remains an explicit security
follow-up.

## Old and prepared flow

```text
retired Windows collector
        │
        ├── unchanged ctl.MddCollectorRun / stg.MddRealtime* history
        └── existing Power BI-facing views

active macOS Legacy7 runner
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

- The installed scheduler interval is five minutes with `StartInterval=300`,
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

The pre-activation SQL validation established reachability to
`172.16.192.128:1433`, selected database `CologneTransitIntelligence`, all 23
additive tables and 3 procedures/views, and unchanged sampling-target IDs 1–7.
The production preflight used the same path and passed without issuing an MDD
request. Three subsequent normal scheduled Legacy7 runs then proved live
Mac-to-SQL persistence.

The read-only overlap audit found zero Windows-era collector runs, Heumarkt
stop observations, or situation observations in the historical six-hour
interval. That is a count result only; exact equivalence, shared events, and
same-event different snapshots remain `NOT_PROVABLE` without legacy payload
hashes/source-event identities. No destructive merge or timestamp-based
deduplication was performed.

The historical pre-import capacity gate was satisfied before import. The
post-import evidence recorded 46,185,209,856 bytes of free `C:\` volume space
and 45,050,331,136 bytes of projected reserve. Four failed import batches
remain retained as immutable `FAILED` history; no source rows were deleted.

The migration is applied by `scripts/apply_mssql_migration.py` only with an
explicit `--apply`. It is batch-aware, atomic, refuses a different database
name, validates required objects before the single commit, captures pre/post
row-count baselines, and rejects any decrease or target-ID change.
`scripts/validate_mssql_migration.py` provides static and read-only live
validation. The direct adapter, importer, and parser tests use fixtures and
fake connections; they do not write fixture rows into the authoritative
database.

## Current operation and rollback

The controlled cutover is complete for the existing Legacy7 scope. The tracked
plist remains safe-by-default and disabled; the active machine-specific plist
is the generated owner-local artifact at
`~/Library/LaunchAgents/com.cologne-transit-intelligence.mdd-collector.plist`.
It uses the exact interpreter
`/Library/Developer/CommandLineTools/usr/bin/python3`, `StartInterval=300`,
`RunAtLoad=false`, and the five approved non-secret gates. Its recorded SHA-256
is `051958af675218f61fca5da6def595e1b83597b5f2b8b4f46ac54f0376688f1f`.

Rollback is scheduler-level and additive: unload or disable only the Mac job,
leave all new evidence intact for diagnosis, and resume the Windows task only
after an operator confirms its state. No migration rollback should drop the
new objects or delete historical rows. Tiered50 activation, new production
target IDs, and Power BI changes remain out of scope.

At final verification the Windows SQL VM was subsequently powered off. The
next scheduled LaunchAgent invocation exited during SQL connectivity preflight
before opening a SQL run or issuing an MDD request. The LaunchAgent remains
loaded; the VM must be running for subsequent intervals to collect.

Rollback is scheduler-level and additive: stop/unload the Mac job, leave all
new evidence intact for diagnosis, and resume the previously approved Windows
task only after an operator confirms its state. No migration rollback should
drop the new objects or delete historical rows. Existing tables and Power BI
objects are untouched, so this preparation has no destructive database
rollback step.

## Artifacts

- Migration: [`sql/mdd-mac-collector/create-source-faithful-persistence.sql`](../sql/mdd-mac-collector/create-source-faithful-persistence.sql)
- Adapter: [`src/cologne_transit_collector/mssql_persistence.py`](../src/cologne_transit_collector/mssql_persistence.py)
- Mac runner: [`src/cologne_transit_collector/macos_runner.py`](../src/cologne_transit_collector/macos_runner.py)
- Safe tracked scheduler template: [`deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist`](../deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist)
- Runtime configuration: [`scripts/configure_macos_runtime.py`](../scripts/configure_macos_runtime.py)
- LaunchAgent installer: [`scripts/install_macos_launch_agent.py`](../scripts/install_macos_launch_agent.py)
- Production preflight: [`scripts/macos_production_preflight.py`](../scripts/macos_production_preflight.py)
- Windows retirement confirmation: [`scripts/confirm_windows_retirement.py`](../scripts/confirm_windows_retirement.py)
- Static/live migration validator: [`scripts/validate_mssql_migration.py`](../scripts/validate_mssql_migration.py)
- Additive migration runner: [`scripts/apply_mssql_migration.py`](../scripts/apply_mssql_migration.py)
- Explicit SQLite importer: [`scripts/import_live_research_sqlite.py`](../scripts/import_live_research_sqlite.py)
- Offline contract tests: [`tests/test_mssql_persistence.py`](../tests/test_mssql_persistence.py), [`tests/test_mssql_runtime.py`](../tests/test_mssql_runtime.py), and [`tests/test_s91_sqlite_import.py`](../tests/test_s91_sqlite_import.py)

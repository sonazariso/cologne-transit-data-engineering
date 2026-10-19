# Stage 1 — SQL Server Stabilization

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Evidence date:** 2026-10-07

**Status:** COMPLETE — current native SQL Server FULL backup, restore
verification, and full read-only integrity validation passed.

This record supersedes earlier PostgreSQL and SQLite platform choices for the
active architecture. Those earlier documents and implementation files remain
historical evidence and are intentionally retained for a later, separately
approved cleanup.

## Scope and safety boundary

The work used the existing authenticated SQL Server connection from the Windows
VM for fresh read-only metadata/data queries, one native `COPY_ONLY` FULL
backup, restore verification, and a full read-only `DBCC CHECKDB`. No schema,
table, view, procedure, index, project data row, identity, collector runtime,
MDD request, GTFS load, Power BI artifact, PostgreSQL runtime, SQLite runtime,
Ubuntu environment, or Docker environment was created or changed. The native
backup intentionally added its expected one-row `msdb` backup-history record.
No project-database DML was issued by this work.

The SQL Server service was already running after an external startup at
`2026-10-07 08:49:05.184948 +02:00`; this stage did not restart SQL Server,
repair the database, shrink files, or change database configuration. The prior
2026-10-05 VM interruption remains historical evidence only.

## Current read-only recheck

The state was re-read on `2026-10-07T06:57:41.576719Z` before the backup
operation. `CologneTransitIntelligence` was `ONLINE` and `MULTI_USER`.
SQL Server's service account is `NT Service\MSSQLSERVER`, the SQL Server
service last started at `2026-10-07 08:49:05.184948 +02:00`, and the default
backup path remains:

```text
C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\Backup
```

| Current read-only measure | Result |
|---|---:|
| Database allocated size | 12,887.63 MB |
| Data allocated / used | 3,848.00 MB / 3,594.50 MB |
| Log allocated size | 9,039.63 MB |
| Log currently used | 23.78 MB (0.2630%) |
| Fixed/attached Windows volumes visible to SQL Server | `C:` only |
| Pre-backup `C:` free space | 284 MB (`298,614,784` bytes from volume DMV) |
| Current `msdb` FULL-backup history rows | 0 |
| Current max stop/situation observation | `2026-10-07 06:49:11` UTC |
| Current max CollectorRun start / finish | `2026-10-07 06:53:54` / `06:54:25` UTC |

`CologneTransitData` was visible to the SQL Server service through the user-
provided VMware share `\\vmware-host\Shared Folders\CologneTransitData`. The
Mac host volume had 83,167,502,336 bytes (77.46 GiB) free before the backup.
The final post-copy recheck showed only `C:` as a fixed SQL-visible drive, with
1,445 MB (`1,515,700,224` bytes) free; the selected backup destination was the
share, so the native backup did not consume the low-space database/system
volume.

The final read-only live recheck at `2026-10-07T07:16:41.596361Z` still showed
`CologneTransitIntelligence` ONLINE, one FULL-backup history row, `C:` as the
only fixed SQL-visible drive with 1,385 MB (`1,453,232,128` bytes) free, and
27.99 MB (0.3097%) of the allocated log used. The existing Windows collector
had reached observation time `2026-10-07 07:13:56` UTC and 377 collector runs;
these later increases are also recorded as external append-only activity.

The log figures are inspection-only. No log shrink, recovery-model change,
compatibility-level change, or other database configuration change was made.

## Authoritative database decision

`CologneTransitIntelligence` is the sole official project database.

The live instance reported:

| Item | Result |
|---|---|
| SQL Server | Microsoft SQL Server 2025, `17.0.1125.2`, RTM |
| Edition | Standard Developer Edition (64-bit) |
| Server / VM | `DataAnalyst-VM` |
| Connection endpoint used | `172.16.192.128:1433` |
| Default instance | Yes; `InstanceName` was NULL |
| Database state | ONLINE, MULTI_USER |
| Compatibility | 170 |
| Recovery model | SIMPLE |
| Collation | `SQL_Latin1_General_CP1_CI_AS` |
| Allocated database size | 12,887.63 MB |
| Data file | 3,848.00 MB — `C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\CologneTransitIntelligence.mdf` |
| Log file | 9,039.63 MB — `C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\CologneTransitIntelligence_log.ldf` |

`NRWTransportDW` also exists and is ONLINE, but it has no active repository
runtime references or cross-database dependencies. Its 21 user tables use a
different `cfg/stg/wrk/dw` legacy shape. It is classified as
`LEGACY_REFERENCE` and was not modified.

## Backup audit

The September 30 candidate remains historical and unselected. Its previously
recorded size is 619,831,296 bytes with SHA-256
`3cbadd3fe2cca22f2394a0afadd5f18e8551b2214c2597ed393d4278b6b06523`; SQL
Server could not open its prior shared-folder path, so it was not reused or
overwritten.

A new timestamped backup was written to:

```text
SQL Server path: \\vmware-host\Shared Folders\CologneTransitData\CologneTransitIntelligence-Full-COPY_ONLY-20261007T070039Z.bak
Mac source path: /Users/admin/Documents/CologneTransitData/CologneTransitIntelligence-Full-COPY_ONLY-20261007T070039Z.bak
Mac safety copy: /Users/admin/Documents/CologneTransitData/mac-safety/CologneTransitIntelligence-Full-COPY_ONLY-20261007T070039Z.bak
```

The SQL Server service could enumerate the destination and the native backup
write succeeded, which verifies actual access rather than assuming VMware
shared-folder permissions. Backup start was `2026-10-07T07:00:40Z` and finish
was `2026-10-07T07:00:52Z`. The backup has logical size 3,770,906,624 bytes;
SQL Server reports compressed backup size 606,591,622 bytes, and the exact
verified file size is 606,586,880 bytes. The VM source SHA-256 is
`df05614cea8a14be396bccb55a80a88ab2427e8930a09af02b0df337b77bc78d`.
The Mac safety copy has the same exact byte size and SHA-256.

`RESTORE HEADERONLY` passed with `DatabaseName = CologneTransitIntelligence`,
`BackupType = 1` (database FULL), `IsCopyOnly = true`,
`HasBackupChecksums = true`, `Compressed = true`, and `CompressionAlgorithm =
MS_XPRESS`. `RESTORE FILELISTONLY` passed and represented both the expected
data and log files. `RESTORE VERIFYONLY WITH CHECKSUM` passed. The recorded
backup LSN metadata is `FirstLSN=182000003902400001`,
`LastLSN=182000003904800001`, `CheckpointLSN=182000003902400001`, and
`DatabaseBackupLSN=0`.

## Read-only baseline

The machine-readable [evidence artifact](01-sql-server-stabilization-evidence.json)
contains the complete object names and current persistent-table row counts.
The live object inventory was:

| Object type | Count |
|---|---:|
| Schemas | 18 total; project schemas are `ctl`, `stg`, `wrk`, `dw`, and `analytics` |
| Persistent user tables | 31 |
| Views | 25 |
| Stored procedures | 5 |
| User-defined table types | 3 |
| Functions | 1 |
| Indexes | 56 |
| Primary keys | 20 |
| Unique constraints | 8 |
| Foreign keys | 39 |

Important current realtime metrics:

| Metric | Current value |
|---|---:|
| `stg.MddRealtimeStopObservation` rows | 2,683 |
| Stop-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-07 07:13:56 |
| `stg.MddRealtimeSituationObservation` rows | 670 |
| Situation-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-07 07:13:56 |
| `stg.MddRealtimeStopSituationLink` rows | 817 |
| Collector runs | 377: 368 Succeeded, 6 Failed, 3 Started |
| Collector-run `StartedAtUtc` | 2026-09-07 20:03:53 through 2026-10-07 07:13:54 |
| `dw.FactOperationalStopOutcome` rows | 527 |
| Operational-fact `ServiceDate` | 2026-09-05 through 2026-09-08 |
| Operational-fact `RefreshedAtUtc` | 2026-09-08 11:59:32 through 2026-09-08 12:10:43 |

The seven existing sampling targets remain present with IDs 1–7, all enabled,
and all configured with `NumberOfResults = 5`. The sampling-slot table contains
10 existing rows; the exact target configuration and slot order are in the
JSON artifact. All static GTFS/warehouse row counts and captured surrogate
identity values are unchanged. The post-work increase was append-only activity
from the already-running Windows collector: two collector runs, ten stop
observations, two situation observations, and two links were added during the
backup/integrity window, followed by the same append-only increments during
final reporting. No target was deleted, reinserted, reseeded, or otherwise
modified.

## Read-only collector-runtime inventory

The current SQL evidence proves that the existing Windows runtime is still
capable of writing observations: the latest captured run was `CollectorRunId
40331`, automatic mode, target 2 / slot 1, started at `2026-10-07 07:13:54`
UTC, completed at `07:13:57` UTC, produced observations at `07:13:56` UTC,
returned HTTP 200, and had status `Succeeded`. The observed five-minute run
pattern is consistent with the existing seven-target runtime.

Direct current Task Scheduler and process inspection was not available through
the host's authenticated SQL path. The existing deployment documentation
identifies the relevant task as `\Cologne Transit Realtime Collector`, running
historically as `DATAANALYST-VM\Somaye` every five minutes, with the action
chain `C:\Collector\Run-MddRealtimeCollector.ps1` to
`C:\Collector\Invoke-MddRealtimeCollector.ps1`. A read-only SQL-side task
directory probe returned no rows and is not treated as proof that no task
exists. The current task enabled state, last/next run, last result, and live
process state therefore remain explicitly unconfirmed as
`WINDOWS_COLLECTOR_RUNTIME_STATE_UNCONFIRMED`. No task was enabled, disabled,
modified, or executed by this work. This is operational context for preventing
dual collection; the future macOS collector must not be activated until the
Windows runtime is explicitly retired or coordinated.

## Validation outcome

- Database existence, ONLINE state, metadata, object inventory, row counts, and
  realtime baseline: PASS.
- Current read-only state recheck, including allocated/used database and log
  space, disk state, service account, backup history, and latest realtime/run
  timestamps: PASS.
- Readability of the queried database objects: PASS.
- JSON parsing/validation: PASS for 15 repository JSON files.
- Full unit suite: PASS — 175 tests in 12.059 seconds; the final rerun had zero
  live network calls.
- An intermediate rerun was blocked by host disk space after discovering all
  175 tests; its 2 failures and 75 errors were caused by unavailable temporary
  directories/SQLite setup (`ENOSPC`), not by an assertion regression. After
  task-cache cleanup, the complete suite passed.
- Stage 14 validator: PASS; zero simulated/live network calls.
- Offline Stage 15 validation: `INSUFFICIENT_EVIDENCE` by design; live Stage 15
  remains `NOT STARTED — AWAITS TIERED50 CUTOVER`.
- Python compilation and `git diff --check`: PASS.
- Final source ZIP `v84(1).zip`: archive-content verification PASS, 234
  members, and zero forbidden macOS metadata members. Exact archive byte size
  and SHA-256 are reported with the handoff.
- Full `DBCC CHECKDB ([CologneTransitIntelligence]) WITH NO_INFOMSGS,
  ALL_ERRORMSGS`: PASS, with no corruption errors, from
  `2026-10-07T07:02:51.381130Z` through `2026-10-07T07:04:16.772131Z`.
- Verified native FULL backup: PASS; the new backup is `COPY_ONLY`, compressed,
  checksum-protected, and passes HEADERONLY, FILELISTONLY, and VERIFYONLY.
- Historical data and identities: untouched by this stage.
- No MDD request, GTFS load, Power BI change, PostgreSQL/SQLite/Ubuntu/Docker
  runtime activity, or collector activation occurred.
- Stage 1: **COMPLETE**.

The stabilization gate is closed. Retain both new backup copies and do not
overwrite or delete the historical September 30 candidate; do not start the
macOS collector or the next project stage automatically. The current Windows collector runtime remains
unconfirmed at the Task Scheduler/process level and must be explicitly retired
or coordinated before any future macOS collection activation.

# Stage 1 — SQL Server Stabilization

**Evidence date:** 2026-10-05

**Status:** BLOCKED — a verified native SQL Server FULL backup is not yet
available. This stage must not be marked COMPLETE until the backup gate passes.

This record supersedes earlier PostgreSQL and SQLite platform choices for the
active architecture. Those earlier documents and implementation files remain
historical evidence and are intentionally retained for a later, separately
approved cleanup.

## Scope and safety boundary

The inspection used the existing authenticated SQL Server connection from the
Windows VM and read-only metadata/data queries. No schema, table, view,
procedure, index, data row, identity, collector runtime, MDD request, GTFS
load, Power BI artifact, PostgreSQL runtime, SQLite runtime, Ubuntu environment,
or Docker environment was created or changed.

The VM became unavailable during the read-only work and SQL Server later
returned with a new start time. No restart or repair action was initiated by
this stage.

## Current read-only recheck

The state was re-read on `2026-10-05T15:05:08.7183131Z` before any backup
operation. `CologneTransitIntelligence` remains `ONLINE` and `MULTI_USER`.
SQL Server's service account is `NT Service\MSSQLSERVER`, the SQL Server
service last started at `2026-10-05 15:56:11.054279 +02:00`, and the default
backup path remains:

```text
C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\Backup
```

| Current read-only measure | Result |
|---|---:|
| Database allocated size | 12,887.63 MB |
| Data allocated / used | 3,848.00 MB / 3,594.38 MB |
| Log allocated size | 9,039.63 MB |
| Log currently used | 23.18 MB (0.2564%) |
| Fixed/attached Windows volumes visible to SQL Server | `C:` only |
| Current `C:` free space | 1,598 MB (`1,676,353,536` bytes from volume DMV) |
| Current `msdb` FULL-backup history rows | 0 |
| Current max stop observation | `2026-10-05 15:03:50` UTC |
| Current max situation observation | `2026-10-05 14:48:50` UTC |
| Current max CollectorRun start / finish | `2026-10-05 15:03:52` / `15:03:54` UTC |

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

`msdb` contained zero FULL-backup history rows for
`CologneTransitIntelligence`, and the SQL Server default backup directory was
empty. The local candidate
`/Users/admin/Documents/CologneTransitIntelligence-Full-2026-09-30T18-32-56.bak`
is 619,831,296 bytes with SHA-256
`3cbadd3fe2cca22f2394a0afadd5f18e8551b2214c2597ed393d4278b6b06523`.
It was not treated as valid merely because it exists. `RESTORE HEADERONLY` and
`RESTORE VERIFYONLY WITH CHECKSUM` against its VMware shared-folder UNC path
both failed with SQL Server operating-system error 2 (the SQL Server service
could not open the file), so it is not a selected safety backup.

No new backup was created. The current recheck still reports only `C:` and
only 1,598 MB free, while the database allocates 12,887.63 MB. The default
backup directory has no recorded backup history, and no alternate
SQL-Server-writable destination was validated. The SQL Server service cannot
be assumed to read or write the VMware Shared Folder path; the September 30
candidate remains historical evidence only. Creating a backup under the
current storage condition could put the VM/database at unsafe disk pressure,
so the backup operation was deliberately not started.

Required storage remediation is an existing non-`C:` volume or an expanded
current volume with sufficient free space and write access for
`NT Service\MSSQLSERVER`. After that remediation, the current database needs a
new timestamped native `FULL`, `COPY_ONLY`, `COMPRESSION`, `CHECKSUM` backup
before this gate can close.

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
| `stg.MddRealtimeStopObservation` rows | 2,618 |
| Stop-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-05 15:03:50 |
| `stg.MddRealtimeSituationObservation` rows | 664 |
| Situation-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-05 14:48:50 |
| `stg.MddRealtimeStopSituationLink` rows | 811 |
| Collector runs | 362: 355 Succeeded, 5 Failed, 2 Started |
| Collector-run `StartedAtUtc` | 2026-09-07 20:03:53 through 2026-10-05 15:03:52 |
| `dw.FactOperationalStopOutcome` rows | 527 |
| Operational-fact `ServiceDate` | 2026-09-05 through 2026-09-08 |
| Operational-fact `RefreshedAtUtc` | 2026-09-08 11:59:32 through 2026-09-08 12:10:43 |

The seven existing sampling targets remain present with IDs 1–7, all enabled,
and all configured with `NumberOfResults = 5`. The sampling-slot table contains
10 existing rows; the exact target configuration and slot order are in the
JSON artifact. No target was deleted, reinserted, reseeded, or otherwise
modified.

## Read-only collector-runtime inventory

The current SQL evidence proves that the existing Windows runtime is still
capable of writing observations: the latest captured run was `CollectorRunId
20338`, automatic mode, target 3 / slot 10, started at `2026-10-05 15:08:52`
UTC, completed at `15:08:54` UTC, produced observations at `15:08:50` UTC,
returned HTTP 200, and had status `Succeeded`. The observed five-minute run
pattern is consistent with the existing seven-target runtime.

Direct current Task Scheduler and process inspection was not available through
the host's authenticated SQL path. The existing deployment documentation
identifies the relevant task as `\Cologne Transit Realtime Collector`, running
historically as `DATAANALYST-VM\Somaye` every five minutes, with the action
chain `C:\Collector\Run-MddRealtimeCollector.ps1` to
`C:\Collector\Invoke-MddRealtimeCollector.ps1`. A read-only SQL-side task
directory probe returned no rows; no task was disabled or modified. The
current task enabled state, next-run time, and live process state therefore
remain explicitly unconfirmed. This is operational context for preventing
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
- Full unit suite: PASS — 175 tests in 14.417 seconds; the final rerun had zero
  live network calls.
- An intermediate rerun was blocked by host disk space after discovering all
  175 tests; its 2 failures and 75 errors were caused by unavailable temporary
  directories/SQLite setup (`ENOSPC`), not by an assertion regression. After
  task-cache cleanup, the complete suite passed.
- Stage 14 validator: PASS; zero simulated/live network calls.
- Offline Stage 15 validation: `INSUFFICIENT_EVIDENCE` by design; live Stage 15
  remains `NOT STARTED — AWAITS TIERED50 CUTOVER`.
- Python compilation and `git diff --check`: PASS.
- Final source ZIP `v82(1).zip`: archive-content verification PASS, 233
  members, and zero forbidden macOS metadata members. Exact archive size and
  SHA-256 were captured at creation and reported with the handoff.
- Full `DBCC CHECKDB` integrity check: NOT RUN because the VM had critically
  low free space and became unavailable; no claim of a full integrity PASS is
  made.
- Verified native FULL backup: BLOCKED; no backup was selected or created.
- Historical data and identities: untouched by this stage.
- Stage 1: **NOT COMPLETE**.

The next action is operational storage remediation outside this repository:
provide SQL Server a safe writable backup destination (or increase available
VM storage), then create and verify the required `COPY_ONLY`, `COMPRESSION`,
`CHECKSUM` FULL backup. Do not start the collector or the next project stage
automatically.

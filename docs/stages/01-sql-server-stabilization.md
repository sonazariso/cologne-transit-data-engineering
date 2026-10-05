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

No new backup was created. `xp_fixeddrives` reported only `C:` with 175 MB
free during the first audit and 1,489 MB after the uninitiated VM/SQL Server
restart, while the database allocates 12,887.63 MB. The default backup
directory remained empty, and no alternate writable destination was validated.
Creating a backup under this condition could exhaust the VM volume; this is the
blocking condition required to be resolved before any backup action.

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
| `stg.MddRealtimeStopObservation` rows | 2,548 |
| Stop-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-05 13:48:47 |
| `stg.MddRealtimeSituationObservation` rows | 659 |
| Situation-observation `ObservedAtUtc` | 2026-09-05 08:27:28 through 2026-10-05 13:48:47 |
| `stg.MddRealtimeStopSituationLink` rows | 806 |
| Collector runs | 348: 341 Succeeded, 5 Failed, 2 Started |
| Collector-run `StartedAtUtc` | 2026-09-07 20:03:53 through 2026-10-05 13:48:53 |
| `dw.FactOperationalStopOutcome` rows | 527 |
| Operational-fact `ServiceDate` | 2026-09-05 through 2026-09-08 |
| Operational-fact `RefreshedAtUtc` | 2026-09-08 11:59:32 through 2026-09-08 12:10:43 |

The seven existing sampling targets remain present with IDs 1–7, all enabled,
and all configured with `NumberOfResults = 5`. The sampling-slot table contains
10 existing rows; the exact target configuration and slot order are in the
JSON artifact. No target was deleted, reinserted, reseeded, or otherwise
modified.

## Validation outcome

- Database existence, ONLINE state, metadata, object inventory, row counts, and
  realtime baseline: PASS.
- Readability of the queried database objects: PASS.
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

# Tiered50 historical and future storage recovery — S144 continuation

Date: 2026-10-13 (Europe/Berlin)

This document continues the existing compact-observation implementation. It
does not create another numbered stage, change the sampling guard, start the
collector, or introduce a replacement architecture.

## Current result

The code review and offline fixes are complete. No production SQL connection,
backup, DDL, data migration, legacy-table retirement, MDF shrink, Power BI
refresh, collector start, or MDD request was performed.

The collector remains disabled and unloaded. Do not use the archived Windows
capture as a current recovery point: its latest verified full backup finished
on 2026-10-12. The archived capture is useful only as evidence that the
Windows-integrated administrative path exists for `DATAANALYST-VM\Somaye` on
`DataAnalyst-VM` / `CologneTransitIntelligence`; every operation below still
requires fresh SSMS results.

The confirmed SQL-visible backup root is
`\\vmware-host\Shared Folders\CologneTransitData`. The SQL Server service
must be able to see and write that UNC path; this is not a Mac filesystem
path. The latest Windows measurement reports 17.31 GiB free on C:. That is
above the 12 GiB operational floor for the backup gate, but it leaves only
5.31 GiB before that floor. It does not currently prove that a full-history
migration peak is safe, and it cannot satisfy the future target of 10 GiB
growth while retaining a 12 GiB floor (that target needs at least 22 GiB free
before growth).

`FINAL_RESULT: BLOCKED_WITH_EXACT_OPERATOR_ACTION`

The blocker is the deliberate handoff of privileged Windows SSMS operations.
The exact sequence, parameters, files, prerequisites, and expected results are
below. If a fresh physical-capacity measurement fails its stated thresholds,
the result must instead be `CAPACITY_REQUIREMENTS_NOT_MET`; do not enable
future writes in that case.

The first safe production operation after this run finishes is manual
execution of `sql/mdd-mac-collector/backup-cologne-transit-off-c-v1.sql` in
Windows SSMS, after changing only `@ApplyBackup` from `0` to `1`. It creates
and verifies one off-C COPY_ONLY backup; it does not install schema, write
historical envelopes, retire tables, shrink MDF, or start the collector.

## Implementation defects fixed

The existing implementation was corrected in place:

- `sql/mdd-mac-collector/compact-observation-storage-v1.sql` now requires all
  eight source tables, rejects incompatible pre-existing compact column
  contracts before allocator initialization, validates migration-unit hashes
  and counts, and permits unit marking only while the migration is `RUNNING`.
- `sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql` now requires
  Power BI confirmation before either future writes or retirement approval and
  no longer clears a previously recorded retirement approval on a later gate
  run.
- `sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql` verifies
  replacement object types and all eight compatibility-view counts before its
  destructive transaction commits.
- `sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql` now blocks active
  production runs and other active SQL transactions. It remains optional,
  dry-run by default, and is not part of the required compact transition.
- `scripts/verify_current_sql_backup.py`,
  `scripts/apply_compact_storage_migration.py`,
  `scripts/migrate_historical_compact_observations.py`, and
  `scripts/reclaim_sql_datafile_space.py` now fail closed on incomplete SQL
  file-space measurements. Retirement drains all SQL result sets and checks
  post-retirement compatibility counts.
- `scripts/calculate_tiered50_storage_capacity.py` now rejects logical or
  compressed-byte claims that are not explicitly measured SQL physical
  allocation, enforces the 8 GiB reserve and 12 GiB floor, and preserves the
  original guard allowance without resetting it.
- The focused regression tests cover these gates; the duplicate static GZIP
  assertion was removed.

## Production-specific parameters to fill in

Do not replace these with Mac paths or values inferred from the Mac host.

| Parameter | Required production value | Rule |
|---|---|---|
| `<SQL_INSTANCE>` | `DataAnalyst-VM` | The backup batch fails closed if `SERVERPROPERTY('ServerName')` differs |
| `<WINDOWS_LOGIN>` | `DATAANALYST-VM\Somaye` | The backup batch fails closed if `SUSER_SNAME()` differs |
| `<DATABASE>` | `CologneTransitIntelligence` | Fixed by every supplied SQL file |
| `<SQL_VISIBLE_BACKUP_PATH>` | Generated below `\\vmware-host\Shared Folders\CologneTransitData` by the backup batch | The SQL Server service must be able to write it; the Mac filesystem is irrelevant |
| `<WINDOWS_C_FREE_GIB>` | `17.31` GiB at the latest Windows check | Re-measure in SSMS; the backup floor is 12 GiB |
| `<BACKUP_DEST_FREE_GIB>` | Fresh Windows measurement of the confirmed UNC destination | Must cover the expected backup size plus the operator-approved reserve; `xp_fileexist` proves reachability, not free capacity |
| `<MIGRATION_ID>` | New UUID recorded by the operator, or the existing failed/blocked UUID being intentionally resumed | Never reuse a UUID for unrelated history |
| `<CAPACITY_PLAN_FILE>` | JSON file made from measured SQL allocation, log, tempdb, rollback, and duplication peaks | Must cover `FULL_HISTORY_MIGRATION_PEAK` |
| `<MDF_LOGICAL_FILE_NAME>` | Exact `sys.database_files.name` returned by SSMS | Never guess the logical file name |
| `<TARGET_SIZE_MB>` | Calculated from current used pages plus explicit headroom | Never substitute `SHRINKDATABASE` |
| `<POWERBI_APPROVER>` | Human who verified the real consumer refresh | Record outside SQL and in the retirement approval artifact |

## Execution order

### 1. Fresh verified off-C backup

Prerequisites:

- Connect in SSMS to the verified Windows SQL instance using the authorized
  Windows-integrated administrator identity.
- Confirm the database identity, `ONLINE`/`MULTI_USER` state, no active
  collector run, no active production lease, and no unrelated user
  transaction. The collector must remain disabled.
- Confirm the existing SQL-visible destination is reachable from the SQL
  Server service and has enough free space for the expected backup plus the
  approved reserve. The batch below also performs `xp_fileexist`; that check
  is not a free-space measurement.

In SSMS, open
`sql/mdd-mac-collector/backup-cologne-transit-off-c-v1.sql`, confirm the
connection is `DataAnalyst-VM` / `CologneTransitIntelligence` as
`DATAANALYST-VM\Somaye`, and change only:

```sql
DECLARE @ApplyBackup BIT = 1;
```

Execute the whole file. It generates a new filename below
`\\vmware-host\Shared Folders\CologneTransitData`, so no path replacement is
needed. The source-controlled default remains `0`.

Expected result: the preflight row has `preflight_status = PREFLIGHT_PASS` and
at least 12 GiB measured on C:, the backup completes, `RESTORE VERIFYONLY`
returns without error, the metadata row has exactly one disk media file with
`copy_only = 1`, `checksum_enabled = 1`, `compressed_backup_size > 0`, and the
final `verifyonly_status = PASS`. The last result set is also a JSON evidence
value. Save that value alone as `fresh-backup-evidence.json`; later Python
gates consume this existing evidence shape. Save the SSMS messages as well.
The archived 2026-10-12 backup is not sufficient.

### 2. Additive compact schema installation

Prerequisites:

- Step 1 is complete and its evidence is saved.
- The collector is still disabled/unloaded; no production run or lease is
  active; current C: free space is at least 12 GiB and log use is within the
  operator’s approved bound.
- The eight legacy tables exist as tables. No legacy data is deleted by this
  operation.

In SSMS, open
`sql/mdd-mac-collector/compact-observation-storage-v1.sql` and execute the
whole file as a reviewed working copy. Change exactly this line in the
working copy, without changing the source-controlled default:

```sql
DECLARE @ApplyCompactStorage BIT = 1;
```

The file contains `GO` separators and starts with `USE CologneTransitIntelligence`;
execute it as one SSMS script, not by pasting
individual procedure fragments. It is additive: it creates the compact state,
allocator, envelope, migration ledger, procedures, and readback/analytics
views. It does not drop, truncate, shrink, null, or rewrite legacy rows.

Expected result:

```text
COMPACT OBSERVATION STORAGE V1 INSTALLED WITH FUTURE WRITES DISABLED.
```

Then run `sql/mdd-mac-collector/audit-compact-storage-readonly.sql` unchanged.
The state row must show `FutureWriteEnabled = 0`,
`CompatibilityVerified = 0`, `HistoricalReadbackVerified = 0`, and
`LegacyRetirementApproved = 0`. Do not enable future writes during this task.

### 3. Bounded historical migration

This is the one operation that is not an SSMS-only operation in the existing
implementation. The canonical UTF-16LE JSON, SHA-256, GZIP, row normalization,
and field-level readback are implemented by
`scripts/migrate_historical_compact_observations.py`; the SQL procedure stores
the already-built envelope. There is no safe SQL-only placeholder command for
all historical JSON and binary fields. Do not replace it with a count-only
`INSERT`.

Before apply, execute this read-only SSMS inventory and save its result:

```sql
USE CologneTransitIntelligence;
GO
SELECT COUNT_BIG(*) AS snapshot_count,
       MIN(SnapshotId) AS first_snapshot_id,
       MAX(SnapshotId) AS last_snapshot_id
FROM stg.MddMacSourceSnapshot;

SELECT COUNT_BIG(*) AS compact_envelope_count,
       SUM(CASE WHEN VerificationStatus = 'VERIFIED' THEN 1 ELSE 0 END) AS verified_count,
       SUM(CASE WHEN VerificationStatus <> 'VERIFIED' THEN 1 ELSE 0 END) AS unverified_count
FROM stg.MddMacCompactObservationEnvelope;
```

Prepare `<CAPACITY_PLAN_FILE>` from a real SQL physical-allocation pilot. It
must contain these fields, with no invented values:

```json
{
  "sample_snapshots": <positive_integer>,
  "planned_snapshot_count": <current_full_snapshot_count>,
  "measurement_basis": "SQL Server physical allocation measurement on the restored production-shape copy",
  "physical_allocation_measured": true,
  "allocation_scope": "FULL_HISTORY_MIGRATION_PEAK",
  "envelope_allocation_bytes": <measured_bytes>,
  "index_overhead_bytes": <measured_bytes>,
  "transaction_log_bytes": <measured_bytes>,
  "tempdb_bytes": <measured_bytes>,
  "rollback_bytes": <measured_bytes>,
  "peak_temporary_duplication_bytes": <measured_bytes>,
  "peak_additional_bytes": <exact_sum_of_the_six_components>
}
```

If an authorized migration host is available, the exact existing command is:

```text
python3 scripts/migrate_historical_compact_observations.py \
  --apply \
  --migration-id <MIGRATION_ID> \
  --batch-size 1 \
  --capacity-plan <CAPACITY_PLAN_FILE> \
  --backup-evidence <fresh-backup-evidence.json> \
  --json
```

Run it with no `--max-snapshots`, `--from-snapshot-id`, or
`--to-snapshot-id` for the full current history. The command must run only
from an authorized host with the existing administrator connection path; this
session will not retry unavailable Mac SQL administrator authentication. The
SQL Server backup path is never a Mac filesystem path.

Expected result for a completed full migration: every selected unit reports
`VERIFIED`, every SQL readback comparison is `PASS`, every batch checkpoint is
`PASS`, the migration row is `Status = COMPLETE`,
`QuarantinedUnitCount = 0`, and the collector remains disabled. A capacity
stop or parity failure leaves the migration restartable and blocks retirement.

### 4. Exact full historical parity

First run this SSMS count/ledger gate. It is necessary but not sufficient for
exact parity:

```sql
USE CologneTransitIntelligence;
GO
SELECT MigrationId, MigrationKind, Status, VerifiedUnitCount,
       QuarantinedUnitCount, LastSnapshotId, CompletedAtUtc
FROM stg.MddMacCompactMigration
WHERE MigrationKind = 'HISTORICAL';

SELECT CASE WHEN
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) =
    (SELECT COUNT_BIG(*) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation) =
    (SELECT COALESCE(SUM(CallRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation) =
    (SELECT COALESCE(SUM(TimingRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence) =
    (SELECT COALESCE(SUM(SituationRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence) =
    (SELECT COALESCE(SUM(ServiceSectionRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence) =
    (SELECT COALESCE(SUM(AttributeRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError) =
    (SELECT COALESCE(SUM(ResponseErrorRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap) =
    (SELECT COALESCE(SUM(ParserGapRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
AND NOT EXISTS
    (SELECT 1 FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus <> 'VERIFIED')
THEN 'PASS' ELSE 'BLOCKED' END AS count_and_status_gate;
```

Then run the existing exact validator with no snapshot limit:

```text
python3 scripts/validate_compact_observation_parity.py
```

Its result is authoritative only when `mode = ONLINE_READ_ONLY`, top-level
`status = PASS`, every unit is `PASS`, every family comparison is `PASS`, and
the validator has selected the full source snapshot count. Counts alone do
not certify NULL versus empty text, Unicode, timestamps, binary GZIP companions,
identities, duplicate multiplicity, or every JSON field.

### 5. Power BI compatibility verification

Power BI compatibility must be proven against the real consumer before
retirement. Repository view definitions and a SQL catalog query do not prove
an external PBIX/PBIP refresh. The repository currently contains no verified
production Power BI consumer inventory.

In SSMS, execute the read-only audit file unchanged:

```text
sql/mdd-mac-collector/audit-compact-storage-readonly.sql
```

Then execute representative compact readback and analytics queries while
capturing errors and `STATISTICS IO/TIME`:

```sql
USE CologneTransitIntelligence;
GO
SET STATISTICS IO ON;
SET STATISTICS TIME ON;

SELECT TOP (100) *
FROM stg.vMddMacCompactSourceSnapshot
ORDER BY SnapshotId;

SELECT TOP (100) *
FROM stg.vMddMacCompactTimingObservation
ORDER BY SnapshotId, TimingObservationId;

SELECT TOP (100) *
FROM analytics.vwMddMacCompactRecordedAtTimeHistory;

SELECT TOP (100) *
FROM analytics.vwMddMacCompactSourceFieldCoverage
ORDER BY ObservedAtUtc, SnapshotId;

SET STATISTICS TIME OFF;
SET STATISTICS IO OFF;
```

For the actual consumer contract, restore the fresh backup to an isolated
Windows staging database, apply the same compact migration and verified
history there, install the retirement compatibility views in that staging
copy, and refresh the real report/dataset or an approved duplicate. Confirm
all report pages, filters, relationships, row totals, NULL/empty behavior,
Unicode, and time-series totals. Record the dataset/report identifiers, model
queries, refresh time, and the human approver. A production flag is not a
substitute for this refresh.

Expected result: all SQL smoke queries succeed, the consumer refresh succeeds
without renamed/missing fields, and the captured aggregate totals match the
pre-migration report. Until that human evidence exists, the Power BI gate is
`NOT_VERIFIED` and retirement must not be approved.

### 6. Human-approved legacy storage retirement

This operation is irreversible without restoring the Step 1 backup. It has two
separate SSMS actions.

First, after Step 5 evidence and human approval, open
`sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql` and set only:

```sql
DECLARE @ApplyCompactGateApproval BIT = 1;
DECLARE @PowerBICompatibilityConfirmed BIT = 1;
DECLARE @EnableFutureWrites BIT = 0;
DECLARE @ApproveLegacyRetirement BIT = 1;
```

Execute the whole file. Expected state output:
`CompatibilityVerified = 1`, `HistoricalReadbackVerified = 1`,
`LegacyRetirementApproved = 1`, and `FutureWriteEnabled = 0`. If any check
fails, stop; do not bypass it.

Second, only after explicit human approval for the irreversible drop, open
`sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql` and change
only:

```sql
DECLARE @ApplyLegacyRetirement BIT = 1;
```

Execute the whole file in SSMS. It checks all ledger gates, external foreign
keys, dependencies, active writers, and exact counts; in one transaction it
drops the eight old physical tables, recreates same-name compatibility views,
recreates the legacy persistence wrapper, reapplies the prior SELECT/VIEW
DEFINITION grants including DENY state, and verifies replacement counts before
commit.

Expected result:

```text
LEGACY OBSERVATION ROWSTORE RETIRED; COMPACT COMPATIBILITY VIEWS INSTALLED.
```

Do not set `@EnableFutureWrites = 1`, restart the collector, or treat the
retirement message as physical disk recovery. The source-controlled defaults
remain zero; only the reviewed SSMS working copy may use one.

### 7. Safe physical MDF space reclamation

Run only after Step 6 committed, with no active writer/lease/transaction and
with explicit approval for the physical shrink. First identify the exact file
and current measurements:

```sql
USE CologneTransitIntelligence;
GO
SELECT df.file_id, df.name AS logical_file_name, df.physical_name,
       df.size AS allocated_pages,
       FILEPROPERTY(df.name, 'SpaceUsed') AS used_pages,
       vs.volume_mount_point, vs.available_bytes,
       d.is_auto_shrink
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
CROSS JOIN sys.databases AS d
WHERE d.database_id = DB_ID()
  AND df.type = 0;
```

Replace `<MDF_LOGICAL_FILE_NAME>` and `<TARGET_SIZE_MB>` only after reviewing
that output. The following is the exact bounded shrink template; its defaults
retain 1 GiB internal headroom, cap the requested shrink at 8 GiB, and enforce
the 12 GiB Windows floor. It must not be changed to `SHRINKDATABASE`.

```sql
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @LogicalFileName sysname = N'<MDF_LOGICAL_FILE_NAME>';
DECLARE @HeadroomGiB decimal(19,3) = 1.000;
DECLARE @MaxShrinkGiB decimal(19,3) = 8.000;
DECLARE @SafetyFloorGiB decimal(19,3) = 12.000;
DECLARE @CurrentPages bigint;
DECLARE @UsedPages bigint;
DECLARE @CurrentSizeMB bigint;
DECLARE @TargetSizeMB bigint;
DECLARE @AvailableBytes bigint;
DECLARE @Volume nvarchar(256);
DECLARE @Sql nvarchar(1000);

IF (SELECT COUNT(*) FROM sys.database_files WHERE type = 0) <> 1
    THROW 51420, 'Targeted shrink requires exactly one data file.', 1;
IF NOT EXISTS (SELECT 1 FROM sys.database_files WHERE type = 0 AND name = @LogicalFileName)
    THROW 51421, 'The supplied logical data-file name does not exist.', 1;
IF EXISTS (SELECT 1 FROM sys.databases WHERE database_id = DB_ID() AND is_auto_shrink = 1)
    THROW 51422, 'AUTO_SHRINK must be disabled.', 1;
IF EXISTS
(
    SELECT 1 FROM ctl.MddMacCollectorRun
    WHERE Status IN ('RUNNING', 'CLAIMED')
      AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
)
    THROW 51423, 'A production collector run is active.', 1;
IF EXISTS
(
    SELECT 1 FROM ctl.MddMacProductionLease
    WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE'
)
    THROW 51424, 'The production lease is active.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.dm_tran_session_transactions AS st
    INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
    WHERE st.session_id <> @@SPID AND es.is_user_process = 1
)
    THROW 51425, 'Another user transaction is active.', 1;

SELECT @CurrentPages = CONVERT(bigint, size),
       @UsedPages = CONVERT(bigint, FILEPROPERTY(name, 'SpaceUsed')),
       @Volume = vs.volume_mount_point,
       @AvailableBytes = CONVERT(bigint, vs.available_bytes)
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE df.type = 0 AND df.name = @LogicalFileName;

IF @UsedPages IS NULL OR @AvailableBytes IS NULL OR @Volume IS NULL
    THROW 51426, 'SQL file-space measurement is incomplete.', 1;
IF LOWER(LEFT(@Volume, 2)) <> N'c:'
    THROW 51427, 'Physical reclamation is restricted to the Windows C: data volume.', 1;
IF @AvailableBytes < CONVERT(bigint, @SafetyFloorGiB * 1073741824.0)
    THROW 51428, 'Windows C: free space is below the 12 GiB floor.', 1;

SET @CurrentSizeMB = CEILING(@CurrentPages * 8192.0 / 1048576.0);
SET @TargetSizeMB = CEILING(
    (@UsedPages + CEILING(@HeadroomGiB * 1073741824.0 / 8192.0))
    * 8192.0 / 1048576.0
);
IF @TargetSizeMB >= @CurrentSizeMB
BEGIN
    SELECT N'NOT_NEEDED' AS status, @CurrentSizeMB AS current_size_mb,
           @TargetSizeMB AS target_size_mb, @Volume AS volume,
           @AvailableBytes / 1073741824.0 AS windows_free_gib;
    RETURN;
END;
IF (@CurrentSizeMB - @TargetSizeMB) / 1024.0 > @MaxShrinkGiB
    THROW 51429, 'The computed shrink exceeds the approved 8 GiB bound.', 1;

SELECT N'APPROVED_TARGET' AS status, @CurrentSizeMB AS current_size_mb,
       @TargetSizeMB AS target_size_mb,
       (@CurrentSizeMB - @TargetSizeMB) / 1024.0 AS requested_shrink_gib,
       @Volume AS volume,
       @AvailableBytes / 1073741824.0 AS windows_free_gib;

SET @Sql = N'DBCC SHRINKFILE (' + QUOTENAME(@LogicalFileName) + N', '
         + CONVERT(nvarchar(30), @TargetSizeMB) + N') WITH NO_INFOMSGS;';
EXEC sys.sp_executesql @Sql;

SELECT df.name AS logical_file_name, df.size * 8192.0 / 1073741824.0 AS allocated_gib,
       FILEPROPERTY(df.name, 'SpaceUsed') * 8192.0 / 1073741824.0 AS used_gib,
       vs.available_bytes / 1073741824.0 AS windows_free_gib
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE df.type = 0 AND df.name = @LogicalFileName;
```

Expected result: either `NOT_NEEDED`, or a bounded `DBCC SHRINKFILE` with the
post-check still at or above 12 GiB free on C:, and allocated MDF size reduced
without going below used pages plus headroom. No automatic shrink is run by
this repository.

### 8. Future-storage capacity verification

Future writes remain disabled. This step is a read-only capacity decision;
it does not authorize a collector restart or a production test write.

Run this SSMS measurement after Step 7 (or after Step 6 if no shrink is
needed):

```sql
USE CologneTransitIntelligence;
GO
SELECT DB_NAME() AS database_name,
       CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS server_name,
       vs.volume_mount_point,
       vs.total_bytes,
       vs.available_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;

SELECT o.name AS object_name,
       SUM(p.reserved_page_count) * 8192 AS reserved_bytes,
       SUM(p.used_page_count) * 8192 AS used_bytes,
       SUM(p.lob_reserved_page_count) * 8192 AS lob_reserved_bytes
FROM sys.dm_db_partition_stats AS p
INNER JOIN sys.objects AS o ON o.object_id = p.object_id
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name = N'stg'
  AND o.name = N'MddMacCompactObservationEnvelope'
GROUP BY o.name;

SELECT total_log_size_in_bytes, used_log_space_in_bytes,
       used_log_space_in_percent
FROM sys.dm_db_log_space_usage
WHERE database_id = DB_ID();

SELECT name, size * 8192 AS allocated_bytes,
       FILEPROPERTY(name, 'SpaceUsed') * 8192 AS used_bytes
FROM tempdb.sys.database_files;

SELECT FutureWriteEnabled, CompatibilityVerified,
       HistoricalReadbackVerified, LegacyRetirementApproved
FROM stg.MddMacCompactStorageState
WHERE StateId = 1;
```

For a real physical sample, use an isolated restored production-shape copy
and the existing envelope procedure/serializer; do not insert a test write in
production. Feed the measured result to the existing calculator:

```text
python3 scripts/calculate_tiered50_storage_capacity.py \
  --bytes-per-snapshot <MEASURED_PHYSICAL_BYTES_PER_SUCCESSFUL_SNAPSHOT> \
  --measurement-basis "SQL Server physical allocation measurement on restored production-shape copy" \
  --windows-free-gib <FRESH_C_FREE_GIB> \
  --current-allocation-gib <FRESH_CURRENT_GUARD_ALLOCATION_GIB> \
  --guard-baseline-gib 21.62 \
  --guard-max-cumulative-growth-gib 17.0
```

The target arithmetic is 36,960 requests in seven days, at most 10.000 GiB
projected growth, at least 8.000 GiB reserve after growth, and at least
12.000 GiB conservative floor after growth. Therefore the physical sample
must be no greater than 290,514 bytes (283.705 KiB) per successful snapshot
for the 10 GiB budget, and the fresh C: free-space result must pass both
post-growth gates. The original guard is not reset or rewritten. If the
calculator returns `NO_GO`, record
`FINAL_RESULT: CAPACITY_REQUIREMENTS_NOT_MET`, leave future writes disabled,
and do not start the collector.

## Exact files and defaults

The operator-facing files are:

- First safe manual operation: `sql/mdd-mac-collector/backup-cologne-transit-off-c-v1.sql`,
  default `@ApplyBackup = 0`; it is bound to `DataAnalyst-VM`,
  `DATAANALYST-VM\Somaye`, and the confirmed UNC backup root.
- Read-only audit: `sql/mdd-mac-collector/audit-compact-storage-readonly.sql`.
- Additive schema: `sql/mdd-mac-collector/compact-observation-storage-v1.sql`,
  default `@ApplyCompactStorage = 0`.
- Optional snapshot-only GZIP backfill:
  `sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql`, default `@Apply = 0`.
  It is not required for compact migration and must not be run before exact
  parity/compatibility evidence without a separate revalidation plan.
- GZIP readback check: `sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql`.
- Gate approval: `sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql`,
  all apply/approval bits default to `0`.
- Destructive retirement:
  `sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql`,
  default `@ApplyLegacyRetirement = 0`.
- Existing serializer/migration:
  `scripts/migrate_historical_compact_observations.py`.
- Existing exact parity validator: `scripts/validate_compact_observation_parity.py`.
- Existing capacity calculator: `scripts/calculate_tiered50_storage_capacity.py`.

Do not run `scripts/verify_current_sql_backup.py` from this Mac session and do
not retry unavailable Mac SQL administrator authentication. The SSMS batch in
Step 1 is the required manual backup action. The existing PowerShell helper
is an optional Windows-integrated alternative, not a reason to assume that a
Mac path is visible to SQL Server.

## Local validation completed

No production connection was used. Offline checks completed:

```text
python3 -m compileall -q src scripts tests                                  PASS
PYTHONPATH=src python3 -m unittest tests.test_compact_observation_storage \
  tests.test_windows_sql_backup_validation tests.test_windows_sql_result_shape \
  tests.test_windows_sql_dbnull tests.test_production_repairs \
  tests.test_tiered50_timing_capacity -q                                    47 PASS, 3 skipped
python3 scripts/validate_snapshot_gzip_storage_v1.py                         PASS
PYTHONPATH=src python3 scripts/validate_mssql_migration.py                   PASS
PYTHONPATH=src python3 -m unittest discover -s tests -q                      411 run; PASS, 3 skipped
git diff --check                                                             PASS
```

The evidence-path tests explicitly isolate the operator-configured external
artifact location, so the full offline suite does not write to or depend on a
user-level evidence path. These are source/static and offline results, not SQL
Server or Power BI results. No production mutation was executed.

## Final labels

```text
SOURCE_CODE_REVIEW: PASS
IMPLEMENTATION_DEFECTS: FIXED_AND_OFFLINE_TESTED
SQL_SERVER_PRODUCTION_VALIDATION: NOT_EXECUTED
FRESH_VERIFIED_OFF_C_BACKUP: READY_FOR_MANUAL_WINDOWS_SSMS; NOT_EXECUTED
WINDOWS_C_FREE_AT_LAST_CHECK: 17.31_GIB; 12_GIB_FLOOR_PASSES_BACKUP_ONLY
COMPACT_SCHEMA_INSTALLATION: NOT_EXECUTED
BOUNDED_HISTORICAL_MIGRATION: NOT_EXECUTED
EXACT_FULL_HISTORICAL_PARITY: NOT_EXECUTED
POWER_BI_COMPATIBILITY: NOT_VERIFIED
LEGACY_RETIREMENT: NOT_EXECUTED
PHYSICAL_MDF_RECLAMATION: NOT_EXECUTED
FUTURE_STORAGE_CAPACITY: NOT_MEASURED; 22_GIB_PRE_GROWTH_FREE_REQUIRED_FOR_CURRENT_TARGET
COLLECTOR_STATUS: DISABLED_AND_UNLOADED; NOT_RESTARTED
FINAL_RESULT: BLOCKED_WITH_EXACT_OPERATOR_ACTION
```

# Tiered50 historical and future storage recovery — S147 correction

Date: 2026-10-14 (Europe/Berlin)

This document continues the existing compact-observation implementation. It
does not create another numbered stage, change the sampling guard, start the
collector, or introduce a replacement architecture.

## Current result

The requested S146 implementation corrections are applied and offline-tested. No
production SQL connection, new backup, DDL, data migration, legacy-table
retirement, MDF shrink, Power BI refresh, collector start, or MDD request was
performed by this repository.

The collector remains disabled and unloaded. The operator has now confirmed
the existing 2026-10-14 recovery point in Windows SSMS. The exact file is
`CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak` below
the SQL-visible root `\\vmware-host\Shared Folders\CologneTransitData`, and
the confirmed SSMS result was:

```text
BACKUP_VERIFYONLY_PASS
```

This is an operator-confirmed, read-only SQL Server `RESTORE VERIFYONLY`
result. It is distinct from machine-generated repository evidence: no new
Windows PowerShell/JSON capture was created in this worktree, and the older
`windows-admin-evidence-v20261013-*.json` artifacts do not represent this
2026-10-14 file. Do not fabricate or backdate a JSON artifact. If a later
Python apply gate needs the existing `_backup_gate` shape, capture the already
verified file's metadata through the approved Windows path once; do not create
another backup or repeat the completed verification merely to produce a new
recovery point.

The confirmed SQL-visible backup root is
`\\vmware-host\Shared Folders\CologneTransitData`. The SQL Server service
must be able to see and write that UNC path; this is not a Mac filesystem
path. The latest supplied Windows measurement reports approximately 13.86 GiB
free on C:. That is only 1.86 GiB above the 12 GiB operational floor. It does
not currently prove that a full-history migration peak is safe. Re-measure it
with the consolidated audit before any capacity decision. It cannot satisfy
the future target of 10 GiB growth while retaining a 12 GiB floor (that target
needs at least 22 GiB free before growth).

`FINAL_RESULT: BLOCKED_PENDING_FULL_HISTORY_CAPACITY_PLAN`

The remaining blocker is missing exact full-history physical capacity evidence:
the production allocation audit and the isolated SQL Server lab measurement
have not been run from this Mac. The implementation has a signed,
independently bound physical-capacity evidence contract. The operator's SSMS
backup result proves the recovery point, but it is not silently relabeled as a
machine-generated signed artifact.
If a fresh physical-capacity measurement fails its stated thresholds, the
result must instead be `CAPACITY_REQUIREMENTS_NOT_MET`; do not enable future
writes in that case.

The next safe operator action is the consolidated read-only storage audit
described below. The backup verification step is complete and must not be
rerun unnecessarily.

## Implementation defects fixed

The existing implementation was corrected in place:

- `sql/mdd-mac-collector/compact-observation-storage-v1.sql` now requires all
  eight source tables, rejects incompatible pre-existing compact column
  contracts before allocator initialization, validates migration-unit hashes
  and counts, and permits unit marking only while the migration is `RUNNING`.
  Its apply marker is now session-context guarded across every GO batch;
  guarded batches execute inside one deployment transaction and a failed
  batch poisons the remaining batches. The Python apply runner first executes
  the complete guarded script in an outer rollback validation transaction.
- `scripts/migrate_historical_compact_observations.py` now casts SQL page
  counts to BIGINT before byte multiplication, reads target and tempdb file
  allocation/autogrowth metadata, measures active collector SQL sessions, and
  performs capacity/log/conflict preflight before the first migration-ledger
  write. Each checkpoint credits observed data/log/tempdb allocation deltas
  once, keeps rollback and transient-duplication reserves live, accounts for
  external C: consumption through the physical free-space gate, and rejects
  finite SQL autogrowth limits that cannot fit the remaining reserve. The
  measured plan must explicitly cover the exact full-history peak sum.
- Historical envelope arrays are ordered by their immutable source-family
  primary keys before canonical UTF-16LE serialization. The runner also adds
  `ORDER BY` to every source-family read, so a resumed `BUILT` unit cannot
  acquire a different hash solely because SQL Server returned rows in another
  order.
- Capacity plans now carry database identity, production file sizes, snapshot
  population, a timestamp, the exact measured sample selection, physical
  allocation deltas, and the migration implementation version. Apply requires
  a separately captured, read-only SQL allocation artifact whose fields are
  bound to those plan values, signed with the operator key, and verified with
  `--capacity-evidence-public-key` (or `MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY`).
- Historical verification now checks the actual SQL compatibility views and a
  separate physical-field readback view. It compares catalog data types,
  lengths, precision, scale, and nullability rules; normalized decoded rows;
  raw JSON types/values; NULL versus empty values; original GZIP bytes;
  identities, timestamps, paths, JSON, duplicates, and Unicode before
  promotion.
- BUILT units are restartable and returned explicitly by the store procedure;
  VERIFIED units are immutable, quarantined units cannot be promoted, and
  network/readback failures leave the migration BLOCKED with a retryable
  BUILT unit rather than falsely completing it.
- `sql/mdd-mac-collector/estimate-storage-v3-compression.sql` now contains a
  disabled-by-default isolated-lab measurement for current complete
  envelopes, raw-binary field storage, lossless dictionary references, and
  existing compressed snapshot payloads, including tempdb physical page
  allocation and a field-count losslessness check.
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
| `<WINDOWS_C_FREE_GIB>` | `13.86` GiB supplied latest Windows estimate | Re-measure in SSMS; only 1.86 GiB is above the 12 GiB floor |
| `<BACKUP_DEST_FREE_GIB>` | Fresh Windows measurement of the confirmed UNC destination | Must cover the expected backup size plus the operator-approved reserve; `xp_fileexist` proves reachability, not free capacity |
| `<MIGRATION_ID>` | New UUID recorded by the operator, or the existing failed/blocked UUID being intentionally resumed | Never reuse a UUID for unrelated history |
| `<CAPACITY_PLAN_FILE>` | JSON file made from measured SQL allocation, log, tempdb, rollback, autogrowth, and duplication peaks plus signed evidence bindings | Must cover `FULL_HISTORY_MIGRATION_PEAK`; self-declared numbers are rejected |
| `<CAPACITY_EVIDENCE_PUBLIC_KEY>` | Operator-controlled public key outside the repository | Must verify the signed SQL allocation evidence embedded in the capacity plan |
| `<MDF_LOGICAL_FILE_NAME>` | Exact `sys.database_files.name` returned by SSMS | Never guess the logical file name |
| `<TARGET_SIZE_MB>` | Calculated from current used pages plus explicit headroom | Never substitute `SHRINKDATABASE` |
| `<POWERBI_APPROVER>` | Human who verified the real consumer refresh | Record outside SQL and in the retirement approval artifact |

## Execution order

### Recovery point already confirmed

The Windows administrator has already completed the read-only SSMS check for
the exact existing file and reported `BACKUP_VERIFYONLY_PASS`. Do not open the
backup script with `@VerifyExistingBackup = 1`, invoke `-VerifyExisting`, or
create another backup as part of this sequence. Retain the operator's result
as an SSMS/operator record, separate from any machine-generated JSON evidence.

### 1. Run the consolidated production storage audit

In SSMS, connect with Windows Authentication to `DataAnalyst-VM`, database
`CologneTransitIntelligence`, using the authorized administrator identity.
Require encrypted transport with certificate validation and leave the
collector disabled/unloaded. Open and execute the whole file unchanged:

```text
sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql
```

The audit is read-only. Save its result sets outside the repository with the
stable `statement_id` values intact. The results must include:

- `capacity.database_identity`, `capacity.database_files`, and
  `capacity.production_object_allocation` with actual SQL allocations and C:
  volume availability;
- `capacity.snapshot_population` and `capacity.historical_row_counts` for the
  complete history;
- `capacity.representative_snapshot_selection` and
  `capacity.representative_observation_sizes`, which are bounded length-only
  diagnostics and are not a physical compact-envelope allocation claim;
- `capacity.transaction_log`, `capacity.tempdb`, `capacity.rollback`, and
  `capacity.autogrowth`;
- `capacity.active_writer_state`, the compact gate state, and the server-side
  column/dependency/foreign-key/module result sets.

Expected outcome: the identity is the intended database, status is `ONLINE`,
access is `MULTI_USER`, no collector run/lease/session or unrelated user
transaction is active, every required source table is present, and no required
allocation field is NULL. Any missing result, incomplete file measurement,
active writer, or C: free-space result below 12 GiB is `BLOCKED`.

The supplied 13.86 GiB value is not reused as current evidence. At that value
the hard available headroom is only `13.86 - 12.00 = 1.86 GiB`; a migration
whose measured peak exceeds that headroom must not be started.

### 2. Measure the full-history peak in an isolated SQL Server lab

Do not run the lab on `DataAnalyst-VM`. Use the already verified backup from
the existing UNC share and an isolated SQL Server instance with an existing
non-C: data/log volume. Restore a production-shape copy there, using the
logical names returned by `RESTORE FILELISTONLY` and explicit `MOVE` targets on
that non-C: volume. The lab database may retain the name
`CologneTransitIntelligence` because the existing compact SQL and migration
scripts bind that database name; isolation is provided by the separate SQL
instance, not by changing the production database.

In the isolated lab SSMS window, use the exact existing file and the backup
position recorded by the operator. First inspect the logical files:

```sql
RESTORE FILELISTONLY
FROM DISK = N'\\vmware-host\Shared Folders\CologneTransitData\CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak'
WITH FILE = <BACKUP_POSITION>;
```

Expected outcome: the result is readable from the isolated SQL Server service
account and lists every logical data/log file. Restore to an empty lab
database using those exact logical names and non-C: paths (one `MOVE` per row):

```sql
RESTORE DATABASE [CologneTransitIntelligence]
FROM DISK = N'\\vmware-host\Shared Folders\CologneTransitData\CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak'
WITH FILE = <BACKUP_POSITION>,
     MOVE N'<LOGICAL_DATA_FILE_1>' TO N'D:\SQLData\CologneTransitIntelligence_1.mdf',
     MOVE N'<LOGICAL_LOG_FILE_1>' TO N'D:\SQLLog\CologneTransitIntelligence_1.ldf',
     RECOVERY, STATS = 5;
```

Do not substitute C: paths, use `WITH REPLACE` against a production database,
or create a second copy on C:. Expected outcome: the isolated database is
`ONLINE`, its source tables and row counts match the production audit, and all
lab data/log files are on the selected non-C: volume.

In the lab SSMS query window, first run this read-only file with
`@CapturePhase = 'BEFORE'`:

```text
sql/mdd-mac-collector/capture-historical-capacity-lab-readonly.sql
```

The file must return `capacity.sample_allocation_before`, the complete
SnapshotId selection, compact-object allocations, database files, log,
tempdb, rollback activity, autogrowth limits, and the lab migration ledger.
The exact full-history measurement uses every current SnapshotId in ascending
order; a representative sample or a logical-byte extrapolation is not a
production capacity plan.

Use the existing scripts in this order on the isolated lab copy:

1. Run the additive compact-storage script in the lab working copy with only
   `@ApplyCompactStorage = 1`, while keeping future writes disabled.
2. Use the existing `scripts/migrate_historical_compact_observations.py`
   serializer and stored procedure against the lab copy, with `--batch-size 1`
   and no snapshot limit. A deliberately overprovisioned, lab-only signed
   preflight plan may be used for this first measurement run; it must never be
   promoted to production evidence. From the authorized lab migration host,
   the command shape is:

   ```text
   py -3 scripts/migrate_historical_compact_observations.py --apply \
     --migration-id <LAB_MIGRATION_ID> --batch-size 1 \
     --capacity-plan <LAB_CAPACITY_PLAN_FILE> \
     --capacity-evidence-public-key <LAB_PUBLIC_KEY> \
     --backup-evidence <LAB_BACKUP_EVIDENCE_JSON> --json
   ```

   The lab evidence and key remain separate from the production plan and key.
3. During the lab run, retain every per-unit pre/post allocation result and
   record the maximum data-file, log, and tempdb allocation observed. Run
   `sql/mdd-mac-collector/estimate-storage-v3-compression.sql` only in its
   isolated lab database, changing only `@RunLab = 1` and leaving
   `@SampleSnapshots = 0` for the full verified history, to measure temporary
   candidate allocations and losslessness. Its logical payload lengths are not
   physical production allocation evidence.
4. After the full history is `VERIFIED`, run the capture file again with only
   `@CapturePhase = 'AFTER'`.

The signed capacity-plan components must be derived from those actual lab
measurements: compact envelope clustered allocation, nonclustered index
overhead, transaction-log peak, tempdb peak, rollback reserve from a separate
bounded abort/rollback sample, finite autogrowth reserve, and peak temporary
duplication. Bind the before/after file snapshots, complete SnapshotId list,
database identity, measurement timestamp, migration implementation version,
and every result hash into `independent_sql_evidence`. Do not label the
production audit's representative sizes or the offline calculator output as
`physical_allocation_deltas`.

Expected outcome: the lab migration completes with every unit `VERIFIED`, no
quarantined unit, exact SQL readback parity, and a full-history measured peak
that can be recomputed from the signed evidence. If any component is not
measured, the capacity plan is incomplete and production remains blocked.

### Capacity decision and safest physical-space fallback

If the full-history peak does not fit in the fresh C: headroom above the 12 GiB
floor, do not run the migration, install a production compact schema, retire
legacy tables, or shrink the MDF. A read-only size sample cannot be used to
justify proceeding.

The safest practical recovery path is, in order:

1. Have the Windows administrator inventory C: with the consolidated audit and
   reclaim only unrelated, recoverable operating-system space (for example,
   stale installers, temporary exports, or duplicate non-database artifacts)
   after an explicit file-by-file review. Do not remove SQL database files,
   transaction logs, historical tables, or the verified backup.
2. If that is insufficient, use an existing non-C: SQL volume for an isolated
   restore and measured compacted copy, then obtain explicit authorization for
   a controlled production replacement/cutover. This keeps the original
   historical database intact and avoids duplicating the entire database on
   C:; it is not an action authorized by this report.
3. Only after exact full-history parity, consumer approval, and explicit
   retirement approval may the existing bounded retirement/reclamation path
   remove legacy physical storage and use a bounded `DBCC SHRINKFILE`.

Do not use `SHRINKDATABASE`, do not delete historical rows to make the
capacity check pass, and do not restart the collector. If no safe unrelated
space or non-C: path is available, leave the database and history untouched
and keep the final result `CAPACITY_REQUIREMENTS_NOT_MET`.

### 3. Additive compact schema installation

Prerequisites:

- Steps 1 and 2 must be complete and their evidence must be saved before
  proceeding.
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

### 4. Bounded historical migration

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

Prepare `<CAPACITY_PLAN_FILE>` from the full-history isolated-lab measurement.
It must contain these fields, with no invented values. The lab run must cover
the current complete snapshot population (the measured SnapshotId selection
must be the full selection used by the plan, not a logical extrapolation from
three representative snapshots); capture the target database identity, target
database file sizes, before/after physical allocation deltas, log/tempdb
growth, rollback reserve, and autogrowth limits. The signed evidence object
must repeat the bound identity, file sizes, population, sample selection,
timestamp, and deltas; a plan containing only self-declared numbers or
read-only estimates is rejected.

```json
{
  "capacity_plan_format_version": "CTI_SQL_PHYSICAL_CAPACITY_PLAN_V2",
  "migration_implementation_version": "compact-historical-migration-v2",
  "database_identity": {
    "server_name": "<SERVERPROPERTY_SERVER_NAME>",
    "database_name": "CologneTransitIntelligence"
  },
  "current_production_file_sizes": [
    {
      "file_id": <file_id>,
      "name": "<logical_file_name>",
      "type": "ROWS|LOG",
      "volume": "C:\\",
      "physical_name": "<SQL_physical_name>",
      "allocated_bytes": <measured_bytes>,
      "used_bytes": <measured_bytes_or_null_for_log>,
      "growth_value": <measured_value>,
      "is_percent_growth": false,
      "max_size_pages": <measured_pages_or_minus_one>,
      "available_bytes": <measured_bytes>
    }
  ],
  "current_tempdb_file_sizes": [
    {
      "file_id": <tempdb_file_id>,
      "name": "<tempdb_logical_file_name>",
      "type": "ROWS",
      "volume": "C:\\",
      "physical_name": "<tempdb_physical_name>",
      "allocated_bytes": <measured_bytes>,
      "used_bytes": <measured_bytes_or_null_for_log>,
      "growth_value": <measured_value>,
      "is_percent_growth": false,
      "max_size_pages": <measured_pages_or_minus_one>,
      "available_bytes": <measured_bytes>
    }
  ],
  "snapshot_population": {
    "count": <current_full_snapshot_count>,
    "first_snapshot_id": <first_snapshot_id>,
    "last_snapshot_id": <last_snapshot_id>
  },
  "measurement_timestamp_utc": "<UTC_TIMESTAMP>",
  "measured_sample_selection": {
    "snapshot_ids": [<measured_snapshot_ids>],
    "selection_sha256": "<sha256_of_compact_snapshot_id_array>"
  },
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
  "autogrowth_reserve_bytes": <measured_bytes>,
  "peak_temporary_duplication_bytes": <measured_bytes>,
  "physical_allocation_deltas": {
    "envelope_allocation_bytes": <same_measured_bytes>,
    "index_overhead_bytes": <same_measured_bytes>,
    "transaction_log_bytes": <same_measured_bytes>,
    "tempdb_bytes": <same_measured_bytes>,
    "rollback_bytes": <same_measured_bytes>,
    "autogrowth_reserve_bytes": <same_measured_bytes>,
    "peak_temporary_duplication_bytes": <same_measured_bytes>
  },
  "peak_additional_bytes": <exact_sum_of_the_seven_components>,
  "independent_sql_evidence": {
    "artifact_version": "CTI_SQL_PHYSICAL_ALLOCATION_EVIDENCE_V1",
    "status": "PASS",
    "capture_provenance": {
      "read_only": true,
      "server_name": "<same_server_name>",
      "database_name": "CologneTransitIntelligence"
    },
    "sample_allocation_before": {
      "database_file_allocated_bytes": <measured_bytes>,
      "log_file_allocated_bytes": <measured_bytes>,
      "tempdb_file_allocated_bytes": <measured_bytes>,
      "transaction_log_used_bytes": <measured_bytes>,
      "windows_c_available_bytes": <measured_bytes>
    },
    "sample_allocation_after": {
      "database_file_allocated_bytes": <measured_bytes>,
      "log_file_allocated_bytes": <measured_bytes>,
      "tempdb_file_allocated_bytes": <measured_bytes>,
      "transaction_log_used_bytes": <measured_bytes>,
      "windows_c_available_bytes": <measured_bytes>
    },
    "query_results": [
      {"statement_id": "capacity.database_identity", "status": "PASS", "executed": true, "result_sha256": "<64_hex_chars>"}
    ],
    "artifact_integrity": {
      "status": "SIGNED",
      "payload_sha256": "<sha256>",
      "signature_base64": "<operator_signature>"
    }
  }
}
```

The evidence query list must include `capacity.database_identity`,
`capacity.database_files`, `capacity.snapshot_population`,
`capacity.sample_allocation_before`, `capacity.sample_allocation_after`,
`capacity.transaction_log`, `capacity.tempdb`, `capacity.rollback`, and
`capacity.autogrowth`. Capture each result in SSMS using the existing
read-only/admin workflow, hash the canonical result, and sign the evidence
with the operator-controlled key outside the repository. Use the existing
`scripts/sign_admin_evidence.py` signing utility for the detached signature;
do not copy its private key into the repository. The signed object must also
repeat `capacity_plan_format_version`, `migration_implementation_version`,
`database_identity`, `current_production_file_sizes`,
`current_tempdb_file_sizes`, `snapshot_population`,
`measurement_timestamp_utc`, `measured_sample_selection`, and
`physical_allocation_deltas` exactly, plus signed raw before/after allocation
snapshots for the sample. The JSON validator binds the signed evidence to the
plan before any SQL connection is opened.

For the existing signer, use an operator-secure intermediate path and keep
the private key outside the repository:

```text
MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY=<CAPACITY_EVIDENCE_PUBLIC_KEY> \
python3 scripts/sign_admin_evidence.py \
  --input <UNSIGNED_CAPACITY_EVIDENCE_JSON> \
  --output <OPERATOR_SECURE_CAPACITY_EVIDENCE_JSON> \
  --intermediate \
  --signing-key <OPERATOR_SECURE_PRIVATE_KEY> \
  --public-key <CAPACITY_EVIDENCE_PUBLIC_KEY>
```

If an authorized migration host is available, the exact existing command is:

```text
python3 scripts/migrate_historical_compact_observations.py \
  --apply \
  --migration-id <MIGRATION_ID> \
  --batch-size 1 \
  --capacity-plan <CAPACITY_PLAN_FILE> \
  --capacity-evidence-public-key <CAPACITY_EVIDENCE_PUBLIC_KEY> \
  --backup-evidence <fresh-backup-evidence.json> \
  --json
```

Run it with no `--max-snapshots`, `--from-snapshot-id`, or
`--to-snapshot-id` for the full current history. The command must run only
from an authorized host with the existing administrator connection path; this
session will not retry unavailable Mac SQL administrator authentication. The
SQL Server backup path is never a Mac filesystem path. The
`<fresh-backup-evidence.json>` input must be an approved machine-generated
artifact for the already verified October 14 file; do not manufacture it from
the text `BACKUP_VERIFYONLY_PASS` and do not create another backup.

Before invoking the command, compute the physical decision from fresh SSMS
values:

```text
available_headroom_bytes = windows_c_available_bytes - (12 * 1024 * 1024 * 1024)
allow_migration = peak_additional_bytes <= available_headroom_bytes
```

Also require the log, tempdb, finite-autogrowth, rollback, active-writer, and
collector gates to pass. With the supplied 13.86 GiB value,
`available_headroom_bytes` is approximately 1.86 GiB; if the signed full-
history peak is greater than that, stop without `--apply`, leave future writes
disabled, and record `FINAL_RESULT: CAPACITY_REQUIREMENTS_NOT_MET`.

Expected result for a completed full migration: every selected unit reports
`VERIFIED`, every SQL readback comparison is `PASS`, every batch checkpoint is
`PASS`, the migration row is `Status = COMPLETE`,
`QuarantinedUnitCount = 0`, and the collector remains disabled. A capacity
stop or parity failure leaves the migration restartable and blocks retirement.

### 5. Exact full historical parity

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

### 6. Power BI compatibility verification

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

### 7. Human-approved legacy storage retirement

This operation is irreversible without restoring the verified off-C backup. It has two
separate SSMS actions.

First, after Step 6 evidence and human approval, open
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

### 8. Safe physical MDF space reclamation

Run only after Step 7 committed, with no active writer/lease/transaction and
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

SELECT df.name AS logical_file_name,
       CONVERT(BIGINT, df.size) * CONVERT(BIGINT, 8192) / 1073741824.0 AS allocated_gib,
       CONVERT(BIGINT, FILEPROPERTY(df.name, 'SpaceUsed'))
           * CONVERT(BIGINT, 8192) / 1073741824.0 AS used_gib,
       vs.available_bytes / 1073741824.0 AS windows_free_gib
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE df.type = 0 AND df.name = @LogicalFileName;
```

Expected result: either `NOT_NEEDED`, or a bounded `DBCC SHRINKFILE` with the
post-check still at or above 12 GiB free on C:, and allocated MDF size reduced
without going below used pages plus headroom. No automatic shrink is run by
this repository.

### 9. Future-storage capacity verification

Future writes remain disabled. This step is a read-only capacity decision;
it does not authorize a collector restart or a production test write.

Run this SSMS measurement after Step 8 (or after Step 7 if no shrink is
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

SELECT name,
       CONVERT(BIGINT, size) * CONVERT(BIGINT, 8192) AS allocated_bytes,
       CONVERT(BIGINT, FILEPROPERTY(name, 'SpaceUsed'))
           * CONVERT(BIGINT, 8192) AS used_bytes
FROM tempdb.sys.database_files;

SELECT FutureWriteEnabled, CompatibilityVerified,
       HistoricalReadbackVerified, LegacyRetirementApproved
FROM stg.MddMacCompactStorageState
WHERE StateId = 1;
```

The calculator below is only the existing future seven-day growth guard. It is
not the full-history migration capacity plan and cannot replace the signed
before/after lab allocation evidence. Use it only after the lab has supplied a
measured physical bytes-per-successful-snapshot result; do not run a test write
in production.

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

- Completed recovery-point procedure: `sql/mdd-mac-collector/backup-cologne-transit-off-c-v1.sql`;
  no new backup is requested. Its existing-file branch is read-only and is
  only for a later machine-generated evidence capture if required.
- Consolidated production audit:
  `sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql`.
- Isolated before/after lab capture:
  `sql/mdd-mac-collector/capture-historical-capacity-lab-readonly.sql`.
- Existing compact-state audit: `sql/mdd-mac-collector/audit-compact-storage-readonly.sql`.
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

Do not rerun the completed SSMS verification or create another backup. If the
Python migration gate later requires the machine-generated `_backup_gate`
artifact, run the existing-file branch only from an authorized Windows host
with the real SQL connection and SQL-visible backup path, and preserve the
operator-confirmed SSMS result separately. Never fabricate the JSON artifact
from the operator report.

## Local validation completed

No production connection was used. Offline checks completed:

```text
python3 -m compileall -q src scripts tests                                  PASS
PYTHONPATH=src python3 -m unittest tests.test_compact_observation_storage \
  tests.test_windows_sql_backup_validation tests.test_windows_sql_result_shape \
  tests.test_windows_sql_dbnull tests.test_production_repairs \
  tests.test_tiered50_timing_capacity -q                                    56 PASS, 3 skipped
python3 scripts/validate_snapshot_gzip_storage_v1.py                         PASS
PYTHONPATH=src python3 scripts/validate_mssql_migration.py                   PASS
PYTHONPATH=src python3 scripts/validate_storage_v3.py                         PASS
static read-only audit keyword/statement-id checks                         PASS
PYTHONPATH=src python3 -m unittest discover -s tests -q                      420 run; PASS, 3 skipped
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
SQL_SERVER_PRODUCTION_VALIDATION: STORAGE_AUDIT_NOT_EXECUTED; SSMS_BACKUP_VERIFYONLY_OPERATOR_CONFIRMED
FRESH_VERIFIED_OFF_C_BACKUP: OPERATOR_CONFIRMED_SSMS_RESTORE_VERIFYONLY_PASS; MACHINE_ARTIFACT_NOT_CAPTURED
WINDOWS_C_FREE_AT_LAST_CHECK: 13.86_GIB_SUPPLIED_ESTIMATE; 12_GIB_FLOOR_LEAVES_1.86_GIB_HEADROOM
CONSOLIDATED_STORAGE_AUDIT: PREPARED; NOT_EXECUTED
COMPACT_SCHEMA_INSTALLATION: NOT_EXECUTED
BOUNDED_HISTORICAL_MIGRATION: NOT_EXECUTED
EXACT_FULL_HISTORICAL_PARITY: NOT_EXECUTED
HISTORICAL_COMPRESSION_EFFICIENCY: OFFLINE_LOGICAL_CANDIDATES_PASS; SQL_PHYSICAL_LAB_NOT_EXECUTED
POWER_BI_COMPATIBILITY: NOT_VERIFIED
LEGACY_RETIREMENT: NOT_EXECUTED
PHYSICAL_MDF_RECLAMATION: NOT_EXECUTED
PEAK_MIGRATION_SPACE: NOT_MEASURED; 1.86_GIB_SUPPLIED_HEADROOM_REQUIRES_FRESH_RECHECK
COLLECTOR_STATUS: DISABLED_AND_UNLOADED; NOT_RESTARTED
FINAL_RESULT: BLOCKED_PENDING_FULL_HISTORY_CAPACITY_PLAN
```

# Tiered50 historical and future storage recovery — S151 correction

Date: 2026-10-14 (Europe/Berlin)

This document updates the existing storage-recovery path. It does not create a
new numbered stage, enable future writes, restart the collector, retire legacy
tables, or authorize a production SQL write.

## Current result

No production SQL connection, snapshot backfill, `ALTER INDEX`, `DROP`,
`DBCC SHRINKFILE`, Power BI refresh, collector start, or MDD request was
performed for this correction. The collector remains disabled and unloaded.

The operator-confirmed recovery point remains:

```text
CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak
BACKUP_VERIFYONLY_PASS
```

No new backup is requested. The operator record is separate from any future
machine-generated evidence artifact; do not fabricate an artifact from the
record.

The first bounded recovery candidate is the historical plaintext source
payload:

| Evidence supplied for review | Value | Interpretation |
|---|---:|---|
| Plaintext `MddMacSourceSnapshot` rows | 695 | Candidate rows only; current SQL result must be rerun |
| Plaintext `SnapshotJson` allocation | 5.51 GiB | Supplied historical allocation, not an exact `COMPRESS` saving |
| Windows C: free space | 13.92 GiB | Supplied historical evidence |
| Protected Windows C: floor | 12.00 GiB | Hard minimum |
| Headroom above floor | 1.92 GiB | `13.92 - 12.00`; must be freshly rechecked |

The exact payload saving, per-snapshot transaction-log peak, physical
allocation delta, and positive Windows free-space recovery are not measured by
this source review. Therefore:

```text
FINAL_RESULT: BLOCKED_PENDING_EXACT_SNAPSHOT_GZIP_AND_CAPACITY_EVIDENCE
PROBLEM_SOLVED: NO
```

The supplied Timing priority baseline remains 2,553,690 rows, 12.99 GiB total
allocation, 11.87 GiB clustered allocation, and `NONE` compression. Those
values remain historical evidence; the bounded Timing lab must be rerun only
after internally reclaimed capacity is measured.

## Active-plan boundaries

The recovery plan uses the existing C: allocation only. It does not add
storage, move SQL data or log files, or treat a different volume as a recovery
step. The only possible return of space to Windows is a later, separately
approved, bounded `DBCC SHRINKFILE` after internal free space and a positive
before/after Windows measurement are proven.

The plan also does not assume that payload-byte savings equal physical file
recovery. `COMPRESS` savings, SQL allocation changes, transaction-log use, and
Windows free-space changes are separate measurements.

## S151 implementation changes

The source-controlled recovery implementation now provides:

- `backfill-snapshot-gzip-v1.sql`: exact `COMPRESS` payload accounting,
  read-only physical preflight, explicit backup/readback/capacity/safety-floor
  gates, explicit approval for changing the original physical representation,
  and a one-snapshot transaction bound.
- `measure-snapshot-gzip-capacity-lab.sql`: disabled-by-default isolated-lab
  measurement that commits one snapshot at a time on a restored copy and
  records the peak transaction-log and allocation requirements. It is
  resumable and does not create a duplicate source table.
- `verify-snapshot-gzip-v1.sql`: exact decompression and consumer-view parity
  checks for both plaintext and compressed-only rows, plus stable compatibility
  evidence.
- `audit-historical-storage-recovery-readonly.sql`: a read-only exact
  `COMPRESS` result set named `capacity.snapshot_gzip_exact_savings` and a
  current-C data-file space result set named
  `capacity.datafile_space_reclamation`.
- `measure-timing-storage-options-lab.sql`: a bounded Timing sample controlled
  by measured reclaimed capacity. It no longer needs a full-history duplicate
  of the approximately 12 GiB Timing table.
- `scripts/migrate_historical_compact_observations.py`: its signed evidence
  query contract now follows the C:-only plan and requires the exact snapshot
  savings and data-file reclamation results for the C:-only recovery path.

## Required evidence before any production write

All of the following are mandatory. A positive logical saving or a passing
offline test is not a substitute for any item.

### 1. Fresh read-only production audit

With the collector disabled and unloaded, run the complete file below in the
authorized SSMS session:

```text
sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql
```

Preserve the result sets with their `statement_id` values. Confirm:

- the intended database/server identity and `ONLINE`/`MULTI_USER` state;
- current data-file, log-file, and tempdb allocation, growth, and limits;
- current C: free space and the 12 GiB floor;
- all 695 current `SnapshotId` values, in order, rather than only count/min/max;
- the exact `capacity.snapshot_gzip_exact_savings` result;
- `capacity.datafile_space_reclamation`, including allocated bytes, used bytes,
  internal free bytes, and current Windows volume availability;
- no active collector run, lease, competing collector session, or unrelated
  user transaction; and
- installed `SnapshotJsonGzip`, the lossless view, and the server-side
  compatibility/dependency result sets.

`capacity.snapshot_gzip_exact_savings` computes, for every currently
plaintext eligible row:

```text
plaintext_utf16_bytes = DATALENGTH(SnapshotJson)
compressed_payload_bytes = DATALENGTH(COMPRESS(SnapshotJson))
exact_payload_savings_bytes = plaintext_utf16_bytes - compressed_payload_bytes
```

It returns no source text. The result is exact for SQL Server `COMPRESS`, but it
is not a physical data-file or Windows-space prediction.

If the current C: result is below 12 GiB, stop. Do not backfill, shrink, or
start the collector.

### 2. Isolated one-row capacity measurement

Restore the existing verified backup to an isolated SQL Server copy using the
lab credentials and host path already defined by the repository. The lab
instance must not be the production SQL Server, and its SQL data, log, and
tempdb files must be on a non-C: volume. If no suitable isolated lab exists,
record the exact blocker:

```text
NO_ISOLATED_SQL_SERVER_AND_NON_C_STORAGE_AVAILABLE_FOR_SNAPSHOT_GZIP_PEAK_MEASUREMENT
```

On that restored copy, install the already versioned Snapshot GZIP schema and
readback view, then run the disabled-by-default file below after setting only
`@RunLab = 1` in the lab working copy:

```text
sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql
```

The lab result must cover the complete current plaintext population and must
include:

- exact payload savings;
- one result row per committed snapshot;
- maximum transaction-log bytes used by one snapshot transaction;
- maximum data-file allocation delta;
- maximum tempdb allocation delta; and
- the lab-volume consumption observed during the transaction.

The lab script records each completed SnapshotId in
`lab.SnapshotGzipCapacityMeasurement`, so an interrupted lab run resumes from
the remaining plaintext rows. The lab evidence measures the one-row operation;
it does not authorize production.

### 3. Backup coverage and readback compatibility

The existing verified recovery point must cover the history selected for the
backfill. Do not create another backup merely to produce a new source artifact.
Before a production write, retain the operator-confirmed backup result and
confirm that the selected history is represented by that recovery point.

Run the read-only verifier on the restored copy and, immediately before any
later production approval, rerun it against the production schema without
changing its defaults:

```text
sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql
```

The verifier must pass exact UTF-16 length/hash parity, valid JSON
decompression, the `stg.vMddMacSourceSnapshotJson` consumer view, row counts,
and the XOR representation contract. Existing consumers must use the view;
direct reads of the physical `SnapshotJson` column will be NULL after a row is
converted.

### 4. Approval and physical-representation gate

The production backfill changes each selected row from its plaintext physical
representation to a compressed physical representation. The source-controlled
defaults keep this disabled. A reviewed working copy must not set `@Apply = 1`
unless all of these values are independently supported by evidence and human
approval:

```sql
DECLARE @Apply BIT = 1;
DECLARE @ApproveOriginalPhysicalRepresentationChange BIT = 1;
DECLARE @BackupCoverageConfirmed BIT = 1;
DECLARE @ReadbackCompatibilityConfirmed BIT = 1;
DECLARE @PeakPerSnapshotCapacityConfirmed BIT = 1;
DECLARE @SafetyFloorConfirmed BIT = 1;
DECLARE @ApprovalReference nvarchar(200) = N'<review-record-id>';
```

The measured values must also be copied from the isolated lab result into the
four `@Measured...` variables in the reviewed working copy. Leaving any flag,
measurement, timestamp, or approval reference at its source-controlled default
keeps the write blocked.

## Guarded resumable production recovery

The approved production path is the existing
`sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql` file. Its contract is:

1. It first returns exact `COMPRESS` payload savings and current physical
   preflight results without changing a row.
2. It selects at most one eligible row in ascending `SnapshotId` order.
3. It takes an application lock and one transaction for that row.
4. It computes and retains the original length/hash before changing the row.
5. It stores `COMPRESS(SnapshotJson)`, clears only the plaintext physical
   column, and verifies the decompressed bytes and the consumer view before
   commit.
6. Any mismatch rolls the transaction back. A later run selects the next
   remaining plaintext row, so a stopped run is safe to resume.
7. It reports observed one-row log/allocation deltas and the next SnapshotId.

The script refuses to write when the live C: free space is below 12 GiB, when
the measured one-row peak would cross that floor, when the current log does not
have the measured capacity, when a collector/lease/transaction is active, or
when the backup/readback/capacity/approval gates are absent.

Do not run this recovery concurrently with collection. Do not increase the
one-row bound. Do not modify the source-controlled defaults as a convenience.

After each approved row, retain the result set named
`capacity.snapshot_gzip_one_row_result`. When the resume state reports zero
remaining plaintext rows, run the full verifier and preserve its result before
considering any physical file action.

## Can bounded `SHRINKFILE` return space to Windows?

Compression first creates reusable space inside the existing SQL data file; it
does not automatically reduce the file allocation. The answer is unknown until
the following read-only checks are run after the complete backfill:

```text
capacity.datafile_space_reclamation
```

Review the exact data-file allocated bytes, used bytes, internal free bytes,
file growth limits, volume availability, and the position of allocated pages.
Internal free bytes are not counted as Windows recovery. They may be returned
only if a bounded file shrink can move the file end without violating the
approved headroom.

If and only if a human explicitly approves the physical operation, use the
Snapshot GZIP-specific guarded command
`scripts/reclaim_snapshot_gzip_datafile_space.py` or an equivalent reviewed
SSMS working copy with all of these checks immediately before execution. The
older `scripts/reclaim_sql_datafile_space.py` remains the legacy-retirement
runner and is not the active recovery path.

- exactly one target data file and the exact logical name from SQL metadata;
- no active collector run, lease, or unrelated user transaction;
- current C: free space at or above 12 GiB;
- measured log/tempdb and external-consumption reserves remain available;
- a positive, bounded shrink target leaves at least 1 GiB internal headroom;
- the requested shrink is within the approved 8 GiB maximum; and
- the command is `DBCC SHRINKFILE`, never `SHRINKDATABASE`.

Capture the Windows C: free-space and SQL file allocation measurements before
and after the operation. The only successful physical-recovery result is a
positive measured Windows free-space delta with the C: floor still protected.
If the delta is zero or not captured, record:

```text
NOT_SOLVED_UNTIL_POSITIVE_PHYSICAL_DISK_RECOVERY_MEASURED
```

No shrink is run by this repository correction.

## Timing PAGE/dictionary reassessment after internal recovery

Timing compression is deliberately second. It may be reassessed only after the
Snapshot GZIP path has produced measured internal SQL capacity and the complete
readback check has passed.

Run the existing lab comparison with its source-controlled default disabled.
In the isolated-lab working copy, set:

```sql
DECLARE @RunLab bit = 1;
DECLARE @SampleRows bigint = <bounded_sample_rows_at_most_100000>;
DECLARE @ReclaimedCapacityBytes bigint = <measured_internal_capacity>;
```

`measure-timing-storage-options-lab.sql` now takes a deterministic bounded
sample smaller than the source population, builds PAGE and dictionary
candidates only for that sample, measures actual reserved pages and
checkpointed log/tempdb use, and reports a reclaimed-capacity gate. Its
losslessness checks preserve typed values, NULL versus empty values, binary
JSON/GZIP bytes, identities, timestamps, paths, and duplicate occurrences.

This lab result is a bounded reassessment. It is not permission to run a
production `ALTER INDEX`, to duplicate the full Timing table, or to claim a
full-history peak. Any later production Timing operation requires its own
measured in-flight peak, fresh C: preflight, explicit approval, and a separate
recovery decision.

## Operator sequence

The safe order is:

1. Keep the collector disabled/unloaded and retain the already verified backup.
2. Run the consolidated read-only production audit.
3. Run the complete one-row Snapshot GZIP capacity measurement in the isolated
   lab.
4. Verify exact readback compatibility and backup coverage.
5. Re-run the production dry-run and review exact `COMPRESS` savings, current C:
   floor, log, tempdb, and measured one-row reserves.
6. Obtain explicit approval for the physical representation change, then use
   the guarded one-row backfill only if every gate passes.
7. Run full Snapshot GZIP verification and preserve all resume/parity evidence.
8. Assess internal data-file free space. Consider bounded `SHRINKFILE` only in a
   separately approved maintenance action and only with a positive measured
   Windows result.
9. Use the internally reclaimed capacity for the bounded Timing lab sample;
   keep all production Timing compression and future writes disabled.

## Exact files and defaults

- Production read-only audit:
  `sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql`
- Additive Snapshot GZIP schema/procedure/view:
  `sql/mdd-mac-collector/optimize-snapshot-gzip-v1.sql`
- Exact dry-run and guarded one-row recovery:
  `sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql`, `@Apply = 0`
- Isolated one-row peak measurement:
  `sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql`,
  `@RunLab = 0`
- Readback verifier:
  `sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql`
- Bounded Timing PAGE/dictionary lab:
  `sql/mdd-mac-collector/measure-timing-storage-options-lab.sql`,
  `@RunLab = 0`
- Snapshot GZIP-specific guarded physical reclamation runner:
  `scripts/reclaim_snapshot_gzip_datafile_space.py` (read-only by default)
- Legacy-retirement runner, not part of this recovery path:
  `scripts/reclaim_sql_datafile_space.py`
- Existing compact migration and exact parity tools remain disabled until a
  separate capacity decision authorizes them.

## Local validation status

The following validations are source/offline checks only. They do not prove SQL
Server capacity, backup coverage, Power BI compatibility, or Windows disk
recovery:

```text
SOURCE_CODE_VALIDATION: TO_BE_RUN_AFTER_S151_SOURCE_CHANGES
ISOLATED_SQL_SERVER_VALIDATION: NOT_EXECUTED
SQL_SERVER_PRODUCTION_MEASUREMENTS: NOT_EXECUTED_FOR_S151
PRODUCTION_WRITES: NOT_EXECUTED
COLLECTOR_RESTART: NOT_EXECUTED
WINDOWS_PHYSICAL_DISK_RECOVERY: NOT_MEASURED
```

## Final labels

```text
SNAPSHOT_GZIP_CANDIDATE: 695 PLAINTEXT_ROWS; 5.51_GIB_SUPPLIED_ALLOCATION_EVIDENCE
SNAPSHOT_GZIP_EXACT_COMPRESS_SAVINGS: NOT_MEASURED; RUN_READ_ONLY_AUDIT_AND_LAB
SNAPSHOT_GZIP_PEAK_PER_ROW_LOG_AND_ALLOCATION: NOT_MEASURED; ISOLATED_LAB_REQUIRED
SNAPSHOT_GZIP_READBACK_COMPATIBILITY: SOURCE_GUARDS_PRESENT; SQL_VERIFICATION_NOT_RUN
BACKUP_COVERAGE: OPERATOR_CONFIRMED_EXISTING_VERIFYONLY_PASS; NO_NEW_BACKUP_REQUESTED
WINDOWS_C_SAFETY_FLOOR: 12_GIB_REQUIRED; 13.92_GIB_SUPPLIED; 1.92_GIB_SUPPLIED_HEADROOM
DATAFILE_SHRINKFILE: BOUNDED_ASSESSMENT_ONLY; NO_SHRINK_EXECUTED
TIMING_PAGE_VS_DICTIONARY: BOUNDED_SAMPLE_LAB_PATH_ADDED; NOT_EXECUTED
TIMING_FULL_HISTORY_DUPLICATE: NOT_PERMITTED
EXTERNAL_STORAGE_ADDITION: EXCLUDED_FROM_ACTIVE_PLAN
SQL_FILE_RELOCATION: EXCLUDED_FROM_ACTIVE_PLAN
PHYSICAL_WINDOWS_DISK_RECOVERY: NOT_EXECUTED; NO_POSITIVE_DELTA_MEASURED
COLLECTOR_STATUS: DISABLED_AND_UNLOADED; NOT_RESTARTED
FINAL_RESULT: BLOCKED_PENDING_EXACT_SNAPSHOT_GZIP_AND_CAPACITY_EVIDENCE
PROBLEM_SOLVED: NO; DO_NOT_CLAIM_SOLVED_UNTIL_ACTUAL_WINDOWS_DISK_RECOVERY_IS_MEASURED
```

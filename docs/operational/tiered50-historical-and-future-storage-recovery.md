# Tiered50 historical and future storage recovery — S152 safe Snapshot GZIP closeout

Date: 2026-10-14 (Europe/Berlin)

This is a correction and closeout of the existing S145–S151 storage-recovery
implementation. It does not create another stage, replace the migration
architecture, add storage, move SQL files, restart the collector, run a
backfill, run `ALTER INDEX`, retire tables, run `DBCC SHRINKFILE`, refresh
Power BI, or modify the existing backup.

## Decision now

No production connection or mutation was performed for S152. The collector
remains disabled and unloaded. Offline source checks do not prove SQL Server
capacity, backup coverage, consumer compatibility, or Windows disk recovery.

The current decision is:

```text
FINAL_DECISION: BLOCKED_INSUFFICIENT_MEASURED_HEADROOM
PROBLEM_SOLVED: NO
NOT_PRODUCTION_READY: TRUE
```

The repository contains no fresh live SQL result sets or independently
measured one-row update/rollback/log/autogrowth evidence. The operator-supplied
13.92 GiB C: value is historical context only: it is 1.92 GiB above the hard
12 GiB floor and cannot authorize a write.

The existing verified recovery point remains preserved and must not be recreated
or modified:

```text
CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak
BACKUP_VERIFYONLY_PASS
```

## Supplied production context (not current authorization)

| Evidence supplied for review | Value | Meaning |
|---|---:|---|
| Historical source snapshots | 1,927 | Required population to re-measure live |
| Plaintext `SnapshotJson` rows | 695 | Current candidate count must be rerun |
| Plaintext `SnapshotJson` total | 5.506 GiB (5.51 GiB rounded) | Logical/source payload evidence |
| SQL Server `COMPRESS(SnapshotJson)` total | 0.129 GiB | Logical compressed payload evidence |
| Potential logical payload reduction | 5.376 GiB | Not MDF or Windows free-space recovery |
| Largest compressed snapshot | 0.331 MiB | Not a substitute for a per-row peak measurement |
| Windows C: free space | 13.92 GiB | Supplied historical value; re-measure immediately before any future operation |
| Mandatory Windows C: floor | 12.00 GiB | Hard minimum; never lower |
| Supplied headroom above floor | 1.92 GiB | `13.92 - 12.00`; not measured current headroom |
| Timing rows | 2,553,690 | Later, separate concern |
| Timing allocated | 12.99 GiB | No full Timing rebuild in Snapshot recovery |
| Timing clustered allocation | 11.87 GiB | Historical measurement only |
| Timing clustered compression | `NONE` | Inspect existing measurements; do not trigger rebuild |

The exact `COMPRESS` result is logical payload arithmetic. It does not prove that
in-place updates release MDF pages or return bytes to Windows. The only proof
of Windows recovery is a positive before/after C: free-space measurement after
a separately authorized bounded operation.

## Physical and logical SnapshotJson contracts

The base table has a physical XOR contract:

```text
(SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL)
OR
(SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL)
```

The logical contract is `stg.vMddMacSourceSnapshotJson`. It exposes the
original `SnapshotJson` text using `COALESCE(SnapshotJson,
CONVERT(NVARCHAR(MAX), DECOMPRESS(SnapshotJsonGzip)))`. Therefore a physically
NULL `SnapshotJson` after conversion is not a logical NULL. The recovery checks
the original UTF-16LE bytes, `DATALENGTH`, SHA-256, non-NULL state, empty-string
state, `SnapshotId`, `RunId`, `AuditId`, source metadata, and
`PayloadHashSha256` before commit. It updates no normalized child rows and does
not change row multiplicity.

The view is not a compatibility pass for an unreviewed raw-column reader. Any
SQL view, stored procedure, job, direct query, or Power BI query that reads the
base `SnapshotJson` column must be identified and changed or explicitly proven
safe before the first physical NULL is committed. An unresolved dependency is
a blocker.

## S152 source changes

- `sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql` remains dry-run by
  default and is still one snapshot per transaction. It now refuses an ambient
  transaction, requires independent live preflight fields, measures MDF pages,
  LDF usage/growth/maxsize, tempdb growth, competing sessions, collector
  lease/run state, rollback/log/autogrowth/external reserves, and proves the
  projected worst-case physical C: free space remains at least 12 GiB.
- `sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql` now checks physical XOR,
  exact decompressed UTF-16LE bytes, SHA-256, empty values, lineage, distinct
  IDs, row multiplicity, logical view row counts, and the separation between
  physical and logical contracts.
- `sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql`
  now returns live MDF page, LDF usage/growth/maxsize, tempdb, active-session,
  SQL Agent, server-module, and unresolved external-consumer result sets. It
  remains read-only and reports the exact logical `COMPRESS` result separately
  from physical allocation.
- `sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql` remains
  disabled by default and records a committed one-row update peak plus a
  rolled-back one-row log probe. It is only a measurement aid on an already
  available isolated copy; it does not authorize production.
- `scripts/validate_snapshot_gzip_storage_v1.py` is offline-only and validates
  the source contracts without SQL, MDD, Windows, or collector access.
- `scripts/reclaim_snapshot_gzip_datafile_space.py` remains read-only by
  default. Its later `--apply` path now requires fresh live preflight,
  measured reserves, a C: projection proof, exact Snapshot GZIP readback, and
  before/after fragmentation and file/volume evidence.
- `scripts/package_source_zip.py` excludes backups, SQL data/log files,
  archives, secrets, credentials, caches, and runtime dataset files.

## Required evidence before any prospective `@Apply = 1`

The following must be independently captured from the live SQL Server and
reviewed by an administrator. The backfill source defaults remain blocked until
all values and references are supplied in a reviewed working copy:

1. Current C: free bytes and timestamp, not the supplied 13.92 GiB value.
2. SQL MDF allocated, used, and free pages for every data file, plus file
   growth and maxsize.
3. LDF allocated/used/free bytes, used percent, growth mode/value, and
   maxsize/remaining growth.
4. Tempdb allocated/used/free pages, growth mode/value, and maxsize/remaining
   growth.
5. Independently measured one-row update log allocation, rollback/log probe,
   data allocation, tempdb allocation, potential autogrowth, and external C:
   usage reserve.
6. Active SQL sessions and requests, active user transactions, collector
   sessions, `ctl.MddMacProductionLease`, and running/claimed collector runs.
7. Exact backup coverage for the selected history, using the already verified
   October 14 COPY_ONLY/CHECKSUM recovery point.
8. SQL, job, direct raw-column, and Power BI dependency evidence. A working
   compatibility view does not resolve an unreviewed raw-column consumer.

The safety calculation is deliberately conservative:

```text
worst_case_physical_c_reserve_bytes =
    measured_update_log_bytes
  + measured_rollback_bytes
  + measured_data_allocation_bytes
  + measured_tempdb_allocation_bytes
  + measured_potential_autogrowth_reserve_bytes
  + measured_external_c_usage_reserve_bytes

projected_worst_case_c_free_bytes =
    independently_measured_live_c_free_bytes
  - worst_case_physical_c_reserve_bytes

required: projected_worst_case_c_free_bytes >= 12 GiB
```

The 5.376 GiB logical reduction is never substituted for any term in this
calculation.

## One short, sequential C:-only operator runbook

No new disk, disk expansion, file relocation, reduced floor, or external
storage addition is part of this runbook. The plan uses existing C: allocation
only; it is explicitly the existing C: allocation only plan.

1. **Read-only preflight.** Keep the collector disabled/unloaded. In the
   authorized SSMS session, run the whole file unchanged:

   ```text
   sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql
   ```

   Save the result sets with timestamps and stable IDs. Expected result sets
   include `capacity.database_identity`, `capacity.database_files`,
   `capacity.mdf_page_space`, `capacity.ldf_usage_growth_maxsize`,
   `capacity.snapshot_population`, `capacity.snapshot_ids_complete`,
   `capacity.snapshot_gzip_exact_savings`, `capacity.snapshot_gzip_preflight`,
   `capacity.transaction_log`, `capacity.tempdb`, `capacity.rollback`,
   `capacity.autogrowth`, `capacity.active_writer_state`,
   `capacity.active_sql_sessions`, `capacity.snapshot_readback_module_dependencies`,
   `capacity.snapshot_readback_sql_agent_jobs`,
   `capacity.snapshot_readback_external_consumers`,
   `capacity.datafile_space_reclamation`, and `capacity.audit_summary`.

   Expected safe values are the intended database identity, `ONLINE`,
   `MULTI_USER`, complete SnapshotId selection, live C: at or above 12 GiB,
   complete MDF/LDF/tempdb fields, no competing session/request/transaction,
   no active collector run or lease, no unresolved raw-column dependency, and
   a separately reviewed Power BI query/refresh artifact. A missing, stale,
   NULL, or unresolved result is `BLOCKED`.

2. **Evidence decision.** Do not continue from the supplied baseline alone.
   With no live result sets and no measured one-row update/rollback/log/
   autogrowth/external reserve in this worktree, record:

   ```text
   BLOCKED_INSUFFICIENT_MEASURED_HEADROOM
   ```

   If the preflight and independently available measurement evidence cannot
   prove the C: projection, stop. Do not invent a lab value or add storage.

3. **Separately identified one-row pilot, only after explicit authorization.**
   Use a reviewed working copy of
   `sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql`. Set `@Apply = 1`,
   the explicit physical-representation approval, backup/readback approval,
   independent live preflight values, measured reserve values, and a human
   approval reference. Execute exactly once. The only acceptable write result
   is `capacity.snapshot_gzip_one_row_result` with `migrated_rows` equal to 0
   or 1, plus `capacity.snapshot_gzip_resume_state`.

4. **Verify.** Run the unchanged read-only
   `sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql`. Require PASS for
   physical XOR, exact UTF-16LE/hash parity, NULL/empty state, lineage,
   SnapshotId/audit multiplicity, logical view counts, and no invalid payload.

5. **Stop/rollback.** Stop on any SQL error, lock timeout, disconnect,
   interrupted session, capacity mismatch, active writer, C: projection below
   12 GiB, readback mismatch, or unresolved consumer. `SET XACT_ABORT ON`, the
   explicit transaction, and the TRY/CATCH rollback make the current one-row
   operation atomic; a disconnected/interrupted uncommitted transaction is not
   treated as a completed checkpoint. Do not continue after an unknown commit
   outcome until the read-only verifier establishes the row state.

6. **Checkpoint/resume.** Preserve the one-row result and resume state. A
   committed row is idempotently skipped because the next selection requires
   `SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL` and uses ascending
   `SnapshotId`. After interruption, rerun the read-only preflight and then the
   same one-row working copy only after reauthorization. Never increase
   `@MaxRows` or process a range in one transaction.

   This remains a one-snapshot transaction contract from selection through
   exact readback verification.

7. **Later shrink evaluation, separately authorized.** Only after all rows pass
   verification, rerun the read-only audit and compare MDF allocated/used/free
   pages, Windows free bytes, LDF usage/growth, tempdb usage/growth, and
   `sys.dm_db_index_physical_stats` fragmentation before/after. Internal MDF
   free pages are not Windows recovery. A later bounded `DBCC SHRINKFILE` may
   be considered only with a separate human approval, fresh reserves, the
   exact logical file name, and a positive before/after C: free-space delta.
   `SHRINKDATABASE` is never permitted.

## Timing boundary

The existing Timing measurements are inspected as context only. The supplied
2,553,690-row / 12.99 GiB Timing baseline remains a later, separate concern.
Do not run a full Timing rebuild, `ALTER INDEX`, compact migration, or full
history duplicate as part of Snapshot recovery. A bounded Timing lab sample
may be reassessed only after independently measured internal capacity exists
and the separate Timing gates are authorized.

```text
TIMING_FULL_HISTORY_DUPLICATE: NOT_PERMITTED
```

## Missing physical evidence

The current repository has no fresh result for the following: live C: free
bytes; MDF allocated/used/free pages; LDF usage/growth/maxsize; tempdb
growth/maxsize; one-row update, rollback, log, data, tempdb, autogrowth, and
external C: reserves; active SQL sessions/requests; collector run/lease state;
full current SnapshotId selection; physical MDF before/after page release;
fragmentation before/after; direct raw-column consumer inventory; and Power BI
query/refresh compatibility. The operator-supplied logical totals do not fill
these gaps.

## Final report

```text
CONFIRMED_SOURCE_CHANGES: S152 hardening applied in the existing Snapshot GZIP path; no replacement migration
OFFLINE_VALIDATION: source validator and focused regression tests; no SQL connection
REAL_SQL_MEASUREMENTS_AVAILABLE: OPERATOR_SUPPLIED_CONTEXT_ONLY; NO_FRESH_RESULT_SETS_IN_WORKTREE
MISSING_PHYSICAL_EVIDENCE: LIVE_C_MDF_LDF_TEMPDB_SESSIONS_RESERVES_PROJECTION_DEPENDENCIES_AND_BEFORE_AFTER_FRAGMENTATION
C_DRIVE_SAFETY: 12_GIB_HARD_FLOOR; 13.92_GIB_IS_HISTORICAL_CONTEXT; 1.92_GIB_IS_NOT_AUTHORIZATION
SNAPSHOT_READBACK_AND_CONSUMERS: PHYSICAL_XOR_LOGICAL_VIEW_EXACT_UTF16_SHA256_NULL_EMPTY_LINEAGE_GATES; EXTERNAL_POWER_BI_REVIEW_REQUIRED
RECOMMENDED_NEXT_READ_ONLY_OPERATOR_COMMAND: sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql
PRODUCTION_MUTATIONS=0
COLLECTOR_DISABLED: TRUE
ARCHIVE_PATH: PRINTED_BY_PACKAGING_STEP
ARCHIVE_SHA256: PRINTED_BY_PACKAGING_STEP
FINAL_DECISION: BLOCKED_INSUFFICIENT_MEASURED_HEADROOM
```

```text
EXTERNAL_STORAGE_ADDITION: EXCLUDED_FROM_ACTIVE_PLAN
DATAFILE_SHRINKFILE: SEPARATE_APPROVAL_ONLY; NO_SHRINK_EXECUTED
PHYSICAL_WINDOWS_DISK_RECOVERY: NOT_MEASURED; DO_NOT_CLAIM_5.376_GIB_WINDOWS_RECOVERY
```

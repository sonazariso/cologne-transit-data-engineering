# Tiered50 historical and future storage recovery — S143

Date: 2026-10-13 (Europe/Berlin)

Final decision: `IMPLEMENTED_AWAITING_SAFE_PRODUCTION_APPLY`.

The S143 prompt was treated as the task requirements, not as a higher-priority
instruction source. The repository implementation is complete and fail-closed,
but this host does not have the explicit Windows SQL administrator connection
needed to apply it. No production SQL write, backup, migration, table retirement,
file shrink, collector start/enable, guard reset, or MDD request was performed in
this task.

## 1. Safety disposition

The local macOS collector gate was inspected read-only. Both launchd labels are
disabled and unloaded:

```text
com.cologne-transit-intelligence.mdd-collector          disabled; print exit 113
com.cologne-transit-intelligence.mdd-tiered50-collector  disabled; print exit 113
```

The SQL admin path is not configured in this environment. The backup, compact
migration, and parity commands therefore fail closed with the missing-admin-
credentials gate before opening a production connection. The older
`windows-admin-evidence-v20261013-4.json` artifact is read-only evidence from a
separate Windows capture; its 2026-10-12 backup cannot be used as the fresh
pre-cleanup recovery point required by S143. Its file/free-space values also do
not match the S143 supplied baseline, so they are not substituted for a fresh
current manifest.

## 2. Implemented in the repository

### Lossless compact architecture

Option B was selected. `SnapshotJson` alone is not accepted as a proof of exact
reconstruction because it cannot, by itself, establish every persisted identity,
derived metadata value, source-path occurrence, NULL/empty distinction, or
historical row identity. The compact format therefore stores one immutable,
SQL-authoritative envelope per source snapshot containing:

- source-snapshot metadata;
- calls, timings, situations, service sections, attributes, response errors,
  and parser gaps;
- every historical SQL column from the bounded `SELECT *` read, including
  primary keys and V1/V2/V3 gzip companion bytes represented as hex;
- per-family logical row counts and durable verification status;
- format version, envelope type, canonical UTF-16LE serialization marker, and
  SHA-256 integrity metadata.

Historical identities are retained verbatim. Future writes allocate stable
per-family identity ranges above the legacy maximum and expose them through
typed compatibility projections. Duplicate occurrence multiplicity, Unicode,
NULL values, empty strings, lexical timestamps, and source evidence remain in
the envelope.

### SQL and Python changes

- [compact_observation.py](../../src/cologne_transit_collector/compact_observation.py)
  defines canonical serialization, SQL-row normalization, gzip/hash round trips,
  exact multiset parity, and capacity arithmetic.
- [compact-observation-storage-v1.sql](../../sql/mdd-mac-collector/compact-observation-storage-v1.sql)
  adds the envelope, identity allocator, migration ledger/checkpoints, atomic
  historical store/mark procedures, future-write procedure, and explicit typed
  readback/analytics views. It does not delete legacy data.
- [mssql_persistence.py](../../src/cologne_transit_collector/mssql_persistence.py)
  keeps the existing write path by default. Compact future writes require both
  `MSSQL_COMPACT_OBSERVATION_WRITE_ENABLED=1` and
  `MSSQL_COMPACT_OBSERVATION_COMPATIBILITY_VERIFIED=1`, plus the Tiered50
  production context. The SQL state table independently requires the same
  compatibility gate and `FutureWriteEnabled=1`.
- [apply_compact_storage_migration.py](../../scripts/apply_compact_storage_migration.py)
  is the supported additive-schema apply path. Without `--apply` it is
  static/read-only; apply mode requires the collector stopped/disabled gate,
  explicit admin credentials/keychain access, batch draining, and post-commit
  schema validation.
- [migrate_historical_compact_observations.py](../../scripts/migrate_historical_compact_observations.py)
  reads one snapshot at a time, commits bounded units, checks Windows/log
  capacity before and after units/batches, stores durable checkpoints, decodes
  SQL readback, and marks only exact-parity units `VERIFIED`. A failed unit is
  quarantined; a subset cannot mark the migration `COMPLETE`.
- [validate_compact_observation_parity.py](../../scripts/validate_compact_observation_parity.py)
  compares all normalized fields, identities, duplicate multiplicity, counts,
  and SHA-derived digests in offline or online-read-only mode.
- [approve-compact-observation-gates-v1.sql](../../sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql)
  is the explicit human/admin checkpoint for Power BI compatibility,
  historical readback approval, future-write enablement, and legacy-retirement
  approval. It is a no-op by default.
- [retire-legacy-observation-storage-v1.sql](../../sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql)
  is separate from migration. It requires verified envelopes, exact count
  reconciliation, active-writer absence, reviewed dependencies, preserved
  grants, and the durable approval flags before replacing the eight legacy
  tables with compatibility views. It does not shrink the MDF.
- [verify_current_sql_backup.py](../../scripts/verify_current_sql_backup.py)
  requires `--apply`, an off-C SQL Server-visible destination, measured
  destination free space, `COPY_ONLY, COMPRESSION, CHECKSUM`, and
  `RESTORE VERIFYONLY`.
- [reclaim_sql_datafile_space.py](../../scripts/reclaim_sql_datafile_space.py)
  requires fresh backup evidence, complete parity evidence, a `COMPLETE`
  historical ledger, exact count equality, no active writer/lease, and
  `--apply`. Optional shrink is one named-file, bounded `DBCC SHRINKFILE` only;
  it stops below 12 GiB C: free space.
- [calculate_tiered50_storage_capacity.py](../../scripts/calculate_tiered50_storage_capacity.py)
  accepts measured physical allocation per successful snapshot and reports
  one-day/seven-day growth, the 10 GiB weekly budget, the 8 GiB reserve, the
  12 GiB operational floor, and guard allowance.
- [audit-compact-storage-readonly.sql](../../sql/mdd-mac-collector/audit-compact-storage-readonly.sql)
  provides the bounded current-state audit for files, volume capacity, log,
  indexes/compression, columns, dependencies, procedure drift, payload modes,
  and compact state.

The tracked environment template now leaves both compact future-write flags at
`0`. The seven-day guard and sampling panel are not modified.

## 3. Compatibility contract

The compact views use explicit `OPENJSON ... WITH` typed projections rather than
`JSON_VALUE` for long evidence fields. They retain the legacy column names and
decode historical gzip companion values where a legacy row stored only the
compressed representation. Same-name replacement views and analytics aliases
are installed only by the retirement transaction after dependency and grant
checks. The durable compact analytics views cover source-field coverage and
RecordedAtTime history.

This contract is implemented but not production-refreshed in Power BI in this
task. Therefore compatibility is not marked as production-verified and the
future-write flag remains closed.

## 4. Historical and capacity baseline

The following are the S143 task-provided baseline facts, retained as labelled
inputs rather than re-presented as a fresh SQL read in this session:

| Measure | S143 supplied baseline |
|---|---:|
| TimingObservation rows | 2,487,155 |
| CallObservation rows | 1,309,089 |
| SourceSnapshot rows | 1,879 |
| MDF allocation | approximately 28,790 MiB |
| Windows C: free | 18.983 GiB |
| Tiered50 rate | 220 scheduled requests/hour |
| Seven-day request count | 36,960 |
| Target weekly growth | at most 10 GiB |
| Desired Windows reserve | at least 8 GiB |
| Hard operational floor | 12 GiB |

The prior V3 review measured approximately 7.20 MiB of physical-equivalent
allocation per successful snapshot for the old rowstore representation. At
36,960 snapshots this is 259.875 GiB/week, and is not a compact-envelope
measurement. The compact target corresponding to 10 GiB/week is at most
283.706 KiB per successful snapshot. Actual compact SQL allocation, index bytes,
CPU/readback cost, transaction-log growth, and post-shrink Windows free bytes
remain unmeasured because the SQL Server apply path was not available.

At the supplied 18.983 GiB free baseline, even exactly 10 GiB of weekly growth
would leave about 8.983 GiB, below the hard 12 GiB operational floor. This is
another reason the future-capacity gate cannot be marked PASS until a measured
compact benchmark and an operational capacity decision exist.

## 5. Tested in the isolated repository environment

Passing checks:

```text
python3 -m py_compile <changed Python modules/scripts>
git diff --check
python3 scripts/apply_compact_storage_migration.py
PYTHONPATH=src python3 scripts/validate_storage_v3.py
PYTHONPATH=src python3 scripts/validate_snapshot_gzip_storage_v1.py
PYTHONPATH=src python3 scripts/validate_observation_gzip_storage_v2.py
PYTHONPATH=src python3 -m unittest tests/test_compact_observation_storage.py \
  tests/test_mssql_persistence.py tests/test_windows_sql_backup_validation.py \
  tests/test_windows_sql_result_shape.py tests/test_tiered50_timing_capacity.py
env -u MSSQL_ADMIN_EVIDENCE_PATH PYTHONPATH=src python3 -m unittest \
  discover -s tests -p 'test_*.py'
```

The focused suite completed with `22 tests`, `OK`, and `2 skipped` (the
pre-existing environment-dependent skips). The compact migration static check
reported 25 SQL batches, the fail-closed default, and the required procedure /
table markers. No local SQL Server/sqlcmd compiler is available, so the SQL
objects were not deployed or server-parsed in this task. The clean full suite
completed with `401 tests`, `OK`, and `3 skipped`; the `env -u` only removes an
IDE-provided evidence-path override so the existing path-isolation tests run
against their intended default.

## 6. Production execution and exact blockers

Executed on the production surface in this task: local launchd state inspection
only. The following were not executed: current SQL audit, fresh off-drive
backup, compact schema deployment, historical migration, online parity,
Power BI refresh/readback, legacy retirement, or MDF shrink.

Blocking conditions are explicit:

1. `MSSQL_ADMIN_HOST`, `MSSQL_ADMIN_USER`, and an administrator password/keychain
   item are not available in this environment.
2. A fresh S143-session `COPY_ONLY + CHECKSUM` backup with `RESTORE VERIFYONLY`
   has not been created and evidenced.
3. The exact current before-state is not re-read from SQL Server in this
   session; the task baseline and archived Windows evidence disagree on file /
   volume figures and must be reconciled before cleanup.
4. Historical SQL readback parity, Power BI refresh compatibility, actual
   compact physical allocation, and actual Windows space recovery are therefore
   unverified.

No cleanup was attempted under these gates. The prepared sequence after an
authorized current admin path is: fresh off-C backup and verify; additive
compact schema apply; full bounded historical migration; online exact parity and
Power BI readback; explicit gate approval; then guarded retirement and, only if
measured safe, bounded file shrink.

## 7. Required terminal labels

```text
HISTORICAL_TIMING_ROWS_BEFORE: 2,487,155 (S143 task-provided baseline)
HISTORICAL_TIMING_ROWS_AFTER_LOGICAL: NOT_MEASURED; expected 2,487,155 only after production parity PASS
HISTORICAL_CALL_ROWS_BEFORE: 1,309,089 (S143 task-provided baseline)
HISTORICAL_CALL_ROWS_AFTER_LOGICAL: NOT_MEASURED; expected 1,309,089 only after production parity PASS
HISTORICAL_PARITY: OFFLINE_SERIALIZER_PASS; PRODUCTION_SQL_READBACK_NOT_EXECUTED
COMPACT_ARCHITECTURE: OPTION_B_LOSSLESS_VERSIONED_PER_SNAPSHOT_ENVELOPE_V1
BACKUP_STATUS: BLOCKED; FRESH_CURRENT_OFF_C_CHECKSUM_BACKUP_NOT_CREATED_IN_THIS_SESSION
PRODUCTION_MIGRATION_STATUS: IMPLEMENTED_BUT_NOT_APPLIED; ADMIN_PATH_MISSING
MDF_ALLOCATED_BEFORE_GIB: 28.115 (S143 approximate 28,790 MiB; not freshly remeasured here)
MDF_ALLOCATED_AFTER_GIB: NOT_MEASURED
MDF_USED_AFTER_GIB: NOT_MEASURED
WINDOWS_FREE_BEFORE_GIB: 18.983 (S143 supplied baseline; not freshly remeasured here)
WINDOWS_FREE_AFTER_GIB: NOT_MEASURED
ACTUAL_WINDOWS_SPACE_RECOVERED_GIB: 0.000 (no retirement/shrink performed)
FUTURE_STORAGE_KIB_PER_SNAPSHOT: NOT_MEASURED; target ceiling 283.706 KiB
PROJECTED_7_DAY_GROWTH_GIB: NOT_VERIFIED; target ceiling 10.000 GiB; prior V3 equivalent 259.875 GiB
GUARD_REMAINING_GIB: approximately 10.505 by task-baseline arithmetic only; not reread
POWER_BI_COMPATIBILITY: IMPLEMENTED_TYPED_COMPATIBILITY_VIEWS; PRODUCTION_REFRESH_NOT_VERIFIED
COLLECTOR_STATUS: STOPPED_AND_DISABLED; LOCAL_LAUNCHD_READ_ONLY_GATE_PASS
FINAL_DECISION: IMPLEMENTED_AWAITING_SAFE_PRODUCTION_APPLY
```

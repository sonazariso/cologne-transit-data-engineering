# Tiered50 historical and future storage recovery — S150 correction

Date: 2026-10-14 (Europe/Berlin)

This document continues the existing compact-observation implementation. It
does not create another numbered stage, change the sampling guard, start the
collector, or introduce a replacement architecture.

## Current result

The requested S146/S149/S150 implementation corrections are applied and
offline-tested. No production SQL connection, new backup, production ALTER
INDEX, data migration, DROP, MDF shrink, Power BI refresh, collector start, or
MDD request was performed by this repository.

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
path. The current S150 production evidence supplied for this review reports:

| TimingObservation evidence | Supplied value |
|---|---:|
| Rows | 2,553,690 |
| Total allocation | 12.99 GiB |
| Clustered allocation | 11.87 GiB |
| Clustered compression | `NONE` |
| Windows C: free | 13.92 GiB |
| Mandatory C: floor | 12.00 GiB |
| Headroom above floor | 1.92 GiB |

These values are operator-supplied production evidence, not a result captured
by this Mac or by the corrected audit. They establish that TimingObservation
is the immediate storage priority: its clustered allocation averages roughly
4.87 KiB per row, while the whole table allocation averages roughly 5.33 KiB
per row. They do not identify the responsible columns or authorize a write.
The actual column-byte and index-design result sets added below must be run in
SSMS before assigning savings to scalar fields, source paths, identity fields,
JSON, or indexes.

At 13.92 GiB free, only 1.92 GiB remains above the floor. A full-history
duplicate/rebuild peak is therefore not feasible on the current C: evidence
unless the isolated lab proves a lower peak and the fresh production audit
confirms the same headroom. The future target of 10 GiB growth while retaining
the 12 GiB floor still requires at least 22 GiB free before growth.

`FINAL_RESULT: BLOCKED_PENDING_FULL_HISTORY_CAPACITY_PLAN`

The remaining blocker is missing exact full-history physical capacity evidence:
the production allocation audit and the isolated SQL Server lab measurement
have not been run from this Mac. The implementation now binds the signed plan
to fresh production SQL identity, MDF/LDF and tempdb allocations, the complete
SnapshotId population, migration implementation version, reserve measurements,
and freshness immediately before the first migration write. The operator's
SSMS backup result proves the recovery point, but it is not silently relabeled
as a machine-generated signed artifact.
If a fresh physical-capacity measurement fails its stated thresholds, the
result must instead be `CAPACITY_REQUIREMENTS_NOT_MET`; do not enable future
writes in that case.

If no isolated SQL Server with a non-C: data/log/tempdb volume is available,
the exact infrastructure blocker is:
`NO_SUITABLE_ISOLATED_SQL_SERVER_AND_NON_C_STORAGE_AVAILABLE_FOR_FULL_HISTORY_MEASUREMENT`.
The data-preserving alternative is to keep the verified backup and production
history untouched, reclaim only separately reviewed unrelated Windows files, or
obtain an approved controlled replacement/cutover on an existing non-C: SQL
volume. Do not fabricate a peak, duplicate the database on C:, or authorize a
production migration from JSON-length estimates.

The next safe operator action is the consolidated read-only storage audit
described below. The backup verification step is complete and must not be
rerun unnecessarily.

### S150 Timing storage findings and VMware feasibility

The source-side review is now explicit about the physical design. The Timing
table repeats the same scalar identifiers across observations, carries several
source-path columns, carries source-event identity and identity-audit JSON,
and has a clustered identity index and additional occurrence/identity access
paths. Repeated values may be good dictionary candidates, but the candidate
must retain original typed values, NULL versus empty, Unicode, timestamps and
source timestamp text, GZIP bytes, row identity, and duplicate occurrences. A
row count or JSON-length estimate is not sufficient.

The read-only production audit now returns:

- `capacity.timing_column_bytes`: `DATALENGTH` totals, NULL/non-NULL counts,
  average non-NULL byte length, category, and index key/INCLUDE membership for
  every physical Timing column;
- `capacity.timing_index_design`: the actual key order, INCLUDE membership,
  uniqueness/filter flags, compression, and reserved/used allocation for each
  Timing index; and
- `capacity.vmware_relocation_assessment`: the C: data/log/tempdb files,
  their allocated bytes, and the target free-space requirement if user-
  database files are moved.

No per-column savings number is claimed yet because the production audit has
not been rerun after this source change. The isolated comparison is provided
by `sql/mdd-mac-collector/measure-timing-storage-options-lab.sql`. It creates
disposable full-history lab candidates and measures actual reserved pages for
lossless PAGE-compressed source-shaped storage and a lossless dictionary
layout. It returns the actual comparison in
`capacity.timing_storage_option_savings`, plus exact fixed-column/dictionary
byte mismatches and NULL/empty metrics. Its `OBSERVED_CHECKPOINT_MAX` is not a
production peak authorization; a separate sampler must capture the in-flight
CREATE/ALTER INDEX high-water mark and the result must be bound to the signed
capacity plan.

The measured-savings ledger for this review is therefore:

| Decision quantity | Current result | Required evidence |
|---|---|---|
| Timing source-shaped PAGE allocated pages/bytes | `NOT_MEASURED` | Lab `capacity.timing_storage_option_allocation` |
| Timing dictionary allocated pages/bytes | `NOT_MEASURED` | Lab `capacity.timing_storage_option_allocation` |
| PAGE savings versus uncompressed source shape | `NOT_MEASURED` | Lab `capacity.timing_storage_option_savings` |
| Dictionary savings versus PAGE | `NOT_MEASURED` | Lab `capacity.timing_storage_option_savings` |
| Full-history migration peak | `NOT_MEASURED` | In-flight sampler plus signed before/after capture |
| C: bytes recovered by VMware relocation | `NOT_MEASURED` | Windows/SQL before-after file and volume evidence |

Consequently the current feasibility decision is `BLOCKED`: 1.92 GiB of
supplied C: headroom is not a measured full-history peak and cannot authorize
an ALTER INDEX, migration, DROP, SHRINK, or restart.

Adding a separate VMware virtual disk is technically feasible only after the
Windows/VMware administrator confirms a persistent non-C: VMDK, NTFS health,
SQL Server service-account access, backup coverage, and enough free space for
the complete allocated data/log files plus copy/rollback reserve. Attaching a
VMDK alone recovers no C: space. Moving user-database MDF/LDF files requires
an approved maintenance window, a verified backup, collector quiescence, an
offline/file-state move, SQL metadata updates, service-account permissions,
and post-move SQL allocation and application checks. Moving tempdb is a
separate planned SQL restart and is not authorized here. The current review
performed none of those operations and has not measured a positive C: delta.

The one recommended safe execution path is: provision and validate the
separate non-C: VMDK; use it for the isolated full-history restore and the
PAGE-versus-dictionary measurement; choose the option only from actual page
and peak evidence; then, in a separately approved maintenance window, move
the existing user-database data/log files to that volume and verify the C:
before/after delta before considering any compact migration, legacy retirement,
or bounded shrink. Until all of those gates pass, keep production history and
the verified backup untouched.

### Production-audit errors observed and corrected

The operator-observed read-only production-audit attempt returned Msg 207
because the current V1/V2 schema does not install the optional Storage V3
GZIP columns used by the original snapshot and representative-payload
queries. The same audit also referenced the nonexistent
`sys.sql_expression_dependencies.referenced_minor_name` field.

The existing audit file is corrected in place. It now inventories the expected
payload columns through `sys.columns`, reports each absent optional column as
`MISSING_OPTIONAL`, and builds the length-only payload expressions only from
physical columns confirmed present. The snapshot population result uses the
same guard, and the dependency result derives the minor name with
`COL_NAME(referenced_id, referenced_minor_id)`. An offline regression models a
V1/V2-only database with every V3 GZIP column absent. No V3 installation,
schema change, data migration, collector restart, or production rerun was
performed; rerun the unchanged read-only audit after this source review.

A follow-up audit attempt exposed Msg 8115 in both 12 GiB safety-floor
expressions: SQL Server evaluated `12 * 1024 * 1024 * 1024` as `INT` before
the outer conversion. Both occurrences now cast `12` to `BIGINT` before
multiplication. The remaining page-to-byte products were reviewed and already
cast their page operands before multiplying by 8192; the regression check
guards both corrected floor expressions and rejects the old overflow pattern.

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
  Before the first write, a fresh read-only SQL binding rechecks identity,
  MDF/LDF growth limits, tempdb, the complete ordered SnapshotId population,
  installed migration version, evidence freshness, log/rollback/transient
  reserves, current Windows headroom, and the selected volume policy. Any
  mismatch blocks without writing a migration ledger row.
- The consolidated historical-storage audit now survives the observed V1/V2
  production schema: `sys.columns` reports the physical payload contract and
  missing optional GZIP columns, the representative-size query references only
  installed columns, and dependency minor names use `COL_NAME(...)`.
- The audit's two 12 GiB floor calculations now cast before multiplication,
  preventing Msg 8115 from intermediate `INT` overflow; all remaining
  page-to-byte capacity products retain BIGINT operands.
- The audit now measures actual `DATALENGTH` bytes for every physical Timing
  column, reports NULL/non-NULL and average-value sizes, exposes Timing index
  key/INCLUDE membership and allocation, and reports the C: file allocation
  that a separate VMware disk could receive. No result is treated as a
  production capacity plan until captured from SQL Server.
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
- `sql/mdd-mac-collector/estimate-storage-v3-compression.sql` remains a
  disabled-by-default bounded diagnostic for compact envelopes and payload
  candidates. The new
  `sql/mdd-mac-collector/measure-timing-storage-options-lab.sql` is the
  full-history Timing comparison: it creates disposable source-shaped PAGE
  and dictionary candidates, measures actual reserved pages and data/log/
  tempdb checkpoints, and verifies exact fixed/variable-field round trips.
  `scripts/sample_timing_storage_peak_lab.py` supplies the separate read-only
  in-flight high-water measurement required before capacity authorization.
- `sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql` now requires
  Power BI confirmation before either future writes or retirement approval and
  no longer clears a previously recorded retirement approval on a later gate
  run.
- `sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql` verifies
  replacement object types and all eight compatibility-view counts before its
  destructive transaction commits. It now also requires measured log, tempdb,
  rollback, DDL/index, autogrowth, and unrelated-disk reserves, checks live
  physical headroom with the 12 GiB C: floor, and blocks finite growth limits
  or active transactions. The Python retirement runner additionally requires
  full-history parity, a fresh real Power BI consumer artifact, dependency and
  schema-contract checks, and explicit human approval.
- The isolated-lab path now uses only `CTI_LAB_MSSQL_*` settings and a separate
  lab Keychain service/identity. It rejects the production host, collector
  login, production evidence key, and C: SQL data/log/tempdb placement. Lab
  credentials and evidence are never accepted by the production path.
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
| `<WINDOWS_C_FREE_GIB>` | `13.92` GiB supplied S150 production evidence | Re-measure in SSMS; only 1.92 GiB is above the 12 GiB floor |
| `<BACKUP_DEST_FREE_GIB>` | Fresh Windows measurement of the confirmed UNC destination | Must cover the expected backup size plus the operator-approved reserve; `xp_fileexist` proves reachability, not free capacity |
| `<MIGRATION_ID>` | New UUID recorded by the operator, or the existing failed/blocked UUID being intentionally resumed | Never reuse a UUID for unrelated history |
| `<CAPACITY_PLAN_FILE>` | JSON file made from measured SQL allocation, log, tempdb, rollback, autogrowth, and duplication peaks plus signed evidence bindings | Must cover `FULL_HISTORY_MIGRATION_PEAK`; self-declared numbers are rejected |
| `<CAPACITY_EVIDENCE_PUBLIC_KEY>` | Operator-controlled public key outside the repository | Must verify the signed SQL allocation evidence embedded in the capacity plan |
| `<LAB_MSSQL_HOST>` | Explicit isolated SQL Server host, different from `DataAnalyst-VM` | Set through `CTI_LAB_MSSQL_HOST`; never reuse `MSSQL_ADMIN_HOST` |
| `<LAB_MSSQL_INSTANCE_ID>` | Non-production lab instance identifier | Set through `CTI_LAB_MSSQL_INSTANCE_ID`; values containing `production` are rejected |
| `<LAB_CAPACITY_EVIDENCE_PUBLIC_KEY>` | Lab-only public key outside the repository | Never pass it through `MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY` or the production command |
| `<MDF_LOGICAL_FILE_NAME>` | Exact `sys.database_files.name` returned by SSMS | Never guess the logical file name |
| `<TARGET_SIZE_MB>` | Calculated from current used pages plus explicit headroom | Never substitute `SHRINKDATABASE` |
| `<POWERBI_APPROVER>` | Human who verified the real consumer refresh | Record outside SQL and in the retirement approval artifact |
| `<POWERBI_EVIDENCE_JSON>` | Fresh PASS artifact from the real report/dataset refresh | Must identify the consumer, dataset/report, refresh, schema contract, approver, and timestamp |
| `<RETIREMENT_RESERVES>` | Fresh measured log/tempdb/rollback/DDL-index/autogrowth/external-disk reserve object | All six byte reserves must be positive, PASS, fresh, and based on the isolated-lab plus production read-only measurements |

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
- `capacity.snapshot_population`, `capacity.snapshot_ids_complete`, and
  `capacity.historical_row_counts` for the complete history;
- `capacity.migration_implementation` and the database GUID/instance identity
  used to bind the evidence to the intended SQL Server;
- `capacity.representative_snapshot_selection` and
  `capacity.representative_observation_sizes`, which are bounded length-only
  diagnostics and are not a physical compact-envelope allocation claim;
- `capacity.representative_payload_schema` and
  `capacity.representative_payload_audit`, which identify installed payload
  columns and explicitly report missing optional Storage V3 columns;
- `capacity.timing_column_bytes`, `capacity.timing_index_design`, and
  `capacity.vmware_relocation_assessment` for the Timing storage decision;
- `capacity.transaction_log`, `capacity.tempdb`, `capacity.rollback`, and
  `capacity.autogrowth`;
- `capacity.active_writer_state`, the compact gate state, and the server-side
  column/dependency/foreign-key/module result sets.

Expected outcome: the identity is the intended database, status is `ONLINE`,
access is `MULTI_USER`, no collector run/lease/session or unrelated user
transaction is active, every required source table is present, and no required
allocation field is NULL. Any missing result, incomplete file measurement,
active writer, incomplete SnapshotId list, stale timestamp, migration-version
mismatch, or C: free-space result below 12 GiB is `BLOCKED`.

The supplied 13.92 GiB value is not reused as current evidence. At that value
the hard available headroom is only `13.92 - 12.00 = 1.92 GiB`; a migration
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

Use the existing scripts in this order on the isolated lab copy. The scripts
run from macOS through the explicit lab SQL connection; they do not use the
launchd production gate or the production administrator Keychain item. Set
only the separated lab variables (the password may come from the separate
`com.cologne-transit-intelligence.mssql-lab-admin-password` Keychain item):

```text
CTI_LAB_MSSQL_HOST=<LAB_MSSQL_HOST>
CTI_LAB_MSSQL_DATABASE=CologneTransitIntelligence
CTI_LAB_MSSQL_USER=<LAB_ADMIN_LOGIN>
CTI_LAB_MSSQL_INSTANCE_ID=<LAB_MSSQL_INSTANCE_ID>
CTI_LAB_MSSQL_PASSWORD=<LAB_PASSWORD_IF_NOT_USING_LAB_KEYCHAIN>
```

The lab host must be different from `DataAnalyst-VM`, and all lab SQL
data/log/tempdb files must remain on the existing non-C: volume. If these
settings or the non-C: volume do not exist, stop with the exact blocker given
in **Current result**; do not substitute the production host or a second C:
volume.

Use the existing scripts in this order on the isolated lab copy:

1. Run the additive compact-storage script through the existing lab runner,
   with `--execution-environment isolated-lab` and only its apply flag, while
   keeping future writes disabled. The runner performs the same non-C: and
   active-writer checks before sending the guarded batches.

   ```text
   python3 scripts/apply_compact_storage_migration.py --apply \
     --execution-environment isolated-lab \
     --backup-evidence <LAB_BACKUP_EVIDENCE_JSON>
   ```
2. Use the existing `scripts/migrate_historical_compact_observations.py`
   serializer and stored procedure against the lab copy, with `--batch-size 1`
   and no snapshot limit. A deliberately overprovisioned, lab-only signed
   preflight plan may be used for this first measurement run; it must never be
   promoted to production evidence. From the authorized lab migration host,
   the command shape is:

   ```text
   CTI_LAB_MSSQL_HOST=<LAB_MSSQL_HOST> \
   CTI_LAB_MSSQL_DATABASE=CologneTransitIntelligence \
   CTI_LAB_MSSQL_USER=<LAB_ADMIN_LOGIN> \
   CTI_LAB_MSSQL_INSTANCE_ID=<LAB_MSSQL_INSTANCE_ID> \
   python3 scripts/migrate_historical_compact_observations.py --apply \
     --execution-environment isolated-lab \
     --migration-id <LAB_MIGRATION_ID> --batch-size 1 \
     --capacity-plan <LAB_CAPACITY_PLAN_FILE> \
     --capacity-evidence-public-key <LAB_PUBLIC_KEY> \
     --backup-evidence <LAB_BACKUP_EVIDENCE_JSON> --json
   ```

   The lab evidence and key remain separate from the production plan and key.
3. During the lab run, retain every per-unit pre/post allocation result and
   record the maximum data-file, log, and tempdb allocation observed. Run
   `sql/mdd-mac-collector/measure-timing-storage-options-lab.sql` only in the
   isolated lab database, changing only `@RunLab = 1`, to compare actual
   reserved pages for lossless PAGE compression and dictionary storage across
   the complete Timing history. In a second lab terminal, run the read-only
   sampler for the whole operation window:

   ```text
   CTI_LAB_MSSQL_HOST=<LAB_MSSQL_HOST> \
   CTI_LAB_MSSQL_DATABASE=CologneTransitIntelligence \
   CTI_LAB_MSSQL_USER=<LAB_ADMIN_LOGIN> \
   CTI_LAB_MSSQL_INSTANCE_ID=<LAB_MSSQL_INSTANCE_ID> \
   python3 scripts/sample_timing_storage_peak_lab.py \
     --duration-seconds <LONGER_THAN_THE_LAB_RUN> \
     --interval-seconds 1 \
     --output <LAB_PEAK_SAMPLER_JSON>
   ```

   The sampler is read-only, rejects the production host and C: placement,
   and records the in-flight data/log/tempdb high-water mark. The SQL script
   labels its post-operation high-water mark `OBSERVED_CHECKPOINT_MAX` until
   this sampler evidence is captured. The
   older `estimate-storage-v3-compression.sql` remains a bounded diagnostic;
   its logical payload lengths are not physical production allocation
   evidence.
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

If the lab prerequisite itself cannot be met, record
`INFRASTRUCTURE_BLOCKER: NO_SUITABLE_ISOLATED_SQL_SERVER_AND_NON_C_STORAGE_AVAILABLE_FOR_FULL_HISTORY_MEASUREMENT`
and keep the verified backup and all production history intact. The practical
alternative is a separately authorized non-C: replacement/cutover or reviewed
unrelated-file cleanup; it is not permission to estimate the peak or migrate
on production.

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
  "execution_environment": "production",
  "database_identity": {
    "server_name": "<SERVERPROPERTY_SERVER_NAME>",
    "instance_name": "<SERVERPROPERTY_INSTANCE_NAME>",
    "database_name": "CologneTransitIntelligence",
    "database_id": <DB_ID>,
    "database_guid": "<sys.database_recovery_status.database_guid>"
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
    "last_snapshot_id": <last_snapshot_id>,
    "snapshot_ids": [<every_current_snapshot_id_in_ascending_order>],
    "snapshot_ids_sha256": "<sha256_of_compact_snapshot_id_array>"
  },
  "snapshot_ids_sha256": "<same_population_hash>",
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
  "external_disk_consumption_reserve_bytes": <measured_bytes>,
  "reserve_measurements": {
    "transaction_log_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "tempdb_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "rollback_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "index_overhead_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "autogrowth_reserve_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "peak_temporary_duplication_bytes": {"status": "PASS", "bytes": <same_measured_bytes>},
    "external_disk_consumption_reserve_bytes": {"status": "PASS", "bytes": <same_measured_bytes>}
  },
  "physical_allocation_deltas": {
    "envelope_allocation_bytes": <same_measured_bytes>,
    "index_overhead_bytes": <same_measured_bytes>,
    "transaction_log_bytes": <same_measured_bytes>,
    "tempdb_bytes": <same_measured_bytes>,
    "rollback_bytes": <same_measured_bytes>,
    "autogrowth_reserve_bytes": <same_measured_bytes>,
    "peak_temporary_duplication_bytes": <same_measured_bytes>
  },
  "peak_additional_bytes": <exact_sum_of_the_seven_components_plus_external_reserve>,
  "independent_sql_evidence": {
    "artifact_version": "CTI_SQL_PHYSICAL_ALLOCATION_EVIDENCE_V1",
    "status": "PASS",
    "capture_provenance": {
      "read_only": true,
      "execution_environment": "production",
      "server_name": "<same_server_name>",
      "database_name": "CologneTransitIntelligence",
      "migration_implementation_version": "compact-historical-migration-v2"
    },
    "sample_allocation_before": {
      "database_file_allocated_bytes": <measured_bytes>,
      "log_file_allocated_bytes": <measured_bytes>,
      "tempdb_file_allocated_bytes": <measured_bytes>,
      "transaction_log_used_bytes": <measured_bytes>,
      "windows_target_volume_available_bytes": <measured_bytes>
    },
    "sample_allocation_after": {
      "database_file_allocated_bytes": <measured_bytes>,
      "log_file_allocated_bytes": <measured_bytes>,
      "tempdb_file_allocated_bytes": <measured_bytes>,
      "transaction_log_used_bytes": <measured_bytes>,
      "windows_target_volume_available_bytes": <measured_bytes>
    },
    "query_results": [
      {"statement_id": "capacity.database_identity", "status": "PASS", "executed": true, "result_sha256": "<64_hex_chars>"},
      {"statement_id": "capacity.snapshot_ids_complete", "status": "PASS", "executed": true, "result_sha256": "<64_hex_chars>"},
      {"statement_id": "capacity.migration_implementation", "status": "PASS", "executed": true, "result_sha256": "<64_hex_chars>"}
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
`capacity.snapshot_ids_complete`, `capacity.migration_implementation`,
`capacity.sample_allocation_before`, `capacity.sample_allocation_after`,
`capacity.transaction_log`, `capacity.tempdb`, `capacity.rollback`,
`capacity.autogrowth`, `capacity.timing_column_bytes`,
`capacity.timing_index_design`, and `capacity.vmware_relocation_assessment`.
Capture each result in SSMS using the existing
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
  --execution-environment production \
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
available_headroom_bytes = windows_target_volume_available_bytes - (12 * 1024 * 1024 * 1024)
allow_migration = peak_additional_bytes <= available_headroom_bytes
```

Also require the log, tempdb, finite-autogrowth, rollback, active-writer, and
collector gates to pass. With the supplied 13.92 GiB value,
`available_headroom_bytes` is approximately 1.92 GiB; if the signed full-
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

The supported retirement runner accepts a separate fresh JSON artifact only
when it contains `status = PASS`, `approved = true`,
`evidence_source = REAL_POWER_BI_CONSUMER`, `refresh_verified = true`,
`schema_contract_status = PASS`, non-empty `consumer_id`,
`dataset_or_report`, `approved_by`, and a `validated_at_utc` no more than 30
minutes old at apply time. A SQL flag or repository view query alone cannot
produce this artifact.

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

The supported Python path is the preferred execution path because it binds the
fresh Power BI artifact and full-history parity result before connecting:

```text
python3 scripts/reclaim_sql_datafile_space.py --apply \
  --backup-evidence <EXISTING_VERIFIED_BACKUP_EVIDENCE_JSON> \
  --parity-evidence <FULL_HISTORY_EXACT_PARITY_JSON> \
  --approval-evidence <HUMAN_RETIREMENT_APPROVAL_WITH_CAPACITY_RESERVES_JSON> \
  --powerbi-evidence <POWERBI_EVIDENCE_JSON> \
  --migration-id <COMPLETE_MIGRATION_ID>
```

The approval artifact's `capacity_reserves` must contain fresh, measured,
positive PASS byte values for `transaction_log_reserve_bytes`,
`tempdb_reserve_bytes`, `rollback_reserve_bytes`,
`ddl_index_reserve_bytes`, `autogrowth_reserve_bytes`, and
`external_disk_consumption_reserve_bytes`, with measurement basis
`FULL_HISTORY_ISOLATED_LAB_AND_PRODUCTION_READONLY_AUDIT`. The runner measures
current SQL file/log/tempdb/legacy allocation again and refuses the DROP if
any reserve, dependency, permission, schema, active-transaction, or C: floor
check fails.

For a direct reviewed SSMS working copy, replace the six `NULL` values in the
retirement script's `@Retirement*ReserveBytes` declarations with those same
measured values. Leaving any one `NULL`, using counts as a substitute, or
omitting the live preflight keeps the script blocked.

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
with explicit approval for the physical shrink. The supported Python runner
repeats the live log, tempdb, rollback, DDL/index, autogrowth, external-disk,
and C: floor preflight immediately before `DBCC SHRINKFILE`; use that path when
possible. If SSMS is used, repeat the same six measured reserve checks in the
working copy immediately before the DBCC statement. First identify the exact
file and current measurements:

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
this repository. A successful retirement or a successful SQL transaction is
not itself physical recovery: record the before/after Windows free-space and
allocated-file measurements, and leave the final problem status
`NOT_SOLVED_UNTIL_POSITIVE_PHYSICAL_DISK_RECOVERY_MEASURED` when the delta is
zero or not captured.

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
- Timing PAGE-versus-dictionary lab measurement:
  `sql/mdd-mac-collector/measure-timing-storage-options-lab.sql`, default
  `@RunLab = 0`; it is the only file in this recovery path that creates
  disposable comparison tables, and it is guarded to reject production/C:
  placement.
- Read-only in-flight lab sampler:
  `scripts/sample_timing_storage_peak_lab.py`; it requires the separate
  `CTI_LAB_MSSQL_*` connection settings and writes evidence only to the
  operator-selected output path.
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
PYTHONPATH=src:scripts python3 -m unittest tests.test_compact_observation_storage \
  tests.test_windows_sql_backup_validation tests.test_windows_sql_result_shape \
  tests.test_windows_sql_dbnull tests.test_production_repairs \
  tests.test_tiered50_timing_capacity tests.test_s149_storage_recovery -q    63 PASS, 3 skipped
python3 scripts/validate_snapshot_gzip_storage_v1.py                         PASS
PYTHONPATH=src python3 scripts/validate_mssql_migration.py                   PASS
PYTHONPATH=src python3 scripts/validate_storage_v3.py                         PASS
static schema-aware read-only audit keyword/statement-id checks             PASS
PYTHONPATH=src:scripts python3 -m unittest discover -s tests -q                432 run; PASS, 3 skipped
git diff --check                                                             PASS
```

The evidence-path tests explicitly isolate the operator-configured external
artifact location, so the full offline suite does not write to or depend on a
user-level evidence path. These are source/static and offline results, not SQL
Server or Power BI results. No production mutation was executed.

## Final labels

```text
SOURCE_CODE_VALIDATION: PASS; 432 OFFLINE TESTS PASS, 3 SKIPPED; NO PRODUCTION CONNECTION
IMPLEMENTATION_DEFECTS: S150_TIMING_BYTE_AUDIT_PAGE_DICTIONARY_LAB_AND_VMWARE_ASSESSMENT_FIXED_IN_SOURCE
ISOLATED_SQL_SERVER_VALIDATION: NOT_EXECUTED; FULL_HISTORY_LAB_CAPTURE_AND_PARITY_REQUIRED
SQL_SERVER_PRODUCTION_MEASUREMENTS: STORAGE_AUDIT_ATTEMPT_OBSERVED_MSG_207; CORRECTED_SOURCE_NOT_RERUN; SSMS_BACKUP_VERIFYONLY_OPERATOR_CONFIRMED_ONLY
PRODUCTION_AUDIT_OBSERVED_ERRORS: MSG_207_OPTIONAL_STORAGE_V3_GZIP_COLUMNS; MSG_207_INVALID_REFERENCED_MINOR_NAME; MSG_8115_INT_CAPACITY_FLOOR_ARITHMETIC
PRODUCTION_AUDIT_FIX: SCHEMA_AWARE_READ_ONLY_SOURCE_APPLIED; BIGINT_FLOOR_ARITHMETIC; V1_V2_ONLY_REGRESSION_ADDED; NOT_RERUN
FRESH_VERIFIED_OFF_C_BACKUP: OPERATOR_CONFIRMED_SSMS_RESTORE_VERIFYONLY_PASS; MACHINE_ARTIFACT_NOT_CAPTURED
WINDOWS_C_FREE_AT_LAST_CHECK: 13.92_GIB_SUPPLIED_EVIDENCE; 12_GIB_FLOOR_LEAVES_1.92_GIB_HEADROOM
CONSOLIDATED_STORAGE_AUDIT: SOURCE_PATCHED_AFTER_OBSERVED_MSG_207; NOT_RERUN
COMPACT_SCHEMA_INSTALLATION: NOT_EXECUTED
BOUNDED_HISTORICAL_MIGRATION: NOT_EXECUTED
EXACT_FULL_HISTORICAL_PARITY: NOT_EXECUTED
TIMING_COLUMN_BYTE_MEASUREMENT: SOURCE_ADDED; PRODUCTION_AUDIT_NOT_RERUN
TIMING_PAGE_VS_DICTIONARY: LAB_SCRIPT_AND_READ_ONLY_SAMPLER_ADDED; NOT_EXECUTED
HISTORICAL_COMPRESSION_EFFICIENCY: OFFLINE_LOGICAL_CANDIDATES_PASS; SQL_PHYSICAL_LAB_NOT_EXECUTED
POWER_BI_COMPATIBILITY: NOT_VERIFIED
LEGACY_RETIREMENT: NOT_EXECUTED
PHYSICAL_WINDOWS_DISK_RECOVERY: NOT_EXECUTED; NO_POSITIVE_BEFORE_AFTER_FREE_SPACE_DELTA_MEASURED
PEAK_MIGRATION_SPACE: NOT_MEASURED; IN_FLIGHT_SAMPLER_NOT_EXECUTED; 1.92_GIB_SUPPLIED_HEADROOM_REQUIRES_FRESH_RECHECK
VMWARE_SEPARATE_DISK: FEASIBILITY_ASSESSED; ATTACHMENT_RELOCATION_AND_C_FREE_DELTA_NOT_EXECUTED
COLLECTOR_STATUS: DISABLED_AND_UNLOADED; NOT_RESTARTED
FINAL_RESULT: BLOCKED_PENDING_FULL_HISTORY_CAPACITY_PLAN_AND_ISOLATED_LAB_VALIDATION
PROBLEM_SOLVED: NO; DO_NOT_CLAIM_SOLVED_UNTIL_ACTUAL_WINDOWS_DISK_RECOVERY_IS_MEASURED
```

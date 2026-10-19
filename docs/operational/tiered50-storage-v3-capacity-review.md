# Tiered50 Storage Optimization V3 capacity review

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Review date:** 2026-10-13 (Europe/Berlin)  
**Decision:** `STORAGE_V3_CAPACITY_DECISION: NO_GO`  
**Scope:** repository implementation and bounded offline/read-only validation only. No production deployment was authorized or performed.

## Executive decision

The repository now contains a disabled-by-default, additive V3 future-write contract. It compresses the remaining nullable `MAX` source-evidence fields only when GZIP is smaller than the original UTF-16 value, and exposes those fields through backward-compatible readback views. Four sanitized fixtures passed exact offline reconstruction, including NULL/empty handling, repeated observation grain, source identity, `EstimatedTime`, and `RecordedAtTime` preservation.

The seven-day capacity gate is **not supported by measured SQL Server evidence**. SQL Server ROW/PAGE allocation savings, V3 trigger CPU/write overhead, and final allocated bytes were not measured because no isolated SQL Server lab was available. The observed baseline of approximately 7.20 MiB per successful snapshot extrapolates to 259.875 GiB for 36,960 successful requests, versus the 10 GiB weekly limit. The existing compressed snapshot payload alone averages 226.3 KiB in a bounded live sample, leaving about 57.4 KiB per scheduled request for all normalized rows, indexes, and log growth.

The V3 implementation therefore remains source-only and disabled. This is a capacity NO_GO, not authorization to change the collector or database.

## 1. Production safety and collector status

- Both LaunchAgents were confirmed disabled with `launchctl print-disabled`.
- Both collector labels were absent from the user GUI launchd domain.
- No collector process was found; the `pgrep` result was only the search command itself.
- The seven-day guard and all existing runtime files were left unchanged.
- Production SQL access used only bounded metadata/length-only `SELECT` statements. No production DDL, DML, backfill, index operation, shrink, restore, or stored-procedure replacement was run.
- No live MDD request was made.
- No password, API key, Keychain value, or raw payload was printed or persisted.

The worktree already contained S141 storage files, evidence artifacts, and an unrelated activation-script modification. Those changes were preserved.

## 2. Stage 1 — current storage audit

### Evidence distinction

The task supplied an operational snapshot of 28,790.25 MiB allocated MDF, 28,787.25 MiB used MDF, 19.36 GiB free Windows space, and table sizes of 2,487,155 timing rows, 1,309,089 call rows, and 1,879 snapshots. These are retained as historical/task-provided evidence.

A bounded live read-only probe during this review returned newer values. They are not silently substituted for the supplied baseline:

| Object | Current live rows | Reserved | Clustered allocation | Secondary indexes | Compression |
|---|---:|---:|---:|---:|---|
| `stg.MddMacTimingObservation` | 2,553,690 | 13,304.62 MiB | 12,157.25 MiB | 1,147.38 MiB | `NONE` |
| `stg.MddMacCallObservation` | 1,344,108 | 5,541.00 MiB | 5,159.25 MiB | 381.75 MiB | `NONE` |
| `stg.MddMacSourceSnapshot` | 1,927 | 6,045.46 MiB | 6,045.18 MiB | 0.28 MiB | `NONE` |

The three tables occupy 24,891.09 MiB reserved in the live metadata view. Snapshot allocation is almost entirely LOB; call and timing allocation is predominantly in-row. All inspected clustered, unique, and secondary indexes reported `NONE` compression.

Current live database-file and volume observations:

- MDF allocated: 29,174.25 MiB; used: 29,134.00 MiB.
- LDF allocated: 1,864.00 MiB; used: 443.65 MiB.
- Windows `C:\` available: 20,382,420,992 bytes = 18.983 GiB.
- The task-provided free-space value was 19.36 GiB; the live value is lower and is used for the conservative capacity decision.

The current live schema exposed the V1/V2 GZIP columns and readback views. A bounded sample of the newest rows used GZIP for `SnapshotJson`, call source evidence, and timing source evidence. The installed persistence procedure had SHA-256 `4e2b36cab192f67ebc4c8d5649b983c158d3c4883253fa4b1b30425a08c51679`, while the repository’s current V2 canonical procedure is `02f19a5e50390d2fe344af92405a019abcc816a8e838a26a8bc4c616bd7a9a33`. This is an unresolved deployment drift gate; V3 does not replace that procedure.

The bounded historical run evidence contains 31 Tiered50 runs: 30 `COMPLETE` and one `COMPLETED_WITH_COVERAGE_GAP`. It is not a full seven-day byte-growth series and is not used as proof of a weekly storage rate.

## 3. Stage 2 — ranked storage bottlenecks

1. **Timing observation rows and their uncompressed indexes.** The timing table is 53.5% of the three-table reserved footprint. It repeats immutable identifiers, multiple source-path columns, timing-source strings, and the compressed `SourcePathsJson` projection. V2 does not compress the individual source-path and identifier columns.
2. **Historical/full source snapshots.** The snapshot table is 24.3% of the three-table footprint and is mostly LOB. Snapshot GZIP reduces future payload bytes, but legacy plaintext history remains and the source snapshot must remain available alongside normalized evidence.
3. **Call observation rows.** The call table is 22.3% of the three-table footprint. It repeats journey, line, station, origin/destination, display, path, and source-event values across observations. V2 compresses two large JSON fields but does not normalize or dictionary-code the repeated scalar text.
4. **Secondary indexes.** The inspected call, timing, and snapshot secondary indexes consume about 1,529.4 MiB combined and are all uncompressed. Page compression or index redesign could matter, but neither was measured or applied.

Identical values were treated as compression/deduplication opportunities only; repeated observation records remain distinct by their original snapshot and occurrence keys. No proposal collapses observations or infers actual arrival/departure times from estimates.

## 4. Stage 3 — lossless optimization experiments

The offline benchmark models SQL Server `COMPRESS(NVARCHAR)` as GZIP over UTF-16LE for logical round-trip and size checks. It does **not** claim SQL Server allocated-byte results.

| Candidate | Evidence/result | Decision |
|---|---|---|
| ROW compression | SQL-specific result: `NOT_MEASURED`; lab procedure prepared | Do not claim savings or deploy |
| PAGE compression | SQL-specific result: `NOT_MEASURED`; lab procedure prepared | Highest-priority lab experiment for repeated in-row values/indexes, not production-ready |
| Remaining textual-field GZIP | Four fixtures: selected V3 fields 17,142 logical bytes to 4,746 stored bytes after avoiding expansion, 72.31% logical savings | Implemented as disabled future-write repository contract |
| Shared dictionaries | Optimistic logical estimate for selected identifiers was 38.37%–72.54% per fixture; no SQL row/index overhead included | Not implemented; requires a separate dimension/reference contract and SQL measurement |
| Delta encoding | No representative consecutive production samples or proven base-chain failure model available | Rejected for V3; exact observation/snapshot reconstruction risk is too high |

Additional bounded fixture results:

- Whole sanitized snapshots: 263,162 UTF-16LE bytes to 12,086 GZIP bytes, 95.41% logical savings.
- Existing V2-selected evidence fields: 80,488 logical bytes to 12,951 GZIP bytes, 83.91% logical savings.
- These are small sanitized logical samples, not production allocation, index, tempdb, log, CPU, or read-latency measurements.

The selected V3 combination is therefore: retain V1/V2, add future-write GZIP for remaining nullable `MAX` source evidence with an anti-expansion check, and measure ROW/PAGE separately in an isolated lab. It is not sufficient evidence for a GO decision.

## 5. Stage 4 — repository implementation

The canonical schema now includes V3 nullable `VARBINARY(MAX)` companions for:

- timing identity-missing and identity-ambiguity JSON;
- situation description/detail and affected-entity/path evidence;
- service-section description/route/via/mode/path evidence;
- attribute text/path/representation evidence; and
- parser-gap source representation.

Five `AFTER INSERT` triggers compress those fields within the caller’s transaction only when the compressed representation is smaller. Four explicit V3 readback views and the timing readback view use `COALESCE(plaintext, DECOMPRESS(gzip))`, preserving legacy plaintext rows and exposing the same logical columns to consumers. The persistence procedure signature, run finalization, uniqueness constraints, sampling policy, quota behavior, `EstimatedTime`, and `RecordedAtTime` handling are unchanged.

`sql/mdd-mac-collector/optimize-storage-v3.sql` is additive and idempotent, checks V1/V2 prerequisites, opens with `@ApplyStorageV3 = 0`, and contains no historical backfill or index operation. It cannot change the database unless an operator edits the explicit gate during a separately authorized deployment.

Offline validation passed:

- 4 sanitized TRIAS fixtures;
- row-family parity for calls, timings, situations, service sections, attributes, parser gaps, and response errors;
- field-by-field lossless reconstruction;
- NULL versus empty-string behavior;
- repeated-observation identity/grain preservation across two snapshots;
- exact preservation of source identity and timing fields; and
- V1/V2 validators and the targeted MSSQL persistence unit tests.

SQL-level new-write idempotency, trigger CPU, transaction-log behavior, rollback after an actual trigger error, and Power BI execution against SQL Server were not executed because the isolated lab is unavailable. The repository contract preserves the existing unique keys and keeps the triggers inside the existing transaction, but that is not a measured SQL PASS.

## 6. Stage 5 — seven-day capacity validation

Deterministic schedule arithmetic:

| Quantity | Value |
|---|---:|
| Scheduled requests/hour | 220 |
| Scheduled requests/day | 5,280 |
| Scheduled requests/week | 36,960 |
| Seven-day budget | 10.000 GiB |
| Maximum total incremental bytes/request | 290,515 bytes |
| Maximum total incremental KiB/request | 283.7 KiB |

Capacity comparison:

- Observed baseline: approximately 7.20 MiB per successful snapshot.
- At 36,960 successful requests: 266,112 MiB = **259.875 GiB**.
- Required reduction versus the 10 GiB limit: approximately **25.99×**.
- A bounded sample of the newest 12 compressed snapshots averaged 231,717 bytes = 226.3 KiB. That payload alone uses 79.76% of the total per-request budget, leaving approximately 57.4 KiB for normalized rows, all indexes, and log/file allocation.
- At the 10 GiB target, the current live free space would fall from 18.983 GiB to about 8.983 GiB, only 0.983 GiB above the required 8 GiB reserve before any operational margin.

The bounded 31-run success/coverage evidence is insufficient to replace the supplied 7.20 MiB-per-successful-snapshot observation or to establish a worst-case success pattern. No direct seven-day V3 SQL allocation result exists. The acceptance gates therefore fail on evidence, not because sampling policy was silently reduced.

## 7. Production deployment and rollback plan for later authorization

1. Keep both LaunchAgents disabled and verify no collector process before any future work.
2. Reconcile the live procedure SHA against the repository V2 definition; do not deploy over unknown drift.
3. Obtain a verified backup covering all current observations, not merely the older backup point. Confirm log and volume headroom.
4. Use a small restored/sanitized database ending in `_STORAGE_V3_LAB`; run `estimate-storage-v3-compression.sql` for ROW/PAGE and apply V3 only in that lab. Record allocated bytes, CPU, persistence latency, readback latency, log/tempdb use, and failure rollback behavior.
5. If and only if the measured V3+compression combination meets the 10 GiB/8 GiB gates, obtain a separate production authorization. Execute the additive migration in one session with `@ApplyStorageV3 = 1`, collector stopped, and no backfill.
6. Validate bounded row counts, representation integrity, V3 view readback, Power BI queries, and disk/log headroom before any separate authorization to resume collection.

If a committed deployment must be operationally backed out, retain V3 columns/views for rows already written and disable the V3 triggers; do not drop compressed columns after V3 data exists. An uncommitted migration failure is expected to roll back through `XACT_ABORT`. Restoring the prior procedure or database state requires the approved recovery plan and is not automated here.

## 8. Unresolved risks and minimum viable alternatives

- SQL ROW/PAGE savings and V3 trigger overhead remain unmeasured.
- V3 is future-write only; it does not reclaim historical plaintext allocation.
- Existing direct base-table consumers would see NULL in physical plaintext columns for compressed fields; they must use the V1/V2/V3 readback views.
- The live persistence procedure has definition drift from the repository, so procedure replacement is not safe without manual review.
- Current free space leaves little margin beyond the 8 GiB reserve even if the 10 GiB target were achieved.

If the physical constraints remain incompatible after the lab measurement, the minimum viable alternatives are a separately approved lower sampling frequency or a separately approved, verified lossless archival architecture. Neither was implemented.

## 9. Files changed for V3

Added:

- `scripts/validate_storage_v3.py`
- `sql/mdd-mac-collector/audit-storage-v3-readonly.sql`
- `sql/mdd-mac-collector/estimate-storage-v3-compression.sql`
- `sql/mdd-mac-collector/optimize-storage-v3.sql`
- `sql/mdd-mac-collector/verify-storage-v3-lab.sql`
- this report

Modified:

- `sql/mdd-mac-collector/create-source-faithful-persistence.sql`

Pre-existing user/S141 changes—including `scripts/activate_tiered50_production.py`, V1/V2 SQL and validators, evidence JSON, and prior operational documents—were not reset or overwritten.

## 10. Commands and validation executed

Read-only/runtime checks:

```text
git status --short
launchctl print-disabled system
launchctl print-disabled user/$(id -u)
launchctl print gui/$(id -u)/com.cologne-transit-intelligence.mdd-collector
launchctl print gui/$(id -u)/com.cologne-transit-intelligence.mdd-tiered50-collector
pgrep -af 'cologne_transit|mdd.*collector|tiered50'
```

Repository and offline validation:

```text
git diff --check
PYTHONPATH=src python3 scripts/validate_snapshot_gzip_storage_v1.py
PYTHONPATH=src python3 scripts/validate_observation_gzip_storage_v2.py
PYTHONPATH=src python3 scripts/validate_storage_v3.py
PYTHONPATH=src python3 scripts/validate_storage_v3.py --json
PYTHONPATH=src python3 -m py_compile scripts/validate_storage_v3.py
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_mssql_persistence.py'
```

The live audit used a short-lived `pymssql` process with only `SELECT` statements against `sys.database_files`, `sys.dm_os_volume_stats`, `sys.dm_db_partition_stats`, `sys.partitions`, `sys.columns`, `sys.indexes`, `sys.objects`, and bounded `TOP`/`DATALENGTH` samples. The reusable SQL form is [audit-storage-v3-readonly.sql](../../sql/mdd-mac-collector/audit-storage-v3-readonly.sql).

## Compact status

```text
STAGE_1: PASS — bounded live read-only audit; procedure drift remains
STAGE_2: PASS — timing rows/indexes and snapshot LOB dominate
STAGE_3: PARTIAL — V3 logical round-trip PASS; SQL ROW/PAGE NOT_MEASURED
STAGE_4: PASS OFFLINE — additive source-only V3 contract implemented and validated
STAGE_5: NO_GO — seven-day SQL capacity evidence is insufficient and baseline exceeds budget
MEASURED_WEEKLY_GROWTH_GIB: NOT_DIRECTLY_MEASURED; 259.875 GiB baseline-equivalent at 36,960 successes
PROJECTED_V3_WEEKLY_GROWTH_GIB: NOT_MEASURED; acceptance ceiling is 10.000 GiB
WINDOWS_FREE_SPACE_GIB: 18.983 live read-only measurement
PRODUCTION_WRITES: 0
LIVE_MDD_REQUESTS: 0
COLLECTOR_STATUS: STOPPED_AND_DISABLED
STORAGE_V3_CAPACITY_DECISION: NO_GO
```

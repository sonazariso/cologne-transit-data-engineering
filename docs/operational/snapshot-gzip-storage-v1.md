# Tiered50 storage optimization V1 — additive compressed snapshots

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Status:** implemented as source-controlled DDL, migration, bounded backfill, validation, and offline tests. **Not applied to SQL Server or the Mac runtime by this package.** The collector must remain stopped while production storage changes are evaluated.

## Why

- SQL measurements (12 October 2026): `MddMacSourceSnapshot` 5759 MB LOB for ~695 snapshots, `MddMacTimingObservation` 6969 MB and `MddMacCallObservation` 3602 MB, predominantly in-row.
- An individual Snapshot 716 compressed from 10.41 MiB to 0.24 MiB (97.66%). This is **one sample**, not proof for all history.
- Original SQL data file ~19.5 GiB, log file ~1.86 GiB, and reported Windows free space ~30 GiB: unsafe for uninterrupted month-long collection with current normalized detail volume.

## Migration contract

1. Check that the **existing** backup is verified and covers the desired recovery point. It may predate recent October observations: do not claim that it protects data collected later. This package does **not** create a new backup.
2. On an **isolated restored SQL Server copy**, apply `sql/mdd-mac-collector/optimize-snapshot-gzip-v1.sql`. It adds `SnapshotJsonGzip VARBINARY(MAX)`, changes existing `SnapshotJson` to nullable, enforces XOR storage, and installs an updated `stg.uspPersistMddMacSnapshot` and lossless read view `stg.vMddMacSourceSnapshotJson`.
3. Collector application code and stored procedure parameters remain unchanged: `@SnapshotJson NVARCHAR(MAX)` still arrives from Python; SQL Server stores `COMPRESS(@SnapshotJson)` for **new snapshots only**. `PayloadHashSha256` retains the existing UTF-8 hash semantics; decompression of UTF-16 source text is exact.
4. Verify both `PERSISTED_PARSED_SNAPSHOT` and the normalized observation row sets in a restored-copy trial; compare `DECOMPRESS` round-trip and event counts against existing accepted fixtures.
5. Deploy the same migration **only after confirming deployed stored procedure has no unreviewed local drift**. The migration has a basic signature drift gate, not a complete SHA-based provenance proof.
6. Historical snapshots remain readable with the new `stg.vMddMacSourceSnapshotJson` view. The base table is a physical XOR contract; the view is the logical source-text contract. A physically NULL `SnapshotJson` after conversion is not a logical NULL, and an original empty string must remain an empty string. Existing queries that directly select `SnapshotJson` from the base table must migrate to this view, otherwise they will see NULL for compressed rows. Do not silently change old JSON consumers.
7. Before any production write, run the exact `COMPRESS` measurement and the isolated one-row capacity workflow in `measure-snapshot-gzip-capacity-lab.sql`. Confirm the existing recovery point, exact UTF-16LE bytes/SHA-256, NULL/empty state, SnapshotId/audit lineage, direct raw-column and Power BI dependencies, independently measured live MDF/LDF/tempdb/session state, per-row update/rollback/log/autogrowth/external reserves, and the protected 12 GiB C: floor. Only an explicitly approved working copy may set `@Apply=1`, `@ApproveOriginalPhysicalRepresentationChange=1`, and the remaining evidence gates. The backfill remains one snapshot per transaction and resumable by SnapshotId; its source-controlled defaults are `@Apply=0` and no writes.
8. Validate with `verify-snapshot-gzip-v1.sql`, all row counts, and known event counts. Logical payload savings are not MDF or Windows recovery. A later bounded `DBCC SHRINKFILE` may be considered only after internal data-file free pages, LDF/tempdb usage, fragmentation, and a positive before/after Windows free-space delta are measured; never run `DBCC SHRINKDATABASE`. Timing PAGE/dictionary work is a second, bounded lab reassessment after measured internal capacity recovery and must not duplicate the full history.

## What this does NOT solve

- The ~10.5 GiB clustered and secondary indexes for timing/call observations still grow rapidly. SQL Server `DATA_COMPRESSION = PAGE` might help but **no rebuild is included** in V1: on a nearly full disk it may need excessive log/temp space and locks. First test representative PAGE/ROW compression on a restored copy and measure actual savings, latency, blocking, and recovery headroom.
- This package does not configure a watchdog, rollover contract, disk alerting, or backups. It does not prove that one month of Tiered50 fits on the existing VM. Do **not** restart unattended month-long collection until capacity and monitoring gates pass.
- A source ZIP from 2026-10-11 was the code basis. The user's 2026-10-12 outage-validator patch, parser changes, and local Git state are not inside this ZIP. Compare against current Git checkout before applying; do not overwrite newer changes.

## Timing/Call compression test already staged

Run `sql/mdd-mac-collector/estimate-observation-page-compression.sql` **only on the restored copy**. It estimates PAGE compression of both large clustered indexes; it does not rebuild either index. Assess exact savings, CPU/persistence throughput, scratch disk/tempdb/log requirements, and post-rebuild behavior before implementing a separate, guarded V2 migration. No unmeasured PAGE/ROW change is enabled in this release.

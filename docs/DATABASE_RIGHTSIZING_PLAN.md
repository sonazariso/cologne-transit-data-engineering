# Database right-sizing for the Data Engineering portfolio sample — NOT EXECUTED

**Status date:** October 15, 2026.  The approved outcome is bounded,
in-place, data-only retention: exactly ten complete MDD snapshots, their
dependent rows, and the minimum coherent GTFS, warehouse, realtime and
control sample.  The exact live RunId/AuditId/hash/child-count evidence and
direct SQL Server capacity measurements are now captured read-only in
`docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md`.  Destructive retention and MDF
recovery remain unexecuted.

This is a Data Engineering portfolio sample, not a continuous MDD dataset or
a reporting product.  Power BI is out of scope.  No second disk, full
database copy, schema replacement, collector restart, backup overwrite or
GitHub push is required by the approved path.

## Live capacity evidence

The 2026-10-15 SQL Server preflight measured approximately 14.9927 GiB free on
Windows `C:` against the mandatory 12 GiB floor, a 28.49 GiB MDF allocation,
an 1.82 GiB LDF with 3.124% used, and 29.2 MiB of unallocated tempdb space at
the capture.  The executable SQL remeasures C:, log free bytes, tempdb,
database/tempdb next-growth reserves and a DMV-based worst-case batch reserve
before every batch.  A batch is not started when the floor or log/tempdb
reserve cannot be proven.

MDF internal free pages are not Windows recovery.  Actual physical recovery
can be claimed only from the after-measurement result of the separate,
disabled-by-default MDF script.

## Retained data contract

The exact MDD IDs are:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

The retained live totals are Timing 14,308; Call 7,504; Situation 0;
ServiceSection 245; Attribute 22; ResponseError 0; ParserGap 121.  The
control lineage is six runs, ten audits, ten HTTP attempts, nine quota
permits, six dispatcher claims, six dispatcher claim events, 81 target claims,
81 target-claim events, zero leases, nine lease events and 65 quota-audit
rows.

The cross-layer sample is concrete and executable: GTFS AgencyId `5`,
ServiceId `50126`, RouteId `de:nrw:rb25:`, ShapeId `22`, the selected trip,
two stops and one sequence-2 StopTime; GTFS LoadBatch 2 with 23 validation
rows; warehouse batch 3 with the selected operational/scheduled facts and
dimensions; and realtime observation 4, situation 2 and link `(4,2)`.

## Implementation boundary

`sql/portfolio/retention-apply.sql` is the reviewed in-place implementation.
It preserves every user schema and object definition.  Its cross-layer
manifest is executable: the same concrete predicates count retained rows,
drive FK/dependency gating and select rows for bounded `DELETE TOP` batches.
Unlisted tables are never deleted.  A listed parent with an unlisted FK child
stops the script before any batch.  Child-first delete order covers the
source, control, GTFS, warehouse and realtime relationships; the warehouse
stop hierarchy deletes leaves before parents.  The script takes an exclusive
application lock and commits each batch, so an interrupted run is resumable.

`sql/portfolio/recover-project-space-apply.sql` is separate.  It names the
actual MDF logical file `CologneTransitIntelligence`, measures Windows C:,
MDF, LDF and tempdb directly, and remains `@ApplyShrink = 0`.  A separately
approved working copy must provide a target above current used pages and
below current allocation; it may issue only bounded `DBCC SHRINKFILE` for the
MDF and must verify actual Windows C: bytes afterward.

## Exact SSMS sequence

Use SSMS against `DataAnalyst-VM`, database
`CologneTransitIntelligence`, and save result sets:

1. Run `sql/portfolio/retention-inventory-readonly.sql` and
   `sql/portfolio/select-ten-snapshots-readonly.sql`. Confirm the exact ten
   live rows against the manifest document.
2. Run `sql/portfolio/retention-dry-run.sql`. Every cross-layer selected-count
   row must be `PASS`; review the complete object/FK inventory and direct
   C:/log/tempdb measurements.
3. Verify an off-C recovery point with `RESTORE VERIFYONLY WITH CHECKSUM`,
   review SQL modules/direct consumers, and confirm collectors and leases are
   inactive.
4. Make a reviewed working copy of `retention-apply.sql`, set `@Apply = 1`,
   complete every approval gate/reference, and execute the whole file in one
   SSMS window. Do not alter the selectors, remove `GO`, increase batch sizes,
   or run it concurrently. On interruption, re-read the evidence and rerun
   the same reviewed working copy.
5. Review exact hashes, child counts, cross-layer counts, FK checks,
   `DBCC CHECKCONSTRAINTS`, and the complete post-apply schema/object/column/
   index/module inventory.
6. Only after retention verification, run the recovery read-only review. A
   second approval is required before any MDF shrink; no log shrink or
   `SHRINKDATABASE` is allowed.

No step above authorizes production mutation by itself. The repository
defaults remain fail-closed.

## Closure criteria

- Exactly ten approved snapshots and all dependent rows pass hash/count
  readback.
- The executable cross-layer selectors pass the expected counts and preserve
  relational closure.
- Every schema, object, column, index, key, foreign key and SQL module hash
  matches before/after.
- Actual Windows C: before/after bytes are recorded; logical MDF free pages
  are not substituted for physical recovery.
- Collectors remain disabled; no continuity or actual-delay KPI is claimed.

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

# SQL retention scope — what “raw data” means here

This engineering closeout is a **data-only retention plan** for the Data
Engineering portfolio sample. It preserves the complete deployed SQL Server
structure and reduces only the reviewed historical rows in the executable
manifest. It does not authorize deleting or replacing an entire SQL schema or
creating an MDD-only database.

Before cleanup, `sql/portfolio/retention-inventory-readonly.sql` must inventory
the live database, including every user schema and object. The output must
distinguish physical tables (`sys.objects.type = 'U'`) from views
(`sys.objects.type = 'V'`); views, functions, procedures, triggers, indexes,
keys, columns, and foreign keys are structure and remain preserved.

| Schema/table family | Role in implementation | Proposed retained state |
| --- | --- | --- |
| `stg.MddMacSourceSnapshot` | Provider response snapshots, source text/GZIP, timestamp and payload lineage | **Exactly 10 reviewed, complete and successfully persisted snapshots** |
| `stg.MddMacTimingObservation`, `stg.MddMacCallObservation` | Parsed per-occurrence time and stop/call records; not simply unprocessed JSON | All child observations for the selected `SnapshotId`s, while only unused history is eligible for deletion after approval |
| `stg.MddMacSituationEvidence`, `stg.MddMacServiceSectionEvidence`, `stg.MddMacAttributeEvidence`, `stg.MddMacResponseError`, `stg.MddMacParserGap` | Source-faithful parsed evidence and quality records | Keep every associated child record for each selected snapshot; never create orphaned examples |
| `ctl` control tables | Run, target-audit, HTTP attempts, quota, dispatcher, collector status and lineage | Retain every row needed to explain the ten snapshots and the sample's ETL configuration/audit lineage; no unrelated control-data delete is automatic |
| `src`, `wrk`, `core` and other user schemas | Existing source, working, canonical or matching structures, if deployed | Preserve all schemas, columns, tables, indexes, keys and foreign keys; reduce rows only through an explicit table-level manifest |
| `analytics` objects | SQL projections/read access, field coverage and data-quality diagnostics | Preserve views, functions, procedures and tables; review table data separately without removing working SQL structures |
| GTFS-named objects / `gtfs*` schemas | Static transit reference data, if deployed | Retain a small, real, coherent GTFS dataset plus its required foreign-key closure; do not discard by name or size alone |
| `dw` / `dwh` / `warehouse` objects | Warehouse or curated analytical structures, if deployed | Retain relevant dimension and fact rows plus their required dimension/fact/lineage foreign-key closure |
| Source code, SQL schema, procedures, tests and docs | Reproducible ETL implementation | Keep in the repository; redact secrets and exclude production datasets/backup files |

**S156 structure invariant:** `stg`, `dw`, `ctl`, `src`, `wrk`, `core`,
`analytics`, and any other live user schema are retained. The bounded in-place
implementation does not require a replacement database or a second disk. If
an independently approved rehearsal is ever used, it must reproduce all existing schemas,
physical tables, columns, appropriate indexes, primary/unique/check/default
keys, foreign keys, views, functions, stored procedures, and triggers before
data is copied. Empty tables and views still matter for training and must not
be omitted. Do not issue `DROP SCHEMA`, blanket `TRUNCATE`, or uninformed
cascade deletes.

**Data-only retention invariant:** the live manifest must cover the exact ten
MDD snapshots and all dependent rows, a coherent GTFS sample, relevant
warehouse dimensions and facts, and the lineage/configuration/audit rows
needed to reproduce and explain those rows. The manifest must be checked
against the complete FK graph, not only MDD foreign keys. Do not claim that
pruning data alone returns physical space to Windows C:. The 80% target refers
to measured **project SQL disk footprint**, not RAM.

**User-approved retention policy:** keep exactly 10 complete snapshots with the operator-confirmed IDs `22, 93, 185, 189, 207, 224, 225, 226, 228, 229`. The live RunId/AuditId/hash/child-count evidence, cross-layer sample selectors and direct C:/log/tempdb measurements were captured read-only on 2026-10-15. **Not yet executed:** destructive retention, consumer/backup sign-off and physical MDF recovery. No physical disk space has been reclaimed.

## Operator-reviewed files

- `sql/portfolio/retention-inventory-readonly.sql` — live files, allocations,
  status, foreign keys and dependency inventory.
- `sql/portfolio/select-ten-snapshots-readonly.sql` — deterministic,
  evidence-backed exactly-ten candidate result; it never writes IDs.
- `sql/portfolio/retention-dry-run.sql` — literal-manifest validation and
  before/after row-count evidence with no base-table mutation.
- `sql/portfolio/replacement-database-dry-run.sql` — optional historical,
  read-only exact-ID/capacity review; it is not required by the approved
  bounded in-place path and does not create a replacement database.
- `sql/portfolio/retention-apply.sql` — disabled-by-default bounded-batch
  in-place implementation. It has literal MDD evidence plus executable GTFS,
  warehouse, realtime and control selectors; the same predicates drive
  readback counts and actual `DELETE TOP` batches. It never removes schema
  objects or touches unlisted tables.
- `sql/portfolio/recover-project-space-readonly.sql` and
  `sql/portfolio/recover-project-space-apply.sql` — physical file/C: review
  and separately authorized shrink boundary. It measures actual Windows C:
  free bytes before and after a reviewed MDF-only command. Internal MDF free
  pages are not reported as Windows recovery.

## Exact SSMS operator sequence

Use SSMS against `DataAnalyst-VM`, database `CologneTransitIntelligence`, and
save every result set as review evidence:

1. Run `retention-inventory-readonly.sql` and
   `select-ten-snapshots-readonly.sql`. Confirm the exact ten-row readback in
   `TEN_SNAPSHOT_RETENTION_MANIFEST.md`.
2. Run `retention-dry-run.sql`. Every cross-layer selected-count row must be
   `PASS`; review the full schema/object/FK inventory and direct C:/log/tempdb
   measurements.
3. Confirm an off-C checked recovery point with
   `RESTORE VERIFYONLY WITH CHECKSUM`, consumer/SQL-module review, and
   collector/lease inactivity. Power BI and a reporting product are out of
   scope for this engineering sample.
4. In a separately reviewed working copy of `retention-apply.sql`, set
   `@Apply = 1`, complete all approval gates and evidence references, and run
   the whole file in one SSMS window. Do not change selectors, remove `GO`,
   increase batch sizes, or run concurrently. Each batch commits separately;
   after an interruption, re-read the evidence and rerun the same reviewed
   working copy.
5. Review exact hashes, child counts, cross-layer counts, FK checks,
   `DBCC CHECKCONSTRAINTS`, and the complete post-apply object inventory.
6. Only after those checks pass, run the recovery read-only review. A separate
   approval must set an exact MDF target at or above the measured allocation
   high-water mark and below current allocation in `recover-project-space-apply.sql`; its default
   `@ApplyShrink = 0` does not execute `DBCC SHRINKFILE`.


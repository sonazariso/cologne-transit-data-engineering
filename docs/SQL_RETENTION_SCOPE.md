# SQL retention scope — what “raw data” means here

This engineering closeout is a **data-only retention plan**. It preserves the
complete deployed SQL Server structure for future Data Analyst training and
reduces only reviewed, unnecessary historical rows. It does not authorize
deleting or replacing an entire SQL schema or creating an MDD-only database.

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

**S155 structure invariant:** `stg`, `dw`, `ctl`, `src`, `wrk`, `core`,
`analytics`, and any other live user schema are retained. A replacement
database, if independently approved, must reproduce all existing schemas,
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

**User-approved retention policy:** keep exactly 10 complete snapshots with the operator-confirmed IDs `22, 93, 185, 189, 207, 224, 225, 226, 228, 229` and remove other unneeded historical rows only after evidence review. **Not yet executed:** live RunId/AuditId/hash/child-count evidence, external-consumer review, full restore evidence, replacement-database peak fit and the intended physical-space recovery method require review before execution. No physical disk space has been reclaimed yet. The user will publish to GitHub independently.

## Operator-reviewed files

- `sql/portfolio/retention-inventory-readonly.sql` — live files, allocations,
  status, foreign keys and dependency inventory.
- `sql/portfolio/select-ten-snapshots-readonly.sql` — deterministic,
  evidence-backed exactly-ten candidate result; it never writes IDs.
- `sql/portfolio/retention-dry-run.sql` — literal-manifest validation and
  before/after row-count evidence with no base-table mutation.
- `sql/portfolio/replacement-database-dry-run.sql` — read-only exact-ID,
  capacity, backup, dependency, external-consumer, GTFS and warehouse review;
  it does not create the replacement database.
- `sql/portfolio/retention-apply.sql` — separate disabled-by-default,
  bounded-batch, data-only template. It retains the MDD sample and requires a
  reviewed GTFS/warehouse/lineage manifest and FK closure; it never removes
  schema objects and has no automatic non-MDD table delete.
- `sql/portfolio/recover-project-space-readonly.sql` and
  `sql/portfolio/recover-project-space-apply.sql` — physical file/C: review
  and separately authorized shrink boundary. Internal MDF free pages are not
  reported as Windows recovery.


# SQL retention scope — what “raw data” means here

This engineering closeout keeps the working **ETL schema** but drastically reduces the **stored historical rows**. It does not authorize deletion of entire SQL schemas.

| Schema/table family | Role in implementation | Proposed retained state |
| --- | --- | --- |
| `stg.MddMacSourceSnapshot` | Provider response snapshots, source text/GZIP, timestamp and payload lineage | **Exactly 10 reviewed, complete and successfully persisted snapshots** |
| `stg.MddMacTimingObservation`, `stg.MddMacCallObservation` | Parsed per-occurrence time and stop/call records; not simply unprocessed JSON | All child observations for the selected `SnapshotId`s, while only unused history is eligible for deletion after approval |
| `stg.MddMacSituationEvidence`, `stg.MddMacServiceSectionEvidence`, `stg.MddMacAttributeEvidence`, `stg.MddMacResponseError`, `stg.MddMacParserGap` | Source-faithful parsed evidence and quality records | Keep every associated child record for each selected snapshot; never create orphaned examples |
| `ctl` control tables | Run, target-audit, HTTP attempts, quota, dispatcher, collector status and lineage | Preserve parent records required for selected samples, documented failures, and referential integrity; only consider pruning unrelated old high-volume operational rows after dependency review |
| `analytics` (views, if deployed) | SQL projections/read access, field coverage and data-quality diagnostics | Preserve only implemented, working SQL-side transformations needed for engineering demonstration |
| Source code, SQL schema, procedures, tests and docs | Reproducible ETL implementation | Keep in the repository; redact secrets and exclude production datasets/backup files |

**Important distinctions:** `stg` includes both source payloads and already parsed/structured evidence. It is not equivalent to “throwaway data”. Do not issue `DROP SCHEMA stg`, blanket `TRUNCATE`, or uninformed cascade deletes. Do not claim that pruning data alone returns physical space to Windows C:. The 80% target refers to measured **project SQL disk footprint**, not RAM.

**User-approved retention policy:** keep exactly 10 complete snapshots and remove other unneeded historical rows. **Not yet executed:** the 10 SnapshotIds still need live read-only selection; destructive statements, affected table lists, full restore evidence and the intended physical-space recovery method require review before execution. No physical disk space has been reclaimed yet. The user will publish to GitHub independently.

## Operator-reviewed files

- `sql/portfolio/retention-inventory-readonly.sql` — live files, allocations,
  status, foreign keys and dependency inventory.
- `sql/portfolio/select-ten-snapshots-readonly.sql` — deterministic,
  evidence-backed exactly-ten candidate result; it never writes IDs.
- `sql/portfolio/retention-dry-run.sql` — literal-manifest validation and
  before/after row-count evidence with no base-table mutation.
- `sql/portfolio/retention-apply.sql` — separate disabled-by-default,
  bounded-batch purge of nonretained `stg` history only; `ctl` lineage stays.
- `sql/portfolio/recover-project-space-readonly.sql` and
  `sql/portfolio/recover-project-space-apply.sql` — physical file/C: review
  and separately authorized shrink boundary. Internal MDF free pages are not
  reported as Windows recovery.


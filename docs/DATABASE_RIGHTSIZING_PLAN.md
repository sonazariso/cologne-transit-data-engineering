# Database right-sizing for a small portfolio dataset — NOT EXECUTED

**Retention policy approved on October 15, 2026: keep exactly 10 source snapshots with all their dependent rows and necessary lineage, and remove other obsolete historical data after a recoverable manifest and verified execution plan. S154 confirms the approved ID set as `22, 93, 185, 189, 207, 224, 225, 226, 228, 229`; live row evidence, dependency sign-off and destructive SQL have NOT been approved or executed.** The user cannot add/expand VMware disks, does not need a continuous MDD dataset, wants a small reference sample, and aims to recover **at least 80% of the current SQL-project disk footprint** for a future Data Analyst project. This target must be defined against a fresh measured project baseline (MDF + LDF + other project-specific files on C:); it is **not** a target for total RAM or the entire Windows system drive.

## Current historical measurements (not fresh authorization)

| Metric | Operator-supplied evidence |
| --- | ---: |
| SQL MDF allocated | approx. 28.49 GiB |
| SQL LDF allocated | approx. 1.82 GiB |
| Timing rows | 2,553,690; allocated approx. 12.99 GiB |
| Call rows | 1,344,108; allocated approx. 5.41 GiB |
| Snapshot rows | 1,927; allocated approx. 5.90 GiB |
| Windows C: available | last reported 13.92 GiB |
| Mandatory operational C: safety floor | 12 GiB |
| Off-C recovery copy | Oct 14 `CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak`; operator reported `RESTORE VERIFYONLY WITH CHECKSUM` success |

A target of 80% reduction from a roughly 30 GiB project SQL-file baseline would require roughly 24 GiB of **actual Windows C: file footprint reduction**. This is a *target illustration*, not proof it is physically achievable. The SQL server may contain additional non-project tables, file limits, active dependencies, or later changes.

## Retention proposal — user must approve exact manifest

Retain **exactly 10 curated, referentially complete source snapshots**, selected from evidenced successfully persisted historical observations. Keep *all* child call/timing/situation/attribute/error/parser-gap rows for those snapshots and the relevant `ctl` run/target-audit lineage. Selection should cover as many actually available interview-relevant cases as possible: multiple stations and UTC windows, ordinary planned/estimated timing, established identity, service/line metadata, situation/attribute evidence, recorded-time presence or absence, and parser-gap/response-error evidence. Never invent an unavailable category or assume a fragmented sample is representative. Preserve sufficient static target identity and parser/source-contract metadata to understand those rows. Keep one source-controlled sample manifest with counts, UTC range, per-snapshot hashes and a verification summary.

Do not confuse retained samples with a continuous sample or representative citywide performance. Preserve Stage 8 and Stage 9 evidence summaries as documentation, regardless of whether those historical records are present in SQL. The pre-cleanup backup stays off-C; it is not published to GitHub. For historical material newer than that backup, require a fresh verifiable off-C recovery point or expressly document that the user has approved its permanent loss.

**The user has authorized reducing the historical dataset to exactly 10 complete snapshots and has confirmed the ten IDs listed above.** This confirms the ID set, **not** the live RunId/AuditId/hash/child-row evidence or approval to execute a destructive database script without a verified restore point, manifest, dependencies, external-consumer review, and measured capacity. No sample may be selected by arbitrary `TOP` without reproducible ordering/constraints.

## Required operator sequence (dry-run, sign-off, then separate apply)

1. **Measure** live object allocations, SQL files/paths, current C: free bytes, LDF/tempdb, auto-growth and competing sessions, and map every FK/view/job/external SQL consumer. Record data versus allocated space. Use the included read-only `sql/portfolio/retention-inventory-readonly.sql`.
2. **Validate the confirmed ID set** with `sql/portfolio/select-ten-snapshots-readonly.sql` and `sql/portfolio/replacement-database-dry-run.sql`. The result must prove exactly the ten approved IDs are successful persisted rows with parent/child counts, hashes, payload representation, source coverage and connected control lineage. Export a sanitized manifest and SQL/data-quality evidence. Confirm provider redistribution rights before any public sample.
3. **Validate a recovery point**: keep the proven backup unchanged, verify restore possibilities, and take a newer off-C backup if the selected current state is not covered. Never store another large backup on C:.
4. **Prefer a small replacement database when feasible:** use the read-only replacement feasibility plan to assess whether an independently validated NEW bounded SQL Server database with the existing required schema and exactly the 10 approved snapshots can fit in the measured C: headroom while preserving the 12 GiB floor. The peak must include copy, log, tempdb, rollback, autogrowth and external-use reserves. Copy complete selected rows with exact primary IDs, foreign keys, timestamps, binary payloads and hashes only in a separately approved operator workflow; verify every family and consumer view before considering retirement of the large original DB. Compare against bounded in-place deletion/shrink, which can log millions of deletions and cause expensive page movement. A *new small database* requires explicit approval of its name and consumer configuration. Existing MDD collectors stay disabled.
5. **Prove recovery on isolated restore/transactional rehearsal** where possible; if not possible with current resources, report the evidence gap and choose explicit operator-approved smaller steps. Validate row hashes, FK and run/audit lineage and rollback before any destructive operation.
6. **Execute destructive retirement only after review of the 10 exact SnapshotIds, their manifest, all affected tables, external consumers, signed-off rollback/restoration steps and the exact script**. The source-controlled `sql/portfolio/retention-dry-run.sql` is the final dry-run evidence gate; `sql/portfolio/retention-apply.sql` is a separate disabled-by-default operator template and remains unexecuted. It is limited to nonretained `stg` history only; `ctl` lineage, payloads and all retained children stay. GTFS, warehouse, analytics and working ETL/SQL structures are reviewed separately and are not automatic purge targets. Avoid giant single-transaction `DELETE` on a nearly full C:; account for logging, tempdb, checkpoint and rollback. Do not use a generic cascade deletion or shrink as an unreviewed shortcut.
7. **Return disk space**: removing rows frees pages **inside MDF**, not necessarily Windows C:. Run `sql/portfolio/recover-project-space-readonly.sql` after retention verification. To return physical disk space, the separately reviewed `sql/portfolio/recover-project-space-apply.sql` requires the exact logical data-file name, fresh reserves, the 12 GiB projection and an 80% project-byte target before a manual `DBCC SHRINKFILE`. Do not blindly shrink a largely full or active database; monitor blocking, log growth and autogrowth, and validate after the operation. Avoid routine scheduled shrinking.
8. **Measure actual achievement** using before/after bytes from `sys.database_files`, `sys.dm_os_volume_stats` (and host filesystem observation) and validate downstream views. Compute `(original project bytes − final project bytes) / original project bytes`. Report the true value, even if it is below 80%.
9. **Keep evidence**: retention manifest, backup SHA/location (not secrets), high-level before/after table sizes, query output samples, validation status and collector disabled state. Never commit the original bulky private DB/backup.

## S154 operator approval boundary

The source-controlled ID set is:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

The exact live RunId/AuditId/hash/child-count attachment referenced by the
operator is not present in this worktree. Therefore the final plan is
**DRY_RUN_ONLY**. Before cleanup, the administrator must provide a signed
approval naming the operator and UTC time, confirm those ten IDs against the
read-only SQL result, attach all seven child-family counts and payload/hash
parity, review FK/module/job/direct/external consumers, verify an off-C checked
backup, and attach fresh before/after MDF/LDF/tempdb/C: measurements. The
approval must also state whether the independently measured replacement
database fits; if it does not, it must explicitly approve the bounded in-place
fallback. Any destructive apply and any `DBCC SHRINKFILE` require separate
approval.

GTFS and warehouse tables are separate portfolio decisions. The inventory and
replacement feasibility queries report them separately and do not discard
them automatically. Working ETL/SQL structures, analytics views and required
lineage remain preserved.

## Why not try PAGE compression first?

The Timing table was approx. 13 GiB, mostly in-row/clustered. Rebuilding it is log/space intensive and would optimize large data that is no longer needed. With only ~1.92 GiB historical headroom over the 12 GiB floor, it is not a safe prerequisite to a small portfolio sample. Right-size retained records first; then decide whether any compression is still necessary.

## Closure criteria

- User-approved retention objective and ID set are 10 snapshots (`22, 93, 185, 189, 207, 224, 225, 226, 228, 229`); live evidence, restoration steps and any destructive execution script must still be reviewed.
- Measured >80% C:-disk release from the **project's** baseline, or a documented quantified reason it could not be reached.
- User-owned off-C backup preserved and sample is reproducible.
- Collectors remain disabled; no fictitious continuity or actual-delay metrics.
- GitHub source sanitization and read-only sample verified.

## Final closeout statement

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.


# Data Engineering portfolio closeout — evidence and acceptance

**Scope date:** October 15, 2026.  This is the final closeout for the
implemented API-to-SQL Server engineering pilot.  No production deletion,
collector activation, GitHub publication or disk reclamation was performed.

## Goal and stop condition

Present the demonstrated API acquisition, parsing, data-quality, lineage,
bounded persistence and operational-safety work.  Stop extending collection
and storage experiments.  The retained portfolio sample is exactly ten
complete MDD snapshots with dependent rows and run/audit lineage, plus the
minimum coherent GTFS, warehouse, realtime and control data needed to show
Data Engineering.  The complete SQL Server schema and object inventory stays
in place.  The sample is discontinuous and unsuitable as a reporting
dataset; Power BI and a reporting product are out of scope.

## What worked

| Area | Evidence | What it proves |
| --- | --- | --- |
| MDD/TRIAS request path | Bounded pilot and persisted SQL evidence | Source acquisition, response validation, parsing and persistence worked |
| Source-backed matching prototype | Stage 9 evidence | Matching/data-quality classifications and explicit evidence gates |
| Legacy7 schedule | Historical six-bucket evidence | Bounded scheduling and safe retirement behavior |
| SQL Server | `ctl` lineage, `stg` snapshots and normalized observations | Real relational landing and operational metadata |
| Failure handling | First Tiered50 heartbeat stopped after its cadence gate failed | Safe failure and diagnostics are part of the implementation |

## Final retention evidence

The exact approved IDs are:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

The live read-only manifest captured exact RunIds, AuditIds, original payload
hashes, payload hashes and child counts for all ten rows.  The retained child
totals are Timing 14,308; Call 7,504; Situation 0; ServiceSection 245;
Attribute 22; ResponseError 0; ParserGap 121.  Control lineage is six runs,
ten audits, ten HTTP attempts, nine quota permits, six dispatcher claims, six
dispatcher claim events, 81 target claims, 81 target-claim events, zero
production leases, nine lease events and 65 quota-audit rows.

The minimum cross-layer manifest is also live-evidenced: GTFS batch 2 and a
small coherent route/trip/stop sample; warehouse batch 3 and the selected
dimension/fact closure; and realtime observation 4, situation 2 and link
`(4,2)`.  The executable selectors and expected counts are in
`sql/portfolio/retention-apply.sql` and are not merely documentation.

## Approved implementation path

The approved implementation is bounded in-place retention.  A second disk or
full database copy is not required.  `retention-apply.sql` preserves every
schema, table, column, index, key, foreign key, view, function, procedure and
trigger.  It uses child-first, bounded `DELETE TOP` batches, an application
lock, direct C:/log/tempdb measurements before every batch, a 12 GiB floor,
and resumable reruns.  It does not touch unlisted tables.

`recover-project-space-apply.sql` is a separate MDF-only recovery boundary.
It names the actual logical file, measures actual Windows C: free space and
remains disabled by default.  A separate approval must set a target above
current used pages and below allocation; no log shrink or `SHRINKDATABASE` is
allowed.

## Exact SSMS runbook

Use SSMS against `DataAnalyst-VM`, database
`CologneTransitIntelligence`, and save every result set:

1. Run `retention-inventory-readonly.sql` and
   `select-ten-snapshots-readonly.sql`. Confirm the exact ten-row manifest.
2. Run `retention-dry-run.sql`. Every cross-layer selected-count row must be
   `PASS`; review the complete object/FK inventory and capacity measurements.
3. Verify the off-C recovery point with `RESTORE VERIFYONLY WITH CHECKSUM`,
   review direct SQL/module consumers, and confirm collectors and leases are
   inactive.
4. Create a reviewed working copy of `retention-apply.sql`, set `@Apply = 1`,
   complete every evidence gate/reference, and execute the whole file in one
   SSMS window. Do not change selectors, remove `GO`, increase batch sizes or
   run concurrently. If interrupted, re-read evidence and rerun the same
   reviewed working copy.
5. Review hashes, child counts, cross-layer counts, FK checks,
   `DBCC CHECKCONSTRAINTS` and the complete post-apply object inventory.
6. Only after retention verification, run the recovery read-only review. Any
   MDF shrink needs a second explicit approval and fresh C: measurements.

## Acceptance boundary

- The ten snapshots and all dependent rows pass exact hash/count readback.
- GTFS, warehouse, realtime and control selectors pass their expected counts.
- The complete object inventory is unchanged.
- Actual Windows C: before/after bytes are recorded for any physical recovery.
- Collectors remain disabled; no continuous coverage or reporting KPI is
  claimed.

**Status:** `LIVE_READ_ONLY_EVIDENCE_CAPTURED / IN_PLACE_RETENTION_READY / APPLY_NOT_EXECUTED / MDF_RECOVERY_NOT_EXECUTED / POWER_BI_OUT_OF_SCOPE`.

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

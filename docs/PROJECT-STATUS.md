# Project status — final Data Engineering portfolio closeout

**Project display name:** Cologne Transit Data Engineering
**Existing remote:** `sonazariso/cologne-transit-intelligence`
**Reference date:** 19 October 2026
**Collector status:** **STOPPED / DISABLED** (Legacy7 retired; Tiered50 not a proven continuous production service)

## Complete as an engineering pilot

- Verified static/source contracts and fixed a **50-station** VRS parent panel; 8/20/22 tier structure, 5/15/30-minute cadence and source-identity continuity.
- Developed Python request, TRIAS parsing, normalized observations, SQL Server control/persistence, quota authorization, concurrency and audit semantics.
- Conducted bounded MDD real-data experiments, SQL persisted pilot and an idempotent historical live-research import with per-family count reconciliation.
- Recorded unsuccessful first scheduled Tiered50 heartbeat and deployment with deadline/coverage gap and disabled active writers without declaring a production success.
- Investigated SQL storage/compaction and made a capacity **NO_GO** decision instead of claiming sustainable throughput.
- Approved a coherent **ten-snapshot** source data portfolio sample with dependency/lineage preservation; executed SQL right-sizing and measured successful OS free-space recovery.

## Final data and Windows VM state (operator-reported 16 October)

| Item | Final observation |
| --- | --- |
| SQL database | `CologneTransitIntelligence` |
| Recovery model | SIMPLE |
| MDF | 3,072 MiB allocated |
| LDF | 264 MiB allocated |
| SQL integrity command | `DBCC CHECKDB ... WITH NO_INFOMSGS, ALL_ERRORMSGS` succeeded |
| Windows C: free | 50,320,338,944 bytes (46.86 GiB) |
| Windows disk recovered overall | approx. 31.63 GiB from initial 15.23 GiB free |
| Latest backup *history* shown | 14 October full; no new verified post-cleanup full evidenced |

The chosen snapshot IDs are `22, 93, 185, 189, 207, 224, 225, 226, 228, 229`. The pre-cleanup read-only manifest recorded their expected child counts; **final row-by-row post-cleanup SQL reconciliation and tested restore are still unverified**. Database shrink and `CHECKDB` do not establish either.

## Not shipped / no future work assumed

No continuously operating 50-station service, production-SLA evidence, citywide actual delay dataset, BI report, reporting endpoint, industrial-scale platform, automated DR, or certified provider reset-policy proof. No more HTTP collection is planned. Legacy ETL/static warehouse/Power BI decisions in historical documents must not be treated as current deliverables.

## Follow-up actions requiring the operator's environment

1. Obtain **fresh** full SQL Server backup to confirmed non-C destination, run appropriate media verify and isolated test restore, record checksum/restore result.
2. Run the [read-only ten-snapshot readback](../sql/portfolio/verify-final-retained-sample-readonly.sql), then independently preserve exact child-family, audit/lineage, SQL object counts/hashes and foreign-key checks after cleanup; correct manifests if actual differ.
3. Review external provider licensing and rights before sharing real sample data.
4. Review local GitHub Desktop diff and push; only **then** rename the remote, update clone URL, badges, local remotes and saved integration references.

This is a closed portfolio case study, not an invitation to reactivate the collector. See [validation results](VALIDATION-RESULTS.md), [quota/storage](QUOTA-CAPACITY-RETENTION.md), and the [historical index](HISTORICAL-RECORDS.md).

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

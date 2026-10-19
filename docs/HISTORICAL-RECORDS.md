# Historical engineering evidence and superseded material

The history is deliberately preserved where it documents *why decisions changed*. Its dates, stage statuses and deployment commands were valid only for their recorded investigation window. **Do not apply stale instructions to the current (offline) database/collector.**

| Area | Location / evidence | Present-day interpretation |
| --- | --- | --- |
| Source permissions and response semantics | [`source-contracts/`](source-contracts/README.md), [`stages/02...`](stages/02-mdd-trias-source-contract-audit.md) | Useful source contract; older future PostgreSQL direction superseded |
| Static feed and parent-station selection | [`stages/04...`](stages/04-cologne-scope-mode-classification.md), [`stages/12...`](stages/12-50-station-selection.md) | 50 validated identities and documented rank logic |
| Quota, phases and scheduling decision | [`stages/13...`](stages/13-tiered-collector-readiness.md) | Approved configuration, not uptime proof |
| Collector development | [`stages/14...`](stages/14-collector-implementation.md) | Implemented components and initial outage |
| Historical SQL/SQLite migration | [`operational/s98...`](operational/s98-live-research-import-and-cutover.md) | Real input/import reconciliation, not active SQLite runtime |
| Initial cutover incident | [`operational/tiered50-deployment-validation-report.md`](operational/tiered50-deployment-validation-report.md) | Failed first scheduled heartbeat, no long-running service |
| Storage feasibility | [`operational/tiered50-storage-v3-capacity-review.md`](operational/tiered50-storage-v3-capacity-review.md) | NO_GO / insufficient storage headroom |
| Ten-snapshot freeze | [`TEN_SNAPSHOT_RETENTION_MANIFEST.md`](TEN_SNAPSHOT_RETENTION_MANIFEST.md) | Oct 15 pre-delete evidence manifest, post-cleanup counts not yet verified |
| Closeout execution | [`PROJECT-STATUS.md`](PROJECT-STATUS.md) | Oct 16 SQL file sizes and CHECKDB operator evidence |

Historical Stage 06/10 SQL warehouse and matching experiments and prior file names remain as source/evolution evidence only. The former Power BI reports, their design documents, analytics-specific scripts and old iterative AI prompt history were removed from this **curated GitHub deliverable**. This does not alter any real SQL Server object, on-disk evidence archive, or actual runtime environment. The original v98 archive should be preserved privately if full research provenance must be retained.

Original logical identifiers (`CologneTransitIntelligence`, LaunchAgent labels, schema names and Python package imports) remain unchanged so the published source still corresponds to historical evidence; avoid cosmetic renames that invalidate operational traceability.

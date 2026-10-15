# Data Engineering portfolio closeout — evidence and acceptance

**Scope date:** October 15, 2026. Source: latest available sanitized project archive (S152, 444 files), existing authoritative project status, Stage 8/9/14 documents, SQL source schema and October 14 operator audit. This document is an editorial closeout **proposal**; no production deletion, collector activation, GitHub publication or disk reclamation is claimed.

## Goal and stop condition

Show a recruiter the **implemented** API-to-SQL Server engineering chain, operational and data-quality engineering decisions, bounded successful acquisition, SQL persistence and limitations. Stop extending ingestion and volume-oriented storage optimization for this project. Preserve **exactly 10 complete and auditable snapshots** with all dependent rows and required run/audit lineage, using the S154 operator-confirmed IDs `22, 93, 185, 189, 207, 224, 225, 226, 228, 229`, subject to live evidence, tested rollback and measured disk reclamation. The closeout is data-only: preserve the complete existing SQL Server structure for future Data Analyst training, including all deployed schemas, physical tables, columns, indexes, keys, foreign keys, views, functions, procedures and triggers.

## What worked (grounded historical evidence)

| Area | Evidence | What it proves |
| --- | --- | --- |
| MDD/TRIAS credential and request path | Stage 8 documents report a completed Köln Hbf pilot, 343 HTTP-200 responses, parse/reconciliation success | A bounded API acquisition/parse path worked; it does not prove persistent multiweek history |
| Source-backed matching prototype | Stage 9 evidence reports 60 observations, 51 MEDIUM and 9 UNMATCHED; zero HIGH evidence for the Stage 10 dependency | Matching/data-quality classifications and evidence gates; **not** an approved reliable trip KPI |
| Legacy7 schedule | Stage 14/source control evidence reports six expected scheduled Legacy7 buckets and preserved corrected heartbeat evidence | A historical limited collection/scheduling path worked |
| SQL Server | `create-source-faithful-persistence.sql` and recorded validation show `ctl` and `stg` lineage, source snapshots and structured observations | Real relational data landing and operational metadata path |
| Failure handling | The Tiered50 first scheduled bucket completed 2 of 18 target audits; 16 were skipped, and the collector was stopped | Safe failure/diagnostics are part of the evidence, not a successful Tiered50 rollout |

## What is not complete

- No continuous, representative 50-station time series. Missing fields and coverage prevent the desired system-performance KPIs.
- No validated Actual Delay derived from provider-supplied `RecordedAtTime` at the correct event identity.
- Historical PostgreSQL Stage 10 operational fact SQL is **not** the active SQL Server reporting implementation; Stage 10 is marked blocked in the original status.
- The live row-level manifest attachment and fresh post-cleanup Windows C: measurements have not been supplied; the approved ID set is recorded, but cleanup remains dry-run-only.
- The complete live object inventory and cross-layer data manifest are also not attached. Any future sample must retain a coherent GTFS dataset, relevant warehouse dimensions/facts, and the ETL lineage/configuration/audit rows needed to explain the retained MDD sample.

## Closeout deliverables

1. Public README focused on demonstrated Data Engineering competencies, with precise status caveats.
2. End-to-end documented data flow and SQL grains; representative code pointers and sanitized validation logs.
3. An exactly 10-snapshot curated dataset/extract (if provider sharing terms permit), manifest with actual SnapshotIds and checksum(s), enough SQL setup/readback instructions to reproduce a limited demo.
4. SQL-level integrity checks: FK completeness, target/run/audit lineage, exact counts and hash validation; explicit source missingness/coverage metrics.
5. Existing verified October 14 backup kept off-C, a new recovery point if changes after its finish require it, and documented rollback before destructive work.
6. Measured physical C: free-space before/after and MDF/LDF changes; record objective achievement, not estimated payload savings.
7. Sanitized documentation ready for owner-managed GitHub upload; truthful engineering demo screenshots only if obtained.

## Archival disposition

Retain old decisions, failed runs and complex storage experiments as **historical records**, clearly labeled not active. Do not republish old drafts as current installation instructions. Keep the default source code offline-safe. Active collection remains disabled for portfolio closeout.

## S154–S155 retention decision and method preference

The exact operator-confirmed retention set is:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

The preferred cleanup method is a small replacement database, but only an
independent peak measurement can establish that it fits on the existing C:
drive while preserving the 12 GiB safety floor. The replacement rehearsal
must preserve the **complete existing structure**—all schemas, physical
tables, columns, indexes, keys, foreign keys, views, functions, procedures
and triggers—not just the MDD tables. Its data must include all seven MDD
child families, source payloads, hashes, NULL/empty state, required `ctl`
lineage/configuration/audit rows, a coherent GTFS sample and relevant
warehouse dimensions/facts. If the peak does not fit, the bounded in-place
data-only fallback requires separate approval.

GTFS and warehouse **data** require separate portfolio-necessity and FK
closure review, while their tables and all other object definitions remain
preserved. SQL metadata, SQL-Agent jobs, direct clients, notebooks, exports
and Power BI/external consumers must be reviewed explicitly before any
physical payload or database change. The inventory must distinguish physical
tables from views before any retention decision.

The final source-controlled SQL path is read-only complete-object inventory →
exact-ID candidate/readback evidence → cross-layer data/FK manifest →
schema-complete replacement feasibility review → `retention-dry-run.sql`.
`retention-apply.sql` and file-space recovery remain disabled-by-default,
data-only templates and were not executed; they cannot remove schemas or
replace views, modules, keys, indexes or other structure.

## Exact operator approval required before cleanup

The administrator must sign a dated approval that names the ten IDs above,
attaches the live hashes/counts/lineage, complete schema/object inventory,
cross-layer GTFS/warehouse/ETL data manifest with FK closure, and
external-consumer review, verifies an off-C `RESTORE VERIFYONLY WITH CHECKSUM`
recovery point, and records fresh before/after MDF/LDF/tempdb/C: measurements.
The approval must confirm that the selected schema-complete method keeps
worst-case C: free space at or above 12 GiB and does not expand C:. It
authorizes the dry run only; destructive data apply and any shrink require
their own explicit approval.

## Missing sign-offs

- **Retention:** user confirmed the exact ten IDs; live row/hash/child evidence, dependencies, executable SQL and rollback boundary still require confirmation.
- **Capacity:** current live disk/log/SQL allocation preflight, restore point and purge/shrink lab proof.
- **Consumers:** actual views/jobs, direct SQL consumers, external consumers, GTFS/warehouse necessity and FK dependencies.
- **GitHub:** owner-managed publication, correct target URL, publication license/permission and secret scan.

**Status:** `OPERATOR_ID_SET_CONFIRMED / SCHEMA_PRESERVATION_REQUIRED / CROSS_LAYER_DATA_MANIFEST_PENDING / DRY_RUN_ONLY / RETENTION_AND_PHYSICAL_RECLAMATION_NOT_EXECUTED`.

## Final statement for GitHub

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.


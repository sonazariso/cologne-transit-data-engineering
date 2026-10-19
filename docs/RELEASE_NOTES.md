# Documentation release — 19 October 2026

**Project display name:** Cologne Transit Data Engineering. This is a local source/doc documentation release; no online GitHub change or SQL database mutation occurred here.

## Scope of release

- Rewrote the active front page, project status, architecture, 50-station collector, data pipeline, source dictionary, quality/observability, quota/capacity, operations, reproducibility, verification and closeout guidance.
- Generated the **exact 50-station list** from `config/tiered50_runtime_manifest.json`, validating 8/20/22 tiers and six-phase loads of 19/18/18/18/19/18.
- Corrected the obsolete "shrink/delete not executed" assertions in the *current* front-facing documents using operator-supplied measurements from October 16. Historical evidence is date-labelled; it is not silently rewritten into a new production claim.
- Curated public-source delivery: removed nested source/data ZIPs, raw discovery JSONL, local lifecycle logs, raw administrator captures, stale Python `.bak`, iterative development prompts, analytics-only folder/scripts and Power BI-only stage design files. Preserved historical operational, Stage 06/10 and SQL lineage contracts where they are technical provenance, not active reporting deliverables.
- Added a read-only SQL query for a new post-cleanup 10-snapshot count/file/history review. It **has not been executed**.
- Kept runtime collector source, TRIAS parser, provider handling, SQL persistence schema and scheduler files **unchanged**. Updated historical *test document assertions* to reflect new project scope and removed prompt fixtures, without modifying collector behavior.

## Local verification

- Python compilation of `src` and `scripts`: PASS.
- Offline `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'`: **458 tests run, 3 skipped, 0 failures, 0 errors** as observed in the release working folder.
- Local Markdown link resolution: 0 broken relative links at package build.
- Manifest panel: 50 unique IDs, A8/B20/C22, 5/15/30 min, requested N20/30/30, 19/18/18/18/19/18 phase loads: PASS.
- ZIP integrity: checked during packaging.
- No live database, provider API, GitHub remote, Mac Keychain or Windows VM access was used in local packaging.

## Mandatory remaining external validation

1. Operator checks current 10-SnapshotId and dependent-row counts/hashes and FK closure with read-only SQL.
2. Operator produces and validates a **new** off-C full backup and performs an isolated recovery test.
3. Repository owner reviews exact GitHub Desktop changes and redistribution/license status, then publishes.
4. **Only after push is visible on the existing URL**, rename remote to `cologne-transit-data-engineering` and update local `origin` plus external references.

These are not falsely marked complete by an offline source package.

# Stage 14 live-research first gap audit

This is a read-only audit of the retained S78 Köln Heumarkt evidence at `~/cologne-transit-data/live-research/collector.sqlite3`. No new MDD request, DNS activity, raw-body copy, or database rewrite was performed.

The database is `LIVE_RESEARCH`, collection context `LIVE_RESEARCH_SINGLE_1`, and observed schema version `2` (the S79 schema change is implemented in the collector for future state). The successful audit is audit `2`, snapshot `1`, HTTP 200, with 20 parsed events. One sanitized raw JSON artifact remains retained outside the repository until `2026-10-02T11:30:58.508390Z`, within the 24-hour limit.

The response contains 121 parser gaps across seven normalized field groups. The full machine-readable grouping, source-path patterns, event/result impact, call scope, parent context, sanitized value shapes, retention classification, and parser action are in [14-live-research-first-gap-audit.json](14-live-research-first-gap-audit.json).

Summary:

- `parallelservice`: 20, structural STOP_EVENT container; excluded from future parser-gap emission.
- `situations`: 1, structural RESPONSE_CONTEXT container; excluded from future parser-gap emission.
- `directionref`, `lineref`, `mode`, `operatorref`, `publishedlinename`: 100, all ServiceSection-scoped and therefore retained as `NEEDS_SEMANTIC_REVIEW`. They are not promoted to event identity or forced into a generic parser mapping.

The structural correction affects subsequent parser output only; the S78 database remains immutable evidence. The expected future gap count for the same response shape is therefore 100, not zero. `recorded_at_time_evidence` remains separate source evidence, while the S79 timing reporting view is a derived reporting projection over normalized timing observations.

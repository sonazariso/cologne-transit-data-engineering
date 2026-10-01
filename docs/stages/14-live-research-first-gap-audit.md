# Stage 14 live-research first-gap audit and S80 migration reconciliation

This artifact records the read-only S78 Köln Heumarkt audit and the subsequent
offline S80 correction of the existing `LIVE_RESEARCH` SQLite file at
`~/cologne-transit-data/live-research/collector.sqlite3`. No new MDD request,
DNS activity, raw-body copy, or live collector run was performed.

The initial S78 evidence was audit `2`, snapshot `1`, HTTP 200, with 20 parsed
events and schema version `2`. S80 migrated that same file to schema version
`4` at `2026-10-01T13:07:28.867592Z`, without rewriting the stored snapshot
JSON. The migration inserted 995 idempotent timing-observation rows and 20
source-faithful ServiceSection rows reconstructed from the retained sanitized
raw response. SQLite `integrity_check` and `quick_check` both passed before
and after migration; RecordedAtTime reconciliation passed with zero
non-NULL source occurrences. The current-day validated backup remains in the
three-version backup policy (one version exists because only one UTC day is
present), and the temporary pre-migration safety backup was removed only after
validation.

The response contains 121 historical parser gaps across seven normalized field
groups. The full machine-readable grouping, source-path patterns, event/result
impact, call scope, parent context, sanitized value shapes, retention
classification, and parser action are in
[14-live-research-first-gap-audit.json](14-live-research-first-gap-audit.json).

Summary:

- `parallelservice`: 20, structural STOP_EVENT container; excluded from future parser-gap emission.
- `situations`: 1, structural RESPONSE_CONTEXT container; excluded from future parser-gap emission.
- `directionref`, `lineref`, `mode`, `operatorref`, `publishedlinename`: 100 historical ServiceSection field occurrences across 20 source sections, now retained explicitly at ServiceSection grain with source paths and bounded source representations. They are not promoted to timing identity.
- The corrected parser emits zero gaps for this retained response shape. Unknown relevant future ServiceSection extensions remain parser-gap evidence.

`recorded_at_time_evidence` remains separate source evidence, while the timing
reporting view is a derived current-state projection over normalized timing
observations. The S80 changes do not start Tiered50 production sampling or
Stage 15B.

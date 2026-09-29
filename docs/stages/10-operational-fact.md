# Stage 10 — Operational Fact

**Status:** `NOT COMPLETE — IMPLEMENTATION CONTRACT ADDED; DATABASE/SOURCE VALIDATION BLOCKED`  
**Date:** `2026-09-29`

Stage 10 is limited to the derived operational outcome fact. It does not
change Stage 9 matching, append-only realtime history, collector behavior,
sampling, static GTFS scope, Actual Physical Delay, or downstream Power BI.

## Implementation boundary

The PostgreSQL implementation is in:

- [`05-create-operational-stop-outcome.sql`](../../sql/04-warehouse/05-create-operational-stop-outcome.sql)
- [`06-refresh-operational-stop-outcome.sql`](../../sql/04-warehouse/06-refresh-operational-stop-outcome.sql)
- [`07-validate-operational-stop-outcome.sql`](../../sql/04-warehouse/07-validate-operational-stop-outcome.sql)

The refresh consumes the approved Stage 9 row-level input contract
`wrk.vwCologneRealtimeTripMatch`. That view is deliberately not recreated by
Stage 10: the current checkout has no physical Stage 9/source database and no
such view. The refresh raises a prerequisite error when it is absent, and the
validator returns `BLOCKED` rather than treating an empty substitute as a
passing population.

## Approved semantics

The fact grain is exactly one row per:

```text
(DateKey, ScheduledStopEventKey)
```

The PostgreSQL physical columns are `date_key` and
`scheduled_stop_event_key`. Only `ExactStopMatch` and
`ParentStationFallback` are eligible. `StaticCoverageMissing` and
`Unresolved` remain outside the fact. The unique constraint and validation
compare both directions: usable Stage 9 grains missing from the fact and
stale fact grains missing from the current usable Stage 9 population.

For each grain, all observations are ordered deterministically by:

```text
ObservedAtUtc ASC, ObservationKey ASC
ObservedAtUtc DESC, ObservationKey DESC
```

The first/latest observation keys and timestamps, observation count, timing,
and platform snapshots are taken from those actual rows. Timetabled arrival is
required to be consistent across the matched grain; conflicting values reject
the refresh and are reported by validation.

`LatestObservedEstimatedArrivalUtc` is the estimate in the latest observation.
It is not the latest non-NULL historical estimate. Therefore, when an earlier
estimate exists and the latest observation contains NULL, both
`latest_observed_estimated_arrival_utc` and
`final_observed_estimated_delay_minutes` remain NULL. No timetable, departure,
collector timestamp, actual arrival, or inferred on-time value is substituted.

`PlatformChangeEvidence` has only these values:

- `Changed`: at least one non-empty planned/estimated pair differs;
- `Unchanged`: at least one comparable pair exists and all comparable pairs are equal;
- `Unknown`: no comparable pair exists.

NULL, empty, and whitespace-only platform values are unavailable evidence.
`Unknown` is not `Unchanged`.

`SituationLinkCount` means the count of persisted observation-to-situation link
rows across the consolidated source observations. It is not a count of
distinct situations and is not proof of causality. `HasSituationEvidence` is
exactly true when that count is greater than zero.

The refresh is transactional, deterministic, repeatable, and idempotent for
unchanged input. It upserts/reconciles only the derived fact and never deletes
or updates append-only realtime staging/history or collector-control records.

## Validation status

The validator covers duplicate grain, exact Stage 9 reconciliation, eligibility,
first/latest lineage, source observation count, NULL-safe timing, the
earlier-estimate/latest-NULL case, timetable consistency, platform evidence,
situation evidence, identity consistency, and two-refresh semantic
repeatability. It ignores only refresh timestamp differences for repeatability.

The current repository has no physical PostgreSQL database, no loaded source
observation tables, and no `wrk.vwCologneRealtimeTripMatch`. In addition, the
current Stage 9 SQL contract exposes `core.source_event_match` rather than the
Stage 10 input view and does not physically persist the source observation
tables. Creating a guessed adapter would change or duplicate approved Stage 9
semantics, so that dependency remains outside this Stage 10 change.

Consequently, the Stage 10 validation cannot currently produce evidence for the
live population and Stage 10 must remain **NOT COMPLETE**.

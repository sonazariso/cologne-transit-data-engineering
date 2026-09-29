# Stage 10 — Operational Fact

**Status:** `NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED`
**Date:** `2026-09-29`

Stage 10 is limited to the derived operational service-event fact. It does not
change Stage 9 matching, append-only source history, collector behavior,
sampling, static GTFS scope, Actual Physical Delay, or downstream reporting.

## Authoritative implementation

The source-controlled PostgreSQL artifacts are:

- [`05-create-operational-stop-outcome.sql`](../../sql/04-warehouse/05-create-operational-stop-outcome.sql)
- [`06-refresh-operational-stop-outcome.sql`](../../sql/04-warehouse/06-refresh-operational-stop-outcome.sql)
- [`07-validate-operational-stop-outcome.sql`](../../sql/04-warehouse/07-validate-operational-stop-outcome.sql)

The physical fact name is:

```text
dw.fact_operational_service_event
```

No compatibility view, quoted legacy object, or competing fact is created.

## Fact grain and identity

The semantic grain is:

> one consolidated operational outcome for one approved canonical service
> event for its canonical `service_date`, using observations associated through
> one explicitly selected COMPLETE Stage 9 matching run/version.

The unique final fact grain is:

```text
(service_date, canonical_service_event_key)
```

`service_date`, `canonical_service_event_key`, `scheduled_time_seconds`, and
the canonical event identity come from `core.canonical_service_event`.
`service_date` is the business date, not a surrogate date key. The fact also
retains `matching_run_key`, `matching_version`, `match_confidence`,
`match_method`, and `data_quality_classification` for auditability.

The canonical timetable is derived from the Stage 9
`service_date`/`scheduled_time_seconds` representation with the already
approved `Europe/Berlin` service-day interpretation. Extended service-day
seconds are not wrapped at midnight. Source `TimetabledTime`, when retained by
the source layer, is evidence and does not replace the canonical event.

## Stage 9 eligibility and run selection

The refresh input begins with:

```text
core.source_event_match
JOIN core.canonical_service_event
```

and is restricted to exactly one caller-supplied pair:

```text
matching_run_key
matching_version
```

The control row must be `COMPLETE`, and both values must match the same Stage 9
execution. There is no implicit latest-run selection and no union of historical
matching runs. This prevents one source observation from entering the fact more
than once because it appeared in multiple historical executions.

The exact eligibility rule is:

```text
match_confidence = 'HIGH'
```

`HIGH` is eligible for a stop-event cross-source association. `MEDIUM` remains
contextual evidence and is not promoted into the fact. `LOW` and `UNMATCHED`
are excluded. Stage 9 data-quality classifications remain a separate retained
field; they are not translated into another status vocabulary.

The current source-backed Stage 9 artifact contains `0 HIGH`, `51 MEDIUM`, and
`9 UNMATCHED` decisions. That is a legitimate current-data result. It does not
authorize promotion of `MEDIUM` rows.

## Source operational evidence boundary

`core.source_event_match` supplies matching decisions and lineage only. It does
not supply `EstimatedTime`, `PlannedBay`, `EstimatedBay`, or Situation links.
The refresh therefore reads those fields from durable source observations only
after checking for these exact physical relations and columns:

```text
src.mdd_stop_event_observation
  mdd_stop_event_observation_key
  source_observation_identity
  observed_at_utc

src.mdd_call_observation
  mdd_call_observation_key
  mdd_stop_event_observation_key
  call_scope
  event_type
  estimated_time_utc
  planned_bay
  estimated_bay

src.mdd_stop_event_situation_link
  mdd_stop_event_situation_link_key
  mdd_stop_event_observation_key
```

Stage 10 does not create or guess these source schemas. `event_type` and
`call_scope = 'THIS'` keep arrival/departure and call context explicit. If a
relation or column is missing, refresh and validation return `BLOCKED` with the
precise prerequisite rather than fabricating operational values.

## Consolidation semantics

Repeated source observations for one canonical event are ordered by:

```text
source_observed_at_utc ASC, source_observation_identity ASC
```

for the first row, and the exact reverse for the latest row. The fact retains
the first/latest source match keys, source observation identities, source
timestamps, observation count, and first/latest estimates.

If an earlier observation has an estimate but the latest observation has no
estimate, the latest estimate and latest estimated delay remain `NULL`. No
`COALESCE` or carry-forward is used. `EstimatedTime`, source `RecordedAtTime`,
and `ObservedAt` remain distinct; Stage 10 does not create Actual Physical
Delay.

`PlannedBay` and `EstimatedBay` remain separate. `bay_evidence` is:

- `Changed` when at least one comparable non-empty pair differs;
- `Unchanged` when at least one comparable pair exists and every pair agrees;
- `Unknown` when no comparable pair exists.

Whitespace-only and NULL values are unavailable evidence. This classification
does not prove which physical bay was ultimately used.

`situation_link_count` counts persisted observation-to-Situation association
rows, not distinct Situation identities. `has_situation_evidence` is exactly
`situation_link_count > 0`. Situation linkage is evidence only and never a
causal claim.

## Validation and current result

The validator requires explicit run/version parameters and checks:

- duplicate final fact grain;
- `HIGH`-only fact eligibility;
- explicit matching-run/version lineage;
- selected-run source-observation duplication and historical-run isolation;
- missing and stale current event grains;
- first/latest source lineage;
- source-observation count;
- first/latest timing and latest-NULL preservation;
- PlannedBay/EstimatedBay evidence;
- Situation association count and flag;
- canonical service-event identity and separate data-quality classification;
- repeat-refresh semantic differences.

Repeatability is validated in the required order:

```text
refresh #1 -> snapshot A -> refresh #2 with unchanged input -> snapshot B
```

Only semantic columns are compared; `refreshed_at_utc` is ignored. A nonzero
difference is `FAIL`.

The current checkout has no usable PostgreSQL database and no physically
implemented durable source-observation population. The retained Stage 9
source-backed evidence also has zero `HIGH` rows. Therefore no live Stage 10
source-backed PASS can be claimed. The correct status is:

```text
NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED
```

No Stage 11 or later work was started.

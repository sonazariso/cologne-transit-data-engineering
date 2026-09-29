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

The physical fact enforces foreign keys to `ctl.matching_run`, the composite
`(matching_version, canonical_service_event_key)` identity in
`core.canonical_service_event`, and both first/latest `source_event_match`
keys. Stage 10 validation also checks those relationships and the selected-run
lineage without changing Stage 9.

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
Stage 10 therefore requires this source-owned working-layer adapter/input
relation:

```text
wrk.v_stage10_operational_observation_input
```

This is a derived Stage 10 interface contract between future durable source
persistence and the operational fact. It is not a new physical source schema,
and Stage 10 does not create it. The adapter must represent exactly one
operational evidence row for each
`(source_system, source_namespace, source_observation_identity, event_type)`
and expose only this logical input:

| Field | Logical type | Meaning |
| --- | --- | --- |
| `source_system` | non-null `text` | Source-system identity used by Stage 9. |
| `source_namespace` | non-null `text` | Source namespace; it is part of the source identity. |
| `source_observation_identity` | non-null `text` | Source observation identity within the system and namespace. |
| `source_observed_at_utc` | non-null `timestamptz` | Observation timestamp used for first/latest ordering; it must reconcile to the selected Stage 9 observation. |
| `event_type` | non-null `text` | `ARRIVAL` or `DEPARTURE` operational evidence row. |
| `estimated_time_utc` | nullable `timestamptz` | Nullable source estimate; it is never an Actual Time. |
| `planned_bay` | nullable `text` | Nullable/blank-normalized planned-bay evidence. |
| `estimated_bay` | nullable `text` | Nullable/blank-normalized estimated-bay evidence. |
| `situation_link_count` | non-null `bigint` | Non-negative persisted observation-to-Situation association-row count; no association is represented as `0`. |

The adapter may translate richer future source structures, including separate
arrival/departure timing collections and association storage, without exposing
source internal keys, call-scope storage, or guessed `src` table names here.
Stage 10 joins the complete namespace-qualified identity tuple and the event
type; `source_observation_identity` alone is never treated as universal. If the
adapter relation or any required field is missing, refresh and validation return
`BLOCKED` rather than fabricating operational values or source persistence.

## Consolidation semantics

Repeated source observations for one canonical event use this complete
deterministic ordering contract. For the first observation:

```text
source_observed_at_utc ASC,
source_system ASC,
source_namespace ASC,
source_observation_identity ASC,
source_event_match_key ASC
```

For the latest observation, every component uses the exact reverse:

```text
source_observed_at_utc DESC,
source_system DESC,
source_namespace DESC,
source_observation_identity DESC,
source_event_match_key DESC
```

`source_event_match_key` is the final stable tie-breaker. The fact retains the
first/latest source match keys, source observation identities, source
timestamps, observation count, and first/latest estimates.

Stage 9 does not guarantee that repeated `HIGH` observations for one canonical
event use identical `match_method` or `data_quality_classification` values.
Stage 10 therefore retains both values from the deterministically latest source
observation, using the same descending ordering above, instead of rejecting an
otherwise valid event or choosing an arbitrary `MIN()` value. The data-quality
classification remains separate lineage and is not translated into a status.

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
- complete source identity tuple reconciliation;
- orphan matching-run, canonical-event, first-source-match, and latest-source-match references;
- selected-run source-observation duplication and historical-run isolation;
- missing and stale current event grains;
- first/latest source lineage;
- source-observation count;
- first/latest timing and latest-NULL preservation;
- PlannedBay/EstimatedBay evidence;
- Situation association count and flag;
- canonical service-event identity and separate data-quality classification;
- latest-observation `match_method` and `data_quality_classification` lineage;
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

# Future Source-Observation and Persistence Contract

**Status:** `APPROVED SEMANTIC/DESIGN GUARDRAIL — SOURCE PERSISTENCE DEFERRED; STAGE 9 REGRESSION IMPLEMENTED; SOURCE-BACKED ACCEPTANCE BLOCKED`<br>
**Current stage:** Stage 3 `COMPLETE`; Stage 3A is `COMPLETE — APPROVED`,
Stage 3B/3C design contracts are approved, and Stage 9 consumes this contract
without activating source persistence

This document records the approved semantic contract for a later source-faithful
observation and persistence architecture. It is not a database schema, table
definition, migration, collector implementation, scheduler design, or KPI
approval.

## 1. Future architecture boundary

The intended conceptual flow is:

```text
SOURCE
  -> COLLECTOR
  -> APPEND-ONLY SOURCE OBSERVATIONS
  -> DERIVED ANALYTICAL STATE
```

Source observations remain the durable evidence boundary. A source event or
Situation disappearing from a later response must never erase an observation
that was previously seen. Derived analytical state may be recomputed later from
the retained evidence.

MDD/TRIAS and GTFS-RT remain source-faithful and independently interpretable
before any future normalization or reconciliation. A source-specific field
must not be silently mapped to an ambiguous common field merely to make the
sources look equivalent.

## 2. Independent timing concepts

Future observation data must preserve these concepts separately:

| Concept | Contract |
|---|---|
| `TimetabledTime` | Source-provided scheduled/planned event time. |
| `EstimatedTime` | Source-provided prediction. It may change across observations and remains a prediction even after the event window has passed. |
| `RecordedAtTime` | Source-provided recorded/actual event time. It is nullable and may be populated only when the source explicitly supplies it. |
| `ObservedAt` / `CapturedAt` | Collector timestamp for when the source response was observed. It is lineage, not an event's actual time. |
| `RecordedAtTimeObservedAt` | Conceptual lineage value recording the collector observation at which a non-null source `RecordedAtTime` was first seen. It is not a substitute for the source value. |

The following are non-negotiable invariants:

- `RecordedAtTime` must never be derived from `EstimatedTime`, the last
  `EstimatedTime`, `TimetabledTime`, `ObservedAt`/`CapturedAt`, or event
  disappearance.
- `EstimatedTime` must never be copied into `RecordedAtTime`.
- If the source never supplies `RecordedAtTime`, it remains `NULL`; no
  collector timestamp or fallback event time may be synthesized.
- When a non-null source `RecordedAtTime` is observed, preserve its exact
  source value and its first-seen observation timestamp. Later non-null source
  observations remain append-only evidence; a later `NULL` or disappearance
  must not overwrite or erase the previously captured value.
- The parsed `RecordedAtTime` record and its lineage are the durable evidence
  required for later use. A full raw response is not a permanent source of
  record. During the bounded Stage 8 validation exception, a successful
  response with positive `RecordedAtTime` evidence may retain its
  corresponding raw payload temporarily for forensic review, for no more than
  24 hours; ordinary successful raw responses are cleaned normally.
- `TimetabledTime`, `EstimatedTime`, and `RecordedAtTime` must not be collapsed
  into one generic `EventTime` concept.

For sources whose schema has a different timing model, the source-specific
meaning remains authoritative. In particular, GTFS-RT `StopTimeEvent.time`
must not be promoted to this contract's confirmed `RecordedAtTime` without
provider-specific semantic validation.

Actual Delay remains **NOT APPROVED** until source-provided `RecordedAtTime`
evidence is observed, semantically validated, and matched to the correct event.
Prediction-accuracy KPIs remain unapproved on the same basis.

## 3. Estimate history

Future persistence must preserve the evolution of source predictions rather
than retain only the latest `EstimatedTime`. Repeated observations of one
service event may therefore retain, for example:

```text
ObservedAt 10:00  -> EstimatedTime 10:18
ObservedAt 10:05  -> EstimatedTime 10:20
ObservedAt 10:10  -> EstimatedTime 10:22
RecordedAtTime 10:21:47  (only if explicitly source-provided)
```

This evidence is intended to support later analysis of first versus latest
estimates, estimate revisions, prediction error by lead time, stability, and
accuracy where a validated `RecordedAtTime` actually exists. Preserving this
capability does not approve those KPIs now.

Transport duplicates may be handled by a later idempotency rule, but
deduplication must not discard distinct source observations or estimate
revisions.

## 4. Actual-time capture state

The future lifecycle may conceptually distinguish:

```text
ActualTimeCaptured = false
ActualTimeCaptured = true
```

`ActualTimeCaptured` becomes true only after a non-null, source-provided
`RecordedAtTime` has been captured. Once true, the system does not need to
continue polling that event specifically to obtain an actual timestamp.

This does not stop all tracking of the event when another independent objective
still exists, including Situation evolution, Cause updates, or
cancellation/status evolution. Actual-time tracking and disruption/cause
tracking are separate concerns. This section documents lifecycle semantics
only; it does not authorize production scheduling.

## 5. Situation and Cause history

Disruption information follows the same historical-evidence principle. If a
source reports a Situation, Cause, `SituationFullRef`, descriptive text,
effect, reason, or other provider-reported disruption evidence at one
observation and it disappears later, the earlier observation remains retained.

Because Situation/Cause information may evolve, future persistence must preserve
the sequence of observed source-reported versions rather than only one mutable
`Cause` value. The completed MDD discovery demonstrates that the persistence
contract must cover more than a dedicated structured reason field. Where
explicitly source-reported, retain evidence such as:

- `ObservedAt` / `CapturedAt`;
- Situation identity, including `ParticipantRef`, `SituationNumber`, and
  `Version` when present;
- references, including `SituationFullRef`, its association scope, and the
  service/event identity needed to retain the link;
- source field/path and source-specific field names;
- `Summary`, `Description`, and `Detail`;
- lifecycle/validity evidence such as `CreationTime`, `ValidFrom`, and
  `ValidTo`;
- observed situation context such as `Affects`, `Source`, `Priority`, and
  other version-related fields;
- meaningful dedicated structured reason/effect fields when actually present;
- provider-reported reason or operational-explanation text in Situation text
  and `IncidentCall` service-information `GeneralAttribute`/`Attribute`
  `Code` + sibling `Text`, including the original source path; and
- operational status fields separately from reason text.

The bounded discovery sample had provider-reported reason-text evidence but no
meaningful dedicated structured reason field. `unknownReason = unknown` is a
placeholder, not a reason. Provider-reported text remains source evidence and
must not be promoted to a proven or confirmed cause.

`ValidFrom`/`ValidTo` are source-reported validity values where present; a
later disappearance must not be converted into a synthetic `ValidTo` or
disappearance-time cause.

Do not create a normalized cause taxonomy in this change. Do not infer root
cause or claim that a reported Situation caused a measured delay merely because
the records are associated.

## 6. Intended future database direction

The redesigned persistence architecture is intended to move toward
**PostgreSQL**. This is an architectural direction only:

- no PostgreSQL migration is performed here;
- no production PostgreSQL tables, schema, or persistence code are created;
- existing SQL Server assets are not removed or rewritten; and
- database choice does not change source availability or what MDD/TRIAS or
  GTFS-RT can provide.

The future direction is motivated by the desired architecture: append-only
realtime observation history, preserved source `NULL` semantics, separate
source timing fields, source-faithful Situation/Cause history, later analytical
derivation, and PostgreSQL-friendly JSONB support where justified. JSONB is a
future implementation option for appropriately bounded source-shaped context;
it is not approval for permanent raw-payload retention.

## 7. Raw evidence boundary

A future design may evaluate short-term raw-payload retention for forensic
reprocessing and parser improvements, especially where a source field was
present but not yet understood. For the current Stage 8 hardening exception,
the boundary is explicit: positive source-provided `RecordedAtTime` responses
receive bounded temporary raw-evidence retention, while ordinary successful
responses do not. The maximum is 24 hours; it must not become permanent raw
storage, and a longer period requires a separate explicit decision.

The sanitized parsed `RecordedAtTime` observation and lineage remain the
durable evidence. If zero occurrences are observed, retained aggregate counts
may describe only the successful responses, call/timing contexts, event
counts, call scopes, and arrival/departure contexts inspected in that bounded
sample. They must not be presented as provider-wide absence.

Any later retention decision must consider storage volume, privacy/data
considerations, reprocessing need, and operational cost. Durable
source-faithful parsed evidence remains the primary future record unless a
separate approval says otherwise.

## 8. Current-stage guardrail

Stage 3 is complete as a source-evidence and design stage:

> **COMPLETE — APPROVED; IMPLEMENTATION NOT STARTED**

The Stage 3A conclusion is approved for the bounded evidence already
documented. The Stage 3B collection/cadence policy and Stage 3C matching design
are approved separately. This contract still does not authorize PostgreSQL
migration, database schema, production collector persistence, scheduler
changes, Power BI changes, Actual Delay KPIs, or prediction-accuracy KPIs.

## 9. Stage 9 matching handoff

Stage 9 implements the derived matching/data-quality layer that consumes this
source-observation contract at regression level. The implementation preserves
source rows and source namespaces, requires explicit sampling-policy/era
lineage for matching decisions, records stop/line mapping versions and
validated correlation basis separately from MDD and GTFS-RT observations, and
does not create an operational outcome fact, synthesize `RecordedAtTime`, or
activate a collector. The bounded source-backed Stage 9 acceptance is complete
for the retained live evidence population: the run used the Stage 8 parser and
bundle builder, the Stage 9 adapter, independent GTFS service-date
establishment, and final matching. See the
[Stage 9 implementation record](../stages/09-matching-data-quality.md) and
[matching SQL contract](../../sql/stage_09_matching_quality.sql).

Related documentation:

- [MDD/TRIAS response contract](mdd-trias-response-contract.md)
- [MDD/TRIAS field-retention matrix](mdd-trias-field-retention-matrix.md)
- [Realtime collector requirements](../collector-design/realtime-collector-requirements.md)
- [Realtime collection design readiness](../stage-03/realtime-collection-design-readiness.md)
- [MDD/TRIAS raw-payload retention decision](mdd-trias-raw-retention.md)
- [Stage 3 completion decision](../stage-03/stage-03-completion-decision.md)

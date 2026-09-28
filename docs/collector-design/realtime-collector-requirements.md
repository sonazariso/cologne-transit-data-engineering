# Realtime Collector Requirements

> **Stage 02 approved source requirements; Stage 3B collection/cadence policy
> approved; Stage 7 pilot design approved; implementation and activation are
> deferred.**

This document is the durable handoff from the approved Stage 02 MDD/TRIAS
source-contract audit to the later realtime collector-design stages. It records
source-faithful requirements that must be reviewed before collector
implementation begins. It does not approve a production collector, polling
cadence, database schema, SQL table, or production code.

The approved evidence and semantic boundaries remain in the [Stage 02
source-contract package](../source-contracts/README.md) and the [detailed
Stage 02 audit](../stages/02-mdd-trias-source-contract-audit.md). The related
near-event validation is described in the [RecordedAtTime lifecycle
validation](../source-contracts/mdd-trias-event-lifecycle-validation.md). The
future timing, history, and persistence boundary is defined in the [future
source-observation and persistence contract](../source-contracts/future-observation-persistence-contract.md).
The approved Stage 3 collection/cadence boundary is defined in the [Stage 3B
contract](../stage-03/03b-collection-strategy-cadence-contract.md). It approves
a policy and guardrails, not a fixed production schedule. Stage 7 now adds a
bounded MDD-only Stage 8 pilot handoff; its settings must not be promoted to
production defaults.

## 0. Approved source roles and separation

The post-Stage-02 source direction is:

- **MDD/TRIAS — primary realtime source** for Cologne-oriented,
  station-targeted prediction, call-sequence, platform/bay, and situation
  evidence; and
- **GTFS-RT — complementary/secondary realtime source** for broad operational
  coverage, TripUpdate/StopTimeUpdate state, `SKIPPED`, `NO_DATA`,
  `ServiceAlerts`, and independent cross-source evidence.

Future collection should therefore be conceptually separated into an
`MddObservationCollector` path and a `GtfsRtObservationCollector` path, each
preserving its source-faithful semantics and lineage. This is a design
direction only: it does not approve collector implementation, table names,
schemas, migrations, cadence, or a common canonical record.

Do not initially blend MDD and GTFS-RT into one ambiguous observation. In
particular, do not introduce `ActualTime` or `ActualDelay` to make the sources
look equivalent. A later normalization/reconciliation layer is a separate
design problem after source-specific staging is approved.

## 1. Prospective collection is required

Stage 02 historical tests established the following results for the specific
tested Köln Hbf arrival request:

| Horizon | Result |
|---|---|
| 1D | `HISTORICAL_TIMETABLE_ONLY` |
| 7D | `HISTORICAL_TIMETABLE_ONLY` |
| 30D | `HISTORICAL_TIMETABLE_ONLY` |
| 90D | `HISTORY_NO_EVENT` |
| 180D | `HISTORY_NO_EVENT` |

The 90D and 180D outcomes do not establish an exact provider retention cutoff.
The collector design must therefore:

- not assume MDD can reconstruct past realtime state;
- not rely on later retrospective queries to recover missed estimates;
- treat missed collector observations as potentially permanently lost
  realtime evidence; and
- make future collector reliability an explicit design concern.

## 2. Preserve source timestamps

When available, retain these fields independently and preserve their source
semantics:

- `TimetabledTime`;
- `EstimatedTime`;
- `RecordedAtTime`;
- `EstimatedTimeLow`;
- `EstimatedTimeHigh`;
- request/observation timestamp;
- response timestamp where meaningful;
- arrival-versus-departure timing context; and
- call scope: `PreviousCall`, `ThisCall`, or `OnwardCall`.

`RecordedAtTime` is nullable and may be populated only when the source
explicitly supplies it. Never derive it from `EstimatedTime`, the last
estimate, `TimetabledTime`, `ObservedAt`/`CapturedAt`, or event disappearance.
When a non-null source value is observed, retain the exact value and the
conceptual `RecordedAtTimeObservedAt` first-seen observation timestamp. A later
`NULL` or disappearance must not overwrite or erase it.

Never persist only a derived delay while discarding the source timestamps.

## 3. Never promote an estimate to actual

The collector must never infer either of the following:

```text
last EstimatedTime == actual arrival/departure
past EstimatedTime == RecordedAtTime
RecordedAtTime == ObservedAt
RecordedAtTime == disappearance time
```

`EstimatedTime` remains a prediction even after the event has passed. Actual
performance requires a validated actual-time field or source.

## 4. Preserve estimate evolution

The future design should be capable of preserving multiple observations of the
same service event over time when analytically justified. This enables future
questions such as:

- how predictions changed approaching arrival;
- prediction stability;
- prediction accuracy if an actual timestamp later becomes available; and
- how early delays become visible.

Repeated observations must preserve the sequence of estimate revisions rather
than only the latest `EstimatedTime`. Transport-level deduplication may remove
an exact duplicate, but it must not remove a distinct source observation or
estimate revision.

Snapshot-retention cadence is intentionally not finalized here.

## 5. Event correlation

The design must preserve enough source identity to correlate the same
service/call across observations. Candidate identity inputs include, where
available:

- `OperatingDayRef`;
- `JourneyRef`;
- `LineRef`;
- `DirectionRef`;
- `OperatorRef`;
- `StopPointRef`;
- `TimetabledTime`;
- `StopSeqNumber`; and
- arrival/departure context.

No field combination is declared a globally unique production key before Stage
7 validation. Event identity and deduplication must be explicitly validated.

## 6. Observation time is first-class data

Every collected realtime observation needs a reliable collection/observation
timestamp. Without observation time, estimate evolution cannot be
reconstructed.

Preserve the distinction between:

- event timetable timestamp;
- provider estimated timestamp;
- provider recorded timestamp; and
- collector observation timestamp.

`ObservedAt` / `CapturedAt` is never `RecordedAtTime` and never substitutes
for it. It records when the collector observed a source response, not when the
vehicle operated.

For GTFS-RT, also preserve feed-header and trip-update timestamps as distinct
source metadata; neither is an arrival time. Repeated observations of the same
source event must remain available so prediction evolution can be reconstructed.

## 7. Operational flags remain independent

Where available, preserve these independently:

- `Cancelled`;
- `Deviation`;
- `UnplannedStop`;
- `NotServicedStop`;
- `NoBoardingAtStop`; and
- `NoAlightingAtStop`.

Do not collapse them into one generic status. Do not infer cancellation from
missing estimates. In particular, `NotServicedStop` is not journey
cancellation.

## 8. Platform/bay data

Preserve `PlannedBay` and `EstimatedBay` independently. Do not treat a missing
`EstimatedBay` as evidence that the bay was unchanged, and do not call a
difference an actual physical platform change without stronger evidence.

## 9. Situation data

Where available, preserve the complete observed Situation evidence rather than
only the three common text fields:

- `ParticipantRef`, `SituationNumber`, and `Version` when present;
- `SituationFullRef` and related references, with original source path and
  association scope;
- `CreationTime`, `ValidFrom`, and `ValidTo` when source-reported;
- `Summary`, `Description`, and `Detail`;
- observed `Affects`, `Source`, `Priority`, and other version-related fields;
- service/event identity needed to retain the association; and
- `GeneralAttribute`/`Attribute` `Code` + sibling `Text`, including the
  distinction between ordinary passenger information and
  `IncidentCall`-coded incident service information.

Preserve the observed sequence of Situation/Cause versions rather than only a
single mutable `Cause` value. If a Situation, Cause, `SituationFullRef`, text,
effect, reason, or other provider-reported evidence disappears from a later
response, retain the earlier observation. Preserve source field/path,
`ObservedAt` / `CapturedAt`, source-reported `ValidFrom`/`ValidTo`, and
association scope where available. Do not synthesize validity from
disappearance, infer root cause, or create a normalized cause taxonomy here.

Situation association is not causal explanation for a delay or cancellation.
Provider-reported reason text is evidence distinct from a meaningful dedicated
structured reason field; `unknownReason = unknown` is not meaningful reason
evidence. Neither evidence class is a proven or confirmed root cause.

## 10. Collection gaps

Future collector architecture must make observation gaps explicit. Collector
downtime must not silently appear as no delay, no realtime, no cancellation,
or no service.

Health and audit metadata must be capable of distinguishing:

- source absence;
- collector failure;
- transport failure;
- parsing failure;
- persistence failure; and
- scheduled non-collection.

Missingness is source-observation state. A missing GTFS-RT StopTimeUpdate must
not automatically mean on time, served, cancelled, or skipped. A missing MDD
event must not automatically mean service absent, cancellation, or on time.

## 11. Retries and quota

Each retry is another HTTP request. Future collector design must budget
together:

- normal polling;
- retries;
- all targets;
- all collector processes using the same credential where measurable; and
- overlap/concurrency.

The current conservative project ceiling is **250,000 requests/month** until
stronger provider or account evidence supersedes it. Stage 3B approves a
tiered, quota-aware cadence policy, but final production cadence, tier
membership, and activation budget are not defined in this Stage 02 handoff.

## 12. Idempotency and duplicates

The collector must tolerate repeated observations, retries, overlapping runs,
and the same event appearing in multiple polls. Future persistence must support
deterministic deduplication/idempotency while preserving analytically
meaningful evolution over time. This must not be solved by discarding every
repeated event.

## 13. Failure causality

Preserve enough audit context to distinguish:

- HTTP failure;
- authorization failure;
- DNS/network failure;
- provider application error;
- parser failure; and
- persistence failure.

Do not infer provider outage from a local resolver failure.

## 14. Raw response policy

The default is **no permanent or automatic full raw-response retention** for
MDD XML/JSON or GTFS-RT protobuf payloads.
Temporary raw evidence outside Git is allowed for an approved diagnostic
experiment. If Stage 7 later requires short-term automated raw capture, that
stage must explicitly approve its purpose, retention, storage, access control,
and cleanup.

## 15. RecordedAtTime discovery

Although `RecordedAtTime` was not observed in Stage 02, future collector
parsing must not discard it if it appears. It remains a nullable candidate
source field. It is source-only: any first non-null observation must preserve
the exact value and first-seen observation timestamp, while later NULL or
disappearance must not clear it. Any first observation must trigger semantic and
coverage validation before Actual Delay becomes a production KPI.

## 16. Actual-time capture versus independent tracking

The future lifecycle may distinguish `ActualTimeCaptured = false` from
`ActualTimeCaptured = true`. The latter is reached only after a non-null,
source-provided `RecordedAtTime` is captured. Once true, actual-time-specific
polling for that event may stop, but collection must continue if an independent
objective still requires Situation evolution, Cause updates, or
cancellation/status evolution. This is a lifecycle contract only; it does not
approve production scheduling.

## 17. Lifecycle sampling

Future validation must be capable of observing the **same event** repeatedly
before and after its expected occurrence. `RecordedAtTime` could potentially
be exposed only during a short event-lifecycle window and disappear in later
historical queries. The [near-event lifecycle validation](../source-contracts/mdd-trias-event-lifecycle-validation.md)
prepared with Stage 02 approval is the initial diagnostic test for this
possibility.

## 18. Future persistence direction

The intended future persistence architecture is PostgreSQL-oriented and follows:

```text
SOURCE -> COLLECTOR -> APPEND-ONLY SOURCE OBSERVATIONS -> DERIVED ANALYTICAL STATE
```

This is a design direction only. It does not create PostgreSQL tables, migrate
SQL Server assets, finalize a schema, or approve production persistence. JSONB
may be considered later where justified for source-shaped context. Database
choice does not change source availability. A future short-term raw-payload
retention option may support forensic reprocessing, but no retention duration is
approved and permanent raw retention is not the default.

## 19. Stage 7-approved Stage 8 pilot handoff

Stage 7 approves a diagnostic handoff for one future Stage 8 execution:

- primary source: MDD/TRIAS only;
- target: Köln Hbf, `de:05315:11201`;
- endpoint: `https://mdd.gorheinland.com/delfi` with `POST`, TRIAS 1.2, and
  `application/xml; charset=utf-8`;
- credential: `MDD_API_KEY` environment variable only, with presence-only
  preflight and no secret output;
- request: `arrival`, `NumberOfResults=20`, previous/onward/realtime flags
  true, `TimeWindow` omitted, and `IncludeOperatingDays` omitted;
- sampling policy: `MDD_STAGE_8_KOELN_HBF_60S_60MIN_V1`, one slot every 60
  seconds for at most 60 minutes and at most 60 scheduled requests;
- timeout/retries: 30 seconds and zero retries;
- overlap: no concurrency or catch-up; an overlapping slot is recorded as
  `SCHEDULED_SLOT_SKIPPED_OVERLAP`;
- early stop: missing credential means zero attempts; 401/403 and 429 stop
  immediately; three consecutive failed request slots stop the pilot; and
- persistence: protected append-only diagnostic JSONL outside Git, with the
  bounded raw-response exception governed by
  [the Stage 7 design](../stages/07-realtime-pilot-design.md).

This handoff preserves the full Stage 2 field-retention requirements and the
Stage 5 logical source grains. It does not authorize Stage 8 host execution,
runtime code, PostgreSQL, GTFS-RT collection, production cadence, or Actual
Delay. The [Stage 7 completion decision](../stages/07-realtime-pilot-design-completion-decision.md)
is the authoritative approval record.

## Handoff gate

Stage 8 host execution and any later collector implementation begin by
reviewing this document and the [Stage 7 pilot design](../stages/07-realtime-pilot-design.md).
The review must explicitly address source timestamps, event identity,
observation time, estimate history, source-reported Situation/Cause history,
operational flags, gaps, quota, failure causality, raw-response policy, and
`RecordedAtTime` coverage before any production design is approved.

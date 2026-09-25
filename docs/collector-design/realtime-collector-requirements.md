# Realtime Collector Requirements

> **Stage 02 approved design inputs; implementation is deferred.**

This document is the durable handoff from the approved Stage 02 MDD/TRIAS
source-contract audit to the later realtime collector-design stages. It records
source-faithful requirements that must be reviewed before collector
implementation begins. It does not approve a production collector, polling
cadence, database schema, SQL table, or production code.

The approved evidence and semantic boundaries remain in the [Stage 02
source-contract package](../source-contracts/README.md) and the [detailed
Stage 02 audit](../stages/02-mdd-trias-source-contract-audit.md). The related
near-event validation is described in the [RecordedAtTime lifecycle
validation](../source-contracts/mdd-trias-event-lifecycle-validation.md).

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

When available, retain these fields independently:

- `TimetabledTime`;
- `EstimatedTime`;
- `RecordedAtTime`;
- `EstimatedTimeLow`;
- `EstimatedTimeHigh`;
- request/observation timestamp;
- response timestamp where meaningful;
- arrival-versus-departure timing context; and
- call scope: `PreviousCall`, `ThisCall`, or `OnwardCall`.

Never persist only a derived delay while discarding the source timestamps.

## 3. Never promote an estimate to actual

The collector must never infer either of the following:

```text
last EstimatedTime == actual arrival/departure
past EstimatedTime == RecordedAtTime
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

Where available, preserve:

- situation identity;
- `SituationFullRef` and related references;
- validity;
- summary;
- description/detail where approved;
- source;
- priority; and
- affected-entity association or scope.

Situation association is not causal explanation for a delay or cancellation.

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

## 11. Retries and quota

Each retry is another HTTP request. Future collector design must budget
together:

- normal polling;
- retries;
- all targets;
- all collector processes using the same credential where measurable; and
- overlap/concurrency.

The current conservative project ceiling is **250,000 requests/month** until
stronger provider or account evidence supersedes it. Final production cadence
is not defined in this document.

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

The default is **no permanent or automatic full raw-response retention**.
Temporary raw evidence outside Git is allowed for an approved diagnostic
experiment. If Stage 7 later requires short-term automated raw capture, that
stage must explicitly approve its purpose, retention, storage, access control,
and cleanup.

## 15. RecordedAtTime discovery

Although `RecordedAtTime` was not observed in Stage 02, future collector
parsing must not discard it if it appears. It remains a nullable candidate
source field. Any first observation must trigger semantic and coverage
validation before Actual Delay becomes a production KPI.

## 16. Lifecycle sampling

Future validation must be capable of observing the **same event** repeatedly
before and after its expected occurrence. `RecordedAtTime` could potentially
be exposed only during a short event-lifecycle window and disappear in later
historical queries. The [near-event lifecycle validation](../source-contracts/mdd-trias-event-lifecycle-validation.md)
prepared with Stage 02 approval is the initial diagnostic test for this
possibility.

## Handoff gate

Stage 7 collector implementation begins by reviewing this document. The review
must explicitly address source timestamps, event identity, observation time,
operational flags, gaps, quota, failure causality, raw-response policy, and
RecordedAtTime coverage before any production design is approved.

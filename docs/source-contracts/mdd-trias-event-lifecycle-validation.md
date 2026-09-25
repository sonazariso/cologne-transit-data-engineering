# MDD/TRIAS Near-Event RecordedAtTime Lifecycle Validation

**Status:** `POST-STAGE-02 VALIDATION — HOST EXECUTION PENDING`

**Validation class:** `POST-STAGE-02 COLLECTOR-DESIGN VALIDATION`

## Purpose

This bounded diagnostic experiment tests whether `RecordedAtTime` or another
validated actual-time candidate appears temporarily around the lifecycle of one
real arrival event, even though the tested later historical queries contained
only timetable data.

The experiment uses one discovery request followed by scheduled observations of
the same event before and after its expected occurrence. It is designed to
observe estimate evolution and the visibility of recorded-time fields without
turning an estimate into an actual.

## Approval boundary

This experiment does **not** reopen Stage 02. Stage 02 remains approved on its
documented evidence: `RecordedAtTime` was not observed in the controlled live
response or the tested 1D/7D/30D historical responses, the historical results
were timetable-only, and no exact provider retention cutoff was inferred.

This is additional evidence for Stage 7 collector design. A positive result may
require an explicit source-contract amendment or refinement; it must not
silently rewrite the approved Stage 02 conclusions. A negative result from one
event cannot prove provider-wide absence.

## Probe artifact

The host-run diagnostic is:

[`scripts/mdd_recorded_time_lifecycle_probe.py`](../../scripts/mdd_recorded_time_lifecycle_probe.py)

Codex must not execute the probe. Host execution remains pending and must be
performed manually from a normal macOS Terminal only after reviewing the
script's safety controls.

## Fixed request contract

The probe uses the proven MDD/TRIAS 1.2 contract:

- endpoint: `https://mdd.gorheinland.com/delfi`;
- method: `POST`;
- request content type: `application/xml; charset=utf-8`;
- authentication header: `x-api-key` from `MDD_API_KEY` only;
- requestor: `CologneTransitIntelligence`;
- station: Köln Hbf;
- `StopPointRef`: `de:05315:11201`;
- `StopEventType`: `arrival`;
- `IncludePreviousCalls`: `true`;
- `IncludeOnwardCalls`: `true`;
- `IncludeRealtimeData`: `true`;
- no redirects;
- no retries; and
- sequential requests only.

The script makes at most seven HTTP attempts: one discovery request with
`NumberOfResults = 20`, followed by at most six fixed-anchor observations.

## Target and observation design

The discovery request selects one `ThisCall` arrival whose parseable initial
reference time is approximately 8–25 minutes in the future. `EstimatedTime` is
preferred as the reference; `TimetabledTime` is used otherwise. The target
must have a sufficiently stable journey identity, and the selected identity is
printed without unrelated raw payload.

Subsequent requests use a fixed `DepArrTime` equal to the target's
`TimetabledTime` minus approximately five minutes. The query anchor does not
move with the wall clock. The planned checkpoints are:

- T−5 minutes;
- T−1 minute;
- T+1 minute;
- T+5 minutes;
- T+15 minutes; and
- T+30 minutes.

Checkpoints already past at target selection are skipped without a
compensating request. The script never silently switches to another journey:
each observation must be `TARGET_FOUND`, `TARGET_NOT_FOUND`, or
`TARGET_MATCH_AMBIGUOUS` against the conservative identity built from
`OperatingDayRef` where available, `JourneyRef`, `StopPointRef`,
`TimetabledTime`, and arrival context, with line/direction/sequence fields used
only for additional disambiguation.

## Evidence captured

For each matched observation the script prints a concise JSON summary containing
request timing, target identity, ThisCall arrival/departure timestamps,
PreviousCall/OnwardCall counts, independent operational flags, bay fields,
and the structural context of every `RecordedAtTime` occurrence. It tracks the
same event's `EstimatedTime` across observations and never calls the final
estimate an actual.

Only a `RecordedAtTime` in the same arrival timing context as
`TimetabledTime` may produce the diagnostic label
`DIAGNOSTIC_ACTUAL_DELAY_CANDIDATE`, calculated as:

```text
RecordedAtTime - TimetabledTime
```

That label is diagnostic only. It is not an approved KPI.

## Lifecycle classifications

The final classification is evidence-driven and is exactly one of:

- `THIS_CALL_RECORDED_AT_TIME_OBSERVED`;
- `RECORDED_AT_TIME_OBSERVED_IN_PREVIOUS_CALLS_ONLY`;
- `RECORDED_AT_TIME_OBSERVED_OTHER_CONTEXT`;
- `NO_RECORDED_AT_TIME_TARGET_REMAINED_OBSERVABLE`;
- `TARGET_DISAPPEARED_POST_EVENT`;
- `TARGET_MATCH_BECAME_AMBIGUOUS`;
- `TARGET_DELAYED_BEYOND_OBSERVATION_WINDOW`;
- `NO_SUITABLE_TARGET`; or
- `INCONCLUSIVE`.

The output also gives one bounded collector implication. The implication is
about this experiment only and does not generalize one event to all operators,
services, or MDD responses.

## Temporary evidence and safety

One permission-restricted temporary directory is created outside the repository
under:

```text
/private/tmp/cologne-transit-recorded-time-lifecycle-XXXXXXXX/
```

Response bodies are written as mode-600 files only after checking that the API
key is not present. The API key and authorization header are never persisted or
printed. The probe performs no SQL, database, Legacy, Task Scheduler, Power BI,
network/DNS, concurrency, or retry operation.

Temporary raw artifacts are diagnostic evidence only and must not be copied
into Git. The default source-of-record policy remains no permanent or automatic
full raw-response retention.

## Pending interpretation

Until host execution is complete, no lifecycle result is claimed. Afterward,
the result must be reviewed against the [Stage 02 approved source-contract
package](README.md) and the [collector requirements handoff](../collector-design/realtime-collector-requirements.md).
Any newly observed actual-time field requires explicit semantic, coverage,
identity, and operational validation before Actual Delay can be considered for
later productionization.

# MDD/TRIAS Response Contract

**Stage:** 02 — MDD/TRIAS Source-Contract Audit<br>
**Document state:** Approval candidate; not user-approved<br>
**Semantic version:** TRIAS 1.2<br>
**Audited response date:** 2026-09-25

This document is the final Stage 02 semantic response contract candidate for
the audited MDD NRW `/delfi` request. It keeps specification capability,
controlled-response observation, Legacy parser behavior, and Legacy
persistence behavior separate.

## Four evidence layers

The same field can have four different states. They must not be collapsed:

| Layer | Meaning in this contract |
|---|---|
| **A. TRIAS 1.2 specification capability** | Semantic fields and structures defined or permitted by VDV 431-2, “EKAP-Schnittstellenbeschreibung V1.2”, 02/2017. Optional in the specification does not mean present in every response. |
| **B. Controlled MDD response** | What was actually observed in one successful response for Köln Hbf, arrival events, maximum five results, at one point in time. Absence is a single-response observation only. |
| **C. Legacy parser behavior** | What `ConvertFrom-MddTriasResponse` reads, traverses, or ignores in the Legacy implementation. A parser omission is not proof of provider absence. |
| **D. Legacy persistence behavior** | What the Legacy snapshot/DataTable/TVP path maps toward SQL based on checked-in source and schema definitions. No SQL was executed for this audit. |

The field-retention matrix provides the detailed four-layer comparison. The
short summary below is intentionally not an implementation specification.

## Audited wire result

| Element | Evidence |
|---|---|
| Request | XML / TRIAS 1.2, `POST`, `application/xml; charset=utf-8` |
| Endpoint | MDD NRW `/delfi` |
| Authentication | `x-api-key`; credential value not documented |
| Response HTTP status | `200` |
| Response Content-Type | `application/json;charset=UTF-8` |
| Response body | JSON; top-level `version` is `1.2` and `serviceDelivery` is present |
| Returned results | `5` `stopEventResult` objects |
| Controlled target | Köln Hbf, `de:05315:11201`, `arrival` |
| Request flags | `IncludePreviousCalls=true`, `IncludeOnwardCalls=true`, `IncludeRealtimeData=true` |

This is a successful controlled-response result, not a provider-wide claim
about every MDD endpoint, stop, mode, operator, or time period.

## Timing contract

The VDV 431-2 TRIAS 1.2 semantics supplied for this audit are:

| Field | Approved semantic interpretation |
|---|---|
| `TimetabledTime` | Required timetable/scheduled time in `ServiceCallStructure`. |
| `RecordedAtTime` | Optional actual/recorded time in `ServiceCallStructure`. It is the TRIAS 1.2 actual-time candidate. |
| `EstimatedTime` | Optional expected/predicted time in `ServiceCallStructure`. |
| `EstimatedTimeLow` / `EstimatedTimeHigh` | Optional lower and upper bounds for the expected time. |

The controlled response contained `TimetabledTime` and `EstimatedTime` values,
but did not contain `RecordedAtTime`, `EstimatedTimeLow`, or
`EstimatedTimeHigh`. `RecordedAtTime` is therefore:

> **SPEC_DEFINED but NOT OBSERVED IN THIS CONTROLLED RESPONSE.**

For a comparable event where both timestamps are present, the Stage 02
derived measure is:

```text
Estimated Delay = EstimatedTime - TimetabledTime
```

It must be labelled **Estimated Delay**. It must not be called Actual Delay,
Actual Arrival Delay, Actual Physical Delay, or realized delay.

`responseTimestamp` is delivery metadata. The Legacy parser maps it to
`ObservedAtUtc`; that does not make it a `RecordedAtTime` value and does not
create actual vehicle timing.

Actual Delay remains unavailable for production until a valid
`RecordedAtTime`/actual-timing source is observed, validated, and later
matched appropriately. Prediction Accuracy is also unavailable until
comparable actual timing exists. Stage 02 does not approve either KPI.

Arrival and departure timing must remain distinguishable. The controlled
response contained `serviceArrival` and `serviceDeparture` structures. The
Legacy parser and persistence path handled the current arrival timing only;
they did not establish a complete arrival/departure retention contract.

## Operational-status contract

The VDV 431-2 semantics supplied for `CallAtStopStructure` and
`DatedJourneyStructure` distinguish stop-level and journey-level concepts:

| Field | Meaning and treatment |
|---|---|
| `NotServicedStop` | The planned stop is not served. This is a stop/call-level operational flag. It is not whole-journey cancellation. |
| `Cancelled` | A distinct journey-level field indicating that the whole journey is cancelled. |
| `UnplannedStop` | A stop was not planned. Preserve independently. |
| `NoBoardingAtStop` | Boarding is not permitted at the stop. Preserve independently. |
| `NoAlightingAtStop` | Alighting is not permitted at the stop. Preserve independently. |
| `Deviation` | The journey takes a different route. Preserve independently from cancellation and stop service flags. |
| `ErrorMessage` | Structured response/result error evidence, when present. Do not infer it from missing optional fields. |

The controlled response contained `NotServicedStop`, `UnplannedStop`,
`NoBoardingAtStop`, `NoAlightingAtStop`, and `Deviation` evidence. It did not
contain a populated journey-level cancellation field. Consequently:

- no cancellation KPI is approved from this response;
- missing timing or missing estimates must not be treated as cancellation;
- `NotServicedStop` must not be widened into whole-journey cancellation; and
- `UnplannedStop`, `NoBoardingAtStop`, `NoAlightingAtStop`, and `Deviation`
  remain separate source fields.

## Platform/bay contract

The VDV 431-2 `CallAtStopStructure` semantics supplied for this audit are:

| Field | Meaning and treatment |
|---|---|
| `PlannedBay` | Planned bay/platform information. |
| `EstimatedBay` | Bay/platform according to the latest prediction state. |

The controlled response contained planned bay text on most call records and
estimated bay text on a smaller set of previous-call records. The Legacy
parser/persistence path reads and persists the current-call fields, not every
previous/onward occurrence.

`NULL` or missing `EstimatedBay` does not mean unchanged. `PlannedBay` and
`EstimatedBay` must remain separate source fields. If both are present and
differ, a future analytical field may describe a **planned-vs-estimated bay
difference**. That difference is not proof of the final physically used
platform, so Stage 02 approves no actual platform-change KPI.

## Situation contract

`SituationFullRef` and the situation context establish an association between
a journey/call and a situation record. The controlled response contained
situation identity, references, text, validity, priority/source evidence, and
affected-entity context.

Future retained situation evidence should preserve, where available:

- situation identity and references;
- validity interval;
- summary, description, and detail;
- source and priority;
- affected-entity association; and
- relation scope, such as service-level versus call-level association.

Situation evidence does **not** prove that the situation caused a delay,
cancellation, platform change, or poor performance. Future BI may report
“services/events associated with situation evidence.” It must not report
“delay reason” unless a later evidence model establishes a defensible causal
relationship.

## Controlled-response observations

The single audited response contained:

- timetable and estimated timestamps;
- journey/service, line, direction, operator, stop, origin, destination,
  mode, operating-day, and result identities/text where observed;
- `PreviousCall`, `ThisCall`, and `OnwardCall` structures;
- stop sequence values;
- planned and estimated bay fields;
- operational stop flags and a service `Deviation` flag; and
- situation references, situation records, text, validity, priority/source,
  and affected-journey context.

The response did not contain `RecordedAtTime`, estimate bounds, a direct delay
field, a populated journey-level cancellation field, or an explicit
platform-change field. These are observations for this response only. A
candidate absent from this response is not marked “unsupported”; the matrix
uses `SPEC_DEFINED_NOT_OBSERVED` where the TRIAS 1.2 semantic field is known.

## Legacy implementation boundary

The Legacy implementation:

- parses the JSON body and validates `serviceDelivery.status`;
- maps `serviceDelivery.responseTimestamp` to `ObservedAtUtc`;
- traverses and persists the current `ThisCall` arrival fields;
- parses current-call timetable/estimate, identity, bay, and three stop flags;
- parses identifiable situation records and service-level situation links; and
- does not parse or persist all previous/onward calls, `RecordedAtTime`,
  estimate bounds, `OperatingDayRef`, `StopSeqNumber`, origin/destination,
  `Deviation`, `UnplannedStop`, situation priority/source/affected-entity
  detail, or a journey-level cancellation field.

This is Legacy evidence, not the future warehouse design. The future field
retention decision is in the [field-retention matrix](mdd-trias-field-retention-matrix.md).

## Coverage and representativeness boundary

The controlled response represents one request, Köln Hbf, arrival events, a
maximum of five results, and one point in time. It is sufficient to confirm
the actual wire format, demonstrate example structural behavior, confirm
observed fields, and identify candidate future fields.

It is not sufficient to calculate field-population percentages or claim
Cologne-wide, mode-wide, station-wide, operator-wide, or network performance.
In particular, `RecordedAtTime` coverage is **UNKNOWN**. Coverage validation
belongs to controlled pilot/coverage stages, especially Stages 7, 8, 9, and
16, and does not block Stage 02 technical closure.

## Permission, quota, and raw-payload boundary

Current MDD authentication/access is working. Use remains subject to
registration/activation, provider terms, and the agreed purpose. The public
MDD dataset description is not treated as an open-license proof for
unrestricted redistribution.

The documented `250,000` requests/month is the conservative known project
ceiling for Stage 02, with the account-level/shared-caller limitation stated
in the [request contract](mdd-trias-request-contract.md). Stage 7 and Stage
13 must budget base requests, retries, overlap/concurrency, and all measurable
active callers. No new schedule is approved here.

Full raw MDD responses are not a permanent or automated source of record by
default. Temporary raw artifacts may be used for explicit diagnostics and
source-contract validation outside source control, without secrets. See the
[raw-retention decision](mdd-trias-raw-retention.md).

## Related contract artifacts

- [Request contract](mdd-trias-request-contract.md)
- [Field-retention matrix](mdd-trias-field-retention-matrix.md)
- [Raw-retention decision](mdd-trias-raw-retention.md)
- [Detailed Stage 02 evidence/audit](../stages/02-mdd-trias-source-contract-audit.md)

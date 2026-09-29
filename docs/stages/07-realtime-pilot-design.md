# Stage 7 — Realtime Pilot Design

**Status:** `COMPLETE — APPROVED PILOT DESIGN`  
**Decision:** [D-033](../DECISION-LOG.md#d-033--stage-7-3-second-pilot-and-operational-planning-envelope), superseding the historical policy detail in [D-032](../DECISION-LOG.md#d-032--stage-7-pilot-cadence-and-recordedattime-correction)<br>
**Date:** `2026-09-28`  
**Stage 8 execution status:** `COMPLETE` — this document records the
design-time handoff; current execution evidence is in the Stage 8 closeout.

This document fixes the bounded, reproducible design and handoff for the
future Stage 8 MDD pilot. Stage 7 is a design / contract stage. It does not
execute the pilot, create runtime code, make network requests, create a
database, execute SQL, or activate a production cadence.

## 1. Purpose and stage boundary

The Stage 8 pilot defined here is a one-station, one-source diagnostic
experiment. It must be able to test, end to end:

1. source request and response correctness;
2. collector observation lineage;
3. repeated estimate history;
4. source `RecordedAtTime` population if it appears;
5. `PreviousCall`, `ThisCall`, and `OnwardCall` preservation;
6. planned and estimated platform/bay evidence;
7. independent operational flags;
8. `Situation` / `SituationFullRef` evidence;
9. `Attribute` / `GeneralAttribute` / `IncidentCall` evidence;
10. source and application errors;
11. observation gaps and failure causality; and
12. parser-to-pilot-persistence reconciliation.

Stage 7 does not approve:

- production cadence or a production station panel;
- 50-station deployment;
- a provider-wide field-coverage claim;
- an Actual Delay KPI;
- canonical cross-source matching;
- operational facts; or
- PostgreSQL runtime implementation.

Stage 5 remains `COMPLETE — APPROVED ARCHITECTURE`. Stage 6 remains
`COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED`. PostgreSQL,
database migration, and runtime implementation remain deferred until explicit
later authorization. At the time of this design handoff, Stage 8 had not
started; the current bounded Stage 8 execution is complete as recorded in the
[Stage 8 closeout](08-small-realtime-pilot-blocked.md).

## 2. Authoritative inputs and compatibility

Stage 7 does not redesign earlier approved stages. The pilot must use the
following existing contracts as its semantic baseline:

- [MDD/TRIAS request contract](../source-contracts/mdd-trias-request-contract.md)
- [MDD/TRIAS response contract](../source-contracts/mdd-trias-response-contract.md)
- [MDD/TRIAS field-retention matrix](../source-contracts/mdd-trias-field-retention-matrix.md)
- [MDD/TRIAS raw-retention decision](../source-contracts/mdd-trias-raw-retention.md)
- [MDD/TRIAS near-event lifecycle validation](../source-contracts/mdd-trias-event-lifecycle-validation.md)
- [future source-observation and persistence contract](../source-contracts/future-observation-persistence-contract.md)
- [realtime collector requirements](../collector-design/realtime-collector-requirements.md)
- [Stage 3B collection/cadence contract](../stage-03/03b-collection-strategy-cadence-contract.md)
- [Stage 3C cross-source matching design](../stage-03/03c-cross-source-matching-design.md)
- [Stage 5 database architecture](05-database-architecture.md)
- [Stage 5 table/grain catalog](05-table-grain-catalog.md)
- [Stage 6 static warehouse and analytics design](06-static-warehouse-analytics-design.md)

The Stage 5 logical source grains remain the compatibility target. The
non-PostgreSQL harness mirrors them diagnostically; it does not replace or
amend the approved logical architecture.

## 3. Source selection

### 3.1 Primary pilot source

MDD/TRIAS is the **only primary source** for the Stage 8 one-station pilot.
MDD remains the targeted Cologne realtime source because the approved evidence
already covers targeted stop requests, call-chain context, estimates,
platform/bay fields, operational flags, Situation references, and service
information.

GTFS-RT remains **complementary / secondary** in the long-term two-source
architecture. Stage 8 does not run a GTFS-RT collection loop, fetch a GTFS-RT
feed, or use GTFS-RT to make the MDD pilot appear more complete. Stage 9 owns
cross-source matching. Existing bounded GTFS-RT feasibility and lifecycle
evidence remains valid and is not reopened by this design.

### 3.2 Pilot station

The approved single target is:

| Item | Value |
|---|---|
| Station | Köln Hbf |
| MDD `StopPointRef` | `de:05315:11201` |
| Request scope | MDD stop-event results for the requested stop, with arrival context and requested call-chain context |

Köln Hbf is selected for source, parser, and persistence validation because:

- multiple successful MDD requests already exist for this stop;
- prior lifecycle diagnostics tracked individual services successfully;
- the station exposes rich `PreviousCall` / `ThisCall` / `OnwardCall` context;
- planned bay/platform evidence has already been observed; and
- service, Situation, and `IncidentCall` evidence has been observed in the
  broader bounded audit.

The station is an efficient diagnostic target because its event density is
useful for repeated observations. It is not declared representative of the
Cologne network. No network-wide, mode-wide, operator-wide, or provider-wide
analytical conclusion may be drawn from the one-station pilot.

## 4. Fixed Stage 8 request contract

The following is the complete pilot request selection. It is a Stage 8 pilot
setting, not a production default.

| Request element | Approved Stage 8 value |
|---|---|
| Endpoint | `https://mdd.gorheinland.com/delfi` |
| Method | `POST` |
| Protocol | TRIAS `1.2` |
| Content-Type | `application/xml; charset=utf-8` |
| Authentication header | `x-api-key` |
| Credential source | `MDD_API_KEY` environment variable only |
| RequestorRef | `CologneTransitIntelligence` |
| Requested StopPointRef | `de:05315:11201` |
| `DepArrTime` | Fixed `PilotAnchorDepArrTime` selected at pilot start in the already-approved UTC TRIAS format and reused for every pilot request |
| `StopEventType` | `arrival` |
| `NumberOfResults` | `20` |
| `IncludePreviousCalls` | `true` |
| `IncludeOnwardCalls` | `true` |
| `IncludeRealtimeData` | `true` |
| `TimeWindow` | Omitted |
| `IncludeOperatingDays` | Omitted |

The body remains an XML TRIAS 1.2 request. At pilot start, select
`PilotAnchorDepArrTime` as the UTC reference timestamp for the pilot and reuse
that same `DepArrTime` value in every MDD request for the one-hour run. Do not
advance it with wall-clock time. The effective request parameters, per-request
request timestamp, fixed anchor, and requested stop must be retained as
lineage with every response attempt. `DepArrTime` is request/reference
context; it is not an event actual time. The first successful response is the
initial cohort/baseline observation. Later responses use the same fixed search
anchor; result additions, removals, and reordering are lifecycle evidence, and
the pilot does not require every result position to remain identical.

The credential must never be hardcoded, printed, logged, placed in command
line arguments, or written into raw artifacts. The pilot may print only the
preflight presence state defined below.

### 4.1 `NumberOfResults` decision

`NumberOfResults = 20` is approved for this bounded pilot only.

The decision is justified because:

- Stage 2 established that the request builder accepts values from 1 through
  100;
- near-event lifecycle tooling already used 20 successfully;
- one HTTP request remains one HTTP request regardless of the result limit; and
- 20 increases field/event coverage per station request while remaining
  bounded.

`NumberOfResults` is not an HTTP request count and is not a production
default. Stage 13 remains responsible for production result-limit coverage and
readiness analysis. This correction keeps the value at 20: the objective is
increased temporal observation density, not larger result-set breadth.

### 4.2 `TimeWindow` decision

`TimeWindow` is **omitted** from the pilot request. TRIAS 1.2 documents the
semantic option, but the audited Legacy/current request path and controlled
MDD validations did not emit or require it. Adding it would introduce an
unrelated request-contract variable.

This records `TimeWindow` as **SEMANTICALLY AVAILABLE / NOT SELECTED FOR THIS
PILOT**. It does not claim that the provider does not support it. A later
readiness stage may test it separately.

## 5. Cadence, duration, and quota boundary

### 5.1 Named pilot sampling policy

Every observation and gap must identify the following explicitly named,
versioned, non-production policy:

`MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`

Policy lineage is preserved: V1 was the original 60-second design and V2 was
the superseding 10-second fixed-anchor design. Both remain historical records;
V3 is the active revised Stage 8 policy.

The policy conceptually records:

| Policy attribute | Value |
|---|---|
| Source | MDD/TRIAS |
| Scope | Köln Hbf / `de:05315:11201` |
| Interval | Fixed start-to-start slot every 3 seconds |
| Maximum duration | 60 minutes |
| Maximum scheduled logical requests | 1200 |
| Maximum normal HTTP attempts | 1200 |
| `NumberOfResults` | 20 |
| `StopEventType` | `arrival` |
| Retry count | 0 |
| Request timeout | 30 seconds |
| Environment | Stage 8 diagnostic pilot; non-production |

This sampling policy must not be reused as a production `SamplingEra` or
represented as a production cadence approval. The 3-second pilot cadence is
not production approval.

### 5.2 Scheduling rules

- Schedule fixed start-to-start logical request slots at `T0`, `T0 + 3s`,
  `T0 + 6s`, and so on for at most 60 minutes; the maximum is 1200 scheduled
  slots.
- Do not queue catch-up requests.
- Do not issue requests concurrently.
- Do not overlap an active request with another request.
- If a slot is missed because the previous request is still active, skip it
  and write the gap state `SCHEDULED_SLOT_SKIPPED_OVERLAP`.
- Do not wait three seconds after receiving the previous response; response
  completion must not shift the fixed slot schedule.
- Record the planned slot, actual attempt state, and outcome separately.
- A failed slot is not silently converted into a successful observation.

The 30-second request timeout exceeds one 3-second slot interval but does not
permit overlap. The scheduler remains fixed-start, sequential, and
non-concurrent: if a request is still running when a later slot arrives, that
slot is skipped and recorded explicitly rather than queued, shifted, or
compressed.

The evidence basis for this bounded diagnostic interval is the retained MDD
lifecycle/probe evidence: observed request elapsed times are predominantly
below the proposed 3-second interval, ranging from sub-second observations to
approximately 2 seconds in the retained artifacts. This supports a 3-second
bounded diagnostic slot interval with sequential execution and overlap
skipping. It does not establish a provider guarantee that every response will
finish within 3 seconds, that no slot will be skipped, or that this is an
approved production cadence.

This is a bounded diagnostic pilot cadence intended to increase temporal
observation density for estimate evolution and potentially short-lived fields.
It is not an approved production cadence; Stage 13 owns production cadence,
shared-quota, and readiness analysis.

### 5.3 Quota accounting

The conservative project ceiling remains **250,000 MDD HTTP requests per
month**. The approved operational planning envelope is **83–84%** of that
ceiling: **207,500–210,000 MDD HTTP requests per month**. Preserve the
remaining **40,000–42,500 requests per month**, approximately **16–17%**, as
unallocated headroom. Normal scheduled production traffic must not be planned
against the full 250,000-request ceiling.

For the 3-second / 60-minute Stage 8 pilot, the maximum is **1200 scheduled
logical slots** and **1200 normal HTTP attempts** because retries are zero.
This is **0.48%** of the full conservative ceiling and approximately
**0.57–0.58%** of the operational planning envelope (`1200 / 210000` and
`1200 / 207500`, respectively). Every actual HTTP attempt must be recorded,
including failed attempts. Actual consumption can be lower because a slot
skipped for overlap does not create an HTTP attempt.
`NumberOfResults` never counts as HTTP attempts. This is a bounded diagnostic
pilot budget, not a production cadence approval; Stage 13 owns final
production station tiers, cadence, staggering, retry policy, overlap analysis,
and the full monthly request projection, sized inside the operational planning
envelope unless the user later changes this decision.

The pilot does not claim knowledge of total account usage by other external
callers sharing the credential. Stage 13 remains responsible for production
budgeting, shared-caller headroom, and final readiness.

## 6. Timeout, retry, preflight, and early-stop rules

### 6.1 Timeout and retry

- Request timeout: **30 seconds** per HTTP attempt.
- Retries: **0**.
- A failed request creates a gap/failure record.
- The same slot is never retried.
- A later scheduled slot may proceed after a transport timeout, network
  failure, or retryable HTTP response unless an early-stop rule applies.

The zero-retry rule keeps request accounting and failure causality
deterministic. It is a Stage 8 setting; Stage 13 owns any production retry
policy.

### 6.2 Credential preflight

Before any HTTP request, check only whether `MDD_API_KEY` is present. Print
exactly one safe state:

```text
MDD_API_KEY: PRESENT
```

or:

```text
MDD_API_KEY: MISSING
```

Never print or expose the value. If it is missing, stop with **zero HTTP
attempts**.

### 6.3 Fail-fast and early-stop conditions

| Condition | Required action and classification |
|---|---|
| HTTP `401` or `403` | Stop immediately; no retry; classify `AUTHENTICATION_OR_AUTHORIZATION_FAILURE`. Do not assert that the key itself is invalid. |
| HTTP `429` | Stop immediately; no retry; classify `RATE_LIMIT_RESPONSE`. |
| Network/DNS failure | Do not retry the slot; classify `NETWORK_OR_DNS_FAILURE`; the next scheduled slot may proceed. |
| Transport timeout | Do not retry the slot; classify `TIMEOUT`; the next scheduled slot may proceed. |
| HTTP `408` or retryable `5xx` | Do not retry the slot; classify `HTTP_ERROR`; the next scheduled slot may proceed unless the consecutive-failure rule stops the pilot. |
| Three consecutive failed request slots | Stop the pilot to avoid wasting quota during an outage. A successful request resets the counter. |
| Provider/application error in a received response | Retain the response/error evidence and classify `MDD_APPLICATION_ERROR` where applicable; never relabel it as a transport success. |

The implementation must record the response status and error evidence before
applying the relevant stop rule when a response exists.

## 7. Observation and source-field retention

The pilot must use the approved Stage 2 retention requirements. It must not
create a reduced parser that drops fields for convenience. Every retained
value remains source-faithful, independently nullable, and associated with
its response, run, source path, and observation timestamp where applicable.

### 7.1 Request, response, and parser lineage

Retain:

- requested `StopPointRef`;
- request timestamp and effective request parameters;
- `DepArrTime`;
- collector `ObservedAt`;
- HTTP status and response/application status;
- response content type;
- response size and content hash;
- response identity and result identity;
- elapsed/request-attempt metadata needed for audit;
- parser version and run identity; and
- populated response-level and result-level `ErrorMessage` evidence.

Transport success must remain separate from service-level success.

### 7.2 Event and service identity

Retain when supplied:

- `OperatingDayRef`;
- `JourneyRef`;
- `LineRef`;
- `DirectionRef`;
- `OperatorRef`;
- service/product/mode context;
- origin and destination identity/text evidence;
- source/result identity; and
- event ordinal.

MDD identities remain in the MDD namespace. They must not be equated to
GTFS-RT identifiers in this pilot.

### 7.3 Call context

For every selected call occurrence, retain:

- `PreviousCall`, `ThisCall`, or `OnwardCall` scope;
- call ordinal and source path;
- `StopPointRef` and display text where supplied;
- `StopSeqNumber` where supplied; and
- arrival versus departure context.

The request flags do not prove that a parser persisted the returned call
occurrences. Reconciliation in Section 11 is mandatory.

### 7.4 Independent timing fields

Retain all of the following independently and nullable, with call scope and
arrival/departure context:

- `TimetabledTime`;
- `EstimatedTime`;
- `RecordedAtTime`;
- `EstimatedTimeLow`; and
- `EstimatedTimeHigh`.

The invariant is:

```text
TimetabledTime != EstimatedTime != RecordedAtTime != ObservedAt
```

`RecordedAtTime` is source-only and exact. If it is observed once in any
retained `THIS`, `PREVIOUS`, or `ONWARD` timing context, and for either arrival
or departure, write it immediately to append-only pilot persistence with the
exact source value, event/source identity, call scope, arrival/departure
context, response identity, `ObservedAt`, and first-seen observation
timestamp (`RecordedAtTimeObservedAt`). A later `NULL` or disappearance must
not overwrite or erase the prior captured evidence; later responses may
independently contain `NULL`. Never derive it from an estimate, timetable,
request timestamp, `ObservedAt`, or event disappearance.

### 7.5 Platform/bay and operational flags

Retain independently:

- `PlannedBay`;
- `EstimatedBay`;
- `Cancelled`;
- `Deviation`;
- `UnplannedStop`;
- `NotServicedStop`;
- `NoBoardingAtStop`; and
- `NoAlightingAtStop`.

Missing `EstimatedBay` is not evidence of an unchanged bay. A planned-versus-
estimated difference is not proof of the physical bay used. `NotServicedStop`
is a stop-level flag, not journey cancellation. No flag may be collapsed into
a generic status.

### 7.6 Situation, service information, and attributes

Retain source-reported Situation evidence over time:

- `SituationFullRef`;
- Situation identity, including `ParticipantRef`, `SituationNumber`, and
  `Version` where supplied;
- `CreationTime`, `ValidFrom`, and `ValidTo` where supplied;
- `Summary`, `Description`, and `Detail`;
- `Source`, `Priority`, and affected-entity context;
- original source path and relation scope;
- `Attribute` / `GeneralAttribute` code and sibling text; and
- `IncidentCall` identity/text evidence.

`IncidentCall` is service-information evidence. It must not be relabelled as
`ErrorMessage`. Situation association is context evidence and does not prove
that a Situation caused a measured delay, cancellation, or platform change.
Do not create `RootCause`, `ConfirmedCause`, or `ProvenCause` during Stage 8.

### 7.7 Errors and unknown fields

Retain populated response-level and result-level `ErrorMessage` occurrences
independently. If an unsupported or unmapped source field is discovered,
record its source field path and parser-gap evidence. Do not silently ignore
the field or call it unsupported merely because it was not in the prior
controlled response.

## 8. Estimate-history semantics

Repeated observations of the same source event are analytically meaningful.
For example:

```text
ObservedAt 10:00 -> EstimatedTime 10:15
ObservedAt 10:05 -> EstimatedTime 10:18
ObservedAt 10:10 -> EstimatedTime 10:17
```

The harness must preserve all three observations, even when a later estimate
equals an earlier estimate. Every successful 3-second response remains a
distinct observation and must not be deduplicated merely because
`EstimatedTime` or `RecordedAtTime` did not change. Exact replay of the exact
same response/run identity is the only replay-idempotency case already defined
by Stage 7; a later scheduled observation is a new observation.

## 9. Actual Delay gate

Actual Delay remains **NOT APPROVED** through Stage 7.

If Stage 8 observes a non-null source `RecordedAtTime`, it is
**ACTUAL-TIME CANDIDATE EVIDENCE** only. A diagnostic
`RecordedAtTime - TimetabledTime` may be calculated for validation when the
semantics and context are comparable, but it must not become a production KPI.
Later semantic, coverage, matching, and operational approval is required.

If zero `RecordedAtTime` values are observed, that is a valid bounded result.
The report must say:

```text
RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT
```

This means absence in the observed sampling window, not provider-wide
absence. Three-second polling materially reduces the risk of missing a
transient `RecordedAtTime` occurrence but cannot mathematically guarantee
capture. No positive `RecordedAtTime` lifecycle sample currently establishes
the field's minimum visibility period. If Stage 8 shows that a relevant source
field can appear and disappear within the 3-second sampling interval, a
separately approved shorter bounded validation may be required. This does not
pre-approve a shorter production cadence. The report must not say
`MDD_DOES_NOT_SUPPORT_RECORDED_AT_TIME`.

## 10. Situation and cause semantics

Situation, reason, and `IncidentCall` evidence is append-only contextual
evidence. If it appears and later disappears, the earlier observation remains
retained. Evolving source versions remain separate observations.

The pilot must not normalize the evidence into a root-cause taxonomy or infer
causality. A Situation association means that the source reported an
association at that observation and scope; it does not prove that the
Situation caused a measured delay or cancellation.

## 11. Bounded raw-capture exception

Stage 7 approves one narrow raw-response exception for MDD Stage 8 pilot
responses only. Its purpose is to compare raw response structure with parser
output, detect parser field loss, and support offline forensic review if an
unexpected field appears. The active V3 cadence increases the response
count, but it does not change the default project policy of no permanent raw
MDD archive.

### 11.1 Storage and access

- Store raw responses outside the repository in a permission-restricted
  temporary directory, for example
  `/private/tmp/cologne-transit-stage-08-XXXXXXXX/`.
- Create the directory with mode `700`.
- Write raw response files with mode `600`.
- Before writing a payload, verify that the API-key value is not present in
  the response content.
- Never persist the `x-api-key` header, API-key value, credential fingerprint,
  authorization material, or command-line secrets.
- Never commit, publish, or redistribute raw payloads.

### 11.2 Retention and cleanup

Raw payloads remain temporary and may exist only during Stage 8 execution and
review, outside Git, with mode-600 files inside a mode-700 directory and a
maximum retention of **24 hours after the pilot run ends**. Delete them after
successful Stage 8 evidence extraction and parser-to-persistence
reconciliation, or earlier after successful offline verification when
possible. Cleanup must run on normal completion.

The Stage 8 final report must state whether cleanup succeeded. A cleanup
failure is an explicit pilot quality result and must not be hidden. The
exception applies only to this bounded pilot and does not approve permanent
raw retention.

## 12. Non-PostgreSQL pilot persistence harness

Because PostgreSQL migration and runtime remain deferred, Stage 8 must use a
non-production append-only local JSONL persistence harness outside Git. The
artifacts must be held in the same protected temporary directory as the raw
payloads, or another explicitly protected temporary directory.

The JSONL harness is diagnostic persistence, not the future database. It must
not be replaced by a mutable `latest event` file and must not be treated as a
production schema.

The harness conceptually mirrors the approved Stage 5 source entities:

| Diagnostic JSONL artifact | Logical Stage 5 compatibility grain |
|---|---|
| Response snapshots | One executed MDD HTTP response |
| Stop-event observations | One `StopEventResult` in one response snapshot |
| Call observations | One `CallAtStop` occurrence belonging to one observed stop event |
| Situation observations | One Situation object observed in one response snapshot |
| Stop-event/Situation associations | One observed association and its scope |
| Service-attribute observations | One observed service `Attribute` / `GeneralAttribute` occurrence |

Every JSONL record must retain, as applicable:

- pilot run identity;
- response identity and source result identity;
- `ObservedAt`;
- source identities and source field values;
- call/association scope;
- source path and field path where needed;
- parser version; and
- failure/quality lineage.

The harness must preserve history and exact replay idempotency without
introducing a database migration, SQL, or production persistence implementation.
After Stage 8, only a small sanitized summary/evidence artifact may be
considered for committing to `docs`; the complete observation history is not
committed unless a later explicit decision authorizes it.

## 13. Parser-to-pilot-persistence reconciliation

For each successful response, Stage 8 must be able to verify:

1. parsed `StopEventResult` count equals persisted pilot stop-event observation
   count for that response;
2. each selected `PreviousCall`, `ThisCall`, and `OnwardCall` occurrence has
   exactly one corresponding pilot call observation;
3. every retained Situation source occurrence is traceable to one persisted
   pilot Situation observation;
4. every observed Situation association reconciles by link count and
   association scope;
5. every retained `Attribute` / `GeneralAttribute` occurrence is traceable to
   one pilot service-attribute observation; and
6. every unsupported or unmapped source field is recorded as parser-gap
   evidence with its field path.

The harness must not silently drop a source value. A reconciliation failure is
classified and reported; it is not converted into a successful parse rate.

## 14. Idempotency boundary

Reprocessing the exact same captured response with the same response identity
and pilot-run identity must not duplicate that exact response persistence.

A later scheduled observation of the same transport event is a new source
observation and must remain, even when every source value is equal to the
earlier observation. The boundary is:

```text
exact replay duplication != legitimate repeated observation
```

The response-level idempotency key must therefore include the response/run
identity required to distinguish an exact replay from a later scheduled
observation. Idempotency must not be implemented by retaining only a mutable
latest event.

## 15. Gap and failure classification

The pilot must distinguish at least these states:

| State | Meaning |
|---|---|
| `SUCCESS` | Request response received and parsed/persisted with required reconciliation checks passing. |
| `SCHEDULED_SLOT_SKIPPED_OVERLAP` | Planned slot was skipped because the preceding request was still active. |
| `AUTHENTICATION_OR_AUTHORIZATION_FAILURE` | HTTP 401/403 or equivalent authorization boundary evidence; key validity is not asserted. |
| `RATE_LIMIT_RESPONSE` | HTTP 429 evidence. |
| `HTTP_ERROR` | Other HTTP failure, including 408 or retryable 5xx without a retry. |
| `NETWORK_OR_DNS_FAILURE` | Transport or resolver failure before a usable HTTP response. |
| `TIMEOUT` | Request exceeded the 30-second timeout. |
| `PARSER_FAILURE` | Response was received but the approved parser failed. |
| `PILOT_PERSISTENCE_FAILURE` | Parsed evidence could not be written/reconciled in the JSONL harness. |
| `MDD_APPLICATION_ERROR` | MDD response/application error evidence was returned, including populated source error structures. |

A missing observation must never be converted into `on time`, `no delay`,
`cancelled`, `no service`, or `served`. The recorded state is a collection
quality outcome, not an operational fact.

## 16. Required Stage 8 metrics and evidence

The final Stage 8 report must include at least:

### Collection and quality

- planned request slots;
- attempted HTTP requests;
- successful HTTP responses;
- HTTP-status distribution;
- timeout and network-failure counts;
- parser-failure count;
- pilot-persistence-failure count;
- skipped-overlap-slot count;
- successful parse rate; and
- raw cleanup result.

### Source event and call coverage

- `StopEventResult` count;
- `PreviousCall` count;
- `ThisCall` count;
- `OnwardCall` count;
- `TimetabledTime` presence count;
- `EstimatedTime` presence count;
- `RecordedAtTime` presence count;
- distinct events with `RecordedAtTime`;
- estimate revision count;
- events with repeated observations;
- `PlannedBay` presence;
- `EstimatedBay` presence; and
- each operational flag's presence and value distribution.

### Situation, service information, and errors

- Situation count;
- `SituationFullRef` association count;
- `IncidentCall` count;
- ordinary passenger-information `Attribute` count;
- populated response-level `ErrorMessage` count;
- populated result-level `ErrorMessage` count; and
- unmapped source-field count.

No percentage in this report may imply provider-wide coverage from one station
and one bounded hour.

## 17. Required `RecordedAtTime` coverage report

The Stage 8 report must explicitly list, for every observed non-null source
`RecordedAtTime` occurrence:

- occurrence count;
- distinct event count;
- event/source identity;
- call scope: `THIS`, `PREVIOUS`, or `ONWARD`;
- arrival/departure context;
- response identity;
- `ObservedAt`;
- first collector observation time;
- exact source value; and
- whether later responses retained or omitted the value.

If the count is zero, the report must include the exact bounded result:

```text
RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT
```

This means absence in the observed sampling window, not a provider-wide
support or absence conclusion.

## 18. Representativeness and source boundaries

The one-station, one-hour pilot validates only:

- technical collection;
- source-field preservation;
- repeated-history semantics;
- failure and gap handling;
- diagnostic pilot persistence; and
- source-field availability in the observed sample.

It does not validate:

- Cologne-wide field-population percentages;
- mode-wide or operator-wide coverage;
- production polling cadence;
- 50-station performance;
- monthly production quota;
- network reliability; or
- an Actual Delay KPI.

The pilot must not normalize source-reported Situation evidence into
operational facts and must not perform cross-source matching. GTFS-RT remains
outside the Stage 8 one-source loop; Stage 9 owns reconciliation design and
implementation.

## 19. Stage 8 handoff

Stage 8 may begin only as a separately identified host-execution stage. Its
implementation handoff is:

1. perform the safe credential-presence preflight;
2. at pilot start, select `PilotAnchorDepArrTime` and use that fixed anchor in
   the exact request contract in Section 4 for every request;
3. apply the named V3 sampling policy, fixed 3-second start-to-start slot
   boundary, no-retry rule,
   timeout, overlap, quota, and early-stop controls;
4. capture only the approved bounded raw exception and protected JSONL
   artifacts;
5. preserve the complete approved field selection and source timing
   semantics;
6. reconcile parser output to pilot persistence before interpreting metrics;
7. produce the required acceptance metrics and `RecordedAtTime` report;
8. clean up raw and JSONL temporary artifacts according to the execution
   report; and
9. keep all conclusions bounded to the pilot sample.

Stage 8 execution, host-network access, runtime code, and any future
PostgreSQL implementation are not part of this Stage 7 closeout.

## 19.1 Historical S40/S41 authorization/request-contract diagnostic gate

The separate Stage 8 diagnostic is implemented at
[`scripts/stage_08_authorization_request_contract_diagnostic.py`](../../scripts/stage_08_authorization_request_contract_diagnostic.py).
It is not the one-hour pilot and does not alter the V3 cadence, quota,
persistence, parser, reconciliation, retention, or field-coverage contract.
When a live branch permits the pilot, S41 passes one explicit effective
configuration into the existing `run_pilot` path; the Hbf/N20/V3 values remain
the defaults.
It makes only the decision-tree requests required to distinguish current
authorization from result-set breadth/provider access:

- Control A uses Köln Hbf, `de:05315:11201`, current `RequestTimestamp` and
  `DepArrTime`, and `NumberOfResults=5`.
- Control B runs only after A=`200` and changes only
  `NumberOfResults` to `20`.
- Control C runs only after A=`401`, uses Köln Heumarkt,
  `de:05315:11110`, and otherwise keeps A's contract.

The controls are sequential, zero-retry, redirect-free, and use the existing
30-second timeout. They retain protected temporary evidence outside Git with
status, content type, elapsed time, byte count, sanitized provider error text,
and parser parseability. The API-key value is never printed, logged, hashed,
fingerprinted, compared, or persisted.

The latest 2026-09-28 Stage 8 attempt returned HTTP `401` with the observed
German response:

`Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`

The available 2026-09-29 repository/Codex process could not run Control A
because its presence-only preflight reported `MDD_API_KEY: MISSING`; therefore
no differential result exists yet, the active V3/N20 design remains
unchanged, and the pilot was not started. The user's separate local Terminal
had previously reported `MDD_API_KEY: PRESENT`; these are different process
environments, not contradictory claims, and this does not establish whether
the separate Terminal credential is correct or incorrect.

The S41 detector recognizes the German supplier-access evidence through
normalized stable phrases as well as the existing English wording. A permitted
decision selects Hbf/N20/V3, Hbf/N5 under a new policy, or Heumarkt/N5 under a
separate new policy, and then invokes the same `run_pilot` path. The actual
pilot creates its own fixed start-time `DepArrTime` anchor; diagnostic control
timestamps are not reused.

Only a live A=`200`, B=`401` response carrying the supplier-access message may
supersede `NumberOfResults=20` with `5` for Stage 8, under a new versioned
sampling/request policy. A=`401`, C=`200` may move the bounded validation
target to Heumarkt and use `5`; A=`401`, C=`401` stops before the pilot. No
branch removes PreviousCalls, OnwardCalls, realtime data, parser fields, or
changes the approved future 50-station objective. Stage 13 still owns the
production `NumberOfResults` decision.

## 20. Stage 7 acceptance criteria

| # | Criterion | Result |
|---:|---|---|
| 1 | One primary source is selected. | PASS — MDD/TRIAS only for the Stage 8 primary pilot. |
| 2 | One pilot station is selected with rationale. | PASS — Köln Hbf, `de:05315:11201`. |
| 3 | The exact request contract is fixed. | PASS — Section 4. |
| 4 | `NumberOfResults` is fixed and justified. | PASS — 20 for the bounded pilot only. |
| 5 | The `TimeWindow` decision is explicit. | PASS — omitted; semantically available but not selected. |
| 6 | Cadence, duration, and request ceiling are explicit. | PASS — V3 3-second fixed start-to-start slots, 60-minute maximum, 1200 scheduled slots and normal HTTP attempts. |
| 7 | Timeout and retry behavior are explicit. | PASS — 30 seconds, zero retries. |
| 8 | Quota accounting is explicit. | PASS — actual attempts counted; 250,000/month remains the conservative ceiling; 207,500–210,000/month (83–84%) is the operational planning envelope; the 1200-attempt maximum is 0.48% of the full ceiling and approximately 0.57–0.58% of the envelope. |
| 9 | Overlap and catch-up behavior are explicit. | PASS — no concurrency or catch-up; overlap slot skipped. |
| 10 | Authentication, rate-limit, and failure stop rules are explicit. | PASS — Section 6. |
| 11 | Field retention is tied to approved prior contracts. | PASS — Sections 2 and 7. |
| 12 | Timing semantics remain unchanged. | PASS — source fields remain independent. |
| 13 | Estimate history is explicitly preserved. | PASS — repeated observations remain distinct. |
| 14 | The `RecordedAtTime` validation rule is explicit. | PASS — source-only, exact, nullable, immediately append-only when observed in any retained call/arrival-departure context, first-seen lineage, later NULL-safe, never synthesized. |
| 15 | Situation/cause semantics remain non-causal by default. | PASS — association/context only. |
| 16 | Raw-capture purpose, storage, access, retention, and cleanup are explicit. | PASS — bounded external exception, 700/600 permissions, maximum 24 hours, cleanup required. |
| 17 | Pilot persistence works without PostgreSQL by design. | PASS — protected append-only JSONL harness. |
| 18 | Parser-to-persistence reconciliation is explicit. | PASS — Section 13. |
| 19 | Exact replay versus repeated observation idempotency is explicit. | PASS — Section 14. |
| 20 | Gap/failure classifications are explicit. | PASS — Section 15. |
| 21 | Stage 8 metrics are explicit. | PASS — Section 16. |
| 22 | Representativeness limitations are explicit. | PASS — Section 18. |
| 23 | GTFS-RT is explicitly excluded from the one-source pilot. | PASS — Section 3 and Section 18. |
| 24 | Actual Delay remains unapproved. | PASS — Section 9. |
| 25 | Stage 8 was not started at the Stage 7 design closeout. | PASS — this document is a design handoff; current execution is recorded separately. |
| 26 | No network, database, SQL, or runtime execution occurred. | PASS — documentation-only Stage 7 work. |
| 27 | Documentation/status synchronization is required. | PASS — closeout files and status documents are updated with D-032; D-031's original cadence detail remains historical and superseded. |
| 28 | `git diff --check` passes. | PASS — local validation completed successfully. |

## 21. Final Stage 7 decision

The design and contract acceptance criteria are complete, and the repository
validation recorded in the completion decision passed. Stage 7 is:

**COMPLETE — APPROVED PILOT DESIGN**

At the time of this Stage 7 design closeout, Stage 8 was **NOT STARTED / NEXT**.
The current Stage 8 status is **COMPLETE** under the bounded execution record
in the Stage 8 closeout. This design document did not itself perform the
execution, and Stage 9+ work remains outside its scope.

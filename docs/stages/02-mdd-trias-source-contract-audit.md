# Stage 02 — MDD/TRIAS Source-Contract Audit

**Stage:** 02 — MDD/TRIAS Source-Contract Audit  
**Stage status:** IN PROGRESS
**Audit date:** 2026-09-25  
**Scope:** Evidence review for Stage 02, including the read-only Legacy
Collector implementation audit, the S13 audit of the successful controlled
`/delfi` response, local authorization and credential-equality follow-up, and
the historical S10/S11/S12 transport diagnostics. The controlled response is
audited below; this is not a claim about every MDD endpoint or a final
provider-wide MDD/TRIAS contract.

## 1. Environment & Repository Discovery

The authoritative new repository is:

```text
/Users/admin/Documents/cologne-transit-intelligence
```

The new repository was on branch `main`. At the start of this audit its Git
status was:

```text
## main...origin/main
?? prompts/
```

The configured `origin` remote was:

```text
https://github.com/sonazariso/cologne-transit-intelligence.git
```

The Legacy reference repository exists at:

```text
/Users/admin/Documents/cologne-public-transport-intelligence
```

The Legacy repository was on branch
`validation/realtime-stop-service-status-persistence` and had no tracked or
untracked changes at the time of inspection. Its `origin` remote was:

```text
https://github.com/sonazariso/cologne-public-transport-intelligence.git
```

This document records four different evidence layers and does not conflate
them:

1. TRIAS specification capability — not established by this source audit.
2. Actual MDD response behavior — established for the audited controlled
   `/delfi` request in the S13 raw artifact, but not generalized to all MDD
   endpoints or populations.
3. Legacy PowerShell request/parser behavior — established below from source.
4. Legacy SQL persistence behavior — established below from source and
   checked-in SQL definitions, without executing SQL.

## 2. Legacy Collector Files Found

All requested Legacy files were found:

- `collector/Run-MddRealtimeCollector.ps1`
- `collector/Invoke-MddRealtimeCollector.ps1`
- `collector/MddRealtimeCollector.psm1`

The module file was present, so the source audit continued. The first two
scripts are wrappers; the request builder, HTTP client, parser, and persistence
functions are in `collector/MddRealtimeCollector.psm1`.

## 3. TRIAS Request Builder

The function `New-MddTriasStopEventRequest` is in
`collector/MddRealtimeCollector.psm1:150-203`.

The source emits this structure:

```xml
<Trias xmlns="http://www.vdv.de/trias"
       xmlns:siri="http://www.siri.org.uk/siri"
       version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp>$requestTimestamp</siri:RequestTimestamp>
    <siri:RequestorRef>CologneTransitIntelligence</siri:RequestorRef>
    <RequestPayload>
      <StopEventRequest>
```

Proven source behavior:

- TRIAS namespace: `http://www.vdv.de/trias`.
- SIRI namespace: `http://www.siri.org.uk/siri`.
- Version: `1.2`.
- Structure: `Trias → ServiceRequest → RequestPayload → StopEventRequest`.
- `RequestTimestamp`: SIRI-qualified and emitted in the `ServiceRequest`.
- `RequestorRef`: `CologneTransitIntelligence`.
- `LocationRef → StopPointRef`: present and populated from `$StopPointRef`.
- `DepArrTime`: present and set to the same formatted timestamp.
- `NumberOfResults`: validated from 1 to 100; builder default is 5.
- `StopEventType`: `arrival`.
- `IncludePreviousCalls`: `true`.
- `IncludeOnwardCalls`: `true`.
- `IncludeRealtimeData`: `true`.
- No additional request parameters beyond the enclosing `Location`,
  `RequestPayload`, and `Params` structures are emitted.

Timestamp behavior:

- A missing builder timestamp uses `(Get-Date).ToUniversalTime()`.
- A supplied timestamp is converted with `.ToUniversalTime()`.
- Formatting is `yyyy-MM-ddTHH:mm:ssZ` using invariant culture.
- Fractional seconds are not emitted.
- The collector passes its UTC start timestamp into the builder at
  `collector/MddRealtimeCollector.psm1:1319-1320,1399-1403`.
- Automatic sampling can replace the effective `NumberOfResults` with the
  database target value when the caller did not explicitly supply one, at
  `collector/MddRealtimeCollector.psm1:1324-1325,1386-1388`.

Serialization/escaping behavior:

- The request is constructed as a PowerShell here-string.
- The source does not use an XML DOM, `XmlWriter`, XML serializer, or explicit
  escaping routine.
- `$StopPointRef` is interpolated verbatim at line 187. The source therefore
  does not prove that XML-special characters in a manually supplied value are
  escaped.
- The timestamp and integer values are generated or typed by the function.

## 4. HTTP Request & Authentication

The HTTP function is `Invoke-MddTriasRequest` at
`collector/MddRealtimeCollector.psm1:382-545`.

| Item | Source evidence |
|---|---|
| Endpoint | Default `https://mdd.gorheinland.com/delfi`, `1292-1296`; overrideable. |
| HTTP method | `POST`, `448-451`. |
| Content-Type | `application/xml; charset=utf-8`, line 452. |
| Header | `x-api-key`, lines 428-430. |
| API-key source | Explicit parameter, then Windows User environment `MDD_API_KEY`, then secure prompt, `1273-1289`. |
| Timeout | 30 seconds by default, configurable 1–120, `397-399`, `455`. |
| MaxAttempts | 3 by default, configurable 1–10, `400-401`. |
| Default retry delays | Exponential 2 seconds then 4 seconds; capped at 30 seconds, `359-380`. |
| Retryable HTTP statuses | 408, 429, 500, 502, 503, 504, `252-267`. |
| Retry-After | Numeric seconds and HTTP-date forms are read and capped at the configured maximum, `310-356`. |

The wrapper `Invoke-MddRealtimeCollector.ps1:3-10` uses the same endpoint and
retry defaults. `Run-MddRealtimeCollector.ps1:9-23` tees normal output and
failure details to a log, but the collector does not explicitly log the
request body or response body.

The successful HTTP response stores `$response.Content` in memory at
`collector/MddRealtimeCollector.psm1:465-468` and passes it to the parser at
line 1420. The parser then applies `ConvertFrom-Json` at lines 559-563.
Therefore the source proves that raw response content exists in memory before
parsing, but does not prove that the live MDD response is XML. It does not use
an XML response parser.

The core collector does not pass raw response content to SQL or write it to a
raw-response file. A separate inspection script writes derived evidence only;
that script states that raw response content was inspected in memory.

## 5. Response Parser Field Matrix

The parser is `ConvertFrom-MddTriasResponse` at
`collector/MddRealtimeCollector.psm1:547-739`. The candidate names below are
classified strictly against the Legacy implementation.

| Candidate field | Class | Legacy implementation evidence |
|---|---:|---|
| `RecordedAtTime` | D | Not found. The parser instead reads `serviceDelivery.responseTimestamp` into `ObservedAtUtc`, `570-571`. |
| `TimetabledTime` | A | Reads `timetabledTime` into `TimetabledArrivalUtc`, `602`; maps it to the DataTable, `803-825`. |
| `EstimatedTime` | A | Reads `estimatedTime` into `EstimatedArrivalUtc`, `603`; maps it to the DataTable, `804-825`. |
| `EstimatedTimeLow` | D | Not found. |
| `EstimatedTimeHigh` | D | Not found. |
| `OperatingDayRef` | D | Not found. |
| `StopSeqNumber` | D | Not found. |
| `Cancelled` | D | Not found. |
| `NotServicedStop` | A | Parsed at `606`; DataTable column and row mapping at `807-809,828-830`. |
| `NoBoardingAtStop` | A | Parsed at `607`; DataTable column and row mapping at `808-809,829`. |
| `NoAlightingAtStop` | A | Parsed at `608`; DataTable column and row mapping at `809-810,830`. |
| `UnplannedStop` | D | Not found. |
| `PlannedBay` | A | Parsed at `604`; DataTable column and row mapping at `805-806,826`. |
| `EstimatedBay` | A | Parsed at `605`; DataTable column and row mapping at `806-807,827`. |
| `JourneyRef` | A | Parsed at `597`; DataTable column and row mapping at `798-799,819`. |
| `LineRef` | A | Parsed at `596`; DataTable column and row mapping at `797-798,818`. |
| `DirectionRef` | A | Parsed at `598`; DataTable column and row mapping at `799-800,820`. |
| `OperatorRef` | A | Parsed at `599`; DataTable column and row mapping at `800-801,821`. |
| `OriginStopPointRef` | D | Not found. |
| `DestinationStopPointRef` | D | Not found. |
| `PreviousCall` | D | Not parsed. Only the request flag `IncludePreviousCalls` is present at line 194. |
| `ThisCall` | C | The `thisCall` container is traversed to reach `callAtStop`, `582-586,683-684`; the container itself is not persisted. |
| `OnwardCall` | D | Not parsed. Only the request flag `IncludeOnwardCalls` is present at line 195. |
| `ErrorMessage` | C | Not parsed from the MDD response. The name is used for sanitized collector exception/audit text at `1090-1130,1560`, not as a response field. |
| `Situation` | A | `ptSituation` is parsed, `625-660`; valid situations are returned and mapped to the situation DataTable, `734-735,834-853`. |

No candidate is classified `B`.

`D = Not found in the Legacy implementation. It does NOT prove that MDD does
not provide the field.` This distinction applies to every `D` row above.

For `Situation`, the parser creates objects for all observed situations but
only situations with nonblank `ParticipantRef` and `SituationNumber` enter
`ValidSituations` and the persistence DataTable. Unidentified situations are
counted for audit.

## 6. Persistence Boundary

The source path is:

```text
MDD response content
→ ConvertFrom-MddTriasResponse
→ snapshot object
→ New-MddRealtimeSnapshotDataTables
→ typed TVPs
→ stg.uspPersistMddRealtimeSnapshot
```

`New-MddRealtimeSnapshotDataTables` constructs three `System.Data.DataTable`
objects at `collector/MddRealtimeCollector.psm1:785-877`:

1. `StopObservations` / `$stopTable`: `ResultId`, `StopPointRef`, `StopName`,
   `LineName`, `LineRef`, `JourneyRef`, `DirectionRef`, `OperatorRef`,
   `PtMode`, `RailSubmode`, `TimetabledArrivalUtc`, `EstimatedArrivalUtc`,
   `PlannedBay`, `EstimatedBay`, `NotServicedStop`, `NoBoardingAtStop`,
   `NoAlightingAtStop`.
2. `SituationObservations` / `$situationTable`: `ParticipantRef`,
   `SituationNumber`, `Summary`, `Description`, `Detail`, `ValidFromUtc`,
   `ValidToUtc`.
3. `SituationLinks` / `$linkTable`: `ResultId`, `ParticipantRef`,
   `SituationNumber`, `RelationScope`.

`Write-MddRealtimeSnapshot` calls
`stg.uspPersistMddRealtimeSnapshot` at lines 1227-1228 and passes:

- `@ObservedAtUtc`
- `@StopObservations` as `stg.MddRealtimeStopObservationInputType`
- `@SituationObservations` as `stg.MddRealtimeSituationObservationInputType`
- `@SituationLinks` as `stg.MddRealtimeStopSituationLinkInputType`

The checked-in SQL definitions show the same TVP names and columns in
`sql/02-staging/06-create-mdd-realtime-persistence-api.sql:62-128`. The SQL
file was read only; no SQL was executed.

The parsed StopEvent fields that are mapped into SQL are the stop fields in
the first DataTable, including normalized UTC timetable/estimate values,
identity fields, bays, and explicit stop-service flags. Parsed situation
objects lacking required identity are excluded before the situation TVP.
Invalid situation links are not emitted. `UnidentifiedSituationCount` is used
for collector-run audit metrics, not as a raw situation row.

Raw MDD response XML/JSON does not reach SQL. The persistence function receives
the parsed snapshot and `ObservedAtUtc`, not `$response.Content`.

## 7. Documentation vs Implementation

The repository’s documented baseline is in
`docs/09-REALTIME-MDD-TRIAS-INTEGRATION-AND-GTFS-MATCHING.md:93-98`. The
implementation comparison is:

| Documented expectation | Legacy implementation |
|---|---|
| `POST https://mdd.gorheinland.com/delfi` | Matches the default; endpoint is overrideable. |
| `Content-Type: application/xml` | Sends `application/xml; charset=utf-8`; compatible in intent but not textually identical. |
| `x-api-key` | Matches. |
| TRIAS 1.2 | Matches. |
| `NumberOfResults` normally 5 | Builder/default is 5, but automatic sampling can replace it with a configured sampling-target value when not explicitly supplied, `1324-1325,1386-1388`. |
| `StopEventType = arrival` | Matches. |
| `IncludePreviousCalls = true` | Matches request construction. |
| `IncludeOnwardCalls = true` | Matches request construction. |
| `IncludeRealtimeData = true` | Matches request construction. |

Additional source findings are not assumptions about MDD: the request includes
`RequestorRef` and `DepArrTime`, while the response parser expects JSON-shaped
`serviceDelivery` content and only traverses `thisCall`.

## 8. Important Findings

- This is a Legacy implementation audit, not approval of the final MDD/TRIAS
  contract.
- The request is hand-built XML; `$StopPointRef` is not explicitly escaped.
- The response path is JSON parsing despite the XML request body. S13 confirms
  that this behavior matches the successful audited `/delfi` response, whose
  media type was JSON; it is not generalized to every MDD endpoint.
- Previous and onward calls are requested but not parsed.
- The Legacy parser does not handle `RecordedAtTime`, cancellation, operating
  day, stop sequence, origin/destination, or low/high estimate candidates.
- Implemented stop-event fields are mapped into the stop-observation TVP.
- Invalid/unidentified situations are counted but excluded from the situation
  persistence TVP.
- Raw response content is held in memory before parsing but is not passed to
  SQL or deliberately written as a raw payload by the core collector.
- Automatic sampling means `NumberOfResults` is not always 5.

None of these findings state that MDD cannot provide a field. They state only
what the Legacy implementation does or does not handle.

## 9. Unknowns / Not Proven After the S13 Audit

The successful artifact resolves the response-format and controlled-response
structure questions for this `/delfi` request. The following remain open:

- Whether `RecordedAtTime`, direct delay, cancellation, or other absent fields
  are returned for other request times, stops, services, or MDD endpoints.
- Whether the audited field coverage is representative of the intended Cologne
  population; one response cannot establish population coverage or absence in
  MDD generally.
- Whether the provider's authoritative TRIAS/MDD documentation assigns the
  exact semantics needed for future analytical retention, especially for
  `deviation`, operational flags, situations, and estimate timestamps.
- Current quota, retention, and provider-policy constraints beyond the
  successful authorization evidence.
- Whether deployed Windows Collector files exactly match the inspected branch.
- Whether any external historical logs or copies contain raw payloads.
- Whether the deployed SQL Server objects match the checked-in definitions.

Stage 02A.1 made no MDD request, executed no SQL, changed no database or
Task Scheduler state, modified no Legacy file, and did not expose the API key.

## 10. Historical Stage 02A.2 Probe Design

At the time of the Stage 02A.1 source audit, Stage 02A.2 probe execution had
not been performed. That historical preflight status is recorded below; the
later controlled 401 and S10 transport result are recorded in §§12 and 14.

The controlled probe should:

1. Use an isolated, dry-run harness around the existing request builder and
   parser, without collector persistence or collector-run audit calls.
2. Make the minimum authorized request set against approved StopPointRefs,
   with bounded attempts and no unnecessary quota activity.
3. Capture redacted response metadata and a field/path inventory, including
   actual response media type, top-level structure, multiplicity, nullability,
   and timestamp formats.
4. Compare raw response paths for `thisCall`, `previousCall`, `onwardCall`,
   timing, cancellation, sequence, bay, situation, and identity fields with
   the parser output.
5. Measure parser-to-persistence loss in memory by constructing DataTables
   without executing SQL.
6. Run an offline builder check using XML-special-character test values to
   establish whether the current request string is well-formed; do not send
   that synthetic value to MDD.
7. Retain raw response content in memory only unless separate approval
   authorizes secure raw-payload retention.

The probe’s output should distinguish, for every candidate field:

```text
TRIAS capability → actual MDD response → Legacy parser handling → SQL persistence
```

No MDD request, SQL query, database change, Legacy repository change, or
Task Scheduler change was made for Stage 02A.1.

## 11. Historical Stage 02A.2 Preflight Status

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe
**Status:** BLOCKED
**Preflight date:** 2026-09-25

This section records the preflight performed before the API key became
available to the Codex execution environment. It is historical context, not
the final status of the controlled probe:

- `MDD_API_KEY` present = **NO**.
- HTTP attempts = **0**.
- Probe execution did not occur.
- Blocker = API key unavailable in the execution environment.
- No source-contract conclusion was made from this failed preflight.

Stage 02 remains in progress and is not complete or approved.

## 12. Historical Stage 02A.2 Controlled Probe Result (HTTP 401)

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe
**Status:** BLOCKED
**Probe request time:** `2026-09-25T16:41:44.264Z`
**Maximum authorized HTTP attempts:** `1`
**Actual HTTP attempts:** `1`
**HTTP retries:** `0`

### 12.1 Probe implementation and final preflight

The diagnostic-only probe is [scripts/stage_02a2_probe.py](../../scripts/stage_02a2_probe.py).
It constructs the request from the audited Legacy builder contract and has one
network call, no retry loop, no redirect follow-up, no Legacy Collector
invocation, no SQL/database code, and no Task Scheduler code. The API key is
read only from `MDD_API_KEY` and supplied only as the `x-api-key` header; it was
not printed, logged, or written to disk.

The authorized target was:

| Item | Value |
|---|---|
| Station | Köln Hbf |
| `StopPointRef` | `de:05315:11201` |
| `NumberOfResults` | `5` |
| `StopEventType` | `arrival` |
| `IncludePreviousCalls` | `true` |
| `IncludeOnwardCalls` | `true` |
| `IncludeRealtimeData` | `true` |
| Endpoint | `https://mdd.gorheinland.com/delfi` |
| Method | `POST` |
| Request `Content-Type` | `application/xml` |
| Authentication header | `x-api-key` |

Final preflight confirmed `MDD_API_KEY` visible = **YES**. The XML request
used the audited TRIAS 1.2 and SIRI namespaces, `RequestorRef` value,
`LocationRef`, `DepArrTime`, timestamp shape, and `Params` hierarchy.

The table above records the already completed historical request and therefore
retains its actual `application/xml` header. After that request, the probe
source was normalized to the Legacy value
`application/xml; charset=utf-8`. S10 invoked that normalized probe exactly
once, but DNS resolution failed before an HTTP response was obtained, so
server receipt of the normalized request is not confirmed. The S10 re-run and
its transport result are recorded separately in §14.

### 12.2 HTTP result

| Measurement | Result |
|---|---|
| HTTP status | `401` |
| Response `Content-Type` | `application/json;charset=UTF-8` |
| Response size | `162` bytes / `161` decoded characters |
| Elapsed request duration | `157.061` ms |
| Response body | UTF-8 text stating that access to this data provider is not enabled; exact raw body is not copied into source-controlled documentation |

The single request failed with an access-denied response. The response does
not establish whether the cause was the key, provider-side permission, data
provider enablement, or another authentication/authorization condition.

### 12.3 Actual MDD response format and structure

The response header advertised `application/json;charset=UTF-8`, but the body
was not valid JSON and was not XML. It decoded as one plain UTF-8 text message
(payload format: **other / unstructured text**). JSON parsing failed at the
first character.

The following structures were therefore not returned or could not be
meaningfully audited:

- no JSON top-level object or TRIAS XML document;
- no `serviceDelivery` or response/status structure;
- no `stopEventResponse` or `stopEventResult` collection;
- no `PreviousCall`, `ThisCall`, or `OnwardCall` structure;
- no service/journey or location/stop structure;
- no structured situation/disruption linkage; and
- no named `ErrorMessage` or other structured error object.

These are not field-absence findings for MDD. They are limitations of the
single HTTP 401 error response.

### 12.4 Timing, identity, operational, and platform field audits

Because no valid service payload was returned, the recursive field audits were
not meaningful. Every field below is **not auditable from this response**;
occurrence count, path, and representative type/value are **N/A**. This must
not be converted into a `present = NO` conclusion.

| Audit group | Fields not auditable in the 401 response |
|---|---|
| Timing | `RecordedAtTime`, `TimetabledTime`, `EstimatedTime`, `EstimatedTimeLow`, `EstimatedTimeHigh` |
| Identity | `OperatingDayRef`, `StopSeqNumber`, `JourneyRef`, `LineRef`, `DirectionRef`, `OperatorRef`, `OriginStopPointRef`, `DestinationStopPointRef` |
| Operational status | `Cancelled`, `NotServicedStop`, `NoBoardingAtStop`, `NoAlightingAtStop`, `UnplannedStop`, `ErrorMessage` |
| Platform | `PlannedBay`, `EstimatedBay` |

In particular, the probe does not establish whether `RecordedAtTime` is
returned by MDD. The only permitted conclusion about this response is that
`RecordedAtTime` was not auditable in the controlled Köln Hbf access-denied
response.

### 12.5 Situation findings

No situation structure, identifier, service linkage, summary, description, or
detail field was available in the error response. No causality conclusion was
made.

### 12.6 Actual response versus Legacy parser and persistence

The actual-response column is intentionally the same limitation for each
candidate: the request returned no valid MDD service payload. The Legacy
columns reproduce the Stage 02A.1 evidence and do not turn the 401 body into a
successful response audit.

| Field | Actual MDD response | Legacy parser | Legacy SQL persistence | Observation |
|---|---|---|---|---|
| `RecordedAtTime` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `TimetabledTime` | Not observable in 401 body | A — parsed | `TimetabledArrivalUtc` | Actual presence remains unknown |
| `EstimatedTime` | Not observable in 401 body | A — parsed | `EstimatedArrivalUtc` | Actual presence remains unknown |
| `EstimatedTimeLow` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `EstimatedTimeHigh` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `OperatingDayRef` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `StopSeqNumber` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `Cancelled` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `NotServicedStop` | Not observable in 401 body | A — parsed | Stop-observation flag | Actual presence remains unknown |
| `NoBoardingAtStop` | Not observable in 401 body | A — parsed | Stop-observation flag | Actual presence remains unknown |
| `NoAlightingAtStop` | Not observable in 401 body | A — parsed | Stop-observation flag | Actual presence remains unknown |
| `UnplannedStop` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `PlannedBay` | Not observable in 401 body | A — parsed | `PlannedBay` | Actual presence remains unknown |
| `EstimatedBay` | Not observable in 401 body | A — parsed | `EstimatedBay` | Actual presence remains unknown |
| `JourneyRef` | Not observable in 401 body | A — parsed | `JourneyRef` | Actual presence remains unknown |
| `LineRef` | Not observable in 401 body | A — parsed | `LineRef` | Actual presence remains unknown |
| `DirectionRef` | Not observable in 401 body | A — parsed | `DirectionRef` | Actual presence remains unknown |
| `OperatorRef` | Not observable in 401 body | A — parsed | `OperatorRef` | Actual presence remains unknown |
| `OriginStopPointRef` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `DestinationStopPointRef` | Not observable in 401 body | D — not found | Not persisted | Actual presence remains unknown |
| `PreviousCall` | Not observable in 401 body | D — not parsed | Not persisted | Actual presence remains unknown |
| `ThisCall` | Not observable in 401 body | C — traversed, container not persisted | Not persisted as a container | Actual presence remains unknown |
| `OnwardCall` | Not observable in 401 body | D — not parsed | Not persisted | Actual presence remains unknown |
| `ErrorMessage` | Unstructured access-denied text only; no named field | C — used for collector error/audit text, not parsed as response field | Not persisted as response field | Not a valid structured MDD error object |
| `Situation` | Not observable in 401 body | A — parsed when valid | Situation observations and links | Actual presence remains unknown |

No A/B/C/D classification of *actual returned fields* is possible from this
failed request:

- A — returned by MDD and persisted by Legacy: **not determined**.
- B — returned by MDD but lost before Legacy persistence: **not determined**.
- C — handled by Legacy but absent from this response: **not determined**;
  the response was not a service payload.
- D — candidate not observed in this response: **not determined as an MDD
  capability claim**.

### 12.7 Raw artifact safety and limitations

For this investigation only, the exact response bytes were retained in the
mode-600 temporary file `/private/tmp/cologne-transit-stage-02a2-h6r5yofu.raw`.
The artifact is outside the repository, contains no API key or authentication
header, and is not tracked by Git. This is transient diagnostic evidence, not
a production raw-response retention policy. The raw payload was not added to
source-controlled documentation.

The probe establishes only the following:

- the authorized request was constructed and sent once;
- the endpoint returned HTTP 401;
- the response header/body format was inconsistent with a parseable JSON
  service payload; and
- a successful MDD response contract was not obtained.

It does not establish MDD field coverage, `RecordedAtTime` availability,
service-event multiplicity, situation linkage, platform semantics, quota
behavior, or the exact provider-side reason for access denial. Stage 02A.2
therefore remains blocked. Stage 2 is not approved or complete.

## 13. Historical Stage 02A.2 Local Authorization Diagnosis

**Diagnosis date:** 2026-09-25
**Scope:** Local source and documentation inspection only. No HTTP request,
SQL query, database change, Legacy repository change, Task Scheduler change, or
Power BI change was made for this diagnosis.
**HTTP requests authorized in this task:** 0
**MDD HTTP requests made for this local authorization diagnosis:** 0
**SQL queries executed:** 0

**Historical S8 diagnosis (superseded):** At that time, the comparison was
`MDD_API_KEY VALUE COMPARISON: NOT PERFORMED`, with outcome
`CREDENTIAL_IDENTITY_UNRESOLVED; PROVIDER_SUPPORT_PROVISIONAL`. Those values
describe the S8 state only and are not the current diagnosis.

**S9 credential follow-up:** The user independently established
`MDD_API_KEY VALUE COMPARISON: SAME` for the current macOS probe value and the
historically successful Windows User-level Legacy value. The environments and
credential scopes remain technically different, but credential value equality
is no longer unresolved.

**S10 request-level diagnosis:**
`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`.
The normalized probe was invoked exactly once and DNS failed before an HTTP
response was obtained.

Line numbers below refer to the inspected Legacy working copy at
`/Users/admin/Documents/cologne-public-transport-intelligence` and to the
probe in this repository. Secret values were not written to or exposed in the
report.

### 13.1 Legacy authentication configuration

| Question | Finding | Source evidence |
|---|---|---|
| API-key environment variable | `MDD_API_KEY` | Legacy `collector/MddRealtimeCollector.psm1:1273-1278` |
| HTTP authentication header | `x-api-key` | Legacy `collector/MddRealtimeCollector.psm1:428-430` |
| Endpoint and method | Default endpoint is `https://mdd.gorheinland.com/delfi`; the request is `POST` | Legacy `collector/MddRealtimeCollector.psm1:1292-1296,448-451` |
| Other authentication headers | None explicitly constructed; the request header hashtable contains only `x-api-key` | Legacy `collector/MddRealtimeCollector.psm1:428-430` |
| `RequestorRef` | `CologneTransitIntelligence` is part of the TRIAS XML body only. It is not used as HTTP authentication. | Legacy `collector/MddRealtimeCollector.psm1:175-183,428-430` |
| Provider/dataset identifier outside the TRIAS body | None found in the MDD request path. The request function accepts endpoint, body, API key, timeout/retry settings, logging, and telemetry; it has no provider/dataset argument or URL parameter. | Legacy `collector/MddRealtimeCollector.psm1:382-413,448-456` |
| Account/client/tenant/subscription/provider authorization parameter | None found in the MDD request path. `StopPointRef` is a TRIAS location identity, not an account or provider entitlement parameter. | Legacy `collector/MddRealtimeCollector.psm1:150-203,382-413` |
| Legacy key source | An explicit `ApiKey` parameter is accepted first; if blank, the module reads the Windows **User** environment value named `MDD_API_KEY`; if still blank, it prompts securely. | Legacy `collector/MddRealtimeCollector.psm1:1273-1289,1316-1317,1396-1397` |
| Process, machine, or config-file source | Not used by the production module's fallback path. The normal wrapper does not expose an API-key parameter and invokes the module. | Legacy `collector/Invoke-MddRealtimeCollector.ps1:3-10,19-33` |

The Legacy request builder sends `application/xml; charset=utf-8` and no
additional explicit authentication header (`collector/MddRealtimeCollector.psm1:448-456`).

### 13.2 Probe versus Legacy request comparison

| Item | Classification | Evidence and assessment |
|---|---|---|
| Endpoint | **MATCH** | Probe `scripts/stage_02a2_probe.py:24`; Legacy default `collector/MddRealtimeCollector.psm1:1292-1296`. |
| HTTP method | **MATCH** | Probe `scripts/stage_02a2_probe.py:118-125`; Legacy `collector/MddRealtimeCollector.psm1:448-451`. |
| Request `Content-Type` | **MATCH IN CURRENT SOURCE; HISTORICAL REQUEST DIFFERED** | The probe source sends `application/xml; charset=utf-8` (`scripts/stage_02a2_probe.py:122-125`), matching the Legacy value (`collector/MddRealtimeCollector.psm1:451-453`). The historical 401 request recorded in §12 used `application/xml`; S10 invoked the normalized probe exactly once, but DNS failed before an HTTP response, so server receipt of the normalized request is not confirmed. |
| Authentication header name | **MATCH** | Both use `x-api-key`: probe `scripts/stage_02a2_probe.py:122-125`; Legacy `collector/MddRealtimeCollector.psm1:428-430`. |
| API-key source | **DIFFERENT** | Probe reads the current Python process environment with `os.environ.get("MDD_API_KEY")` (`scripts/stage_02a2_probe.py:110-114`); Legacy resolves an explicit parameter, then Windows User environment, then secure prompt (`collector/MddRealtimeCollector.psm1:1273-1289`). |
| `RequestorRef` | **MATCH** | Both use `CologneTransitIntelligence`: probe `scripts/stage_02a2_probe.py:45-48`; Legacy `collector/MddRealtimeCollector.psm1:180-183`. |
| TRIAS namespace and version | **MATCH** | Both use the VDV TRIAS namespace, SIRI namespace, and version `1.2`: probe `scripts/stage_02a2_probe.py:41-44`; Legacy `collector/MddRealtimeCollector.psm1:175-179`. |
| Request structure | **MATCH** for the controlled target | Both use `ServiceRequest → RequestPayload → StopEventRequest → Location/LocationRef → Params`, with `DepArrTime`, five results, arrival events, and all three include flags: probe `scripts/stage_02a2_probe.py:45-66`; Legacy `collector/MddRealtimeCollector.psm1:180-201`. |
| Request timestamp | **MATCH** | Both emit UTC `yyyy-MM-ddTHH:mm:ssZ` without fractional seconds: probe `scripts/stage_02a2_probe.py:116`; Legacy `collector/MddRealtimeCollector.psm1:163-173`. |
| Redirect behavior | **DIFFERENT** / Legacy policy **UNKNOWN** from source | The probe explicitly rejects redirect follow-up (`scripts/stage_02a2_probe.py:30-35,135-139`). Legacy calls `Invoke-WebRequest` without an explicit redirect setting (`collector/MddRealtimeCollector.psm1:448-456`). The recorded response was a direct HTTP 401; no redirect was evidenced. |
| Retry behavior | **DIFFERENT** | The probe makes one attempt. Legacy defaults to up to three attempts, but classifies HTTP 401/403 as non-retryable (`collector/MddRealtimeCollector.psm1:397-407,440-456,505-508`). Retry behavior therefore cannot explain the first 401 response. |
| Other explicit request headers | **MATCH** in names | The probe explicitly supplies only `Content-Type` and `x-api-key` (`scripts/stage_02a2_probe.py:122-125`); the Legacy code explicitly constructs only `x-api-key` and supplies `ContentType` separately (`collector/MddRealtimeCollector.psm1:428-430,451-453`). No additional Legacy authentication header was found. |

The current process and Windows User environment scopes remain technically
different, but S9 independently established that their `MDD_API_KEY` values
were the same; credential value equality is therefore resolved. The historical
401 retains its `Content-Type` difference from the normalized probe source.
S10 invoked the normalized request exactly once, but DNS failed before an HTTP
response, so server receipt is not confirmed. The endpoint, explicit
authentication header name, RequestorRef, namespaces, timestamp shape, and
controlled TRIAS body otherwise match. No local evidence identifies a missing
account, client, tenant, subscription, provider, or dataset parameter.

### 13.3 Named key-source comparison

**SAME CONFIGURATION SOURCE: YES**

Both implementations are intended to use the same named configuration source,
`MDD_API_KEY`. The Legacy collector's normal fallback is the Windows User
environment (with a secure prompt fallback), while the current macOS probe
reads the environment of the Codex/Python process. The environment scopes are
different, but the user separately established that the two values are the
same.

**MDD_API_KEY VALUE COMPARISON: SAME**

The user locally compared the SHA-256 fingerprints of the Windows User-level
Legacy value and the macOS probe value and established that the values are the
same. This report does not reproduce either fingerprint and does not contain
the key, its length, prefix, suffix, or any other derived secret material. The
user also relaunched VS Code from the macOS terminal carrying the confirmed
credential so the probe process could inherit it.

### 13.4 Historical Legacy success evidence

The most recent explicit HTTP 200 evidence located in the Legacy repository is
the compatibility report
`docs/25-REALTIME-50-STATION-MDD-COMPATIBILITY.md:1-31,36-43`, timestamped
`2026-09-22T12:35:17Z`. It records 50 HTTP 200 responses, including Köln Hbf
(`de:05315:11201`) at line 42. This is a Legacy compatibility diagnostic using
the Legacy request path, not the scheduled production collector.

For the scheduled production collector, the latest documented live verification
is `2026-09-08 13:45:46 UTC`; its latest collector run started at
`13:43:52 UTC` and is documented as `Succeeded` (`docs/09-REALTIME-MDD-TRIAS-INTEGRATION-AND-GTFS-MATCHING.md:31-38`).
The same document explicitly records HTTP 200 authentication and successful
collector logs, including Köln Hbf, in
`docs/09-REALTIME-MDD-TRIAS-INTEGRATION-AND-GTFS-MATCHING.md:91-102,430-438,475-481`.
A later 2026-09-24 diagnostic inspection is explicitly a separate raw probe,
not a scheduled Legacy Collector run (`docs/29-TIMING-UNAVAILABLE-ROOT-CAUSE.md:155-183`).

This historical evidence supports that the Legacy Windows request path and its
then-used authorization context worked at those historical points. The
historical Legacy evidence alone did not prove credential equality. The later
S9 user-side comparison independently established that the current Windows
User-level and macOS probe credential values were the same.
The current Stage 1 quota summary at
`docs/PROJECT-STATUS.md:74-81` records executions and HTTP attempts, but does
not provide per-attempt HTTP 200 status evidence and is not used to infer it.

### 13.5 Local hypothesis classification

| Hypothesis | Classification | Local evidence |
|---|---|---|
| A. Probe authentication implementation differs from Legacy | **SUPPORTED** | The key environment scope, redirect policy, and retry behavior differ or are not equivalent as documented in §13.2; the historical 401 also used a different request `Content-Type`. This establishes implementation differences, not their causal role in the 401. |
| B. Current API-key value may differ from the Legacy working key | **NOT SUPPORTED by the user's comparison** | The user established that the Windows User-level Legacy value and macOS probe value are the same. This does not establish that the key is valid or currently authorized. |
| C. Same key may exist but DELFI/provider entitlement is disabled | **SUPPORTED for the earlier 401 only; current state undetermined** | The earlier controlled response stated that access to the data provider was not enabled (`§12.2`). The S10 re-run failed during DNS resolution before an HTTP response, so it provides no new provider-side authorization evidence. |
| D. Account/provider authorization may have changed since historical success | **POSSIBLE** | Historical HTTP 200 evidence exists, while the later controlled request returned 401. No local change record proves when or why authorization changed. |
| E. Endpoint/request contract may have changed | **POSSIBLE** for server-side contract drift; endpoint change itself is not supported | The endpoint and TRIAS body match the Legacy source, but no current provider contract or redirect policy is locally authoritative. The historical 401 request used a different `Content-Type` spelling; the S10 probe source and preflight now use the normalized Legacy value. |
| F. Another locally evidenced explanation | **NOT SUPPORTED** | No additional account, client, tenant, subscription, provider, dataset, or authentication-header condition was found in the local request path. |

No listed hypothesis is established as the sole cause, and no hypothesis is
disproved by the local evidence. In particular, the 401 must not be converted
into a claim that the API key itself is invalid.

### 13.6 Existing authorization and correspondence documentation

The search was limited to the new repository and the Legacy repository. It did
not search the home directory, browser profiles, Keychain, email, or unrelated
files.

Useful non-secret references found:

- Legacy `docs/02-DATA-SOURCES-AND-GTFS-PROFILE.md:75-115` documents the DELFI
  endpoint, `x-api-key` access model, monthly planning limit, and an HTTP 200
  five-result sample.
- Legacy `docs/09-REALTIME-MDD-TRIAS-INTEGRATION-AND-GTFS-MATCHING.md:91-112`
  documents historical authentication success and a reviewed permission/
  retention correspondence boundary; it also identifies a contact for renewed
  out-of-scope review. Its API-key setup description is at `:353-357`.
- Legacy `docs/25-REALTIME-50-STATION-MDD-COMPATIBILITY.md:1-31,36-43`
  documents the dated 50-station HTTP 200 compatibility test.
- New `docs/PROJECT-STATUS.md:30-48,64-81` and this document's §12 record the
  current 401 result without treating it as proof of an invalid key.

No local document was found that records MDD registration, a provider
activation transaction, account enablement details, a client/tenant/provider ID,
or a current authorization change explaining the 401. The existing permission
correspondence is therefore not sufficient to resolve the current provider
entitlement state.

### 13.7 Local remediation decision

**S10 REQUEST-LEVEL DIAGNOSIS:**
`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`

The user's comparison removes a current Windows-versus-macOS key-value mismatch
as the explanation under investigation. However, the S10 re-run received no
HTTP response because endpoint name resolution failed before an HTTP response
could be obtained. It therefore cannot establish current provider
authorization, entitlement, key validity, or account state. The earlier 401
remains historical evidence and is not reclassified by the transport failure.

No provider-support email was sent or drafted from this result, and no second
request was authorized or made.

## 14. Historical Stage 02A.2 S10 Controlled Re-run Result

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe
**Status:** BLOCKED
**Probe request timestamp:** `2026-09-25T17:43:59.786Z`
**Probe network attempts:** `1`
**Confirmed MDD HTTP requests:** `0`
**HTTP retries:** `0`

This section records the single S10 re-run after the user confirmed that the
macOS `MDD_API_KEY` value matched the historically successful Windows User-level
Legacy value. The preflight verified the reviewed endpoint, `POST` method,
`x-api-key` header, `application/xml; charset=utf-8` request content type,
request body target, one-call path, disabled redirects, 30-second timeout, and
secure external temporary-artifact behavior. The process reported
`MDD_API_KEY` present = **YES**. No secret or fingerprint was read into this
document.

The probe was invoked exactly once. It failed with:

```text
URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
```

The probe reported an elapsed time of `33.598` ms. No HTTP status, response
`Content-Type`, response body, or response bytes were obtained. The failure is
consistent with DNS name resolution failing before an HTTP response; no MDD
HTTP request is confirmed as having reached the endpoint. The single probe
attempt is not a retry, and no follow-up request was made.

### 14.1 Response format and field audit

There was no response to classify as XML, JSON, or other, and no root,
TRIAS/service-delivery, `StopEventResult`, call, timing, identity, operational
status, platform, or situation structure was available for audit. These fields
remain unobserved; this run must not be converted into a claim about MDD field
support or field absence. The earlier HTTP 401 body and its limitations remain
documented in §12 as a separate historical response.

### 14.2 Raw-response safety

No response bytes were received, so no temporary raw-response artifact was
created. No raw response, authorization header, API key, or derived secret
material was written to the repository or documentation.

### 14.3 Current status and diagnosis

The current blocker is:

`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`

The result does not support classifying the provider as unauthorized,
disabled, revoked, or otherwise responsible for the failure. Stage 02A.2
remains **Blocked** because no valid operational MDD service payload was
obtained or audited. Stage 02 remains **In progress** and is not approved or
complete.

## 15. Historical Stage 02A.2 S11 DNS-only Diagnosis

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe

**Status:** BLOCKED

**Diagnostics date:** 2026-09-25

**MDD HTTP requests in S11:** 0

**MDD probe executions in S11:** 0

**SQL queries executed:** 0
**Database changes:** NONE

S11 performed DNS-only diagnostics. It did not execute
`scripts/stage_02a2_probe.py`, open an HTTP/HTTPS connection, connect to any
returned IP address, or modify DNS/network configuration.

### 15.1 Endpoint literal verification

The `ENDPOINT` source literal in `scripts/stage_02a2_probe.py:24` is exactly:

```text
https://mdd.gorheinland.com/delfi
```

It has no leading or trailing whitespace, hidden/non-ASCII characters, newline,
scheme error, hostname error, or path error.

**ENDPOINT_LITERAL_VALID: YES**

### 15.2 Python DNS results

Using `socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)` from the same
Codex environment as the probe:

| Hostname | Result |
|---|---|
| `mdd.gorheinland.com` | **DOES_NOT_RESOLVE** |
| `ckan.mdd.gorheinland.com` | **DOES_NOT_RESOLVE** |
| `download.gorheinland.com` | **DOES_NOT_RESOLVE** |
| `gorheinland.com` | **DOES_NOT_RESOLVE** |

No address was returned for any tested hostname.

### 15.3 macOS system DNS results

- `/etc/hosts` contains no `gorheinland.com` or MDD override.
- `dscacheutil -q host -a name` returned no host record for any of the four
  tested names.
- `scutil --dns` reported `No DNS configuration available` in this
  environment.
- The generated `/etc/resolv.conf` shows a `fritz.box` search domain and
  private nameserver entries, but macOS states that this file is not the
  resolver-routing source. The queried names are fully qualified, so the
  search domain does not plausibly explain the failure.
- Local `dig` was blocked before issuing a query (`Operation not permitted`),
  and service-level `networksetup` inspection was unavailable because
  `AuthorizationCreate()` failed. No VPN/tunnel-scoped resolver was therefore
  established from the available system evidence.

The system-resolver evidence is consistent with a local macOS/Codex resolver
failure, not with an MDD HTTP or provider-authorization result.

### 15.4 Public resolver comparison

Because the normal resolver could not resolve the MDD hostname, the permitted
comparison was made with `dig +time=2 +tries=1 @1.1.1.1 A`. No connection was
made to any returned address.

| Hostname | Public A result |
|---|---|
| `mdd.gorheinland.com` | **RESOLVES** → `195.14.241.23` |
| `ckan.mdd.gorheinland.com` | **RESOLVES** → `195.14.241.23` |
| `download.gorheinland.com` | **RESOLVES** → `195.14.241.21` |

The MDD hostname and sibling hosts therefore have public DNS records.

### 15.5 Historical classification and current qualification

**HISTORICAL S11 DNS CLASSIFICATION (inside the Codex environment):**
`LOCAL_MACOS_SYSTEM_RESOLVER_FAILURE`

S11 classified the observed Codex-environment resolver behavior as
`LOCAL_MACOS_SYSTEM_RESOLVER_FAILURE`. Because the diagnostics were
constrained by the Codex execution environment, host-level macOS DNS failure is
not yet independently established. S11 narrows the observed transport cause
inside that environment; it does not establish provider authorization.

The S10 request-level diagnosis remains
`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`.

**CURRENT DNS DIAGNOSIS:**
`CODEX_ENVIRONMENT_DNS_FAILURE; HOST_MACOS_RESOLVER_STATE_UNCONFIRMED; PROVIDER_AUTHORIZATION_UNDETERMINED`

**S12 NEXT ACTION:** Run the host-side DNS diagnostic from a normal
interactive macOS Terminal outside Codex and use its single final result to
choose the next approved action. Do not change DNS configuration or run the
MDD probe in S12.

Stage 02A.2 remains **Blocked** and Stage 02 remains **In progress**.

## 16. Historical Stage 02A.2 S12 Host-vs-Codex DNS Qualification

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe

**Status:** BLOCKED

**Diagnostics date:** 2026-09-25

**MDD HTTP requests in S12:** 0

**MDD probe executions in S12:** 0

**SQL queries executed:** 0
**Database changes:** NONE

S12 does not treat the S11 Codex-environment diagnostics as proof that the
ordinary interactive macOS host resolver is broken. The S11 classification is
retained as historical evidence and is qualified by the current diagnosis:
`CODEX_ENVIRONMENT_DNS_FAILURE; HOST_MACOS_RESOLVER_STATE_UNCONFIRMED; PROVIDER_AUTHORIZATION_UNDETERMINED`.

S12 created
`scripts/stage_02a2_host_dns_check.sh` for the user to run from a normal
interactive macOS Terminal. It checks the following hostnames with
`python3` and `socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)`:
`mdd.gorheinland.com`, `ckan.mdd.gorheinland.com`,
`gorheinland.com`, and `example.com`. It summarizes
`dscacheutil -q host -a name` results, uses the default
`dig +time=2 +tries=1 A` resolver when available, runs the
`@1.1.1.1` control query only when the normal/default MDD resolution
fails, and internally summarizes `scutil --dns` without exposing its
full output.

The host script is diagnostic-only. It reads no credential, makes no service
request, changes no network setting, uses no elevated command, and does not
flush caches or alter VPN/proxy state. It was not executed by Codex, so no
host DNS result is recorded here. Host-level DNS evidence remains pending.

### 16.1 Decision tree after the host result

| Host result | Next approved action |
|---|---|
| `HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS` | Run the controlled MDD probe from the normal trusted macOS host environment with the already-confirmed credential and exactly one HTTP attempt. Do not run it in S12. |
| `HOST_DNS_RESULT: NORMAL_RESOLVER_FAILS_PUBLIC_DNS_WORKS` | Repair or configure the macOS host DNS resolver before another MDD request. Do not modify DNS automatically in S12. |
| `HOST_DNS_RESULT: GENERAL_HOST_DNS_FAILURE` | Resolve the general macOS network/DNS problem first. |
| `HOST_DNS_RESULT: INCONCLUSIVE` | Keep Stage 02A.2 blocked and request only the minimum additional host diagnostic needed. |

Until the host result is supplied, Stage 02A.2 remains **Blocked** and Stage 02
remains **In progress**.

## 17. Stage 02A.2 S13 Successful Response Audit

**Stage:** 02A.2 — Controlled MDD Raw-Response Probe
**Status:** COMPLETED
**Audit date:** 2026-09-25

This section is the current authoritative Stage 02A.2 result and supersedes
the pre-S13 current-status statements in §§13–16. Those sections remain in the
document as historical chronology for the earlier 401, credential mismatch,
Codex-environment DNS failure, and host-vs-Codex DNS qualification.

The audit used only the existing successful user-run artifact from the
controlled host probe. No new MDD request or probe execution occurred in this
task.

### 17.1 Raw Artifact Verification

| Check | Result |
|---|---|
| Artifact | `/private/tmp/cologne-transit-stage-02a2-hhqob8km.raw` |
| Availability/readability | **YES**; readable with mode `0600` |
| Size | **71,602 bytes** |
| JSON parse | **SUCCESS** |
| Top-level JSON type | **object** |
| Top-level properties | `serviceDelivery`, `version` |
| Repository location | Outside the authoritative repository |
| Authentication/header material | No header envelope or authentication/header-named JSON property was structurally present. No JSON value was printed, searched, hashed, or compared for secret material. |

The supplied successful-probe metadata records request time
`2026-09-25T18:29:21.835Z`, endpoint `https://mdd.gorheinland.com/delfi`,
method `POST`, request Content-Type `application/xml; charset=utf-8`, one HTTP
attempt, HTTP 200, response Content-Type `application/json;charset=UTF-8`,
71602 response bytes, 71553 response characters, and 774.625 ms elapsed. No
retry occurred. The API-key value is not reproduced.

The artifact was used as temporary diagnostic evidence only. It was not copied
into this repository, and this audit does not define a policy to retain raw
MDD payloads permanently. It was not deleted because no approved cleanup
policy was supplied.

### 17.2 Actual Response Structure

The audited body is JSON even though the request body is XML. JSON path
notation below uses `[]` for any array element.

| Level | Actual path and evidence |
|---|---|
| Root | `$` is an object with `version` and `serviceDelivery`; `version` is `1.2`. |
| Delivery | `$.serviceDelivery` is an object with `responseTimestamp`, `producerRef`, `calcTime`, `language`, `status`, and `deliveryPayload`. |
| Payload | `$.serviceDelivery.deliveryPayload` contains `stopEventResponse`. |
| Response | `$.serviceDelivery.deliveryPayload.stopEventResponse` contains an empty `errorMessage` array, `stopEventResponseContext`, and `stopEventResult`. |
| Results | `$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[]` is an array of **5** result objects. Five results were returned. |
| Stop event | Each result has `stopEvent`; each `stopEvent` has `previousCall`, `thisCall`, `onwardCall`, `service`, `operatingDaysDescription`, and `parallelService`. |
| Context situations | `$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResponseContext.situations.ptSituation[]` contains **9** objects; `roadSituation[]` is empty. |

Per result, the call cardinalities are:

| Result index | `previousCall[]` | `thisCall` | `onwardCall[]` |
|---:|---:|---:|---:|
| 0 | 9 | 1 object | 0 |
| 1 | 11 | 1 object | 0 |
| 2 | 28 | 1 object | 2 |
| 3 | 44 | 1 object | 1 |
| 4 | 5 | 1 object | 6 |
| **Total call records** | **97** | **5** | **9** |

Every returned call record has a `callAtStop` object, for **111**
call-level records. `serviceArrival` occurs 106 times and
`serviceDeparture` occurs 103 times. `thisCall` is an
object rather than a collection; `previousCall` and
`onwardCall` are collections.

### 17.3 Deterministic Field Inventory

Counts below are occurrences in the response unless otherwise stated. A field
can be structurally present as an empty array; such fields are identified as
empty rather than treated as populated evidence. `varies` means the
populated value or collection differs across the five returned result objects
where the field is result-scoped.

#### Timing

| Candidate | Classification | Exact JSON path(s) | Count/type | Varies across results |
|---|---|---|---:|---|
| `TimetabledTime` | PRESENT | `$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[].stopEvent.previousCall[].callAtStop.serviceArrival.timetabledTime`; same `serviceDeparture` path; corresponding `onwardCall[]` paths; `thisCall.callAtStop.serviceArrival.timetabledTime` | 209 / string | YES |
| `EstimatedTime` | PRESENT | Same call branches as `TimetabledTime`, under `serviceArrival.estimatedTime` and `serviceDeparture.estimatedTime` | 196 / string | YES |
| `RecordedAtTime` | NOT_PRESENT_IN_THIS_RESPONSE | No path observed | 0 | — |
| `ExpectedTime`, `ActualTime`, `AimedTime` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key observed | 0 each | — |
| `ArrivalTime`, `DepartureTime` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key observed; `serviceArrival`/`serviceDeparture` are the observed containers | 0 each | — |
| `EstimatedTimeLow`, `EstimatedTimeHigh` | NOT_PRESENT_IN_THIS_RESPONSE | No path observed | 0 each | — |
| Direct `Delay`, `DelayMinutes`, `ArrivalDelayMinutes` | NOT_PRESENT_IN_THIS_RESPONSE | No path observed | 0 | — |
| `responseTimestamp` | PRESENT | `$.serviceDelivery.responseTimestamp` | 1 / string | Not result-scoped |
| Other timestamp-like fields | PRESENT | `...ptSituation[].creationTime` (9), `...ptSituation[].validityPeriod[].startTime` (3), `...ptSituation[].validityPeriod[].endTime` (3) | string | Situation-scoped |

**RecordedAtTime was not present in this controlled response.** This is a
single-response absence finding only; it does not establish that MDD never
provides `RecordedAtTime`.

#### Journey and service identity

| Candidate | Classification | Exact JSON path | Count/type | Varies across results |
|---|---|---|---:|---|
| `JourneyRef` | PRESENT | `...stopEventResult[].stopEvent.service.journeyRef.value` | 5 / string | YES |
| `LineRef` | PRESENT | `...stopEventResult[].stopEvent.service.serviceSection[].lineRef.value` (5); also `...ptSituation[].affects.vehicleJourneys.affectedVehicleJourney[].lineRef.value` (21) | 26 / string | YES |
| `DirectionRef` | PRESENT | `...service.serviceSection[].directionRef.value` | 5 / string | YES |
| `OperatorRef` | PRESENT | `...service.serviceSection[].operatorRef.value` | 5 / string | YES |
| `StopPointRef` | PRESENT | `...stopEventResult[].stopEvent.(previousCall[]/thisCall/onwardCall[]).callAtStop.stopPointRef.value` | 111 / string | YES |
| `DestinationText` | PRESENT | `...stopEventResult[].stopEvent.service.destinationText[].text` | 5 / string | YES |
| `DestinationRef` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key; `destinationStopPointRef.value` is present instead | 0 | — |
| `OriginText` | PRESENT | `...stopEventResult[].stopEvent.service.originText[].text` | 5 / string | YES |
| `OriginRef` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key; `originStopPointRef.value` is present instead | 0 | — |
| `originStopPointRef` / `destinationStopPointRef` | PRESENT | `...service.originStopPointRef.value`; `...service.destinationStopPointRef.value` | 5 each / string | YES |
| `ServiceSection` | PRESENT | `...stopEventResult[].stopEvent.service.serviceSection[]` | 5 arrays, one populated section per result | YES |
| `OperatingDayRef` | PRESENT | `...service.operatingDayRef.value` | 5 / string | NO |
| `ResultId` | PRESENT | `...stopEventResult[].resultId` | 5 / string | YES |
| Vehicle/journey identifiers beyond `JourneyRef` | NOT_PRESENT_IN_THIS_RESPONSE | `datedVehicleJourneyRef[]` and `vehicleJourneyRef[]` occur only as 21 empty arrays in situation impact objects; no `vehicleRef` or `vehicleType` key | 0 populated | — |

#### Call structure

| Candidate | Classification | Evidence |
|---|---|---|
| `PreviousCall` | PRESENT | `...stopEventResult[].stopEvent.previousCall[]`; 97 `callAtStop` records across five results |
| `ThisCall` | PRESENT | `...stopEventResult[].stopEvent.thisCall.callAtStop`; one object per result |
| `OnwardCall` | PRESENT | `...stopEventResult[].stopEvent.onwardCall[]`; 9 records across three results and empty arrays in two results |
| `callAtStop` | PRESENT | 111 objects, each containing stop/location and call-level fields |
| `serviceArrival` | PRESENT | 106 objects across previous/current/onward calls |
| `serviceDeparture` | PRESENT | 103 objects across previous/onward calls; no populated current-call departure object was observed |
| `stopPointRef` / `stopPointName` / `stopSeqNumber` | PRESENT | Each occurs on all 111 call records; `stopPointName[].text` is the localized display value and `stopSeqNumber` is numeric |

#### Realtime and operational status

| Candidate | Classification | Exact path/evidence |
|---|---|---|
| Delivery `status` | PRESENT | `$.serviceDelivery.status` is boolean `true`; the Legacy parser uses it as a validity gate |
| `deviation` | PRESENT | `...stopEventResult[].stopEvent.service.deviation`; boolean `true` in 2 of 5 services. Its precise provider semantics remain to be validated. |
| `notServicedStop` | PRESENT | `...callAtStop.notServicedStop`; 7 populated boolean values, all `true`, in three results; previous and current calls only |
| `noBoardingAtStop` | PRESENT | `...callAtStop.noBoardingAtStop`; 6 populated boolean values, all `true`, in three results; previous calls only |
| `noAlightingAtStop` | PRESENT | `...callAtStop.noAlightingAtStop`; 7 populated boolean values, all `true`, in three results; previous and current calls only |
| `unplannedStop` | PRESENT | `...previousCall[].callAtStop.unplannedStop`; 4 populated boolean values, all `true`, in three results |
| Cancellation/service availability/realtime status | NOT_PRESENT_IN_THIS_RESPONSE | No `cancellation`, `cancelled`, `serviceCancelled`, `stopCancelled`, `realtime`, `realtimeStatus`, `serviceStatus`, or `availability` key was observed |
| Direct delay | NOT_PRESENT_IN_THIS_RESPONSE | No direct delay field was observed; missing delay does not imply no delay |

The explicit boarding/alighting and service flags are evidence of named source
fields in this response. They must not be widened into a general cancellation
or service-availability conclusion.

#### Platform/bay

| Candidate | Classification | Exact path/evidence |
|---|---|---|
| Planned bay | PRESENT | `...callAtStop.plannedBay[].text`; non-empty in 110 of 111 call records, including all five `thisCall` records |
| Estimated/current bay | PRESENT | `...callAtStop.estimatedBay[].text`; non-empty in 12 of 111 records, all on `previousCall`; empty on all five `thisCall` records and all nine `onwardCall` records |
| `BayText`, `BayRef`, `Platform` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key observed; bay text is represented by `plannedBay[].text`/`estimatedBay[].text` |
| Explicit platform-change flag | NOT_PRESENT_IN_THIS_RESPONSE | No platform-change indicator observed |

Missing estimated-bay data is not evidence that the planned bay was unchanged.

#### Situations and disruptions

| Candidate | Classification | Exact path/evidence |
|---|---|---|
| `PtSituation` | PRESENT | `...stopEventResponseContext.situations.ptSituation[]`; 9 objects |
| `SituationFullRef` | PRESENT | `...service.situationFullRef[]`; 3 populated service references across three results. Call-level `situationFullRef[]` properties are present but empty. |
| `ParticipantRef` / `SituationNumber` | PRESENT | `...ptSituation[].participantRef.value` and `...ptSituation[].situationNumber.value` (9 each); also on the 3 service references |
| `Summary` / `Description` / `Detail` | PRESENT | `...ptSituation[].summary.value`, `.description.value`, `.detail.value` (9 each) |
| Validity period | PRESENT | `...ptSituation[].validityPeriod[]`; 3 populated periods with 3 `startTime` and 3 `endTime` values; six period arrays empty |
| `Priority`, `CreationTime`, `Source`, `SourceType`, `UnknownReason` | PRESENT | `priority` (9 numeric), `creationTime` (9 string), `source.sourceType` (9 string), `unknownReason` (9 string) |
| Affected journeys | PRESENT | `...ptSituation[].affects.vehicleJourneys.affectedVehicleJourney[]`; 21 objects, with 21 populated affected `lineRef` values and 21 `route[]` arrays whose `direction[]` arrays are empty |
| `SituationRef`, `Reason`, `Cause`, `Advice`, `Severity` | NOT_PRESENT_IN_THIS_RESPONSE | No exact key observed |

Situation references, affected lines, and situation text are structural
associations. **Situation Evidence does not prove causality.** Semantic
interpretation of `deviation` and situation impact relationships
remains UNKNOWN until the provider/TRIAS contract is validated.

No candidate required an `NOT_APPLICABLE` classification in this response;
`UNKNOWN` is reserved for unresolved semantics, not used to turn a field
absence into a provider-wide capability claim.

### 17.4 Additional Valuable Fields Actually Present

| Present field/path | Evidence | Management or BI question supported | Limitation |
|---|---|---|---|
| `stopSeqNumber` and `stopPointName[].text` | 111 call records | Which stop sequence and named locations are represented in a returned service call chain? | Requires matching and stop-scope rules before KPI use. |
| `operatingDayRef.value` | 5 of 5 services | Which operating day does a returned service identity belong to? | Not a replacement for a validated timetable/calendar contract. |
| `originStopPointRef.value`, `destinationStopPointRef.value` | 5 each | What origin/destination endpoints are associated with returned services? | Legacy does not currently parse these fields. |
| `mode.ptMode`, `mode.railSubmode`, `mode.name[].text` | 5 each | Which transport mode/submode is represented in the response mix? | The observed sample is not a population distribution. |
| `publishedLineName[].text` | 5 of 5 services | What public-facing line name accompanies each service identity? | It is display metadata, not necessarily a stable route key. |
| `service.attribute[].code` and `service.attribute[].text[].text` | 21 attribute objects across five results | Which source-provided service attributes are attached to journeys? | Code semantics require a provider codebook; no amenity KPI is inferred. |
| `service.deviation` | 2 boolean `true` values | Which returned services carry the provider's deviation flag? | Provider semantics and causality are not established. |
| Situation priority, creation time, source type, validity, affected line refs | 9 situation objects; 21 affected-journey objects | Which notices have what validity, source, priority, and affected-line scope? | Association is evidence, not confirmed cause; only this response is audited. |
| `serviceDelivery.responseTimestamp`, `calcTime`, `producerRef`, `language`, `version` | Delivery metadata present once | When and by which producer/version was the response produced, and what response calculation time was reported? | These are response metadata, not physical delay measures. |

No KPI is proposed here for actual delay, cancellation rate, platform-change
rate, or disruption causality because this response does not provide the
required direct evidence.

### 17.5 Actual Response vs Legacy Parser Matrix

The Legacy parser is `ConvertFrom-MddTriasResponse` at
`/Users/admin/Documents/cologne-public-transport-intelligence/collector/MddRealtimeCollector.psm1:547-739`.
The classifications below are limited to this actual response.

| Field/structure | Actual response | Legacy parser handling | Classification |
|---|---|---|---|
| `serviceDelivery.status` | Present, `true` | Validates it at `566-568` | ACTUAL RESPONSE + LEGACY PARSED |
| `serviceDelivery.responseTimestamp` | Present once | Converts to `ObservedAtUtc` at `570-571` | ACTUAL RESPONSE + LEGACY PARSED |
| `stopEventResult` / `ResultId` | Five results; five IDs | Enumerates results and reads `resultId` at `573-594` | ACTUAL RESPONSE + LEGACY PARSED |
| `ThisCall.callAtStop` | One current call per result | Traverses `thisCall.callAtStop` at `582-586` | ACTUAL RESPONSE + LEGACY PARSED |
| `PreviousCall` | 97 records | Not traversed; only the request flag is built | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `OnwardCall` | 9 records | Not traversed; only the request flag is built | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `TimetabledTime` | 209 timestamps across all call branches | Reads only current arrival `timetabledTime` at `602` | ACTUAL RESPONSE + LEGACY PARSED, with previous/onward occurrences not parsed |
| `EstimatedTime` | 196 timestamps across all call branches | Reads only current arrival `estimatedTime` at `603` | ACTUAL RESPONSE + LEGACY PARSED, with previous/onward occurrences not parsed |
| `RecordedAtTime` | Not observed | No `RecordedAtTime` path; uses response timestamp instead | NOT OBSERVED IN RESPONSE + LEGACY DOES NOT PARSE |
| Direct delay | Not observed | No direct delay parser field | NOT OBSERVED IN RESPONSE + LEGACY DOES NOT PARSE |
| `serviceArrival` | Present on current, previous, and onward records | Current arrival is parsed; other branches are not | ACTUAL RESPONSE + LEGACY PARSED, partially |
| `serviceDeparture` | 103 populated departure objects | Not read | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `JourneyRef`, `LineRef`, `DirectionRef`, `OperatorRef` | Present at service paths in all five results | Read at `596-599` | ACTUAL RESPONSE + LEGACY PARSED |
| `StopPointRef`, `StopPointName` | Present on all 111 call records | Reads current values at `593-595` | ACTUAL RESPONSE + LEGACY PARSED, current call only |
| `PtMode`, `RailSubmode`, `PublishedLineName` | Present in all five service sections | Reads mode and published line name at `595,600-601` | ACTUAL RESPONSE + LEGACY PARSED |
| `OriginText`, `OriginStopPointRef`, `DestinationText`, `DestinationStopPointRef` | Present in all five services | No corresponding parser properties | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `OperatingDayRef` | Present in all five services | No corresponding parser property | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `stopSeqNumber` | Present on all 111 call records | No corresponding parser property | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `PlannedBay`, `EstimatedBay` | Planned populated on 110 calls; estimated populated on 12 previous calls | Reads current-call properties at `604-605` | ACTUAL RESPONSE + LEGACY PARSED, current call only |
| `notServicedStop`, `noBoardingAtStop`, `noAlightingAtStop` | Populated on previous/current calls; no-boarding is absent on current calls | Reads current-call properties at `606-608` | ACTUAL RESPONSE + LEGACY PARSED, current call only |
| `unplannedStop` | Four populated previous-call flags | No corresponding parser property | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `deviation` | Two populated service flags | No corresponding parser property | ACTUAL RESPONSE + LEGACY NOT PARSED |
| Cancellation/realtime status fields | Not observed | No corresponding parser properties | NOT OBSERVED IN RESPONSE + LEGACY DOES NOT PARSE |
| `PtSituation` and situation text/validity | Nine situations; all nine have identity, text, and valid identity fields | Parses context situations at `621-676` | ACTUAL RESPONSE + LEGACY PARSED |
| Service `SituationFullRef` | Three populated service references | Parses service links at `678-705` | ACTUAL RESPONSE + LEGACY PARSED |
| Call `SituationFullRef` | Call-level properties empty | Parser has a current-call branch at `708-727`, but no populated current-call ref occurs here | ACTUAL RESPONSE + LEGACY PARSED, no populated value in this response |
| Situation priority/source/creation/unknown reason/affected journeys | Present in context | No corresponding parser properties | ACTUAL RESPONSE + LEGACY NOT PARSED |
| `service.attribute`, mode name, route/via, `parallelService` | Attributes present; route/via/parallel arrays empty | No corresponding parser properties | ACTUAL RESPONSE + LEGACY NOT PARSED or empty |
| `errorMessage` arrays | Structurally present but empty | Not parsed as response error objects | ACTUAL RESPONSE + LEGACY NOT PARSED |

No important requested field fell into **NOT OBSERVED IN RESPONSE + LEGACY
PARSER EXPECTS**: the parser's required identifiers and current-call path are
present in this response. Legacy parser capability still does not establish
provider capability for fields absent from this one response.

### 17.6 Actual Response vs Legacy SQL Persistence Matrix

This comparison is from checked-in source/schema only. No SQL query was
executed and no database was changed. The source path is the parser result →
`New-MddRealtimeSnapshotDataTables`
(`collector/MddRealtimeCollector.psm1:785-877`) → typed TVPs →
`stg.uspPersistMddRealtimeSnapshot`.

| Evidence class | Fields/structures | Source-only persistence conclusion |
|---|---|---|
| ACTUAL + PARSED + PERSISTED | `responseTimestamp` as `ObservedAtUtc`; `ResultId`; current `StopPointRef`, `StopName`, `LineName`, `LineRef`, `JourneyRef`, `DirectionRef`, `OperatorRef`, `PtMode`, `RailSubmode`, `TimetabledArrivalUtc`, `EstimatedArrivalUtc`, `PlannedBay`, `EstimatedBay`, `NotServicedStop`, `NoBoardingAtStop`, `NoAlightingAtStop` | Mapped to the stop-observation DataTable and the `stg.MddRealtimeStopObservation` TVP/table. The SQL definitions list the same columns at `sql/02-staging/05-create-mdd-realtime-staging-tables.sql:14-40` and `06-create-mdd-realtime-persistence-api.sql:62-85,146-214`. |
| ACTUAL + PARSED + PERSISTED | Nine identifiable `PtSituation` records and their `ParticipantRef`, `SituationNumber`, `Summary`, `Description`, `Detail`, `ValidFromUtc`, `ValidToUtc` | Mapped to the situation DataTable/TVP and `stg.MddRealtimeSituationObservation`; source path at parser `652-660`, DataTable `834-853`, SQL type/table `79-95` and `90-103`. |
| ACTUAL + PARSED + PERSISTED | Three populated service `SituationFullRef` relationships | Mapped to `SituationLinks` with `RelationScope = SERVICE`; the persistence procedure resolves links through source identities. This is persistence of association, not causality. |
| ACTUAL + PARSED + NOT_PERSISTED_AS_SOURCE_FIELD | `serviceDelivery.status`, parser container objects, and `ThisCall`/`serviceSection` containers | `status` gates parsing; containers supply child values. A successful collector-run status is not the same as persisting the source status field. |
| ACTUAL + NOT_PARSED | Previous/onward call records and their timing, stop sequence, stop names, bays, flags, and situation properties | Not placed in the Legacy stop TVP because the parser only materializes the current call. |
| ACTUAL + NOT_PARSED | `serviceDeparture`, origin/destination fields, `OperatingDayRef`, `deviation`, `unplannedStop`, `service.attribute`, mode name, route/via, priority/source/creation/unknown-reason, affected-journey detail | No Legacy snapshot DataTable or TVP column receives these fields. |
| NOT OBSERVED | `RecordedAtTime`, direct delay, cancellation/realtime status, explicit platform-change, vehicle identifiers beyond `JourneyRef` | No source value is available to parse or persist from this response; absence is not a general MDD capability claim. |

The current Legacy SQL persistence is implementation evidence, not the desired
future source-contract schema. In particular, downstream delay values are
derived from persisted timetable/estimate fields; no direct delay field was
received in this response.

### 17.7 Response Format Conclusion

| Contract element | Audited evidence |
|---|---|
| Request payload | XML / TRIAS 1.2 |
| Request method/content type | `POST`; `application/xml; charset=utf-8` |
| Actual successful response Content-Type | `application/json;charset=UTF-8` |
| Actual successful body format | JSON |
| Audited endpoint | `https://mdd.gorheinland.com/delfi` |
| Audited HTTP status | 200 |

Therefore the historical Legacy use of `ConvertFrom-Json` is
consistent with the actual MDD response observed for this audited `/delfi` request on
2026-09-25. This conclusion is limited to this request and does not claim that
all MDD endpoints return JSON.

### 17.8 Authorization Conclusion

The complete chronology is now:

1. Historical Legacy execution included HTTP 200 evidence.
2. The initial macOS probe used a credential value different from the
   Windows/Legacy value and received HTTP 401.
3. The later comparison established `MDD_API_KEY VALUE COMPARISON: SAME`.
4. Host DNS was then confirmed healthy as `HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS`.
5. The normalized host probe using the confirmed Legacy-equivalent credential
   returned HTTP 200 with one HTTP attempt and no retry.

**CURRENT MDD AUTHORIZATION: WORKING.** The evidence strongly supports that
the earlier 401 occurred while the macOS probe was using a different
credential value. It does not support a claim that provider-side entitlement
changed. Provider support is not currently required based on this evidence.

### 17.9 Stage 02A.2 Completion Decision

All required controlled-response evidence is available:

- raw artifact available and readable;
- JSON parse successful;
- actual response hierarchy and call cardinality audited;
- timing, identity, call, operational, platform/bay, and situation fields
  inventoried;
- Legacy parser comparison complete;
- Legacy source/schema persistence comparison complete; and
- no unresolved technical blocker remains for this controlled response.

**STAGE 02A.2: COMPLETED**

Parent Stage 02 remains **IN PROGRESS**. This completion does not approve
Stage 02, a future schema, a new polling schedule, or a general claim about
MDD field coverage.

### 17.10 Documentation and Safety Synchronization

The current-state summaries in the following files were synchronized with this
S13 result:

- `docs/PROJECT-STATUS.md`
- `docs/source-contracts/README.md`
- `README.md`

The current blocker language was superseded with the working authorization,
normal host DNS, successful HTTP 200, JSON response, and completed Stage
02A.2 findings. Historical S10/S11/S12 chronology remains labeled as
historical. `docs/DECISION-LOG.md` was not modified because S13 did not make a
new project-level decision. No historical prompt file was modified.

### 17.11 S13 Verification Record

| Verification | Result |
|---|---|
| Stage 0 | Approved |
| Stage 1 | Approved |
| Stage 02 | In progress |
| Stage 02A.1 | Completed |
| Stage 02A.2 | Completed |
| Current MDD authorization | Working |
| Request format | XML / TRIAS 1.2 |
| Successful response format | JSON |
| New MDD HTTP requests in this task | 0 |
| MDD probe executions in this task | 0 |
| SQL queries executed | 0 |
| Database changes | None |
| Legacy repository modified | No |
| Task Scheduler changes | None |
| Power BI changes | None |
| Raw response copied to repository | No |
| Raw response committed | No |
| Secret exposed or written | No |

### 17.12 Recommended Next Stage-02 Action

Use this successful response audit as the implementation evidence baseline, then
finish the remaining Stage 02 contract decisions from authoritative TRIAS/MDD
documentation: semantic validation of the observed estimate, operational-flag,
bay, and situation fields; representative-coverage limits; quota/retention
constraints; and the future field-retention matrix. Do not label estimated
timestamps as actual delay, infer cancellation from absent fields, infer
platform stability from absent estimates, or treat situation association as
causality. Keep Stage 02 in progress until those decisions are explicitly
reviewed and approved.

## 18. Current Stage 02 State

STAGE 02: IN PROGRESS
STAGE 02A.1: COMPLETED
STAGE 02A.2: COMPLETED
CURRENT MDD AUTHORIZATION: WORKING
MDD_API_KEY VALUE COMPARISON: SAME
HOST DNS RESULT: NORMAL_RESOLVER_WORKS
REQUEST FORMAT: XML
RESPONSE FORMAT: JSON
HTTP STATUS OF AUDITED RESPONSE: 200
NEW MDD HTTP REQUESTS IN THIS TASK: 0
MDD PROBE EXECUTIONS IN THIS TASK: 0
SQL QUERIES EXECUTED: 0
DATABASE CHANGES: NONE
LEGACY REPOSITORY MODIFIED: NO
TASK SCHEDULER CHANGES: NONE
POWER BI CHANGES: NONE
RAW RESPONSE COMMITTED: NO
SECRET EXPOSED OR WRITTEN: NO

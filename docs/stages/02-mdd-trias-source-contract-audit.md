# Stage 02A.1 — Legacy Implementation Audit

**Stage:** 02 — MDD/TRIAS Source-Contract Audit  
**Substage status:** COMPLETED  
**Audit date:** 2026-09-25  
**Scope:** Read-only inspection of the Legacy Collector source. This is not the complete MDD/TRIAS source-contract validation.

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
2. Actual MDD response behavior — not established without the controlled
   Stage 02A.2 probe.
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
- The response path is JSON parsing despite the XML request body. Live response
  media type and structure remain unproven.
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

## 9. Unknowns / Not Proven

The following remain open until Stage 02A.2:

- Actual MDD response media type and live JSON/XML structure.
- Whether MDD returns any candidate field marked `D` and with what coverage.
- Whether the live endpoint accepts the emitted namespace, timestamp, and
  request serialization for all relevant StopPointRefs.
- Actual response examples, field presence, nullability, multiplicity, and
  timestamp formats.
- Runtime behavior of authentication, retry responses, and `Retry-After`.
- Whether deployed Windows Collector files exactly match the inspected branch.
- Whether any external historical logs or copies contain raw payloads.
- Whether the deployed SQL Server objects match the checked-in definitions.

Stage 02A.1 made no MDD request, executed no SQL, changed no database or
Task Scheduler state, modified no Legacy file, and did not expose the API key.

## 10. Recommended Stage 02A.2 Probe Design

Stage 02A.2 is **not started** and was not performed as part of this audit.

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

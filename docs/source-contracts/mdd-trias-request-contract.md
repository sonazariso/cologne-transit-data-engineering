# MDD/TRIAS Request Contract

**Stage:** 02 — MDD/TRIAS Source-Contract Audit<br>
**Document state:** Approved Stage 02 source contract; Stage 7 pilot selection appended<br>
**Version:** TRIAS 1.2<br>
**Last updated:** 2026-09-29

This document defines the Stage 02 request contract for the audited MDD NRW
`/delfi` endpoint. It records the VDV 431-2 TRIAS 1.2 semantics supplied for
this audit, the request shape evidenced by the Legacy implementation and the
successful controlled request, and the limits that remain for later approval
stages.

It contains no API key, token, credential value, or other secret.

## Semantic authority and evidence boundary

The semantic authority for this contract is VDV — IP-KOM-ÖV / VDV 431-2,
“EKAP-Schnittstellenbeschreibung V1.2”, 02/2017. Current TRIAS 1.4 behavior
must not be silently substituted for the 1.2 contract.

The request details below combine three kinds of evidence:

1. **TRIAS 1.2 semantics** supplied from the VDV 431-2 reference.
2. **Legacy request construction** inspected in
   `collector/MddRealtimeCollector.psm1`.
3. **Controlled request metadata** from the successful Köln Hbf request
   audited in the Stage 02 document.

The controlled request proves the audited wire shape. It does not approve a
production polling schedule, retry policy, target panel, or quota budget.

## Endpoint and transport role

| Element | Contract |
|---|---|
| Endpoint role | MDD NRW / DELFI statewide passenger-information backend endpoint for stop-event information. |
| Audited endpoint | `https://mdd.gorheinland.com/delfi` (the endpoint is documented without secrets). |
| HTTP method | `POST`. |
| Request body | XML using the VDV TRIAS namespace and `version="1.2"`. |
| Request Content-Type | `application/xml; charset=utf-8`. |
| Authentication mechanism | `x-api-key` HTTP header. The credential is external configuration and must never be written into source-controlled documentation or raw artifacts. |
| Successful response boundary | The audited endpoint returned JSON with `application/json;charset=UTF-8`; this request contract does not assume that every MDD endpoint has the same response media type. |

## Request parameters

| Parameter / structure | TRIAS 1.2 role | Audited request / Legacy evidence | Stage 02 contract decision |
|---|---|---|---|
| `RequestTimestamp` | Timestamp in the `ServiceRequest` envelope identifying when the request was made. | Emitted as a SIRI-qualified UTC timestamp in `yyyy-MM-ddTHH:mm:ssZ` form, without fractional seconds. | Retain as request lineage. It is not a vehicle or stop actual-time field. |
| `RequestorRef` | Identifies the requesting client/application in the TRIAS request body. | `CologneTransitIntelligence`. | Treat as request identity, not as HTTP authentication or provider entitlement. |
| `Location` / `LocationRef` / `StopPointRef` | Identifies the stop point for which stop events are requested. | Controlled request: Köln Hbf, `de:05315:11201`. | Preserve the requested stop-point identity with the effective request parameters. |
| `DepArrTime` | Reference time for the requested departure/arrival event search. | Emitted by the Legacy builder; the controlled request set it to the same formatted UTC request time. | Preserve the effective value. Do not infer a production time-window or polling schedule from this one request. |
| `NumberOfResults` | Maximum number of departure/arrival events returned in the response. | Controlled request: `5`; Legacy builder default: `5`, subject to an explicit caller or automatic sampling target. | `NumberOfResults != HTTP request count.` It is a response-result limit, not a quota unit by itself. |
| `TimeWindow` | Time interval for returned stop events, calculated from the `LocationContext` time. | Not emitted in the audited Legacy request. | Record as a TRIAS 1.2 semantic option for future request design; do not invent a production value in Stage 02. |
| `StopEventType` | Selects departure events, arrival events, or both. | Controlled request: `arrival`. | Preserve the effective value because arrival/departure timing context is analytically material. |
| `IncludePreviousCalls` | Requests preceding calls in the returned service chain. | `true`; the controlled response contained `97` previous call records. | Preserve the requested flag and, when collected, the returned call scope. Requesting a field does not prove that the Legacy parser persists it. |
| `IncludeOnwardCalls` | Requests following calls in the returned service chain. | `true`; the controlled response contained `9` onward call records. | Preserve the requested flag and, when collected, the returned call scope. |
| `IncludeOperatingDays` | Request option for including operating-day information where supported by the TRIAS/MDD contract. | Not emitted by the audited Legacy builder; the controlled response nevertheless contained `OperatingDayRef` in service data. | Keep as an explicit contract candidate. Do not claim current Legacy support or silently add it to a production request in Stage 02. |
| `IncludeRealtimeData` | Controls whether realtime information is considered and returned. | `true`. | Preserve the effective flag. Presence of the flag does not guarantee that every optional realtime field is populated. |

The enclosing structure evidenced by the Legacy builder is:

```text
Trias (TRIAS 1.2)
└── ServiceRequest
    ├── RequestTimestamp
    ├── RequestorRef
    └── RequestPayload
        └── StopEventRequest
            ├── Location / LocationRef / StopPointRef
            ├── DepArrTime
            └── Params
                ├── NumberOfResults
                ├── StopEventType
                ├── IncludePreviousCalls
                ├── IncludeOnwardCalls
                └── IncludeRealtimeData
```

The current Legacy builder does not emit `TimeWindow` or
`IncludeOperatingDays`. Their presence in this contract documents the
TRIAS-facing interpretation and a future validation point; it is not a claim
that the current builder already sends them.

## Stage 8 pilot settings

Stage 7 selects the following bounded, non-production settings for the future
Stage 8 MDD pilot. These settings do not replace the Stage 02 audited request
evidence above and must not be promoted to production defaults.

| Pilot element | Stage 8 setting |
|---|---|
| Endpoint | `https://mdd.gorheinland.com/delfi` |
| Method / protocol | `POST` / TRIAS `1.2` |
| Content-Type | `application/xml; charset=utf-8` |
| Authentication | `x-api-key` header from `MDD_API_KEY` environment variable only |
| RequestorRef | `CologneTransitIntelligence` |
| StopPointRef | `de:05315:11201` (Köln Hbf) |
| `DepArrTime` | Fixed `PilotAnchorDepArrTime` selected at pilot start in the already-approved UTC TRIAS format and reused for every pilot request |
| `StopEventType` | `arrival` |
| `NumberOfResults` | `20` for the bounded pilot only |
| `IncludePreviousCalls` | `true` |
| `IncludeOnwardCalls` | `true` |
| `IncludeRealtimeData` | `true` |
| `TimeWindow` | Omitted; semantically available but not selected for this pilot |
| `IncludeOperatingDays` | Omitted; it was not part of the validated request path |
| Timeout | `30` seconds |
| Retries | `0` |

The API-key value must never be hardcoded, printed, logged, passed in command-
line arguments, or written into raw artifacts. The pilot uses a presence-only
preflight and prints only `MDD_API_KEY: PRESENT` or `MDD_API_KEY: MISSING`.

`NumberOfResults = 20` is justified by the bounded pilot's need for broader
per-request event/field coverage, the previously validated 1–100 builder
range, and existing lifecycle tooling that used 20. It remains distinct from
HTTP request count and is not a production setting. This correction keeps the
value at 20 because the objective is increased temporal observation density,
not larger result-set breadth. At pilot start, select
`PilotAnchorDepArrTime` as the UTC reference timestamp and reuse that exact
`DepArrTime` in every request; do not advance it with wall-clock time. A failed
request is not retried in the same slot.

The later 2026-09-28 Stage 8 attempt returned HTTP `401` with the observed
German provider response:

`Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`

It is preserved as current Stage 8 failure evidence rather than being
rewritten as the earlier missing-key preflight. The separate
[Stage 8 authorization/request-contract diagnostic](../../scripts/stage_08_authorization_request_contract_diagnostic.py)
must run the bounded A/B/C decision tree before this pilot setting is changed.
The available 2026-09-29 process reported `MDD_API_KEY: MISSING` before
Control A, so the documented Stage 8 setting remains 20 pending live
differential evidence. If and only if A=`200` and B=`401` with the sanitized
supplier-access message, Stage 8 may supersede 20 with 5 under a new versioned
sampling/request policy; Stage 13 still owns the final production result-limit
decision. The detector accepts normalized stable German supplier-access phrases
as well as the existing English wording and does not classify a generic `401`
as supplier access. The repository/Codex process did not inherit the key; the
user's separate local Terminal had previously reported `MDD_API_KEY: PRESENT`.
These are different process environments, not contradictory claims, and do not
establish whether the separate Terminal credential is correct or incorrect.

## Result count, HTTP requests, retries, and quota

These terms are separate:

- `NumberOfResults` is the maximum number of stop-event results requested in a
  single logical TRIAS response.
- One logical request normally produces one initial HTTP request.
- **`NumberOfResults != HTTP request count`.** Asking for five results does
  not consume five HTTP requests merely because the value is five.
- A retry is an additional HTTP request and must be counted separately for
  quota and audit purposes.

The known project/account evidence records a documented monthly ceiling of
`250,000` requests. The Legacy Collector snapshot at
`2026-09-25 11:23:41 UTC` records `1,599` collector executions, `1,629`
recorded HTTP attempts, and `35` additional retry attempts. The calculated
collector-recorded usage was approximately `0.6516%`, with approximately
`99.3484%` remaining against that documented ceiling.

This is recorded collector consumption only. It does not establish total
account consumption if other callers share the credential, and the current
public MDD dataset description is not treated as independent proof of the
`250,000` figure. Until provider/account documentation proves otherwise,
Stage 02 uses `250,000` as the project's conservative known request ceiling.

Stage 02 defines the accounting implication, not a production retry policy.
Stage 7 approves the bounded pilot timeout, zero-retry behavior, and active V3
policy `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3` in [the Stage 7 design](../stages/07-realtime-pilot-design.md):
fixed start-to-start 3-second slots for at most 60 minutes, at most 1200
scheduled logical slots and 1200 normal HTTP attempts, approximately 0.48% of
the conservative monthly ceiling and approximately 0.57–0.58% of the
operational planning envelope. The conservative ceiling remains `250,000`
requests/month; normal operational planning must use the `83–84%` envelope of
`207,500–210,000` requests/month and preserve `40,000–42,500` requests/month
(`16–17%`) as unallocated headroom. This remains a bounded diagnostic cadence,
not a production approval; Stage 13 owns production cadence/readiness analysis
and must size production inside the operational planning envelope.
Requests remain sequential and non-concurrent. An overlapping slot is skipped
as `SCHEDULED_SLOT_SKIPPED_OVERLAP`; it is not queued or caught up. Do not
wait three seconds after response completion, and do not shift the fixed slot
schedule. `NumberOfResults=20` remains a per-response result limit, not an
HTTP request count.
Stage 13 must still budget base requests, retries, concurrency/overlap, and
all measurable active collectors using the credential for production. No
production polling schedule is approved by this document.

## Permission boundary

The historical audited request returned HTTP 200. Current Stage 8
authentication/access is **not proven**: the later Stage 8 HTTP 401 carried the
sanitized supplier-access message described above, and the available
differential diagnostic process had no visible credential to run Control A.
MDD access is understood to depend on registration/activation, provider terms,
and the agreed use purpose. A public dataset description that advertises TRIAS
as an available NRW resource is not treated as an open-license or unrestricted
redistribution grant.

The request contract therefore permits documenting and validating the source
contract, but it does not authorize publication or redistribution of raw MDD
payloads. See the [raw-retention decision](mdd-trias-raw-retention.md).

## Stage 02 boundary

This is the technical Stage 02 request contract plus the clearly labelled
Stage 8 pilot selection. It does not:

- define a production retry or timeout policy;
- approve a production polling cadence or station panel;
- define SQL tables, columns, or database objects;
- establish provider-wide field coverage; or
- convert the Legacy request builder into the future implementation.

Stage 8 execution itself remains not started. The pilot settings authorize a
future design handoff, not a request run in Stage 7.

The detailed response evidence and the four-layer comparison are in the
[response contract](mdd-trias-response-contract.md) and the
[Stage 02 audit](../stages/02-mdd-trias-source-contract-audit.md).

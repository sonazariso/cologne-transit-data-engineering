# Stage 8 — Small Realtime Pilot Completion and Historical Authorization Chronology

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Current status:** `COMPLETE`
**Current status date:** `2026-09-29`
**Historical pre-success attempt:** `2026-09-28` — HTTP `401`

**Stage 9 status at this historical Stage 8 closeout:** `NOT STARTED / NEXT`

This document retains the earlier blocked/preflight attempts as chronology. The
current authoritative Stage 8 status is `COMPLETE`; the closeout evidence is
recorded below.

## Scope and implementation

The Stage 8-specific runtime is implemented at
[`scripts/stage_08_mdd_realtime_pilot.py`](../../scripts/stage_08_mdd_realtime_pilot.py).
The separate authorization/request-contract diagnostic is implemented at
[`scripts/stage_08_authorization_request_contract_diagnostic.py`](../../scripts/stage_08_authorization_request_contract_diagnostic.py).
It is constrained to the approved MDD/TRIAS Stage 8 contract and contains no
GTFS-RT, SQL, PostgreSQL, matching, operational-fact, Power BI, or later-stage
implementation. The same pilot path can receive an explicit bounded runtime
configuration when the differential diagnostic authorizes the Hbf/N5 or
Heumarkt/N5 validation branch; the approved Hbf/N20/V3 configuration remains
the default.

## Runtime correction status

The Stage 8 runtime now retains audited service-level origin and destination
identity/text fields with their exact source paths; compares `RecordedAtTime`
only within source-derived call-specific contexts; declines lifecycle claims
when call evidence is insufficient or ambiguous; skips fixed slots missed
during request/response processing, including persistence work; reconciles
parser bundles against records read back from the append-only JSONL artifacts;
persists populated response-level and result-level `ErrorMessage` evidence
with scope, source paths, result identity, source representation, and error
code where supplied; applies consistent pilot/response/observation/parser
lineage to diagnostic records; preserves structured Situation `Source` and
`SourceType` evidence; retains structured `SituationFullRef` values with
identity, scope, source path, and inline-resolution status; and carries
sanitized aggregated parser-gap findings into the final runtime summary.
It also carries one effective runtime configuration containing the station,
`StopPointRef`, `NumberOfResults`, and sampling/request policy through the
actual request, response snapshot, collection-slot, termination/failure, and
final-summary lineage. These are Stage 8 runtime corrections only; the
approved V3 defaults and the Stage 8 pilot completion requirements remain
unchanged unless the live diagnostic proves one of its explicit conditional
branches.

Cleanup now deletes the protected temporary directory after successful evidence
extraction and parser-to-persistence reconciliation. If an actual HTTP pilot
is blocked before that gate completes, the directory remains mode `700` with
mode `600` files for bounded diagnostic review, is reported as pending, and
must be removed no later than the approved 24-hour boundary. No permanent raw
archive is created.

## Historical 2026-09-28 preflight result

The earlier Stage 8 runtime preflight was invoked with the approved credential
source check. It printed:

```text
MDD_API_KEY: MISSING
```

Measured result:

- planned logical slots: `1200`;
- attempted HTTP requests: `0`;
- temporary raw/JSONL directory: not created;
- cleanup result: `NOT_CREATED`;
- parser-gap count: `0`;
- stop reason: `MDD_API_KEY_MISSING`.

No MDD response was available for parsing, persistence, reconciliation, field
coverage measurement, or `RecordedAtTime` analysis. The required bounded
marker is therefore recorded only as a preflight non-observation:

`RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`

This is not a provider-coverage conclusion. At the time of this preflight, the
one-hour pilot remained unexecuted. This historical result is superseded by
the successful pilot closeout below.

## Latest 2026-09-28 Stage 8 HTTP attempt

The later Stage 8 attempt must remain in the chronology rather than being
rewritten as the earlier missing-key preflight. It received HTTP `401`. The
observed provider response was:

`Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`

The response does not, by itself, prove whether the cause is the current
credential value, provider/data-supplier entitlement, or another authorization
condition.

This is preserved historical Stage 8 failure evidence, not a replacement for
the earlier successful historical MDD controls and not a conclusion that MDD
or the future 50-station plan is unusable.

## Authoritative successful Stage 8 execution — 2026-09-29

The chronology continued after the missing-key preflight and the earlier
invalid/local credential-configuration attempts. A macOS probe using a
different credential had returned HTTP 401; the corrected Legacy-equivalent
local credential configuration later returned HTTP 200 for the same Köln Hbf
controlled request. The separate 2026-09-28 Stage 8 attempt returned the
German supplier-access-looking HTTP 401 body recorded above.

Under the corrected local credential configuration, the differential diagnostic
ran exactly the required Hbf controls with one fixed diagnostic
`DepArrTime` anchor and independent per-request `RequestTimestamp` values:

| Control | Station / StopPointRef | NumberOfResults | Result |
|---|---|---:|---|
| A | Köln Hbf / `de:05315:11201` | `5` | HTTP `200`, parseable |
| B | Köln Hbf / `de:05315:11201` | `20` | HTTP `200`, parseable |

The diagnostic classification was
`CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING`. No request
parameter change was required, and the approved Hbf/N20/V3 contract remained
in force. The bounded evidence therefore supports only this conclusion: Köln
Hbf works with both result counts under the corrected local credential
configuration, so the earlier 401 does not demonstrate that `NumberOfResults=20`
or the current Hbf Stage 8 request contract requires additional supplier
authorization. No broader claim about every MDD data supplier is made.

The full pilot then used Köln Hbf, `de:05315:11201`, `NumberOfResults=20`,
and `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`. It completed with:

- actual elapsed time approximately `3606.801` seconds;
- `1200` planned logical slots;
- `343` attempted HTTP requests and `343` HTTP 200 responses;
- `343` successful parser/reconciliation responses and reconciliation rate
  `1.0` / `100%` over successful HTTP responses;
- `6860` `StopEventResult` occurrences;
- `0` parser failures, persistence failures, network/DNS failures, timeouts,
  and MDD application errors; and
- successful cleanup (`SUCCEEDED`).

The pilot attempted `343` requests because `857` fixed slots overlapped the
preceding sequential request/processing cycle and were skipped by design.
The scheduler does not queue or catch up those slots. These are not collector
failures; the observation is evidence for the later Stage 13
sequential-versus-bounded-concurrency/readiness decision, which is not made in
this document.

The bounded RecordedAtTime result is exactly
`RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`. It does not establish
provider-wide absence. Actual Physical Delay remains **NOT_APPROVED** and an
`EstimatedTime`-based value is not renamed as actual physical delay.

The sanitized live parser-gap evidence observed source fields that are not yet
mapped by the current Stage 8 persistence model: `affectedVehicleJourney`,
`datedVehicleJourneyRef`, `destinations`,
`direction`, `operatingDaysDescription`, `origins`, `situations`,
`vehicleJourneyRef`, and `vehicleJourneys`. These remain evidence for later
matching/data-quality review; they are not added to the production schema in
this task. Existing Situation parsing and persistence remains in force.

The successful controls also exposed two metadata corrections now applied in
the diagnostic: a parseable HTTP 200 control has
`existing_stage_8_failure_classification = null`, while genuine parser,
timeout, network/DNS, HTTP 401, and other failure cases retain their failure
classification. Normal Situation/passenger-information text remains in the
Situation evidence path and no longer populates
`sanitized_provider_error_text`; genuine failed-response evidence, including
the German HTTP 401 body, remains available for supplier-access detection.

Offline validation of these corrections made no MDD request.

## Historical S40 differential diagnostic and S41 handoff implementation

The diagnostic uses only the following decision-tree requests, in order:

1. Control A: Köln Hbf, `de:05315:11201`, `NumberOfResults=5`.
2. Control B only when A returns HTTP `200`: identical to A with
   `NumberOfResults=20`.
3. Control C only when A returns HTTP `401`: Köln Heumarkt,
   `de:05315:11110`, otherwise identical to A.

At the beginning of one diagnostic run, the runtime selects one fixed
`DiagnosticAnchorDepArrTime` and uses it for every control that executes. Each
HTTP request still receives its own actual `RequestTimestamp`; the control
evidence records both values explicitly. The later full pilot selects a new,
independent pilot-start `DepArrTime` anchor.

Each control records status, response content type, elapsed time, response
byte count, sanitized provider error text, and Stage 8 parser parseability in
protected temporary evidence outside Git. It uses zero retries, sequential
requests, disabled redirects, the existing 30-second timeout, and the shared
TRIAS 1.2 request builder. The credential value is never printed, logged,
hashed, fingerprinted, compared, or persisted.

The earlier repository/Codex execution process reported only
`MDD_API_KEY: MISSING`. Therefore Controls A, B, and C were not executed, no
diagnostic HTTP evidence was created, and the diagnostic result is
`DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING`. The user's separate local Terminal
had previously reported `MDD_API_KEY: PRESENT`; these are different process
environments, not contradictory claims, and no credential values were exposed
or compared. The repository process did not inherit the credential, so the
live A/B/C diagnostic was not executed; this does not establish whether the
credential present in the separate Terminal is correct or incorrect.

The S41 implementation now automatically invokes the same `run_pilot` path
after a permitted live decision. A=`200`, B=`200` selects Köln Hbf,
`de:05315:11201`, N20, and the existing V3 policy. A=`200`, B=`401` with a
supplier-access message selects Hbf/N5 under
`MDD_STAGE_8_KOELN_HBF_3S_60MIN_V4_N5_DIAGNOSTIC`. A=`401`, C=`200` selects
Heumarkt, `de:05315:11110`, N5, under
`MDD_STAGE_8_KOELN_HEUMARKT_3S_60MIN_V1_N5_DIAGNOSTIC`. A=`401`, C=`401`
does not start the pilot. The actual pilot creates its own fixed start-time
`DepArrTime` anchor; diagnostic control timestamps are never reused.

The historical conditional decision remains explicit: only A=`200`, B=`401` with the
sanitized supplier-access message can supersede `NumberOfResults=20` with `5`
for Stage 8, under a new versioned Stage 8 sampling/request policy. A=`401`,
C=`200` would instead move the bounded pilot target to Heumarkt and use `5`;
A=`401`, C=`401` stops before the pilot. Previous/This/Onward call parsing,
realtime data, persistence, reconciliation, cadence, quota, and retention
requirements are unchanged in every branch. Stage 13 still owns the final
production `NumberOfResults` decision.

The diagnostic summary exposes `stage_8_pilot_started`,
`stage_8_pilot_status`, and `stage_8_complete`. Process exit status is zero only
when the permitted full pilot returns `stage_8_status=COMPLETE`; permission to
start the pilot alone is not completion. Diagnostic evidence is retained under
the existing maximum-24-hour boundary for blocked or failed runs. After a
successful pilot has completed its own extraction, reconciliation, and cleanup,
the sanitized diagnostic summary is assembled and the separate diagnostic
temporary directory is cleaned as well.

## Closeout boundary

The required closeout gate is now satisfied: the authorized host run completed
the differential diagnostic, applied the evidence-supported unchanged Hbf/N20
contract decision, completed the bounded pilot, reconciled successful
responses, produced the required measurements, and cleaned up protected
temporary artifacts. Stage 8 is therefore **COMPLETE**. The fixed 50-station
objective, 250,000/month quota, and all other approved boundaries remain
unchanged. At this historical Stage 8 closeout point, Stage 9 and all later
stages were not started or modified; the current Stage 9 status is maintained
in the [Stage 9 record](09-matching-data-quality.md).

## Prospective post-Stage-8 hardening

The following hardening applies only to future offline/host executions. It does
not reopen Stage 8, rerun the one-hour pilot, rewrite the historical result,
or recover the raw payloads deleted during the successful 2026-09-29 cleanup.

The shared Stage 8 credential boundary now validates the API-key value before
request construction, the network transport, or temporary-directory creation.
An empty value retains `MDD_API_KEY: MISSING`. A value containing unsafe
header characters, including Unicode U+2028, newline, or carriage-return
contamination, prints only `MDD_API_KEY: INVALID_FORMAT` and is classified as
`CREDENTIAL_FORMAT_INVALID`. No credential value, length, position, hash, or
derived secret is exposed. The authorization diagnostic and the pilot use this
same boundary.

For future successful responses, a non-null source-provided `RecordedAtTime`
is parsed and persisted with its existing source/event/response/call-scope,
arrival/departure, `ObservedAt`, and first-seen lineage. That response is
marked `RECORDED_AT_TIME_RAW_EVIDENCE`; only its corresponding raw payload is
kept in the protected mode-700/mode-600 temporary area. Ordinary successful
response artifacts are cleaned normally. The non-error cleanup status
`RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS` reports intentional bounded
retention and supplies a maximum deletion deadline of 24 hours. Full raw
payloads remain temporary and are never the durable warehouse source; the
sanitized parsed `RecordedAtTime` evidence and lineage are the durable record.

Future runs also retain sanitized aggregate evidence for the observed sample:
successful responses and call/timing contexts inspected, non-null occurrences,
distinct events, call-scope distribution, and arrival/departure distribution.
Zero occurrences remain a bounded sample result and are not provider-wide
absence evidence. Failed/blocked diagnostic evidence continues to use the
existing bounded failure-retention behavior.

Offline hardening validation uses synthetic/local responses only and confirms
invalid-credential zero-request behavior, normal cleanup without
`RecordedAtTime`, positive raw-evidence retention, exact parsed value and
lineage preservation, later-NULL/disappearance safety, secret-free artifacts,
and the 24-hour maximum. The historical Stage 8 status and metrics above
remain authoritative. The future 50-station objective, 250,000/month quota,
and Stage 13 ownership of production cadence, concurrency, `NumberOfResults`,
and rollout decisions are unchanged; Stage 9+ implementation was not started.

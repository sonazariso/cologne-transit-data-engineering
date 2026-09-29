# Stage 8 — Small Realtime Pilot Authorization Diagnostic Blocker

**Status:** `BLOCKED — DIFFERENTIAL AUTHORIZATION DIAGNOSTIC NOT COMPLETED`
**Date:** `2026-09-29`
**Latest Stage 8 attempt:** `2026-09-28` — HTTP `401`

**Stage 9 status:** `NOT STARTED / NEXT`

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

This is not a provider-coverage conclusion. The actual one-hour pilot remains
unexecuted and Stage 8 is not complete.

## Latest 2026-09-28 Stage 8 HTTP attempt

The later Stage 8 attempt must remain in the chronology rather than being
rewritten as the earlier missing-key preflight. It received HTTP `401`. The
observed provider response was:

`Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`

The response does not, by itself, prove whether the cause is the current
credential value, provider/data-supplier entitlement, or another authorization
condition.

This is current Stage 8 failure evidence, not a replacement for the earlier
successful historical MDD controls and not a conclusion that MDD or the
future 50-station plan is unusable.

## S40 differential diagnostic and S41 handoff implementation

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

The repository/Codex execution process available for this update reported only
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

The conditional decision remains explicit: only A=`200`, B=`401` with the
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

Stage 8 must not be marked `COMPLETE` until the authorized host run completes
the required differential diagnostic, applies only an evidence-supported
versioned Stage 8 contract decision, executes the resulting bounded pilot,
reconciles successful responses, produces the required measurements, and
cleans up protected temporary artifacts. The fixed 50-station objective,
250,000/month quota, and all other approved boundaries remain unchanged.
Stage 9 and all later stages were not started or modified.

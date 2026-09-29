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
It is constrained to the approved MDD/TRIAS Köln Hbf contract and contains no
GTFS-RT, SQL, PostgreSQL, matching, operational-fact, Power BI, or later-stage
implementation.

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
These are Stage 8 runtime corrections only; the approved pilot policy and the
Stage 8 pilot completion requirements remain unchanged.

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
sanitized provider message stated that access to this data provider was not
enabled. The response does not, by itself, prove whether the cause is the
current credential value, provider/data-supplier entitlement, or another
authorization condition.

This is current Stage 8 failure evidence, not a replacement for the earlier
successful historical MDD controls and not a conclusion that MDD or the
future 50-station plan is unusable.

## S40 differential diagnostic implementation and available execution

The diagnostic uses only the following decision-tree requests, in order:

1. Control A: Köln Hbf, `de:05315:11201`, current request timestamp and
   `DepArrTime`, `NumberOfResults=5`.
2. Control B only when A returns HTTP `200`: identical to A with
   `NumberOfResults=20`.
3. Control C only when A returns HTTP `401`: Köln Heumarkt,
   `de:05315:11110`, otherwise identical to A.

Each control records status, response content type, elapsed time, response
byte count, sanitized provider error text, and Stage 8 parser parseability in
protected temporary evidence outside Git. It uses zero retries, sequential
requests, disabled redirects, the existing 30-second timeout, and the shared
TRIAS 1.2 request builder. The credential value is never printed, logged,
hashed, fingerprinted, compared, or persisted.

The repository-process execution available for this update reported only
`MDD_API_KEY: MISSING`. Therefore Controls A, B, and C were not executed, no
diagnostic HTTP evidence was created, and the diagnostic result is
`DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING`. No evidence-supported Stage 8 request
parameter change is authorized by that local preflight. The known working
Legacy-equivalent credential must be restored/reloaded locally before the
decision tree can run.

The conditional decision remains explicit: only A=`200`, B=`401` with the
sanitized supplier-access message can supersede `NumberOfResults=20` with `5`
for Stage 8, under a new versioned Stage 8 sampling/request policy. A=`401`,
C=`200` would instead move the bounded pilot target to Heumarkt and use `5`;
A=`401`, C=`401` stops before the pilot. Previous/This/Onward call parsing,
realtime data, persistence, reconciliation, cadence, quota, and retention
requirements are unchanged in every branch. Stage 13 still owns the final
production `NumberOfResults` decision.

## Closeout boundary

Stage 8 must not be marked `COMPLETE` until the authorized host run completes
the required differential diagnostic, applies only an evidence-supported
versioned Stage 8 contract decision, executes the resulting bounded pilot,
reconciles successful responses, produces the required measurements, and
cleans up protected temporary artifacts. The fixed 50-station objective,
250,000/month quota, and all other approved boundaries remain unchanged.
Stage 9 and all later stages were not started or modified.

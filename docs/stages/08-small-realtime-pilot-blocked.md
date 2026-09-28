# Stage 8 — Small Realtime Pilot Preflight Blocker

**Status:** `BLOCKED — MDD_API_KEY MISSING AT PREFLIGHT`  
**Date:** `2026-09-28`  
**Preflight timestamp:** `2026-09-28T19:42:51Z`

**Stage 9 status:** `NOT STARTED / NEXT`

## Scope and implementation

The Stage 8-specific runtime is implemented at
[`scripts/stage_08_mdd_realtime_pilot.py`](../../scripts/stage_08_mdd_realtime_pilot.py).
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
Stage 8 blocker remain unchanged.

Cleanup now deletes the protected temporary directory after successful evidence
extraction and parser-to-persistence reconciliation. If an actual HTTP pilot
is blocked before that gate completes, the directory remains mode `700` with
mode `600` files for bounded diagnostic review, is reported as pending, and
must be removed no later than the approved 24-hour boundary. No permanent raw
archive is created.

## Safe preflight result

The runtime was invoked with the approved credential source check. It printed:

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

## Closeout boundary

Stage 8 must not be marked `COMPLETE` until an authorized host run supplies
`MDD_API_KEY`, executes the fixed V3 pilot, reconciles successful responses,
produces the required measurements, and cleans up protected temporary
artifacts. Stage 9 and all later stages were not started or modified.

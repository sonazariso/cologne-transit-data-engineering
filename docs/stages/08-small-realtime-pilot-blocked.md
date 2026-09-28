# Stage 8 — Small Realtime Pilot Preflight Blocker

**Status:** `BLOCKED — MDD_API_KEY MISSING AT PREFLIGHT`  
**Date:** `2026-09-28`  
**Preflight timestamp:** `2026-09-28T18:50:36Z`

**Stage 9 status:** `NOT STARTED / NEXT`

## Scope and implementation

The Stage 8-specific runtime is implemented at
[`scripts/stage_08_mdd_realtime_pilot.py`](../../scripts/stage_08_mdd_realtime_pilot.py).
It is constrained to the approved MDD/TRIAS Köln Hbf contract and contains no
GTFS-RT, SQL, PostgreSQL, matching, operational-fact, Power BI, or later-stage
implementation.

## Runtime correction status

The Stage 8 runtime now retains audited service-level origin and destination
identity/text fields with their exact source paths; tracks later timing-context
observations when `RecordedAtTime` is absent; skips fixed slots missed during
request/response processing, including persistence work; reconciles parser
bundles against records read back from the append-only JSONL artifacts; applies
consistent pilot/response/observation/parser lineage to diagnostic records; and
preserves structured `SituationFullRef` values with identity, scope, source
path, and inline-resolution status. These are Stage 8 runtime corrections only;
the approved pilot policy and the Stage 8 blocker remain unchanged.

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

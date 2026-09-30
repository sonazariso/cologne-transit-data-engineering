# Stage 13 — Tiered Collector Readiness Completion Decision

**Date:** `2026-09-30`  
**Final status:** `COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED`
**Production status:** `NOT STARTED`

## Decision

All Stage 13 readiness design gates are closed using the exact approved Stage
12 panel and sanitized bounded evidence. The machine-readable artifact at
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json)
is authoritative.

| Gate | Result | Evidence / decision |
|---|---|---|
| Stage 12 fixed panel | **PASS** | `TIERED50_PANEL_V1`; exactly 50 fixed parent stations; all Legacy7 anchors preserved |
| Exact static feed | **PASS** | `VERSION__20260930_0053`; ZIP SHA-256 `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147` |
| Station/tier density | **PASS** | 5,100 station/service-day rows; 5/10/15/30-minute forward-window evidence; deterministic density tiers |
| Production tier assignment | **PASS** | A=10, B=20, C=20; density metrics only; no Stage 12 rank reuse |
| Cadence | **PASS — DESIGN APPROVAL** | A/5 min, B/15 min, C/30 min; 240 logical requests/hour |
| Quota planning | **PASS — CONSERVATIVE** | 30-day nominal total 181,540; 31-day nominal total 187,588; 10% stress total 205,444 after Legacy7 and diagnostic reserves |
| Current shared credential usage | **LIMITATION, NOT FALSE ZERO** | current Legacy audit unavailable; full documented Legacy7 reserve of 8,640/8,928 is applied |
| Production `NumberOfResults` | **PASS — DESIGN APPROVAL** | fixed `30` selected from bounded N5/N10/N20/N30 coverage evidence |
| Provider maximum | **LIMITATION** | `PROVIDER_MAXIMUM_NOT_ESTABLISHED`; not required because N=30 has direct bounded support |
| Sequential duration evidence | **PASS — EVIDENCE RECORDED** | six representatives × four result limits, 24 sequential attempts in the corrected matrix |
| Bounded concurrency | **PASS — DESIGN APPROVAL** | three cap-2 pairs; six valid HTTP 200/parseable responses; no 429/5xx/timeout/transport pressure |
| Heartbeat feasibility | **PASS** | due sequence `21/21/19/20/20/19`; max 21; cap-2 P95 estimate 10,544.501 ms |
| Phase assignment | **PASS — DESIGN CONTRACT** | stable identity-hash assignment; exact 50 assignments recorded; no production rows created |
| Claim/overlap contract | **PASS — DESIGN CONTRACT** | unique `(SamplingTargetId, LogicalDueBucket)` atomic claim; duplicate invocation does not issue HTTP |
| Failure isolation | **PASS — DESIGN REQUIREMENT** | independent per-target status, attempts, duration, parser, persistence, and error metadata |
| Historical continuity | **PASS** | Legacy7 history and target identities preserved; no cutover or replay |

## Approved design

The production-readiness design approved for a later controlled implementation
is:

- `TIERED50_PANEL_V1`, exactly 50 approved stations;
- Tier A: 10 density-ranked stations every 5 minutes;
- Tier B: 20 density-ranked stations every 15 minutes;
- Tier C: 20 density-ranked stations every 30 minutes;
- `NumberOfResults = 30`;
- `MaxAttempts = 1`, zero automatic retries;
- `MaximumConcurrency = 2`, limited to the evidence-supported bounded design;
- six-heartbeat phase super-cycle with due counts `21/21/19/20/20/19`; and
- atomic execution identity `(SamplingTargetId, LogicalDueBucket)` with no
  catch-up replay.

The provider maximum remains unknown by design. The selected result limit is a
safe bounded production value for the selected cadence evidence, not a claim
about the provider's theoretical limit or guaranteed realtime completeness.

## Credential and quota boundary

The repository inventory found only inactive bounded diagnostics plus the
external Legacy Collector / `ctl.MddCollectorRun` consumer. Current shared
credential usage was unavailable through an established read-only audit path.
The design therefore reserves the complete documented Legacy7 monthly
baseline rather than fabricating zero current usage. The Stage 13 diagnostic
hard ceiling is 100 attempts; 90 total Stage 13 attempts were consumed and
only sanitized evidence was retained.

The selected normal and stress plans remain under the 210,000 upper edge of the
project's normal planning envelope even with the conservative Legacy7 reserve
and the 10% stress sensitivity. Current shared usage must still be audited
before any production activation.

## Boundary confirmation

- Stage 14 remains **NOT STARTED**.
- Tiered50 was not activated.
- No 7→50 cutover was performed.
- No Windows Scheduled Task was created.
- No production database object, scheduler, or target row was created.
- Power BI was not modified.
- Legacy7 history and the seven existing target identities were not deleted or
  reinserted.

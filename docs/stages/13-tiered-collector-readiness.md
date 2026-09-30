# Stage 13 — Tiered Collector Readiness

**Date:** `2026-09-30`
**Status:** `COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED`
**Stage 14:** `NOT STARTED`
**Production activation:** `NOT STARTED`

This is the final Stage 13 design decision approved explicitly in the current
conversation and recorded in `prompts/S68.md`. The authoritative artifact is
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json).
This closeout made no MDD request, DNS/live-network operation, database change,
Windows Scheduled Task, Power BI change, target-row creation, scheduler
implementation, or Tiered50 activation.

D-046 remains historical evidence and is superseded. D-047/D-048 preserve the
reopened gate and corrected diagnostic chronology. The final user-approved
decision is recorded as the next decision-log entry without rewriting that
history.

## Final approved design

| Item | Approved value |
|---|---|
| Panel | `TIERED50_PANEL_V1`; exactly 50 stations; Stage 12 membership unchanged |
| Tier membership | Density ranks 1–8 / 9–28 / 29–50 for A / B / C |
| Cadence | A/B/C every 5 / 15 / 30 minutes |
| Logical load | 110 requests per 30-minute super-cycle; 220 requests/hour |
| NumberOfResults | Tier A `20`; Tier B `30`; Tier C `30` |
| MaximumConcurrency | `2` bounded workers |
| Retry policy | `MaxAttempts = 2`: one initial attempt plus at most one approved transient retry |
| Automatic-retry protection | Block automatic retries at projected/current monthly consumption above `207,500` |

All final station IDs, density ranks, tier assignments, phase assignments, and
calculated support values are persisted in the machine-readable evidence. The
runtime assignment basis is approved density evidence plus immutable station
identity; mutable Stage 12 `SelectionRank` is not a runtime input.

## Stage 12 panel and static source boundary

The fixed panel remains `TIERED50_PANEL_V1` with exactly 50 parent stations and
all seven Legacy7 continuity anchors. Stage 12 evidence was not modified.
The retained exact-feed source is:

| Field | Value |
|---|---|
| Feed version | `VERSION__20260930_0053` |
| ZIP SHA-256 | `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147` |
| Feed period | `2026-09-02` through `2026-12-12` |
| Analysis | GTFS calendar/calendar-date expansion and scheduled child-stop arrivals |
| Stage 13 source used for final recalculation | retained Stage 13 station-level evidence; offline, no ZIP/network reopen |

The final deterministic density membership is:

| Tier | Density ranks | Count | Cadence |
|---|---:|---:|---:|
| A | 1–8 | 8 | 5 minutes |
| B | 9–28 | 20 | 15 minutes |
| C | 29–50 | 22 | 30 minutes |

The evidence retains the explicit 50-station membership and the density sort
keys: 30-minute station-day rolling maximum P95 descending, then 15-minute
P95, then 5-minute P95, then immutable source-parent ID ascending. Static
scheduled density is planning evidence only; it is not realtime response
cardinality or passenger demand.

## Exact static support for the approved result limits

The generator recalculates these values from retained station-level exact-feed
metrics. No expected number was inserted only as documentation text.

| Tier | Cadence window | Stations | Max station P95 scheduled arrivals | Max observed static scheduled-arrival count | Approved NumberOfResults | P95 support |
|---|---:|---:|---:|---:|---:|---:|
| A | 5 minutes | 8 | 19 | 20 | 20 | 8/8 |
| B | 15 minutes | 20 | 23 | 23 | 30 | 20/20 |
| C | 30 minutes | 22 | 29 | 29 | 30 | 22/22 |
| **Total** | — | **50** | — | — | — | **50/50** |

Historical valid diagnostics established only that N20 and N30 were accepted
and produced valid HTTP/TRIAS responses in a bounded representative sample.
They did not establish a provider-wide maximum, guaranteed realtime
completeness, guaranteed 20/30-event availability, or complete realtime
forward coverage. The later corrected forward-coverage diagnostic is retained
as `CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT`.

The approved A20/B30/C30 policy is an explicit user-approved production-design
choice supported by exact-feed static density, bounded historical N20/N30
acceptance, lower unnecessary response volume for Tier A, and a simple
two-value configuration. It is not a provider-maximum claim.

## Quota planning and retry reserve

The absolute provider ceiling remains `250,000` requests/month. The normal
planning envelope remains `207,500–210,000`; operation is not intentionally
planned against the full ceiling. The generator calculates a normal planning
retry reserve of 10% of nominal Tiered50 logical requests:

| Month | Nominal Tiered50 | 10% retry reserve | Tiered50 with retry reserve | Legacy7 reserve | Diagnostic reserve | Conservative planning total |
|---|---:|---:|---:|---:|---:|---:|
| 30 days | 158,400 | 15,840 | 174,240 | 8,640 | 100 | **182,980** |
| 31 days | 163,680 | 16,368 | 180,048 | 8,928 | 100 | **189,076** |

`MaxAttempts=2` does not authorize unlimited monthly retries. Stage 14 must
enforce the automatic-retry guardrail so current/projected monthly consumption
does not cross `207,500`; a retry beyond that boundary is blocked. The
Legacy7 reserve remains conservative because current shared-account usage is not
available through an established read-only audit. The 100 diagnostic reserve is
historical reconciliation, not a recurring production workload.

## Phase staggering and heartbeat feasibility

The persisted six-heartbeat, 30-minute phase arrangement is:

| Heartbeat | Tier A | Tier B | Tier C | Total due |
|---:|---:|---:|---:|---:|
| 0 | 8 | 7 | 4 | **19** |
| 1 | 8 | 7 | 3 | **18** |
| 2 | 8 | 6 | 4 | **18** |
| 3 | 8 | 7 | 3 | **18** |
| 4 | 8 | 7 | 4 | **19** |
| 5 | 8 | 6 | 4 | **18** |

The phase capacities are A `8`; B `7/7/6`; and C `4/3/4/3/4/4`.
This is the minimum possible maximum load for 110 requests distributed over
six heartbeats, with two heartbeats at 19 and four at 18. Assignments are
sorted by `SHA-256(panel version + tier + immutable SamplingTargetId)` and the
persisted capacities are filled in order. Stage 14 may materialize these
assignments; Stage 13 created no target rows.

The final maximum due load is 19. With approved cap-2 and retained bounded
timing evidence, the calculated P95 heartbeat execution estimate is
`9,585.91 ms` and the observed-max estimate is `9,771.31 ms`, within the
five-minute heartbeat. This is bounded HTTP timing evidence, not full
production SQL-persistence timing; Stage 14 must validate the complete path.

## Concurrency, retry, claims, and continuity

`MaximumConcurrency = 2` is approved as a bounded design value. Sequential
execution is technically feasible, cap-2 was successful in the retained
representative sample, and cap-2 provides heartbeat margin. No 429, timeout,
5xx, transport-pressure, or parse failure was observed in that sample. This is
not a provider-wide rate-limit claim. Stage 14 must validate cap-2 with actual
persistence/database behavior and stop activation on provider pressure, SQL
contention, persistence instability, overlap problems, or any other material
failure. A value above 2 requires new evidence and a new decision.

The approved retry contract permits one automatic retry only for approved
transient conditions: HTTP 408, 429, 500, 502, 503, 504, and approved
transient transport/network failures. HTTP 429 must respect `Retry-After` where
applicable. HTTP 401/403, deterministic request-contract errors, deterministic
parser/schema errors, and deterministic TRIAS application errors are not
retryable. One failed station must not abort other due targets.

The execution identity remains exactly:

`(SamplingTargetId, LogicalDueBucket)`

Claims must be atomic and durable in Stage 14. A duplicate invocation issues no
HTTP request; stale/incomplete runs remain observable; missed historical
buckets are not replayed; and no fabricated historical realtime snapshot is
created. Legacy7 history and all seven existing target identities remain
unchanged. Future additions are new approved targets only, with explicit
Legacy7-versus-Tiered50 era distinction and no naive raw-volume comparison
across cadence eras.

## Retained diagnostic accounting and limitations

The valid historical sequential population reconciles to 24 client-side
invocations, 24 HTTP responses, and 24 confirmed provider HTTP attempts. The
retained cap-2 population reconciles to 6/6/6. The corrected failed diagnostic
reconciles to 24 client-side invocations, 0 HTTP responses, 0 confirmed
provider HTTP attempts, unknown provider quota consumption, and 24 separate
conservative quota-accounting attempts. Cumulative client invocations are 114
(90 historical plus 24 explicitly authorized); this is not a claim that 114
provider requests or quota units were consumed.

The historical initial diagnostic budget remains 100, the explicit additional
authorization is 24 client invocations, and the authorized cumulative client
invocation ceiling is 114. The corrected run remains
`INVALID_FOR_NUMBER_OF_RESULTS_DECISION`. No raw response, request header, API
key, or secret-bearing error text was persisted.

## Closeout boundaries

- Stage 13 is complete as an approved readiness design.
- Stage 14 remains `NOT STARTED`.
- Tiered50 is not live and no cutover was performed.
- No production database object, target row, scheduler, Windows Scheduled Task,
  or Power BI artifact was created or changed.
- Legacy7 data and existing sampling-target identities were not changed.
- The final artifact was generated and validated offline; this closeout issued
  zero MDD requests and required no DNS/live-network operation.

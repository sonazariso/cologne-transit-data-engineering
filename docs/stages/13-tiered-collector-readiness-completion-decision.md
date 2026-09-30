# Stage 13 — Tiered Collector Readiness Completion Decision

**Date:** `2026-09-30`  
**Final status:** `CONDITIONALLY BLOCKED — PROVIDER_MAXIMUM_NOT_ESTABLISHED; STATIC_EVENT_DENSITY_NOT_ESTABLISHED; CURRENT_PRODUCTION_MDD_AUDIT_UNAVAILABLE; CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED`  
**Production status:** `NOT STARTED`

## Decision

Stage 13 produced an evidence-based readiness audit, but it cannot be marked
`COMPLETE — APPROVED READINESS DESIGN`. The remaining conditions affect the
definition and safety of the production design itself:

| Gate | Result | Evidence / blocker |
| --- | --- | --- |
| Stage 12 fixed panel | **PASS — HANDOFF COMPLETE** | `TIERED50_PANEL_V1`; 50 fixed parent stations; no production target activation |
| Candidate 10/20/20 arithmetic | **CALCULATED, NOT APPROVED** | 280/hour; 201,600 per 30-day month; 208,320 per 31-day month |
| Monthly quota planning | **CONDITIONAL** | 250,000 ceiling; 207,500–210,000 normal envelope; current shared-caller production usage unavailable |
| Production `NumberOfResults` | **BLOCKED** | Bounded N5 and N20 populations saturate; `PROVIDER_MAXIMUM_NOT_ESTABLISHED` |
| Static station/tier density | **BLOCKED** | Stage 12 provides panel evidence; this Stage 13 audit does not assign production tiers or reconcile forward-horizon coverage |
| Execution duration | **EVIDENCE RECORDED** | Separate mode populations with P50/P90/P95/P99/max; Stage 8 run-level skips retained |
| Retry / HTTP attempts | **CONDITIONAL** | Bounded source-controlled populations had zero retries; legacy 35 additional attempts are historical only |
| Phase staggering | **DESIGN PASS** | 24/23/23/24/23/23; 140 per 30 minutes; 280/hour |
| Concurrency | **CONDITIONALLY BLOCKED** | Small cap-2 direction only; provider-safe concurrent behavior unmeasured |
| Claim/overlap protection | **DESIGN PASS** | Unique `(SamplingTargetId, LogicalDueBucket)` atomic claim contract |
| Failure isolation | **DESIGN PASS** | Per-target audit/status/attempt/duration/parser/persistence/error fields |
| Historical continuity | **DESIGN PASS** | Legacy7 preserved; explicit Legacy7/Tiered50 era boundary required |

The full details and machine-readable values are in
[`13-tiered-collector-readiness.md`](13-tiered-collector-readiness.md) and
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json).

## Production decision

The `10/20/20` cadence is retained as a **provisional analysis candidate**.
Its base arithmetic is not an approval for scheduled collection. The 31-day
base consumes `208,320` logical requests, or `83.328%` of the full ceiling and
`99.2%` of the upper normal planning envelope, before retries and other
credential consumers.

No production `NumberOfResults` is selected. The bounded evidence establishes
only that Köln Hbf controls work at N5 and N20, and that the sampled N5/N20
responses reach their configured limits. It does not establish a provider
maximum or forward-horizon coverage. The required status is
`PROVIDER_MAXIMUM_NOT_ESTABLISHED`.

The future dispatcher contract is approved as a design requirement: derive a
UTC five-minute logical bucket, atomically claim the unique target/bucket
identity, retain stale/incomplete-run observability, execute only the winning
claim, and never replay a missed historical bucket. One failed target must not
abort the remaining due targets. No persistence object or scheduler is created
by this decision.

The concurrency direction is conditional bounded concurrency with a maximum of
two workers, subject to a separately approved provider-safe validation. It is
not activated or approved for production. Fully sequential execution is not
accepted as a production guarantee because the Stage 8 run-level evidence
showed substantial overlap skips and only limited margin for 23–24 due targets
within five minutes.

## Required next evidence before readiness approval

1. Re-run station-level static event-density and forward-window coverage
   analysis against the approved Stage 4 snapshot or a separately approved
   current snapshot.
2. Establish provider/result-limit behavior beyond N20 and select a production
   `NumberOfResults` only from observed coverage evidence.
3. Audit current shared-credential production usage, including any external
   Legacy Collector, and reconcile it with the 30/31-day budget.
4. Validate the bounded concurrency cap, timeout, retry, 429/5xx behavior,
   failure isolation, and quota impact before activation.

## Boundary confirmation

- Stage 14 was not started.
- The 50-station collector was not activated.
- No production scheduler, database object, SQL migration, data load, or
  Power BI artifact was created.
- Legacy realtime history and the seven existing Legacy7 target identities are
  preserved by requirement; no cutover was performed.

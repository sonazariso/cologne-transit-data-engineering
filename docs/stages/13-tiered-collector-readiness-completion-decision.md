# Stage 13 — Tiered Collector Readiness Completion Decision

**Date:** 2026-09-30
**Current status:** COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED
**Stage 14:** NOT STARTED
**Production activation:** NOT STARTED

## Final decision

The explicit approval recorded in prompts/S68.md finalizes Stage 13 as an
approved readiness design. The machine-readable
[13-tiered-collector-readiness-evidence.json](13-tiered-collector-readiness-evidence.json)
is authoritative for the retained panel, density ranks, phase assignments,
static support, quota arithmetic, retry contract, limitations, and gate
status. Stage 12 evidence and the fixed panel were not changed.

D-046 remains historical evidence. D-047 and D-048 preserve the reopened gate
and corrected diagnostic chronology. D-049 records this final explicit
approval without rewriting those earlier entries.

| Gate or decision | Final result |
|---|---|
| Fixed panel | PASS — TIERED50_PANEL_V1, exactly 50 stations, all seven Legacy7 continuity anchors preserved |
| Density tier membership | APPROVED — ranks 1–8 / 9–28 / 29–50 for A / B / C; counts 8 / 20 / 22 |
| Cadence | APPROVED — A/B/C every 5 / 15 / 30 minutes; 110 requests per 30-minute super-cycle and 220 per hour |
| NumberOfResults | APPROVED DESIGN — Tier A 20, Tier B 30, Tier C 30 |
| Static support | PASS — A max station P95 19 and max observed 20; B 23/23; C 29/29; 50/50 total P95 support |
| Provider maximum | LIMITATION RETAINED — PROVIDER_MAXIMUM_NOT_ESTABLISHED |
| Corrected live coverage | LIMITATION RETAINED — CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT |
| Concurrency | APPROVED DESIGN — maximum concurrency 2; Stage 14 must validate the full persistence path |
| Retry policy | APPROVED DESIGN — MaxAttempts=2, one automatic retry only for approved transient HTTP/transport conditions |
| Retry guardrail | APPROVED — block automatic retries when projected/current monthly consumption would exceed 207,500 |
| Claims and overlap | APPROVED DESIGN — atomic (SamplingTargetId, LogicalDueBucket) identity; no duplicate HTTP request; no catch-up replay |
| Production state | NOT STARTED — no scheduler, database objects, target rows, Windows task, Power BI change, or Tiered50 activation |
| Stage 14 | NOT STARTED |

## Approved tier and result-limit design

The runtime design uses the immutable Stage 12 panel identity and retained
Stage 13 density evidence. Stage 12 SelectionRank is not a runtime tier input.

| Tier | Density ranks | Stations | Cadence | NumberOfResults |
|---|---:|---:|---:|---:|
| A | 1–8 | 8 | 5 minutes | 20 |
| B | 9–28 | 20 | 15 minutes | 30 |
| C | 29–50 | 22 | 30 minutes | 30 |

The static support recalculation is based on retained station-level exact-feed
evidence and was performed offline. It does not establish realtime response
completeness, a provider-wide result maximum, guaranteed 20/30-event
availability, or complete forward coverage. Historical valid N20/N30
diagnostics remain bounded acceptance evidence only.

## Quota and retry reserve

The documented absolute provider ceiling remains 250,000 requests/month. The
normal planning envelope remains 207,500–210,000. The approved nominal
Tiered50 load, 10% retry reserve, Legacy7 reserve, and diagnostic reserve
reconcile as follows:

| Month | Nominal Tiered50 | 10% retry reserve | Tiered50 with reserve | Legacy7 reserve | Diagnostic reserve | Conservative total |
|---|---:|---:|---:|---:|---:|---:|
| 30 days | 158,400 | 15,840 | 174,240 | 8,640 | 100 | **182,980** |
| 31 days | 163,680 | 16,368 | 180,048 | 8,928 | 100 | **189,076** |

The retry contract permits one retry for HTTP 408, 429, 500, 502, 503, 504
and approved transient transport failures. HTTP 429 must respect Retry-After.
HTTP 401/403, deterministic request-contract failures, deterministic
parser/schema failures, and deterministic TRIAS application errors are not
retryable. A failed station is isolated from other due targets. MaxAttempts=2
is bounded by the monthly 207,500 automatic-retry guardrail; it does not
authorize unlimited retries.

Current shared-credential usage remains unavailable through an established
read-only audit path, so the documented Legacy7 reserve is retained
conservatively and is not treated as zero.

## Phase, overlap, and continuity contract

The six-heartbeat phase capacities are:

| Heartbeat | Tier A | Tier B | Tier C | Total |
|---:|---:|---:|---:|---:|
| 0 | 8 | 7 | 4 | **19** |
| 1 | 8 | 7 | 3 | **18** |
| 2 | 8 | 6 | 4 | **18** |
| 3 | 8 | 7 | 3 | **18** |
| 4 | 8 | 7 | 4 | **19** |
| 5 | 8 | 6 | 4 | **18** |

The persisted due sequence is [19, 18, 18, 18, 19, 18]; maximum due load is
19. Phase assignment is deterministic from panel version, tier, and immutable
SamplingTargetId, and is a Stage 14 materialization contract rather than a
production row created by Stage 13.

The unique execution identity is
(SamplingTargetId, LogicalDueBucket). Claims must be atomic and durable.
Duplicate invocations issue no HTTP request, missed historical buckets are not
replayed, and no fabricated historical realtime snapshot is created. Legacy7
history and all seven existing target identities remain unchanged.

## Retained diagnostic evidence and boundaries

The retained historical sequential population reconciles to 24 client-side
invocations, 24 HTTP responses, and 24 confirmed provider HTTP attempts. The
retained cap-2 population reconciles to 6/6/6. The corrected diagnostic
reconciles to 24 client-side invocations, 0 HTTP responses, 0 confirmed
provider HTTP attempts, unknown provider quota consumption, and 24 separate
conservative quota-accounting attempts. Cumulative client invocations are 114;
this is not a claim that 114 provider requests or quota units were consumed.

The historical 100-attempt diagnostic budget remains historical, and the
explicit 24-invocation extension is retained in the evidence chronology. The
corrected live limitation is not a provider-wide completeness claim. No raw
response, request header, API key, or secret-bearing error text was persisted.

Stage 14 must be separately started and must validate cap-2 concurrency,
database persistence, atomic claims, retry guardrails, failure isolation, and
continuity before any activation decision. This Stage 13 closeout itself made
zero MDD requests and performed no DNS/live-network operation.

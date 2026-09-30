# Stage 13 — Tiered Collector Readiness Completion Decision

**Date:** `2026-09-30`  
**Current status:** `IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED`
**Production status:** `NOT STARTED`

## Reopened decision state

Stage 13 evidence is retained using the exact approved Stage 12 panel and
sanitized bounded evidence, but the material production design gates are open
because explicit user approval was required before finalizing them. The
machine-readable artifact at
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json)
is authoritative. The prior D-046 closeout is preserved as historical audit
evidence, superseded, and not valid as final user approval.

| Gate | Result | Evidence / decision |
|---|---|---|
| Stage 12 fixed panel | **PASS** | `TIERED50_PANEL_V1`; exactly 50 fixed parent stations; all Legacy7 anchors preserved |
| Exact static feed | **PASS** | `VERSION__20260930_0053`; ZIP SHA-256 `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147` |
| Station/tier density | **PASS** | 5,100 station/service-day rows; 5/10/15/30-minute forward-window evidence; deterministic density tiers |
| Production tier assignment | **TECHNICALLY EVALUATED OPTION — USER APPROVAL REQUIRED** | A=10, B=20, C=20; density metrics only; no Stage 12 rank reuse |
| Cadence | **TECHNICALLY EVALUATED OPTION — USER APPROVAL REQUIRED** | A/5 min, B/15 min, C/30 min; 240 logical requests/hour |
| Quota planning | **TECHNICALLY EVALUATED OPTION — CONSERVATIVE** | 30-day nominal total 181,540; 31-day nominal total 187,588; 10% stress total 205,444 after Legacy7 and diagnostic reserves |
| Current shared credential usage | **LIMITATION, NOT FALSE ZERO** | current Legacy audit unavailable; full documented Legacy7 reserve of 8,640/8,928 is applied |
| Production `NumberOfResults` | **UNDECIDED — USER APPROVAL REQUIRED** | static P95 comparison retains 36/50 for N20 and 50/50 for N30 and A20/B30/C30; the corrected live run produced no valid response |
| Provider maximum | **LIMITATION** | `PROVIDER_MAXIMUM_NOT_ESTABLISHED`; no provider-maximum claim is needed or made while NumberOfResults remains undecided |
| Sequential duration evidence | **PASS — HISTORICAL EVIDENCE RETAINED** | six representatives × four result limits, 24 sequential client invocations; temporal-span fields invalid for forward coverage |
| Corrected result-limit diagnostic | **EXECUTED — NO VALID LIVE RESPONSES** | exactly 24 approved client-side invocations; 0 HTTP responses; retained safe category `OTHER_NETWORK_FAILURE`; provider quota consumption is unknown/not confirmed; NumberOfResults remains undecided |
| Bounded concurrency | **TECHNICALLY EVALUATED OPTION — USER APPROVAL REQUIRED** | three cap-2 pairs; six valid HTTP 200/parseable responses; no 429/5xx/timeout/transport pressure |
| Heartbeat feasibility | **PASS** | due sequence `21/21/19/20/20/19`; max 21; cap-2 P95 estimate 10,544.501 ms |
| Phase assignment | **PASS — DESIGN CONTRACT** | stable identity-hash assignment; exact 50 assignments recorded; no production rows created |
| Claim/overlap contract | **PASS — DESIGN CONTRACT** | unique `(SamplingTargetId, LogicalDueBucket)` atomic claim; duplicate invocation does not issue HTTP |
| Failure isolation | **PASS — DESIGN REQUIREMENT** | independent per-target status, attempts, duration, parser, persistence, and error metadata |
| Historical continuity | **PASS** | Legacy7 history and target identities preserved; no cutover or replay |

## Technically evaluated options (not final user approval)

The retained production-readiness options for later review are:

- `TIERED50_PANEL_V1`, exactly 50 Stage 12-approved stations;
- Tier A: 10 density-ranked stations every 5 minutes;
- Tier B: 20 density-ranked stations every 15 minutes;
- Tier C: 20 density-ranked stations every 30 minutes;
- N20, N30, and A20/B30/C30 remain unselected; static schedule support is not
  realtime coverage evidence;
- `MaxAttempts = 1`, zero automatic retries, as a technically evaluated retry option;
- `MaximumConcurrency = 2`, as a technically evaluated bounded option;
- six-heartbeat phase super-cycle with due counts `21/21/19/20/20/19`; and
- atomic execution identity `(SamplingTargetId, LogicalDueBucket)` with no
  catch-up replay.

The provider maximum remains unknown. The old forward-span algorithm was
invalid because it mixed PreviousCall, ThisCall, OnwardCall, and departure
timings. The user explicitly approved the corrected comparison, and exactly 24
sequential client-side MDD request invocations were issued, increasing
cumulative client invocations from 90 to 114. All 24 ended before a valid HTTP
response. The retained safe category is `OTHER_NETWORK_FAILURE` because the
historical raw exception details were not retained; confirmed provider HTTP
attempts are 0 and provider quota consumption is
`UNKNOWN_NOT_CONFIRMED_BECAUSE_FAILURE_OCCURRED_BEFORE_HTTP_RESPONSE`. The
historical initial diagnostic budget is 100, the explicit additional user
authorization is 24, and the authorized cumulative client-attempt ceiling is
114. The historical budget was not rewritten.

## Credential and quota boundary

The repository inventory found only inactive bounded diagnostics plus the
external Legacy Collector / `ctl.MddCollectorRun` consumer. Current shared
credential usage was unavailable through an established read-only audit path.
The design therefore reserves the complete documented Legacy7 monthly
baseline rather than fabricating zero current usage. The historical Stage 13
initial diagnostic budget is 100 client attempts; 90 historical client
diagnostic attempts plus the explicitly approved 24 corrected client
invocations produce a cumulative client-invocation count of 114 without
silently changing that historical budget. The corrected run received 0 HTTP
responses, so no provider quota count is claimed. For conservative planning,
24 is retained separately as `conservative_quota_accounting_attempts`. Only
sanitized evidence was retained.

The technically evaluated normal and stress plans remain under the 210,000
upper edge of the project's normal planning envelope even with the conservative
Legacy7 reserve and the 10% stress sensitivity. Current shared usage must still
be audited before any production activation, and the cadence/retry choices
remain gated by user approval.

## User decision gate

The machine-readable artifact explicitly records all material design choices as
technically evaluated options. Until the user approves them, all of these flags
remain `false`:

- `production_number_of_results_approved`;
- `production_concurrency_approved`;
- `production_cadence_approved`; and
- `production_retry_policy_approved`.

The current user recommendations are retained separately from approval:

- preferred cadence candidate: `8/20/22 @ 5/15/30`, subject to the boundary
  review in the evidence artifact;
- preferred bounded concurrency: `2`, subject to Stage 14 end-to-end dry-run
  validation;
- preferred retry policy: `MaxAttempts=2` with at most one bounded retry for
  approved transient failures; and
- NumberOfResults remains undecided because the corrected run produced no
  valid live response.

No new final decision may be recorded while the gate is open.

## Future live-diagnostic boundary

The previous 24-invocation authorization has been exercised and is not
authorization for another rerun. Any future live retry requires a new explicit
approval after review of the offline correction. The execution environment must
first pass the zero-request DNS preflight; the first authorized MDD invocation
is then a smoke request, and the remaining matrix may proceed only after a
usable HTTP/TRIAS response. DNS/network, TLS/proxy/connectivity, and 401/403
failures stop immediately. Provider HTTP errors such as 429/5xx follow the
explicit stop policy and are never relabeled as DNS/network failures.

The exact network-capable local action is:

```text
python3 scripts/stage_13_tiered_collector_readiness.py --gtfs-zip /path/to/verified-stage12-vrs-global-id.zip --run-live-diagnostics --approve-live-rerun-after-network-preflight --prior-client-attempts 114
```

`MDD_API_KEY` must be supplied through the inherited environment only. A
successful rerun authorizes at most 24 additional client-side request
invocations; provider quota consumption remains unknown unless HTTP responses
are actually received.

## Boundary confirmation

- Stage 14 remains **NOT STARTED**.
- Tiered50 was not activated.
- No 7→50 cutover was performed.
- No Windows Scheduled Task was created.
- No production database object, scheduler, or target row was created.
- Power BI was not modified.
- Legacy7 history and the seven existing target identities were not deleted or
  reinserted.

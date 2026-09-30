# Stage 13 — Tiered Collector Readiness

**Date:** `2026-09-30`  
**Status:** `CONDITIONALLY BLOCKED — PROVIDER_MAXIMUM_NOT_ESTABLISHED; STATIC_EVENT_DENSITY_NOT_ESTABLISHED; CURRENT_PRODUCTION_MDD_AUDIT_UNAVAILABLE; CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED`  
**Production status:** `NOT STARTED`

The authoritative machine-readable audit is
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json).
The audit is source-controlled and offline. It does not create a scheduler,
collector, database object, station target, or production request.

## Decision summary

The historical Tiered50 cadence arithmetic is reproducible:

- Tier A: 10 targets every 5 minutes;
- Tier B: 20 targets every 10 minutes; and
- Tier C: 20 targets every 30 minutes.

That candidate produces `280` logical requests/hour, `6,720`/day, `201,600`
in a 30-day month, and `208,320` in a 31-day month. The arithmetic is not a
production approval. The 31-day base is already `99.2%` of the upper
`210,000` normal planning envelope before retries or legitimate diagnostics.
The repository has historical retry-count evidence, but no current production
caller audit or live `ctl.MddCollectorRun` history from which a safe total
budget can be established.

The production `NumberOfResults` remains unset. Both `5` and `20` reached the
configured response limit in bounded samples. This establishes compatibility
and saturation in those samples, not provider maximum, forward-horizon
coverage, or an all-station result limit. The required explicit classification
is `PROVIDER_MAXIMUM_NOT_ESTABLISHED`.

Stage 12 now approves fixed panel version `TIERED50_PANEL_V1` with 50 parent
stations. This readiness artifact does not assign those stations to production
tiers or activate targets. The historical `10/20/20` population remains only a
provisional count population for quota and phase arithmetic, not a production
tier assignment.

## 1. Evidence boundary and prerequisite gate

Earlier approved semantics remain in force: MDD/TRIAS is the primary targeted
realtime source, GTFS-RT remains complementary, source timestamps and
estimated timing remain distinct, `RecordedAtTime` is not inferred, raw
payloads are not made permanent, and sampled station evidence is not
network-wide performance evidence.

The Stage 12 completion artifacts are now present and authoritative:

- `docs/stages/12-50-station-selection.md`;
- `docs/stages/12-50-station-selection-completion-decision.md`; and
- `docs/stages/12-50-station-selection-evidence.json`.

The previously discussed `10/20/20` candidate is retained as:

| Provisional population field | Value | Use in this audit |
| --- | ---: | --- |
| Total station count | 50 | quota and phase-count arithmetic only |
| Tier A count | 10 | quota and phase-count arithmetic only |
| Tier B count | 20 | quota and phase-count arithmetic only |
| Tier C count | 20 | quota and phase-count arithmetic only |
| Station identities | fixed `TIERED50_PANEL_V1` | no target creation or production activation |

The Stage 12 prerequisite is satisfied. The remaining blockers are provider
result-limit evidence, Stage 13-specific tier/density and forward-horizon
analysis, current shared-credential usage, and provider-safe concurrency. No
Stage 13 production implementation is started by this handoff.

## 2. Candidate cadence and quota

### 2.1 Logical request math

`NumberOfResults` is a per-response result limit; it does not multiply HTTP
request count. The candidate base load is:

| Tier | Targets | Interval | Logical requests/hour | Logical requests/day |
| --- | ---: | ---: | ---: | ---: |
| A | 10 | 5 min | 120 | 2,880 |
| B | 20 | 10 min | 120 | 2,880 |
| C | 20 | 30 min | 40 | 960 |
| **Total** | **50** | — | **280** | **6,720** |

| Calendar month | Base logical requests | % of 250,000 ceiling | % of 207,500 lower envelope | % of 210,000 upper envelope | Headroom to ceiling |
| --- | ---: | ---: | ---: | ---: | ---: |
| 30 days | 201,600 | 80.640% | 97.157% | 96.000% | 48,400 |
| 31 days | 208,320 | 83.328% | 100.395% | 99.200% | 41,680 |

The 30-day base is below the nominal 83% lower edge, which is conservative;
the 31-day base is inside the nominal 83–84% range but leaves only `1,680`
requests to the `210,000` upper edge. Neither row is an activation approval.
Retries, legitimate diagnostics, any still-running external Legacy Collector,
and operational changes must remain outside the intentional base schedule.

### 2.2 Retry sensitivity

The only durable quota retry count in the restart repository is the historical
Legacy audit snapshot: `35` additional retry attempts among `1,629` HTTP
attempts, an additional-attempt share of approximately `2.1486%`. It is not an
observed Tiered50 retry rate. Applying it as a sensitivity only gives:

| Additional-attempt scenario | 30-day projected HTTP requests | 31-day projected HTTP requests | 31-day % of ceiling | 31-day headroom to ceiling |
| --- | ---: | ---: | ---: | ---: |
| 0% | 201,600 | 208,320 | 83.328% | 41,680 |
| Legacy historical share, 2.1486% | 205,931.49 | 212,795.87 | 85.118% | 37,204.13 |
| 5% scenario | 211,680 | 218,736 | 87.494% | 31,264 |
| 10% scenario | 221,760 | 229,152 | 91.661% | 20,848 |

The scenario rows are calculations, not observed retry rates. They show why a
candidate base schedule cannot be approved from base-request arithmetic alone.
All retries count as actual HTTP requests. No quota is reserved for completed
or inactive diagnostics merely because their scripts remain in the repository.

## 3. MDD credential consumers

The repository contains no scheduled production collector or Windows Task
definition. The following are the repository-controlled callers that can use
`MDD_API_KEY`:

| Caller | Purpose / normal cadence | Retry policy | Classification | Included in forward monthly planning? |
| --- | --- | --- | --- | --- |
| `scripts/stage_08_mdd_realtime_pilot.py` | One bounded Hbf diagnostic; no normal cadence; historical run complete | 0 retries; 30-second timeout; overlap slots skipped | bounded diagnostic, inactive after completion | historical actual only; no standing reserve |
| `scripts/stage_08_authorization_request_contract_diagnostic.py` | Manual Hbf N5/N20 decision-tree controls | 0 retries; bounded controls | bounded diagnostic, inactive after completion | no standing reserve; explicit approval required for any rerun |
| `scripts/stage_03a_disruption_evidence_probe.py` | Stage 3A MDD Situation/discovery evidence; manual host-only run | 0 retries; sequential; no fallback | bounded diagnostic, inactive after completion | historical actual only; no standing reserve |
| `scripts/stage_09_source_backed_acceptance.py` | Up to three station requests for Stage 9 acceptance | 0 retries; one request per target | bounded diagnostic, inactive after completion | historical actual only; no standing reserve |
| `scripts/mdd_recorded_time_lifecycle_probe.py` | Near-event `RecordedAtTime` lifecycle diagnostic | 0 retries; seven sequential attempts maximum | bounded diagnostic, historical | no standing reserve |
| `scripts/stage_02a2_probe.py` | One-request source-contract diagnostic | 0 retries; one request maximum | inactive diagnostic | no |
| `scripts/stage_02a3_historical_actual_time_probe.py` | Five historical-horizon availability requests | 0 retries; one per horizon | inactive diagnostic | no |

The external Legacy Collector / SQL Server `ctl.MddCollectorRun` history is a
shared-credential consumer outside this restart repository. Its historical
2026-09-25 snapshot recorded `1,599` executions, `1,629` HTTP attempts, and
`35` additional retry attempts. Current cadence, current account usage, and
current database history are unavailable here. It must be included in the
activation budget once audited; it is not silently treated as zero.

## 4. `NumberOfResults` decision

The audit does not carry forward either `5` or `20` as a production default.

| Evidence population | Requested limit | Returned-result evidence | Interpretation |
| --- | ---: | --- | --- |
| Stage 3A ten-station MDD discovery | 5 | 10/10 responses returned 5 results | saturated at N5; N5 is not sufficient evidence for production coverage |
| Stage 8 one-hour Köln Hbf pilot | 20 | 343 successful responses and 6,860 `StopEventResult` occurrences, equal to 343 × 20 | bounded Hbf saturation/compatibility only; raw per-response rows were cleaned after evidence extraction |
| Stage 9 three-station source-backed acceptance | 20 | 3/3 request contexts had 20 stop-event results | bounded three-station saturation only |
| Stage 8 N5/N20 controls | 5 and 20 | both HTTP 200 and parseable at Köln Hbf | compatibility controls only |

The following remain unresolved:

- provider maximum: `PROVIDER_MAXIMUM_NOT_ESTABLISHED`;
- forward temporal coverage beyond the configured result limit;
- differences by station, tier, mode, time of day, or density;
- static scheduled-event density for the future population; and
- whether tier-specific result limits would materially improve coverage.

Application-side validation ranges are not provider evidence. Because the
bounded samples saturate at both tested limits and the provider maximum is not
established, the production value remains `NULL`/unset and the gate is
conditionally blocked.

## 5. Static event-density audit

The current approved Stage 4 evidence is the VRS Global-ID snapshot
`VERSION__20260928_0042`, valid `2026-09-02` through `2026-12-12`, with ZIP
SHA-256
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`.
It retains aggregate scope evidence, including `3,137` Cologne stop rows and
`1,144,295` Cologne scheduled stop events. The raw approved GTFS ZIP and the
station/tier panel are not present in this checkout. The current Stage 4 JSON
does not contain station-level stop-time rows or active-calendar expansions.

Consequently, the required station/tier rows cannot be produced without
inventing station identities or treating an aggregate as a density. The
machine-readable artifact records an empty row set with this contract:

`station | tier | scheduled_event_density | forward_window | result_limit_coverage_status`

The density distribution, percentiles, outliers, and evidence that a result
limit covers the intended forward horizon are all
`NOT_ESTABLISHED`. Static scheduled density is planning evidence only and is
not realtime response cardinality.

## 6. Execution-duration evidence

Different execution modes remain separate. Percentiles use linear interpolation
over the retained `elapsed_ms` values in each population.

| Population | Count | Min ms | P50 ms | P90 ms | P95 ms | P99 ms | Max ms | Failures / timeouts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Stage 3A ten-station discovery | 10 | 405.888 | 494.601 | 763.346 | 816.290 | 858.646 | 869.235 | 0 / 0 |
| Stage 8 near-event lifecycle logs | 35 | 528.158 | 671.382 | 1,154.985 | 1,239.916 | 1,773.889 | 2,009.980 | 0 / 0 |
| Stage 9 source-backed acceptance | 3 | 550.525 | 839.481 | 921.824 | 932.117 | 940.351 | 942.410 | 0 / 0 |

The Stage 8 one-hour pilot has durable run-level evidence, not per-request
durations: `343` attempts in approximately `3,606.801` seconds, `857` skipped
overlap slots, and no parser, persistence, network, timeout, or MDD
application failures. Its average whole-loop wall time is approximately
`10.516` seconds per attempted request. Twenty-four sequential due targets at
that average would take approximately `252.379` seconds of a 300-second
heartbeat, before retries or variance. The skipped 3-second diagnostic slots
are relevant evidence about serialization overhead, not a production
requirement.

These measurements support a five-minute feasibility analysis but do not
establish a production service-level guarantee across fifty targets.

## 7. Retry and `HttpAttempts` evidence

### Source-controlled bounded evidence

- Stage 3A discovery: 10 HTTP 200 responses, zero retries, zero timeouts and
  network failures; successful first-attempt percentage `100%` for that
  bounded population.
- Stage 8 one-hour pilot: 343 HTTP 200 responses, zero retries, zero parser,
  persistence, network/DNS, timeout, and MDD application failures; successful
  first-attempt percentage `100%` for that bounded population.
- Stage 9 source-backed acceptance: 3 HTTP 200 responses, zero retries and
  zero recorded transport failures; successful first-attempt percentage
  `100%` for that bounded population.
- The later historical Stage 8 authorization attempt returned one HTTP 401
  without a retry. It is failure chronology, not a current production failure
  rate.

### Legacy and unavailable current evidence

The legacy audit count of 35 additional attempts is retained as historical
evidence and used only for labeled quota sensitivity. It does not provide a
successful-first-attempt percentage or HTTP failure distribution. The current
SQL Server `ctl.MddCollectorRun` history is not in the restart repository, so
there is no current production measurement to present as current. The exact
state is `UNAVAILABLE_CURRENT_PRODUCTION_AUDIT_EVIDENCE`.

## 8. Deterministic phase staggering

The count-level design reconciles over a six-heartbeat, 30-minute super-cycle:

| Heartbeat | Tier A | Tier B | Tier C | Total due targets |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 10 | 10 | 4 | 24 |
| 2 | 10 | 10 | 3 | 23 |
| 3 | 10 | 10 | 3 | 23 |
| 4 | 10 | 10 | 4 | 24 |
| 5 | 10 | 10 | 3 | 23 |
| 6 | 10 | 10 | 3 | 23 |
| **Total / 30 min** | **60** | **60** | **20** | **140** |

The next 30-minute cycle repeats, so the total is `280` logical requests/hour.
Tier A is due every heartbeat. Tier B has two persisted phases of ten. Tier C
has six persisted phases with counts `4/3/3/4/3/3`.

Each approved target must carry an immutable `SamplingTargetId` and a persisted
phase index. A stable target-ID-based assignment may be materialized during
Stage 12, but mutable rank ordering must not be an input. The assignments must
be deterministic, persisted/configured, auditable, and versioned with the
panel. No production scheduler is implemented here.

## 9. Sequential versus bounded concurrency

The explicit Stage 13 readiness direction is **small bounded concurrency, with
a provisional maximum of two workers, but not approved for activation**.

### Option A — fully sequential

The retained Stage 8 run-level evidence implies approximately 252.379 seconds
for 24 targets at its observed average whole-loop time, leaving only about
47.621 seconds in a five-minute heartbeat. That margin is not a guarantee once
retries, persistence variance, a 30-second timeout, or a slower station are
included. Fully sequential execution is therefore not approved as a production
guarantee.

### Option B — small bounded concurrency

Two workers would provide schedule margin for 23–24 due targets while keeping
provider load bounded and isolating a slow or failed target. Unbounded
concurrency is prohibited. However, the repository contains no controlled
provider-safe concurrent MDD evidence, no rate-limit behavior under
concurrency, and no current production retry population. The cap of two is a
conditional design direction, not an activation approval.

The exact blocker is `CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED`. Before any
activation, a separately approved bounded concurrency validation must measure
provider response behavior, HTTP 429/5xx outcomes, timeout and retry behavior,
failure isolation, and quota impact. The Stage 8 30-second timeout is the only
available timeout baseline; it is not silently promoted as a final production
value.

| Evaluation factor | Option A: sequential | Option B: bounded cap 2 |
| --- | --- | --- |
| 23–24 targets in 5 minutes | only limited margin at the Stage 8 run-level average | materially more schedule margin, subject to validation |
| Request timeout | a single 30-second timeout can consume a large part of the heartbeat | timeout impact is isolated to one worker, but still consumes quota/time |
| Retry behavior | simple to reason about, but retries extend the serial queue | bounded retries can run on one worker; every retry remains a real request |
| Quota impact | lowest concurrency pressure; base/retry counts are unchanged | same logical quota arithmetic, with additional provider burst pressure |
| Provider load | one request at a time | two simultaneous requests maximum; provider behavior is unmeasured |
| Failure isolation | requires explicit per-target continuation | naturally isolates a slow/failed worker if claims are independent |
| Operational simplicity | simplest implementation | more coordination and lease/claim observability |
| Overlap risk | still requires atomic target/bucket claims | requires the same atomic claims plus bounded worker ownership |

This comparison supports a conditional cap-2 direction, not a production
approval.

## 10. Overlap and duplicate-execution protection

The future dispatcher must enforce unique execution identity at:

`(SamplingTargetId, LogicalDueBucket)`

The logical due bucket is the integer UTC epoch bucket
`floor(current_utc_epoch_seconds / 300)`. UTC bucket identity avoids DST
duplication/omission. The future durable control relation must have a unique
constraint on the target/bucket pair. Overlapping Windows Task/process
invocations must therefore compete for the same atomic claim; a process
atomically inserts a claim with
a run ID and claim token; a unique-key conflict means another invocation owns
the target/bucket and no HTTP request may be issued by the loser.

The claim contract must retain at least:

- target and logical bucket identity;
- claim token and collector-run identity;
- claimed, started, and completed timestamps;
- status and error metadata; and
- stale/lease observability.

A crash leaves a durable `RUNNING` or `STALE_INCOMPLETE` record visible for
operations. It does not authorize re-executing the same historical bucket.
A later invocation may claim the current wall-clock bucket, but missed buckets
must not be replayed and no historical realtime snapshot may be fabricated.
No physical claim table or scheduler is created in Stage 13.

## 11. Failure isolation

The requirement is approved as a design contract: one failed station must not
abort the remaining due targets. Each target request retains independent audit,
HTTP status, attempt count, duration, parser result, persistence result, and
error metadata. A batch summary may report incomplete coverage, but it must not
erase the per-target failure or turn a missing observation into an on-time,
cancelled, or served claim.

## 12. Quota guardrails

The production guardrails are:

1. The monthly full ceiling is `250,000` HTTP requests.
2. Normal planning remains around `207,500–210,000` requests/month, not the
   full ceiling.
3. Retries are real HTTP requests and must be counted in the active budget.
4. `NumberOfResults` does not multiply HTTP request count.
5. The base cadence must be calculated for both 30-day and 31-day months.
6. Current account/caller usage must be audited before activation; the absent
   current `ctl.MddCollectorRun` history cannot be treated as zero usage.
7. Completed/inactive diagnostics receive no standing quota reserve.
8. Catch-up bursts are prohibited.
9. A duplicate target/bucket claim is not allowed to spend another request.
10. A budget must show base requests, bounded retry sensitivity, other active
    credential consumers, calendar-month variation, and remaining headroom.

The current evidence does not approve the candidate as a final production
budget because the fixed panel, provider result maximum, current shared-caller
usage, and concurrency behavior are unresolved.

## 13. Historical continuity and rollout boundary

The future Stage 14 handoff must preserve the seven existing Legacy7
`SamplingTargetId` values and their realtime history. It may add only newly
approved targets, must not delete/reinsert existing targets, and must record an
explicit `Legacy7` versus `Tiered50` sampling era/version boundary. Before/after
raw-volume comparisons require cadence qualification. Stage 13 performs no
cutover, no data migration, and no activation.

## 14. What is and is not approved

Approved as Stage 13 readiness requirements:

- the quota accounting rules and 30/31-day arithmetic;
- the six-heartbeat phase-count reconciliation;
- deterministic, persisted, rank-independent phase assignment as a future
  panel contract;
- unique target/bucket claim semantics;
- no historical replay/catch-up behavior;
- per-target failure isolation; and
- Legacy7 historical-continuity requirements.

Not approved:

- the final 50-station panel or production target identities;
- a production `NumberOfResults` value;
- production cadence activation;
- a production retry count or final timeout policy;
- production concurrency; and
- any Stage 14 cutover or Tiered50 collector activation.

The completion decision records the resulting conditional block rather than
marking Stage 13 complete merely for chronology.

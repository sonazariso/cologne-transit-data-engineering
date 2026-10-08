# Stage 15 — Post-Cutover Validation Readiness

> **Current status note (2026-10-08):** This readiness record remains the
> version-controlled offline contract for later Tiered50 validation. SQL Server
> is the sole authoritative project database. Tiered50 is prepared but
> inactive, and Legacy7 is retired; no activation or Stage 15B claim is made.

**Current Stage 15A status:** `IN PROGRESS`
**Stage 15B live post-cutover validation:** `NOT STARTED — AWAITS TIERED50 CUTOVER`

This document is the authoritative Stage 15 readiness handoff. It does not
claim that the Tiered50 panel has been activated, that production evidence
exists, or that Stage 15B has started. S78's `LIVE_RESEARCH_SINGLE_1` SQLite
observations are research evidence only and are never treated as a production
sampling-era transition.

The surrounding status remains:

- Stage 14 overall: `TIERED50 PREPARED — PRODUCTION INACTIVE; LEGACY7 RETIRED`
- Stage 14 collector implementation: `STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE`
- Stage 15 validation readiness: `IN PROGRESS`
- Stage 15 live post-cutover validation: `NOT STARTED — AWAITS TIERED50 CUTOVER`

The historical Mac Legacy7 runtime completed its corrected selector and
heartbeat evidence, then was unloaded and disabled at the recorded boundary.
The exact Tiered50 materialization and offline preflight are complete, but the
supplied Monitoring screenshot does not establish the provider-wide scope,
freshness, reset period, or accounting model required for activation. The
Tiered50 LaunchAgent remains disabled. This does not start Stage 15B.

## 15A — Pre-cutover validation framework/readiness

Stage 15A is the offline work that can be completed before production
activation. It provides:

- the version-controlled [validation contract](../../config/stage15_validation_contract.json);
- the reusable `cologne_transit_stage15` analyzer package;
- a read-only SQLite evidence adapter that can inspect either the disposable
  `DEVELOPMENT` profile or durable `LIVE_RESEARCH` evidence, plus a
  backend-neutral evidence-adapter protocol where it remains useful;
- deterministic reconciliation of approved expected target/bucket work against
  actual audit, claim, attempt, snapshot, and quota evidence;
- dispatcher-owner, duplicate-invocation, target-claim, stale-claim, retry,
  quota, coverage, duration, parser, persistence, and continuity diagnostics;
- machine-readable JSON reports from
  `python scripts/stage_15_validation.py`;
- deterministic offline tests using the Stage 14 simulation harness.

The JSON report is authoritative. Every validation result uses the explicit
fields `validation_id`, `category`, `grain`, `window_start_utc`,
`window_end_utc`, `expected_value`, `actual_value`, `unit`, `status`,
`policy_state`, `evidence_count`, `reason`, and `limitations`.

The result status vocabulary is deliberately small:

`PASS`, `FAIL`, `WARNING`, `POLICY_PENDING`, `NOT_APPLICABLE`, and
`INSUFFICIENT_EVIDENCE`.

The analyzer does not infer success from a missing error row. Each expected
target/logical-bucket pair is classified explicitly as
`EXPECTED_AND_SUCCEEDED`, `EXPECTED_AND_FAILED`, `EXPECTED_AND_SKIPPED`, or
`EXPECTED_BUT_MISSING`. Duplicate dispatcher and target-claim events are
reported as `DUPLICATE_INVOCATION_PREVENTED`; actual work outside the approved
manifest/schedule is `UNEXPECTED_EXECUTION`.

The analyzer is read-only with respect to collector evidence. It does not
claim buckets, mutate target state, repair stale claims, finalize quota
permits, write parsed snapshots, or send transport requests. SQLite is opened
with a read-only connection. The adapter protocol keeps these semantics
independent of any later storage decision. PostgreSQL work is not currently
planned or authorized.

## Approved automatic checks

The contract encodes only values already approved in Stage 13/14:

- `TIERED50_PANEL_V1`, exactly 50 targets, the exact 7/43 Legacy7/new split,
  exact Legacy7 lineage IDs 1 through 7, and null new production IDs before
  materialization;
- tier counts 8/20/22, cadence 5/15/30 minutes, and NumberOfResults 20/30/30;
- five-minute distribution `[19, 18, 18, 18, 19, 18]`, 110 logical requests
  per 30-minute super-cycle, and 220 logical requests per hour;
- MaximumConcurrency 2, MaxAttempts 2, at most one automatic retry, and no
  catch-up/replay of missed realtime buckets;
- duplicate dispatcher/target-claim transport protection and failed-target
  isolation when relevant evidence exists;
- quota boundaries 207,500 for automatic retry protection, 210,000 for normal
  operation, and 250,000 for the provider absolute ceiling;
- separation of scheduled logical requests, client invocations, network
  attempts, HTTP responses, confirmed provider attempts, conservative unknown
  attempts, active reservations, and offline simulation activity;
- preservation of Legacy7 identities as distinct lineage.

An automatic PASS is not emitted for a design-only future production check
merely because the production backend is absent. Evidence-dependent checks use
`NOT_APPLICABLE` or `INSUFFICIENT_EVIDENCE` as appropriate.

## Policy-pending measurements

The analyzer reports, without inventing acceptance criteria:

- target failure, timeout, retry, and heartbeat-budget skip rates;
- stale-claim count/rate, expected-vs-actual station coverage, missing-bucket
  rate, HTTP response rate, and zero-result frequency;
- parser-gap count/rate, parser-failure rate, persistence-failure rate,
  stop-event-result counts, and per-error-category frequency;
- expected/actual buckets, success/failure/skip/missing/duplicate-prevented
  counts, persisted snapshots, and measured station/tier percentages;
- target, HTTP-attempt, and collector-run/heartbeat duration count, min, P50,
  P90, P95, P99, and max;
- per-station and per-tier coverage.

S79 also adds a read-only timing-history vocabulary: normalized timing
observation count, events with estimate revision history, non-NULL
`RecordedAtTime` evidence count, events with `LastObservedRecordedAtTime`, and
the count whose latest snapshot value is NULL while historical non-NULL source
evidence remains. These values are measurement-only and do not establish an
acceptance threshold or a semantic timing interpretation. The analyzer keeps
`recorded_at_time_evidence` as source evidence and reads the timing reporting
projection as a derived helper, so the same source occurrence is not counted
twice.

S80 makes the historical-window boundary explicit: raw timing observations are
filtered by logical due bucket and by observed/captured timestamp before any
reporting grouping. The SQLite reporting view remains a global current-state
projection and is not used as historical input. Ambiguous same-snapshot stable
identity rows are retained as evidence but excluded from aggregate grouping.

Every measured policy metric remains `POLICY_PENDING` until a user-approved
threshold exists. Missing evidence is represented as `INSUFFICIENT_EVIDENCE`
with `policy_state: POLICY_PENDING`. A parser gap is evidence to measure, not
an automatic fatal result. A zero-result response is not automatically an
error.

The final heartbeat persistence/cleanup safety margin remains deferred, so no
duration acceptance threshold is applied.

## 15B — Real post-cutover validation

Stage 15B cannot begin until all of the following are real, durable, and
identified as production evidence:

1. Tiered50 has actually been activated through the approved cutover gate.
2. Real production target IDs have been materialized; no development null-ID
   manifest is treated as production identity evidence.
3. A real sampling-era transition timestamp has been recorded by the
   production adapter. Stage 15A never creates or infers this timestamp.
4. Post-cutover dispatcher, claim, transport, parser, persistence, quota, and
   snapshot evidence is available for a declared UTC analysis window.

Only then can the validator assess one real era transition, preserved Legacy7
history, no delete/reinsert continuity break, post-cutover Tiered50
attribution, and the operational measurements above. Before then, those
claims are `INSUFFICIENT_EVIDENCE` or `NOT_APPLICABLE`; they are not PASS.

The current repository has no real cutover evidence. Therefore this iteration
does not make post-cutover completeness, success, coverage, quota-consumption,
or continuity claims.

## Offline command

Manifest/configuration readiness:

```text
python scripts/stage_15_validation.py
```

Read-only analysis of an existing SQLite development or live-research evidence
file:

```text
python scripts/stage_15_validation.py \
  --evidence .local/stage14/collector.sqlite3 \
  --window-start 2026-09-30T12:00:00Z \
  --window-end 2026-09-30T12:05:00Z
```

Deterministic offline Stage 14 evidence can be generated in a temporary
fixture and analyzed with `--simulate all-success` or another existing
offline fault scenario. No command in this readiness workflow performs MDD,
DNS, live-network, PostgreSQL, scheduler, target-activation, or Power BI
activity. A live-research evidence file may be inspected with the same
read-only adapter; analysis does not start Stage 15B or mutate the file.

## Explicit non-goals

This readiness iteration does not define failure, retry, timeout, coverage,
parser-gap, or heartbeat-duration thresholds. It does not install/configure
PostgreSQL or create a PostgreSQL adapter, create production database objects,
materialize production targets, create a sampling-era timestamp, deploy a
scheduler, modify Power BI, start live Stage 15, or begin Stage 16.

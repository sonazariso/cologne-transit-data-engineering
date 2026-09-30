# Stage 3B — Collection Strategy & Cadence Contract

**Status:** `COMPLETE — APPROVED DESIGN CONTRACT — ACTIVATION DEFERRED`<br>
**Completion date:** `2026-09-28`<br>
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **COMPLETE**

This document closes Stage 3B as a collection and cadence design contract. It
does not activate a production collector, choose final station tiers or
intervals, create a scheduler, create database objects, or authorize a new
polling run.

## Decision boundary

| Classification | Stage 3B result |
|---|---|
| **Validated source fact** | MDD/TRIAS supports targeted stop requests and exposes timetable, estimate, call-chain, bay/platform, Situation, SituationFullRef, provider-reported service/reason text, and operational-status evidence in the bounded evidence already recorded. GTFS-RT supplies a broad snapshot with TripUpdates, ServiceAlerts, schedule-relationship states such as `SKIPPED`/`NO_DATA` when present, and a static identity bridge that remains partial. |
| **Approved design decision** | MDD/TRIAS is the primary targeted realtime loop; GTFS-RT is the complementary broad-snapshot loop. The loops are logically separate and are reconciled only after source-faithful ingestion. MDD cadence is tiered, quota-aware, and versioned as a Sampling Policy/Sampling Era. |
| **Deferred implementation decision** | Final station-tier membership, exact intervals, retry values, timeout values, persistence schema, scheduler, collector code, and raw-payload duration remain for later collector-readiness and database stages. |
| **Open implementation risk** | Actual source-provided `RecordedAtTime` is still unvalidated; the conservative MDD quota ceiling is shared/operationally uncertain; coverage, runtime, overlap, event density, and storage must be measured before activation. |

## Separate collection loops

The future architecture uses two independent loops:

```text
MDD target planner -> bounded MDD requests -> MDD source observations

GTFS cycle        -> one feed fetch      -> GTFS-RT source observations

MDD observations + GTFS-RT observations
                 -> later canonical reconciliation / association
```

- A successful MDD observation remains valid when the GTFS-RT loop fails.
- A successful GTFS-RT snapshot remains valid when an MDD target request or
  the MDD loop fails.
- The two sources must not be required to succeed in the same transaction.
- Source-specific errors, missingness, timestamps, identifiers, and reported
  evidence remain visible after ingestion.
- Cross-source normalization and matching happen only after the two
  source-faithful observation streams exist.

The source roles are not interchangeable: MDD is the **PRIMARY targeted
Cologne/VRS realtime source** and GTFS-RT is the **COMPLEMENTARY/SECONDARY
broad-snapshot source**. Neither source replaces the other.

## MDD collection strategy

The later MDD collector must follow these rules:

- issue targeted station/stop requests rather than a single uncontrolled
  network-wide request;
- count each logical request and every retry against the quota budget;
- keep the API key outside code and source control, supplied through external
  configuration or a secret store;
- apply an explicit request timeout to every attempt;
- use bounded retries only; no uncontrolled retry loop, retry storm, or
  fallback endpoint is permitted;
- record `ObservedAt`/`CapturedAt` independently of every source event time;
- preserve request parameters, target identity, response/transport outcome,
  and collector lineage as audit metadata;
- append source observations rather than replacing history;
- retain successful source values when a later response omits the event or
  returns the field as NULL;
- preserve `TimetabledTime`, `EstimatedTime`, nullable source-only
  `RecordedAtTime`, call scope, bay/platform values, Situation evidence,
  operational flags, and source paths independently; and
- never replay, fabricate, or backfill a missed realtime observation from a
  later request.

`RecordedAtTime` remains source-only, nullable, and never synthesized from an
estimate, timetable, collector timestamp, or disappearance. `ObservedAt` is
collector lineage only. Situation and cause evidence remains append-only
historical evidence, including versions and association scope.

## GTFS-RT collection strategy

The later GTFS-RT collector must follow these rules:

- fetch the feed once per collection cycle;
- never issue one GTFS-RT request per station;
- parse the single feed snapshot into the relevant source observations and
  alert/entity evidence;
- preserve the feed-header timestamp separately from collector
  `ObservedAt`/`CapturedAt`;
- preserve TripUpdate timestamps and other provider metadata separately from
  event timing fields;
- do not erase historical evidence when a later feed snapshot no longer
  contains an event, update, alert, or entity;
- retain only the necessary normalized source-faithful evidence long-term by
  default; and
- leave the duration and operational storage policy for full raw protobuf
  snapshots to a later explicit decision.

An absent update is not an automatic on-time, served, cancelled, skipped, or
service-absent conclusion. An explicit source-reported `SKIPPED` or `NO_DATA`
state remains distinct from a missing update.

## Cadence policy

Stage 3B approves a **CADENCE POLICY**, not a fixed production timetable.

1. MDD cadence is tiered and quota-aware.
2. Exact production tier membership and final intervals require later
   operational readiness measurements.
3. Normal scheduled MDD traffic must be planned inside the approved
   operational envelope of **83–84%** of the conservative `250,000`-request
   monthly ceiling: **207,500–210,000 MDD HTTP requests/month**. The full
   ceiling must not be treated as available normal production capacity.
4. Preserve **40,000–42,500 requests/month**, approximately **16–17%**, as
   unallocated headroom for retries, diagnostics, manual investigations,
   unexpected longer calendar months, and operational changes.
5. Every active cadence is versioned as a `SamplingPolicy` / `SamplingEra`.
6. Changing cadence must never rewrite historical observations.
7. Each observation must retain enough collector metadata to identify the
   Sampling Policy under which it was collected.
8. Analytics must not compare periods with materially different sampling
   policies without acknowledging the sampling change.
9. MDD requests must not overlap uncontrollably; target ownership, run
   coordination, or an equivalent overlap guard is required before activation.
10. Retry policy is finite, quota-accounted, and included in the active budget.
11. GTFS-RT cadence is independent of MDD station cadence because one
    GTFS-RT request supplies the broad snapshot.

Historical Stage 3 interpretation note: the `207,500–210,000` / 83–84%
language above was the planning target approved at that stage. Stage 13's
explicitly approved lower-load design supersedes any interpretation of
`207,500` as a minimum utilization requirement. Stage 13 now uses `210,000` as
the normal operational maximum, `207,500` as the automatic-retry protection
boundary, and no minimum monthly utilization gate.

The historical candidate of 10 stations every 5 minutes, 20 every 10 minutes,
and 20 every 30 minutes is **not approved as production truth**. Its previously
calculated load was 280 MDD logical requests/hour, approximately 201,600
requests per 30-day month or 208,320 per 31-day month, before retries and
headroom. That consumes too much of the conservative documented
`250,000`-request/month ceiling to activate without operational benchmarking.

The final production budget must account for all measurable callers sharing the
credential. A budget that counts only successful base requests is insufficient;
initial attempts, bounded retries, diagnostics, manual investigations,
calendar-month variation, overlap protection, and reserved headroom must be
visible in the approval record. Stage 13 owns the final production station
tiers, cadence, staggering, retry policy, overlap analysis, and full monthly
projection. Its explicitly approved lower-load design supersedes the historical
interpretation of the `207,500–210,000` band as a required operating range.

## Sampling-era and retention semantics

The Sampling Policy/Era is collector lineage, not a correction to historical
data. When a policy changes:

- prior observations remain unchanged;
- the new policy receives a new version or era identifier;
- observations retain the policy identifier and collection metadata; and
- downstream comparisons disclose materially different sampling intensity or
  target membership.

Long-term persistence is source-faithful normalized evidence plus lineage and
audit metadata. Full raw MDD responses and full raw GTFS-RT protobuf feeds
  remain short-term diagnostic options only; no permanent raw-payload policy or
  retention duration is approved here.

## Activation/readiness gate

Final intervals and station tiers belong to the later collector-readiness /
activation stage. Before activation, measure and document at least:

- request duration, including P50/P95/P99 runtime;
- bounded retry frequency and causes;
- station event density;
- `NumberOfResults` coverage;
- quota usage and all known callers;
- overlap and phase-staggering behavior;
- bandwidth and storage impact; and
- source-specific success, parse, persistence, and observation-gap rates.

Stage 3B is complete because these rules and boundaries are approved. It does
not imply that a final 50-station schedule or any production interval is
active.

## Carried-forward items

- `RecordedAtTime` has not been observed and validated in the available
  source evidence; Actual Delay and prediction accuracy versus actual remain
  unapproved.
- Final MDD tier membership and intervals are not activated.
- Final raw-payload retention duration is not decided.
- The production collector, scheduler, persistence schema, and PostgreSQL
  implementation are not created.
- No replay or fabrication of missed realtime snapshots is permitted.

Related documents:

- [Stage 3A evidence audit](03a-disruption-cause-alert-evidence-audit.md)
- [Stage 3C cross-source matching design](03c-cross-source-matching-design.md)
- [Realtime collector requirements](../collector-design/realtime-collector-requirements.md)
- [Future observation and persistence contract](../source-contracts/future-observation-persistence-contract.md)

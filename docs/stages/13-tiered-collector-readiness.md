# Stage 13 — Tiered Collector Readiness

**Date:** `2026-09-30`  
**Status:** `IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED`
**Production status:** `NOT STARTED`

The authoritative machine-readable evidence is
[`13-tiered-collector-readiness-evidence.json`](13-tiered-collector-readiness-evidence.json).
The implementation is an evidence/design record only. It creates no
production target rows, scheduler, database object, Windows Scheduled Task,
Power BI artifact, or Tiered50 activation.

The prior D-046 closeout is retained as historical evidence but is superseded
and is not valid as final user approval. The machine-readable
`user_decision_gate` is authoritative: technically evaluated options are
recorded separately from user-approved design choices, and all material
production approval flags remain false.

## Decision summary

Stage 13 evidence is ready for user review. The fixed Stage 12 panel is reused
exactly as `TIERED50_PANEL_V1`; no panel membership was changed and Stage 12
`SelectionRank` was not treated as a production tier.

The technically evaluated cadence option is:

| Tier | Stations | Cadence | Logical requests/hour |
|---|---:|---:|---:|
| A | 10 | every 5 minutes | 120 |
| B | 20 | every 15 minutes | 80 |
| C | 20 | every 30 minutes | 40 |
| **Total** | **50** | — | **240** |

The exact Stage 12 static snapshot supports this evaluated assignment and the
evaluated cadence-window result-limit option `NumberOfResults = 30`. Bounded
live evidence also technically evaluates `MaximumConcurrency = 2`. Neither is
a user-approved production decision. The provider's theoretical maximum
result limit remains intentionally unknown and is recorded as
`PROVIDER_MAXIMUM_NOT_ESTABLISHED`.

## 1. Stage 12 dependency and source boundary

The approved Stage 12 panel dependency is satisfied:

- panel version: `TIERED50_PANEL_V1`;
- exactly 50 unique parent stations;
- all seven Legacy7 continuity anchors retained;
- exact candidate-universe evidence remains the Stage 12 source of panel
  membership; and
- no Stage 12 redesign, replacement, cutover, or historical deletion occurred.

Static density used the exact Stage 12 feed snapshot:

| Field | Value |
|---|---|
| Feed version | `VERSION__20260930_0053` |
| ZIP SHA-256 | `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147` |
| Feed period | `2026-09-02` through `2026-12-12` |
| Active service dates | `102` |
| Station/service-day density rows | `5,100` |
| Raw ZIP retention | not committed or retained by Stage 13 |

The verified ZIP was read from temporary storage only. A newer or different
feed was not substituted.

## 2. Static scheduled-event density and deterministic tiers

For each approved parent station, GTFS `stop_times.arrival_time` rows for the
validated Stage 12 child stops were expanded against `calendar.txt` and
`calendar_dates.txt`. For each active service day and each forward window, the
analysis calculated the maximum scheduled arrivals in the half-open interval
`[scheduled arrival anchor, anchor + window)`.

This is schedule planning evidence, not realtime response coverage. It does
not claim that the provider returns every scheduled event or that a live
response is complete.

The distribution below is across the 50 station-level P95 values. The separate
machine-readable artifact also retains the 5,100 station/service-day maxima.

| Forward window | Median | P90 | P95 | P99 | Max |
|---:|---:|---:|---:|---:|---:|
| 5 min | 7.5 | 12.1 | 14.1 | 17.53 | 19 |
| 10 min | 11 | 20 | 21.55 | 26.02 | 27 |
| 15 min | 17.5 | 28.3 | 33 | 42.04 | 44 |
| 30 min | 31 | 50.4 | 58.3 | 76.02 | 77 |

The evaluated tier assignment sorts only by measured 30-minute station-day P95,
then 15-minute P95, then 5-minute P95, with source parent ID as the stable
tie-breaker. It does not use `SelectionRank`.

| Density rank | Tier | Station | Source parent | 5m P95 | 15m P95 | 30m P95 |
|---:|:---:|---|---|---:|---:|---:|
| 1 | A | Köln Mülheim Wiener Platz | `de:05315:19211` | 19 | 44 | 77 |
| 2 | A | Köln Neumarkt | `de:05315:11111` | 15 | 40 | 75 |
| 3 | A | Köln Heumarkt | `de:05315:11110` | 13 | 33 | 61 |
| 4 | A | Köln Bf Mülheim | `de:05315:19201` | 16 | 33 | 55 |
| 5 | A | Köln Bf Deutz/Messe LANXESS arena | `de:05315:11907` | 13 | 31 | 54 |
| 6 | A | Köln Breslauer Platz/Hbf | `de:05315:11212` | 12 | 28 | 50 |
| 7 | A | Köln Rudolfplatz | `de:05315:11610` | 10 | 24 | 42 |
| 8 | A | Köln Aachener Str./Gürtel | `de:05315:13411` | 10 | 23 | 42 |
| 9 | A | Köln Friesenplatz | `de:05315:11710` | 9 | 23 | 42 |
| 10 | A | Köln Ebertplatz | `de:05315:11810` | 10 | 23 | 41 |
| 11 | B | Köln Chlodwigplatz | `de:05315:11411` | 10 | 22 | 39 |
| 12 | B | Köln Appellhofplatz | `de:05315:11209` | 8 | 20 | 36 |
| 13 | B | Köln Dom/Hbf | `de:05315:11211` | 7 | 19 | 36 |
| 14 | B | Köln Kalk Post | `de:05315:18010` | 8 | 19 | 35 |
| 15 | B | Köln Messe/Deutz Bf | `de:05315:11901` | 9 | 19 | 34 |
| 16 | B | Köln Barbarossaplatz | `de:05315:11511` | 9 | 19 | 33 |
| 17 | B | Köln Buchheim Herler Str. | `de:05315:19312` | 9 | 19 | 33 |
| 18 | B | Köln Hansaring | `de:05315:11801` | 7 | 18 | 33 |
| 19 | B | Köln Kalk Kapelle | `de:05315:18011` | 7 | 18 | 33 |
| 20 | B | Köln Chorweiler | `de:05315:16101` | 11 | 21 | 32 |
| 21 | B | Köln Bf Ehrenfeld | `de:05315:14201` | 9 | 19 | 32 |
| 22 | B | Köln Wahn S-Bahn | `de:05315:17701` | 9 | 19 | 32 |
| 23 | B | Köln Hbf | `de:05315:11201` | 9 | 18.95 | 32 |
| 24 | B | Köln Neusser Str./Gürtel | `de:05315:15011` | 10 | 19 | 31 |
| 25 | B | Köln Bocklemünd | `de:05315:14611` | 9 | 19 | 31 |
| 26 | B | Köln Poststr. | `de:05315:11112` | 7 | 17 | 31 |
| 27 | B | Köln Venloer Str./Gürtel | `de:05315:14211` | 7 | 17 | 30 |
| 28 | B | Köln Moltkestr. | `de:05315:11611` | 7 | 16 | 30 |
| 29 | B | Köln Porz Markt | `de:05315:17311` | 8 | 17 | 29 |
| 30 | B | Köln Ubierring | `de:05315:11410` | 5 | 14 | 28 |
| 31 | C | Köln Deutz Technische Hochschule | `de:05315:11914` | 6 | 14 | 27 |
| 32 | C | Köln Rodenkirchen Bf | `de:05315:12711` | 7 | 15 | 26 |
| 33 | C | Köln Stammheim S-Bahn | `de:05315:19801` | 8 | 15 | 25 |
| 34 | C | Köln Sülzgürtel | `de:05315:13211` | 6 | 14 | 25 |
| 35 | C | Köln Stammheimer Ring | `de:05315:19851` | 7 | 15 | 24 |
| 36 | C | Köln Deutzer Freiheit | `de:05315:11909` | 6 | 13 | 24 |
| 37 | C | Köln Reichenspergerplatz | `de:05315:11809` | 6 | 13 | 23 |
| 38 | C | Köln Weißhausstr. | `de:05315:13111` | 8 | 15 | 22 |
| 39 | C | Köln Weiden Zentrum | `de:05315:13708` | 6 | 12 | 22 |
| 40 | C | Köln Amsterdamer Str./Gürtel | `de:05315:15121` | 7 | 13 | 21 |
| 41 | C | Köln Merheim | `de:05315:18511` | 6 | 13 | 21 |
| 42 | C | Köln Eifelwall/Stadtarchiv | `de:05315:11514` | 5.95 | 12 | 21 |
| 43 | C | Köln Dellbrück Hauptstr. | `de:05315:19511` | 6 | 11 | 20 |
| 44 | C | Köln Worringen S-Bahn | `de:05315:16601` | 6 | 11 | 19 |
| 45 | C | Köln Sülzburgstr. | `de:05315:13113` | 5 | 10 | 17 |
| 46 | C | Köln Junkersdorf | `de:05315:13611` | 5 | 10 | 17 |
| 47 | C | Köln Sürth Bf | `de:05315:12811` | 4 | 9 | 16 |
| 48 | C | Köln Slabystr. | `de:05315:15113` | 4 | 9 | 15 |
| 49 | C | Köln Klettenbergpark | `de:05315:13221` | 4 | 9 | 14 |
| 50 | C | Köln Thielenbruch | `de:05315:19512` | 3 | 7 | 12 |

For the technically evaluated tier-cadence option, `NumberOfResults = 30` is
above the exact-feed station-level P95 density for all 50 cadence windows. The
machine-readable coverage check is `50/50`; this remains static planning
evidence only and is not a user approval.

## 3. Cadence and quota decision

The technically evaluated option is `10/20/20` with `5/15/30` minute
intervals. The old `10/20/20` with `5/10/30` remains comparison evidence only.
Neither cadence is a final user-approved design.

| Plan | Requests/hour | 30-day base | 31-day base | N30 static P95 fit | 31-day total at 10% stress + Legacy7 reserve + 100 diagnostics |
|---|---:|---:|---:|---|---:|
| Historical 10/20/20 at 5/10/30 | 280 | 201,600 | 208,320 | 50/50 | 238,180 — fails |
| **Technically evaluated 10/20/20 at 5/15/30** | **240** | **172,800** | **178,560** | **50/50** | **205,444 — passes** |
| 10/15/25 at 5/10/30 | 260 | 187,200 | 193,440 | 0/50 | 221,812 — fails |
| 8/20/22 at 5/15/30 | 220 | 158,400 | 163,680 | 50/50 | 189,076 — passes |
| 8/12/30 at 5/10/30 | 228 | 164,160 | 169,632 | 0/50 | 195,624 — passes |

The evaluated option retains useful five-minute coverage for the ten densest
stations, gives the next 20 measured-density stations a 15-minute cadence, and
keeps the lower-density 20 at 30 minutes. It leaves more headroom than the
historical candidate without minimizing observation coverage solely to reduce
API usage. The user must approve this material cadence/tier choice before it is
recorded as final.

The technically evaluated retry option uses `MaxAttempts = 1` and zero
automatic retries, consistent with the bounded request contract. A failed target is
independently recorded and waits for the next current logical bucket; it is not
retried immediately and missed historical buckets are not replayed.

| Scenario | 30-day conservative total | 31-day conservative total | 31-day headroom to 210,000 |
|---|---:|---:|---:|
| Normal zero-retry policy | 181,540 | 187,588 | 22,412 |
| Historical Legacy additional-attempt share, sensitivity only | 185,253 | 191,425 | 18,575 |
| 5% stress sensitivity | 190,180 | 196,516 | 13,484 |
| 10% stress sensitivity | 198,820 | 205,444 | 4,556 |

Current shared-account Legacy Collector usage was not observable through an
established read-only database mechanism. It is not reported as zero. The
documented full Legacy7 baseline is reserved conservatively as `8,640` logical
requests for 30 days and `8,928` for 31 days. Inactive repository diagnostics
have no standing recurring reserve; the Stage 13 hard diagnostic budget of 100
HTTP attempts is included in the historical conservative reconciliation above.
The separately gated corrected comparison was explicitly approved and consumed
24 attempts; it does not silently change that historical ceiling. Its
sanitized result was `NETWORK_OR_DNS_FAILURE` for all 24 attempts, with no
standing recurring reserve or valid coverage evidence created.

Known repository callers and the external Legacy consumer are listed in the
machine-readable `credential_consumers` section. No other normal production
caller was found in repository evidence. Current shared-account usage remains
an activation precondition for a later implementation stage.

## 4. Technically evaluated `NumberOfResults` evidence and correction gate

The historical bounded live comparison used one fixed `DepArrTime` anchor per
station comparison (the retained anchor was `2026-09-30T10:40:59Z`), sequential
requests, `MaxAttempts = 1`, and zero retries. Six representatives were
selected deterministically:

| Role | Station | Density rank | Tier | Mode/interchange coverage |
|---|---|---:|:---:|---|
| High density | Köln Mülheim Wiener Platz | 1 | A | Regional/Bus, Tram, KVB Bus |
| High-density Legacy7 | Köln Heumarkt | 3 | A | Regional/Bus, Tram, KVB Bus |
| Middle density | Köln Neusser Str./Gürtel | 24 | B | Tram, KVB Bus |
| Low density | Köln Thielenbruch | 50 | C | Tram |
| Interchange diversity | Köln Bf Mülheim | 4 | A | Regional/Bus, RB, RE, S-Bahn, Tram, KVB Bus |
| Urban bus/tram diversity | Köln Neumarkt | 2 | A | Tram, KVB Bus |

Each result limit was requested for all six representatives. The historical
sanitized records preserve HTTP 200, parser-success, valid TRIAS response,
returned-event count, saturation, `StopPointRef` consistency, and duration
evidence. Their old temporal-span values are retained for audit history but are
marked `INVALID_FOR_FORWARD_COVERAGE_DECISION` because they mixed
PreviousCall, ThisCall, OnwardCall, and departure timings.

| Historical requested N | Requests | Saturated | HTTP 200 / valid | Historical span status | Duration P50 / P95 ms |
|---:|---:|---:|---:|---|---:|
| 5 | 6 | 6 | 6 / 6 | `INVALID_FOR_FORWARD_COVERAGE_DECISION` | 446.397 / 502.147 |
| 10 | 6 | 6 | 6 / 6 | `INVALID_FOR_FORWARD_COVERAGE_DECISION` | 509.750 / 1,084.616 |
| 20 | 6 | 6 | 6 / 6 | `INVALID_FOR_FORWARD_COVERAGE_DECISION` | 537.159 / 581.138 |
| **30** | **6** | **6** | **6 / 6** | `INVALID_FOR_FORWARD_COVERAGE_DECISION` | **682.994 / 722.676** |

All historical tested limits saturated in that bounded sample; saturation is
retained honestly and is not treated as a provider-maximum discovery. The
corrected diagnostic was explicitly approved and then executed as exactly 24
additional sequential attempts across the same six representatives and four
limits. Every corrected attempt ended with sanitized
`NETWORK_OR_DNS_FAILURE`; there were no HTTP response statuses, returned
results, saturations, or valid THIS-call arrivals to calculate coverage.

The corrected metric uses only each returned `StopEventResult`'s ThisCall
arrival timing:

`forward coverage = latest relevant ThisCall arrival at or after requested DepArrTime - requested DepArrTime`

PreviousCall and OnwardCall timings, departure timings, and route-context
departure timings must remain contextual evidence only. Returned events before
the anchor are counted separately and cannot inflate forward coverage.

The corrected run used one fixed comparable anchor,
`2026-09-30T11:54:56Z`, zero diagnostic retries, `MaxAttempts = 1`, no raw
payload persistence, and the immediate 401/403 stop rule. Its cumulative
accounting is 90 historical attempts + 24 approved attempts = 114. The
historical 100-attempt ceiling remains unchanged as a historical budget; the
separate approved extension is recorded explicitly.

### Corrected NumberOfResults comparison

| Policy | Exact-feed static P95 support | Corrected live evidence | Decision |
|---|---:|---|---|
| Fixed N=20 | 36/50 cadence windows | 0/24 valid responses; no coverage or saturation | **UNDECIDED** |
| Fixed N=30 | 50/50 cadence windows | 0/24 valid responses; no coverage or saturation | **UNDECIDED** |
| Tier-specific A=20, B=30, C=30 | 50/50 cadence windows | N20/N30 probes had 0/24 valid responses | **UNDECIDED** |

The tier-specific A20/B30/C30 policy is the simpler static candidate supported
by the exact-feed density evidence, but it is not supported by corrected live
evidence because the diagnostic received no valid response. No clearly superior
alternative can be selected from this run. NumberOfResults remains undecided;
N=30 is not finalized and no provider-maximum claim is made.

### Cadence boundary review

The requested comparison says “two stations differ,” but the deterministic
density-ranked cardinalities imply four changed assignments: two A→B and two
B→C. The exact boundary records are:

| Rank | StopPointRef | Station | 10/20/20 tier | 8/20/22 tier | Window P95 (5/10/15/30) | Routes | Modes | Child stops | Legacy7 | Boundary reason |
|---:|---|---|:---:|:---:|---|---|---|---:|:---:|---|
| 9 | `de:05315:11710` | Köln Friesenplatz | A | B | 9/14/23/42 | 12, 15, 16, 172, 173, 18, 3, 4, 5 | Tram; KVB Bus | 6 | No | A shrinks from 10 to 8; rank 9 enters B |
| 10 | `de:05315:11810` | Köln Ebertplatz | A | B | 10/15/23/41 | 127, 12, 140, 15, 16, 18 | Tram; KVB Bus | 6 | No | A shrinks from 10 to 8; rank 10 enters B |
| 29 | `de:05315:17311` | Köln Porz Markt | B | C | 8/12/17/29 | 151, 152, 154, 160, 161, 162, 165, 166, 7 | Tram; KVB Bus | 4 | Yes | C grows from 20 to 22; rank 29 enters C |
| 30 | `de:05315:11410` | Köln Ubierring | B | C | 5/10/14/28 | 116, 133, 142, 15, 16, 18 | Tram; KVB Bus | 5 | No | C grows from 20 to 22; rank 30 enters C |

The full canonical route references, route-by-mode mappings, daily scheduled
arrival P95 values, and child-stop counts are retained in
`cadence_boundary_review` in the machine-readable evidence.

Provider status remains `PROVIDER_MAXIMUM_NOT_ESTABLISHED`. The design does not
claim guaranteed full realtime coverage from static schedules or a bounded
sample.

Stage 13 has consumed 114 cumulative MDD HTTP/transport attempts: 90
historical attempts plus exactly 24 approved corrected attempts. The current
corrected run has no valid live response evidence. The final evidence retains
sanitized historical and corrected records only; no raw payload, request
header, or credential was persisted.

## 5. Bounded concurrency and heartbeat feasibility

The cap-2 diagnostic executed three pairs, six requests total, with no retry.
All six retained requests were valid HTTP 200/parseable responses. There was
no 429, 5xx, timeout, or transport-pressure signal, and completion of one pair
member was not dependent on the other. This remains a technically evaluated
option pending user approval, not a provider-wide rate-limit claim.

| Population | Count | P50 ms | P90 ms | P95 ms | P99 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|
| Cap-2 request durations | 6 | 758.949 | 883.496 | 929.695 | 966.654 | 975.894 |
| Cap-2 pair/batch durations | 3 | 791.728 | 940.051 | 958.591 | 973.423 | 977.131 |

The Stage 13 technically evaluated concurrency option is:

`MaximumConcurrency = 2`

The complete six-heartbeat, 30-minute super-cycle is:

| Heartbeat | Tier A | Tier B | Tier C | Total due |
|---:|---:|---:|---:|---:|
| 1 | 10 | 7 | 4 | 21 |
| 2 | 10 | 7 | 4 | 21 |
| 3 | 10 | 6 | 3 | 19 |
| 4 | 10 | 7 | 3 | 20 |
| 5 | 10 | 7 | 3 | 20 |
| 6 | 10 | 6 | 3 | 19 |
| **Total / 30 min** | **60** | **40** | **20** | **120** |

The maximum due count is 21. At cap 2 this is 11 measured-duration batches.
Using the observed cap-2 P95 batch duration gives an estimated
`10,544.501 ms` heartbeat execution time; the observed maximum-based estimate
is `10,748.441 ms`. Both are below the 300-second heartbeat interval, so the
the technically evaluated option is feasible with substantial measured
duration margin. It is not an approved production concurrency decision.

## 6. Phase-assignment contract

The phase contract is design-only and does not materialize production target
rows. It is deterministic and auditable:

- identity input is immutable `SamplingTargetId` / source parent identity;
- assignment key is `SHA-256(panel version + tier + immutable SamplingTargetId)`;
- stable identity-hash ordering fills balanced phase capacities;
- Tier A has one phase of 10 targets;
- Tier B has three phases with target counts `7/7/6`;
- Tier C has six phases with target counts `4/4/3/3/3/3`; and
- mutable `SelectionRank` is not used at runtime.

The exact 50 phase assignments are retained in the machine-readable evidence
under `phase_staggering.phase_assignments`. Stage 14 may materialize them only
under the approved panel/version boundary.

## 7. Claims, overlap, failure isolation, and continuity

The future execution contract under review remains:

- unique execution identity: `(SamplingTargetId, LogicalDueBucket)`;
- `LogicalDueBucket = floor(current UTC epoch seconds / 300)`;
- atomic durable claim with a unique-key conflict as the duplicate decision;
- only the winning claim may issue HTTP;
- stale/incomplete `RUNNING` state remains independently observable;
- no catch-up or historical replay;
- one failed target does not abort remaining due targets; and
- every target independently records bucket, HTTP status, attempt count,
  duration, parser status, persistence status, and error metadata.

All Legacy7 history and existing Legacy7 target identities remain preserved.
The future implementation must distinguish the `Legacy7` and `Tiered50`
sampling eras and must not infer a naive before/after raw-volume change.

## 8. Completion boundary

Stage 13 is not complete. Evidence is ready for explicit user review, but no
material production design choice may be recorded as final until the
`user_decision_gate` is approved.

- Stage 14: **NOT STARTED**.
- Tiered50 activation: **NOT PERFORMED**.
- 7→50 cutover: **NOT PERFORMED**.
- Windows Scheduled Tasks: **NOT CREATED**.
- Production database objects: **NOT CREATED**.
- Power BI: **NOT MODIFIED**.
- Legacy7 history: **PRESERVED**.

The current shared Legacy audit remains an explicit activation precondition,
despite the conservative reserve used in the technically evaluated quota
option. D-046 remains historical and superseded; a new final decision may be
recorded only after explicit user approval.

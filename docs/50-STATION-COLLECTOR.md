# Fixed 50-station MDD/TRIAS collector — approved implementation contract

**Panel:** `TIERED50_PANEL_V1` · **Current service:** stopped · **Source of truth:** [`config/tiered50_runtime_manifest.json`](../config/tiered50_runtime_manifest.json). This document is regenerated from the **50 actual manifest entries**; it is not a new station list or the Stage 12 ranking order. The config file is an *offline template*: `activation_status=DEFERRED_NOT_ACTIVE` and `sampling_target_id=null` do **not** mean SQL target materialization never occurred. Historical SQL deployment materialized seven existing identities (IDs 1–7) plus 43 new identities (IDs 8–50), but no stable continuous Tiered50 service resulted.

## 1. Selection rationale and identity

Stage 12 screened **864 valid Cologne parent-station candidates** from VRS static GTFS `VERSION__20260930_0053`. Every accepted parent had scheduled child-stop evidence. Its method preserved seven Legacy7 continuity anchors, covered selected transport modes, sampled up to 25 distinct geographic cells (0.025° latitude × 0.04° longitude) and filled remaining seats by a deterministic operational-richness ranking. Selection rank is **not** the runtime tier ranking. Stage 13 independently ranked planned arrival density (30-minute station-day rolling P95, then 15-min/5-min P95 and parent-ID tie-break) for tier assignment. Scheduled events are not passenger counts.

The fixed provider source identity is the **parent `StopPointRef`**. No automatic replacement, reassignment or unknown child-stop fallback is authorized. Stage 12 made a bounded *compatibility* request for each of the 50 (`NumberOfResults=1`, `MaxAttempts=1`), returning 50 HTTP 200/parse-success-compatible outcomes, not a 50-station long-duration production proof.

## 2. Tier, quota and schedule definition

| Tier | Stations | Cadence | Requested `NumberOfResults` | Nominal per 30-minute cycle |
| --- | ---: | ---: | ---: | ---: |
| A | 8 | every 5 min | 20 | 48 |
| B | 20 | every 15 min | 30 | 40 |
| C | 22 | every 30 min | 30 | 22 |
| **Total** | **50** | **6 phases** | — | **110** |

Phase `0..5` corresponds to six successive **UTC five-minute** sub-buckets within a 30-minute supercycle, not a local DST-dependent schedule. Every phase includes all eight Tier A stations. Tier B `phase_index=0,1,2` fires in phases `(0,3)`, `(1,4)` or `(2,5)` respectively; Tier C `phase_index=0..5` fires once. Actual due counts: **`19, 18, 18, 18, 19, 18`**. Maximum concurrency is **2**, and the policy allows at most **2 HTTP attempts per due target** (an initial request plus one eligible transient retry). A skip is **not** a successful acquisition and may consume zero HTTP requests.

Normal maximum planning ceiling 210,000 requests/month, automatic retry guard 207,500, provider shared ceiling 250,000; nominal 30-day operation 158,400 before retries/external consumers. A permit must be authorized immediately before transmission; uncertain provider reset means fail closed. See [quota and storage](QUOTA-CAPACITY-RETENTION.md).

## 3. Full source manifest — every fixed station

This is **manifest order**, not Stage 12 rank. `Phase` is the tier-relative index from the checked-in config; `Legacy7` marks original source identity continuity, not an active fallback writer.

| # | Station | Parent StopPointRef | Tier | Min | N | Phase | Legacy7 |
| ---: | --- | --- | :---: | ---: | ---: | ---: | :---: |
| 1 | Köln Heumarkt | `de:05315:11110` | A | 5 | 20 | 0 | Yes |
| 2 | Köln Neumarkt | `de:05315:11111` | A | 5 | 20 | 0 | No |
| 3 | Köln Breslauer Platz/Hbf | `de:05315:11212` | A | 5 | 20 | 0 | No |
| 4 | Köln Rudolfplatz | `de:05315:11610` | A | 5 | 20 | 0 | No |
| 5 | Köln Bf Deutz/Messe LANXESS arena | `de:05315:11907` | A | 5 | 20 | 0 | No |
| 6 | Köln Aachener Str./Gürtel | `de:05315:13411` | A | 5 | 20 | 0 | No |
| 7 | Köln Bf Mülheim | `de:05315:19201` | A | 5 | 20 | 0 | Yes |
| 8 | Köln Mülheim Wiener Platz | `de:05315:19211` | A | 5 | 20 | 0 | No |
| 9 | Köln Poststr. | `de:05315:11112` | B | 15 | 30 | 1 | No |
| 10 | Köln Hbf | `de:05315:11201` | B | 15 | 30 | 2 | Yes |
| 11 | Köln Appellhofplatz | `de:05315:11209` | B | 15 | 30 | 1 | No |
| 12 | Köln Dom/Hbf | `de:05315:11211` | B | 15 | 30 | 1 | No |
| 13 | Köln Chlodwigplatz | `de:05315:11411` | B | 15 | 30 | 1 | No |
| 14 | Köln Barbarossaplatz | `de:05315:11511` | B | 15 | 30 | 0 | No |
| 15 | Köln Moltkestr. | `de:05315:11611` | B | 15 | 30 | 1 | No |
| 16 | Köln Friesenplatz | `de:05315:11710` | B | 15 | 30 | 2 | No |
| 17 | Köln Hansaring | `de:05315:11801` | B | 15 | 30 | 1 | No |
| 18 | Köln Ebertplatz | `de:05315:11810` | B | 15 | 30 | 0 | No |
| 19 | Köln Messe/Deutz Bf | `de:05315:11901` | B | 15 | 30 | 0 | No |
| 20 | Köln Bf Ehrenfeld | `de:05315:14201` | B | 15 | 30 | 2 | Yes |
| 21 | Köln Venloer Str./Gürtel | `de:05315:14211` | B | 15 | 30 | 1 | No |
| 22 | Köln Bocklemünd | `de:05315:14611` | B | 15 | 30 | 0 | No |
| 23 | Köln Neusser Str./Gürtel | `de:05315:15011` | B | 15 | 30 | 0 | No |
| 24 | Köln Chorweiler | `de:05315:16101` | B | 15 | 30 | 2 | No |
| 25 | Köln Wahn S-Bahn | `de:05315:17701` | B | 15 | 30 | 0 | No |
| 26 | Köln Kalk Post | `de:05315:18010` | B | 15 | 30 | 0 | No |
| 27 | Köln Kalk Kapelle | `de:05315:18011` | B | 15 | 30 | 2 | No |
| 28 | Köln Buchheim Herler Str. | `de:05315:19312` | B | 15 | 30 | 2 | No |
| 29 | Köln Ubierring | `de:05315:11410` | C | 30 | 30 | 4 | No |
| 30 | Köln Eifelwall/Stadtarchiv | `de:05315:11514` | C | 30 | 30 | 0 | No |
| 31 | Köln Reichenspergerplatz | `de:05315:11809` | C | 30 | 30 | 1 | No |
| 32 | Köln Deutzer Freiheit | `de:05315:11909` | C | 30 | 30 | 3 | No |
| 33 | Köln Deutz Technische Hochschule | `de:05315:11914` | C | 30 | 30 | 5 | No |
| 34 | Köln Rodenkirchen Bf | `de:05315:12711` | C | 30 | 30 | 5 | Yes |
| 35 | Köln Sürth Bf | `de:05315:12811` | C | 30 | 30 | 3 | No |
| 36 | Köln Weißhausstr. | `de:05315:13111` | C | 30 | 30 | 5 | No |
| 37 | Köln Sülzburgstr. | `de:05315:13113` | C | 30 | 30 | 2 | No |
| 38 | Köln Sülzgürtel | `de:05315:13211` | C | 30 | 30 | 3 | No |
| 39 | Köln Klettenbergpark | `de:05315:13221` | C | 30 | 30 | 2 | No |
| 40 | Köln Junkersdorf | `de:05315:13611` | C | 30 | 30 | 4 | No |
| 41 | Köln Weiden Zentrum | `de:05315:13708` | C | 30 | 30 | 0 | No |
| 42 | Köln Slabystr. | `de:05315:15113` | C | 30 | 30 | 5 | No |
| 43 | Köln Amsterdamer Str./Gürtel | `de:05315:15121` | C | 30 | 30 | 2 | No |
| 44 | Köln Worringen S-Bahn | `de:05315:16601` | C | 30 | 30 | 0 | Yes |
| 45 | Köln Porz Markt | `de:05315:17311` | C | 30 | 30 | 4 | Yes |
| 46 | Köln Merheim | `de:05315:18511` | C | 30 | 30 | 2 | No |
| 47 | Köln Dellbrück Hauptstr. | `de:05315:19511` | C | 30 | 30 | 1 | No |
| 48 | Köln Thielenbruch | `de:05315:19512` | C | 30 | 30 | 4 | No |
| 49 | Köln Stammheim S-Bahn | `de:05315:19801` | C | 30 | 30 | 1 | No |
| 50 | Köln Stammheimer Ring | `de:05315:19851` | C | 30 | 30 | 0 | No |

## 4. Execution state machine, guarantees and failure modes

1. macOS launchd scheduled wakeup at local minute `00, 05, ..., 55` provides a trigger **opportunity**, not an exact UTC launch timestamp; process startup time is separately recorded.
2. Runner reads live UTC clock, applies the fixed target manifest/SQL target mapping and claims the logical UTC due bucket and selected targets. Missed past slots are **not replayed** from a later live response.
3. The same absolute five-minute deadline applies to startup, configuration/SQL checks, transport, parsing, persistence and audit. No late reconstruction of an execution budget is allowed.
4. Shared-credential SQL quota/period/permit guard is checked at dispatch and again before each actual HTTP transmission. Failed permits or ambiguous transmission states are not silently forgiven.
5. Eligible HTTP response is parsed, normalized and attributed to one run/audit/snapshot with source hashing and source-time semantics; bounded SQL stored-procedure writes preserve child occurrences and errors/gaps.
6. Deadlines and non-authorized requests create explicit **skip-audit** evidence; no fictional snapshots or backfills. SQL skip-audit inserts use bounded transactional batches and do not overwrite terminal rows on retries.
7. Run finalization and **coverage outcome** are separate. `COMPLETED_WITH_COVERAGE_GAP` can coexist with successful run bookkeeping; never report lifecycle complete as 50-station success.

**Critical capacity defect demonstrated in design:** 19 due targets with concurrency 2 and 30-second per-request worst-case require ten serialized 30s batches = **300s**, already the complete 5-minute interval, excluding 5-second safety margin and SQL overhead. The project did not prove reliable full-phase completion and was stopped rather than quietly overstating operational performance.

## 5. What actually ran, and what did not

- **Yes:** fixed 50-station compatibility; deterministic panel; source/SQL schema and 50-target materialization; two HTTP-backed successes in the first Tiered50 scheduled heartbeat; bounded earlier Legacy7 and research runs; preserved operational/skip evidence.
- **No:** the first Tiered50 scheduled heartbeat did not satisfy cadence/coverage: historical evidence reports **18 target audits, 2 HTTP attempts and 16 skips**. LaunchAgent was disabled; historical Legacy7 was also retired. No successful complete six-bucket or multi-day Tiered50 production run is independently proven.
- **Not a live contract:** the checked-in manifest preserves null production target PK fields by design; the real materialization was applied in SQL at the historical cutover. The live DB has since been right-sized, and current runtime activation should not be inferred from old JSON evidence.

## 6. Evidence and source code references

- Versioned config: [`config/tiered50_runtime_manifest.json`](../config/tiered50_runtime_manifest.json)
- Selection/membership: [`stages/12-50-station-selection.md`](stages/12-50-station-selection.md)
- Tier/rate/cadence proof: [`stages/13-tiered-collector-readiness.md`](stages/13-tiered-collector-readiness.md)
- Runner timing and skipped audit: [`operational/tiered50-scheduled-runtime-contract.md`](operational/tiered50-scheduled-runtime-contract.md)
- Deployment and original failed heartbeat: [`operational/tiered50-deployment-validation-report.md`](operational/tiered50-deployment-validation-report.md)
- Python implementation: [`src/cologne_transit_collector/`](../src/cologne_transit_collector/) · [`scripts/stage_14_collector.py`](../scripts/stage_14_collector.py)
- SQL object/targets: [`sql/mdd-mac-collector/`](../sql/mdd-mac-collector/)

**No command in this document authorizes new MDD polling or production activation.**

# Stage 12 — 50-Station Selection

**Status:** `COMPLETE — APPROVED FIXED 50-STATION PANEL`

**Panel version:** `TIERED50_PANEL_V1`

**Evidence:** [machine-readable Stage 12 evidence](12-50-station-selection-evidence.json)
**Completion decision:** [Stage 12 completion decision](12-50-station-selection-completion-decision.md)

## Scope and boundary

Stage 12 owns fixed parent-station membership and panel versioning only. It does
not approve production tiers, 5/10/30 cadence, `NumberOfResults`, concurrency,
phase staggering, retries, overlap claiming, scheduler activation, Power BI,
or the Stage 14 cutover. `SelectionRank` is a panel rank, not a production
tier.

The final panel contains exactly 50 unique source parent-station identities.
Membership is fixed after approval; a later membership change requires a new
panel version. The historical Legacy7 identities remain in the panel.

## Current source provenance

The approved Stage 4 VRS Global-ID source URL was retrieved once for this stage
because the exact Stage 4 ZIP was not available locally. The raw ZIP was used
read-only from temporary storage and is not committed.

| Field | Value |
|---|---|
| Source | `https://download.vrs.de/gtfs/google_transit_goR.zip` |
| Retrieval timestamp | `2026-09-30T10:42:58Z` |
| Feed publisher | `VRS` |
| Feed version | `VERSION__20260930_0053` |
| Feed date range | `20260902` through `20261212` |
| ZIP size | `35,707,327` bytes |
| ZIP SHA-256 | `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147` |
| Relevant rows | `agency=33`, `routes=957`, `stops=30,409`, `parent stations=9,903`, `trips=126,919`, `stop_times=2,958,966`, `calendar=4,903`, `calendar_dates=133,194` |

The current feed passes the approved Stage 4 structural assumptions: the direct
`stop_id` prefix rule remains `de:05315:`, Cologne children resolve to source
`location_type=1` parents, all 2,276 Cologne child positions have Cologne
parents, there are no orphan parent references, duplicate parent identities,
unknown stop-time references, or unresolved trip references. The approved Stage
4 evidence was not overwritten.

## Candidate population

Candidate identity is established only from the validated source hierarchy:

- parent `stop_id` begins with `de:05315:`;
- parent `location_type=1`;
- child membership comes from the actual `parent_station` field and child
  `location_type=0` rows; and
- scheduled evidence comes from resolved `stop_times.txt` rows for those child
  stop positions.

The resulting population contains 864 unique Cologne parent-station candidates.
All 864 have scheduled service evidence, yielding 1,146,295 parent-level
scheduled child-stop events. There are 2,276 validated child stop positions.
The current scope contains 137 Cologne-serving route IDs. There are no
duplicate, invalid, orphaned, or unresolved final candidate identities.

Scheduled stop-event volume is operational schedule evidence only. It is not
passenger demand, and no passenger-volume or ridership source was used.

## Deterministic selection method

The method is a staged, deterministic policy with no hidden weighted score:

1. retain all seven current Legacy7 continuity anchors;
2. add the strongest candidate for any current Stage 4 core mode not yet
   represented;
3. use validated parent coordinates only as a secondary diversity diagnostic,
   with fixed `0.025°` latitude × `0.04°` longitude cells, selecting one
   operationally strongest candidate per new cell until 25 distinct cells are
   represented; and
4. fill remaining seats using the exposed lexicographic key:
   scheduled event count descending, distinct route count descending, distinct
   approved-mode count descending, active child-stop count descending, service
   days descending, and source parent ID ascending as the tie-breaker.

The current population occupies 57 fixed cells; the panel represents 25 cells
(43.86%). The geographic pass is secondary and does not establish Cologne
identity. The reserve list retains the next 25 candidates by the same exposed
key; it is used only for an explicitly documented compatibility replacement.

## Approved fixed panel

`StopPointRef` below is the source parent-station ID used for the bounded MDD
compatibility request. Modes are inherited from the approved Stage 4
source-faithful classifier. `SelectionRank` is stable panel ordering only.

| Rank | StopPointRef (parent) | Station | Scheduled events | Routes | Modes | Active child stops | Lat | Lon | Selection phase |
|---:|---|---|---:|---:|---|---:|---:|---:|---|
| 1 | `de:05315:11110` | Köln Heumarkt | 11321 | 12 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 13 | 50.935705 | 6.959995 | legacy_anchor |
| 2 | `de:05315:11201` | Köln Hbf | 5359 | 19 | Regional Bahn (RB)<br>Regional Express (RE)<br>S-Bahn | 12 | 50.943033 | 6.958724 | legacy_anchor |
| 3 | `de:05315:12711` | Köln Rodenkirchen Bf | 4136 | 8 | Stadtbahn / Tram<br>Urban / KVB Bus | 8 | 50.890903 | 6.990057 | legacy_anchor |
| 4 | `de:05315:14201` | Köln Bf Ehrenfeld | 4449 | 16 | Regional / Other Bus<br>Regional Bahn (RB)<br>Regional Express (RE)<br>S-Bahn<br>Urban / KVB Bus | 10 | 50.951528 | 6.917273 | legacy_anchor |
| 5 | `de:05315:16601` | Köln Worringen S-Bahn | 1948 | 6 | Regional / Other Bus<br>S-Bahn<br>Urban / KVB Bus | 8 | 51.052379 | 6.851706 | legacy_anchor |
| 6 | `de:05315:17311` | Köln Porz Markt | 4325 | 9 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.884992 | 7.059152 | legacy_anchor |
| 7 | `de:05315:19201` | Köln Bf Mülheim | 12383 | 18 | Regional / Other Bus<br>Regional Bahn (RB)<br>Regional Express (RE)<br>S-Bahn<br>Stadtbahn / Tram<br>Urban / KVB Bus | 15 | 50.957979 | 7.013288 | legacy_anchor |
| 8 | `de:05315:11810` | Köln Ebertplatz | 11330 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 6 | 50.950619 | 6.958794 | geographic_spread |
| 9 | `de:05315:11907` | Köln Bf Deutz/Messe LANXESS arena | 10583 | 17 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 13 | 50.939594 | 6.978799 | geographic_spread |
| 10 | `de:05315:11809` | Köln Reichenspergerplatz | 8310 | 3 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.954025 | 6.964301 | geographic_spread |
| 11 | `de:05315:13411` | Köln Aachener Str./Gürtel | 7800 | 8 | Stadtbahn / Tram<br>Urban / KVB Bus | 10 | 50.936998 | 6.908550 | geographic_spread |
| 12 | `de:05315:11411` | Köln Chlodwigplatz | 7740 | 9 | Stadtbahn / Tram<br>Urban / KVB Bus | 12 | 50.921082 | 6.959623 | geographic_spread |
| 13 | `de:05315:18011` | Köln Kalk Kapelle | 5720 | 5 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.938927 | 7.008548 | geographic_spread |
| 14 | `de:05315:13221` | Köln Klettenbergpark | 5118 | 2 | Stadtbahn / Tram | 2 | 50.907869 | 6.919328 | geographic_spread |
| 15 | `de:05315:11410` | Köln Ubierring | 4827 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 5 | 50.922400 | 6.965481 | geographic_spread |
| 16 | `de:05315:13708` | Köln Weiden Zentrum | 4671 | 9 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 5 | 50.938239 | 6.835035 | geographic_spread |
| 17 | `de:05315:18511` | Köln Merheim | 4315 | 3 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.944071 | 7.048102 | geographic_spread |
| 18 | `de:05315:13611` | Köln Junkersdorf | 4279 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 8 | 50.937636 | 6.868372 | geographic_spread |
| 19 | `de:05315:19801` | Köln Stammheim S-Bahn | 4187 | 6 | S-Bahn<br>Urban / KVB Bus | 4 | 50.990068 | 7.002094 | geographic_spread |
| 20 | `de:05315:16101` | Köln Chorweiler | 4130 | 10 | Regional / Other Bus<br>S-Bahn<br>Stadtbahn / Tram<br>Urban / KVB Bus | 17 | 51.021129 | 6.897810 | geographic_spread |
| 21 | `de:05315:14611` | Köln Bocklemünd | 3624 | 10 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 5 | 50.969371 | 6.872822 | geographic_spread |
| 22 | `de:05315:19511` | Köln Dellbrück Hauptstr. | 3542 | 4 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.974805 | 7.074822 | geographic_spread |
| 23 | `de:05315:17701` | Köln Wahn S-Bahn | 3236 | 11 | Regional / Other Bus<br>Regional Bahn (RB)<br>S-Bahn<br>Urban / KVB Bus | 18 | 50.858127 | 7.079251 | geographic_spread |
| 24 | `de:05315:19512` | Köln Thielenbruch | 3042 | 2 | Stadtbahn / Tram | 4 | 50.978059 | 7.085462 | geographic_spread |
| 25 | `de:05315:19851` | Köln Stammheimer Ring | 3019 | 8 | Regional / Other Bus<br>Urban / KVB Bus | 6 | 50.989246 | 6.998359 | geographic_spread |
| 26 | `de:05315:12811` | Köln Sürth Bf | 2821 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 7 | 50.864090 | 7.003437 | geographic_spread |
| 27 | `de:05315:11111` | Köln Neumarkt | 19115 | 10 | Stadtbahn / Tram<br>Urban / KVB Bus | 13 | 50.935770 | 6.947677 | operational_richness |
| 28 | `de:05315:19211` | Köln Mülheim Wiener Platz | 12620 | 20 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 11 | 50.961797 | 7.004204 | operational_richness |
| 29 | `de:05315:11212` | Köln Breslauer Platz/Hbf | 11121 | 29 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 14 | 50.943657 | 6.959362 | operational_richness |
| 30 | `de:05315:11610` | Köln Rudolfplatz | 10653 | 7 | Stadtbahn / Tram<br>Urban / KVB Bus | 8 | 50.936343 | 6.939010 | operational_richness |
| 31 | `de:05315:11209` | Köln Appellhofplatz | 9965 | 5 | Stadtbahn / Tram | 4 | 50.939972 | 6.950437 | operational_richness |
| 32 | `de:05315:11511` | Köln Barbarossaplatz | 9805 | 5 | Regional / Other Bus<br>Stadtbahn / Tram | 6 | 50.928582 | 6.942723 | operational_richness |
| 33 | `de:05315:11112` | Köln Poststr. | 8985 | 4 | Stadtbahn / Tram | 2 | 50.931238 | 6.950060 | operational_richness |
| 34 | `de:05315:11211` | Köln Dom/Hbf | 8480 | 5 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.941806 | 6.957062 | operational_richness |
| 35 | `de:05315:11611` | Köln Moltkestr. | 8300 | 5 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.936071 | 6.932603 | operational_richness |
| 36 | `de:05315:19312` | Köln Buchheim Herler Str. | 7856 | 5 | Stadtbahn / Tram<br>Urban / KVB Bus | 5 | 50.957886 | 7.024985 | operational_richness |
| 37 | `de:05315:13211` | Köln Sülzgürtel | 7374 | 5 | Stadtbahn / Tram<br>Urban / KVB Bus | 6 | 50.912354 | 6.923938 | operational_richness |
| 38 | `de:05315:11909` | Köln Deutzer Freiheit | 7283 | 4 | Stadtbahn / Tram<br>Urban / KVB Bus | 7 | 50.936662 | 6.970358 | operational_richness |
| 39 | `de:05315:15113` | Köln Slabystr. | 6477 | 2 | Stadtbahn / Tram | 4 | 50.966582 | 6.988121 | operational_richness |
| 40 | `de:05315:15011` | Köln Neusser Str./Gürtel | 6371 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 8 | 50.972326 | 6.951354 | operational_richness |
| 41 | `de:05315:11914` | Köln Deutz Technische Hochschule | 6111 | 4 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.936467 | 6.986145 | operational_richness |
| 42 | `de:05315:11801` | Köln Hansaring | 5977 | 10 | Regional / Other Bus<br>Regional Bahn (RB)<br>S-Bahn<br>Stadtbahn / Tram<br>Urban / KVB Bus | 9 | 50.949124 | 6.952559 | operational_richness |
| 43 | `de:05315:13111` | Köln Weißhausstr. | 5746 | 8 | Regional / Other Bus<br>Stadtbahn / Tram<br>Urban / KVB Bus | 5 | 50.921501 | 6.933520 | operational_richness |
| 44 | `de:05315:18010` | Köln Kalk Post | 5742 | 6 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.937512 | 6.998488 | operational_richness |
| 45 | `de:05315:11514` | Köln Eifelwall/Stadtarchiv | 5708 | 3 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.924781 | 6.937042 | operational_richness |
| 46 | `de:05315:14211` | Köln Venloer Str./Gürtel | 5512 | 9 | Stadtbahn / Tram<br>Urban / KVB Bus | 6 | 50.950166 | 6.916705 | operational_richness |
| 47 | `de:05315:15121` | Köln Amsterdamer Str./Gürtel | 5453 | 4 | Stadtbahn / Tram<br>Urban / KVB Bus | 6 | 50.970840 | 6.969106 | operational_richness |
| 48 | `de:05315:13113` | Köln Sülzburgstr. | 5421 | 3 | Stadtbahn / Tram<br>Urban / KVB Bus | 4 | 50.915724 | 6.927503 | operational_richness |
| 49 | `de:05315:11710` | Köln Friesenplatz | 5288 | 9 | Stadtbahn / Tram<br>Urban / KVB Bus | 6 | 50.940533 | 6.939977 | operational_richness |
| 50 | `de:05315:11901` | Köln Messe/Deutz Bf | 5263 | 23 | Regional / Other Bus<br>Regional Bahn (RB)<br>Regional Express (RE)<br>S-Bahn | 9 | 50.940875 | 6.974998 | operational_richness |

## Legacy7 continuity

All seven continuity anchors were re-resolved in the current static feed as
`location_type=1` parent stations with scheduled child-stop evidence and were
retained unchanged:

| Station | Parent StopPointRef | Scheduled events | Result |
|---|---|---:|---|
| Köln Heumarkt | `de:05315:11110` | 11,321 | Preserved |
| Köln Hbf | `de:05315:11201` | 5,359 | Preserved |
| Köln Rodenkirchen Bf | `de:05315:12711` | 4,136 | Preserved |
| Köln Bf Ehrenfeld | `de:05315:14201` | 4,449 | Preserved |
| Köln Worringen S-Bahn | `de:05315:16601` | 1,948 | Preserved |
| Köln Porz Markt | `de:05315:17311` | 4,325 | Preserved |
| Köln Bf Mülheim | `de:05315:19201` | 12,383 | Preserved |

## Current core-mode coverage

The six current Stage 4 core modes all have current Cologne evidence and are
represented in the panel. SEV has no current Cologne evidence in this snapshot;
no SEV coverage is invented. Event representation below is scheduled
stop-event evidence, not ridership.

| Core mode | Cologne network scheduled events | Cologne-serving routes | Selected stations | Selected event representation | Share of network events |
|---|---:|---:|---:|---:|---:|
| Regional / Other Bus | 19,905 | 48 | 17 | 6,719 | 33.755% |
| Regional Bahn (RB) | 6,245 | 7 | 6 | 3,918 | 62.738% |
| Regional Express (RE) | 4,967 | 8 | 4 | 3,965 | 79.827% |
| S-Bahn | 27,796 | 4 | 9 | 12,652 | 45.517% |
| Stadtbahn / Tram | 507,354 | 12 | 43 | 233,556 | 46.034% |
| Urban / KVB Bus | 580,028 | 58 | 42 | 76,031 | 13.108% |

The low representation share for Urban/KVB Bus is an expected sample-coverage
limitation of a 50-parent monitoring panel, not a claim about passenger demand
or network performance.

## Geographic concentration review

The panel represents 25 of 57 occupied fixed cells. The largest selected cell
contains 12 of 50 stations (24.0%). A 1.2 km proximity review found 70 retained
nearby parent-station pairs. Nearby source identities were not collapsed:
they remain separate when the VRS hierarchy gives distinct parent IDs and their
route/mode/child-stop evidence is distinct.

Diagnostic-only checks after source identity was established found one selected
central-station identity, four selected Deutz-area identities, and two selected
Mülheim-area identities. These checks explain concentration around major
interchange areas; they were not selection identity rules. The detailed pair
and cell evidence is in the JSON artifact.

## Bounded MDD compatibility probe

The final panel was tested sequentially against
`https://mdd.gorheinland.com/delfi` using the approved TRIAS StopEvent request
construction:

- one request per station, `MaxAttempts=1`, zero retries;
- `NumberOfResults=1`, arrival request, and
  `IncludeRealtimeData=true`;
- approved `IncludePreviousCalls=true` and `IncludeOnwardCalls=true`;
- current UTC request timestamp used as the valid `DepArrTime`;
- no unsupported parameters, production cadence, or concurrency.

All 50 provider HTTP attempts returned HTTP 200, parsed successfully, and were
valid TRIAS application responses. Each returned one event and source
`StopPointRef` evidence matching the requested parent or a validated static
child stop. The probe used 50 of the 75-attempt hard budget. No replacement was
needed and no raw response was persisted. A preliminary non-escalated host
attempt failed local DNS before receiving an HTTP response; it did not consume
provider HTTP quota and is not included in the 50-provider-attempt count.

A valid parseable no-result response would have been retained as valid
compatibility evidence when static scheduled service existed; it would not have
been treated as an invalid `StopPointRef`.

## Fixed-panel versioning contract

`TIERED50_PANEL_V1` is approved as a fixed panel:

- membership is fixed after Stage 12 approval;
- station replacement is not dynamic during collection;
- any later membership change requires a new panel version;
- old panel versions remain historically interpretable;
- a new version records its effective date/time, reason, and predecessor;
- Legacy7 history is never deleted;
- future Stage 14 must preserve existing historical sampling identities where
  applicable; and
- reporting must distinguish sampling/panel eras.

## Boundary and artifacts

Stage 13 production work remains `NOT STARTED`. The pre-existing offline Stage
13 readiness notes remain conditionally blocked; Stage 12 does not turn them
into production approval. No production tier, cadence, result limit,
concurrency, retry, claim, or cutover implementation was added.

Created or updated for this closeout:

- `docs/stages/12-50-station-selection.md`;
- `docs/stages/12-50-station-selection-evidence.json`;
- `docs/stages/12-50-station-selection-completion-decision.md`;
- `scripts/stage_12_50_station_selection.py`;
- `tests/test_stage_12_station_selection.py`; and
- synchronized README, project-status, roadmap, decision-log, changelog, and
  guardrail entries.

The full acceptance-gate result is recorded in the [completion decision](12-50-station-selection-completion-decision.md). The full unittest suite and `git diff --check` are required before final handoff.

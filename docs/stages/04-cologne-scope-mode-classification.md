# Stage 4 — Cologne Scope & Mode Classification

**Status:** `COMPLETE — APPROVED`  
**Completion date:** 2026-09-28  
**Scope:** static-source validation and analytical design contract only

This document is the authoritative current Stage 4 approval snapshot. The
previous 2025-expired snapshot is preserved separately as historical/legacy
evidence in
[`04-cologne-scope-mode-evidence-historical-2026-09-28.json`](04-cologne-scope-mode-evidence-historical-2026-09-28.json).
It defines the technical Cologne boundary, separates source route identity
from geographical KPI scope, re-proves the observed mode inventory from the
current official VRS Global-ID feed, and records unresolved/unsupported cases
explicitly. It does not implement PostgreSQL, collectors, matching, Power BI,
or production analytics.

## 1. Goal and authoritative source

Stage 4 was authorized to:

1. define and validate the rule for “inside Cologne”;
2. distinguish route/trip identity from in-Cologne stop-event and segment
   scope;
3. re-prove the mode inventory from current static source rows;
4. approve a deterministic mode-classification contract; and
5. document source-quality limitations without inventing classifications.

The authoritative source for this approval is the current official VRS
OpenData Global-ID GTFS ZIP:

`https://download.vrs.de/gtfs/google_transit_goR.zip`

The official source page is:
`https://www.vrs.de/fuer-unternehmen/open-data-service`.
It identifies the mast-level Global-ID GTFS as the recommended dataset and
states that the VRS GTFS datasets are updated daily.

The current ZIP was retrieved once on 2026-09-28 and audited locally. No MDD
endpoint, GTFS-RT endpoint, API key, SQL Server, database, or Power BI system
was accessed for this stage. The reproducible audit is
[`scripts/stage_04_cologne_scope_mode_audit.py`](../../scripts/stage_04_cologne_scope_mode_audit.py);
the summary evidence is
[`04-cologne-scope-mode-evidence.json`](04-cologne-scope-mode-evidence.json).

### Feed snapshot

| Field | Recorded value |
|---|---|
| Source profile | VRS Global-ID GTFS |
| Retrieval timestamp | `2026-09-28T13:44:03Z` |
| ZIP size | 35,127,041 bytes |
| ZIP SHA-256 | `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b` |
| `feed_publisher_name` | `VRS` |
| `feed_publisher_url` | `http://www.vrs.de` |
| `feed_lang` | `de` |
| `feed_start_date` | `20260902` |
| `feed_end_date` | `20261212` |
| `feed_version` | `VERSION__20260928_0042` |
| Earliest service/calendar date | `20260902` |
| Latest service/calendar date | `20261212` |
| Freshness classification | `CURRENT_OR_OPERATIONALLY_RELEVANT` |
| Network retrieval operations | 1 official ZIP download; no retry |

The archive contained `agency.txt`, `calendar.txt`, `calendar_dates.txt`,
`feed_info.txt`, `frequencies.txt`, `routes.txt`, `shapes.txt`, `stops.txt`,
`stop_times.txt`, `transfers.txt`, and `trips.txt`. All required files were
parsed. `calendar.txt` had 4,616 data rows and `calendar_dates.txt` had 124,061
data rows; both covered `20260902` through `20261212` with no invalid date
values. The feed therefore overlaps the 2026-09-28 project date and also
contains forward schedule validity. No raw ZIP was copied into Git.

## 2. Exact Cologne scope rule

The approved primary identity rule is:

```text
validated Cologne stop_id starts with: de:05315:
```

This is a source-identity rule for the current VRS Global-ID feed. It is not
derived from operator, route name, route number, agency, vehicle mode, free
text, or approximate latitude/longitude. Coordinates remain diagnostic only.

The audit uses these formal concepts:

| Concept | Stage 4 definition |
|---|---|
| Cologne stop | A source stop whose actual `stop_id` starts with `de:05315:` and exists in `stops.txt`. |
| Cologne scheduled stop event | A `stop_times.txt` row whose referenced actual `stop_id` is a validated Cologne stop. |
| Cologne internal segment | Two adjacent scheduled stop events of one trip after `stop_sequence` ordering, where both endpoint stop IDs are validated Cologne stops. |
| Cologne-serving trip | A source `trip_id` with at least one Cologne scheduled stop event. This is a discovery/identity label, not a full-trip geographical claim. |
| Cologne-internal trip | All known source stop events of the trip are validated Cologne stops, with no unknown stop reference. |
| Cross-boundary Cologne trip | The trip has at least one Cologne event and at least one known non-Cologne event. Only its Cologne events and Cologne-internal segments enter core Cologne scope. |
| Cologne touch-only trip | A Cologne-serving trip with no Cologne-internal segment and no unknown stop reference. It is a diagnostic population and can overlap the formal internal/cross-boundary populations. |
| Cologne-serving route | A source `route_id` referenced by at least one Cologne-serving trip. This is route discovery only; it does not mean the physical route is wholly inside Cologne. |

The actual `stop_id` referenced by `stop_times.txt` controls stop-event scope.
`parent_station` does not silently pull a non-Cologne stop event into scope.

## 3. Static source integrity and hierarchy validation

### Source counts

| Source population | Count |
|---|---:|
| Agencies | 33 |
| Route rows | 957 |
| Distinct `route_id` values | 957 |
| Stop rows | 30,364 |
| Distinct `stop_id` values | 30,364 |
| Parent stations (`location_type=1`) | 9,903 |
| Stop positions (`location_type=0`) | 20,461 |
| Trip rows | 126,160 |
| Distinct `trip_id` values | 126,160 |
| Stop-time rows | 2,943,746 |

The complete current `route_type` inventory is:

| `route_type` | Route rows | Distinct route IDs | Preliminary source-row classification |
|---:|---:|---:|---|
| `0` | 18 | 18 | Stadtbahn / Tram: 18 |
| `2` | 31 | 31 | S-Bahn: 6; Regional Express: 11; Regional Bahn: 14 |
| `3` | 907 | 907 | Urban / KVB Bus: 58; Regional / Other Bus: 849 |
| `4` | 1 | 1 | `OTHER_MODE` / unsupported in this classifier: 1 |

The complete `location_type` inventory is `0: 20,461; 1: 9,903`. The current
feed contains explicit parent-station rows and child stop positions; the
actual stop ID referenced by `stop_times.txt` remains the controlling
geographical identity.

### Parent/child results

| Check | Result |
|---|---:|
| Cologne-prefix parent-station rows | 864 |
| Cologne-prefix child/stop-position rows | 2,273 |
| Cologne child rows whose parent is also Cologne | 2,273 |
| Cologne child rows whose parent is outside Cologne | 0 |
| Cologne child rows without a parent | 0 |
| Non-Cologne child rows whose parent is Cologne | 0 |
| Orphan `parent_station` references | 0 |
| `stop_times` rows referencing unknown `stop_id` | 0 |

The current hierarchy is retained as source evidence, but parent/child
relationships do not silently override the actual `stop_id` referenced by
`stop_times.txt`.

## 4. Cologne scope measurements

The feed contains 3,137 Cologne stop rows and 3,137 distinct Cologne stop IDs.
The stop-time partition is complete:

| Stop-time scope | Rows |
|---|---:|
| Validated Cologne stop events | 1,144,295 |
| Known non-Cologne stop events | 1,799,451 |
| Unknown stop ID references | 0 |
| Total stop-time rows | 2,943,746 |

The resulting trip/route populations are:

| Population | Count |
|---|---:|
| Cologne scheduled stop events | 1,144,295 |
| Cologne internal consecutive segments | 1,082,490 |
| Cologne-serving trips | 61,805 |
| Cologne-internal trips | 39,566 |
| Cross-boundary Cologne trips | 22,239 |
| Cross-boundary trips with an internal Cologne segment | 20,633 |
| Touch-only Cologne trips | 1,606 |
| Unresolved stop-scope trips | 0 |
| Cologne-serving routes | 137 |
| Cross-boundary share of Cologne-serving trips | 35.982526% |

The formal cross-boundary and touch-only populations are intentionally
overlapping definitions. The mutually exclusive trip buckets reconcile exactly
to all 61,805 Cologne-serving trip IDs:

| Exclusive bucket | Count |
|---|---:|
| Cologne-internal with an internal segment | 39,566 |
| Cross-boundary with an internal segment | 20,633 |
| Cross-boundary touch-only | 1,606 |
| **Total** | **61,805** |

No external stop event was included merely because a parent station had a
Cologne identity. The current feed's parent/child hierarchy was measured, but
the actual stop ID in `stop_times.txt` remains authoritative.

## 5. Route identity versus analytical scope

`route_id` and `trip_id` remain source identities. A route can serve both
Cologne and external municipalities, and a trip can contain both Cologne and
non-Cologne stop events. A Cologne-serving route is therefore not a complete
Cologne KPI route.

Core geographical KPI calculations may use:

- validated Cologne scheduled stop events;
- validated Cologne-internal segments with both endpoints in Cologne; and
- the Cologne portion of cross-boundary services.

They must not aggregate every external stop of a route simply because the
route has one Cologne stop. A single passing stop is valid Cologne evidence at
the stop-event level, but it is not by itself an internal Cologne movement.

## 6. Approved mode classifier

The classifier preserves every source `route_type`, `route_short_name`,
`route_long_name`, `agency_id`, and resolved `agency_name`. The analytical
label is derived; it never overwrites the source label.

The deterministic precedence is:

1. Conflicting duplicate route/trip identity inheritance remains an explicit
   ambiguity bucket.
2. `route_type=0` is `Stadtbahn / Tram` for this feed profile.
3. `route_type=2` establishes rail. Within rail, check long-distance labels,
   then S-Bahn, RE, RB, and finally Other Rail.
4. `route_type=3` establishes bus. Check bounded SEV evidence first, then the
   validated KVB agency, then Regional / Other Bus.
5. Unsupported route types remain `OTHER_MODE`; missing or unresolved source
   identity remains `UNCLASSIFIED_MODE` or an explicit ambiguity bucket.

### Validated KVB source evidence

The current agency row is:

| `agency_id` | `agency_name` | Resolution |
|---:|---|---|
| `1` | `KVB Kölner Verkehrs-Betriebe AG` | Single validated KVB source agency |

No old hard-coded agency ID was used without resolving it from the current
`agency.txt` row.

### Rail evidence

Rail classification required `route_type=2` first. Examples below show the
source fields used; the analytical class does not replace them.

| Analytical class | `route_id` | `route_short_name` | `route_long_name` | Agency | `route_type` | Evidence |
|---|---|---|---|---|---:|---|
| S-Bahn | `de:nrw:s11:` | `S11` | *(blank)* | DB DB Regio AG | 2 | bounded `S-number` label |
| S-Bahn | `be:sncb:S41:` | `S41` | *(blank)* | SNCB Belgische Eisenbahn | 2 | bounded `S-number` label |
| Regional Express (RE) | `de:nrw:re1:` | *(blank)* | `RE1 (RRX)` | NX National Express Rail GmbH | 2 | bounded `RE-number` in `route_long_name` |
| Regional Express (RE) | `de:nrw:re12:` | `RE12` | *(blank)* | DB DB Regio AG | 2 | bounded `RE-number` label |
| Regional Bahn (RB) | `de:nrw:rb20:` | `RB20` | *(blank)* | DB DB Regio AG | 2 | bounded `RB-number` label |
| Regional Bahn (RB) | `de:nrw:rb21:` | `RB21` | *(blank)* | RTB Rurtalbahn GmbH | 2 | bounded `RB-number` label |
| Long-distance | — | — | — | — | — | No ICE, IC, EC, NightJet/NJ, FlixTrain/FLX, Thalys, or Eurostar-equivalent label was observed in current rail rows. |
| Other Rail | — | — | — | — | — | All 31 current rail rows matched S-Bahn, RE, or RB evidence; no residual Other Rail row remained. |

### Bus, Regional / Other Bus, and SEV

- `route_type=3` plus the validated KVB agency produces `Urban / KVB Bus`.
  This yields 58 source route rows, 58 Cologne-serving routes, and 580,028
  Cologne scheduled stop events.
- A non-KVB `route_type=3` row is `Regional / Other Bus` unless bounded SEV
  evidence takes precedence. This is a core mode only for its validated
  in-Cologne events and segments; external trip portions remain
  outside Cologne KPI scope.
- SEV evidence uses exact bounded labels/phrases in source route labels,
  including `SEV`, `Schienenersatzverkehr`, or `Ersatzverkehr`. No current
  route row matched those bounded route-label patterns, so SEV has 0 source
  route rows, 0 Cologne-serving routes, 0 Cologne-serving trips, and 0
  Cologne scheduled stop events. It remains a separate analytical category
  rather than being treated as an ordinary bus. Agencies whose names contain
  `SEV` were not used as a substitute for route-label evidence.
- The classifier did not classify all non-KVB buses as SEV.

### Mode-level results

`Scheduled stop events total` is the denominator of the mode percentage; it
includes all source `stop_times` rows associated with trips in that mode.
`Cologne events` counts only validated `de:05315:` stop IDs.

| Mode | Scope status | Source route rows | Distinct route IDs | Cologne-serving routes | Scheduled events total | Cologne events | Cologne events % | Serving trips | Internal trips | Cross-boundary trips | Touch-only trips | Internal segments |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Stadtbahn / Tram | CORE_KPI | 18 | 18 | 12 | 700,388 | 507,354 | 72.438991% | 25,436 | 16,001 | 9,435 | 102 | 481,918 |
| Urban / KVB Bus | CORE_KPI | 58 | 58 | 58 | 606,427 | 580,028 | 95.646797% | 25,917 | 23,001 | 2,916 | 0 | 554,111 |
| S-Bahn | CORE_KPI | 6 | 6 | 4 | 54,591 | 26,345 | 48.258871% | 2,543 | 315 | 2,228 | 58 | 23,802 |
| Regional Express (RE) | CORE_KPI | 11 | 11 | 8 | 31,860 | 4,650 | 14.595104% | 1,460 | 4 | 1,456 | 112 | 3,190 |
| Regional Bahn (RB) | CORE_KPI | 14 | 14 | 7 | 50,606 | 6,061 | 11.976841% | 1,542 | 0 | 1,542 | 66 | 4,519 |
| Regional / Other Bus | CORE_KPI | 849 | 849 | 48 | 1,499,608 | 19,857 | 1.324146% | 4,907 | 245 | 4,662 | 1,268 | 14,950 |
| Rail Replacement Bus (SEV) | SEPARATE_ANALYTICAL | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 |
| Long-distance | OUTSIDE_CORE | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 |
| Other Rail | OUTSIDE_CORE | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 |
| `OTHER_MODE` | OUTSIDE_CORE | 1 | 1 | 0 | 266 | 0 | 0.000000% | 0 | 0 | 0 | 0 | 0 |

No Cologne-scoped events were unclassified or ambiguous. The unsupported
`route_type=4` row is retained explicitly as `OTHER_MODE`; it has no Cologne
events. Mode-level sums reconcile exactly to 1,144,295 Cologne events, 61,805
serving trips, 137 serving route IDs, and 1,082,490 internal segments.

The agency distribution and full representative source rows are retained in
the JSON evidence file. The largest current serving populations are KVB
(`agency_id=1`: 70 serving route IDs, 51,353 serving trips, 1,087,382 Cologne
events), DB Regio (`agency_id=5`: 13, 3,863, 31,516), and NX National Express
(`agency_id=139`: 5, 1,439, 4,562). Agency presence does not expand
geographical scope or override the route-type classifier.

## 7. Duplicate IDs and unresolved source-quality cases

The current feed contains no duplicate `route_id`, `stop_id`, or `trip_id`
values. Therefore no Cologne-serving route inheritance was ambiguous, and no
row was arbitrarily selected among duplicate route definitions. The audit
still retains explicit ambiguity buckets for future feeds.

The current anomaly measurements are:

| Anomaly | Count |
|---|---:|
| Duplicate route IDs | 0 |
| Duplicate stop IDs | 0 |
| Duplicate trip IDs | 0 |
| Unknown stop-time stop IDs | 0 |
| Unknown Cologne-prefix stop IDs in `stop_times` | 0 |
| Unknown trip references | 0 |
| Missing trip IDs on Cologne events | 0 |
| Invalid stop sequences | 0 |
| Duplicate stop sequences | 0 |
| Unclassified/ambiguous Cologne events | 0 |

The observed unsupported route-type inventory is explicit: one `route_type=4`
row (`de:vrs:993:RF`, `F93`, a ferry) is classified as `OTHER_MODE`, has 266
scheduled events outside Cologne, and contributes no Cologne KPI evidence. No
unsupported or ambiguous row was forced into a core mode.

If a future feed contains conflicting duplicate route rows, the audit will
preserve all rows and place affected route/trip/event inheritance in
`AMBIGUOUS_ROUTE_ID` or `AMBIGUOUS_TRIP_ID` rather than choosing a row.

## 8. Current approval snapshot and legacy comparison

The previous static evidence is retained as an explicitly historical snapshot;
it is not the current authoritative source:

| Field | Current Stage 4 approval snapshot | Historical/legacy snapshot |
|---|---|---|
| Source URL | `https://download.vrs.de/gtfs/google_transit_goR.zip` | `https://www.vrs.de/fileadmin/05_Zukunftsmobilitaet/Fuer_Unternehmen/GTFS_VRS_mit_SPNV_hID_GlobalID.zip` |
| Retrieval timestamp | `2026-09-28T13:44:03Z` | `2026-09-28T13:04:20Z` |
| ZIP SHA-256 | `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b` | `d702c82cb1f274c8cc7ab8ec6e547920f92e1325b82baf533d9a7e52a59604d1` |
| Feed version | `VERSION__20260928_0042` | `VERSION__20250114_2330` |
| Feed date range | `2026-09-02` through `2026-12-12` | `2024-08-21` through `2025-03-30` |
| Freshness | `CURRENT_OR_OPERATIONALLY_RELEVANT` | Historical/stale relative to the project date |

### Scope-count comparison

| Measure | Current | Historical/legacy | Difference (current − historical) |
|---|---:|---:|---:|
| Cologne stop rows / distinct IDs | 3,137 | 862 | +2,275 |
| Cologne scheduled stop events | 1,144,295 | 448,755 | +695,540 |
| Cologne internal consecutive segments | 1,082,490 | 421,999 | +660,491 |
| Cologne-serving trips | 61,805 | 26,692 | +35,113 |
| Cologne-internal trips | 39,566 | 13,268 | +26,298 |
| Cross-boundary Cologne trips | 22,239 | 13,424 | +8,815 |
| Touch-only trips | 1,606 | 1,187 | +419 |
| Cologne-serving routes | 137 | 136 | +1 |
| Cologne parent-station rows | 864 | 0 | +864 |
| Unknown stop-time references | 0 | 0 | 0 |

### Mode-count comparison

| Mode | Current source route rows | Historical source route rows | Current Cologne events | Historical Cologne events | Current serving trips | Historical serving trips |
|---|---:|---:|---:|---:|---:|---:|
| Stadtbahn / Tram | 18 | 20 | 507,354 | 131,497 | 25,436 | 6,242 |
| Urban / KVB Bus | 58 | 55 | 580,028 | 256,408 | 25,917 | 10,624 |
| S-Bahn | 6 | 5 | 26,345 | 31,311 | 2,543 | 2,953 |
| Regional Express (RE) | 11 | 10 | 4,650 | 6,030 | 1,460 | 1,871 |
| Regional Bahn (RB) | 14 | 10 | 6,061 | 4,654 | 1,542 | 1,245 |
| Regional / Other Bus | 849 | 516 | 19,857 | 18,855 | 4,907 | 3,757 |
| Rail Replacement Bus (SEV) | 0 | 2 | 0 | 0 | 0 | 0 |
| Long-distance | 0 | 0 | 0 | 0 | 0 | 0 |
| Other Rail | 0 | 0 | 0 | 0 | 0 | 0 |
| `OTHER_MODE` | 1 | 0 | 0 | 0 | 0 | 0 |

### Structural differences and interpretation

The count changes are expected and are not treated as failures. The current
feed is a different version with a current/future service window, a larger
Global-ID network snapshot, explicit parent-station hierarchy, a different
route/trip population, and one observed `route_type=4` ferry row that is
explicitly outside core scope. The previous snapshot contained two bounded
SEV-labelled source rows; the current snapshot contains no route-label SEV
matches, so no current row was silently reclassified as SEV. All current
Cologne events, serving trips, serving routes, and internal segments reconcile
to their mode buckets, and no current rows disappeared without an explicit
classification or scope reason.

The historical evidence file is
[`04-cologne-scope-mode-evidence-historical-2026-09-28.json`](04-cologne-scope-mode-evidence-historical-2026-09-28.json).
The current evidence file has exactly one role:
`CURRENT STAGE 4 APPROVAL SNAPSHOT`.

This stage describes the current authoritative VRS Global-ID GTFS feed only.
It does not prove:

- complete physical public-transport network coverage beyond this feed;
- complete long-distance rail coverage;
- actual realtime operation;
- delays, cancellations, or current service performance;
- GTFS.de identity equivalence;
- historical identity stability; or
- that the feed's static service calendar is an observation of operation.

## 9. Acceptance checks

All automatic Stage 4 audit checks passed, including:

1. current official VRS Global-ID source and VRS `feed_info` provenance
   recorded;
2. ZIP size, SHA-256, archive file list, and feed metadata recorded;
3. `calendar.txt` and `calendar_dates.txt` inspected, with the latest
   represented service/calendar date recorded;
4. freshness classified as `CURRENT_OR_OPERATIONALLY_RELEVANT` against the
   2026-09-28 project date;
5. direct `de:05315:` identity rule measured against `stops.txt` and
   `stop_times.txt`;
6. parent/child anomalies and unknown references measured;
7. stop-event and both-endpoints segment scope measured;
8. internal, cross-boundary, and touch-only populations measured;
9. route identity explicitly separated from event/segment geography;
10. every observed `route_type` inventoried, including explicit `OTHER_MODE`
    handling for `route_type=4`;
11. deterministic classifier precedence documented;
12. S-Bahn, RE, RB, KVB bus, and Regional/Other Bus source evidence retained;
    SEV, long-distance, and residual Other Rail remain explicit separate or
    outside-core categories;
13. duplicate-ID ambiguity handling is explicit and no current duplicate was
    silently resolved;
14. unknown/unclassified values remain visible;
15. mode-level route/trip/event/segment counts reconcile with overall totals;
16. no static evidence is described as realtime or actual performance;
17. the audit is standard-library-only and does not require a database;
18. the historical snapshot is explicitly separated from the current approval
    snapshot.

Repository-level checks completed after documentation synchronization:

- Python compilation, AST parsing, `--help`, current local ZIP parsing, JSON
  validation, and a byte-for-byte deterministic second offline pass passed.
- `git diff --check` passed.
- Exactly one current official ZIP retrieval occurred; no MDD or GTFS-RT
  request was made, and no SQL/database, production collector, scheduler,
  Power BI, or legacy-repository change was made.

## 10. Final Stage 4 decision

Stage 4 is **COMPLETE — APPROVED** for the current static-source scope and
mode contract documented here.

The approval is specifically for:

1. direct Global-ID Cologne stop identity using `stop_id` prefix `de:05315:`;
2. stop-event-first geographical scope;
3. the both-endpoints rule for internal Cologne segments;
4. technical Cologne-serving trip/route labels not being full geographical KPI
   scope;
5. cross-boundary services contributing only validated Cologne evidence;
6. the deterministic source-faithful mode classifier;
7. RE, RB, and Regional/Other Bus inclusion only for in-Cologne
   events/segments;
8. SEV as a separate analytical category;
9. long-distance rail remaining outside core KPI scope; and
10. unknown, unsupported, and ambiguous source values remaining explicit.

Stage 5 — Database Architecture is now **COMPLETE — APPROVED ARCHITECTURE**.
Its closeout approves the future PostgreSQL architecture only; it does not
create a PostgreSQL schema, migrate SQL Server, build a warehouse, implement
collectors or matching, modify Power BI, or start Stage 6. Stage 6 is
**NOT STARTED / NEXT**.

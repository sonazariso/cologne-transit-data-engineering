# Stage 4 — Cologne Scope & Mode Classification

**Status:** `COMPLETE — APPROVED`  
**Completion date:** 2026-09-28  
**Scope:** static-source validation and analytical design contract only

This document is the authoritative Stage 4 closeout. It defines the technical
Cologne boundary, separates source route identity from geographical KPI scope,
re-proves the observed mode inventory from the current authoritative VRS
Global-ID feed, and records the unresolved/unsupported cases explicitly. It
does not implement PostgreSQL, collectors, matching, Power BI, or production
analytics.

## 1. Goal and authoritative source

Stage 4 was authorized to:

1. define and validate the rule for “inside Cologne”;
2. distinguish route/trip identity from in-Cologne stop-event and segment
   scope;
3. re-prove the mode inventory from current static source rows;
4. approve a deterministic mode-classification contract; and
5. document source-quality limitations without inventing classifications.

The authoritative source for this validation is the official VRS OpenData
Global-ID GTFS ZIP:

`https://www.vrs.de/fileadmin/05_Zukunftsmobilitaet/Fuer_Unternehmen/GTFS_VRS_mit_SPNV_hID_GlobalID.zip`

The ZIP was retrieved once on 2026-09-28 and audited locally. No VRS HTML
page, MDD endpoint, GTFS-RT endpoint, API key, SQL Server, database, or Power
BI system was accessed for this stage. The reproducible audit is
[`scripts/stage_04_cologne_scope_mode_audit.py`](../../scripts/stage_04_cologne_scope_mode_audit.py);
the summary evidence is
[`04-cologne-scope-mode-evidence.json`](04-cologne-scope-mode-evidence.json).

### Feed snapshot

| Field | Recorded value |
|---|---|
| Source profile | VRS Global-ID GTFS |
| Retrieval timestamp | `2026-09-28T13:04:20Z` |
| ZIP size | 15,638,946 bytes |
| ZIP SHA-256 | `d702c82cb1f274c8cc7ab8ec6e547920f92e1325b82baf533d9a7e52a59604d1` |
| `feed_publisher_name` | `VRS` |
| `feed_publisher_url` | `http://www.vrs.de` |
| `feed_lang` | `de` |
| `feed_start_date` | `20240821` |
| `feed_end_date` | `20250330` |
| `feed_version` | `VERSION__20250114_2330` |
| Network retrieval operations | 1 official ZIP download; no retry |

The archive contained `agency.txt`, `calendar.txt`, `calendar_dates.txt`,
`feed_info.txt`, `frequencies.txt`, `routes.txt`, `shapes.txt`, `stops.txt`,
`stop_times.txt`, `transfers.txt`, and `trips.txt`. All required files were
parsed. The optional files were inventoried; `frequencies.txt` was present
with zero data rows, while `calendar.txt`, `calendar_dates.txt`, `shapes.txt`,
and `transfers.txt` were present with data. No raw ZIP was copied into Git.

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
| Agencies | 27 |
| Route rows | 618 |
| Distinct `route_id` values | 618 |
| Stop rows | 6,892 |
| Distinct `stop_id` values | 6,892 |
| Parent stations (`location_type=1`) | 0 |
| Stop positions (`location_type=0`) | 6,892 |
| Trip rows | 74,615 |
| Distinct `trip_id` values | 74,615 |
| Stop-time rows | 1,795,448 |

The complete current `route_type` inventory is:

| `route_type` | Route rows | Distinct route IDs | Preliminary source-row classification |
|---:|---:|---:|---|
| `0` | 20 | 20 | Stadtbahn / Tram: 20 |
| `2` | 25 | 25 | S-Bahn: 5; Regional Express: 10; Regional Bahn: 10 |
| `3` | 573 | 573 | Urban / KVB Bus: 55; Regional / Other Bus: 516; SEV: 2 |

The complete `location_type` inventory is `0: 6,892`. No source row used
`location_type=1`, so this feed snapshot has no parent-station rows to which
child records could be attached.

### Parent/child results

| Check | Result |
|---|---:|
| Cologne-prefix parent-station rows | 0 |
| Cologne-prefix child/stop-position rows | 862 |
| Cologne child rows whose parent is also Cologne | 0 |
| Cologne child rows whose parent is outside Cologne | 0 |
| Cologne child rows without a parent | 862 |
| Non-Cologne child rows whose parent is Cologne | 0 |
| Orphan `parent_station` references | 0 |
| `stop_times` rows referencing unknown `stop_id` | 0 |

The absence of parent records is retained as a source-quality observation; it
does not justify reconstructing parent stations from coordinates or names.

## 4. Cologne scope measurements

The feed contains 862 Cologne stop rows and 862 distinct Cologne stop IDs.
The stop-time partition is complete:

| Stop-time scope | Rows |
|---|---:|
| Validated Cologne stop events | 448,755 |
| Known non-Cologne stop events | 1,346,693 |
| Unknown stop ID references | 0 |
| Total stop-time rows | 1,795,448 |

The resulting trip/route populations are:

| Population | Count |
|---|---:|
| Cologne scheduled stop events | 448,755 |
| Cologne internal consecutive segments | 421,999 |
| Cologne-serving trips | 26,692 |
| Cologne-internal trips | 13,268 |
| Cross-boundary Cologne trips | 13,424 |
| Cross-boundary trips with an internal Cologne segment | 12,237 |
| Touch-only Cologne trips | 1,187 |
| Unresolved stop-scope trips | 0 |
| Cologne-serving routes | 136 |
| Cross-boundary share of Cologne-serving trips | 50.292222% |

The formal cross-boundary and touch-only populations are intentionally
overlapping definitions. The mutually exclusive trip buckets reconcile exactly
to all 26,692 Cologne-serving trip IDs:

| Exclusive bucket | Count |
|---|---:|
| Cologne-internal with an internal segment | 13,268 |
| Cross-boundary with an internal segment | 12,237 |
| Cross-boundary touch-only | 1,187 |
| **Total** | **26,692** |

No external stop event was included merely because a parent station might have
had a Cologne identity; this feed had no parent-station records in any case.

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
| S-Bahn | `501206` | `S6` | *(blank)* | DB DB - Regio AG, Region NRW | 2 | bounded `S-number` label |
| S-Bahn | `501211` | `S11` | *(blank)* | DB DB - Regio AG, Region NRW | 2 | bounded `S-number` label |
| Regional Express (RE) | `13901001` | *(blank)* | `RE1 (RRX)` | NX National Express Rail GmbH | 2 | bounded `RE-number` in `route_long_name` |
| Regional Express (RE) | `13901007` | `RE7` | *(blank)* | NX National Express Rail GmbH | 2 | bounded `RE-number` label |
| Regional Bahn (RB) | `13001026` | `RB26` | *(blank)* | TR trans regio Deutsche Regionalbahn GmbH | 2 | bounded `RB-number` label |
| Regional Bahn (RB) | `13901048` | `RB48` | *(blank)* | NX National Express Rail GmbH | 2 | bounded `RB-number` label |
| Long-distance | — | — | — | — | — | No ICE, IC, EC, NightJet/NJ, FlixTrain/FLX, Thalys, or Eurostar-equivalent label was observed in current rail rows. |
| Other Rail | — | — | — | — | — | All 25 current rail rows matched S-Bahn, RE, or RB evidence; no residual Other Rail row remained. |

### Bus, Regional / Other Bus, and SEV

- `route_type=3` plus the validated KVB agency produces `Urban / KVB Bus`.
  This yields 55 source route rows, 55 Cologne-serving routes, and 256,408
  Cologne scheduled stop events.
- A non-KVB `route_type=3` row is `Regional / Other Bus` unless bounded SEV
  evidence takes precedence. This is approved as a core mode only for its
  validated in-Cologne events and segments; external trip portions remain
  outside Cologne KPI scope.
- SEV evidence uses exact bounded labels/phrases in source route labels,
  including `SEV`, `Schienenersatzverkehr`, or `Ersatzverkehr`. Two source
  route rows matched `SEV` in `route_short_name`: `15101298` (HLB) and
  `19901298` (agency name `SEV Schienenersatzverkehr Deutsche Bahn AG`).
  Neither was Cologne-serving in this snapshot, so SEV has 0 Cologne-serving
  routes, 0 Cologne-serving trips, and 0 Cologne scheduled stop events. It
  remains a separate analytical category rather than being treated as an
  ordinary bus.
- The classifier did not classify all non-KVB buses as SEV.

### Mode-level results

`Scheduled stop events total` is the denominator of the mode percentage; it
includes all source `stop_times` rows associated with trips in that mode.
`Cologne events` counts only validated `de:05315:` stop IDs.

| Mode | Scope status | Source route rows | Distinct route IDs | Cologne-serving routes | Scheduled events total | Cologne events | Cologne events % | Serving trips | Internal trips | Cross-boundary trips | Touch-only trips | Internal segments |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Stadtbahn / Tram | CORE_KPI | 20 | 20 | 13 | 221,576 | 131,497 | 59.346229% | 6,242 | 3,882 | 2,360 | 26 | 125,255 |
| Urban / KVB Bus | CORE_KPI | 55 | 55 | 55 | 266,295 | 256,408 | 96.287200% | 10,624 | 9,147 | 1,477 | 1 | 245,784 |
| S-Bahn | CORE_KPI | 5 | 5 | 4 | 71,981 | 31,311 | 43.498979% | 2,953 | 6 | 2,947 | 0 | 28,358 |
| Regional Express (RE) | CORE_KPI | 10 | 10 | 9 | 36,377 | 6,030 | 16.576408% | 1,871 | 3 | 1,868 | 57 | 4,159 |
| Regional Bahn (RB) | CORE_KPI | 10 | 10 | 6 | 31,385 | 4,654 | 14.828740% | 1,245 | 0 | 1,245 | 66 | 3,409 |
| Regional / Other Bus | CORE_KPI | 516 | 516 | 49 | 1,167,464 | 18,855 | 1.615039% | 3,757 | 230 | 3,527 | 1,037 | 15,034 |
| Rail Replacement Bus (SEV) | SEPARATE_ANALYTICAL | 2 | 2 | 0 | 370 | 0 | 0.000000% | 0 | 0 | 0 | 0 | 0 |
| Long-distance | OUTSIDE_CORE | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 |
| Other Rail | OUTSIDE_CORE | 0 | 0 | 0 | 0 | 0 | — | 0 | 0 | 0 | 0 | 0 |

Unsupported/ambiguous/unresolved buckets were all zero in this snapshot. The
mode-level sums reconcile exactly to 448,755 Cologne events, 26,692 serving
trips, 136 serving route IDs, and 421,999 internal segments.

The agency distribution and full representative source rows are retained in
the JSON evidence file. The largest current serving populations are KVB
(`agency_id=1`: 68 serving route IDs, 16,866 serving trips, 387,905 Cologne
events), DB Regio NRW (`agency_id=5`: 12, 4,224, 35,943), and NX National
Express (`agency_id=139`: 11, 1,743, 5,092). Agency presence does not expand
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

If a future feed contains conflicting duplicate route rows, the audit will
preserve all rows and place affected route/trip/event inheritance in
`AMBIGUOUS_ROUTE_ID` or `AMBIGUOUS_TRIP_ID` rather than choosing a row.

## 8. Legacy comparison and limitations

Historical comparison values supplied by the restart evidence were:

| Measure | Current Stage 4 feed | Historical comparison only |
|---|---:|---:|
| Cologne stop records | 862 | 3,156 |
| Parent stations | 0 | 866 |
| Stop positions | 862 | 2,290 |
| Cologne-serving routes | 136 | 153 |
| Cologne-serving trips | 26,692 | 90,331 |
| Cologne scheduled stop-event patterns | 448,755 | 1,551,343 |

These values are not a pass/fail equality test. The current feed has a
different snapshot/version, date range, source profile, hierarchy shape, and
static counting population from the historical evidence. The current VRS
Global-ID results above are authoritative for this Stage 4 decision. No raw
legacy feed was available in this repository for a second independent
recalculation.

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

1. official VRS Global-ID source and VRS `feed_info` provenance recorded;
2. ZIP size, SHA-256, archive file list, and feed metadata recorded;
3. direct `de:05315:` identity rule measured against `stops.txt` and
   `stop_times.txt`;
4. parent/child anomalies and unknown references measured;
5. stop-event and both-endpoints segment scope measured;
6. internal, cross-boundary, and touch-only populations measured;
7. route identity explicitly separated from event/segment geography;
8. every observed `route_type` inventoried;
9. deterministic classifier precedence documented;
10. S-Bahn, RE, RB, KVB bus, Regional/Other Bus, and SEV source evidence
    retained, with long-distance and residual Other Rail explicitly absent in
    this feed;
11. duplicate-ID ambiguity handling is explicit and no current duplicate was
    silently resolved;
12. unknown/unclassified values remain visible;
13. mode-level route/trip/event/segment counts reconcile with overall totals;
14. no static evidence is described as realtime or actual performance;
15. the audit is standard-library-only and does not require a database.

Repository-level checks completed after documentation synchronization:

- Python compilation succeeded with the bytecode cache redirected to
  `/private/tmp`.
- AST parsing, `--help`, local ZIP parsing, JSON parsing, and a second offline
  repeat run passed.
- `git diff --check` passed.
- No MDD or GTFS-RT request was made; no SQL/database, production collector,
  scheduler, Power BI, or legacy-repository change was made.

## 10. Final Stage 4 decision

Stage 4 is **COMPLETE — APPROVED** for the static-source scope and mode
contract documented here.

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

Stage 5 — Database Architecture is **NOT STARTED / NEXT**. This closeout does
not create a PostgreSQL schema, migrate SQL Server, build a warehouse,
implement collectors or matching, modify Power BI, or start Stage 5.

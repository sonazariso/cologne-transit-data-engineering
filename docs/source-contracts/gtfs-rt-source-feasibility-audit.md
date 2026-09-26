# GTFS-Realtime Source Feasibility Audit

**Status:** `POST-STAGE-02 VALIDATION — HOST EXECUTION PENDING`  
**Stage position:** Stage 0 — **APPROVED**; Stage 1 — **APPROVED**; Stage 2 —
**APPROVED**; Stage 3 — **NEXT / NOT STARTED**  
**Validation type:** bounded, read-only source-feasibility audit  
**Production decision:** none; GTFS-Realtime is not approved as a production
source by this document.

## 1. Purpose

This audit measures whether the public GTFS-Realtime feed at
<https://realtime.gtfs.de/realtime-free.pb> can provide useful complementary
evidence for Cologne Transport Intelligence. The bounded questions concern
feed validity, entity coverage, Köln Hbf and Köln Neumarkt discovery, timing
field population, prediction evolution, cancellation/skipped-stop state,
freshness, lifecycle visibility, and identifier compatibility.

The audit tooling is diagnostic only. It does not integrate GTFS-Realtime into
the database, production collector, Power BI, SQL Server, or the Legacy
repository. It does not modify the existing MDD collector and it does not
start Stage 3.

## 2. Why this audit is being performed now

Stage 2 is approved and remains closed. Its source-contract evidence showed
that `TimetabledTime` is scheduled time, `EstimatedTime` is predicted time,
and `RecordedAtTime` is the TRIAS candidate for an actual/recorded event time.
`RecordedAtTime` was not observed in the tested MDD responses, including the
bounded lifecycle validation. Historical StopEvent requests at 1, 7, and 30
days returned timetable data without realtime/actual timing, while the tested
90- and 180-day requests returned no matching events.

The MDD evidence therefore supports preserving prospective estimate evolution
but does not approve an Actual Delay KPI. This separate post-Stage-02 audit
tests whether GTFS-Realtime may add useful evidence without changing that
conclusion or making a provider-wide claim from one feed run.

## 3. Existing MDD evidence

The approved MDD/TRIAS source-contract package remains authoritative. In
particular:

- `EstimatedTime - TimetabledTime` may be reported only as explicitly labelled
  **Estimated Delay**.
- The last `EstimatedTime` must never be promoted to `ActualTime`.
- `RecordedAtTime` remains a semantic candidate, not an observed or approved
  actual-time source for the project.
- Prospective snapshot history is important because a later historical query
  may not reconstruct missed realtime state.

The GTFS-Realtime audit is complementary evidence and does not reopen, amend,
or invalidate Stage 2.

## 4. Source under test

The only network source permitted for this initial audit is:

<https://realtime.gtfs.de/realtime-free.pb>

The script uses an HTTP `GET`, no authentication, a timeout of approximately
30 seconds, sequential requests, no retries, and no MDD or authenticated VRS
requests. The public GTFS.de realtime documentation describes this feed as
containing TripUpdates and ServiceAlerts, covering Verkehrsverbund
Rhein-Sieg/Köln-Bonn, and being updated approximately every 10 seconds. The
public documentation also states that the realtime stream is designed to
match GTFS.de static feeds; that does **not** establish compatibility with the
project's current VRS/go.Rheinland static GTFS. The public feed documentation
is referenced at <https://gtfs.de/en/feeds/>.

## 5. Public-source provenance

The feed URL and public-feed characteristics above are source-provenance
inputs supplied for this bounded validation and should be rechecked during
host execution. VRS separately describes GTFS-RT IST availability through its
registered/internal OpenService access; that statement is not treated as
permission to call an authenticated VRS feed in this audit. See the VRS
OpenService information at <https://www.vrs.de/service/openservice>.

The public GTFS.de stream is therefore tested as a distinct source. No claim
is made that its identifiers, coverage, retention, or provider semantics are
the same as the project's current VRS/go.Rheinland static source.

## 6. GTFS-RT semantic guardrails

The official GTFS-Realtime reference defines TripUpdate as realtime progress
and prediction information. Its reference documentation is at
<https://gtfs.org/documentation/realtime/reference/> and its Trip Updates
guide is at <https://gtfs.org/documentation/realtime/feed-entities/trip-updates/>.

The audit applies these rules:

- `StopTimeEvent.time` is an estimated **or** actual event time by the
  GTFS-Realtime schema. The field alone is neutral and does not prove actual
  arrival.
- A past stop retaining a time does not by itself prove that the time is
  actual.
- A time becoming stable after the event does not by itself prove actual
  arrival.
- `TripUpdate.timestamp` is the moment realtime progress was last measured or
  updated for prediction purposes; it is not an arrival time.
- `FeedHeader.timestamp` is the feed production timestamp; it is not an
  arrival time.
- GTFS-Realtime permits StopTimeUpdates for past events, and a producer may
  drop past StopTimeUpdates. Therefore, a past event can remain visible or
  disappear without either outcome proving actual arrival.
- Missing entities, missing StopTimeUpdates, and `NO_DATA` are preserved as
  unknown or distinct states. They are not converted to on-time, served, or
  cancelled conclusions.

The script uses neutral names such as `gtfs_rt_arrival_time_utc`,
`gtfs_rt_departure_time_utc`, and `reported_event_time`. It does not emit
`actual_arrival_time`, `actual_departure_time`, or `actual_delay`.

## 7. StopTimeEvent.time ambiguity

The standard GTFS-Realtime schema deliberately permits a `StopTimeEvent.time`
value to represent an estimated or actual event time. The standard schema does
not provide a universal boolean saying that a value is a confirmed physical
arrival. The lifecycle report therefore separates observed time evolution
from actual-time semantics.

The script reviews the installed protobuf descriptors and exposed extensions.
An extension is surfaced for manual review as ambiguous; a field name alone is
never treated as proof of actual timing. With only standard fields exposed,
the result is `NO_EXPLICIT_ACTUAL_SEMANTIC_OBSERVED`.

## 8. FeedHeader.timestamp semantics

`FeedHeader.timestamp` is recorded as `feed_header_timestamp_utc`. The script
derives `feed_age_seconds_at_download` from the request timestamp and reports
it only as feed freshness evidence. It is not used as a vehicle event time or
as an actual arrival substitute.

## 9. TripUpdate.timestamp semantics

`TripUpdate.timestamp` is recorded independently as
`trip_update_timestamp_utc`, with an observation-age value where available.
Lifecycle mode tracks whether that timestamp advances for the same selected
target. Advancement indicates changing or refreshed realtime information; it
does not establish that an arrival occurred.

## 10. Köln Hbf and Köln Neumarkt discovery scope

The script uses the following identifiers only as structurally checked
discovery prefixes:

| Station | Prefix |
|---|---|
| Köln Hbf | `de:05315:11201` |
| Köln Neumarkt | `de:05315:11111` |

An exact parent ID or an ID beginning with `<prefix>:` is accepted. A
platform/mast child identifier is not hardcoded. A string that merely contains
a similar number is not accepted as a station match.

The inventory reports exact-parent and child-prefix examples separately and
caps printed examples. A station match is discovery evidence, not a claim that
the public feed covers the complete Cologne network or the project's scope.

## 11. Inventory test procedure

Run inventory once after installing the pinned binding. It makes exactly one
HTTP request, parses a successful HTTP 200 body as `FeedMessage`, validates the
feed header, counts entity and timing fields, inspects structural alert
references, checks the two station prefixes, and emits one JSON Lines summary.

Inventory records include:

- HTTP status, elapsed time, response bytes, Content-Length comparison, parse
  result, feed version, incrementality, header timestamp, and feed age;
- TripUpdate, VehiclePosition, ServiceAlert, and other supported entity
  counts;
- timing-field counts for arrival/departure time, delay, and uncertainty;
- trip/stop schedule relationships, cancellation, skipped-stop, `NO_DATA`,
  and `assigned_stop_id` counts;
- bounded identifier examples and station-prefix matches; and
- structural ServiceAlert informed-entity counts without dumping alert text.

The final inventory classification is exactly one of:

`GTFS_RT_REACHABLE_WITH_COLOGNE_EVIDENCE`,
`GTFS_RT_REACHABLE_NO_COLOGNE_EVIDENCE_FOUND`,
`GTFS_RT_REACHABLE_NO_TRIP_UPDATES`, `GTFS_RT_PARSE_FAILED`,
`GTFS_RT_HTTP_FAILED`, or `INCONCLUSIVE`.

A single inventory snapshot is not a source-quality conclusion.

## 12. Lifecycle test procedure

Lifecycle mode runs one station at a time. Discovery is observation 0 and
HTTP attempt 1. A candidate is preferred when it has a trip ID, station stop
ID, usable arrival time 8–25 minutes in the future, non-cancelled trip state,
and non-`SKIPPED`/non-`NO_DATA` stop state. A departure-time candidate is used
only when no suitable arrival candidate exists, and the context is labelled
`DEPARTURE`.

The selected identity preserves the feed entity ID, trip ID, route ID,
direction ID, start date/time, trip relationship, stop ID, stop sequence, and
arrival/departure context when available. The script never replaces the target
with another trip. If the same target cannot be uniquely matched later, it
reports `TARGET_AMBIGUOUS` rather than selecting a replacement.

Follow-up checkpoints are approximately T-5, T-1, T+1, T+5, T+15, and T+30
minutes relative to the selected initial event time. The maximum is seven HTTP
attempts including discovery. A checkpoint already in the past is marked
`SKIPPED_ALREADY_PAST` without an extra request. One lifecycle run may take
approximately 20–50 minutes depending on target selection.

For the same target, lifecycle analysis records first/last/minimum/maximum
reported event time, distinct times, changes, first past observation, post-
event visibility, disappearance, schedule-relationship changes, advancing
TripUpdate timestamps, cancellation, `SKIPPED`, and `NO_DATA` evidence.

## 13. Static compatibility caveat

The public feed is documented as matching GTFS.de static feeds, while this
project currently uses a VRS/go.Rheinland static GTFS source. Compatibility is
therefore measured separately and is never assumed.

Without `--static-gtfs`, the result is exactly
`LOCAL_STATIC_COMPATIBILITY_NOT_TESTED`. The script does not download a static
feed. With an existing local ZIP, it reads `trips.txt`, `routes.txt`,
`stops.txt`, and `stop_times.txt` without modifying or extracting them.

For TripUpdates with a tested Hbf/Neumarkt stop, it reports exact trip and
route matches, exact stop matches, explicit station-parent-prefix-only
evidence, missing stop IDs, and stop-sequence consistency. A sequence is not
declared compatible merely because its number looks plausible. The descriptive
classification is based on the measurements:

- `STATIC_IDENTITY_HIGH_COMPATIBILITY`: supplied identifiers are exact and all
  testable stop sequences are consistent;
- `STATIC_IDENTITY_PARTIAL_COMPATIBILITY`: some exact identity evidence exists
  but there are parent-only, missing, or inconsistent dimensions;
- `STATIC_IDENTITY_LOW_COMPATIBILITY`: no exact identity evidence is available
  for the relevant observations while direct mismatches are present;
- `LOCAL_STATIC_COMPATIBILITY_NOT_TESTED`: no local ZIP was supplied; or
- `INCONCLUSIVE`: the ZIP/feed evidence cannot support a classification.

The measurements, not the label alone, are the compatibility result.

## 14. Cancellation, SKIPPED, and NO_DATA semantics

The official GTFS-Realtime reference defines explicit operational relationships
for a trip and for an individual StopTimeUpdate. The audit preserves them as
distinct:

- TripDescriptor `CANCELED` is explicit trip cancellation evidence.
- StopTimeUpdate `SKIPPED` is explicit skipped-stop evidence and does not mean
  the whole trip was cancelled.
- StopTimeUpdate `NO_DATA` is an explicit lack-of-stop-data state and is not
  converted to cancellation.
- `UNSCHEDULED`, `NEW`, `REPLACEMENT`, and `DUPLICATED` are retained in
  relationship counts when exposed by the installed binding.
- A missing TripUpdate does not mean on time.
- A missing StopTimeUpdate does not mean the stop was served.

## 15. Raw snapshot policy

For diagnostic reproducibility, successful HTTP 200 response bytes may be
written as `.pb` files in a private directory such as
`/private/tmp/cologne-transit-gtfs-rt-audit-XXXXXXXX/`. The directory is
created with mode 700 and files with mode 600 where the host permits it. The
directory is never inside the repository, is not git-added, and is not a
permanent raw source of record. The final JSON summary prints its path so the
host operator can manage the temporary artifacts.

The policy does not alter the approved Stage 2 no-permanent-raw-retention
decision.

## 16. Request budget

| Mode | Maximum HTTP attempts | Network behavior |
|---|---:|---|
| `inventory` | 1 | one GET to the public feed, no retry |
| `lifecycle` | 7 | one discovery GET plus six scheduled sequential GETs, no retry |

Redirects are rejected rather than followed into hidden requests. Static GTFS
compatibility reads only a user-supplied local ZIP. No credentials, API keys,
MDD endpoints, authenticated VRS endpoints, database, scheduler, or OS/network
configuration are accessed.

## 17. Interpretation rules

The following distinctions are mandatory:

- reported event time is not automatically actual event time;
- time stability is not actual-arrival confirmation;
- post-event visibility is not actual-arrival confirmation;
- `TripUpdate.timestamp` and `FeedHeader.timestamp` are not arrival times;
- cancellation, skipped stop, and `NO_DATA` remain separate;
- missing data remains unknown; and
- one inventory or lifecycle run is evidence for the tested feed population
  and selected journey only.

## 18. What would constitute positive actual-time evidence

Positive actual-time evidence would require an explicit provider semantic,
documented extension, or independently validated provider contract that
identifies an event as observed, recorded, or confirmed actual. The evidence
must be tied to the same event identity and be sufficiently reproducible to
support the project's actual-delay rules. A future source-contract decision
would need to document that evidence before any Actual Delay KPI could be
approved.

## 19. What would **not** constitute positive actual-time evidence

None of the following is sufficient by itself:

- a populated standard `StopTimeEvent.time`;
- a timestamp after the scheduled event;
- a past stop retaining a time;
- a time remaining unchanged across snapshots;
- an advancing `TripUpdate.timestamp`;
- a feed or response timestamp;
- an estimate equal to the timetable time; or
- disappearance of a StopTimeUpdate.

## 20. Result template

Host results should preserve the emitted JSON Lines and record at least:

```text
inventory_classification: <one allowed inventory classification>
actual_time_semantic_classification: <one allowed semantic classification>
static_compatibility_classification: <one allowed compatibility classification>
raw_temp_directory: <temporary path>

lifecycle_classification: <one allowed lifecycle classification>
collector_implication: <evidence-based wording only>
```

Lifecycle semantic classification is separate from lifecycle classification:

`EXPLICIT_ACTUAL_SEMANTIC_OBSERVED`,
`NO_EXPLICIT_ACTUAL_SEMANTIC_OBSERVED`, or
`ACTUAL_SEMANTIC_AMBIGUOUS`.

For standard `StopTimeEvent.time` alone, the result must remain ambiguous as
to estimated versus actual meaning; the lifecycle label must not be upgraded
to explicit actual semantics.

## 21. Status / next action

Current status is **HOST EXECUTION PENDING**. The next action is to install the
pinned binding and run inventory manually from the normal macOS Terminal. The
inventory output must be reviewed before any lifecycle run is started:

```bash
cd /Users/admin/Documents/cologne-transit-intelligence
python3 -m pip install -r scripts/requirements-gtfs-rt-audit.txt
python3 scripts/gtfs_rt_feasibility_probe.py inventory
```

Only after reviewing that inventory should the operator run, one station at a
time if appropriate:

```bash
python3 scripts/gtfs_rt_feasibility_probe.py lifecycle --station hbf
python3 scripts/gtfs_rt_feasibility_probe.py lifecycle --station neumarkt
```

No GTFS-RT production approval, architecture change, Stage 3 start, MDD
collector change, database change, Power BI change, or final architectural
decision is made before host evidence exists.

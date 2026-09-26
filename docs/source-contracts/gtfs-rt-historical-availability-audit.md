# GTFS-Realtime Historical Availability Audit

**Status:** `POST-STAGE-02 VALIDATION — HOST EXECUTION PENDING`<br>
**Task:** S19<br>
**Stage position:** Stage 0 — **APPROVED**; Stage 1 — **APPROVED**; Stage 2 —
**APPROVED**; Stage 3 — **NEXT / NOT STARTED**<br>
**Production decision:** none; GTFS-Realtime remains non-production.

## 1. Purpose

S19 tests one narrow question: can an isolated GTFS-Realtime protobuf snapshot
for the exact public GTFS.de realtime resource be recovered near three and six
calendar months before a selected audit date?

The initial project audit date is `2026-09-26`, so the deterministic target
dates are:

| Horizon | Target date | Target reference |
|---|---|---|
| 3 calendar months | `2026-06-26` | `2026-06-26T12:00:00Z` |
| 6 calendar months | `2026-03-26` | `2026-03-26T12:00:00Z` |

This is an availability audit, not a production implementation, retention
approval, source-selection decision, or continuous-history reconstruction.

## 2. S18 evidence motivating the test

S18 established that the current public feed is a valid GTFS-Realtime 2.0
`FULL_DATASET` feed with TripUpdates, StopTimeUpdates, and ServiceAlerts. The
tested current snapshot contained widely populated arrival/departure times and
delays, `SKIPPED` and `NO_DATA` StopTimeUpdates, no observed VehiclePositions,
and no provider-specific explicit actual-event semantic. `TripUpdate.timestamp`
was not populated in the tested inventory and `route_id` was absent from the
tested TripUpdates.

The bounded S18 lifecycle validation also showed that a past StopTimeUpdate may
remain visible or disappear while the TripUpdate remains present. Disappearance
does not mean served, on-time, cancelled, or skipped. A reported
`StopTimeEvent.time` remained an estimated-or-actual event time; historical
observation did not convert it into confirmed physical arrival/departure
evidence.

## 3. Why prospective collection remains important

Because past StopTimeUpdates are not guaranteed to remain available, a later
query may not reconstruct the realtime state that existed during an event.
S19 can test whether a third party happened to preserve one historical copy,
but it cannot provide controlled observation cadence or prove that missed
snapshots are recoverable. Prospective collection remains important if the
project needs a controlled operational realtime history.

## 4. Exact live-feed identity

The source identity under evaluation is exactly:

<https://realtime.gtfs.de/realtime-free.pb>

The output records `source_identity_scope = EXACT_URL_ONLY`. The CDX query
uses this URL as its exact archive lookup target. Query parameters, alternate
GTFS.de endpoints, HTTP variants, mirrors, and guessed provider history paths
are outside S19.

## 5. Why S19 does not fetch the current live feed

The current URL is a live stream, not a validated historical endpoint. S19
never opens `realtime.gtfs.de` directly. Historical retrieval is constructed
as a Wayback raw replay URL whose request destination is an approved Internet
Archive host. A redirect to `realtime.gtfs.de` is rejected before it can be
followed.

S19 also does not call MDD, authenticated VRS APIs, Transitland, Mobility
Database, search engines, GitHub, random mirrors, or another archive provider.

## 6. History terminology

The audit keeps these concepts separate:

- **Current realtime feed:** the provider's live protobuf.
- **Provider-supported history:** a historical archive explicitly supplied or
  supported by the provider. S19 does not validate one.
- **Third-party archived snapshot:** a copy captured by Internet Archive /
  Wayback.
- **Continuous historical coverage:** a sufficiently dense sequence of
  snapshots across a period.

Wayback evidence is third-party evidence. It cannot be converted into a claim
about provider-supported retention or complete operational history.

## 7. Calendar-month horizons

The probe subtracts calendar months using standard-library arithmetic rather
than treating three and six months as 90 and 180 days. The destination day is
clamped to the final valid day of the destination month. For example,
`2026-05-31 - 3 months` becomes `2026-02-28`.

`--as-of-date` is explicit for reproducible evidence. Its default is the
current UTC calendar date, but the project evidence command supplies
`2026-09-26` explicitly. `--target-hour-utc` defaults to `12`, producing the
reference point used for distance calculations.

## 8. Search window

For each target reference, the default Wayback search interval is inclusive:

```text
target_reference - 7 days through target_reference + 7 days
```

The interval is controlled by `--window-days`. S19 does not expand the window
automatically when no capture is found.

## 9. Exact-URL Wayback discovery

The probe makes one CDX metadata request per requested horizon to the Wayback
CDX API. It asks for only the fields needed for selection and provenance:
`timestamp`, `original`, `statuscode`, `mimetype`, `digest`, and `length`.

The query requests exact URL matching, uses successful archive captures where
practical, does not filter on MIME type, limits each result set to 100 rows,
and does not paginate or retry. A result set with exactly 100 metadata rows is
marked `capture_result_set_may_be_truncated = true`.

Invalid timestamps, non-200 rows, and rows whose `original` value is not the
exact source URL are not qualifying captures. An empty qualifying set after a
successful CDX request is classified as `NO_ARCHIVE_CAPTURE_FOUND`; it does
not prove that no historical copy exists elsewhere.

## 10. Nearest-capture selection

The script parses every valid qualifying timestamp in the configured window,
converts it to timezone-aware UTC, and selects the capture minimizing absolute
distance from the target reference. Equal-distance captures use the earlier
timestamp as the deterministic tie-break. The result records the count,
first/last capture times, selected metadata, and distance in seconds.

Capture count is selection evidence only. It is not an archive-density,
provider-retention, or continuous-coverage measure.

## 11. Raw replay

Normal mode replays at most one selected capture per horizon using Wayback's
raw `id_` form. `--metadata-only` stops after CDX discovery and returns
`ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED` when a candidate exists.

The replay records its URL, HTTP status, final host, elapsed time, response
content metadata, response byte count, and SHA-256 hash. It intentionally does
not render a Wayback HTML page or dump response bodies.

## 12. Redirect protection

The allowlist contains only `web.archive.org`. Every initial request is
validated against that allowlist. Redirect destinations are inspected by a
custom handler and are never followed automatically: a redirect to the live
GTFS.de host is classified as `LIVE_SOURCE_REDIRECT_REJECTED`, while an
approved-host redirect is also rejected at the single-request boundary to
preserve the bounded request budget. This prevents a hidden request from
turning the historical audit into a live-feed request.

## 13. Request budget

In normal mode, each horizon has at most one CDX request and, only when a
candidate exists, one replay request. The default two-horizon run therefore
has a maximum of four HTTP attempts. Metadata-only mode has a maximum of two.
There are no retries, concurrency, background workers, pagination loops, or
automatic window expansion. The counter increments before each planned HTTP
attempt and the final summary reports total and successful HTTP attempts.

## 14. Temporary raw storage

Downloaded response bytes are written only when a private temporary directory
can be created outside the repository, normally under
`/private/tmp/cologne-transit-gtfs-rt-history-XXXXXXXX/`. The directory is
created with mode `700` and snapshot files with mode `600` where the host
permits those modes. Raw payloads are not written into the repository and are
not approved for permanent retention.

## 15. Response and protobuf validation

The probe records HTTP status, content type, content length when supplied,
response bytes, and elapsed milliseconds before interpretation. Obvious HTML,
Wayback error, JSON, or text responses are rejected without body scraping.
Non-obvious binary content receives one bounded parse attempt as
`google.transit.gtfs_realtime_pb2.FeedMessage`.

Parse failure is classified as `ARCHIVE_REPLAY_NOT_GTFS_RT_PROTOBUF`. The
probe does not run decompression guesses, format guesses, schema guesses,
embedded-binary scraping, or external conversion services.

## 16. FeedHeader temporal comparison

For a parseable snapshot, the probe records the GTFS-Realtime version,
incrementality, and `FeedHeader.timestamp` in UTC. It measures:

```text
feed_header_vs_archive_capture_delta_seconds
    = feed_header_timestamp - archive_capture_timestamp
```

It also records whether both timestamps fall on the same UTC date. The
diagnostic classes are `SAME_UTC_DATE`, `DIFFERENT_UTC_DATE`,
`HEADER_TIMESTAMP_MISSING`, and `INCONCLUSIVE`. No arbitrary delta threshold
is used to declare an archive payload authentic or invalid.

A parseable payload with a different UTC date receives
`ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE_TEMPORAL_MISMATCH`. It remains recoverable
for point-snapshot counting while retaining the mismatch evidence.

## 17. Historical feed inventory

Each parseable snapshot is inventoried without dumping the feed. The JSONL
record includes entity totals and TripUpdate, VehiclePosition, ServiceAlert,
and other-entity counts; StopTimeUpdate totals; TripUpdate timestamp presence;
arrival/departure time, delay, and uncertainty presence; assigned stop IDs;
trip and stop schedule-relationship counts; cancelled-trip, skipped-stop, and
`NO_DATA` counts; and unique trip, route, and stop ID counts.

Identifier example arrays are sorted and capped at ten values. The optional
current Neumarkt ID observation is explicitly labelled as non-proof and is
never used in the availability classification.

## 18. Actual-Time semantic guardrail

GTFS-Realtime `StopTimeEvent.time` remains an estimated-or-actual event time.
Historical presence of event times or delays does not establish confirmed
physical arrival/departure and does not approve an Actual Delay KPI. S19 emits
`actual_time_semantic_classification =
NO_EXPLICIT_ACTUAL_SEMANTIC_OBSERVED` for the standard schema evidence under
test. It does not create a confirmed physical event-time field or calculate an
Actual Delay KPI.

## 19. Station identity guardrail

S19 does not use current GTFS.de Neumarkt IDs as historical identity proof.
The current IDs (`106842`, `207108`, `441164`, `581903`, and `96309`) are
valid only for the current static/realtime identity evidence. They may have
changed between static feed versions.

Historical station classification is therefore always
`HISTORICAL_STATION_IDENTITY_NOT_TESTED`. Köln railway Hbf is not matched at
all; the two adjacent KVB complexes are not substitutes for railway Köln Hbf.

## 20. Static GTFS boundary

S19 downloads no static GTFS and does not compare an archived realtime payload
with `gtfsde-nv-latest.zip` or the current VRS/go.Rheinland feed. Historical
realtime/static compatibility needs a date-compatible static dataset and is
outside this task. The output records
`HISTORICAL_STATIC_COMPATIBILITY_NOT_TESTED`.

## 21. Per-horizon classifications

Each requested horizon has one primary classification:

- `NO_ARCHIVE_CAPTURE_FOUND`
- `ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED`
- `ARCHIVE_REPLAY_FAILED`
- `ARCHIVE_REPLAY_NOT_GTFS_RT_PROTOBUF`
- `ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE`
- `ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE_TEMPORAL_MISMATCH`
- `INCONCLUSIVE`

The two parseable classifications count as recoverable point snapshots. A
temporal mismatch remains a diagnostic flag and is not, by itself, proof that
the payload is invalid.

## 22. Aggregate classifications

The final summary uses only these aggregate values:

- `BOTH_3M_AND_6M_POINT_SNAPSHOTS_RECOVERABLE`
- `ONLY_3M_POINT_SNAPSHOT_RECOVERABLE`
- `ONLY_6M_POINT_SNAPSHOT_RECOVERABLE`
- `NO_3M_OR_6M_POINT_SNAPSHOT_RECOVERABLE`
- `THIRD_PARTY_ARCHIVE_METADATA_ONLY`
- `HISTORICAL_ARCHIVE_TEST_INCONCLUSIVE`

None of these values means that three or six months of continuous operational
history exists. In particular, the audit never emits a full-history or
provider-retention conclusion.

## 23. JSONL result template

The first record is `run_configuration`, followed by one `horizon_result` per
requested horizon, and a `final_summary` record. The concrete output contains
the inventory fields described above; the following abbreviated template
shows the key provenance and classification fields:

```json
{"record_type":"run_configuration","probe_name":"gtfs_rt_historical_availability_probe","source_identity_scope":"EXACT_URL_ONLY","archive_source":"Internet Archive / Wayback Machine","as_of_date":"2026-09-26","requested_month_horizons":[3,6],"window_days":7,"target_reference_hour_utc":12,"metadata_only":false,"maximum_http_attempts":4,"provider_history_endpoint_validated":false,"provider_history_retention_policy_validated":false}
{"record_type":"horizon_result","months_back":3,"target_date":"2026-06-26","archive_capture_count_in_window":0,"selected_capture_timestamp_utc":null,"archive_replay_attempted":false,"protobuf_parse_success":false,"historical_station_identity":"HISTORICAL_STATION_IDENTITY_NOT_TESTED","historical_static_compatibility":"HISTORICAL_STATIC_COMPATIBILITY_NOT_TESTED","continuous_archive_coverage_established":false,"historical_availability_classification":"NO_ARCHIVE_CAPTURE_FOUND"}
{"record_type":"final_summary","requested_horizons":[3,6],"total_http_attempts":2,"recoverable_point_snapshot_count":0,"provider_continuous_history_established":false,"continuous_archive_coverage_established":false,"third_party_archive_only":true,"actual_time_kpi_approved":false,"aggregate_history_classification":"NO_3M_OR_6M_POINT_SNAPSHOT_RECOVERABLE"}
```

The example is a result-shape illustration, not host evidence.

## 24. Interpretation rules

An archived parseable payload answers only whether one recoverable third-party
point snapshot was found near the requested reference. It does not establish:

- provider-supported historical retention;
- a provider history endpoint;
- continuous coverage between the 3-month and 6-month points;
- historical station identity compatibility;
- static/realtime compatibility for an old feed version; or
- confirmed physical arrival/departure or an Actual Delay KPI.

Absence of an exact Wayback capture is likewise not proof that no historical
copy exists elsewhere. Any later archive source or wider search window needs a
separate explicit approval after the bounded result is reviewed.

## 25. Next decision gate

The next step is host execution of the exact bounded command below, followed by
review of its JSONL evidence. No production collector, database, scheduler,
Power BI, MDD, Legacy, or architecture change is authorized by S19.

```bash
cd /Users/admin/Documents/cologne-transit-intelligence

python3 scripts/gtfs_rt_historical_availability_probe.py \
  --as-of-date 2026-09-26 \
  --months 3 6 \
  --window-days 7
```

The host command must be run manually. The archive window must not be widened
and another archive provider must not be added automatically before the first
bounded result is reviewed.

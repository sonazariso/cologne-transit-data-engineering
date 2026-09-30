# Semantic and Data Guardrails

These guardrails apply from project foundation unless explicitly replaced by a later approved decision.

The approved Stage 02 source-contract package hands future collector work to
the [realtime collector requirements](collector-design/realtime-collector-requirements.md);
implementation remains deferred. The separate [near-event RecordedAtTime
lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is bounded post-Stage-02 design evidence and is not a Stage 02 blocker.

1. Estimated timing is not Actual Physical Delay. `Estimated Delay` may be
   derived only from comparable `EstimatedTime` and `TimetabledTime` values and
   must be labelled explicitly.
2. Actual Delay requires validated recorded/actual timing evidence, with
   optional TRIAS 1.2 `RecordedAtTime` the priority candidate. Its presence in
   the specification does not prove population in MDD responses.
3. NULL timing is not “on time.”
4. Missing estimate is not automatically a cancellation.
5. Situation/disruption evidence is not automatically proof of causality.
6. Platform/bay unknown is not the same as unchanged.
7. Static GTFS alone cannot prove actual delay, cancellation, or realtime disruption.
8. Raw observation count is not service count.
9. Technical matching status and analytical KPI scope are separate concepts.
10. A sampled station panel must not be presented as complete Cologne network observation.
11. Historical sampling eras with different cadences must remain distinguishable.
12. `NumberOfResults` controls events requested in one logical TRIAS request; it is not itself the number of HTTP requests.
13. Retries consume additional HTTP requests and quota.
14. Source fields are not assumed equivalent merely because their names look similar across GTFS/TRIAS.
15. Preserve unknown/NULL semantics unless a documented deterministic transformation is approved.
16. Do not fabricate missed historical realtime snapshots.
17. Do not delete legacy realtime history during restart work.
18. `NotServicedStop` is a stop-level service flag, not journey-level
    `Cancelled`.
19. `PlannedBay` and `EstimatedBay` are separate source fields; a missing
    `EstimatedBay` is not evidence that the platform was unchanged, and a
    difference is not proof of the final physical platform used.
20. A single controlled MDD response is not representative Cologne-wide,
    mode-wide, station-wide, operator-wide, or network coverage.
21. Full raw MDD responses are not retained permanently or automatically by
    default; temporary diagnostic artifacts require explicit purpose and remain
    outside source control without secrets.
22. A provider timestamp with an explicit `Z` and a lexical suffix such as the
    observed `[GMT]` may be normalized only according to the observed format.
    A timezone-less value remains timezone-unresolved for absolute-time work.
23. Comparing a timezone-less provider timestamp's lexical calendar date with
    the requested Europe/Berlin date is **DATE-ALIGNMENT ONLY**; it must not be
    used to fabricate a UTC instant or perform delay arithmetic.
24. Historical scheduled events without `EstimatedTime` or `RecordedAtTime`
    must not be treated as historical realtime or actual-delay evidence. A
    successful HTTP status alone does not establish historical alignment.
25. Later realtime collector implementation must preserve prospective
    observations because the tested historical StopEvent interface cannot be
    relied on to reconstruct missed realtime state.
26. A near-event lifecycle result is evidence about the tested event and
    coverage scenario only; it must not be generalized to provider-wide
    `RecordedAtTime` presence or absence without further validation.
27. MDD/TRIAS is the approved primary realtime source direction; GTFS-RT is
    complementary/secondary. This is a design direction, not production
    collector approval.
28. MDD and GTFS-RT namespaces and semantics must remain distinct. Current
    GTFS.de identity evidence must not be substituted for the project's
    VRS/go.Rheinland static identity without validation.
29. `StopTimeEvent.time` is estimated-or-actual by GTFS-RT schema; no
    provider-specific actual semantic was observed. It must not be promoted to
    confirmed Actual Time.
30. Source-faithful MDD and GTFS-RT observations must be staged separately
    before future normalization or reconciliation.
31. `ObservedAt` / `CapturedAt` is first-class future lineage, and repeated
    observations must preserve estimate evolution rather than only the latest
    state.
32. Missing GTFS-RT StopTimeUpdates and missing MDD events are observation
    states, not automatic evidence of on-time service, service, cancellation,
    skipping, or absence.
33. Permanent or automatic full raw XML/JSON/protobuf retention is not the
    default. Any bounded automatic raw capture requires an explicit later gate.
34. `TimetabledTime`, `EstimatedTime`, and source-provided `RecordedAtTime` are
    independent timing concepts. `RecordedAtTime` is nullable and source-only;
    it must never be derived from an estimate, timetable, `ObservedAt`,
    `CapturedAt`, or event disappearance.
35. `ObservedAt` / `CapturedAt` is collector lineage, not actual event time.
    When a non-null source `RecordedAtTime` is first observed, retain its exact
    value and conceptual first-seen observation timestamp. A later `NULL` or
    source disappearance must not overwrite or erase it.
36. Future observations must preserve `EstimatedTime` history and revisions,
    not only the latest estimate. Transport deduplication must not discard
    analytically distinct observations.
37. `ActualTimeCaptured = true` is a conceptual state reached only by capturing
    a non-null source `RecordedAtTime`. It may end actual-time-specific polling
    for an event, but it must not stop independent Situation, Cause,
    cancellation, or status tracking.
38. Source-reported Situation/Cause evidence is append-only historical evidence.
    Its disappearance must not erase earlier observations, and evolving
    versions must not be collapsed into one mutable Cause value. Do not infer
    root cause or create a normalized cause taxonomy in the current stage.
39. The intended future evidence flow is `SOURCE -> COLLECTOR -> APPEND-ONLY
    SOURCE OBSERVATIONS -> DERIVED ANALYTICAL STATE`; source disappearance must
    never erase previously observed evidence.
40. PostgreSQL 18 is the approved future authoritative platform direction for
    the redesigned architecture, with JSONB only where later justified. This
    does not change source availability, create PostgreSQL tables, migrate SQL
    Server assets, or authorize production persistence.
41. A future short-term raw-payload retention option may be evaluated for
    forensic reprocessing and parser improvements. No duration is approved;
    storage volume, privacy/data considerations, reprocessing need, and
    operational cost must be considered, and permanent raw retention is not the
    default.
42. Source `route_id`/route identity is not geographical Cologne scope. A
    Cologne-serving route or trip must not make all of its external stops part
    of a Cologne KPI.
43. One validated Cologne stop event does not make an entire trip or route an
    internal Cologne movement or full-trip KPI subject.
44. A Cologne-internal segment requires two consecutive scheduled stop events
    with both actual endpoint `stop_id` values validated inside Cologne.
45. Mode classification never expands geographical scope. RE, RB,
    Regional/Other Bus, KVB Bus, Tram, S-Bahn, and SEV remain subject to the
    validated stop-event/segment boundary.
46. A `parent_station` relationship must not silently override the actual
    source `stop_id` referenced by `stop_times.txt`.
47. Unknown, unsupported, duplicate-ambiguous, or unresolved route/mode values
    remain explicit and out of core scope; they must not be forced into a
    named core mode to eliminate an unresolved bucket.
48. Static GTFS mode and scope evidence describes scheduled source coverage
    only. It is not realtime operation, actual delay, cancellation, or current
    performance evidence.
49. `src` is durable source-faithful evidence, not disposable staging. Derived
    current state belongs in rebuildable or validated layers and must not
    replace source history.
50. Source natural IDs are not universal cross-source keys. Source duplicates
    must remain visible until a validated layer establishes a documented grain.
51. `RecordedAtTime` is nullable source-only evidence and is never synthesized
    from `EstimatedTime`, `TimetabledTime`, `ObservedAt`, collector time, or
    event disappearance.
52. `NULL` is not false, zero, on-time, served, cancelled, skipped, or
    unchanged. A later NULL or disappearance must not erase prior evidence.
53. Static source snapshots are content-identified. Re-fetching the same
    approved ZIP/hash adds retrieval/audit evidence but must not duplicate all
    parsed source rows.
54. JSONB is bounded to provider extensions, rare/unmodeled source fragments,
    and source-shaped metadata; it must not replace relational fields or hold
    credentials.
55. Full Germany-wide GTFS-RT snapshots are not approved for permanent
    storage by default. GTFS-RT persistence must be explicitly selected and
    scoped before implementation.
56. Large append-only realtime tables may use monthly range partitioning by
    `ObservedAtUtc` after implementation design accounts for PostgreSQL
    uniqueness and foreign-key requirements; tables are not partitioned merely
    for completeness.
57. Every realtime observation must retain source, collector-run,
    sampling-policy/era, and `ObservedAt` lineage. Changing cadence must not
    rewrite history.
58. Once production realtime collection begins, the durable source layer must
    have tested backup/recovery protection appropriate to irrecoverable
    observations.
59. Stage 5 approved a clean PostgreSQL redesign only. Legacy SQL Server is not
    copied 1:1; any later import must preserve source IDs/timestamps/NULLs and
    distinguish legacy rows from PostgreSQL-native observations.
60. The current Stage 4 static-source counts are validated source baselines and
    future reconciliation targets, not results from a physical warehouse.
61. `FactScheduledTrip` is a scheduled trip-pattern fact, not one row per
    active service date.
62. `FactScheduledStopEvent` is a scheduled stop-event pattern fact, not one
    row per active service date and not a realtime/actual-outcome fact.
63. `BridgeServiceDate` owns active `Service + Date` expansion; it must not be
    confused with trip-pattern or stop-event-pattern grain.
64. `DateKey` does not enter the `FactScheduledStopEvent` grain. Date-expanded
    analytics must use `BridgeServiceDate` in a separate derived/query output.
65. GTFS schedule times greater than `24:00:00` retain their service-day
    offset and must not be wrapped to a civil-day time without preserving that
    offset.
66. A route/trip serving label does not expand Cologne geography to external
    route/trip stops.
67. The actual source `stop_id` referenced by `stop_times.txt` controls
    scheduled-event geography; `parent_station` does not override it.
68. Stage 4 is the authoritative source mode classifier. Mode classification
    never expands geography and unresolved classifications remain explicit.
69. Static GTFS schedule/calendar evidence does not prove actual operation,
    actual delay, actual cancellation, operational reliability, or physical
    service delivery.
70. PostgreSQL migration, runtime, database/schema/table creation, SQL,
    migration, and data loading remain deferred until explicit user
    authorization.
71. The active Stage 8 V3 pilot cadence is a fixed start-to-start MDD slot at
    `T0`, `T0 + 3s`, `T0 + 6s`, and so on for at most 60 minutes, with at most
    1200 scheduled logical slots and 1200 normal HTTP attempts. It is a bounded
    diagnostic cadence, not a production cadence; Stage 13 owns production
    cadence/readiness analysis.
72. One Stage 8 station, Köln Hbf, is source/parser/persistence validation
    evidence only and must not be presented as Cologne-wide coverage.
73. `NumberOfResults` is a per-response result limit, not an HTTP request
    count.
74. Stage 8 uses zero retries. A failed slot is recorded as a gap/failure and
    is not silently retried.
75. Stage 8 must not issue concurrent or overlapping MDD requests. If a fixed
    slot arrives while the prior request is active, record
    `SCHEDULED_SLOT_SKIPPED_OVERLAP`; do not queue it.
76. Stage 8 must not queue catch-up requests after a missed or skipped slot,
    and must not shift the fixed start-to-start schedule or wait three seconds
    after response completion.
77. Stage 8 omits `TimeWindow`; this records a pilot selection, not a claim
    that the TRIAS semantic option is unsupported.
78. `RecordedAtTime` remains exact, nullable, source-only evidence. It must
    never be synthesized from the last estimate, any estimate, timetable,
    request time, `ObservedAt`, or event disappearance.
79. The last or most stable `EstimatedTime` is not actual time and must not be
    promoted to Actual Delay evidence.
80. Stage 8 raw payloads are temporary diagnostic artifacts only, must remain
    outside Git with the approved protected permissions, and must be deleted
    after review within the approved maximum retention.
81. Stage 8 append-only JSONL artifacts are diagnostic pilot persistence, not
    the production database, PostgreSQL implementation, or a mutable latest-
    event store.
82. GTFS-RT is excluded from the Stage 8 one-source pilot; its complementary
    role and later cross-source matching remain separate.
83. Actual Delay remains **NOT APPROVED**. A Stage 8 `RecordedAtTime` result is
    candidate evidence only and cannot become a production KPI without later
    semantic, coverage, matching, and operational approval.
84. The active Stage 8 V3 pilot selects `PilotAnchorDepArrTime` at pilot start
    and reuses that fixed `DepArrTime` for every request; it must not advance
    with wall-clock time.
85. If source `RecordedAtTime` is observed once in any retained `THIS`,
    `PREVIOUS`, or `ONWARD` context for either arrival or departure, the exact
    value and its event/source identity, call scope, context, response identity,
    `ObservedAt`, and first-seen timestamp must be written immediately to
    append-only pilot persistence. Later `NULL` or disappearance must not erase
    it, and equal-valued successful responses remain distinct observations.
86. Three-second sampling materially reduces but cannot mathematically
    guarantee capture of a transient `RecordedAtTime`.
    `RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT` means absence in the
    observed sampling window, not provider-wide absence; a shorter validation
    requires separate approval and no shorter production cadence is
    pre-approved.
87. The conservative monthly MDD ceiling remains exactly `250,000` requests.
    Normal MDD planning must not consume the full ceiling; the planned monthly
    operating envelope is `207,500–210,000` requests, or `83–84%`.
88. Preserve `40,000–42,500` requests/month, approximately `16–17%`, as
    unallocated headroom. Stage 13 must size normal production collection
    inside the `207,500–210,000` envelope unless the user changes this
    decision.
89. An HTTP `429` stops the Stage 8 pilot immediately, is classified as
    rate-limit/provider evidence, and is never retried.
90. Credential preflight must validate the API-key header format before any
    temporary evidence directory, request construction, or network transport.
    Missing keys retain `MDD_API_KEY: MISSING`; unsafe values are reported only
    as `MDD_API_KEY: INVALID_FORMAT` and classified as
    `CREDENTIAL_FORMAT_INVALID`. The value, length, position, hash, and any
    derived secret information must never be printed, logged, or persisted.
91. A successful response with a non-null source `RecordedAtTime` receives the
    marker `RECORDED_AT_TIME_RAW_EVIDENCE`; only its corresponding raw payload
    may remain temporarily for review. Ordinary successful artifacts are
    cleaned normally. The intentional retention status is
    `RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS`, not a cleanup failure.
92. Positive raw evidence remains in mode `600` files under a mode `700`
    temporary directory, is never committed or published, and must be deleted
    within the current maximum of 24 hours. A longer raw-retention period
    requires a separate explicit decision.
93. The parsed source-faithful `RecordedAtTime` record and lineage are the
    durable evidence; the full raw payload is temporary and is never the
    authoritative warehouse source. Zero occurrences retain only sanitized
    aggregate evidence for the bounded observed sample and do not prove
    provider-wide absence.
94. This hardening change is prospective only. Stage 8 remains **COMPLETE**;
    the fixed `TIERED50_PANEL_V1` objective and the `250,000` monthly quota are
    unchanged. Stage 13 retains evidence-ready technically evaluated options,
    but its material production choices require explicit user approval; Stage
    9+ physical implementation, scheduler work, and activation remain
    separately gated.
95. The historical `10/20/20` Tiered50 plan at 5/10/30 minutes is comparison
    evidence only. The technically evaluated Stage 13 option uses the fixed
    10/20/20 panel counts at 5/15/30 minutes, with station membership assigned
    from exact-feed density evidence and never from Stage 12 `SelectionRank`;
    explicit user approval is required before it is final.
96. The unique future realtime execution identity is
    `(SamplingTargetId, LogicalDueBucket)`. Claims must be atomic and durable;
    a duplicate claim must not issue another HTTP request.
97. A logical due bucket is a deterministic UTC five-minute bucket. A later
    invocation may continue from the current wall-clock bucket, but it must
    not replay a missed historical bucket or fabricate a realtime snapshot.
98. Future phase assignments must be deterministic, persisted/configured,
    auditable, versioned with the sampling panel, and independent of mutable
    rank ordering.
99. One failed station must not abort other due targets. Audit, HTTP status,
    attempts, duration, parser, persistence, and error metadata remain
    independently attributable to each target.
100. Stage 13 records `NumberOfResults=30` as a technically evaluated option
     only. Exact-feed static P95 cadence-window density and the retained
     bounded multi-representative N5/N10/N20/N30 evidence support evaluation,
     but the historical mixed-call temporal-span fields are
     `INVALID_FOR_FORWARD_COVERAGE_DECISION`. The corrected metric uses only
     ThisCall arrival timing at or after the requested `DepArrTime` anchor; the
     provider theoretical maximum remains unknown.
101. Unbounded MDD concurrency is prohibited. Stage 13 records a maximum of
     two concurrent workers as a technically evaluated bounded option after the
     cap-2 diagnostic showed no pressure signal and preserved failure
     isolation. Explicit user approval is required before it is treated as a
     production decision; this is not a provider-wide rate-limit claim or
     activation.
102. Current shared-credential production usage must be auditable before
     activation. Missing current `ctl.MddCollectorRun` history must not be
     represented as zero usage, and completed/inactive diagnostics do not
     receive a standing quota reserve.
103. Stage 13 readiness analysis does not authorize Stage 14, production
     scheduler implementation, database objects, Power BI work, Windows tasks,
     or Tiered50 activation. Stage 13 is
     `IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED`; production
     remains not started.
104. Stage 12 panel membership is fixed after approval. A dynamic station
     replacement is prohibited during collection; a membership change requires
     a new version with effective date/time, reason, and predecessor panel
     version.
105. The approved Stage 12 panel version is `TIERED50_PANEL_V1`; historical
     Legacy7 identities must never be deleted, and reporting must distinguish
     sampling/panel eras.
106. A Stage 12 `SelectionRank` is panel ordering evidence only. It must not be
     interpreted as a production tier, cadence, result limit, concurrency,
     retry budget, phase assignment, or overlap-claim decision.
107. Parent-station panel membership requires a validated VRS source parent
     with `location_type=1`, direct Cologne source identity, valid
     `parent_station` child hierarchy, and scheduled child-stop evidence.
     Names, operators, coordinates, and route labels do not establish Cologne
     identity.
108. Scheduled stop-event volume is timetable/service evidence, not passenger
     demand. Ridership must not be inferred or invented from static schedule
     density.
109. A COMPLETE Stage 12 selection must retain a sanitized, exact-snapshot
     candidate-universe artifact sufficient for offline replay through the
     authoritative selector. The artifact must contain no raw GTFS ZIP, raw
     MDD payload, or secret; deterministic algorithm reproducibility and exact
     durable-input replayability are separate required gates.
110. Stage 13 diagnostics may use only the inherited `MDD_API_KEY` environment
     value. If it is missing, the diagnostic makes zero requests. The key,
     request headers, raw MDD payloads, and credential-bearing errors must not
     be persisted, printed, hashed, or committed; a 401/403 stops the run.
111. The historical Stage 13 diagnostic budget was at most 100 new HTTP
     attempts, including the original result-limit and cap-2 checks. Reuse of
     already sanitized evidence does not issue another request and must remain
     visibly distinguished from a new live run. Any separately approved
     extension must be recorded explicitly rather than silently changing the
     historical ceiling.
112. Stage 13 must distinguish `TECHNICALLY_EVALUATED_OPTION` from
     `USER_APPROVED_DESIGN` in machine-readable evidence. Until explicit user
     approval, `production_number_of_results_approved`,
     `production_concurrency_approved`, `production_cadence_approved`, and
     `production_retry_policy_approved` must all remain `false`, and Stage 13
     must remain `IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED`.
113. The historical Stage 13 `forward_temporal_span_seconds` values are not
     valid result-limit forward-coverage evidence. Forward coverage must use
     only the requested StopEventResult's THIS-call arrival timing at or after
     the requested `DepArrTime` anchor; PreviousCall, OnwardCall, departure,
     and route-context timings are contextual evidence only.
114. Because raw MDD responses were not retained, the corrected Stage 13
     NumberOfResults comparison required explicit approval for exactly 24
     additional sequential MDD requests. That approval was granted and exactly
     24 attempts were consumed, increasing cumulative diagnostic attempts from
     90 to 114. The historical 100-attempt ceiling was not silently changed;
     all 24 attempts ended in sanitized `NETWORK_OR_DNS_FAILURE`, and no
     further diagnostic request is authorized by this result.
115. The corrected Stage 13 run must report fixed N20, fixed N30, and
     tier-specific A20/B30/C30 separately. If no valid THIS-call response
     exists, NumberOfResults remains undecided; static P95 support does not
     finalize a realtime result limit or establish a provider maximum. Current
     user recommendations for `8/20/22 @ 5/15/30`, bounded concurrency 2, and
     `MaxAttempts=2` with one bounded transient retry remain recommendations,
     not approvals.

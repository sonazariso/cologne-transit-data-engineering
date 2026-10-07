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
40. The earlier PostgreSQL 18 direction for the redesigned architecture is
    historical/deferred-cleanup material and is superseded for the active
    roadmap by S87. Microsoft SQL Server is the sole authoritative project
    database. The active Stage 14 overall status is
    `HISTORICAL IMPLEMENTATION RETAINED — SQL SERVER CUTOVER DEFERRED`; its
    collector implementation remains `STAGE 14 COLLECTOR IMPLEMENTATION
    COMPLETE`, and Stage 15 live remains `NOT STARTED — AWAITS TIERED50 CUTOVER`.
    This does not change source availability, create PostgreSQL
    tables, migrate SQL Server assets, or authorize production persistence.
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
87. The absolute provider MDD ceiling remains exactly `250,000` requests/month.
    Normal planned operation must not intentionally exceed the separate
    `210,000` normal operational maximum without a new explicit decision.
88. The `207,500` value is the conservative automatic-retry protection
    boundary: automatic retries must be blocked when projected/current shared-
    credential monthly consumption would exceed it. There is no requirement to
    consume at least `207,500`; lower utilization is valid and preserves quota
    headroom. Stage 13's approved 30/31-day conservative totals are `182,980`
    and `189,076`, safely below both operational thresholds.
    Stage 7's historical `207,500–210,000` / 83–84% planning target remains
    historical context only and is not a current minimum-utilization gate.
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
    unchanged. Stage 13 is now **COMPLETE — APPROVED TIERED COLLECTOR
    READINESS DESIGN; PRODUCTION NOT STARTED** after the explicit approval in
    `prompts/S68.md`. Stage 14, physical implementation, scheduler work,
    and activation remain separately gated.
95. The approved Stage 13 density plan assigns ranks 1–8 / 9–28 / 29–50 to
    Tier A/B/C, with 8 / 20 / 22 stations at 5 / 15 / 30-minute cadence.
    Station membership uses retained density evidence and immutable panel
    identity, never Stage 12 `SelectionRank`. The historical 10/20/20
    plan is comparison evidence only.
96. The unique future realtime execution identity is
    `(SamplingTargetId, LogicalDueBucket)`. Claims must be atomic and durable;
    a duplicate claim must not issue another HTTP request.
97. A logical due bucket is a deterministic UTC five-minute bucket. A later
    invocation may continue from the current wall-clock bucket, but it must
    not replay a missed historical bucket or fabricate a realtime snapshot.
98. Future phase assignments must be deterministic, persisted/configured,
    auditable, versioned with the sampling panel, and independent of mutable
    rank ordering. For the approved Stage 13 design, the assignment identity
    is `source_parent_station_id / parent StopPointRef` and the hash is
    `SHA-256(panel version + tier + source_parent_station_id)`; production
    phase-index persistence is a Stage 14 materialization obligation. The
    approved due sequence is 19/18/18/18/19/18, with capacities A 8, B 7/7/6,
    and C 4/3/4/3/4/4.
99. One failed station must not abort other due targets. Audit, HTTP status,
    attempts, duration, parser, persistence, and error metadata remain
    independently attributable to each target.
100. The approved production result limits are Tier A/B/C
     `NumberOfResults` 20 / 30 / 30. Retained static evidence supports
     maximum station P95/max observed counts of A 19/20, B 23/23, and C 29/29,
     with 50/50 total P95 support. This does not establish realtime
     completeness, guaranteed event availability, or a provider-wide maximum.
101. Unbounded MDD concurrency is prohibited. Maximum concurrency 2 is an
     approved bounded design based on the retained cap-2 diagnostic and
     failure isolation. Stage 14 must validate the complete persistence path;
     this is not a provider-wide rate-limit claim or activation.
102. Current shared-credential production usage must be auditable before
     activation. Missing current `ctl.MddCollectorRun` history must not be
     represented as zero usage. The approved normal retry reserve is 10% of
     nominal Tiered50 requests, and automatic retries must be blocked when
     projected/current shared-credential monthly consumption would exceed
     207,500 across all known credential consumers. The Stage 14 runtime
     decision must account for actual audited current-month shared-credential
     attempts, projected remaining unavoidable/non-retry consumption for known
     enabled consumers, the candidate retry, and any approved reserved
     shared-account consumption. Current Legacy7 usage must not be fabricated;
     until the audit path exists, retain the conservative Legacy7 reservation.
103. Stage 13 readiness analysis does not authorize Stage 14, production
     scheduler implementation, database objects, Power BI work, Windows tasks,
     or Tiered50 activation. Stage 13 is
     `COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED`;
     production remains not started. D-051 separately authorizes the
     cross-platform, offline Stage 14 collector implementation and validation;
     production persistence, scheduling, and activation remain gated.
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
111. The historical Stage 13 diagnostic budget was at most 100 new client-side
     diagnostic invocations, including the original result-limit and cap-2
     checks. Confirmed provider HTTP attempts must be derived from retained
     response evidence rather than assumed from that invocation budget. The
     separately approved 24-invocation extension is retained in the chronology
     and does not rewrite the historical budget. Reuse of sanitized evidence
     issues no request; this Stage 13 closeout issued no new request.
112. Stage 13 machine-readable evidence distinguishes
     `TECHNICALLY_EVALUATED_OPTION` from `USER_APPROVED_DESIGN`. The explicit
     approval sets `production_number_of_results_approved`,
     `production_concurrency_approved`, `production_cadence_approved`, and
     `production_retry_policy_approved` to `true`. Stage 14 and all
     production activation work remain separately gated.
113. The historical Stage 13 `forward_temporal_span_seconds` values are not
     valid result-limit forward-coverage evidence. Forward coverage must use
     only the requested StopEventResult's THIS-call arrival timing at or after
     the requested `DepArrTime` anchor; PreviousCall, OnwardCall, departure,
     and route-context timings are contextual evidence only.
114. Because raw MDD responses were not retained, the corrected Stage 13
     NumberOfResults comparison used exactly 24 additional client-side MDD
     request invocations: 0 HTTP responses, 0 confirmed provider HTTP attempts,
     and unknown provider quota consumption. The 24 invocations are retained as
     `conservative_quota_accounting_attempts`. Cumulative client invocations
     are 114, not 114 claimed provider requests or quota units. The retained
     sanitized failure category is `OTHER_NETWORK_FAILURE`; the exact
     limitation is `CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT`.
115. The final approved tier-specific result-limit design is A20/B30/C30.
     `PROVIDER_MAXIMUM_NOT_ESTABLISHED` remains explicit, and no claim may
     be made for guaranteed 20/30-event availability, guaranteed realtime
     completeness, or complete forward coverage. Static P95 support is retained
     as planning evidence only.

## Stage 14 implementation guardrails

116. Stage 14's historical implementation status is `HISTORICAL IMPLEMENTATION RETAINED — SQL SERVER CUTOVER DEFERRED`. The SQLite research profile is retained as bounded historical evidence, not active architecture; S87 makes SQL Server the sole authoritative project database. The separately documented macOS direct adapter is prepared but not activated. Stage 15A readiness is in progress; Stage 15B live validation remains `NOT STARTED — AWAITS TIERED50 CUTOVER`.
117. The offline Stage 14 runtime remains a Python, cross-platform,
     standard-library-first collector package. Its offline core must not import
     PostgreSQL/SQL-driver modules, platform-specific APIs, shell orchestration,
     or hardcoded platform paths. The isolated optional direct adapter is the
     sole exception for the prepared macOS path; it uses the pinned DB-API
     dependency and creates no local shadow database. Deployment scheduling and
     host selection remain outside the package.
118. The versioned Stage 14 manifest is the authoritative offline runtime
     configuration. The prepared direct runner separately reads the unchanged
     seven existing SQL sampling targets, requires IDs 1–7 and their exact
     StopPointRef mapping, and refuses the deferred Tiered50 target list. The
     offline manifest still contains exactly 50 targets, fixed Stage 13 phase
     indexes, and no fabricated future `SamplingTargetId` values. Manifest
     phase changes fail closed against Stage 13 evidence.
119. The production claim identity remains `(SamplingTargetId,
     LogicalDueBucket)`. A disposable local backend may use the explicitly
     labeled development identity `(panel_version, source_parent_station_id,
     logical_due_bucket)` only because the 43 new production IDs do not yet
     exist. The adapter boundary must prevent that local key from becoming a
     production schema decision.
120. The one-heartbeat command derives the current five-minute logical bucket
     from UTC wall-clock time, executes only that bucket, and never replays
     missed buckets or fabricates missed snapshots. It must not become a
     permanent internal scheduler loop.
121. SQLite has two retained historical profiles: `DEVELOPMENT` is disposable
     local development/test state, while `LIVE_RESEARCH` is durable,
     non-disposable evidence outside the repository. `LIVE_RESEARCH` retains
     parsed history indefinitely, bounds sanitized raw forensic evidence to at
     most 24 hours, and preserves three validated daily backups. It is not a
     Tiered50 production cutover. The prepared macOS path uses direct
     persistence and must not route through either SQLite profile.
122. The real MDD client must be POST/TRIAS 1.2, arrival-scoped, per-target
     result-limited, 30-second per-attempt, environment-secret-only, and
     explicitly opted in. Importing or initializing the client must not issue
     network traffic. Default plan/dry-run/simulate validation must remain
     offline-safe.
123. Ordinary successful live MDD payloads are not retained. The bounded
     `LIVE_RESEARCH` exception may retain only sanitized forensic raw evidence
     outside the repository for at most 24 hours, with an indexed expiry and
     cleanup; parsed source-faithful history remains durable. Secrets,
     authorization headers, unrestricted exceptions, and unsanitized raw live
     payloads must not enter logs or durable evidence.
124. Every authorized quota permit must move from `PENDING_AUTHORIZATION` to
     exactly one terminal state: `RELEASED_NO_NETWORK`,
     `CONSERVATIVE_LIVE_ATTEMPT`, or `HTTP_PROVIDER_ATTEMPT_CONFIRMED`.
     A live permit must not be reusable between transport invocation and
     durable attempt accounting. Confirmed provider attempts, HTTP responses,
     client-side live invocations, provider-quota-unknown failures, and
     conservative live-attempt protection remain separate accounting concepts;
     simulation and credential/request-contract preflight failures do not
     consume provider or conservative live headroom.
125. Stage 14 implementation validation must include a no-network check,
     manifest reconciliation, full repository tests, syntax checks, static
     architecture checks, duplicate-claim behavior, retry/deadline/quota
     fault injection, restart/crash visibility, failure isolation, and
     whitespace/diff review. No validation step may consume MDD quota or issue
     DNS activity.
126. Tiered50 production cutover remains gated on a real sampling-era
     timestamp, production target-ID materialization, final heartbeat safety
     margin, deployment platform, authorized live dry-run, Tiered50 activation,
     and post-activation smoke validation. The prepared existing-seven-target
     macOS path has separate gates: current Windows runtime identification and
     retirement, explicit cutover approval, direct-database schema validation,
     and one manually authorized live Heumarkt validation. PostgreSQL
     host/runtime is not active architecture.

## Stage 15A validation-readiness guardrails

127. Stage 15A is offline validation-framework/readiness work. The authoritative statuses are Stage 14 overall `HISTORICAL IMPLEMENTATION RETAINED — SQL SERVER CUTOVER DEFERRED`, Stage 14 collector `STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE`, Stage 15 readiness `IN PROGRESS`, and Stage 15 live validation `NOT STARTED — AWAITS TIERED50 CUTOVER`.
128. Stage 15A must not start live Stage 15, issue MDD requests, perform DNS or
     live-network activity, install/configure PostgreSQL, create production
     database objects, materialize the 43 new production targets, create a
     sampling-era cutover timestamp, deploy a scheduler, modify Power BI, or
     alter Stage 12/13 evidence. The separately documented additive prepared
     adapter/migration is not Stage 15A validation and does not constitute
     production cutover.
129. The version-controlled Stage 15 validation contract may encode automatic
     PASS/FAIL only for already approved invariants. Design-only production
     checks are `NOT_APPLICABLE` or `INSUFFICIENT_EVIDENCE` when their evidence
     is absent; absence is never a PASS.
130. Stage 15 validation must use a backend-neutral read-only evidence adapter.
     The validator must not claim buckets, mutate target or dispatcher claims,
     finalize quota permits, write parsed snapshots, repair stale state, or
     send transport requests. SQLite is a read-only development evidence
     adapter, not a production database implementation.
131. Expected target/bucket work is derived only from the approved manifest,
     approved schedule engine, and logical bucket identity. Missing audit
     evidence is `EXPECTED_BUT_MISSING`, not success. Duplicate dispatcher and
     target-claim events remain explicit auditable classifications, and
     unexpected execution remains visible as `UNEXPECTED_EXECUTION`.
132. Dispatcher validation must preserve one durable owner per panel/bucket,
     make duplicate invocations observable, prove zero duplicate target
     transport when a duplicate sample exists, and keep incomplete/stale
     claims visible. Target validation must preserve at most one winning claim
     per target/bucket and must diagnose rather than repair stale claims.
133. Attempt/retry validation must hard-fail attempt number greater than 2,
     more than one automatic retry, retry after deterministic non-retryable
     categories or 401/403, and duplicate target transport. Retry-rate
     percentages remain policy pending and must not be given an invented
     threshold.
134. Quota validation must separately report scheduled logical requests, live
     client invocations, network attempts, HTTP responses, confirmed provider
     attempts, conservative provider-quota-unknown attempts, and active
     reservations. Simulation/offline attempts must never count as provider
     usage. Unavailable quota state must be explicit rather than treated as
     zero.
135. Stage 15 metrics may report measured rates, percentages, distributions,
     parser gaps, zero-result frequency, error categories, and deterministic
     duration statistics, but no failure-rate, retry-rate, timeout-rate,
     coverage, parser-gap, or heartbeat-duration acceptance threshold may be
     invented. The final persistence/cleanup safety margin remains deferred.
136. A parser gap is measured evidence and is not automatically fatal. Zero
     returned events are not automatically erroneous. `RecordedAtTime`, source
     identity, sampling era, and Legacy7 lineage remain source-faithful and
     nullable where evidence is absent.
137. Stage 15B may validate a real era transition, Legacy7 history/identity
     continuity, delete/reinsert continuity, and post-cutover Tiered50
     attribution only after actual Tiered50 activation and a real production
     sampling-era timestamp exist. Stage 15A must never infer or fabricate
     either.

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
40. PostgreSQL is the intended future persistence direction for this redesigned
    architecture, with JSONB only where later justified. This does not change
    source availability, create PostgreSQL tables, migrate SQL Server assets,
    or authorize production persistence.
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

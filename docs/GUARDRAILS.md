# Semantic and Data Guardrails

These guardrails apply from project foundation unless explicitly replaced by a later approved decision.

The approved Stage 02 source-contract package hands future collector work to
the [realtime collector requirements](collector-design/realtime-collector-requirements.md);
implementation remains deferred. The separate [near-event RecordedAtTime
lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is post-Stage-02 design evidence with host execution pending and is not a Stage
02 blocker.

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

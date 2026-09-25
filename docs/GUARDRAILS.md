# Semantic and Data Guardrails

These guardrails apply from project foundation unless explicitly replaced by a later approved decision.

1. Estimated timing is not Actual Physical Delay.
2. Actual Delay requires validated recorded/actual timing evidence, with `RecordedAtTime` the priority candidate.
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

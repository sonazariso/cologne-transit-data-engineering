# Data quality, lineage and operational observability

**Evidence separation:** quality rules can be implemented without claiming that they passed on every station or hour. Below are repeatable engineering checks and the actual validation boundaries.

## Grain and provenance controls

Use `RunId` (execution) → `AuditId` (due-target disposition) → `SnapshotId` (provider response) → one-or-many typed child evidence rows. Keep sampling era, parent StopPointRef, request breadth, observation time, original source identifiers/path and parser version. Distinguish returned empty payload, HTTP error, timeout, quota skip, cadence skip, duplicate target claim and successful persisted nonempty observation.

## Suggested quality SLI contract (not deployed SLAs)

| Measure | Definition | Interpretation |
| --- | --- | --- |
| Due-target accounted rate | `(success + fail + explicit skip + duplicate) / due` | Must not hide unaccounted outcomes |
| Persisted-response rate | persisted snapshots / authorized completed attempts | Separate connectivity from SQL availability |
| Source parse completeness | valid extracted occurrences / expected parseable occurrences for a *specified* schema fixture | Avoid unknown denominator outside audited source fields |
| Freshness | report time − newest `ObservedAtUtc` for that panel/era | Requires explicit panel and timezone |
| Quota consumption | transmitted + conservatively reserved ambiguous attempts | Monitored by shared monthly ledger |
| Deadline success | due targets completed inside absolute bucket budget / due | Different from run lifecycle `COMPLETE` |
| Recorded-time availability | source `RecordedAtTime` present / valid comparable timing contexts | Missing is unknown, never zero delay |
| Integrity | unmatched RunId/AuditId/SnapshotId FKs, duplicate natural identities, hash mismatches | Exception must be investigated |

These are **measurement definitions**, not measured production percentages; no daily P95, steady-state uptime or 50-station coverage SLI has been validated. Quality gates should emit PASS/FAIL/NOT_PROVEN and an evidence timestamp. Historical Stage 9 matching classifications and read-only Stage 15 readiness are separate from continuous service proof.

## Failure taxonomy and action

1. **Provider/transport**: DNS/HTTP failures, source errors, parse-contract mismatch → record attempt and response/error category; retry only under bounded approved policy.
2. **Quota**: missing reset/ledger, expired permit, shared budget exhausted → fail closed; never silently spend remaining provider budget.
3. **Scheduling**: missed bucket, expired deadline, contested target claim → skip with explicit attribution; no fabricated catch-up.
4. **Persistence**: insert/procedure failure, mismatched child count → record/preserve failure and investigate idempotent rerun; do not count the snapshot as durable.
5. **Semantic**: absent recorded time, ambiguous source identity, differing call scopes → preserve NULL/ambiguity; do not back-fill from estimates.
6. **Capacity**: SQL MDF/LDF growth, log pressure or disk reserves below floor → stop/hold collection, inspect retention, verify backup and read-only capacity.

## Recommended monitoring for any future restart

Emit per-bucket due/succeeded/failed/skipped/unaccounted counts, attempt/retry categories, quota ledger balance, permit ambiguity, 5-minute deadline headroom, SQL write latency, write bytes/snapshot, current ROWS/LOG/tempdb and C: free. Alert on unaccounted targets, storage reserve breaches, invalid source schema, or two writers. Do not report hypothetical alerting as already deployed. Treat history with discontinuous sampling as nonrepresentative.

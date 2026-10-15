# Cologne Transport Intelligence — Data Engineering Pilot

**Python · MDD/TRIAS API · Microsoft SQL Server · ETL/ELT · Data Quality · Source Lineage**

A hands-on portfolio project demonstrating how public-transport source data for Cologne (Köln), Germany, can be acquired, parsed, validated, stored, and prepared as reproducible SQL datasets.

> **Project scope closed at the successfully demonstrated engineering pilot.** This repository is not a currently running production service or a complete, continuous transport-performance dataset. The Tiered50 scheduled rollout failed its first production heartbeat and is disabled. Its diagnostics remain documented; the earlier bounded pilots and corrected Legacy7 heartbeats did collect and persist real source observations.

## What was demonstrated

| Capability | Implementation / evidence | Status |
| --- | --- | --- |
| External API acquisition | Python MDD/TRIAS request/response handling and provider-quota checks | Demonstrated in bounded pilots |
| Source parsing | Structured extraction of calls, timing, situation, service-section, attribute, and parser-gap evidence | Implemented; tested and historically run |
| Relational persistence | SQL Server `ctl` control lineage and `stg` source-faithful tables, transactional stored procedure | Historically exercised on real SQL Server |
| Operational safeguards | Runs, target audit, HTTP attempts, quota permits, leases, retries and skip accounting | Implemented; bounded validation evidence |
| Data quality and lineage | Source values versus inference, NULL preservation, matching-confidence handling | Documented and implemented in selected paths |
| Source-to-analytics handoff | SQL read-access views and source-quality/coverage queries | Partial; not a completed governed reporting warehouse |
| Data collection | 343 successful HTTP responses in the Stage 8 Köln Hbf pilot; six scheduled Legacy7 buckets | Historical success; **not continuous 50-station coverage** |

## Data flow

```text
MDD / TRIAS API
   -> Python collector (request, response validation, parser)
   -> run / target / HTTP-attempt / quota lineage (ctl)
   -> SQL Server source snapshots and normalized evidence (stg)
   -> SQL read-access and evidence views (stg / analytics)
   -> SQL read-access / validated sample extracts for downstream consumers
```

Read [the implementation walkthrough](docs/DATA_ENGINEERING_PIPELINE.md), [data dictionary and source-quality limitations](docs/DATASET_AND_REPORTING_BOUNDARY.md), [SQL data-retention scope](docs/SQL_RETENTION_SCOPE.md), and [validation/closeout status](docs/PORTFOLIO_CLOSEOUT.md).

## Architecture and repository map

- `src/cologne_transit_collector/`: collector, source parsing and SQL persistence modules.
- `scripts/`: guarded probes, imports, operator tooling, runtime checks and validation.
- `sql/mdd-mac-collector/create-source-faithful-persistence.sql`: SQL Server control and observation schema, stored procedures and read views.
- `docs/stages/` and `docs/source-contracts/`: engineering decisions and evidence, including prototypes that were not productionized.
- `docs/operational/`: incident, runtime and storage audit history. Some historical documents describe **plans rather than completed work**.
- `tests/`: automated offline regression coverage. Test success is not equivalent to a live runtime acceptance test.

## Key engineering decisions

1. Retain source-provided `TimetabledTime`, `EstimatedTime`, `RecordedAtTime` and collector `ObservedAtUtc` **as different fields**, never synthesizing actual event time from estimates.
2. Preserve row grain, identifiers, call scope (`THIS`/`PREVIOUS`/`ONWARD`), timing context (`ARRIVAL`/`DEPARTURE`) and source/observation lineage.
3. Separate acquired raw evidence (`stg`) from operational metadata (`ctl`) and derived/report-facing projections (`analytics`).
4. Use least-privilege SQL runtime access, secrets outside Git and fail-closed collection/quota rules.
5. Treat incomplete days, missing actual times, and mismatched journey identities as data-quality limitations, not as zero delay or success.

## Reproducibility and scope

The repository contains source and design artifacts, not live credentials, a reproducible complete local SQL Server dump, or a deployed reporting service. The independent source-provider contract and runtime configuration must be supplied for actual collection; **collectors are intentionally disabled** in the current environment.

The approved closeout retention set is **exactly 10 source snapshots** — `22, 93, 185, 189, 207, 224, 225, 226, 228, 229` — with their complete dependent evidence and required run/audit lineage. The IDs are operator-confirmed; live row/hash/consumer evidence must still be verified before any removal. Never publish the bulk production dataset. See [the retention manifest](docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md) and [database right-sizing](docs/DATABASE_RIGHTSIZING_PLAN.md). Do not commit personal data, secrets, raw provider payloads without distribution rights, or large backups.

## Outcome

**Completed engineering proof of concept with real collection and persistence evidence.** Continuous 50-station sampling, validated actual-delay KPIs, and a complete SQL Server warehouse are **not claimed**. The ten-snapshot ID set is confirmed for a dry-run closeout; actual SQL deletion and disk recovery have not been executed and require independent evidence, explicit approval, and a measured 12 GiB C: safety-floor check.

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

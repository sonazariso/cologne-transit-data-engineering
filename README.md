# Cologne Transit Data Engineering

**A documented, bounded public-transport API ingestion and SQL Server data-engineering project for Cologne (Köln), Germany.**

`Python` · `MDD / TRIAS` · `VRS GTFS` · `Microsoft SQL Server` · `ELT` · `Data quality` · `Lineage` · `Quota-aware scheduling`

> **Project status — closed and offline (19 October 2026).** Actual provider responses were acquired and persisted during bounded historical runs. A deterministic 50-station tiered collection system was designed, implemented and partially exercised, **but uninterrupted 50-station production coverage was never demonstrated**. The first scheduled Tiered50 heartbeat failed its cadence deadline; both Tiered50 and Legacy7 were subsequently disabled. No live monitoring service, public API, dashboard or production SLO is advertised.

## Why this project exists

An external transit feed does not arrive as a clean table. The engineering challenge is to acquire bounded source observations, preserve their original meaning, record *which call generated which rows*, detect missing/error evidence, and keep storage, API consumption and operational cost under control. This repository is a case study in building that pathway **and documenting its capacity/rollout failure honestly**.

## Implemented evidence and boundaries

| Capability | Demonstrated / implemented | Boundary |
| --- | --- | --- |
| Provider integration | Real MDD/TRIAS calls in bounded pilots; 50 station identities passed a minimal compatibility probe | Not evidence of continuous service |
| Scheduling and governance | UTC bucket selection, tier/phase schedule, durable claims, quota permits, retry policy, launchd templates | First scheduled Tiered50 run failed; collector stopped |
| Source-faithful parsing | Calls, timing, situation, service sections, attributes, response errors and parser gaps | Source timestamps cannot be inferred from collector time |
| Persistence | SQL Server `ctl` lineage, `stg` snapshots and structured child evidence; transactional SQL boundary | Historical observations only; sample reduced for closeout |
| Data quality | Source identity/missingness, request outcome, skip reasons, parse consistency, stage-specific matching evidence | No validated citywide punctuality or passenger-demand measure |
| Data operations | Migration, SQL permissions, read-only preflights, incident analysis, quota/capacity modelling, safe stop | No measured production SLA or disaster-recovery restore test |
| Storage lifecycle | Exactly 10 approved historical snapshot IDs; October 16 database right-sizing and Windows cleanup | Exact **post-retention** row inventory and new full-backup verification remain to be independently captured |

## Pipeline at a glance

```text
     VRS GTFS static reference                 MDD / TRIAS live HTTP feed
            (design context)                              |
                  |                             Tiered50 / Legacy7 Python
                  |                             bucket scheduling + quota
                  |                                       |
                  +---------------------------> request / parse / validate
                                                          |
                                          SQL Server (CologneTransitIntelligence)
                                             ctl: run, audit, quota lineage
                                             stg: snapshot + 7 child families
                                                          |
                                          SQL inspection / verified sample
```

No visualization or business-intelligence layer is part of the active deliverable. Historical warehouse prototypes remain only where needed to preserve SQL sample/lineage or trace architectural decisions.

## Fifty-station panel

The **fixed `TIERED50_PANEL_V1`** contains 50 distinct Cologne VRS parent-station StopPointRefs, including the seven prior Legacy7 identities. The approved policy is:

| Tier | Stations | Interval | `NumberOfResults` | Nominal requests/day |
| --- | ---: | ---: | ---: | ---: |
| A | 8 | 5 minutes | 20 | 2,304 |
| B | 20 | 15 minutes | 30 | 1,920 |
| C | 22 | 30 minutes | 30 | 1,056 |
| **Total** | **50** | **six 5-minute phases** | — | **5,280** |

Concurrency is capped at **2**; retries are bounded at **2 total attempts**. The planned 30-minute supercycle has **110 station requests** across phases **19/18/18/18/19/18**. Nominal uninterrupted operation would require **158,400 calls in 30 days**, *before retries*; quota authorization can still prevent them. These are design arithmetic, **not achieved production metrics**.

For every station's name, StopPointRef, tier, interval, phase and the exact run/authorization protocol, see [50-station collector](docs/50-STATION-COLLECTOR.md) and [`config/tiered50_runtime_manifest.json`](config/tiered50_runtime_manifest.json).

## Engineering documentation

Start with [documentation index](docs/README.md) for the current technical reference. Core pages:

- [Architecture and boundaries](docs/ARCHITECTURE.md) · [Data ingestion and SQL pipeline](docs/DATA_ENGINEERING_PIPELINE.md)
- [50-station collector, all 50 IDs, schedule and failure modes](docs/50-STATION-COLLECTOR.md)
- [Source contracts and data dictionary](docs/DATA-CONTRACTS.md) · [Quality, lineage and observability](docs/DATA-QUALITY-AND-OBSERVABILITY.md)
- [Quota and capacity](docs/QUOTA-CAPACITY-RETENTION.md) · [Operations, security, backup](docs/OPERATIONS-RUNBOOK.md)
- [Verified evidence and limitations](docs/VALIDATION-RESULTS.md) · [Current project state](docs/PROJECT-STATUS.md)
- [Offline reproducibility](docs/REPRODUCIBILITY.md) · [Historical evidence index](docs/HISTORICAL-RECORDS.md)

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/cologne_transit_collector/` | Collector, scheduling, TRIAS parsing and persistence code |
| `src/cologne_transit_stage15/` | Historical verification and evidence model |
| `config/` | Versioned fixed panel and validation contracts (templates, not live state) |
| `sql/mdd-mac-collector/` | SQL Server schema, stored procedures and storage experiments |
| `sql/portfolio/` | Historical bounded-retention tooling: **not for casual execution** |
| `scripts/` | Offline validators, guarded diagnostics and operator-only scripts |
| `deployment/launchd/` | Disabled, reviewed macOS scheduler templates |
| `tests/` | Offline code regressions; passing tests cannot prove a live heartbeat |
| `docs/` | Current engineering guide plus clearly labelled historical records |

## Safety and reproducibility

Real API access needs provider authorization; the **250,000-request/month** MDD account ceiling is a shared credential limit, not an allowance per script. No secret, raw real-time payload export, `.bak`, database file, admin capture or local diagnostic log is published in this repository. The runtime should not be started just to reproduce the docs. Run offline tests and inspect versioned/sanitized evidence; see [reproducibility](docs/REPRODUCIBILITY.md).

## Closeout and what remains unverified

Approved source snapshots: **`22, 93, 185, 189, 207, 224, 225, 226, 228, 229`** (plus dependent evidence). SQL Server data and log files were reduced on **16 October 2026** to **3,072 MiB MDF / 264 MiB LDF**; the operator's final C: free-space reading was **46.86 GiB**. `DBCC CHECKDB` reported no errors. These facts do **not** independently prove the exact retained rows, backup integrity or restore readiness. [Closeout details](docs/PROJECT-STATUS.md).

**Project name:** Cologne Transit Data Engineering. **Existing repository URL:** `sonazariso/cologne-transit-intelligence`; rename of the remote is deferred until the edited local files are committed, pushed and links/CI/clone paths are checked. History remains under its original names; executable Python/SQL identifiers are not blindly renamed.

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

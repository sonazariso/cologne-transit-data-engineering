# Documentation — Cologne Transit Data Engineering

This is the **current** documentation entry point. Dates and status in older stage/operational records are historical; do not treat old TODOs as an active deployment plan.

| Read | Purpose |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Bounded architecture, active SQL boundary, components and non-goals |
| [Pipeline](DATA_ENGINEERING_PIPELINE.md) | Extract → parse → validate → SQL persist → verify, lifecycle and grain |
| [50-station collector](50-STATION-COLLECTOR.md) | All 50 configured targets, time phases, quota, retries and incident |
| [Source contracts](DATA-CONTRACTS.md) | Provider interfaces, timing semantics, technical data dictionary |
| [Data quality and observability](DATA-QUALITY-AND-OBSERVABILITY.md) | SLIs, metrics, accepted/missing data, provenance and failure taxonomy |
| [Quota, capacity and retention](QUOTA-CAPACITY-RETENTION.md) | Accounting, request forecast, storage estimates, ten-snapshot closure |
| [Operations runbook](OPERATIONS-RUNBOOK.md) | Access, deployments, disabled state, read-only validation and backup |
| [Verification results](VALIDATION-RESULTS.md) | Claims and evidence for what was actually proven |
| [Project status](PROJECT-STATUS.md) | Final operational/storage state, completion and remaining checks |
| [Reproducibility](REPRODUCIBILITY.md) | Offline code entry points and constraints |
| [Historical records](HISTORICAL-RECORDS.md) | Chronological sources, superseded experiments and incidents |
| [GitHub release checklist](GITHUB_RELEASE_CHECKLIST.md) | Merge, commit and optional repository rename procedure |
| [Release notes](RELEASE_NOTES.md) | Source curation, local verification and external open items |

Historical stage files, source-contract details and operational incidents are retained under their prior filenames for provenance. Their banners mark them as historical, not current instructions. Intentionally removed: active Power BI deliverables, analytics-only documentation and original iteration prompts; no functioning ingestion module was removed.

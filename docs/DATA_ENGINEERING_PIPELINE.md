# Source to SQL Server — implemented Data Engineering workflow

This is the source-based architecture narrative of the current repository. It separates **implemented/historically demonstrated** capabilities from **design-only** material.

## 1. Scope and upstream systems

- Geography: Cologne urban transport; legacy seven-station panel and approved (but unsuccessful-at-runtime) Tiered50 design are not the same observed population.
- Dynamic feed: provider MDD/TRIAS over HTTP using configured credentials and quota limits. This project does not claim unfettered public/live access.
- Static context: official VRS GTFS identities and proposed matching architecture were investigated. Some PostgreSQL/static warehouse scripts are legacy design, **not a verified end-to-end SQL Server production static warehouse**.
- Event semantics: one source response can hold multiple vehicle journeys, stops, timing observations and situation evidence. A response or snapshot is not a unique vehicle event.

## 2. Extract — request and observation lineage

Relevant code: `src/cologne_transit_collector/`, `scripts/stage_08_mdd_realtime_pilot.py`, `scripts/stage_14_collector.py`, and the Mac/SQL runtime document `docs/macos-mssql-collector.md`.

The collector issues bounded TRIAS requests using the chosen station/target and request breadth; tracks logical due bucket, run identity, attempted HTTP request, status, response timestamp and quota; categorizes skips/failures distinctly from successful persistence. The production cadence and quota rules are guarded and versioned. Only the supported historical validation windows are asserted successful.

## 3. Transform — source-faithful parsing

Capture the original observation time and parser version. Extract distinct row families while preserving their source meaning:

| Row family | Grain / key | Example fields |
| --- | --- | --- |
| `stg.MddMacSourceSnapshot` | 1 source response per `SnapshotId` / unique `AuditId` | `RunId`, `ObservedAtUtc`, `TargetIdentity`, payload/hash |
| `stg.MddMacCallObservation` | one stop-call occurrence inside a snapshot / `CallObservationId` | stop/journey identifiers, line, bay, cancellation, source path |
| `stg.MddMacTimingObservation` | one time occurrence / `TimingObservationId` and occurrence key | call scope, arrival/departure, planned/estimated/recorded source text, identity status |
| `stg.MddMacSituationEvidence` | one provider Situation evidence occurrence / `SituationEvidenceId` | source-reported summary, detail, linkage, validity |
| `stg.MddMacServiceSectionEvidence` | service-section occurrence | route/mode/service text and source representation |
| `stg.MddMacAttributeEvidence` | attribute occurrence | provider code and text, source path/context |
| `stg.MddMacResponseError` | response error occurrence | error field/value/scope |
| `stg.MddMacParserGap` | parser-gap occurrence | missing/unknown source field and lineage |

Do not rewrite `NULL` source values as zeros. `EstimatedTime` is a prediction, not actual arrival. A missing `RecordedAtTime` cannot be filled from `ObservedAtUtc`. Preserve repeated observations and original row counts where distinct.

## 4. Load — transactionally persisted SQL Server tables

`sql/mdd-mac-collector/create-source-faithful-persistence.sql` holds the additive SQL schema and `stg.uspPersistMddMacSnapshot` persistence boundary. Collector runtime uses `pymssql`, short-lived SQL connections, a dedicated limited-permission login and stored procedure execution.

Control metadata lives in `ctl.MddMacCollectorRun`, `ctl.MddMacTargetAudit`, `ctl.MddMacHttpAttempt`, dispatcher/target claims, quota tables, production lease and era/methodology records. Source/evidence rows live under `stg`. `RunId` / `AuditId` / `SnapshotId` connect the layers. Idempotency and error/skip reporting are part of the design and tests.

JSON payload fields may be plaintext or GZIP. Logical readers must use the SQL source-JSON/read-access views (e.g. `stg.vMddMacSourceSnapshotJson`, `stg.vMddMacTimingObservationJson`) where installed, rather than assuming a nullable base payload means missing evidence. Some optional V3 columns were not present in the last live audit; never assume every source-controlled migration was deployed.

## 5. Quality gates and analytical interpretation

Check counts and lineage between run, target audit, source snapshot and child observation families. Classify HTTP statuses and parser errors; verify data types, source paths, JSON and time semantics; track journey/stop identity confidence rather than forging a match.

Data-quality dimensions useful to future analysts: source-field completeness, fraction of returned snapshots with `RecordedAtTime`, estimated-time completeness, parser-gap density, successful audited collection buckets, distinct time windows and coverage by station. These are **coverage and engineering metrics**, not travel reliability KPIs. At most, explicitly labelled estimated delay can be studied on comparable time fields and a qualified sample.

## 6. SQL data-access layer — proved versus designed

The SQL schema provides `stg.vMddMacSourceSnapshotJson`, `stg.vMddMacTimingObservationJson`, `analytics.vwMddMacSourceFieldCoverage`, `analytics.vwMddMacRecordedAtTimeHistory` and related views, subject to live schema verification. These facilitate bounded inspection and future extraction.

The Stage 10 scripts in `sql/04-warehouse` use PostgreSQL syntax and must not be presented as deployed SQL Server procedures. The implemented scope is source acquisition, transformation, SQL persistence, validation, and documented SQL-access contracts. No unverified dimensional warehouse or continuously maintained analytics dataset is claimed.

## 7. Operation, evidence and failure case

The bounded Stage 8 pilot successfully issued and parsed 343 HTTP-200 responses; documented Legacy7 corrected heartbeats ran successfully. Tiered50's first scheduled production heartbeat failed its expected-budget/cadence gate (2 completed of 18 target audits), after which collectors were disabled. Do not imply the service presently runs continuously.

Tests cover configuration and important source/SQL contracts; inspect the `tests/` tree. A passing offline suite cannot substitute for an active long-term source/SQL demonstration. The current Oct 14 verified off-C backup and live audit are operational historical evidence, not files for public GitHub distribution.

## 8. Security and sharing

Keep Mac Keychain credentials, SQL passwords, `.env`, downloaded provider responses, production data, `.bak`, `.mdf`, `.ldf`, debug logs and runtime data out of Git. Validate provider redistribution permissions before including sanitized samples; changing names or removing personal fields does not itself grant redistribution rights.


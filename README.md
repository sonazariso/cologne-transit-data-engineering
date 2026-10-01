# Cologne Transport Intelligence

Business Intelligence and data-engineering portfolio project for understanding the **system performance of public transport within Cologne (Köln), Germany**.

This repository is a clean restart. The previous `cologne-public-transport-intelligence` repository is retained as historical evidence only; its architecture and implementation are not automatically authoritative here.

## Primary objective

Build a management-oriented analytical system that helps a non-technical decision maker understand whether public transport within Cologne is operating well during the period under analysis, where problems are concentrated, how performance changes over time, and what source evidence supports those conclusions.

The long-term timing objective is **Actual Delay derived from validated
`RecordedAtTime` evidence**. Stage 02 does not approve that KPI: `Estimated
Delay` may be reported only when comparable `EstimatedTime` and
`TimetabledTime` values exist and the label is explicit. The project must not
rename estimated delay as actual delay if the source does not provide
sufficient recorded/actual timing evidence.

## Current status

- Stage 0 — Project Definition: **APPROVED**
- Stage 1 — Source Inventory & Permission: **APPROVED**
- Stage 2 — MDD/TRIAS Source-Contract Audit: **APPROVED**. The S14
  technical-closure package remains the approved baseline. The previous host
  run completed the five S15 historical requests, and S16 performed zero MDD
  requests while correcting their offline analysis.
- Stage 3 — Static GTFS Profile / Realtime Evidence Validation: **COMPLETE** (source evidence, collection/cadence design, and cross-source matching design).
- Stage 3A — [Disruption Cause & Alert Evidence Audit](docs/stage-03/03a-disruption-cause-alert-evidence-audit.md): **COMPLETE — APPROVED**. The bounded ten-station MDD discovery contains positive Situation/reason text evidence; approval preserves its evidence limits and does not authorize production implementation.
- Stage 3B — [Collection Strategy & Cadence Contract](docs/stage-03/03b-collection-strategy-cadence-contract.md): **COMPLETE — APPROVED DESIGN; ACTIVATION DEFERRED**. The cadence policy does not activate a fixed station schedule.
- Stage 3C — [Cross-Source Matching Design](docs/stage-03/03c-cross-source-matching-design.md): **COMPLETE — APPROVED DESIGN HANDOFF**. Stage 9 now owns the implemented matching/data-quality foundation.
- Stage 4 — [Cologne Scope & Mode Classification](docs/stages/04-cologne-scope-mode-classification.md): **COMPLETE — APPROVED**. The current official VRS Global-ID static feed validates the direct `de:05315:` stop rule, stop-event-first scope, both-endpoints internal segments, cross-boundary handling, and the source-faithful mode classifier.
- Stage 5 — [Database Architecture](docs/stages/05-database-architecture.md): **COMPLETE — APPROVED ARCHITECTURE**. PostgreSQL 18 is approved as the future authoritative platform direction using a clean redesign with durable source-faithful evidence, explicit grains, lineage, and deferred physical implementation. [Completion decision](docs/stages/05-database-architecture-completion-decision.md) and [table/grain catalog](docs/stages/05-table-grain-catalog.md) are authoritative. Its static warehouse/analytics handoff is documented in Stage 6; physical implementation remains deferred until explicit user authorization.
- Stage 6 — [Static Warehouse & Analytics Design](docs/stages/06-static-warehouse-analytics-design.md): **COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED**. The [completion decision](docs/stages/06-static-warehouse-analytics-completion-decision.md) finalizes source-to-Cologne scope, grains, time/calendar semantics, static analytics, lineage, and the future reconciliation contract without creating PostgreSQL objects or loading data.
- Stage 7 — [Realtime Pilot Design](docs/stages/07-realtime-pilot-design.md): **COMPLETE — APPROVED PILOT DESIGN**. The active Stage 8 handoff is MDD/TRIAS-only at Köln Hbf (`de:05315:11201`) under `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`: fixed start-to-start 3-second slots for at most 60 minutes, at most 1200 scheduled logical slots and 1200 normal HTTP attempts, a fixed `DepArrTime` anchor, 20 results, zero retries, sequential/non-concurrent execution, overlap skipping, explicit lineage, append-only `RecordedAtTime` capture, and a temporary raw-response exception. The 3-second cadence is a bounded diagnostic design, not production approval. Stage 7 historically described 207,500–210,000 (83–84%) as a planning target; Stage 13 now governs the approved lower-load design with a 250,000 provider ceiling, a 210,000 normal operational maximum, a 207,500 automatic-retry protection boundary, and no minimum utilization requirement. [Completion decision](docs/stages/07-realtime-pilot-design-completion-decision.md) is authoritative.
- Stage 8 — [Small Realtime Pilot completion and historical authorization chronology](docs/stages/08-small-realtime-pilot-blocked.md): **COMPLETE**. The corrected local credential configuration produced the successful differential diagnostic at Köln Hbf: Control A (`NumberOfResults=5`) returned HTTP 200 and Control B (`NumberOfResults=20`) returned HTTP 200, both parseable. The classification was `CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING`; no request-contract reduction was required. The full Hbf/N20/V3 pilot then completed successfully with 343 HTTP 200 responses, 100% parse/reconciliation over successful HTTP responses, and successful cleanup. The historical 401 and earlier missing-key/credential-configuration attempts remain documented as chronology.
- Stage 9 — [Matching & Data Quality](docs/stages/09-matching-data-quality.md): **COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS**. The matcher prevents unvalidated StopSeqNumber/direction/operator/endpoint coincidences from changing candidate rank or tie resolution, while validated policies remain usable. The corrected bounded source-backed run made exactly 3 MDD attempts and retained 60 live observations: 53 independently established service dates, 51 core deterministic matches, and 51 `MEDIUM` / 9 `UNMATCHED` decisions. The durable row-level evidence integrity gate passed with exact JSONL-to-summary reconciliation. See the [evidence artifact](docs/stages/09-matching-data-quality-evidence.json), [probe summary](docs/stages/09-source-backed-matching-summary.json), and [row-level evidence](docs/stages/09-source-backed-matching-evidence.jsonl). Railway Köln Hbf remains unresolved.
- Stage 10 — [Operational Fact](docs/stages/10-operational-fact.md): **NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED**. The canonical `dw.fact_operational_service_event` contract, explicit Stage 9 run/version refresh, HIGH-only eligibility, Stage 10 input-adapter evidence boundary, deterministic first/latest semantics, and repeat-refresh validator are source-controlled. The current checkout has no usable PostgreSQL database or durable source-observation population, and the retained Stage 9 evidence contains zero HIGH rows, so no live Stage 10 PASS is claimed.
- Stage 11 — [Power BI Model](docs/stages/11-power-bi-model-design.md): **COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED**. The semantic subject areas, table grains, physical-fact versus semantic Scheduled Trip relationship boundary, relationship/filter-direction contract, governed Date strategy, static-versus-canonical identity boundary, DAX measure contract, service-date-scoped matching coverage with separate missing-service-date evidence, threshold/NULL semantics, terminology, and sampling/coverage/reporting limits are approved. `Dim Sampling Era` currently filters only the `Fact Matching Decision` population; Data Quality classifications carried by those decisions inherit that context, while the independent `Fact Data Quality Issue` and `Data Quality Issue Count` are not Sampling-Era-filtered in the base model. Sampling Era is not an operational KPI slicer until validated operational-event lineage exists. [Completion decision](docs/stages/11-power-bi-model-completion-decision.md).
- Stage 12 — [50-Station Selection](docs/stages/12-50-station-selection.md): **COMPLETE — APPROVED FIXED 50-STATION PANEL**. `TIERED50_PANEL_V1` contains exactly 50 unique validated VRS parent stations, preserves all seven Legacy7 continuity anchors, measures current core-mode representation and geographic concentration, and passes a bounded sequential MDD compatibility probe with 50 HTTP 200/parseable application responses and source `StopPointRef` evidence. The complete sanitized 864-candidate population is retained in [candidate evidence](docs/stages/12-50-station-candidate-evidence.json), and the authoritative selector reproduces the fixed panel offline without the ZIP, network, MDD, or credentials. See the [completion decision](docs/stages/12-50-station-selection-completion-decision.md) and [sanitized evidence artifact](docs/stages/12-50-station-selection-evidence.json).
- Stage 13 — [Tiered Collector Readiness](docs/stages/13-tiered-collector-readiness.md): **COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED**. Explicit approval recorded in `prompts/S68.md` finalizes `TIERED50_PANEL_V1` as exactly 50 fixed stations with density ranks 1–8 / 9–28 / 29–50 for Tier A/B/C, cadence 5/15/30 minutes, 110 requests per 30-minute super-cycle, Tier A/B/C `NumberOfResults` 20/30/30, bounded concurrency 2, and `MaxAttempts=2` with one approved transient retry. Static support is 50/50 at the approved tier-specific limits; `PROVIDER_MAXIMUM_NOT_ESTABLISHED` and `CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT` remain explicit limitations. The 182,980 / 189,076 conservative shared-credential totals are valid safely below the 210,000 normal operational maximum and 207,500 automatic-retry protection boundary; the 31-day guardrail example is 189,076 with 18,424 headroom, while the Tiered50-only component is 180,048. The historical/evaluated 10/20/20 @ 5/15/30 comparison remains unapproved and reconciles to 205,444 for 31 days. See the [completion decision](docs/stages/13-tiered-collector-readiness-completion-decision.md) and [evidence artifact](docs/stages/13-tiered-collector-readiness-evidence.json).
- Stage 14 — [Collector Implementation and Offline Cutover Readiness](docs/stages/14-collector-implementation.md): **IN PROGRESS — PRODUCTION CUTOVER DEFERRED**. Collector implementation status is **STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE** after durable dispatcher claims, just-in-time target claims, source-contract-faithful identity/attribute/Situation retention, atomic quota-permit finalization, offline/live accounting separation, explicit transport-attempt metadata, and deterministic SQLite lifecycle validation. PostgreSQL, deployment scheduling, real target materialization/cutover, live MDD, and activation remain explicitly deferred. Stage 15 remains **NOT STARTED**.
- Stage 4 evidence: [approved reference snapshot](docs/stages/04-cologne-scope-mode-evidence.json), [historical/legacy snapshot](docs/stages/04-cologne-scope-mode-evidence-historical-2026-09-28.json), and [reproducible audit](scripts/stage_04_cologne_scope_mode_audit.py). The approved reference contains 3,137 Cologne stop IDs, 1,144,295 Cologne scheduled stop events, 1,082,490 internal segments, 61,805 Cologne-serving trips, and 137 Cologne-serving route IDs. Approved reference ZIP SHA-256: `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`; feed version: `VERSION__20260928_0042`. The separate Stage 9 current official snapshot is `VERSION__20260929_0106` with SHA-256 `ca64753223d3908a86b74fc322103b9e4c6c9c90275cfc5cb2c4c624b0082ee0` and passed all 17 Stage 4 revalidation checks.
- Post-Stage-02 validation: [GTFS-RT Source Feasibility Audit](docs/source-contracts/gtfs-rt-source-feasibility-audit.md) — **BOUNDED LIFECYCLE VALIDATION COMPLETE; NEUMARKT SAMPLES #1 AND #2 RECORDED**. The [GTFS.de identity bridge](docs/source-contracts/gtfsde-static-identity-bridge-audit.md) remains partial because railway Köln Hbf is unresolved; this work does not approve GTFS-RT as a production source or production implementation.
- Post-Stage-02 validation: [GTFS-RT Historical Availability Audit](docs/source-contracts/gtfs-rt-historical-availability-audit.md) — **BOUNDED VALIDATION COMPLETE**. Both the initial ±7-day exact-URL Wayback test and the separate ±45-day metadata-only test returned zero captures at the 3-month and 6-month targets. This does not establish provider-supported or continuous history or approve GTFS-RT for production; Stage 3A now separately validates current disruption evidence.
- Approved realtime source direction: **MDD/TRIAS PRIMARY** and **GTFS-RT COMPLEMENTARY**. Source-faithful observations remain separate; the Stage 9 implementation preserves the approved reconciliation guardrails. Neither source currently provides confirmed Actual Time.
- [Cross-source reconciliation and Cologne scope contract](docs/source-contracts/cross-source-reconciliation-contract.md) — **APPROVED CONTRACT — STAGE 9 REGRESSION IMPLEMENTED; SOURCE-BACKED ACCEPTANCE PASS**. The bounded source-backed Stage 9 gate passed with a lossless row-level decision artifact; optional identity bridges and production activation remain deferred. The contract requires GTFS-RT filtering to Cologne analytical scope; cross-boundary external portions and long-distance services remain outside core Cologne KPI calculations; cross-source disagreement is evaluated only with a validated correlation basis; provider-reported causes remain distinct from proven root causes.
- [Stage 3 completion decision](docs/stage-03/stage-03-completion-decision.md) is the authoritative closeout record. [Realtime collection design readiness](docs/stage-03/realtime-collection-design-readiness.md) records the completed handoff; it does not authorize production implementation.
- Stage 02A.1 — Legacy Implementation Audit: **COMPLETED**.
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **COMPLETED**. The audited
  request was XML/TRIAS 1.2 and the historical successful `/delfi` response was
  JSON with HTTP 200; that historical result does not resolve the later Stage
  8 HTTP 401.
- Stage 02A.3 — Historical Actual-Time Availability Validation: **COMPLETED**.
  The corrected offline evidence classifies 1D/7D/30D as
  `HISTORICAL_TIMETABLE_ONLY` and 90D/180D as `HISTORY_NO_EVENT`. No
  `EstimatedTime` or `RecordedAtTime` was observed in the historical result
  sets.
- The audited response did not contain `RecordedAtTime` or a direct delay
  field. `RecordedAtTime` remains a TRIAS 1.2 optional actual-time candidate,
  not a provider-wide absence claim. No Actual Delay KPI is approved. See the
  [Stage 02 source-contract package](docs/source-contracts/README.md) and
  [Project Status](docs/PROJECT-STATUS.md) for details.
- [Realtime collector requirements](docs/collector-design/realtime-collector-requirements.md)
  are approved Stage 02/Stage 7 design inputs; the Stage 8 pilot handoff is
  documented and the bounded runtime plus authorization diagnostic exist;
  activation remains blocked until the live differential decision tree proves
  the current authorization/request contract.
- The [future source-observation and persistence contract](docs/source-contracts/future-observation-persistence-contract.md)
  keeps timetable, estimate, source-provided recorded time, and collector
  observation time separate; preserves estimate and Situation/Cause history;
  and records PostgreSQL as the persistence direction. The Stage 9 matching
  migration is source-controlled but has not been executed or loaded with
  production data.
- The [near-event RecordedAtTime lifecycle validation](docs/source-contracts/mdd-trias-event-lifecycle-validation.md)
  is a separate **POST-STAGE-02 COLLECTOR-DESIGN VALIDATION**. Six bounded
  live journeys were reviewed (three at Köln Hbf and three at Köln Neumarkt);
  no `RecordedAtTime` was observed, and the result does not make a
  provider-wide absence claim or reopen Stage 02.
- No physical PostgreSQL database has been created or loaded. Stage 5/6
  physical warehouse work remains deferred; Stage 9 adds a source-controlled
  PostgreSQL matching migration contract but does not execute it or activate a
  collector. Power BI semantic design is now approved under Stage 11, while
  PBIP/Desktop implementation remains deferred.
- The legacy collector/database remains untouched.
- Confirmed Actual Time is unavailable; Actual Delay and prediction accuracy
  against confirmed actual timing remain unapproved/unavailable.

The historical responses used the provider lexical timestamp form
`YYYY-MM-DDTHH:mm:ssZ[GMT]`. The terminal `Z` supplies explicit UTC semantics;
`[GMT]` is a provider suffix. Date comparisons in the Stage 02A.3 audit are
documented as **DATE-ALIGNMENT ONLY** and do not fabricate a timezone for
timezone-less values. The five raw artifacts remain temporary evidence outside
the repository.

See [Project Status](docs/PROJECT-STATUS.md), [Stage 0 Project Definition](docs/stages/00-project-definition.md), and [Roadmap](docs/ROADMAP.md).

## Repository principles

1. Review before implementation.
2. Source evidence before assumptions.
3. Approval gate at the end of every stage.
4. No invented operational facts.
5. Preserve unknown/NULL semantics.
6. Do not delete legacy realtime history.
7. Do not start new MDD polling outside an explicitly approved future stage
   and gate; quota and permission constraints are documented here but no
   production schedule is approved by Stage 02.
8. Keep secrets, database dumps, large raw payload archives, PBIX files, and local runtime state out of Git.
9. Architecture approval does not imply physical database creation, SQL
   execution, collector activation, or Power BI implementation; each remains
   owned by its explicitly approved stage.

## Working environment

The authoritative Git working copy is intended to live on macOS:

`/Users/admin/Documents/cologne-transit-intelligence/`

Windows may access the same working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Windows should not maintain a second Git clone for this project. The new
Stage 14 collector does not require Windows, VMware, PowerShell, Windows Task
Scheduler, or a host-specific runtime; those remain legacy/optional tooling
boundaries only.

## Repository layout

- `docs/` — authoritative project documentation and stage decisions
- `references/` — pointers/checksums for legacy evidence; not copied legacy implementation
- `src/` — application/collector source when an implementation stage is approved
- `sql/` — database source when database architecture is approved
- `powerbi/` — Power BI semantic-model boundary and later PBIP/TMDL/PBIR source
- `tests/` — automated/validation tests when implementation begins

Stage 14 implementation details are in
[Collector Implementation and Offline Cutover Readiness](docs/stages/14-collector-implementation.md).
The scheduler-neutral command is `python scripts/stage_14_collector.py`;
without a mode it performs an offline `plan`. `dry-run` and `simulate` are
offline-safe, while `live` remains explicitly blocked until the deferred
production persistence and cutover gates are complete.

The Stage 14 collector is the first reusable implementation in `src/`; later
production persistence and deployment artifacts remain intentionally deferred.

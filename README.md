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
- Stage 3 — Static GTFS Profile: **NEXT / NOT STARTED**.
- Post-Stage-02 validation: [GTFS-RT Source Feasibility Audit](docs/source-contracts/gtfs-rt-source-feasibility-audit.md) — **BOUNDED LIFECYCLE VALIDATION COMPLETE; NEUMARKT SAMPLES #1 AND #2 RECORDED**. The [GTFS.de identity bridge](docs/source-contracts/gtfsde-static-identity-bridge-audit.md) remains partial because railway Köln Hbf is unresolved; this work does not approve GTFS-RT as a production source or start Stage 3.
- Post-Stage-02 validation: [GTFS-RT Historical Availability Audit](docs/source-contracts/gtfs-rt-historical-availability-audit.md) — **BOUNDED VALIDATION COMPLETE**. Both the initial ±7-day exact-URL Wayback test and the separate ±45-day metadata-only test returned zero captures at the 3-month and 6-month targets. This does not establish provider-supported or continuous history, approve GTFS-RT for production, or start Stage 3.
- Approved realtime source direction: **MDD/TRIAS PRIMARY** and **GTFS-RT COMPLEMENTARY**. Source-faithful observations remain separate; the approved reconciliation contract provides guardrails only and does not approve implementation. Neither source currently provides confirmed Actual Time.
- [Cross-source reconciliation and Cologne scope contract](docs/source-contracts/cross-source-reconciliation-contract.md) — **APPROVED DESIGN GUARDRAIL — IMPLEMENTATION NOT STARTED**. The contract requires GTFS-RT filtering to Cologne analytical scope; cross-boundary external portions and long-distance services remain outside core Cologne KPI calculations; provider-reported causes remain distinct from proven root causes.
- [Stage 3 realtime collection design readiness](docs/stage-03/realtime-collection-design-readiness.md) is **READY FOR USER REVIEW — STAGE 3 NOT STARTED**. It records entry conditions and guardrails only; it is not an implementation design.
- Stage 02A.1 — Legacy Implementation Audit: **COMPLETED**.
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **COMPLETED**. The audited
  request was XML/TRIAS 1.2 and the successful `/delfi` response was JSON with
  HTTP 200; current MDD authorization and host DNS are working.
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
  are approved Stage 02 design inputs; implementation is deferred.
- The [near-event RecordedAtTime lifecycle validation](docs/source-contracts/mdd-trias-event-lifecycle-validation.md)
  is a separate **POST-STAGE-02 COLLECTOR-DESIGN VALIDATION**. Six bounded
  live journeys were reviewed (three at Köln Hbf and three at Köln Neumarkt);
  no `RecordedAtTime` was observed, and the result does not make a
  provider-wide absence claim or reopen Stage 02.
- No new database, collector, or Power BI implementation is authoritative yet.
- The legacy collector/database must remain untouched until explicitly reviewed.
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

## Working environment

The authoritative Git working copy is intended to live on macOS:

`/Users/admin/Documents/cologne-transit-intelligence/`

Windows accesses the same working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Windows should not maintain a second Git clone for this project.

## Repository layout

- `docs/` — authoritative project documentation and stage decisions
- `references/` — pointers/checksums for legacy evidence; not copied legacy implementation
- `src/` — application/collector source when an implementation stage is approved
- `sql/` — database source when database architecture is approved
- `powerbi/` — Power BI source when the BI model stage is approved
- `tests/` — automated/validation tests when implementation begins

Empty implementation directories intentionally contain only `.gitkeep` files at project foundation.

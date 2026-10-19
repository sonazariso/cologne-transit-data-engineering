# Scope and closeout — Cologne Transit Data Engineering

**Current state:** data-engineering pilot **CLOSED**, collector disabled. No active BI or future dashboard roadmap.

## Outcomes delivered

1. MDD/TRIAS and static GTFS contract research; source identity and actual-vs-estimate semantics.
2. Historical successful bounded requests, nested parsing and source-faithful SQL Server persistence.
3. Deterministic 50-parent-station selection, approved 8/20/22 tier/phase/quota plan, deployed target materialization.
4. Versioned audit, scheduling, retries, permits, operational validation and documented Tiered50 cutover failure.
5. Historical import reconciliation, storage capacity NO_GO, coherent sample retention design and measured final SQL/Windows disk right-sizing.

## Closure, not upcoming engineering sprint

The current activity is documentation/portfolio publication. If later reopened, it would require independent approval of provider account terms, quota reset, new capacity model, resilient scheduling, monitoring SLOs, backups and access. Do not treat historical `docs/stages/` status dates as planned work or assume old Power BI analyses are part of the current project.

The [current status](PROJECT-STATUS.md) and [validation matrix](VALIDATION-RESULTS.md) supersede older historical plans.

## Stage 15 — Post-Cutover Validation (historical; not completed)

The proposed full Tiered50 post-cutover acceptance was not completed: the first scheduled heartbeat failed and the collector remains stopped.

Stage 15 remains **NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED** (historical acceptance status; current project is closed).

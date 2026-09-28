# Decision Log

This file indexes approved project-level decisions. Detailed records may later be added under `docs/decisions/` when a decision needs alternatives/rationale captured separately.

| ID | Date | Decision | Status |
|---|---|---|---|
| D-001 | 2026-09-25 | New project name: **Cologne Transport Intelligence** | Approved |
| D-002 | 2026-09-25 | New GitHub repository: `sonazariso/cologne-transit-intelligence` | Approved |
| D-003 | 2026-09-25 | Legacy repository remains reference/evidence and is not bulk-copied | Approved |
| D-004 | 2026-09-25 | macOS working tree is authoritative; Windows uses VMware shared folder | Approved |
| D-005 | 2026-09-25 | Project objective is management-oriented System Performance BI | Approved |
| D-006 | 2026-09-25 | Actual Delay using validated `RecordedAtTime` evidence is the primary timing objective | Approved |
| D-007 | 2026-09-25 | Estimated timing is secondary and may support prediction-accuracy analysis | Approved |
| D-008 | 2026-09-25 | Core analytical geography is performance inside Cologne, not whole routes merely passing through | Approved |
| D-009 | 2026-09-25 | ICE/IC/NightJet/FlixTrain/Thalys are outside core KPI scope for now | Approved |
| D-010 | 2026-09-25 | No new MDD polling before Stage 1 quota/source review | Approved |
| D-011 | 2026-09-25 | Every stage requires an explicit approval gate | Approved |
| D-012 | 2026-09-25 | TRIAS 1.2 is the semantic contract for the currently audited MDD interface; see the [Stage 02 source-contract package](source-contracts/README.md) | Approved |
| D-013 | 2026-09-25 | `Estimated Delay` may be calculated only as `EstimatedTime - TimetabledTime` and must be labelled explicitly; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-014 | 2026-09-25 | Actual Delay is not approved until a validated actual-time source is observed and sufficiently validated; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-015 | 2026-09-25 | `RecordedAtTime` is the TRIAS actual/recorded-time candidate, but was not observed in Stage 02 controlled evidence; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-016 | 2026-09-25 | `NotServicedStop` is not equivalent to journey cancellation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-017 | 2026-09-25 | `PlannedBay` and `EstimatedBay` remain separate source fields; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-018 | 2026-09-25 | Situation references establish association, not causal explanation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-019 | 2026-09-25 | The tested historical StopEvent interface cannot be relied on to reconstruct past realtime/actual operational state; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-020 | 2026-09-25 | Prospective realtime collection is architecturally important; see the [collector requirements](collector-design/realtime-collector-requirements.md) | Approved |
| D-021 | 2026-09-25 | Permanent/automatic full raw MDD payload retention is not the default source-of-record strategy; see the [raw-retention decision](source-contracts/mdd-trias-raw-retention.md) | Approved |
| D-022 | 2026-09-25 | The conservative documented project request ceiling remains 250,000 requests/month until stronger provider/account evidence supersedes it; see the [Stage 02 request contract](source-contracts/mdd-trias-request-contract.md) | Approved |
| D-023 | 2026-09-26 | Realtime source roles: **MDD/TRIAS primary; GTFS-RT complementary**; source-faithful staging remains separate before future reconciliation | Approved direction |
| D-024 | 2026-09-26 | Cross-Source Reconciliation and Cologne Scope Contract | Approved design guardrail |
| D-025 | 2026-09-28 | Stage 3 formally started with a bounded Stage 3A disruption/cause evidence audit; no schema, cadence, cause-model, matching, or production decision is made before host evidence | Procedural status |

## D-023 — Realtime Source Roles — MDD Primary, GTFS-RT Complementary

**Decision:** Use MDD/TRIAS as the primary realtime source for the Cologne
Transport Intelligence project. Use GTFS-RT as a complementary realtime source
for operational states, cross-source evidence, broad coverage, and capabilities
such as `SKIPPED`, `NO_DATA`, and `ServiceAlerts`.

Neither source is currently approved as a confirmed Actual Time source. MDD
`RecordedAtTime` has the relevant TRIAS semantic but was not observed in the
bounded tested responses and six live lifecycle journeys. GTFS-RT
`StopTimeEvent.time` can mean estimated or actual, and no provider-specific
explicit actual/recorded semantic was observed.

**Rationale:** MDD is better aligned with the project's Cologne/VRS source
environment, supports station-targeted requests, exposes rich
`PreviousCall`/`ThisCall`/`OnwardCall` context, explicit timetable and estimate
times, platform/bay fields, and situation associations. GTFS-RT adds broad
operational coverage, trip and stop schedule relationships, explicit
`SKIPPED`/`NO_DATA` states, `ServiceAlerts`, independent cross-source
evidence, and demonstrated current GTFS.de identity compatibility for tested
Köln Neumarkt records. The two identifier namespaces remain distinct, and
railway Köln Hbf is unresolved in GTFS.de.

**Consequences:**

- Future collection is expected to use separate MDD and GTFS-RT source-faithful
  ingestion paths.
- `ObservedAt`/`CapturedAt` is first-class, and repeated observations must
  preserve prediction evolution rather than overwrite prior estimates.
- Source provenance and source-specific semantics must not be erased by an
  ambiguous common record.
- Prospective collection is required; retrospective recovery cannot be relied
  upon for either source's realtime history.
- No `ActualTime` or `ActualDelay` field/KPI is introduced without later
  validated semantic evidence; Actual Delay remains **NOT APPROVED**.
- Missing events or updates remain source-observation states, not automatic
  on-time, served, cancelled, skipped, or service-absent conclusions.
- Future normalization/reconciliation is a separate design problem deferred
  until source-specific staging is designed.

This decision is a future design direction. It does not start Stage 3, approve
collector production code, finalize schemas or table names, or authorize
database, scheduler, Power BI, or runtime changes. See the [Stage 3 realtime
collection design readiness document](stage-03/realtime-collection-design-readiness.md).

## D-024 — Cross-Source Reconciliation and Cologne Scope Contract

**Decision:**

- MDD remains the primary realtime source.
- GTFS-RT remains complementary.
- GTFS-RT is filtered to the Cologne project scope before analytical use.
- Germany-wide GTFS-RT data does not expand project scope.
- Source records are reconciled only through validated identity evidence.
- Trip-specific cross-source claims require HIGH-confidence reconciliation.
- GTFS-RT `ServiceAlert` entity scope is preserved as trip, route, stop, or
  agency context.
- Provider-reported cause/effect is not treated as proven root cause.
- Cross-boundary routes contribute only in-scope Cologne evidence to core KPIs.
- Long-distance services remain outside core KPI scope.

The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
is the detailed design guardrail. It defines the conceptual canonical stop,
line, and service-event identities; stop-event-first matching; HIGH/MEDIUM/LOW
confidence; source-faithful storage; alert association levels; MDD Situation
semantics; claim limits; and unresolved Stage 3 validation items.

**Consequences:**

- `MDD JourneyRef == GTFS-RT trip_id` is not a valid identity rule.
- A validated canonical reconciliation layer is required before trip-level
  cross-source attribution.
- An in-scope Cologne stop event does not make an entire through-service or
  cross-boundary trip a Cologne KPI subject.
- Route-, stop-, and agency-level alerts remain context unless stronger
  validated linkage exists; only a HIGH-confidence trip-level association may
  support a trip-level provider-reported cause/effect claim.
- `ReportedCause` remains distinct from `RootCause`, and MDD Situation
  association remains distinct from proven delay causality.
- MDD and GTFS-RT disagreement must be retained rather than overwritten.
- Actual Delay, Actual On-Time Performance, and Prediction Accuracy vs Actual
  remain unapproved until confirmed actual time is validated.

This is an approved design guardrail, not production implementation approval.
It does not create SQL tables, schemas, migrations, collector code, matching
algorithms, ETL, Power BI changes, scheduler jobs, or production mappings.
Stage 3 is now **IN PROGRESS** through Stage 3A, whose status is **PARTIAL HOST
EVIDENCE — MDD EXECUTION PENDING**.

## D-025 — Stage 3A Procedural Start

Stage 3 formally started with the bounded [Stage 3A Disruption Cause & Alert
Evidence Audit](stage-03/03a-disruption-cause-alert-evidence-audit.md). The
substage measures current GTFS-RT ServiceAlert population and two sampled MDD
Situation responses using exactly one GTFS-RT GET and two MDD POST requests.

This is a procedural status entry, not a final analytical decision. MDD
execution is still pending. No database schema, collector cadence, cause
taxonomy, GTFS-RT production-ingestion approval, cross-source matching,
causal attribution, scheduler, or Power BI decision is made here.

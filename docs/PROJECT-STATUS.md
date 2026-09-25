# Project Status

**Project:** Cologne Transport Intelligence  
**Repository:** `sonazariso/cologne-transit-intelligence`
**Status date:** 2026-09-25

## Authoritative status

| Stage | Name | Status |
|---|---|---|
| 0 | Project Definition | Approved |
| 1 | Source Inventory & Permission | Approved |
| 2 | MDD/TRIAS Source-Contract Audit | Ready for approval |
| 3 | Static GTFS Profile | Not started |
| 4 | Cologne Scope & Mode Classification | Not started |
| 5 | Database Architecture | Not started |
| 6 | Static Warehouse & Analytics | Not started |
| 7 | Realtime Pilot Design | Not started |
| 8 | Small Realtime Pilot | Not started |
| 9 | Matching & Data Quality | Not started |
| 10 | Operational Fact | Not started |
| 11 | Power BI Model | Not started |
| 12 | 50-Station Selection | Not started |
| 13 | Tiered Collector Readiness | Not started |
| 14 | Controlled 50-Station Cutover | Not started |
| 15 | Post-Cutover Validation | Not started |
| 16 | Revisit Timing/Data Coverage Issues | Not started |
| 17 | Actual Physical Delay Productionization | Not started |

### Stage 2 substage status

- Stage 02A.1 — Legacy Implementation Audit: **Completed** (read-only source
  evidence documented).
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **Completed**.

  The existing successful host-probe artifact was audited without another MDD
  request. The normalized request to `https://mdd.gorheinland.com/delfi` used
  XML/TRIAS 1.2 and returned HTTP **200** with
  `application/json;charset=UTF-8`; the body is JSON. It contains five
  `stopEventResult` records, timetable and estimate timestamps, identity,
  bay, operational-flag, and situation evidence. `RecordedAtTime`, direct
  delay, cancellation, and explicit platform-change fields were not present
  in this response.

  The artifact is temporary evidence at
  `/private/tmp/cologne-transit-stage-02a2-hhqob8km.raw`, outside this
  repository. It was not copied or committed. The audit found no unresolved
  technical blocker for this controlled response.

Current authoritative transport and authorization state:

- `HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS`.
- `MDD_API_KEY VALUE COMPARISON: SAME`.
- `CURRENT MDD AUTHORIZATION: WORKING`.
- The earlier 401 is historical evidence and is strongly explained by the
  earlier different macOS credential value; no provider-side entitlement
  change is claimed.
- Stage 02 technical audit is **Complete** and **Ready for approval**. The
  explicit user/reviewer approval gate remains pending; Stage 02 is not marked
  **Approved**.

The earlier S10/S11/S12 transport and DNS findings remain in the Stage 02 audit
as historical chronology, not as current blockers. S13 made zero MDD requests,
executed zero probes, executed no SQL, and changed no database, Legacy
repository, Task Scheduler, or Power BI state.

## Current authoritative decisions

- The project is management-oriented BI supported by data engineering.
- The umbrella management concept is **System Performance**.
- Analytical geography is public transport performance **inside Cologne**; a route merely passing through Cologne does not automatically make the whole route part of the core KPI scope.
- Primary timing objective: Actual Delay based on validated `RecordedAtTime` evidence.
- Stage 02 allows **Estimated Delay** only when timetable and estimate times
  are both present, comparable, and explicitly labelled. It does not approve
  an Actual Delay or prediction-accuracy KPI.
- `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis.
- The audited successful response did not contain `RecordedAtTime` or a direct
  delay value; this does not establish general MDD absence.
- `RecordedAtTime` is a TRIAS 1.2 optional actual-time field; its population
  coverage is deferred to Stages 8 and 16 and is not a Stage 02 blocker.
- `NotServicedStop` is a stop-level flag, not journey-level cancellation.
  `PlannedBay` and `EstimatedBay` remain separate, and situation association
  is not causal evidence.
- Full raw MDD responses are not a permanent or automated source of record by
  default; temporary diagnostic artifacts remain outside source control.
- Core starting modes: Stadtbahn/Tram, urban/KVB bus, and S-Bahn.
- RE, RB, and regional/other bus require Stage 4 service-level scope validation.
- SEV is retained as a separate analytical category.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.

## Former open critical questions — reclassified

The questions below are no longer left open at the Stage 02 gate. Each is
explicitly classified as resolved for Stage 02 or deferred to its proper
future stage.

1. `RecordedAtTime` semantic role — **RESOLVED_FOR_STAGE_02**. Population
   coverage — **DEFERRED_TO_STAGE_8**, with a later revisit in
   **DEFERRED_TO_STAGE_16**. It was absent from the one audited response, but
   that does not establish provider-wide absence.
2. Current MDD permission/access boundary — **RESOLVED_FOR_STAGE_02**. Access
   works; registration/activation, provider terms, and agreed use remain
   documented constraints. Future publication or redistribution requires a
   separate provider-terms check.
3. Quota — **RESOLVED_FOR_STAGE_02** at the conservative-contract level. The
   documented ceiling and shared-caller caveat are captured; operational
   budgeting is **DEFERRED_TO_STAGE_7** and **DEFERRED_TO_STAGE_13**.
4. Authoritative GTFS feed and coverage/update policy — **DEFERRED_TO_STAGE_3**;
   it is not a Stage 02 blocker.
5. Future retained TRIAS fields — **RESOLVED_FOR_STAGE_02** by the field-
   retention matrix; implementation/schema design belongs to later approved
   stages.
6. Full raw-payload retention — **RESOLVED_FOR_STAGE_02** by the raw-retention
   decision candidate; any automated exception requires a later Stage 7 gate.

**STILL BLOCKING STAGE 02: NONE.**

The point-in-time Legacy Collector quota audit snapshot at **2026-09-25
11:23:41 UTC** recorded **1,599** collector executions, **1,629** HTTP
attempts, and **35** additional retry attempts. Against the documented
monthly quota of **250,000 requests**, that is approximately **0.6516%**
recorded collector usage, with approximately **99.3484%** remaining against
that documented quota. This proves recorded consumption by the Legacy
Collector at that audit snapshot only; it does not prove total account-level
MDD consumption if another caller shares the same API key or account.

Do not implement around these deferred items before their designated approval
gates.

See the [Stage 02 source-contract package](source-contracts/README.md) and
[detailed Stage 02 synthesis](stages/02-mdd-trias-source-contract-audit.md)
for the full evidence layers, retention matrix, analytical permission matrix,
and acceptance criteria.

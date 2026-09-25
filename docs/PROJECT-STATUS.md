# Project Status

**Project:** Cologne Transport Intelligence  
**Repository:** `sonazariso/cologne-transit-intelligence`
**Status date:** 2026-09-25

## Authoritative status

| Stage | Name | Status |
|---|---|---|
| 0 | Project Definition | Approved |
| 1 | Source Inventory & Permission | Approved |
| 2 | MDD/TRIAS Source-Contract Audit | In progress |
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
- Stage 02 remains **In progress** because this response audit does not finish
  the broader TRIAS/MDD semantic, coverage, quota, retention, and approval
  decisions.

The earlier S10/S11/S12 transport and DNS findings remain in the Stage 02 audit
as historical chronology, not as current blockers. S13 made zero MDD requests,
executed zero probes, executed no SQL, and changed no database, Legacy
repository, Task Scheduler, or Power BI state.

## Current authoritative decisions

- The project is management-oriented BI supported by data engineering.
- The umbrella management concept is **System Performance**.
- Analytical geography is public transport performance **inside Cologne**; a route merely passing through Cologne does not automatically make the whole route part of the core KPI scope.
- Primary timing objective: Actual Delay based on validated `RecordedAtTime` evidence.
- `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis.
- The audited successful response did not contain `RecordedAtTime` or a direct
  delay value; this does not establish general MDD absence.
- Core starting modes: Stadtbahn/Tram, urban/KVB bus, and S-Bahn.
- RE, RB, and regional/other bus require Stage 4 service-level scope validation.
- SEV is retained as a separate analytical category.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.

## Open critical questions

1. Does MDD return `RecordedAtTime` with sufficient coverage for the intended population? It was absent from the one audited response.
2. What are the current MDD permission, quota, retention, and provider constraints?
3. What is the current account-level or shared-caller MDD quota consumption
   beyond the recorded Legacy Collector snapshot, and what current provider
   quota constraints apply?
4. What is the current authoritative GTFS feed and its coverage/update policy?
5. Which TRIAS fields must be retained before a new realtime schema is designed?

The point-in-time Legacy Collector quota audit snapshot at **2026-09-25
11:23:41 UTC** recorded **1,599** collector executions, **1,629** HTTP
attempts, and **35** additional retry attempts. Against the documented
monthly quota of **250,000 requests**, that is approximately **0.6516%**
recorded collector usage, with approximately **99.3484%** remaining against
that documented quota. This proves recorded consumption by the Legacy
Collector at that audit snapshot only; it does not prove total account-level
MDD consumption if another caller shares the same API key or account.

Do not implement around these unknowns before their designated approval gates.

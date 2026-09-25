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
- Stage 02A.2 — Controlled MDD Raw-Response Probe: **Blocked**.

  Preflight completed, but `MDD_API_KEY` was not available in the Codex
  execution environment. `MDD_API_KEY` present = **NO**. HTTP attempts = **0**;
  probe execution did not occur. The blocker is API-key unavailability in the
  execution environment. No source-contract conclusion was made from this
  failed preflight.

Stage 02 is not complete. The Stage 02A.1 document records Legacy request,
parser, and persistence behavior only; actual MDD response behavior remains
unproven until the controlled Stage 02A.2 probe.

## Current authoritative decisions

- The project is management-oriented BI supported by data engineering.
- The umbrella management concept is **System Performance**.
- Analytical geography is public transport performance **inside Cologne**; a route merely passing through Cologne does not automatically make the whole route part of the core KPI scope.
- Primary timing objective: Actual Delay based on validated `RecordedAtTime` evidence.
- `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis.
- Core starting modes: Stadtbahn/Tram, urban/KVB bus, and S-Bahn.
- RE, RB, and regional/other bus require Stage 4 service-level scope validation.
- SEV is retained as a separate analytical category.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.

## Open critical questions

1. Does the actual MDD endpoint return `RecordedAtTime` with sufficient coverage for the intended population?
2. What are the current MDD permission, quota, retention, and provider constraints?
3. What is the live quota consumption of the legacy 7-station collector, including retries?
4. What is the current authoritative GTFS feed and its coverage/update policy?
5. Which TRIAS fields must be retained before a new realtime schema is designed?

Do not implement around these unknowns before their designated approval gates.

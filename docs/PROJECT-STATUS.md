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

  Final preflight confirmed `MDD_API_KEY` present = **YES**. The one authorized
  POST was sent for Köln Hbf (`de:05315:11201`) with five arrival results and
  previous/onward/realtime calls enabled. It returned HTTP **401** after
  **1** HTTP attempt. The response header was
  `application/json;charset=UTF-8`, but the 162-byte/161-character body was
  UTF-8 plain text stating that access to this data provider is not enabled.
  It was not a valid MDD service payload, so Stage 02A.2 remains blocked and
  no response-field audit could be completed.

Stage 02 is not complete. The Stage 02 audit document records Legacy request,
parser, and persistence behavior together with the controlled Stage 02A.2
access-denied result; actual successful MDD response behavior remains
unproven. The read-only local authorization diagnosis in the same document
found no concrete local fix that can be validated without another request. The
historical 401 used a different request `Content-Type`, which is now corrected
in the unexecuted probe source. The named key source is the same, but the
Legacy Windows User environment and the current macOS process environment are
not proven to contain the same value.
The credential-value comparison was not performed because the two environment
values are separated between the Windows VMware guest and the macOS process.
The provider-denial condition therefore remains unresolved and the outcome is
`CREDENTIAL_IDENTITY_UNRESOLVED; PROVIDER_SUPPORT_PROVISIONAL`. This does not
state that the API key itself is invalid. The unexecuted probe source now uses
the Legacy Content-Type `application/xml; charset=utf-8`; the historical 401
request used `application/xml`, and no new request was made.

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

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

  The latest S10 preflight confirmed `MDD_API_KEY` present = **YES**, and the
  user confirmed that the macOS value matches the historically successful
  Windows User-level Legacy value. The reviewed probe was invoked exactly once
  at `2026-09-25T17:43:59.786Z` for Köln Hbf
  (`de:05315:11201`) with five arrival results and previous/onward/realtime
  calls enabled. DNS name resolution failed before an HTTP response was
  obtained:

  ```text
  URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
  ```

  Probe attempts = **1**, HTTP retries = **0**, confirmed MDD HTTP requests =
  **0**, HTTP status = **unavailable**, response bytes = **0**, and no temporary
  raw artifact was created. This run does not establish provider
  authorization or denial.

  The earlier HTTP **401** response remains historical evidence in the Stage
  02 audit. It was not a valid MDD service payload, so Stage 02A.2 remains
  blocked and no successful response-field audit could be completed.

Stage 02 is not complete. The Stage 02 audit document records Legacy request,
parser, and persistence behavior, the earlier controlled HTTP 401, and the
latest S10 transport failure; actual successful MDD response behavior remains
unproven. The user's value comparison removes a current key-value mismatch as
the explanation under investigation, but the S10 run obtained no HTTP response
and therefore cannot establish provider authorization, entitlement, key
validity, or account state. The S10 request-level diagnosis was
`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`. The
probe source uses the Legacy Content-Type `application/xml; charset=utf-8`.
S11 DNS-only diagnostics classified the observed Codex-environment resolver
behavior as `LOCAL_MACOS_SYSTEM_RESOLVER_FAILURE`: the same-environment Python
and system resolver paths did not resolve the tested hosts, while the permitted
public resolver comparison did. Because those diagnostics were constrained by
the Codex execution environment, host-level macOS DNS failure is not yet
independently established. Provider authorization remains undetermined. No
second request was made.

The current diagnosis is
`CODEX_ENVIRONMENT_DNS_FAILURE; HOST_MACOS_RESOLVER_STATE_UNCONFIRMED; PROVIDER_AUTHORIZATION_UNDETERMINED`.
Host-level DNS evidence is still pending. S12 made no MDD request and did not
execute the MDD probe or modify DNS/network configuration.

S11 endpoint and Codex-environment DNS evidence:

- `ENDPOINT_LITERAL_VALID: YES` for
  `https://mdd.gorheinland.com/delfi`; no source change was required.
- Python `socket.getaddrinfo()` returned no address for
  `mdd.gorheinland.com`, `ckan.mdd.gorheinland.com`,
  `download.gorheinland.com`, or `gorheinland.com`.
- `/etc/hosts` had no relevant override; `dscacheutil` returned no record and
  `scutil --dns` reported no available DNS configuration in this environment.
- Public DNS at `1.1.1.1` resolved the MDD hostname and the two requested
  sibling hosts. No connection was made to those addresses.

**S12 next action:** The user must run
`scripts/stage_02a2_host_dns_check.sh` from a normal interactive macOS
Terminal outside Codex. Its result will determine whether the host resolver
works, whether the host resolver fails while public DNS works, whether there
is a general host DNS failure, or whether the evidence is inconclusive. S11
and S12 made zero MDD HTTP requests and zero probe executions; Stage 02A.2
remains **Blocked** and Stage 02 remains **In progress**.

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

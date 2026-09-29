# Source Contracts

This directory contains the approved Stage 02 MDD/TRIAS source-contract
package. The S14 technical-closure package remains the baseline, and the
current Stage 02 status is **APPROVED** after the explicit S17 user approval
gate. Stage 02A.1, Stage 02A.2, and Stage 02A.3 are completed.

Current stage position: Stage 0 — **APPROVED**; Stage 1 — **APPROVED**;
Stage 2 — **APPROVED**; Stage 3 — **COMPLETE** as a source-evidence,
collection-design, and matching-design stage.

## Approved Stage 02 contract

These documents are the current Stage 02 source-contract record:

- [MDD/TRIAS request contract](mdd-trias-request-contract.md)
- [MDD/TRIAS response contract](mdd-trias-response-contract.md)
- [MDD/TRIAS field-retention matrix](mdd-trias-field-retention-matrix.md)
- [MDD/TRIAS raw-retention decision](mdd-trias-raw-retention.md)
- [Detailed Stage 02 evidence/audit](../stages/02-mdd-trias-source-contract-audit.md)

## Future architecture contract

The [future source-observation and persistence contract](future-observation-persistence-contract.md)
is a post-Stage-02 semantic/design guardrail. It preserves the independent
timing fields, append-only estimate and Situation/Cause history, and intended
PostgreSQL direction without creating a schema or approving implementation.

Stage 02A.3 is implemented in
[`scripts/stage_02a3_historical_actual_time_probe.py`](../../scripts/stage_02a3_historical_actual_time_probe.py).
The previous host run completed one historical request at each of 1D, 7D,
30D, 90D, and 180D. The offline-only correction/reanalysis path is
[`scripts/stage_02a3_reanalyze_existing.py`](../../scripts/stage_02a3_reanalyze_existing.py);
it reads only the five existing raw artifacts and does not require credentials
or network access.

The request contract records the audited `POST` XML/TRIAS 1.2 request,
`x-api-key` mechanism, request parameters, `NumberOfResults` versus HTTP
request-count semantics, and quota/permission boundaries.

The response contract records the successful controlled `/delfi` response as
JSON with HTTP 200 and keeps these layers separate:

1. TRIAS 1.2 specification capability;
2. fields observed in the controlled MDD response;
3. Legacy parser behavior; and
4. Legacy persistence behavior.

The field matrix uses `SPEC_DEFINED_NOT_OBSERVED` for specified candidate
fields absent from this one response. It does not generalize that absence to
MDD as a whole. In particular, `RecordedAtTime` is a TRIAS 1.2 optional
actual-time field but was not observed in the controlled response.

The approved raw-retention decision says that full raw MDD responses are not a
permanent or automated source of record by default. Temporary diagnostic
artifacts may remain outside source control for an explicitly approved
diagnostic task, without secrets. No arbitrary retention period or automated
capture is approved in Stage 02.

The [realtime collector requirements](../collector-design/realtime-collector-requirements.md)
are the durable Stage 02-approved source handoff; implementation remains
deferred under the approved [Stage 3B collection/cadence contract](../stage-03/03b-collection-strategy-cadence-contract.md).
The [future source-observation and persistence contract](future-observation-persistence-contract.md)
records the later architecture's non-negotiable separation of timetable,
estimate, source-provided recorded time, and collector observation time. It
also requires append-only estimate and Situation/Cause history and records
PostgreSQL as an intended future persistence direction only; it does not create
tables, migrate SQL Server assets, or approve production persistence.
The [near-event RecordedAtTime lifecycle validation](mdd-trias-event-lifecycle-validation.md)
is separate post-approval evidence for later collector design and is now
**BOUNDED VALIDATION COMPLETE**. Six live journeys were reviewed (three at
Köln Hbf and three at Köln Neumarkt); no `RecordedAtTime` was observed in the
bounded `ThisCall`, `PreviousCall`, or `OnwardCall` contexts.

The [GTFS-RT Source Feasibility Audit](gtfs-rt-source-feasibility-audit.md) is
also separate post-Stage-02 validation. Its current-feed inventory is complete
from supplied host evidence, and its two-sample Köln Neumarkt lifecycle check
is **BOUNDED LIFECYCLE VALIDATION COMPLETE**. The [GTFS.de static identity bridge](gtfsde-static-identity-bridge-audit.md)
remains `PARTIAL` because railway Köln Hbf is unresolved. It is read-only
host-run tooling, does not approve a production GTFS-RT source or production
implementation, and does not alter the completed Stage 3 design closeout.

The [GTFS-RT Historical Availability Audit](gtfs-rt-historical-availability-audit.md)
is a separate S19/S19.1 post-Stage-02 validation with status **BOUNDED
VALIDATION COMPLETE**. The initial ±7-day exact-URL Wayback test and the
separate ±45-day metadata-only test each returned zero captures at the
requested 3-month and 6-month horizons. It does not merge with the current-
feed feasibility audit or the GTFS.de static identity bridge, establish
provider-supported or continuous history, approve a collector, or alter the
completed Stage 3 design closeout.

## Post-Stage-02 realtime source direction

The approved source-role direction is **MDD/TRIAS primary** and **GTFS-RT
complementary/secondary**. MDD is the core Cologne-oriented realtime
observation source because of its source alignment, station-targeted requests,
rich call sequence, estimate, bay, and situation evidence. GTFS-RT supplies
complementary operational state, broad coverage, cross-source evidence, and
explicit `SKIPPED`, `NO_DATA`, and `ServiceAlerts` capabilities.

The two sources must initially remain source-faithful and separate. Future
collection must preserve `ObservedAt`/`CapturedAt`, source identity, and
estimate evolution before any normalization or reconciliation. Confirmed
Actual Time is unavailable; Actual Delay remains not approved. The [Stage 3
realtime collection design readiness document](../stage-03/realtime-collection-design-readiness.md)
records the completed design handoff; it does not approve production
implementation.

The [Stage 3 completion decision](../stage-03/stage-03-completion-decision.md)
is the authoritative closeout record. The [Stage 3B collection/cadence
contract](../stage-03/03b-collection-strategy-cadence-contract.md) and [Stage
3C matching design](../stage-03/03c-cross-source-matching-design.md) close the
two design substages without activating a collector or matching engine.

## Cross-source reconciliation and Cologne scope guardrail

The [Cross-Source Reconciliation Contract](cross-source-reconciliation-contract.md)
is **APPROVED DESIGN CONTRACT — IMPLEMENTATION NOT STARTED**. It makes the
GTFS-RT analytical scope Cologne-only even when the source feed is Germany-wide,
requires validated canonical stop/line/service-event identity evidence, and
prohibits direct equality between MDD `JourneyRef` and GTFS-RT `trip_id`.

The contract requires source-faithful observations before reconciliation,
stop-event-first matching, auditable HIGH/MEDIUM/LOW confidence, distinct
trip/route/stop/agency alert scopes, Cologne-only KPI evidence, and explicit
through-service and long-distance exclusions. Provider-reported cause/effect
remains distinct from proven root cause, and MDD Situation association remains
distinct from causality. The contract is the Stage 3C reconciliation handoff;
it does not define final schemas, persistence algorithms, or production
implementation.

## Historical / Legacy implementation evidence

The detailed audit contains historical chronology for the Legacy source audit,
the earlier 401, S10/S11/S12 transport diagnostics, and the successful S13
controlled-response audit. Those sections are evidence, not approval of the
Legacy implementation or proof of provider-wide behavior.

The Legacy parser audit must not be read as a capability claim for MDD. It
shows what the inspected Legacy code parsed and what its checked-in
persistence path received. The controlled response is one Köln Hbf arrival
request with five results at one point in time; it is not representative
coverage evidence.

Current status facts:

- Stage 02A.1 — Legacy Implementation Audit: **COMPLETED**.
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **COMPLETED**.
- Stage 02A.3 — Historical Actual-Time Availability Validation: **COMPLETED**.
- Historical Stage 02 controlled MDD authorization: **Working for that audited
  request**. Current Stage 8 authorization is **NOT PROVEN**: the latest
  2026-09-28 Stage 8 attempt returned HTTP 401 with the observed German
  supplier-access message, and the available S40/S41 diagnostic process
  reported `MDD_API_KEY: MISSING` before Control A.
- The S40 diagnostic is implemented at
  [`scripts/stage_08_authorization_request_contract_diagnostic.py`](../../scripts/stage_08_authorization_request_contract_diagnostic.py).
  Its available repository/Codex execution made zero diagnostic requests and
  authorized no request-parameter change. The user's separate local Terminal
  had previously reported `MDD_API_KEY: PRESENT`; these are different process
  environments, not contradictory claims, and do not establish whether that
  separate credential is correct or incorrect.
- Host DNS result: `NORMAL_RESOLVER_WORKS`.
- Stage 02 technical audit baseline: **Complete under S14**; current Stage 02
  status: **Approved** after the S17 approval synchronization.
- The previous host run completed five historical MDD requests. S16 performed
  zero MDD HTTP requests and zero probe executions; it used only the existing
  raw artifacts.
- Corrected historical result: 1D/7D/30D are
  `HISTORICAL_TIMETABLE_ONLY`; 90D/180D are `HISTORY_NO_EVENT` with the
  populated `STOPEVENT_NOEVENTFOUND` response evidence.
- The provider timetable lexical form is `...Z[GMT]`: `Z` is explicit UTC and
  `[GMT]` is a provider suffix. Timezone-less values remain unresolved for
  absolute time; date comparison is **DATE-ALIGNMENT ONLY**.
- Empty `ErrorMessage` arrays are not real errors. Corrected counts are zero
  for the three result-bearing responses; the 90D/180D populated response
  messages remain counted.
- No `EstimatedTime` or `RecordedAtTime` was observed in the historical
  responses. Delivery `responseTimestamp` and processing `calcTime` are not
  actual vehicle timing.
- No SQL query, database change, Legacy repository change, scheduler change,
  Power BI change, commit, or push was made.

Historical prompt files remain unchanged. `docs/DECISION-LOG.md` now indexes
the approved Stage 02 decisions, while the lifecycle validation remains a
separate post-approval experiment and does not alter this historical evidence
baseline.

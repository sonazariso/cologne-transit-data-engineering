# Source Contracts

This directory contains the approved Stage 02 MDD/TRIAS source-contract
package. The S14 technical-closure package remains the baseline, and the
current Stage 02 status is **APPROVED** after the explicit S17 user approval
gate. Stage 02A.1, Stage 02A.2, and Stage 02A.3 are completed.

Current stage position: Stage 0 — **APPROVED**; Stage 1 — **APPROVED**;
Stage 2 — **APPROVED**; Stage 3 — **NEXT / NOT STARTED**.

## Approved Stage 02 contract

These documents are the current Stage 02 source-contract record:

- [MDD/TRIAS request contract](mdd-trias-request-contract.md)
- [MDD/TRIAS response contract](mdd-trias-response-contract.md)
- [MDD/TRIAS field-retention matrix](mdd-trias-field-retention-matrix.md)
- [MDD/TRIAS raw-retention decision](mdd-trias-raw-retention.md)
- [Detailed Stage 02 evidence/audit](../stages/02-mdd-trias-source-contract-audit.md)

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
are the durable Stage 02-approved design handoff; implementation is deferred.
The [near-event RecordedAtTime lifecycle validation](mdd-trias-event-lifecycle-validation.md)
is separate post-approval evidence for later collector design and is currently
`HOST EXECUTION PENDING`.

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
- Current MDD authorization: **Working**.
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

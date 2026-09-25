# Source Contracts

This directory will hold approved source-contract documentation. The Stage 02
audit document contains evidence for both the Legacy implementation audit and
the controlled MDD raw-response probe; it is not an approved final MDD/TRIAS
response contract.

Current Stage 02 evidence:

- [Stage 02 — MDD/TRIAS Source-Contract Audit](../stages/02-mdd-trias-source-contract-audit.md)

Stage 02A.2 — Controlled MDD Raw-Response Audit — is **Completed** for the
successful controlled response. The normalized host request used XML/TRIAS 1.2
and returned HTTP **200** with response Content-Type
`application/json;charset=UTF-8`; the audited body is JSON and contains five
stop-event results. `MDD_API_KEY VALUE COMPARISON: SAME`,
`HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS`, and current MDD authorization is
working.

The audited response contains timetable and estimate timestamps, service and
stop identities, planned/estimated bay fields, explicit operational flags, and
situation evidence. `RecordedAtTime`, direct delay, cancellation, and an
explicit platform-change flag were not present in this response. These are
single-response observations and do not establish general MDD capability or
absence.

The temporary artifact used for the audit is outside the repository at
`/private/tmp/cologne-transit-stage-02a2-hhqob8km.raw`. It was not copied or
committed, and no raw-payload retention policy was created. No secret was
exposed or written.

The earlier HTTP **401** and S10/S11/S12 DNS findings remain historical
chronology in the Stage 02 audit, not current blockers. S13 made zero MDD
requests, executed zero probes, and did not execute SQL or modify the database.
Stage 02 remains **In progress** while the broader TRIAS/MDD semantic,
coverage, quota, retention, and approval decisions are completed.

Planned documents include:

- GTFS source contract / field inventory
- MDD/TRIAS request contract
- MDD/TRIAS response field inventory
- field retention matrix: defined → returned → coverage → parsed → persisted → analytical purpose
- raw-payload retention decision

Do not treat the Legacy parser audit as proof of MDD capability or actual live
response behavior. Stage 1 and Stage 2 must establish the final contracts from
authoritative documentation, Legacy source evidence, and the controlled raw
response probe.

# Source Contracts

This directory will hold approved source-contract documentation. The Stage 02
audit document contains evidence for both the Legacy implementation audit and
the controlled MDD raw-response probe; it is not an approved final MDD/TRIAS
response contract.

Current Stage 02 evidence:

- [Stage 02 — MDD/TRIAS Source-Contract Audit](../stages/02-mdd-trias-source-contract-audit.md)

Stage 02A.2 — Controlled MDD Raw-Response Probe — is **Blocked**. The final
preflight confirmed `MDD_API_KEY` present = **YES**, but the one authorized POST
to the approved Köln Hbf target returned HTTP **401**. The response header was
`application/json;charset=UTF-8`; its 162-byte/161-character body was
UTF-8 plain text stating that access to this data provider is not enabled,
not a valid MDD service payload. HTTP attempts = **1**, with no retry.

The earlier missing-key preflight remains documented as historical evidence in
the Stage 02 audit. No successful MDD response-field or source-contract audit
was completed by the controlled probe.

The actual successful MDD response shape and field coverage remain open until
an authorized probe obtains a valid service payload and is reviewed. The
raw-response retention decision also remains open.

The local authorization diagnosis recorded in §13 of the Stage 02 audit found
found no concrete local fix for the 401 that can be validated without another
request, but the Windows User-level and macOS process-level `MDD_API_KEY`
values were not compared. Its outcome is
`CREDENTIAL_IDENTITY_UNRESOLVED; PROVIDER_SUPPORT_PROVISIONAL`; this is not an
approved MDD response contract and does not claim that the API key itself is
invalid. The unexecuted probe source is now normalized to the Legacy
Content-Type `application/xml; charset=utf-8`; the historical 401 request used
`application/xml`, and no new request was made.

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

# Source Contracts

This directory will hold approved source-contract documentation. The current
Stage 02A.1 document is evidence for the Legacy implementation audit; it is
not an approved final MDD/TRIAS response contract.

Current Stage 02 evidence:

- [Stage 02A.1 — Legacy Implementation Audit](../stages/02-mdd-trias-source-contract-audit.md)

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

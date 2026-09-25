# Source Contracts

This directory will hold approved source-contract documentation. The Stage 02
audit document contains evidence for both the Legacy implementation audit and
the controlled MDD raw-response probe; it is not an approved final MDD/TRIAS
response contract.

Current Stage 02 evidence:

- [Stage 02 — MDD/TRIAS Source-Contract Audit](../stages/02-mdd-trias-source-contract-audit.md)

Stage 02A.2 — Controlled MDD Raw-Response Probe — remains **Blocked**. The
latest S10 preflight confirmed `MDD_API_KEY` present = **YES** and the user
confirmed that the macOS value matches the historically successful Windows
User-level Legacy value. The reviewed probe was invoked exactly once at
`2026-09-25T17:43:59.786Z`, but DNS name resolution failed before an HTTP
response was obtained:

```text
URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
```

Probe attempts = **1**, HTTP retries = **0**, confirmed MDD HTTP requests =
**0**, HTTP status = **unavailable**, response bytes = **0**, and no temporary
raw artifact was created. No provider-authorization conclusion can be drawn
from this transport failure.

The earlier HTTP **401** response remains historical evidence in the Stage 02
audit. No successful MDD response-field or source-contract audit has been
completed. The actual successful MDD response shape and field coverage remain
open until an authorized probe obtains a valid service payload and is reviewed.
The raw-response retention decision also remains open.

The current conservative diagnosis is
`TRANSPORT_DNS_RESOLUTION_FAILURE; PROVIDER_AUTHORIZATION_UNDETERMINED`.
The probe source uses the Legacy Content-Type
`application/xml; charset=utf-8`. No key, fingerprint, authorization header,
raw response, or derived secret material was written to the repository.

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

# MDD/TRIAS Raw-Payload Retention Decision

**Stage:** 02 — MDD/TRIAS Source-Contract Audit<br>
**Decision state:** Approved Stage 02 default; Stage 7 adds a bounded Stage 8 pilot exception<br>
**Scope:** Full raw MDD XML/JSON response payloads

## Approved decision

Full raw MDD responses are **not a permanent or automated source of record**.

The default policy is:

- do not commit raw MDD responses to Git;
- do not permanently retain full raw XML or JSON payloads by default;
- do not design the future warehouse around raw-payload storage;
- make durable storage consist of approved source-faithful fields plus
  lineage and audit metadata;
- allow temporary raw responses only for explicit diagnostics and
  source-contract validation;
- keep temporary raw artifacts outside source control; and
- never store secrets or authentication material with a raw artifact.

This is the approved Stage 02 contract decision. It closes the Stage 02
question without inventing an arbitrary retention period. The same default
applies to future GTFS-RT protobuf payloads: no permanent or automatic full
raw payload retention is introduced by the source-role decision.

## Evidence and permission boundary

The S13 controlled response was read from a temporary artifact outside the
repository. It was not copied into source control, and the Legacy collector
holds response content in memory before parsing rather than passing the raw
payload to SQL. Those facts support a diagnostic-use boundary; they do not
create a permanent retention obligation or a provider redistribution right.

Current MDD authentication/access works. MDD use remains subject to
registration/activation, provider terms, and the agreed purpose. The current
official MDD NRW dataset description establishes an access/usage context, not
an open-license proof for unrestricted publication or redistribution of raw
payloads.

## Explicitly allowed temporary use

Temporary raw response material may be created for a named diagnostic or
source-contract validation task when it is necessary to inspect structure,
nullability, media type, or parser loss. Such an artifact must:

- be outside Git and outside the durable warehouse by default;
- have no API key, authorization header, token, or other secret in it;
- be access-controlled by the diagnostic environment; and
- be handled under the cleanup controls of the approved diagnostic task.

The Stage 02 decision does not prescribe a permanent-retention period because
no such period was authorized by the evidence supplied for that audit.

The future architecture may evaluate a **short-term** raw-payload retention
option for forensic reprocessing and parser improvements, particularly where a
source field was present but not yet understood. This is a future concept, not
an approved duration or an instruction to retain raw payloads permanently. Any
later duration must be chosen using storage volume, privacy/data
considerations, reprocessing need, and operational cost.

## Stage 7 bounded Stage 8 exception

Stage 7 explicitly approves a narrow automated raw-response exception for MDD
Stage 8 pilot responses only. It does not change the default permanent-
retention decision and does not approve a general raw archive.

| Control | Stage 8 pilot requirement |
|---|---|
| Purpose | Verify raw-response-to-parser fidelity, detect parser field loss, and support offline forensic comparison when unexpected fields appear. |
| Scope | MDD Stage 8 pilot responses only. No GTFS-RT payload capture is included. |
| Storage | Temporary directory outside the repository, such as `/private/tmp/cologne-transit-stage-08-XXXXXXXX/`. |
| Directory permissions | Mode `700`. |
| Raw-file permissions | Mode `600`. |
| Credential check | Before writing, verify that the API-key value is not present in the response content. |
| Prohibited content | No `x-api-key` header, API-key value, credential fingerprint, authorization material, or command-line secret. |
| Retention | Ordinary successful response artifacts are deleted after evidence extraction. If a successful response contains a non-null source `RecordedAtTime`, only its corresponding raw response is retained for review, with a maximum of 24 hours after the run ends. Failed/blocked diagnostic evidence retains the existing bounded 24-hour review behavior. |
| Cleanup | Ordinary successful cleanup remains required. Intentional positive `RecordedAtTime` retention is reported as `RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS`, not as a cleanup failure, and the final report gives the deletion deadline. |
| Source control/publication | Never commit, publish, or redistribute raw payloads. |

The append-only JSONL pilot persistence artifacts are diagnostic artifacts in
the same protected temporary area (or another explicitly protected temporary
directory), not a permanent raw archive and not the future PostgreSQL source
layer. Stage 8 must treat them as temporary diagnostic artifacts, clean them
up under its execution/review report, and never commit the complete history.

### Positive `RecordedAtTime` evidence exception

When a successful MDD response contains at least one non-null source-provided
`RecordedAtTime`, the parsed source-faithful observation remains the evidence
of record with its event/source identity, response identity, call scope,
arrival/departure context, `ObservedAt`, and first-seen
`RecordedAtTimeObservedAt` lineage. The corresponding raw response is marked
`RECORDED_AT_TIME_RAW_EVIDENCE` and is retained only in the protected
temporary Stage 8 directory. Other successful raw responses and ordinary
successful diagnostic files are cleaned normally; this feature does not
retain every successful response.

The retained raw evidence remains mode `600` inside a mode `700` directory,
contains no API key or authorization material, is never committed, published,
moved into Git, or used as the authoritative warehouse source, and is deleted
no later than the current maximum of 24 hours after the run. The non-error
cleanup status is `RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS`. A longer
retention period requires a separate explicit decision based on storage,
privacy/provider terms, and operational cost; this document does not approve
7-day, 14-day, or permanent raw retention.

If no non-null `RecordedAtTime` occurs, the collector may retain only
sanitized aggregate counts for the observed sample: successful responses and
call/timing contexts inspected, non-null occurrences, distinct events, call
scope, and arrival/departure distributions. A zero count is a bounded sample
absence result, not a provider-wide absence claim.

This exception is intentionally bounded to the Stage 8 handoff in
[the Stage 7 design](../stages/07-realtime-pilot-design.md). It does not
authorize Stage 8 execution, production collection, or a permanent raw-
payload policy.

## Relationship to source-faithful retention

The absence of permanent raw payloads does not authorize dropping meaning that
is needed later. The field-retention matrix identifies source-faithful fields,
identities, request parameters, relation scope, and lineage/audit metadata to
retain without making the raw payload itself the source of record.

See the [field-retention matrix](mdd-trias-field-retention-matrix.md), the
[response contract](mdd-trias-response-contract.md), and the
[Stage 02 audit](../stages/02-mdd-trias-source-contract-audit.md).

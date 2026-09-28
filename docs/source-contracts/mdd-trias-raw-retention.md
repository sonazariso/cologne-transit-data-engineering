# MDD/TRIAS Raw-Payload Retention Decision

**Stage:** 02 — MDD/TRIAS Source-Contract Audit<br>
**Decision state:** Approved Stage 02 decision; synchronized with post-Stage-02 consolidation<br>
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

The Stage 02 decision does not prescribe a number of days because no such
period was authorized by the evidence supplied for this audit.

The future architecture may evaluate a **short-term** raw-payload retention
option for forensic reprocessing and parser improvements, particularly where a
source field was present but not yet understood. This is a future concept, not
an approved duration or an instruction to retain raw payloads permanently. Any
later duration must be chosen using storage volume, privacy/data
considerations, reprocessing need, and operational cost.

## Future exception gate

If Stage 7 later establishes a justified need for **automated short-term raw
capture**, it requires a new explicit approval before implementation. That
approval must state:

1. purpose;
2. bounded retention period;
3. storage location;
4. access controls; and
5. cleanup mechanism.

Stage 02 does not approve automated capture, a raw archive, or a policy such
as “keep raw XML forever,” “keep raw JSON forever,” or “keep raw protobuf
forever.” Any bounded automatic raw capture requires a later explicit approval
of purpose, retention, storage, access, and cleanup.

## Relationship to source-faithful retention

The absence of permanent raw payloads does not authorize dropping meaning that
is needed later. The field-retention matrix identifies source-faithful fields,
identities, request parameters, relation scope, and lineage/audit metadata to
retain without making the raw payload itself the source of record.

See the [field-retention matrix](mdd-trias-field-retention-matrix.md), the
[response contract](mdd-trias-response-contract.md), and the
[Stage 02 audit](../stages/02-mdd-trias-source-contract-audit.md).

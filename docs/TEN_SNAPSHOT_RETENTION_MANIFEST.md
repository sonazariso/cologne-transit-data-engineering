# Ten retained snapshots — operator-confirmed ID set and dry-run manifest

**Status: OPERATOR_CONFIRMED_ID_SET / SCHEMA_PRESERVATION_REQUIRED / CLEANUP_BLOCKED_PENDING_LIVE_EVIDENCE.**
S154 supplies the exact ten IDs approved for the portfolio sample. The
repository does not contain the referenced `TEN_SNAPSHOT_RETENTION_MANIFEST_FILLED.md`
attachment, so this document integrates the confirmed IDs without inventing
RunIds, AuditIds, timestamps, hashes, child counts, or physical-space
measurements. The read-only SQL result and its evidence attachment remain
required before any cleanup working copy is authorized.

## Approved retention intent

Retain exactly these ten operator-confirmed `SnapshotId` values:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

Retain every child row for each ID in all seven source-faithful families:

1. `stg.MddMacTimingObservation`
2. `stg.MddMacCallObservation`
3. `stg.MddMacSituationEvidence`
4. `stg.MddMacServiceSectionEvidence`
5. `stg.MddMacAttributeEvidence`
6. `stg.MddMacResponseError`
7. `stg.MddMacParserGap`

Keep each source payload in its exact existing representation (plaintext or
GZIP), original UTF-16LE bytes, `PayloadHashSha256`, NULL/empty distinction,
Unicode, duplicates, and all `SnapshotId`/`RunId`/`AuditId` foreign-key
lineage. Keep the minimum connected `ctl` parents and operational records
needed to explain each retained snapshot: the collector run, target audit,
HTTP attempts, referenced quota permits, and any run-linked claim/lease rows
reported by the read-only query. Working ETL/SQL structures and views remain
in place.

## S155 complete-structure and cross-layer data invariant

This manifest governs DATA only. The full existing SQL Server structure must
remain available for future Data Analyst training: every user schema (`stg`,
`dw`, `ctl`, and any deployed `src`, `wrk`, `core`, `analytics`, or other
schema), physical table, column, appropriate index, key, foreign key, view,
function, stored procedure, and trigger. A view is a logical object, not a
physical table; the live inventory must distinguish `sys.objects.type = 'V'`
from `sys.objects.type = 'U'` before any retention decision. A replacement
database, if approved, must reproduce that complete object inventory even when
some tables are empty in the reduced sample.

The cross-layer data manifest must also retain:

- a small, real, coherent GTFS dataset and every required foreign-key parent,
  child, and bridge row;
- relevant warehouse dimension and fact rows and the complete required
  dimension/fact/lineage foreign-key closure; and
- the ETL lineage, configuration, audit, run, target, quota, claim, lease and
  other control rows needed to explain and reproduce the retained sample.

No GTFS, warehouse, `src`, `wrk`, `core`, `analytics`, or control table is
automatically disposable because it is outside the ten MDD `SnapshotId`s. The
retention review must inventory actual rows and dependencies across all
layers, then record table-level data decisions separately from the immutable
schema/object inventory.

The approved ID set is not a continuous panel and is not a representative
frequency estimate. No child, payload, lineage, GTFS, warehouse, or analytics
object is eligible for automatic discard merely because it is outside the
ten-snapshot set.

## Integrated operator manifest

The IDs below are the operator-confirmed manifest integrated from S154. The
remaining columns deliberately identify the missing live evidence rather than
claiming values that are not present in this worktree. A row count other than
ten, an ID mismatch, a hash mismatch, an orphaned child, or an unresolved
consumer is a hard stop.

| Slot | SnapshotId | RunId | AuditId | TargetIdentity | ObservedAtUtc | Payload representation | Original SHA-256 | PayloadHashSha256 | Timing | Call | Situation | Service section | Attribute | Response error | Parser gap | Rationale | Verified |
| ---: | ---: | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | 22 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 2 | 93 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 3 | 185 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 4 | 189 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 5 | 207 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 6 | 224 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 7 | 225 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 8 | 226 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 9 | 228 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |
| 10 | 229 | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH LIVE SQL | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | ATTACH | OPERATOR_CONFIRMED_ID_ONLY | ID_APPROVED_LIVE_ROW_UNVERIFIED |

The live candidate result must still prove successful HTTP 200/parsing/
persistence status, physical payload XOR, original-byte SHA-256, payload hash,
all seven child counts, and `SnapshotId`/`RunId`/`AuditId` parity. Preserve the
complete result set as the evidence attachment; do not replace it with the ID
list above.

## Preferred cleanup method and scope boundary

The preferred path is a small, independently validated replacement database
containing the **complete existing schema and object inventory** plus exactly
these ten complete snapshots and the reviewed cross-layer sample. It may be considered only if an isolated copy/rehearsal measures
the full peak of the new database, copy/log/tempdb overhead, and rollback
reserve on the existing C: drive and proves:

```text
measured C: free bytes - measured replacement peak reserve >= 12 GiB
```

The rehearsal must validate every retained child family, source payload and
hash, `ctl` lineage, GTFS coherence, warehouse dimension/fact coherence,
foreign keys across all layers, and every working ETL/SQL object and consumer
view before the original database is considered for retirement. It must not
create two large database copies. If the measured peak does not fit, the
separately reviewed bounded in-place plan is the fallback; it is not an
automatic choice.

GTFS and warehouse DATA require separate portfolio-necessity reviews and
explicit coherent-sample manifests. The inventory emits separate `GTFS`,
`warehouse`, `analytics`, lineage and complete-object result sets; zero rows
means only that no matching live objects were visible to that query, not that
a destructive decision is authorized. No schema, table definition, view,
function, procedure, trigger, index, key or foreign key is removed by the
ten-snapshot cleanup scope.

## Required verification gates before cleanup

The operator must attach all of the following to the manifest review:

- the complete read-only candidate result for all ten IDs and a manifest
  fingerprint;
- the complete live schema/object inventory, with physical tables distinguished
  from views and counts of schemas, tables, views, functions, procedures,
  triggers, indexes, keys and foreign keys;
- exact before/after counts for the seven child families, with every retained
  row and payload preserved;
- the cross-layer data manifest and readback proving a coherent GTFS sample,
  relevant warehouse dimensions/facts, required ETL lineage/configuration/audit
  rows, and all foreign-key closure;
- source payload representation, original-byte SHA-256, NULL/empty state and
  `SnapshotId`/`RunId`/`AuditId` parity;
- foreign-key, SQL-module, SQL-Agent-job, direct SQL, notebook, export and
  Power BI/external-consumer review, including raw `SnapshotJson` access;
- backup metadata plus operator-run `RESTORE VERIFYONLY WITH CHECKSUM` for an
  off-C recovery point covering the selected state; the October 14 backup must
  not be modified;
- fresh MDF/LDF/tempdb, autogrowth, active-session, collector-state and
  Windows C: measurements, including before/after file and volume bytes;
- an independently measured replacement-database peak or a documented,
  reviewed reason the bounded in-place fallback is safer;
- proof that the projected worst-case C: free space never falls below the
  12 GiB floor. The 80% project-recovery target is measured from before/after
  bytes, never from logical payload arithmetic.

The read-only inventory, replacement feasibility query, candidate query and
retention dry run never authorize `DELETE`, `DROP`, `TRUNCATE`,
`DBCC SHRINKFILE`, backup overwrite, collector restart, or schema removal.

## Exact operator approval required

Before any cleanup working copy is enabled, the administrator must complete
and retain this approval with the attached result references:

```text
I, <operator>, approve the portfolio cleanup review on <UTC timestamp>.
I confirm the exact retained SnapshotIds are:
22, 93, 185, 189, 207, 224, 225, 226, 228, 229.
I reviewed the live ten-row result, all seven child-family counts, payload
representation and hashes, NULL/empty state, and RunId/AuditId lineage.
I reviewed the complete schema/object inventory and confirm that all schemas,
physical tables, columns, indexes, keys, foreign keys, views, functions,
procedures and triggers are preserved.
I reviewed the coherent GTFS data sample, relevant warehouse dimensions and
facts, required ETL lineage/configuration/audit rows, and FK closure across all
layers.
I reviewed foreign keys, SQL modules/jobs, direct consumers, notebooks,
exports, Power BI access, and GTFS/warehouse/analytics necessity separately.
I verified an off-C checked recovery point covering this state and will not
overwrite the October 14 recovery point.
I reviewed fresh C:/MDF/LDF/tempdb/active-session measurements and confirm
the selected method keeps at least 12 GiB free at worst-case peak.
I approve the documented replacement-database choice or the explicitly
identified bounded in-place fallback. I authorize the dry run only; any
destructive apply and any shrink require a separate explicit approval.
Approval reference: <ticket or evidence path>
```

## Current decision

```text
EXACT_RETENTION_COUNT: 10
ACTUAL_SNAPSHOT_IDS: OPERATOR_CONFIRMED — 22, 93, 185, 189, 207, 224, 225, 226, 228, 229
LIVE_ROW_EVIDENCE: NOT_ATTACHED_IN_WORKTREE
DESTRUCTIVE_SQL: NOT_EXECUTED
PHYSICAL_PROJECT_RECOVERY: NOT_MEASURED
80_PERCENT_TARGET: TARGET_ONLY_UNTIL_BEFORE_AFTER_BYTES_EXIST
REPLACEMENT_DATABASE: PREFERRED_ONLY_IF_INDEPENDENT_PEAK_FITS_C_WITH_12_GIB_FLOOR
GTFS_WAREHOUSE_DISPOSITION: COHERENT_SAMPLE_MANIFEST_REQUIRED; NO_AUTOMATIC_DISCARD
SCHEMA_OBJECT_DISPOSITION: PRESERVE_COMPLETE_EXISTING_SQL_SERVER_STRUCTURE
COLLECTORS: DISABLED
GITHUB_PUSH: NOT_PERFORMED
```

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

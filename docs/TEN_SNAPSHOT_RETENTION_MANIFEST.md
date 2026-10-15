# Ten retained snapshots — selection and verification manifest

**Status: BLOCKED_UNTIL_READ_ONLY_OPERATOR_EVIDENCE.** The repository has
not connected to the deployed SQL Server for S153, so no SnapshotId is
published here and no row is claimed deleted. The exact ten-row candidate
output must come from
[sql/portfolio/select-ten-snapshots-readonly.sql](../sql/portfolio/select-ten-snapshots-readonly.sql).
Do not fill this table from memory, an arbitrary TOP, or historical counts
that do not contain actual deployed IDs.

## Approved retention intent

Retain exactly ten actual, successful, persisted historical source snapshots
with every row in these seven child families:

1. stg.MddMacTimingObservation
2. stg.MddMacCallObservation
3. stg.MddMacSituationEvidence
4. stg.MddMacServiceSectionEvidence
5. stg.MddMacAttributeEvidence
6. stg.MddMacResponseError
7. stg.MddMacParserGap

Keep the source payload in its exact existing representation (plaintext or
GZIP), original UTF-16LE bytes, PayloadHashSha256, NULL/empty distinction,
Unicode, duplicates, and all SnapshotId/RunId/AuditId foreign-key lineage.
Keep the minimum connected ctl parents and operational records needed to
explain each retained snapshot: the collector run, target audit, HTTP
attempts, referenced quota permits, and any run-linked claim/lease records
reported by the selection query. The proposed purge leaves unrelated ctl
history in place unless a later separately reviewed manifest expands the
scope.

## Exact operator manifest

Copy the ten rows returned by the read-only candidate query into this table
after human review. Include the rationale from the real evidence, not an
assumed category. A row count other than ten is a hard stop.

| Slot | SnapshotId | RunId | AuditId | TargetIdentity | ObservedAtUtc | Payload representation | Original SHA-256 | PayloadHashSha256 | Timing | Call | Situation | Service section | Attribute | Response error | Parser gap | Rationale | Verified |
| ---: | ---: | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 2 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 3 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 4 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 5 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 6 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 7 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 8 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 9 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| 10 | PENDING SQL | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

The SQL result also reports original byte length, physical GZIP hash,
planned/estimated/recorded timing counts, identity status, source coverage,
and connected control-row counts. Preserve that result set as the evidence
attachment for this manifest. The selection is a diversified interview sample,
not a continuous panel and not a representative frequency estimate.

## Required verification gates

Before any destructive step, the operator must attach:

- the complete ten-row candidate result and a manifest fingerprint;
- exact counts for all seven child families, with retained rows equal before
  and after the selected-set copy/readback;
- source payload representation, original-byte SHA-256, NULL/empty state and
  SnapshotId/RunId/AuditId parity;
- foreign-key and module/job/direct-consumer review, including any external
  SQL or Power BI reader of physical SnapshotJson;
- the off-C verified recovery point and coverage for the current selected
  rows; the existing October 14 backup must not be modified;
- fresh MDF/LDF/tempdb, autogrowth, active-session, collector-state and
  Windows C: measurements;
- a documented choice between a tiny independent complete sample database and
  a bounded in-place purge. The choice must preserve the 12 GiB C: floor and
  must not create two large database copies.

The read-only inventory and candidate query never authorize DELETE, DROP,
TRUNCATE, DBCC SHRINKFILE, or collector changes. Use the separate dry-run and
apply files only after the exact rows and recovery commands have been
reviewed.

## Current decision

EXACT_RETENTION_COUNT: 10
ACTUAL_SNAPSHOT_IDS: PENDING_READ_ONLY_SQL_RESULT
DESTRUCTIVE_SQL: NOT_EXECUTED
PHYSICAL_PROJECT_RECOVERY: NOT_MEASURED
80_PERCENT_TARGET: TARGET_ONLY_UNTIL_BEFORE_AFTER_BYTES_EXIST
COLLECTORS: DISABLED
GITHUB_PUSH: NOT_PERFORMED

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

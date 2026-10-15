# Ten-snapshot retention manifest — live evidence and operator boundary

**Status: `LIVE_READ_ONLY_EVIDENCE_CAPTURED_2026-10-15 / IN_PLACE_RETENTION / APPLY_NOT_EXECUTED`.**

This is the finalized Data Engineering portfolio sample.  The evidence below
was read directly from `CologneTransitIntelligence` on `DataAnalyst-VM` using
read-only SQL.  No production `DELETE`, `SHRINK`, `DROP`, collector restart,
backup overwrite, or GitHub push was performed.

## Approved MDD retention set

Exactly these ten `SnapshotId` values are retained:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

Every row in all seven dependent source-faithful families is retained for
these IDs.  The payload representation, original UTF-16LE bytes, empty/null
distinction, `PayloadHashSha256`, `RunId`, and `AuditId` are part of the
readback contract.

The seven dependent physical families are `MddMacTimingObservation`,
`MddMacCallObservation`, `MddMacSituationEvidence`,
`MddMacServiceSectionEvidence`, `MddMacAttributeEvidence`,
`MddMacResponseError`, and `MddMacParserGap`.

## Exact live row evidence

All ten rows were present with HTTP 200, completed/succeeded run and audit
status, `PARSED`, `PERSISTED_PARSED_SNAPSHOT`, plaintext payloads, and
`OriginalWasEmpty = 0`.

| Slot | SnapshotId | RunId | AuditId | TargetIdentity | ObservedAtUtc | Original SHA-256 | PayloadHashSha256 | Timing | Call | Situation | Service section | Attribute | Response error | Parser gap |
| ---: | ---: | --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 22 | `LR_0edc9b1a56_debcd106-2fcc-47f0-99e9-53717125be6f` | 2 | `de:05315:11110` | `2026-10-01T11:30:58.440000` | `C0362E08E8B87BDA0E997B68F17960739B0078CDABA347CF3BD9D41D24FF4349` | `4511e4f27c1e4376ee587ff15f5f4586e78bd76f2e61d116ed90d98d75633800` | 995 | 526 | 0 | 20 | 0 | 0 | 121 |
| 2 | 93 | `127809b0-70fa-4770-9943-664942be5fc3` | 95 | `de:05315:11201` | `2026-10-07T16:25:08.673000` | `A557056A0DE9F41E23C03FFC35AE8716D4C63ECFB77D1FEB60EB0C3BAC7D8C2B` | `6f568d9bb369cbba51aa01cfb55a9c976a548b3f651713b91ec711f3248a4b94` | 178 | 96 | 0 | 5 | 22 | 0 | 0 |
| 3 | 185 | `177056c4-1e5a-4db8-9f8d-fa3eaabf0661` | 195 | `de:05315:11111` | `2026-10-08T12:30:29.218000` | `5381047347CF962D617B35E9E0AC303C219EB1EBE3E5F74760E6B2BBC9E696E4` | `f82ee6b33b436f849e93d95ea435e8151dba931ed7bb21e551f0510458f66562` | 1114 | 585 | 0 | 20 | 0 | 0 | 0 |
| 4 | 189 | `2d7b436d-dd36-433a-b24a-f9283a36ee16` | 439 | `de:05315:11212` | `2026-10-12T14:10:28.819000` | `DE20071DA027AF416FCEE97B16A70BCE95F14FDF87758A8611688E2ABE5DA46B` | `207a298f359f68eae0e48fe6227ed3cb99584321412ad55aae023dff1aab0f85` | 1258 | 653 | 0 | 20 | 0 | 0 | 0 |
| 5 | 207 | `77e2f199-bd93-4117-be76-f984736c2c0c` | 457 | `de:05315:11511` | `2026-10-12T14:15:24.831000` | `D50023DC66291CC3A5C9B0D916C340D06F66F9829B454B863F872C5893BDC1B0` | `e2e561c72f0e089bfab7f1d2d74649535e65469a7387188201fe61f9c55ec036` | 1946 | 1018 | 0 | 30 | 0 | 0 | 0 |
| 6 | 224 | `f528e321-ccb4-452f-87e9-aaa37c1042ba` | 474 | `de:05315:11112` | `2026-10-12T14:20:23.455000` | `0FBD70D0DC823DD167DC782C4F2171A6AFFAA3FCF6FD38769F4AD8E6F6C12D18` | `9ad8037155225ebcaa469252a7b9f2f881e4f19817fa5fe3399ba230bace5c09` | 2174 | 1132 | 0 | 30 | 0 | 0 | 0 |
| 7 | 225 | `f528e321-ccb4-452f-87e9-aaa37c1042ba` | 475 | `de:05315:11209` | `2026-10-12T14:20:29.248000` | `824BEE6A4F8F7BF8498FB45F15803742F0E04721288E93F068556822CD29B42F` | `16c4a57133a7e556037762d5933ac7fde632f5dbe0a7a2465494d3cab7c6013b` | 1858 | 974 | 0 | 30 | 0 | 0 | 0 |
| 8 | 226 | `f528e321-ccb4-452f-87e9-aaa37c1042ba` | 476 | `de:05315:11211` | `2026-10-12T14:20:38.705000` | `C879CC9B55C4E590D499403A4C135824D1CE4978A88F4E83026D57911A15F221` | `e65a9776c5ab5958db9209bab4f066279a40d97530e4c92fe64c1bdc19f897bf` | 1627 | 855 | 0 | 30 | 0 | 0 | 0 |
| 9 | 228 | `f528e321-ccb4-452f-87e9-aaa37c1042ba` | 478 | `de:05315:11410` | `2026-10-12T14:20:52.288000` | `086AA4E43901784D54ED2B4CF9BEF0EDDB71E0743585C388A92C7D8138A08A0E` | `e9db01cd0ab36276b05cb07142d689ced456635f2d5e9bfa12f02325b2c0c150` | 1748 | 915 | 0 | 30 | 0 | 0 | 0 |
| 10 | 229 | `f528e321-ccb4-452f-87e9-aaa37c1042ba` | 479 | `de:05315:11411` | `2026-10-12T14:20:58.068000` | `E5E248E4A5A1B06A6F40B3410B2D05B5D8BA5EAC28444E4E4D428721397BC8A6` | `62f505f81b3db86b9d8e36c76cacdf1ab97a91a49e6f01b8ce243aa6541264c8` | 1410 | 750 | 0 | 30 | 0 | 0 | 0 |

**Retained child totals:** Timing 14,308; Call 7,504; Situation 0;
ServiceSection 245; Attribute 22; ResponseError 0; ParserGap 121.

**Control lineage readback:** six distinct runs; ten target audits; ten HTTP
attempts; nine referenced quota permits; six dispatcher claims; six dispatcher
claim events; 81 target claims; 81 target-claim events; zero production leases;
nine production-lease events; and 65 quota-audit rows.  The executable apply
manifest selects these rows through their exact RunId/AuditId/PermitId
relationships.

## Minimum cross-layer sample

The executable manifest in `sql/portfolio/retention-apply.sql` selects and
deletes only through these concrete predicates.  The live readback captured:

| Layer | Retained evidence |
| --- | --- |
| GTFS | AgencyId `5`; ServiceId `50126`; CalendarDate `20260103` exception `2`; one FeedInfo row; RouteId `de:nrw:rb25:`; ShapeId `22` at `ShapePtSequence = 0`; two stops (`de:05315:11201`, `de:05315:11201:7:80`); one selected trip; one StopTime at sequence `2`; zero frequencies and transfers |
| GTFS control | `GtfsLoadBatch.LoadBatchId = 2`; singleton `GtfsStagingState` current batch `2`; 23 validation rows |
| Warehouse | `StaticWarehouseLoadBatchId = 3`; `FactOperationalStopOutcomeKey = 467`; `FactScheduledStopEventKey = 615`; `FactScheduledTrip.TripKey = 563`; Agency 13; Date 20260905; Mode 3; Route 2; Service 892; Stops 8 and 1017; one BridgeServiceDate row |
| Realtime | ObservationKey 4; SituationObservationKey 2; link `(4,2)` |

The sample is intentionally discontinuous and unsuitable as a reporting
dataset.  This closeout is Data Engineering only; Power BI and a reporting
product are out of scope.

## Direct capacity evidence

Read-only SQL Server measurements captured on 2026-10-15 were approximately:

| Measure | Value |
| --- | ---: |
| Windows `C:` free | 16,098,295,808 bytes (14.9927 GiB) |
| Required floor | 12,884,901,888 bytes (12 GiB) |
| MDF allocated | 30,591,418,368 bytes (about 28.49 GiB) |
| MDF used | 30,550,589,440 bytes (3,729,320 used pages) |
| MDF internal free | 4,984 pages (about 40.8 MiB) |
| LDF allocated | 1,954,537,472 bytes (about 1.82 GiB) |
| LDF used | 61,063,168 bytes (3.124%) |
| Tempdb unallocated | 29,229,056 bytes at the capture |

The apply script remeasures C:, log free bytes, tempdb unallocated bytes,
database/tempdb next-growth reserves, and an index-aware reserve based on the
maximum measured non-retained row payload before every `DELETE TOP` batch.  It
stops before starting a batch if the 12 GiB floor, log reserve, or tempdb
reserve cannot be proven.  The current headroom is only about 3 GiB, so no
cleanup is authorized by this document.

## Exact SSMS execution instructions

1. Open SSMS, connect to `DataAnalyst-VM`, and select
   `CologneTransitIntelligence`. Save result sets to the review evidence
   folder; do not run from an application connection or an active collector
   session.
2. Execute `sql/portfolio/retention-inventory-readonly.sql`, then
   `sql/portfolio/select-ten-snapshots-readonly.sql`. Confirm the exact-ID
   readback matches this document, including hashes and all seven child
   counts.
3. Execute `sql/portfolio/retention-dry-run.sql`. Confirm every
   `retention_cross_layer_selected_counts` row is `PASS`, the FK inventory is
   complete, and the capacity result is current. Capture the result sets.
4. Separately verify an off-`C:` checked recovery point with
   `RESTORE VERIFYONLY WITH CHECKSUM`; do not overwrite the October 14
   recovery point. Confirm collector/lease inactivity and consumer review.
5. Create a reviewed working copy of `retention-apply.sql`. Set `@Apply = 1`
   and complete every gate/reference at the top of that working copy. Execute
   the whole file in one SSMS window. Do not remove `GO`, change selectors,
   increase batch sizes, or run it concurrently. The script commits each
   batch, so an interruption is resumable: recheck the live evidence, then
   rerun the same reviewed working copy.
6. Review the post-apply hash, child-count, cross-layer-count, FK,
   schema/object/column/index/module inventory and `DBCC CHECKCONSTRAINTS`
   result sets. Stop if any result is not an exact pass.
7. Only after the retention readback passes, run
   `sql/portfolio/recover-project-space-readonly.sql`, then review the live
   recovery preflight in `recover-project-space-apply.sql`. A separate
   approval must set an exact MDF target at or above the measured allocation
   high-water mark and below current allocation. Never shrink the log, use `SHRINKDATABASE`, or infer
   Windows recovery from MDF internal free pages.

No step above authorizes a production mutation by itself. The repository
defaults remain fail-closed and the S156 implementation was not executed.

The project was stopped due to the high volume of collected data and insufficient available storage capacity. Successful bounded ingestion and SQL Server persistence were demonstrated; continuous coverage was not achieved.

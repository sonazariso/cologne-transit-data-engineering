# Quota, capacity planning and database retention

## Quota budget — approved design, not a measured 30-day run

MDD NRW account cap: **250,000 requests/month** (shared credential). Historical Stage 13 decision added an operational ceiling of **210,000**, and an automatic retry guard at **207,500**, subject to current SQL/credential period evidence. Provider reset boundary/timezone and cross-consumer consumption required explicit evidence; the fail-closed checks are intentionally conservative. Never circumvent them to obtain a demo screenshot.

| Tier | Targets | Requests/hour | Requests/24h | Requests/30d | Requests/31d |
| --- | ---: | ---: | ---: | ---: | ---: |
| A / 5 min | 8 | 96 | 2,304 | 69,120 | 71,424 |
| B / 15 min | 20 | 80 | 1,920 | 57,600 | 59,520 |
| C / 30 min | 22 | 44 | 1,056 | 31,680 | 32,736 |
| **Total** | **50** | **220** | **5,280** | **158,400** | **163,680** |

These are scheduled logical requests with **zero misses**, not transmissions proven by evidence. Up to two attempts per due target could in principle create **316,800 requests/30d**, exceeding provider cap; guardrails must stop this. A planning **10% retry reserve** on 30-day baseline gives **174,240**, before external consumers and replays. Fixed 30-minute phase loads: `19/18/18/18/19/18`; approved max concurrency 2. A worst-case 19-target phase at 30 seconds/request and concurrency 2 needs ten sequential batches = **300 seconds** **without startup/SQL/parse overhead or safety margin**: this cannot establish five-minute deadline reliability.

## Storage growth and why project stopped

Nested TRIAS timing/call evidence expands much faster than HTTP-response counts. The 2026-10-13 V3 capacity review estimates **~7.20 MiB/successful snapshot** from a bounded sample; extrapolation at 36,960 weekly planned requests is **~259.9 GiB/week**, dramatically over a 10 GiB weekly budget. This is a risk estimate, **not** measured seven-day usage. GZIP and SQL compaction were investigated, but lab data did not support a production GO decision. See [`tiered50-storage-v3-capacity-review.md`](operational/tiered50-storage-v3-capacity-review.md).

## Final SQL retention intent and source snapshot set

The approved **ten** preserved source SnapshotIds are:

```text
22, 93, 185, 189, 207, 224, 225, 226, 228, 229
```

The **pre-cleanup** October 15 read-only manifest recorded **14,308 timing**, **7,504 call**, **245 service-section**, **22 attribute** and **121 parser-gap** child rows for those ten, with zero situation/response-error rows, and necessary `ctl` run/audit/claim/quota evidence. It also describes a small coherent GTFS and warehouse sample. Refer to [`TEN_SNAPSHOT_RETENTION_MANIFEST.md`](TEN_SNAPSHOT_RETENTION_MANIFEST.md). These are **baseline/expected** counts, **not verified post-deletion** counts; the operator's October 16 shrink and CHECKDB do not prove the surviving row set.

Do **not** casually rerun SQL retention `DELETE` scripts: those were designed for a pre-closeout schema/data state and contain explicit destructive gates. Retain SQL source files as auditable historical engineering artifacts only. A new retention task requires fresh current inventory, independent backup and explicit review.

## Observed one-time disk recovery, 16 Oct 2026

| Item | Operator-observed result |
| --- | --- |
| MDF file | 3,072 MiB after `DBCC SHRINKFILE` |
| LDF file | 264 MiB after one-time log shrink (`SIMPLE`; prior `log_reuse_wait_desc=NOTHING`) |
| Final Windows C: free | 50,320,338,944 bytes = **46.86 GiB** |
| Initial pre-recovery C: free | about **15.23 GiB** |
| Net increase across SQL + OS cleanup | **31.63 GiB** |
| `DBCC CHECKDB` | finished without reported errors |

Windows Component Store cleanup and user Temp clearing contributed to the net disk improvement. The before/after `FileProperty` result does not imply size reduction equals database payload deletion; shrinking recovered previously allocated unused file pages. **Routine periodic shrinking is discouraged.** A fresh post-retention full backup and tested restore have not been supplied.

## Backup and recoverability requirements

A SQL Server `msdb` full-backup history entry establishes only a backup record, not valid media. Prior media was located on an off-C VMware/macOS shared destination, but **the most recent observed dated backup was 14 Oct 2026**, before the shrink operation. Obtain a fresh FULL backup after closeout to a capacity-safe non-C location; test `RESTORE VERIFYONLY WITH CHECKSUM` as applicable, then **restore into an isolated disposable database** and verify row counts, `CHECKDB`, app compatibility and retention hashes. Ensure old backup media stays protected until replacement restore is proven.

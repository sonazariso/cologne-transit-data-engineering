# Verification matrix — demonstrated vs not proven

This page is deliberately conservative. Claims are attributed to **historical repository evidence** or to **operator-reported 16 Oct 2026 SSMS/PowerShell results**. The document-writing process did not connect to the live SQL Server or call MDD.

| Claim | Evidence | Evaluation |
| --- | --- | --- |
| 50 compatible source parent stations | Stage 12 compatibility probe (50 HTTP 200, 50 parsed, 50 compatible, one result requested) | **PASS for compatibility probe only** |
| Fixed approved panel selection | Stage 12 candidate replay and Stage 13 tiered policy | **Implemented and reproducible offline** |
| 50-target SQL configuration | Stage 14 materialization, 7 historical IDs + 43 new IDs | **Historically recorded** |
| Historical bounded ingestion | Stage 8 reported 343 success HTTP responses in Köln Hbf pilot; six final corrected Legacy7 buckets | **Historically demonstrated** |
| LIVE_RESEARCH import parity | Oct 7 importer report: 70 snapshots, 38,120 calls, 72,190 timings, 1,400 ServiceSections, 121 parser gaps | **Historical source/destination reconciliation PASS** |
| Initial Tiered50 scheduled heartbeat | First-heartbeat record: 18 due/target audits, 2 HTTP-backed requests, 16 deadline/skipped | **Coverage FAILED; Tiered50 stopped** |
| Long-running continuous 50-station coverage | No accepted multi-day / full-cycle availability evidence | **NOT DEMONSTRATED** |
| Complete actual event delays | Source `RecordedAtTime` absent in 72,190 imported timings in documented window | **NOT DEMONSTRATED** |
| Storage V3 fits 10 GiB/week | Capacity forecast ~259.9 GiB/week at nominal requests; no proven lab savings | **NO_GO** |
| Exactly ten source snapshots after deletion | Pre-retention manifest and user reports of shrink; no post-retention count/hash result supplied | **POST-RETENTION SQL RECONCILIATION UNVERIFIED** |
| SQL structural consistency after cleanup | `DBCC CHECKDB` operator reported success on 2026-10-16 | **PASS for that check** |
| Physical C: recovery | 15.23 → 46.86 GiB free across SQL + Windows cleanup, operator readings | **PASS for measured disk space** |
| New validated full backup after cleanup | Only earlier `msdb` history shown; `RESTORE VERIFYONLY` / test-restore not shown | **OPEN** |

## Evidence sources

- [`docs/stages/12-50-station-selection.md`](stages/12-50-station-selection.md), [`docs/stages/13-tiered-collector-readiness.md`](stages/13-tiered-collector-readiness.md)
- [`docs/operational/tiered50-deployment-validation-report.md`](operational/tiered50-deployment-validation-report.md), [`docs/operational/s98-live-research-import-and-cutover.md`](operational/s98-live-research-import-and-cutover.md)
- [`docs/operational/tiered50-storage-v3-capacity-review.md`](operational/tiered50-storage-v3-capacity-review.md), [`docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md`](TEN_SNAPSHOT_RETENTION_MANIFEST.md)
- Operator-supplied DBCC/DISM/PowerShell final session evidence from **16 Oct 2026**; not independently rerun by this repository documentation pass.

## How to raise a claim's confidence

Obtain signed/approved timestamped results from the **current** SQL Server, not stale JSON exports; check run/target/attempt/snapshot counts and retention child hashes; confirm a fresh external full backup and test restore; capture quota ledger and exact provider reset terms; only then consider controlled deployment claims. Offline test suite success is a code-regression signal and cannot substitute for the acceptance sequence above.

# LIVE_RESEARCH import and cutover record

Captured 2026-10-07. This record is operational evidence, not a new project
stage or production Tiered50 activation.

## Source and capacity

The importer and validator used only the explicit source
`/Users/admin/cologne-transit-data/live-research/collector.sqlite3`:

- 575,041,536 bytes;
- SHA-256 `0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc`;
- SQLite application schema 5, role `LIVE_RESEARCH`, context
  `LIVE_RESEARCH_SINGLE_1`;
- integrity and quick checks `ok`;
- source session `2026-10-01T16:59:32.654121Z` through
  `2026-10-01T22:55:22.809977Z` (5.9306 hours).

The fresh pre-import capacity gate passed with a conservative requirement of
1,150,083,072 bytes. ROWS internal free space and physical volume free space
were evaluated independently; LOG reusable space was reported but never
counted toward ROWS capacity. After import, the read-only validator still
passed: the C: volume had 46,185,209,856 bytes free, projected post-import
reserve was 45,050,331,136 bytes, and `log_reuse_wait_desc` was `NOTHING`.

## Import and reconciliation

All prior failed batches remain immutable and resume-safe. The completed
historical import and exact rerun were:

- `a2233292-da3e-483d-bb4f-c4b215a34820`: `COMPLETE`; reconciled the partial
  earlier work and inserted 20 source-table-only ServiceSection rows.
- `ef31f8a1-f688-45c5-98ab-8871fe2c7693`: `COMPLETE`; exact rerun inserted
  zero table or evidence rows.

The final read-only validator reports:

| Family | Source | Destination | Status |
| --- | ---: | ---: | --- |
| snapshots | 70 | 70 | PASS |
| call observations | 38,120 | 38,120 | PASS |
| timing observations | 72,190 | 72,190 | PASS |
| ServiceSections | 1,400 | 1,400 | PASS |
| situations | 0 | 0 | PASS |
| Attribute/GeneralAttribute | 0 | 0 | PASS |
| response errors | 0 | 0 | PASS |
| parser gaps | 121 | 121 | PASS |

The parsed snapshot projection contains 1,380 ServiceSections; the remaining
20 are the source table's durable rows and were inserted directly. The
source-table reconciliation is therefore zero unexplained differences, not a
loss of 20 rows. No `RootCause` field was introduced, and provider association
remains evidence rather than causality.

No source `RecordedAtTime` value or source path occurred. All 72,190 imported
timings have established source-event identity, zero non-NULL
`RecordedAtTime`, and zero non-NULL `RecordedAtTimeSourcePath`. The reporting
view has 17,622 imported identities, all with latest/first/last values NULL
and no latest-NULL/history-non-NULL contradiction.

Existing sampling target IDs 1–7 and their StopPointRefs are unchanged. No
Windows-era row was merged, deleted, reseeded, or relabeled as Mac runtime
evidence. The SQL Server database remains the sole authoritative database;
Power BI, Tiered50, and the active SQLite runtime were not changed.

## Windows runtime gate

The Task Scheduler operational log positively identifies the exact task
`\Cologne Transit Realtime Collector`, principal `DATAANALYST-VM\Somaye`,
`powershell.exe` action, five-minute time trigger, and successful result code
0. The latest SQL evidence captured after the attempted retirement was
`CollectorRunId 40390`, started `2026-10-07 13:28:53Z`, completed
`13:28:56Z`, HTTP 200. The Windows task was therefore still active.

The exact disable command was attempted through a temporary SQL-side
`xp_cmdshell` bridge and returned that the task did not exist for the SQL
Server service account. Both `xp_cmdshell` and `show advanced options` were
restored to their original disabled state. No ambiguous task was changed, and
the Mac scheduler remains disabled. Completing the cutover requires an
interactive or administrative Windows Task Scheduler context that can access
the user-owned task object; the SQL service account alone is insufficient.

The reusable read-only checks are in
[`scripts/capacity_preflight.py`](../../scripts/capacity_preflight.py) and
[`scripts/validate_sqlite_import.py`](../../scripts/validate_sqlite_import.py).

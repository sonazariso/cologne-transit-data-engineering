# LIVE_RESEARCH import and cutover record

Captured 2026-10-07. This record is operational evidence, not a new project
stage or production Tiered50 activation. The import and Windows-gate sections
below describe the pre-activation state; the current post-retirement and
Legacy7 activation addendum is at the end of the record.

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

## Windows runtime gate before operator retirement

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

## Current post-retirement and Legacy7 activation evidence

The operator manually disabled the exact task `\Cologne Transit Realtime
Collector`. The final read-only SQL confirmation recorded:

- task state: `DISABLED`;
- authoritative final Windows `CollectorRunId`: `40393`;
- final Windows completion: `2026-10-07T13:43:56Z`;
- final Windows observation: `2026-10-07T13:43:55Z`;
- read-only evidence boundary: `2026-10-07T16:37:17.988028Z` (not an
  invented operator-disable timestamp);
- two post-retirement read-only boundaries with zero newer Windows runs and
  zero newer Windows observations.

No SQL-side task disable operation was used for the final retirement. The Mac
production preflight passed against `CologneTransitIntelligence` using
`/Library/Developer/CommandLineTools/usr/bin/python3` and `pymssql==2.3.2`,
with Keychain-backed secrets, exact Legacy7 IDs 1–7, no active SQLite runtime,
and no PostgreSQL runtime.

During the historical activation window, the generated user-local LaunchAgent
was active at
`~/Library/LaunchAgents/com.cologne-transit-intelligence.mdd-collector.plist`;
the tracked plist remains safe-by-default and disabled. Its installed SHA-256
is `051958af675218f61fca5da6def595e1b83597b5f2b8b4f46ac54f0376688f1f`.
Three normal scheduled runs completed successfully:

| RunId | Logical bucket | Target audits | HTTP attempts | Snapshots | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| `127809b0-70fa-4770-9943-664942be5fc3` | 5971301 | 7/7 | 7 | 7 | `COMPLETE` |
| `83c3f261-5bf0-484e-8de6-b6f0e0724a49` | 5971302 | 7/7 | 7 | 7 | `COMPLETE` |
| `5a7e014c-6266-421c-b402-98c4f1cb89b6` | 5971303 | 7/7 | 7 | 7 | `COMPLETE` |

No manual Heumarkt request was made. Tiered50 target materialization and
activation remain deferred, and Power BI was not changed. During that
historical window, the runtime reused the existing approved SQL login;
read-only inspection identified it as
`sa`/sysadmin, with no new login or permission grant made. A least-privilege
runtime login remains a follow-up.

## Cadence-correction addendum — pre-activation review

The three run rows above are preserved historical source evidence, not a
normal Legacy7 cadence sample. The pre-activation review found that the Mac runner expanded the
approved seven-target scope into all seven target audits on each of the three
heartbeats. The bounded oversampling window therefore has a reported shape of
21 initial attempts versus 3 historical Legacy7-equivalent attempts, an
excess of 18. The counts were subsequently confirmed read-only and recorded
in the append-only methodology evidence. No existing run, audit, attempt,
snapshot, or collection-context value was updated.

The exact user-local LaunchAgent was unloaded and disabled during the
pre-activation recovery window before further SQL activity. The tracked plist
and SQL evidence remain in place; the retired Windows collector was not
changed or re-enabled, and no manual MDD request was issued in that window.

Corrected future Mac Legacy7 audits were specified to use one SQL-selected
target per UTC five-minute bucket under `MAC_LEGACY7_PRODUCTION`, with
`SamplingTargetId`, `SamplingSlot`, `LogicalDueBucket`,
`SelectionPolicyVersion`, and collection context retained. The dedicated
`CologneTransitCollector` login and its exact object-level grants and
rollback-safe negative checks are now provisioned and recorded in
[`scripts/provision_macos_runtime_login.py`](../../scripts/provision_macos_runtime_login.py).
Tiered50 remains inactive and Power BI remains unchanged.

## Corrected Legacy7 activation follow-up — 2026-10-07

After the pre-activation gates passed, the generated user-local LaunchAgent
was activated with the tracked template still disabled, `StartInterval=300`,
and `RunAtLoad=false`. Three ordinary scheduled heartbeats completed under
`MAC_LEGACY7_PRODUCTION`:

| RunId | Bucket | Slot | SamplingTargetId | Status |
| --- | ---: | ---: | ---: | --- |
| `85d2d601-ff66-4fd1-b3e6-105a1a384478` | 5971342 | 3 | 1 | `COMPLETE` |
| `8286961f-0edc-4b64-b5ef-15ae776c1389` | 5971343 | 4 | 4 | `COMPLETE` |
| `24cb4cab-38f9-4629-bedf-6bf41c52b57a` | 5971344 | 5 | 6 | `COMPLETE` |

Each run used the live SQL selector, persisted one target audit and snapshot,
and received HTTP 200. The corrected sampling era
`MAC_LEGACY7_PRODUCTION` was recorded effective at
`2026-10-07T19:51:46.041Z`, the first corrected scheduled heartbeat. The
retired Windows task remains disabled; no manual Heumarkt request was made;
Tiered50 target materialization/cutover and Power BI changes remain deferred.

The sanitized machine-readable transition closeout is recorded in
[`legacy7-production-transition-closeout-evidence.json`](legacy7-production-transition-closeout-evidence.json).

Two interrupted pre-activation diagnostic runs were separately finalized as
`FAILED` through the existing run-finalization procedure after scheduler
restart recovery. They are excluded from the three-heartbeat success gate; no
production run remains in `RUNNING` state.

# Operations, security and disaster-recovery runbook

**Safe current state:** both collector services disabled, production acquisition stopped, historical evidence preserved. This page is **not** an activation instruction. Any restart would require a new provider/quota/storage approval.

## Actual host boundaries

- SQL Server database: `CologneTransitIntelligence` on Windows VM (historically named `DataAnalyst-VM`). Host labels are examples; do not embed personal Windows accounts in public docs.
- Python runtime: later collector implementation on macOS; exact interpreter and `pymssql` version were validated in an earlier operational window, not guaranteed current.
- LaunchAgent templates: `deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist` and `...mdd-tiered50-collector.plist` are **disabled** source examples. No collector should be installed/started just to follow the README.
- `NRWTransportDW`, PostgreSQL and SQLite research contexts are no longer active authoritative production stores. Historical SQL and migration code may remain for provenance.

## Read-only operator verification before any action

1. Inspect the Windows VM C: free bytes and SQL Server ROWS/LOG capacity. Keep units and measurement time; MDF free pages ≠ OS C: free.
2. Check SQL Server database online state, recovery model, `sys.database_files`, `DBCC CHECKDB` last reported outcome and exact snapshot/child/run counts (not only logical allocation). The [read-only final-sample query](../sql/portfolio/verify-final-retained-sample-readonly.sql) provides one preliminary check; it does not validate full foreign-key closure or backup restore.
3. Query `msdb` full-backup history and the physical backup destination; prove new media exists and is accessible from SQL Server.
4. Verify all Windows Task Scheduler and Mac launchd writers are disabled, no active claims/leases, no overlapping agent process.
5. Confirm target IDs, stage-13 panel version, provider window, quota ledger and provider reset evidence **before** any future collection request.
6. Do not assume historical JSON snapshots reflect the current DB. Record result timestamps and PASS/FAIL/NOT_PROVEN.

## Security controls

- Keep MDD credentials, SQL passwords, admin evidence and personal paths out of Git. `.env.example` is an empty-key template.
- SQL runtime previously used a dedicated `CologneTransitCollector` login with narrowly defined permissions; admin evidence used separate roles and should not be copied as raw JSON into public repos.
- Fail-closed gates around quota and production activation must not be bypassed. Run only read-only operations until a new task explicitly requires mutation.
- Principle of least privilege applies to SQL users, backup locations, Windows VM, Mac Keychain services and GitHub collaborators.
- Do not redistribute raw provider responses/sanitized samples until licensing and privacy review approves the exact fields.

## Troubleshooting playbook

| Symptom | Read-only diagnosis | Safe response |
| --- | --- | --- |
| No heartbeat | LaunchAgent/Task Scheduler state; due UTC bucket | Preserve stopped state; diagnose, do not auto-reactivate |
| Missed targets / partial success | Run vs target audit, deadline, skips and permits | Surface coverage gap; inspect execution budget |
| HTTP failures | Provider status, attempt category, request timestamp | Check provider contract; respect retry quota |
| Missing source-recorded time | `RecordedAtTime` source field/path evidence | Preserve `NULL`; no estimate-to-actual conversion |
| SQL insert failure | PK/FK, stored-procedure, permissions, log capacity | Review idempotent replay safety before retry |
| High storage growth | per-snapshot bytes, MDF/LDF/tempdb, disk reserves | Hold collector; review bounded retention plus restore readiness |

## Backup / disaster recovery

After a successful new SQL Server full backup off C:, run media inspection and a controlled test restore on a disposable database/instance, then `DBCC CHECKDB` and lineage/hash consistency checks. Do not overwrite the portfolio database or drop historical media while testing. Document RPO/RTO as **not established** unless measured by an actual recovery exercise. The last chat-provided result is a successful CHECKDB after shrink, *not* proof of a tested restore.

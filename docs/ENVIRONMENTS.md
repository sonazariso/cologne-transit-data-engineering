# Environments and Local Paths

## macOS

Role: authoritative Git working copy, VS Code/Codex editing, source control.

Planned path:

`/Users/admin/Documents/cologne-transit-intelligence/`

## Windows 11 VM

Role: host for the official Microsoft SQL Server instance and optional Power BI
tooling. The authoritative project database is
`CologneTransitIntelligence`; the SQL Server instance remains on this VM.
Future collectors and pipelines are planned as Python on macOS and connect to
SQL Server here.

Access the same macOS working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a separate Git clone on Windows under the current workflow.

## Legacy runtime and retained files

The existing collector runtime, SQLite research database path, PostgreSQL
contracts, Windows scheduled-task material, and historical realtime files remain
untouched and are marked for deferred cleanup only. S87 does not migrate,
delete, or rewrite them. `NRWTransportDW` is an existing SQL Server
`LEGACY_REFERENCE` database and must remain untouched.

## Prepared direct database path

The prepared macOS collector uses the pinned `pymssql==2.3.2` DB-API wheel and
opens short-lived direct connections to `CologneTransitIntelligence`. It does
not create a local SQLite shadow, use PostgreSQL, or route evidence through an
intermediary database. The additive `ctl.MddMac*` and `stg.MddMac*` contract is
separate from the existing Windows collector tables and is documented in
[`docs/macos-mssql-collector.md`](macos-mssql-collector.md).

The future `launchd` definition is present at
`deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist` but
is explicitly disabled. The Windows task has not been changed. Direct Mac
collection remains blocked until the current Windows runtime is identified and
the controlled cutover is separately approved.

## Secrets

API keys, connection strings containing credentials, tokens, and passwords must not be committed. The exact secret-management approach for the new collector is a later implementation decision.

The tracked `.env.example` file contains the active non-secret names for the
prepared direct adapter as well as historical placeholders. SQL Server
credentials remain local/secret and must never be committed. The launchd
artifact contains no API key or database credential.

## Collector and pipeline runtime

The collector/pipeline direction is Python on macOS with SQL Server as its
only official persistence platform. SQL Server remains on the Windows VM, and
Power BI consumes SQL Server analytical/warehouse data. The historical
Stage 14 Python/SQLite implementation remains retained research evidence; the
new direct adapter and migration are prepared, while production scheduling and
cutover remain disabled.

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

## Secrets

API keys, connection strings containing credentials, tokens, and passwords must not be committed. The exact secret-management approach for the new collector is a later implementation decision.

The tracked `.env.example` file contains historical non-secret placeholders.
PostgreSQL, SQLite, Ubuntu, and Docker values in it are not active runtime
configuration. SQL Server credentials remain local/secret and must never be
committed.

## Collector and pipeline runtime

The future collector/pipeline runtime is Python on macOS with SQL Server as its
only official persistence platform. SQL Server remains on the Windows VM, and
Power BI consumes SQL Server analytical/warehouse data. The current Stage 14
Python/SQLite implementation is retained historical research evidence;
production connection, scheduling, and cutover are not part of S87.

# Environments and Local Paths

## macOS

Role: authoritative Git working copy, VS Code/Codex editing, source control.

Planned path:

`/Users/admin/Documents/cologne-transit-intelligence/`

## Windows 11 VM

Role: host for the official Microsoft SQL Server instance and optional Power BI
tooling. The authoritative project database is
`CologneTransitIntelligence`; the SQL Server instance remains on this VM.
The active Legacy7 collector runs as Python on macOS and connects to SQL
Server here. Tiered50 remains inactive; Power BI is unchanged.

Access the same macOS working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a separate Git clone on Windows under the current workflow.

## Legacy runtime and retained files

The existing collector runtime, SQLite research database path, PostgreSQL
contracts, Windows scheduled-task material, and historical realtime files remain
untouched and are marked for deferred cleanup only. S87 does not migrate,
delete, or rewrite them. The S87 stabilization work left `NRWTransportDW`
untouched; it was later classified
`LEGACY_SAFE_TO_ARCHIVE` and was removed from the SQL Server instance on
2026-10-07 after the verified native backup and ZIP were confirmed on external
USB storage. The `.bak` and ZIP remain preserved under `CologneTransitData` as
the recovery source; `CologneTransitIntelligence` is the only project SQL
Server database now attached to the VM.

## Active direct database path

The prepared macOS collector uses the pinned `pymssql==2.3.2` DB-API wheel and
opens short-lived direct connections to `CologneTransitIntelligence`. It does
not create a local SQLite shadow, use PostgreSQL, or route evidence through an
intermediary database. The additive `ctl.MddMac*` and `stg.MddMac*` contract is
separate from the existing Windows collector tables and is documented in
[`docs/macos-mssql-collector.md`](macos-mssql-collector.md).

The tracked `launchd` definition at
`deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist` is a
safe-by-default disabled template. The active generated artifact is
`~/Library/LaunchAgents/com.cologne-transit-intelligence.mdd-collector.plist`
and invokes `/Library/Developer/CommandLineTools/usr/bin/python3` from an
owner-only runtime bundle. It uses a five-minute interval, no `RunAtLoad`, and
the approved Legacy7 cutover gates.

The explicit historical source is
`/Users/admin/cologne-transit-data/live-research/collector.sqlite3`; its
SHA-256 is
`0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc` and its
role/context are `LIVE_RESEARCH` / `LIVE_RESEARCH_SINGLE_1`. The source proof
and overlap audit are read-only. The source passed a fresh conservative
capacity gate, was imported into additive `ctl.MddImport*`/`stg.MddMac*`
lineage, and passed an exact idempotency rerun. No manual Heumarkt request was
made or required. The exact Windows task
`\Cologne Transit Realtime Collector` was manually disabled by the operator.
Read-only SQL confirmation saw zero newer Windows runs or observations across
two boundaries; the authoritative final Windows run is 40393, completed at
`2026-10-07T13:43:56Z`.

## Secrets

API keys, connection strings containing credentials, tokens, and passwords must
not be committed. The active collector stores non-secret runtime settings in
`~/Library/Application Support/CologneTransitIntelligence/runtime.json`
(`0600`, parent `0700`) and resolves `MDD_API_KEY` plus the SQL password from
macOS Keychain generic-password items. The installed plist and logs contain no
secret values.

The tracked `.env.example` file contains the active non-secret names for the
prepared direct adapter as well as historical placeholders. SQL Server
credentials remain local/secret and must never be committed. The launchd
artifact contains no API key or database credential.

## Collector and pipeline runtime

The collector/pipeline direction is Python on macOS with SQL Server as its
only official persistence platform. SQL Server remains on the Windows VM, and
Power BI consumes SQL Server analytical/warehouse data. The historical
Stage 14 Python/SQLite implementation remains retained research evidence; the
active direct adapter writes Legacy7 evidence to the additive `ctl.MddMac*` /
`stg.MddMac*` contract. Tiered50 production activation remains deferred.

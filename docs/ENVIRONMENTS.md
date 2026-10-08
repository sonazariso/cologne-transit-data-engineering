# Environments and Local Paths

## macOS

Role: authoritative Git working copy, VS Code/Codex editing, source control.

Planned path:

`/Users/admin/Documents/cologne-transit-intelligence/`

## Windows 11 VM

Role: host for the official Microsoft SQL Server instance and optional Power BI
tooling. The authoritative project database is
`CologneTransitIntelligence`; the SQL Server instance remains on this VM.
The Tiered50-only collector is implemented as Python on macOS and connects to
SQL Server here under the production context `MAC_TIERED50_PRODUCTION`. The
separate user-local Tiered50 LaunchAgent is installed with a five-minute,
no-`RunAtLoad` schedule but remains disabled because the supplied dashboard
reading does not verify provider-wide scope, freshness, reset bounds, or the
accounting model required by the quota gate. Legacy7 is retired; Power BI is
unchanged.

Access the same macOS working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a separate Git clone on Windows under the current workflow.

## Legacy runtime and retained files

The existing collector runtime, SQLite research database path, PostgreSQL
contracts, Windows scheduled-task material, and historical realtime files remain
untouched and are marked for deferred cleanup only. The tracked Mac LaunchAgent
templates remain disabled by design; the generated Legacy7 artifact is retired
and the generated Tiered50 artifact is installed but disabled.
S87 does not migrate,
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

The tracked `launchd` definitions are safe-by-default disabled templates. The
Tiered50 generated artifact is
`~/Library/LaunchAgents/com.cologne-transit-intelligence.mdd-tiered50-collector.plist`
and invokes `/Library/Developer/CommandLineTools/usr/bin/python3` from a
separate owner-only versioned runtime bundle. It uses a five-minute interval,
no `RunAtLoad`, and the approved Tiered50 gates; it remains disabled until
activation is safe.

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
Power BI consumes SQL Server analytical/warehouse data. The historical Stage 14
Python/SQLite implementation and Legacy7 SQL evidence remain retained
research/history; the prepared direct adapter writes Tiered50 evidence to the
additive `ctl.MddMac*` / `stg.MddMac*` contract. Tiered50 production activation
remains blocked because the observed provider dashboard reading is not yet a
registrable provider-wide quota baseline.

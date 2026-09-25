# Environments and Local Paths

## macOS

Role: authoritative Git working copy, VS Code/Codex editing, source control.

Planned path:

`/Users/admin/Documents/cologne-transit-intelligence/`

## Windows 11 VM

Role: Windows-only tooling when required, including SQL Server/SSMS, Windows Task Scheduler, and Power BI Desktop.

Access the same macOS working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a separate Git clone on Windows under the current workflow.

## Legacy runtime

Existing legacy SQL database, Windows scheduled task, collector runtime, and realtime history belong to the previous project generation. They must remain untouched until a later stage explicitly approves a migration/cutover action.

## Secrets

API keys, connection strings containing credentials, tokens, and passwords must not be committed. The exact secret-management approach for the new collector is a later implementation decision.

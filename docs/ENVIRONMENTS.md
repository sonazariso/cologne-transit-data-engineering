# Environments and Local Paths

## macOS

Role: authoritative Git working copy, VS Code/Codex editing, source control.

Planned path:

`/Users/admin/Documents/cologne-transit-intelligence/`

## Windows 11 VM

Role: optional legacy/Power BI tooling when separately required. The Stage 14
collector does not require this VM, Windows Task Scheduler, PowerShell, or a
Windows-specific runtime.

Access the same macOS working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a separate Git clone on Windows under the current workflow.

## Legacy runtime

Existing legacy SQL database, Windows scheduled task, collector runtime, and realtime history belong to the previous project generation. They must remain untouched until a later stage explicitly approves a migration/cutover action.

## Secrets

API keys, connection strings containing credentials, tokens, and passwords must not be committed. The exact secret-management approach for the new collector is a later implementation decision.

The tracked `.env.example` file is a non-secret historical environment
template. S78 does not use or authorize PostgreSQL/SSH deployment variables.
The live-research collector reads only inherited `MDD_API_KEY` and the
dedicated live-root/database settings; copy values to a local `.env` only when
the execution environment supplies them, and never commit the resulting file.

pgAdmin credentials are only for signing in to the pgAdmin web interface. They
do not replace the PostgreSQL username and password; pgAdmin still uses the
PostgreSQL credentials when registering a database server or running queries.

## Stage 14 collector runtime

The collector is Python and cross-platform. Its only execution contract is a
single heartbeat; no recurring scheduler is deployed. `plan`, `dry-run`, and
`simulate` use no network. `live-research` requires an explicit root/database
outside the repository and uses durable SQLite for `LIVE_RESEARCH_SINGLE_1`;
`live` remains blocked. The `.local/` SQLite file remains disposable
development state. PostgreSQL host selection, installation, and deployment
are not planned or authorized in the active roadmap.

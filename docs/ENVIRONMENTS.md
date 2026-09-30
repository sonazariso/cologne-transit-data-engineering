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

The tracked `.env.example` file is a non-secret template containing mock values
for the production server/SSH connection, PostgreSQL container, and optional
pgAdmin web login. Copy it to a local `.env`, replace every mock value, and do
not commit the resulting `.env` file. SSH keys are preferred over storing an SSH
password when the deployment setup supports them.

pgAdmin credentials are only for signing in to the pgAdmin web interface. They
do not replace the PostgreSQL username and password; pgAdmin still uses the
PostgreSQL credentials when registering a database server or running queries.

## Stage 14 collector runtime

The collector is Python and cross-platform. Its only recurring-execution
contract is the one-heartbeat command; the deployment platform is intentionally
deferred. `plan`, `dry-run`, and `simulate` use no network and no production
database. The SQLite file under `.local/` is disposable development state and
is not the future PostgreSQL runtime. PostgreSQL host selection, installation,
and production deployment are not required for this iteration.

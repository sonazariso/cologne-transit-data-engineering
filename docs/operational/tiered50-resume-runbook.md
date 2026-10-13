# Tiered50 resume runbook

This runbook resumes an already activated and materialized `TIERED50_PANEL_V1`
after a stopped LaunchAgent. It is not a second cutover. The resume operation
does not accept an effective timestamp, recreate a sampling era, rewrite target
rows, replay historical requests, or reactivate Legacy7.

## Required one-time evidence setup

The resume gate requires a fresh, sanitized, signed administrative evidence
artifact. It must identify the actual SQL instance, database, effective
administrator identity, capture time, read-only execution path, permissions,
database and volume capacity, log space, latest native full-backup history,
server-side `RESTORE VERIFYONLY ... WITH CHECKSUM`, current quota state, and
collector history. The private signing key and any SQL password remain outside
the repository and outside all evidence files.

The production resume write still requires a separate administrator SQL
connection on the Mac. Windows Authentication evidence is read-only evidence;
it is not authorization for the Mac process to append a resume event. The
preferred Mac credential path is a Keychain item:

```text
MSSQL_ADMIN_HOST=<authoritative SQL host or alias>
MSSQL_ADMIN_DATABASE=CologneTransitIntelligence
MSSQL_ADMIN_USER=<separately authorized administrator>
MSSQL_ADMIN_KEYCHAIN_SERVICE=com.cologne-transit-intelligence.mssql-admin-password
MSSQL_ADMIN_KEYCHAIN_ACCOUNT=<optional; defaults to MSSQL_ADMIN_USER>
```

The operator may use a one-process `MSSQL_ADMIN_PASSWORD` override when
necessary, but must not place it in source, tracked environment files, logs,
evidence, or review archives. The resume gate rejects the restricted
`CologneTransitCollector` login for lifecycle writes. With the secure
Keychain item available, run:

```text
python3 scripts/tiered50_production_evidence.py \
  --signing-key /operator-secure/admin-evidence-signing-key.pem
```

With no positional output argument, capture writes the stable current artifact
at `docs/operational/tiered50-production-evidence-current.json`. An optional
`MSSQL_ADMIN_EVIDENCE_PATH` override must be an absolute owner-controlled path
with that exact filename; daily filenames and arbitrary temporary locations
are rejected. Resume consumes this same path.

The administrative variables never inherit `MSSQL_*` runtime credentials. The
runtime account must remain `CologneTransitCollector` and the administrator
must be a different, separately authorized identity. The signed artifact must
contain the actual `SERVERPROPERTY('ServerName')` identity and the resume gate
compares it with the identity returned by the installed runtime connection;
matching database names or aliases alone are insufficient.

The controlled Windows Authentication alternative is
`scripts/capture_windows_sql_admin_evidence.ps1`. It writes an unsigned,
sanitized payload only after the SQL-side permission, capacity, backup,
materialization, and quota gates pass. Sign and immediately verify that
payload with `scripts/sign_admin_evidence.py`, copy only the signed artifact
to the operator-secure storage, and configure its matching public key as
`MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY`:

Run this exact command from the repository root in the existing interactive
Windows PowerShell session under the separately authorized Windows identity.
It uses the approved `DataAnalyst-VM` SQL instance and
`CologneTransitIntelligence` database; Windows Authentication supplies the
identity, so the command contains no password or secret:

```powershell
$ErrorActionPreference = "Stop"
$payload = Join-Path $env:LOCALAPPDATA "CologneTransitIntelligence\windows-admin-evidence-unsigned.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $payload) | Out-Null
& .\scripts\capture_windows_sql_admin_evidence.ps1 `
  -SqlInstance "DataAnalyst-VM" `
  -Database "CologneTransitIntelligence" `
  -UnsignedPayloadPath $payload `
  -EncryptMode "Mandatory"
```

The script first confirms that the `SqlServer` module and compatible
`Invoke-Sqlcmd` parameters (`-Encrypt` and `-TrustServerCertificate`) are
available. If that local prerequisite is absent, stop and obtain operator
authorization before installing only the current-user module with
`Install-Module -Name SqlServer -Scope CurrentUser -Repository PSGallery`.
The script does not fall back to plaintext or unverified certificate
transport. It records the live SQL session encryption result, Windows
authentication scheme, and certificate-validation method, and rejects a
connection whose actual transport is not encrypted.

The read-only evidence command above cannot create the current recovery point
required before storage changes. After measuring an approved SQL-visible
off-C destination, the same interactive Windows identity can run the narrowly
scoped backup command below. It uses Windows Authentication, requires no
password argument, rejects C:, does not use `INIT`, performs no schema/data
change, and runs `RESTORE VERIFYONLY` against the exact new backup set. The
resulting JSON is sanitized and is the only backup artifact accepted by the
reclamation and migration gates:

```powershell
$ErrorActionPreference = "Stop"
& .\scripts\execute_windows_sql_backup.ps1 `
  -SqlInstance "DataAnalyst-VM" `
  -Database "CologneTransitIntelligence" `
  -BackupPath "\\approved-server\CologneTransitIntelligence\CTI-current.bak" `
  -DestinationFreeGiB 20 `
  -Apply `
  -EvidencePath "$env:LOCALAPPDATA\CologneTransitIntelligence\tiered50-fresh-backup.json"
```

The `20` value is an example placeholder: replace it only with a fresh
measured free-space value for the approved destination. This command does not
apply the compact schema or historical migration; those remain separate,
bounded, approval-gated operations.

The Windows payload is written with explicit UTF-8 without a BOM. The Mac
parser also accepts a valid UTF-8 BOM for compatibility with older Windows
PowerShell 5.1 output while retaining normal JSON and evidence-schema
validation. The disk section measures every fixed Windows volume and binds
the SQL data and log files to measured volumes; it requires the established
5% and 1 GiB minimum usable reserve.

```text
MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY=/operator-secure/admin-evidence-public-key.pem \
python3 scripts/sign_admin_evidence.py \
  --input /operator-secure/windows-admin-evidence-unsigned.json \
  --output /operator-secure/windows-admin-evidence.json \
  --intermediate \
  --signing-key /operator-secure/admin-evidence-signing-key.pem \
  --public-key /operator-secure/admin-evidence-public-key.pem
```

On the Mac, merge that verified SQL
envelope with a fresh local host/runtime capture and sign the resulting full
operational artifact:

```text
python3 scripts/tiered50_production_evidence.py \
  --admin-evidence /operator-secure/windows-admin-evidence.json \
  --signing-key /operator-secure/admin-evidence-signing-key.pem
```

The Mac merge command overwrites the same stable current artifact. The final
signature is applied after the merged payload and current status are complete;
the Windows envelope is only an authenticated intermediate input.

The public-key verification happens before the merge; the final signature
binds the SQL and Mac evidence together. The Windows evidence workflow does
not and must not perform the SQL schema or historical lifecycle write. If no separate Mac administrator
Keychain item or one-process authorized credential is available, stop here and
leave Tiered50 stopped with `MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS`.
The final artifact must also contain a fresh Windows VM disk-capacity check;
the SQL and Mac captures do not infer that capacity from the host filesystem.
VMware guest operations are not a prerequisite for this path; the interactive
Windows capture is the authoritative source for Windows disk evidence.

## Separate schema-repair gate

The missing object repair is a separate operator decision. Production DDL
approval does not authorize Tiered50 Resume. Before any authorized repair,
present and record approval for creating only
`ctl.MddMacProductionResumeEvent` and its source-defined constraints from the
single idempotent batch in
`sql/mdd-mac-collector/create-source-faithful-persistence.sql`:

```text
python3 scripts/repair_mdd_mac_schema.py \
  --apply \
  --authorize-production-ddl
```

The command rechecks the actual SQL instance/database, backup verification,
competing collectors, and schema inventory; takes the scoped SQL application
lock; performs no general migration, SamplingEra creation, cutover repeat,
quota rewrite, ResumeEvent insert, or collector activation; and independently
verifies the installed contract through a fresh administrator connection.
Its creation result must be read from `creation_status`, not inferred from a
successful idempotent batch. An ambiguous COMMIT is recorded as
`COMMIT_OUTCOME_UNKNOWN` until reconciled and is never described as a
successful rollback.

After repair, generate fresh administrative evidence and sign it with the
operator-controlled key. The DDL approval remains distinct from the later
explicit operational authorization required by the Resume command.

## Read-only and rollback validation

Run the static validator before any live validation:

```text
python3 scripts/validate_tiered50_skip_audit_sql.py
```

The default result is `NOT_RUN`; it does not claim SQL Server execution. With
the same separately authorized administrator connection, `--live` creates
uniquely named test rows inside one transaction, checks 1, 16, 18, and 19-row
batches, retries, authoritative-audit conflicts, absence of claims, and a
simulated partial failure, then rolls everything back. It issues no MDD
request. Do not treat the validation as PASS unless the returned mode is
`REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK`.

## Safe resume command

With the LaunchAgent still stopped and the signed artifact fresh:

```text
python3 scripts/resume_tiered50_production.py \
  --apply \
  --resume-key TIERED50_PANEL_V1:RESUME:2026-10-08T16:00Z
```

The command acquires the exclusive SQL application lock
`CologneTransitIntelligence:TIERED50:RESUME` before readiness validation. It
holds that session-owned lock across immutable-state checks, the LaunchAgent
transition, and completion recording, while releasing ordinary row locks before
slow launchd work. The command takes SQL locks while checking the immutable target set,
original activation boundary, one real sampling era, current-era pointer, activation
record, historical outage, competing workers, current quota headroom, signed
administrative evidence, and the latest installed bundle. It enables the
existing versioned artifact only after every gate passes, verifies the service
is loaded, and then appends one `ctl.MddMacProductionResumeEvent` row with the
actual SQL UTC timestamp. A partial enablement is disabled and unloaded again;
the installed plist and actual launchd state must both verify stopped. An
unreadable or still-active rollback is reported as a critical operational
blocker with the original activation failure and separate rollback failures.

Immediately before enablement, the same session lock is re-asserted and the
workflow rereads SQL UTC, the runtime SQL identity, the current provider-period
or October bootstrap authorization, quota headroom and pending permits,
competing writers, signed evidence freshness, and the installed LaunchAgent
state. The recheck is repeated after the slow SQL/evidence preparation; an
expired artifact, changed quota, clock crossing of the October hard stop, lost
lock, or service that became loaded during preparation prevents enablement.
The macOS wall clock is not used as an authorization clock.

Repeating the same key is idempotent and never creates a second resume event.
If its event is already recorded while the service is stopped, the command
reports `IDEMPOTENT_ALREADY_RECORDED` with
`result_code=IDEMPOTENT_EVENT_RECORDED_SERVICE_STOPPED`, exits nonzero, and does
not silently reactivate it. The result explicitly requests a new resume
transition key and renewed readiness checks. A new explicit key may be used
only after the reason for the stop has been reviewed. A loaded LaunchAgent
from an earlier event is reported as `LOADED_NOT_VALIDATED`; it is not reported
as an `ACTIVE AND VALIDATED` production cycle. A historical `OUTAGE` remains
immutable evidence and does not by itself keep a current, successfully resumed
service in `BLOCKED` status.

## Current review state

This review did not execute Resume or any MDD request. The separate schema
repair is complete and the live SQL Skip Audit validator passed with its probe
transaction rolled back. The current sanitized administrator evidence is fresh
and its target, backup, capacity, and collector gates pass, but it remains
unsigned because the operator-controlled signing key was not supplied. Monthly
provider rollover also remains fail-closed: the exact provider reset boundary is unverified, and
the bounded calendar-month policy is implemented but requires explicit
operator approval of the global UTC-12 through UTC+14 envelope, a 30-minute
post-boundary delay, and its documented temporary collection gaps before use.
That policy deliberately loses 26 hours 30 minutes (26.5 hours) at each normal
month transition from the previous safe-period end to the next authorization;
the October-bootstrap-to-November example loses 37 hours 30 minutes.

Explicit one-time approval request (not granted): “I approve the
`CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY` policy for this shared MDD
credential, bounded by `UTC-12 through UTC+14` using `Etc/GMT+12` and
`Etc/GMT-14`, with authorization no earlier than 12:30 UTC on the first day
of each calendar month, a 30-minute post-boundary safety delay, a 26 hours
30 minutes normal intermonth collection gap (and a 37 hours 30 minutes
October-bootstrap-to-November gap), and all documented pending-permit,
SQL-outage, quota, locking, and idempotency stop conditions.” This approval
must be recorded by the
administrator-only SQL registration workflow; it does not waive the need for
provider-issued evidence or authorize resume by itself.

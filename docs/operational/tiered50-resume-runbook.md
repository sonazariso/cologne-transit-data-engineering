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
  docs/operational/tiered50-production-evidence-current-20261008.json \
  --signing-key /operator-secure/admin-evidence-signing-key.pem
```

The administrative variables never inherit `MSSQL_*` runtime credentials. The
runtime account must remain `CologneTransitCollector` and the administrator
must be a different, separately authorized identity. The signed artifact must
contain the actual `SERVERPROPERTY('ServerName')` identity and the resume gate
compares it with the identity returned by the installed runtime connection;
matching database names or aliases alone are insufficient.

The controlled Windows Authentication alternative is
`scripts/capture_windows_sql_admin_evidence.ps1`. It writes an unsigned,
sanitized payload only after the SQL-side permission, capacity, backup,
materialization, and quota gates pass. Sign that payload with
`scripts/sign_admin_evidence.py`, copy only the signed artifact to the
operator-secure storage, and configure its matching public key as
`MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY`. On the Mac, merge that verified SQL
envelope with a fresh local host/runtime capture and sign the resulting full
operational artifact:

```text
python3 scripts/tiered50_production_evidence.py \
  docs/operational/tiered50-production-evidence-current-20261008.json \
  --admin-evidence /operator-secure/windows-admin-evidence.json \
  --signing-key /operator-secure/admin-evidence-signing-key.pem
```

The public-key verification happens before the merge; the final signature
binds the SQL and Mac evidence together. This Windows workflow does not and
must not perform the SQL lifecycle write. If no separate Mac administrator
Keychain item or one-process authorized credential is available, stop here and
leave Tiered50 stopped with `MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS`.
The final artifact must also contain a fresh Windows VM disk-capacity check;
the SQL and Mac captures do not infer that capacity from the host filesystem.

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

Repeating the same key is idempotent and never creates a second resume event.
If its event is already recorded while the service is stopped, the command
reports `IDEMPOTENT_ALREADY_RECORDED` and does not silently reactivate it. A
new explicit key may be used only after the reason for the stop has been
reviewed. A historical `OUTAGE` remains
immutable evidence and does not by itself keep a current, successfully resumed
service in `BLOCKED` status.

## Current review state

This review did not execute resume, live SQL skip-audit validation, or any MDD
request. The current sanitized evidence remains blocked because no fresh,
authenticated administrative artifact is available. Monthly provider rollover
also remains fail-closed: the exact provider reset boundary is unverified, and
the bounded calendar-month policy is implemented but requires explicit
operator approval of a finite IANA timezone envelope before use.

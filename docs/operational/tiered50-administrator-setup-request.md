# One-time Tiered50 administrator setup request

This is the reusable one-time setup template. The 2026-10-09 administrator
refresh has now proven the separate read-only SQL path with the existing
operator-configured identity; Tiered50 remains stopped because the narrow
schema repair, signed fresh evidence, and separate Resume authorization gates
are still pending. No credential is being invented or stored by the collector.

Please provide one existing, separately authorized SQL identity for the
`CologneTransitIntelligence` database. It must be allowed to:

- read the effective SQL identity, SQL version/permission state, database
  files, transaction-log usage, volume capacity, backup history, and
  operational/materialization history;
- perform the server-side checksum-bearing `RESTORE VERIFYONLY` check against
  the complete latest native full-backup media set; and
- append the single idempotent `ctl.MddMacProductionResumeEvent` lifecycle row
  after all gates pass.

The preferred Mac path is an existing approved SQL login with its password
entered into the macOS Keychain UI under:

```text
service: com.cologne-transit-intelligence.mssql-admin-password
account: <the authorized SQL login>
```

Configure only these non-secret values in the operator’s process environment
or private local configuration:

```text
MSSQL_ADMIN_HOST=<authoritative SQL host or alias>
MSSQL_ADMIN_DATABASE=CologneTransitIntelligence
MSSQL_ADMIN_USER=<authorized SQL administrator>
MSSQL_ADMIN_KEYCHAIN_SERVICE=com.cologne-transit-intelligence.mssql-admin-password
MSSQL_ADMIN_KEYCHAIN_ACCOUNT=<authorized SQL login>
```

Then run the existing evidence capture and sign it with the operator-held
private key outside the repository. Do not put the password, Keychain export,
private signing key, raw logs, or production database in source, shell history,
screenshots, evidence JSON, or the review ZIP.

If no approved signing key exists, request operator approval for this one-time
setup on the operator-secure filesystem, using a restrictive umask and no
repository path:

```text
umask 077
install -d -m 700 /operator-secure/cologne-transit-intelligence
openssl genrsa -out /operator-secure/cologne-transit-intelligence/admin-evidence-signing-key.pem 3072
openssl rsa \
  -in /operator-secure/cologne-transit-intelligence/admin-evidence-signing-key.pem \
  -pubout \
  -out /operator-secure/cologne-transit-intelligence/admin-evidence-public-key.pem
chmod 600 /operator-secure/cologne-transit-intelligence/admin-evidence-signing-key.pem
chmod 644 /operator-secure/cologne-transit-intelligence/admin-evidence-public-key.pem
openssl rsa \
  -in /operator-secure/cologne-transit-intelligence/admin-evidence-signing-key.pem \
  -check -noout
```

Configure only the public path through the existing supported setting:

```text
MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY=/operator-secure/cologne-transit-intelligence/admin-evidence-public-key.pem
```

Keep the private key outside the repository, runtime bundle, ZIP, and logs;
the existing `OPENSSL_DIGEST_SHA256` workflow signs the fresh evidence and
verifies it against this public key before Resume.

The Windows Authentication capture may establish read-only administrative
evidence, but it does not authorize the Mac process to append the resume event.
The restricted `CologneTransitCollector` login must remain unchanged and must
not receive administrator, broad database, backup-verification, or lifecycle
write privileges.

After the identity is available, rerun the authenticated evidence capture,
rollback-only SQL Skip Audit validator, and the controlled resume command. If
the identity or any required permission is unavailable, leave Tiered50 stopped
and retain the exact `NOT_PROVEN` result.

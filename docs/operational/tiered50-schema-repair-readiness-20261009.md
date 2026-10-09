# Tiered50 schema-repair readiness

Captured 2026-10-09 from the read-only administrator path. No MDD request,
production DDL, collector activation, quota rewrite, or historical-row update
was performed.

## Read-only production findings

- Target identity was proven as `DataAnalyst-VM` / `CologneTransitIntelligence`
  at `172.16.192.128:1433`, using the separate `sa` administrator login.
- The authoritative source-object inventory found exactly one missing required
  object: `ctl.MddMacProductionResumeEvent`. `ctl.MddMacProductionCutover`,
  `ctl.MddMacSamplingEra`, `ctl.MddMacQuotaResetContract`, all required
  procedures, and the remaining source tables were present.
- Related columns, indexes, constraints, and SQL-expression dependencies were
  inventoried. The repair path extracts the exact idempotent ResumeEvent batch
  from `sql/mdd-mac-collector/create-source-faithful-persistence.sql`.
- Backup `VERIFYONLY ... WITH CHECKSUM`, database files, log space, and volume
  capacity checks passed. No active production collector or lease was found.
- Administrative permissions passed after recognizing effective sysadmin
  authorization and successful DMV execution. SQL Server major version was
  `17`; the newer DMV permission metadata was NULL, not a verified denial.
- Runtime health passed with database status `ONLINE` and recovery model
  `SIMPLE` after decoding SQL driver byte values.
- The administrative history query still returns SQL error 208 for the missing
  ResumeEvent table. This is the only remaining schema-drift defect.

## Repair state

`scripts/repair_mdd_mac_schema.py` is prepared and defaults to read-only
inventory. It requires both `--apply` and the explicit
`--authorize-production-ddl` flag before it can execute DDL. If authorized, it
creates only the missing table in one rollbackable transaction, verifies the
authoritative columns, constraints, indexes, and permissions, records the
created object, and is safe to repeat.

Actual production repair: **NOT APPLIED — explicit operator DDL authorization
was not supplied.**

Fresh sanitized evidence is in
[`tiered50-production-evidence-current-20261009.json`](tiered50-production-evidence-current-20261009.json).
It is read-only and unsigned because an operator-controlled private signing key
was not supplied; no signature was fabricated.

The rollback-only real SQL Skip Audit validation completed **PASS** as
`REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK` for row counts 1, 16, 18, and 19;
zero rows remained after rollback, and zero MDD requests were issued.

## Current operational blockers

- The ResumeEvent schema repair remains unapplied, so the history query remains
  unproven with error 208.
- Tiered50 remains stopped after the recorded outage; no ResumeEvent exists and
  the six-bucket production coverage gate is not proven.
- A signed post-repair evidence artifact requires the operator-controlled
  private signing key or an explicitly authorized signing setup.
- The future monthly rollover contract remains separately unapproved.

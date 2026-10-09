# Tiered50 schema-repair closeout

Captured 2026-10-09 from the separate administrator path. The scoped schema
repair is complete. It created only
`ctl.MddMacProductionResumeEvent` from the authoritative source-faithful DDL;
it did not insert a ResumeEvent, activate a collector, rewrite quota state,
repeat cutover, or issue an MDD request.

The sanitized machine-readable result is
[`tiered50-schema-repair-evidence-20261009.json`](tiered50-schema-repair-evidence-20261009.json).

## Preconditions and repair result

- The target was proven as `DataAnalyst-VM` /
  `CologneTransitIntelligence` at `172.16.192.128:1433`, using the separate
  `sa` administrator login.
- A fresh inventory found exactly one missing required source object:
  `ctl.MddMacProductionResumeEvent`. No competing production collector or
  lease was active, and the MDD request counter remained zero.
- The latest native full backup passed server-side
  `RESTORE VERIFYONLY ... WITH CHECKSUM`; database, log, and volume capacity
  gates passed.
- The exact authorized command was:

  ```text
  python3 scripts/repair_mdd_mac_schema.py --apply --authorize-production-ddl
  ```

- The operation reported `CREATED_BY_THIS_OPERATION`, committed its transaction,
  and independently reconciled the table as valid. The authoritative columns,
  identity, nullability, primary and unique keys, foreign key, check/default
  constraints, indexes, schema ownership, and administrator permissions all
  verified.
- A fresh post-repair administrator history query returned no SQL error 208.
  The table is present and contains zero ResumeEvent rows.

An earlier guarded attempt rolled back during schema verification; the final
authorized invocation succeeded after the existing validator was corrected for
SQL Server driver byte-valued identity metadata. No object or row from the
rolled-back attempt remained.

## Preservation and validation

The seven historical target IDs remain intact, the 43 Tiered50 targets remain
IDs 8–50, and the controlled before/after administrator evidence matched for
the existing materialization, cutover, quota, Legacy7, and Tiered50 history.
The repair batch was invariant-checked to create only the approved table.

The real SQL Server Skip Audit validator passed in
`REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK` mode for row counts 1, 16, 18, and
19. Zero rows remained after rollback, historical production audit rows were
unchanged, and zero MDD requests were issued. The relevant regression set
passed 95 tests.

## Current operational gates

The fresh administrator evidence is the stable current artifact
[`tiered50-production-evidence-current.json`](tiered50-production-evidence-current.json).
Its administrator, backup, capacity, and collector gates passed, but the
artifact is intentionally `UNSIGNED` because the operator-controlled signing
key was not supplied. The read-only runtime permission inspection passed all
34 positive matrix permissions and denied every prohibited privilege. Windows
VM disk capacity remains `NOT_PROVEN` because VMware guest operations require
the running VM's encryption password; it was not inferred from Mac or SQL
measurements. The exact one-time key setup and public-key configuration are documented in
[`tiered50-administrator-setup-request.md`](tiered50-administrator-setup-request.md).

Tiered50 remains stopped, Legacy7 remains retired, zero stations are
collecting, and no Resume operation was executed. Schema repair approval does
not authorize Resume; signed evidence, the separate operational authorization,
and the remaining rollover/coverage gates are still required. Legacy7 and
Power BI were not changed.

The current status is therefore **BLOCKED — TIERED50 STOPPED; LEGACY7 RETIRED**.

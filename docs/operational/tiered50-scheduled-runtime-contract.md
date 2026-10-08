# Tiered50 scheduled runtime contract

The single Tiered50 LaunchAgent is wall-clock aligned with twelve local
`StartCalendarInterval` minute entries: `00, 05, 10, ..., 55`. `RunAtLoad` and
catch-up execution remain disabled, and the Tiered50 plist does not override
launchd's own throttling. The runner records the expected UTC calendar
boundary, Python module/process entry, main-function entry, preflight start and
completion, settings initialization, Collector construction completion, and
the dispatcher entry captured inside `Collector.run_once()`. Python process
entry is explicitly not treated as the exact launchd trigger; the latter is
`NOT_INDEPENDENTLY_OBSERVED` unless an independent launchd trace exists.

The logical bucket is preserved from the observed process timestamp, while the
execution budget is established from the current dispatcher wall clock and the
absolute UTC bucket end. Startup, credential/configuration, schema, SQL
initialization, quota recovery, settings, and Collector construction therefore
consume the same heartbeat budget; constructing a later budget cannot reopen an
expired bucket. Monotonic elapsed time is retained for duration measurement,
and wall-clock remaining time is monotone non-increasing across clock jumps.
The runner derives target identity from UTC, so local DST changes do not change
the logical bucket; durable dispatcher and target claims remain the duplicate
and overlap protection.

Each target is authorized immediately before its request. Offline simulations
may inject a deterministic timestamp, but the live runner rejects `--at` and
never resolves production credentials for an injected timestamp. Live SQL
authorization uses the current SQL Server UTC clock and validates the active
provider period or bounded October bootstrap at that instant. The permit is
checked against SQL UTC again immediately before transmission; a failed or
expired check releases it without a request. A request that began before the
bucket boundary but completes after it is retained and labeled as an in-flight
overrun.

When the remaining budget cannot accommodate another request, pending targets
are converted into one bounded, transactional skip-audit batch. The batch
retains run, target, bucket, skip reason, deadline classification, and audit
timestamps, but deliberately creates no target claims. SQLite and SQL Server
paths are idempotent; terminal audit rows are not overwritten by an ambiguous
retry. A missing or failed audit batch keeps the run in coverage-gap status.
The SQL Server path binds all 28 audit columns and parameters, rejects more
than 75 rows in one statement, and uses an insert-only `HOLDLOCK` merge under
the 2,100-parameter limit.

Transport accounting keeps proven pre-network failures, confirmed responses,
transmitted requests without responses, and ambiguous live open/read/close
exceptions distinct. Only proven pre-network outcomes release a permit. An
ambiguous live exception retains conservative quota protection without
inventing physical transmission or an HTTP response; attempt-row persistence
occurs before separate permit finalization, so a persistence failure leaves
the permit pending for crash recovery.

Timing evidence also records the first winning target claim, first HTTP
transmission, network completion, parsing, HTTP-to-snapshot persistence gap,
target-audit finalization, and run finalization. The fixed configuration is
mathematically unable to guarantee worst-case 19-target coverage: ten
30-second request batches require 300 seconds before the five-second safety
margin and all SQL/parsing/audit overhead. Percentiles are emitted only when
the evidence series meets the analysis tool's minimum sample count; the two
historical HTTP requests are not used to claim p50/p95/p99.

Run completion has two independent meanings:

- `dispatcher_lifecycle_status` records whether bookkeeping completed.
- `coverage` records due, succeeded, failed, skipped, duplicate, and
  unaccounted targets. A skipped target or deadline overrun produces
  `COMPLETED_WITH_COVERAGE_GAP`; it cannot be reported as healthy `COMPLETE`.

The provider's future reset timezone and exact boundary remain unverified. The
October bootstrap hard stop is unchanged, and automatic future-month
authorization remains fail-closed until provider evidence or a separately
approved conservative policy is registered through the existing SQL workflow.
Production activation also requires an explicitly configured separate
`MSSQL_ADMIN_HOST/DATABASE/USER/PASSWORD` identity whose effective login and
version-appropriate DMV, backup, and operational-history permissions are proven. The
current capture has no such identity, so capacity, backup, and historical
query failures remain `NOT_PROVEN` and Tiered50 remains stopped.

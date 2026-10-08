# Tiered50 scheduled runtime contract

The single Tiered50 LaunchAgent is wall-clock aligned with twelve local
`StartCalendarInterval` minute entries: `00, 05, 10, ..., 55`. `RunAtLoad` and
catch-up execution remain disabled. The runner derives the logical bucket from
UTC, so local DST changes do not change target identity; durable dispatcher and
target claims remain the duplicate and overlap protection.

Each target is authorized immediately before its request. Offline simulations
may inject a deterministic timestamp. Live SQL authorization uses the current
SQL Server UTC clock and validates the active provider period or bounded
October bootstrap at that instant. A permit that expires before transmission
is released without a request. A request that began before the bucket boundary
but completes after it is retained and labeled as an in-flight overrun.

Run completion has two independent meanings:

- `dispatcher_lifecycle_status` records whether bookkeeping completed.
- `coverage` records due, succeeded, failed, skipped, duplicate, and
  unaccounted targets. A skipped target or deadline overrun produces
  `COMPLETED_WITH_COVERAGE_GAP`; it cannot be reported as healthy `COMPLETE`.

The provider's future reset timezone and exact boundary remain unverified. The
October bootstrap hard stop is unchanged, and automatic future-month
authorization remains fail-closed until provider evidence or a separately
approved conservative policy is registered through the existing SQL workflow.

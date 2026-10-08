# Tiered50 deployment validation report

Status: **BLOCKED — LEGACY7 PRESERVED**

The approved `TIERED50_PANEL_V1` configuration is validated offline and remains inactive. The implementation now contains a separately guarded `MAC_TIERED50_PRODUCTION` runtime path, additive materialization SQL, a disabled Tiered50 LaunchAgent template, stable production target identity handling, and read-only preflight checks.

Offline validation passed for the 50-target manifest, the exact six-phase schedule `[19, 18, 18, 18, 19, 18]`, 110 nominal requests per 30-minute cycle, 220 per hour, concurrency 2, max attempts 2, UTC bucket selection, no catch-up, fail-closed credential gates, claim uniqueness, and source-faithful parser-to-SQL row construction. No MDD requests or SQL writes were issued by this validation.

The repository suite passed five consecutive times at 243 tests per run with `ResourceWarning` treated as an error. Compilation, JSON validation, migration checks, Stage 14/15 readiness checks, and `git diff --check` also passed.

The additive SQL is statically reconciled to 43 new target rows with identities 8–50, while historical identities 1–7 remain untouched. It was not applied to the live database, so Tiered50 is not active and zero Tiered50 stations are collecting. The latest available Legacy7 evidence supports seven Legacy7 stations collecting.

The available Legacy7 execution evidence contains three consecutive persisted UTC buckets (15 minutes of bucket span). It does not prove a complete 30-minute stable interval, uninterrupted launchd execution, VMware Fusion availability, Windows VM availability, SQL Server continuity beyond the observed successful writes, database capacity, backup/recovery readiness, or shared-credential quota reconciliation. Missing-bucket causes are not inferred.

The Tiered50 LaunchAgent is disabled, Windows collectors remain retired, Power BI and analytics permissions are unchanged, and no manual Heumarkt request was issued. A controlled cutover and one complete live Tiered50 cycle remain outstanding.

The review artifact is `/Users/admin/Documents/cologne-transit-intelligence-review.zip`; its final SHA-256, byte size, and member count are returned with the handoff.

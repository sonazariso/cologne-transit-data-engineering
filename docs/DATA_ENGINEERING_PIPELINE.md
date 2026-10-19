# End-to-end ingestion and persistence — implementation walkthrough

The pipeline below is an **implemented bounded engineering pathway**, not a claim of continuously operating service.

## Extract: planned targets and attributed attempts

`config/tiered50_runtime_manifest.json` supplies immutable station identities and approved tier parameters. A schedule derives due station/phase from a **UTC five-minute logical bucket**, avoiding DST/local-time selection drift. The macOS LaunchAgent fires at local wall-clock 00/05/…/55 (best-effort wake-up); the runner does **not** reclassify process startup as launchd's exact trigger. Dispatcher and target claims protect against overlapping processes. Startup and SQL checks consume the real same-bucket time budget; an expired bucket cannot be reopened by retries.

Before each HTTP transmission, a live permit must pass the shared provider-quota gate and SQL UTC expiration check. An unexpired budget is necessary but not sufficient: each request also needs a due target and a valid runtime grant. One logical due target may generate 0 attempts (skipped), 1 attempt, or at most 2 attempts when an approved transient retry occurs. Attempt outcome is recorded even if persistence later fails. Confirmed pre-network failures and ambiguous post-transmission exceptions are treated differently for quota accounting.

## Parse: preserving source time and row multiplicity

`trias.py` parses the returned TRIAS response; `domain.py` represents source occurrences, context and identity status. Preserve every distinct call/timing instance and nested source path. Do not collapse estimated timestamps across repeated observations or lose provenance when multiple service-section/attribute values occur.

**Timing fields are different facts:**

- `TimetabledTime`: scheduled/published provider event time.
- `EstimatedTime`: provider prediction (may change on successive polls).
- `RecordedAtTime`: source-recorded actual timestamp **only if present**; many captured observations had none.
- `ObservedAtUtc`: when the collector observed the provider response; never an arrival/departure time.

A missing field stays `NULL`. Avoid inferred cancellation, invented zero delay, inferred Situation root cause, or assuming two journey IDs are equivalent across feeds without evidence.

## Load: SQL Server and recorded lineage

The schema and stored-procedure contract is in [`sql/mdd-mac-collector/create-source-faithful-persistence.sql`](../sql/mdd-mac-collector/create-source-faithful-persistence.sql). Historical SQL Server implementation contains `ctl.MddMacCollectorRun`, `ctl.MddMacTargetAudit`, `ctl.MddMacHttpAttempt`, and `stg.MddMacSourceSnapshot`; the `stg` child families are:

| Family | Grain / record meaning |
| --- | --- |
| `MddMacCallObservation` | source stop-call occurrence within a snapshot |
| `MddMacTimingObservation` | planned/predicted/recorded timing occurrence with context and source path |
| `MddMacSituationEvidence` | provider Situation evidence occurrence, not verified cause |
| `MddMacServiceSectionEvidence` | source service-section/route representation occurrence |
| `MddMacAttributeEvidence` | source attribute/GeneralAttribute occurrence |
| `MddMacResponseError` | source error occurrence |
| `MddMacParserGap` | parser unknown/missing/unhandled-source evidence occurrence |

`stg.uspPersistMddMacSnapshot` is the transactional persistence boundary used by the SQL adapter. Keep `RunId → AuditId → SnapshotId → child` joins explicit, and prove cardinality after any migration. Proven SQL imports from a historical SQLite research database are **not** a separate active production database.

## Validate: acquisition is not coverage

Stage 8 recorded **343 successful HTTP responses** in a bounded Köln Hbf experiment; Stage 12 tested all 50 station refs for response compatibility; selected Legacy7 buckets persisted successful scheduled results. Stage 14 materialized a 50-target configuration, but the first scheduled Tiered50 heartbeat ended in coverage failure with the collector disabled. Do not compute a continuous success rate from these unrelated windows.

Pipeline acceptance for a bounded run should check all due-target identities; no unaccounted targets; HTTP attempts associated with the correct RunId/AuditId; snapshot-to-child completeness; explicit parser gaps; exact provider-time field retention; SQL PK/FK integrity; and quota ledger accounting. See [Data Quality](DATA-QUALITY-AND-OBSERVABILITY.md) and [Verification](VALIDATION-RESULTS.md).

## Historical replay and idempotency

Offline parsers, synthetic fixtures, SQL reconciliation and historical data import scripts can reproduce **transforms and recorded persistence**; do not attempt to replay past live arrivals by calling MDD today. Operational idempotency applies to run/audit/claim and persisted source identities, not a magical exactly-once upstream network transport guarantee.

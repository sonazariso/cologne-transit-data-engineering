# Tiered50 Storage Optimization V2 — normalized evidence GZIP

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Status:** OFFLINE PATCH ONLY — not deployed to the SQL Server.

## Evidence from the supplied 200-row samples (12 Oct 2026)

- `TimingObservation.SourcePathsJson`: **3,761.4 bytes/row** on average.
- `TimingObservation.SourceEventIdentityBasisJson`: **638.3 bytes/row**.
- Five independent Timing source-path columns together: **1,289.0 bytes/row**.
- `CallObservation.SourceEventJson`: **3,495.5 bytes/row**.
- `CallObservation.SourcePathsJson`: **1,669.7 bytes/row**.
- Sampled fields total 6,117.5 bytes/Timing row and 5,891.6 bytes/Call row. These do not represent full on-disk row size, indexes, or SQL allocation.

## Implementation

1. Apply Storage V1 first on an isolated restored SQL Server copy. V2 explicitly checks for the V1 schema and procedure.
2. New nullable `VARBINARY(MAX)` columns store `COMPRESS(NVARCHAR(MAX))` for four heavy JSON fields. Future rows store **only** the gzip form for these fields. Original fields remain NULL on new compressed rows; older plain rows are untouched.
3. The source-compatible `stg.uspPersistMddMacSnapshot` still accepts exactly the same JSON parameters from the Python collector and preserves run finalization and all non-JSON values.
4. `stg.vMddMacCallObservationJson`, `stg.vMddMacTimingObservationJson`, and the V1 Snapshot view expose the restored NVARCHAR text. Reporting clients must use these views for the affected text fields.
5. No row backfill, DROP, shrink, index rebuild, retention change, or live MDD call is part of V2.

## Release gates — do not skip

- The Windows SQL database currently has about 27.7 GiB free; existing October observations exceed the Oct 7 backup coverage. First verify a recovery point **covering current history** using approved backup/recovery procedures; this package does not create a backup.
- Check that the collector is unloaded, no active SQL lease or competing writer exists, and deployment SQL matches the current installed stored procedure exactly. The drift gate is structural, not cryptographic.
- Execute the V1 and then V2 migration only on a **restored copy** and validate compression/decompression of non-ASCII strings and the exact audit, call, and timing counts.
- Validate app read paths: old physical JSON columns become NULL for new rows by design, and reports reading them directly must migrate to views.
- Measure a representative *SQL* compression ratio, write latency, and file growth; 200-row source-length samples **do not** prove a compression ratio. Current MDF will not shrink merely because future rows are compressed.
- Timing's five separate source-path strings still consume significant space. More optimization (deduplication, content-addressed evidence store, page compression estimation) and a realistic one-month capacity/backup plan remain required.

## Testing

```bash
PYTHONPATH=src python3 scripts/validate_snapshot_gzip_storage_v1.py
PYTHONPATH=src python3 scripts/validate_observation_gzip_storage_v2.py
```

Both tests are offline static/source-level checks, not SQL Server execution evidence.

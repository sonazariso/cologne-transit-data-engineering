/*
    SNAPSHOT GZIP V1 — GUARDED ONE-SNAPSHOT RECOVERY

    The source-controlled defaults are read-only.  The first result set is an
    exact SQL Server COMPRESS measurement for every eligible plaintext
    snapshot; it is payload-byte evidence, not a claim about MDF allocation.

    A reviewed working copy may set @Apply = 1 only after the isolated-lab
    capacity measurement, backup/readback checks, and the live C: safety-floor
    preflight are complete.  @ApproveOriginalPhysicalRepresentationChange is
    deliberately separate: applying this operation changes the physical row
    from SnapshotJson to SnapshotJsonGzip while the lossless view preserves the
    decompressed source text.  The operation is one snapshot per transaction
    and is resumable because only the remaining plaintext rows are selected.

    This file never creates a backup, never shrinks a file, never drops a
    table, and never starts or restarts the collector.

    Physical contract: exactly one of SnapshotJson and SnapshotJsonGzip is
    non-NULL in the base row.  Logical contract: consumers read the original
    SnapshotJson value through stg.vMddMacSourceSnapshotJson.  A NULL physical
    SnapshotJson after conversion is therefore not evidence that the logical
    source payload was NULL.  The byte, hash, empty-value, SnapshotId, and
    audit-lineage checks below keep those contracts separate.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

/* Never join an operator's pre-existing transaction. */
IF @@TRANCOUNT <> 0
    THROW 51109, 'An existing transaction is present; refusing the backfill.', 1;

DECLARE @Apply BIT = 0;  -- CHANGE only in a separately approved working copy.
DECLARE @ApproveOriginalPhysicalRepresentationChange BIT = 0;
DECLARE @BackupCoverageConfirmed BIT = 0;
DECLARE @ReadbackCompatibilityConfirmed BIT = 0;
DECLARE @PeakPerSnapshotCapacityConfirmed BIT = 0;
DECLARE @SafetyFloorConfirmed BIT = 0;
DECLARE @ApprovalReference nvarchar(200) = N'';
DECLARE @VerifiedBackupEvidenceLabel nvarchar(400) =
    N'CologneTransitIntelligence_PreCompaction_20261014_162324_90D92CE6.bak';

/* A one-row transaction is the recovery and log-spike bound. */
DECLARE @MaxRows INT = 1;

/* These values must come from the isolated lab measurement; NULL is blocked. */
DECLARE @MeasuredPeakTransactionLogBytes BIGINT = NULL;
DECLARE @MeasuredPeakRollbackBytes BIGINT = NULL;
DECLARE @MeasuredPeakDataAllocationBytes BIGINT = NULL;
DECLARE @MeasuredPeakTempdbAllocationBytes BIGINT = NULL;
DECLARE @MeasuredAutogrowthReserveBytes BIGINT = NULL;
DECLARE @MeasuredExternalReserveBytes BIGINT = NULL;
DECLARE @CapacityMeasurementCapturedAtUtc DATETIME2(7) = NULL;
DECLARE @CapacityEvidenceMaxAgeMinutes INT = 60;

/* These values must come from the separate, fresh read-only production audit.
   The source default is intentionally blocked; the write session re-measures
   the same live state and compares it to this independently captured record. */
DECLARE @IndependentLivePreflightConfirmed BIT = 0;
DECLARE @IndependentLivePreflightEvidenceReference NVARCHAR(400) = N'';
DECLARE @IndependentLivePreflightCapturedAtUtc DATETIME2(7) = NULL;
DECLARE @IndependentLivePreflightMaxAgeMinutes INT = 15;
DECLARE @IndependentCFreeBytes BIGINT = NULL;
DECLARE @IndependentMdfAllocatedPages BIGINT = NULL;
DECLARE @IndependentMdfUsedPages BIGINT = NULL;
DECLARE @IndependentMdfFreePages BIGINT = NULL;
DECLARE @IndependentLdfAllocatedBytes BIGINT = NULL;
DECLARE @IndependentLdfUsedBytes BIGINT = NULL;
DECLARE @IndependentLdfGrowthBytes BIGINT = NULL;
DECLARE @IndependentLdfMaxSizePages BIGINT = NULL;
DECLARE @IndependentTempdbFreePages BIGINT = NULL;
DECLARE @IndependentTempdbGrowthBytes BIGINT = NULL;
DECLARE @IndependentTempdbMaxSizePages BIGINT = NULL;
DECLARE @IndependentActiveSqlSessions BIGINT = NULL;
DECLARE @IndependentActiveSqlRequests BIGINT = NULL;
DECLARE @IndependentActiveCollectorSessions BIGINT = NULL;
DECLARE @IndependentActiveCollectorRuns BIGINT = NULL;
DECLARE @IndependentActiveProductionLeases BIGINT = NULL;
DECLARE @IndependentActiveUserTransactions BIGINT = NULL;

DECLARE @SafetyFloorBytes BIGINT = CONVERT(BIGINT, 12) * 1024 * 1024 * 1024;
DECLARE @LockResult INT;
DECLARE @Migrated INT = 0;

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
   OR COL_LENGTH(N'stg.MddMacSourceSnapshot', N'SnapshotJsonGzip') IS NULL
    THROW 51100, 'Snapshot GZIP migration is not installed.', 1;
IF OBJECT_ID(N'stg.vMddMacSourceSnapshotJson', N'V') IS NULL
    THROW 51105, 'Lossless SnapshotJson readback view is not installed.', 1;
IF @MaxRows <> 1
    THROW 51101, 'Snapshot GZIP recovery is limited to exactly one row per transaction.', 1;

IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot
    WHERE (SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL)
       OR (SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NOT NULL)
)
    THROW 51108, 'SnapshotJson physical representations violate the XOR contract.', 1;

/*
    Exact logical payload measurement.  DATALENGTH(NVARCHAR(MAX)) is the
    original UTF-16LE payload size and DATALENGTH(COMPRESS(...)) is the exact
    VARBINARY payload SQL Server would store.  No row is modified here.
*/
SELECT
    N'capacity.snapshot_gzip_exact_savings' AS statement_id,
    COUNT_BIG(*) AS total_snapshot_rows,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END))
        AS eligible_plaintext_rows,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END))
        AS existing_gzip_rows,
    SUM(CONVERT(BIGINT, DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                       THEN SnapshotJson END))) AS plaintext_utf16_bytes,
    SUM(CONVERT(BIGINT, DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                       THEN COMPRESS(SnapshotJson) END))) AS compressed_payload_bytes,
    SUM(CONVERT(BIGINT, DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                       THEN SnapshotJson END)))
      - SUM(CONVERT(BIGINT, DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                            THEN COMPRESS(SnapshotJson) END))) AS exact_payload_savings_bytes,
    CONVERT(DECIMAL(19, 3),
        (
            SUM(CONVERT(DECIMAL(28, 3), DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                                       THEN SnapshotJson END)))
          - SUM(CONVERT(DECIMAL(28, 3), DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                                       THEN COMPRESS(SnapshotJson) END)))
        ) / 1073741824.0) AS exact_payload_savings_gib,
    CONVERT(DECIMAL(19, 3),
        (
            SUM(CONVERT(DECIMAL(28, 3), DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                                       THEN COMPRESS(SnapshotJson) END)))
            * 100.0
            / NULLIF(SUM(CONVERT(DECIMAL(28, 3), DATALENGTH(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                                                  THEN SnapshotJson END))), 0)
        )) AS compressed_payload_percent_of_plaintext,
    N'PAYLOAD_ONLY; PHYSICAL_MDF_ALLOCATION_REQUIRES_LAB_MEASUREMENT' AS measurement_basis
FROM stg.MddMacSourceSnapshot;

/* A deterministic detail ledger lets the operator identify the largest
   one-row log/allocation cases without exposing the source text. */
SELECT
    N'capacity.snapshot_gzip_exact_savings_detail' AS statement_id,
    SnapshotId,
    CONVERT(BIGINT, DATALENGTH(SnapshotJson)) AS plaintext_utf16_bytes,
    CONVERT(BIGINT, DATALENGTH(COMPRESS(SnapshotJson))) AS compressed_payload_bytes,
    CONVERT(BIGINT, DATALENGTH(SnapshotJson))
        - CONVERT(BIGINT, DATALENGTH(COMPRESS(SnapshotJson))) AS exact_payload_savings_bytes
FROM stg.MddMacSourceSnapshot
WHERE SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
ORDER BY SnapshotId;

/* Current read-only physical preflight is returned even in dry-run mode. */
DECLARE @CurrentCFreeBytes BIGINT;
DECLARE @CurrentLogTotalBytes BIGINT;
DECLARE @CurrentLogUsedBytes BIGINT;
DECLARE @CurrentTempdbAllocatedBytes BIGINT;
DECLARE @CurrentTempdbUsedBytes BIGINT;
DECLARE @CurrentDataAllocatedBytes BIGINT;
DECLARE @CurrentDataUsedBytes BIGINT;
DECLARE @CurrentMdfAllocatedPages BIGINT;
DECLARE @CurrentMdfUsedPages BIGINT;
DECLARE @CurrentMdfFreePages BIGINT;
DECLARE @CurrentLogGrowthBytes BIGINT;
DECLARE @CurrentLogMaxSizePages BIGINT;
DECLARE @CurrentLogHasPercentGrowth BIT;
DECLARE @CurrentTempdbGrowthBytes BIGINT;
DECLARE @CurrentTempdbMaxSizePages BIGINT;
DECLARE @CurrentTempdbHasPercentGrowth BIT;
DECLARE @CurrentActiveSqlSessions BIGINT;
DECLARE @CurrentActiveSqlRequests BIGINT;
DECLARE @CurrentActiveCollectorSessions BIGINT;
DECLARE @CurrentActiveUserTransactions BIGINT;
DECLARE @CurrentActiveCollectorRuns BIGINT;
DECLARE @CurrentActiveProductionLeases BIGINT;
DECLARE @ProjectedWorstCaseCFreeBytes BIGINT;
DECLARE @MeasuredPeakPhysicalCReserveBytes BIGINT;

SELECT @CurrentCFreeBytes = MIN(volume_available_bytes)
FROM
(
    SELECT CONVERT(BIGINT, vs.available_bytes) AS volume_available_bytes
    FROM sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
    UNION ALL
    SELECT CONVERT(BIGINT, vs.available_bytes)
    FROM tempdb.sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
) AS c_volume;

SELECT
    @CurrentMdfAllocatedPages = COALESCE(SUM(CONVERT(BIGINT, df.size)), 0),
    @CurrentMdfUsedPages = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed'))), 0)
FROM sys.database_files AS df
WHERE df.type = 0;
SET @CurrentMdfFreePages = @CurrentMdfAllocatedPages - @CurrentMdfUsedPages;
SET @CurrentDataAllocatedBytes = @CurrentMdfAllocatedPages * CONVERT(BIGINT, 8192);
SET @CurrentDataUsedBytes = @CurrentMdfUsedPages * CONVERT(BIGINT, 8192);

SELECT
    @CurrentLogTotalBytes = CONVERT(BIGINT, total_log_size_in_bytes),
    @CurrentLogUsedBytes = CONVERT(BIGINT, used_log_space_in_bytes)
FROM sys.dm_db_log_space_usage;

SELECT
    @CurrentLogGrowthBytes = COALESCE(SUM(CASE WHEN df.is_percent_growth = 0
        THEN CONVERT(BIGINT, df.growth) * CONVERT(BIGINT, 8192) ELSE 0 END), 0),
    @CurrentLogMaxSizePages = CASE WHEN MAX(CASE WHEN df.max_size < 0 THEN 1 ELSE 0 END) = 1
        THEN CONVERT(BIGINT, -1) ELSE COALESCE(SUM(CONVERT(BIGINT, df.max_size)), 0) END,
    @CurrentLogHasPercentGrowth = CONVERT(BIT, MAX(CONVERT(INT, df.is_percent_growth)))
FROM sys.database_files AS df
WHERE df.type = 1;

SELECT
    @CurrentTempdbAllocatedBytes = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
    @CurrentTempdbUsedBytes = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                            * CONVERT(BIGINT, 8192), 0),
    @CurrentTempdbGrowthBytes = COALESCE(SUM(CASE WHEN df.is_percent_growth = 0
        THEN CONVERT(BIGINT, df.growth) * CONVERT(BIGINT, 8192) ELSE 0 END), 0),
    @CurrentTempdbMaxSizePages = CASE WHEN MAX(CASE WHEN df.max_size < 0 THEN 1 ELSE 0 END) = 1
        THEN CONVERT(BIGINT, -1) ELSE COALESCE(SUM(CONVERT(BIGINT, df.max_size)), 0) END,
    @CurrentTempdbHasPercentGrowth = CONVERT(BIT, MAX(CONVERT(INT, df.is_percent_growth)))
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT
    @CurrentActiveSqlSessions = COUNT_BIG(*),
    @CurrentActiveSqlRequests = COALESCE(SUM(CASE WHEN r.session_id IS NOT NULL THEN CONVERT(BIGINT, 1) ELSE 0 END), 0),
    @CurrentActiveCollectorSessions = COALESCE(SUM(CASE WHEN LOWER(COALESCE(s.program_name, N'')) LIKE N'%cologne%transit%'
        OR LOWER(COALESCE(s.program_name, N'')) LIKE N'%mdd%collector%'
        OR LOWER(COALESCE(s.host_name, N'')) LIKE N'%mdd%collector%'
        THEN CONVERT(BIGINT, 1) ELSE 0 END), 0)
FROM sys.dm_exec_sessions AS s
LEFT JOIN sys.dm_exec_requests AS r ON r.session_id = s.session_id
    AND r.database_id = DB_ID()
WHERE s.session_id <> @@SPID
  AND s.is_user_process = 1
  AND (s.database_id = DB_ID() OR r.session_id IS NOT NULL);

SELECT @CurrentActiveUserTransactions = COUNT_BIG(*)
FROM sys.dm_tran_session_transactions AS st
INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
WHERE st.session_id <> @@SPID AND es.is_user_process = 1;

SELECT @CurrentActiveCollectorRuns = COUNT_BIG(*)
FROM ctl.MddMacCollectorRun
WHERE Status IN (N'RUNNING', N'CLAIMED')
  AND CollectionContext IN (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION');

SELECT @CurrentActiveProductionLeases = COUNT_BIG(*)
FROM ctl.MddMacProductionLease
WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE';

SELECT
    N'capacity.snapshot_gzip_preflight' AS statement_id,
    @CurrentCFreeBytes AS current_windows_c_free_bytes,
    @SafetyFloorBytes AS required_windows_c_floor_bytes,
    @CurrentDataAllocatedBytes AS current_data_file_allocated_bytes,
    @CurrentDataUsedBytes AS current_data_file_used_bytes,
    @CurrentDataAllocatedBytes - @CurrentDataUsedBytes AS current_data_file_internal_free_bytes,
    @CurrentMdfAllocatedPages AS current_mdf_allocated_pages,
    @CurrentMdfUsedPages AS current_mdf_used_pages,
    @CurrentMdfFreePages AS current_mdf_free_pages,
    @CurrentLogTotalBytes AS current_log_allocated_bytes,
    @CurrentLogUsedBytes AS current_log_used_bytes,
    @CurrentLogTotalBytes - @CurrentLogUsedBytes AS current_log_free_bytes,
    @CurrentLogGrowthBytes AS current_log_fixed_growth_bytes,
    @CurrentLogMaxSizePages AS current_log_max_size_pages,
    @CurrentLogHasPercentGrowth AS current_log_has_percent_growth,
    @CurrentTempdbAllocatedBytes AS current_tempdb_allocated_bytes,
    @CurrentTempdbUsedBytes AS current_tempdb_used_bytes,
    @CurrentTempdbAllocatedBytes - @CurrentTempdbUsedBytes AS current_tempdb_free_bytes,
    @CurrentTempdbGrowthBytes AS current_tempdb_fixed_growth_bytes,
    @CurrentTempdbMaxSizePages AS current_tempdb_max_size_pages,
    @CurrentTempdbHasPercentGrowth AS current_tempdb_has_percent_growth,
    @CurrentActiveSqlSessions AS current_active_sql_sessions,
    @CurrentActiveSqlRequests AS current_active_sql_requests,
    @CurrentActiveCollectorSessions AS current_active_collector_sessions,
    @CurrentActiveUserTransactions AS current_active_user_transactions,
    @CurrentActiveCollectorRuns AS current_active_collector_runs,
    @CurrentActiveProductionLeases AS current_active_production_leases,
    @IndependentLivePreflightConfirmed AS independent_live_preflight_confirmed,
    @IndependentLivePreflightEvidenceReference AS independent_live_preflight_evidence_reference,
    @IndependentLivePreflightCapturedAtUtc AS independent_live_preflight_captured_at_utc,
    CASE WHEN @CurrentCFreeBytes >= @SafetyFloorBytes THEN N'PASS' ELSE N'BLOCKED' END AS safety_floor_status,
    N'REQUIRES_INDEPENDENT_LIVE_PREFLIGHT_AND_MEASURED_PER_ROW_UPDATE_ROLLBACK_LOG_AUTOGROWTH_RESERVES_BEFORE_APPLY' AS capacity_status;

IF @Apply = 0
BEGIN
    PRINT 'DRY RUN ONLY: NO SNAPSHOTS MODIFIED';
    RETURN;
END;

/* Every write gate is explicit and independently reviewable. */
IF @ApproveOriginalPhysicalRepresentationChange <> 1
    THROW 51120, 'Explicit approval to replace the physical plaintext representation is required.', 1;
IF @BackupCoverageConfirmed <> 1 OR NULLIF(@VerifiedBackupEvidenceLabel, N'') IS NULL
    THROW 51121, 'Existing verified backup coverage is not confirmed.', 1;
IF @ReadbackCompatibilityConfirmed <> 1
    THROW 51122, 'Existing lossless readback compatibility is not confirmed.', 1;
IF @PeakPerSnapshotCapacityConfirmed <> 1
   OR @MeasuredPeakTransactionLogBytes IS NULL
   OR @MeasuredPeakRollbackBytes IS NULL
   OR @MeasuredPeakDataAllocationBytes IS NULL
   OR @MeasuredPeakTempdbAllocationBytes IS NULL
   OR @MeasuredAutogrowthReserveBytes IS NULL
   OR @MeasuredExternalReserveBytes IS NULL
    THROW 51123, 'Measured isolated-lab per-snapshot capacity evidence is required.', 1;
IF @IndependentLivePreflightConfirmed <> 1
   OR NULLIF(@IndependentLivePreflightEvidenceReference, N'') IS NULL
   OR @IndependentLivePreflightCapturedAtUtc IS NULL
   OR @IndependentLivePreflightMaxAgeMinutes <= 0
    THROW 51134, 'Independently captured live C:, MDF, LDF, tempdb, session, lease, and run evidence is required.', 1;
IF DATEDIFF(MINUTE, @IndependentLivePreflightCapturedAtUtc, SYSUTCDATETIME())
       NOT BETWEEN 0 AND @IndependentLivePreflightMaxAgeMinutes
    THROW 51135, 'Independent live preflight evidence is stale or dated in the future.', 1;
IF @IndependentCFreeBytes IS NULL
   OR @IndependentMdfAllocatedPages IS NULL
   OR @IndependentMdfUsedPages IS NULL
   OR @IndependentMdfFreePages IS NULL
   OR @IndependentLdfAllocatedBytes IS NULL
   OR @IndependentLdfUsedBytes IS NULL
   OR @IndependentLdfGrowthBytes IS NULL
   OR @IndependentLdfMaxSizePages IS NULL
   OR @IndependentTempdbFreePages IS NULL
   OR @IndependentTempdbGrowthBytes IS NULL
   OR @IndependentTempdbMaxSizePages IS NULL
   OR @IndependentActiveSqlSessions IS NULL
   OR @IndependentActiveSqlRequests IS NULL
   OR @IndependentActiveCollectorSessions IS NULL
   OR @IndependentActiveCollectorRuns IS NULL
   OR @IndependentActiveProductionLeases IS NULL
   OR @IndependentActiveUserTransactions IS NULL
    THROW 51140, 'Independent live preflight fields are incomplete.', 1;
IF @SafetyFloorConfirmed <> 1
   OR @CapacityMeasurementCapturedAtUtc IS NULL
   OR @CapacityEvidenceMaxAgeMinutes <= 0
    THROW 51124, 'Fresh 12 GiB Windows safety-floor evidence is required.', 1;
IF DATEDIFF(MINUTE, @CapacityMeasurementCapturedAtUtc, SYSUTCDATETIME()) NOT BETWEEN 0 AND @CapacityEvidenceMaxAgeMinutes
    THROW 51125, 'Per-snapshot capacity evidence is stale or dated in the future.', 1;
IF NULLIF(@ApprovalReference, N'') IS NULL
    THROW 51126, 'A human approval reference is required before changing physical representations.', 1;
IF @MeasuredPeakTransactionLogBytes < 0
   OR @MeasuredPeakRollbackBytes < 0
   OR @MeasuredPeakDataAllocationBytes < 0
   OR @MeasuredPeakTempdbAllocationBytes < 0
   OR @MeasuredAutogrowthReserveBytes < 0
   OR @MeasuredExternalReserveBytes < 0
    THROW 51127, 'Measured per-snapshot capacity values cannot be negative.', 1;
IF @CurrentCFreeBytes IS NULL OR @CurrentCFreeBytes < @SafetyFloorBytes
    THROW 51128, 'Windows C: free space is below the protected 12 GiB floor.', 1;
IF @CurrentLogTotalBytes IS NULL
   OR @CurrentLogUsedBytes IS NULL
   OR @CurrentTempdbAllocatedBytes IS NULL
   OR @CurrentTempdbUsedBytes IS NULL
   OR @CurrentDataAllocatedBytes IS NULL
   OR @CurrentDataUsedBytes IS NULL
   OR @CurrentMdfAllocatedPages IS NULL
   OR @CurrentMdfUsedPages IS NULL
   OR @CurrentMdfFreePages IS NULL
   OR @CurrentLogGrowthBytes IS NULL
   OR @CurrentLogMaxSizePages IS NULL
   OR @CurrentTempdbGrowthBytes IS NULL
   OR @CurrentTempdbMaxSizePages IS NULL
   OR @CurrentActiveSqlSessions IS NULL
   OR @CurrentActiveSqlRequests IS NULL
   OR @CurrentActiveCollectorSessions IS NULL
   OR @CurrentActiveUserTransactions IS NULL
   OR @CurrentActiveCollectorRuns IS NULL
   OR @CurrentActiveProductionLeases IS NULL
    THROW 51133, 'Current SQL data, log, or tempdb capacity measurement is incomplete.', 1;
SET @MeasuredPeakPhysicalCReserveBytes =
      @MeasuredPeakTransactionLogBytes
    + @MeasuredPeakRollbackBytes
    + @MeasuredPeakDataAllocationBytes
    + @MeasuredPeakTempdbAllocationBytes
    + @MeasuredAutogrowthReserveBytes
    + @MeasuredExternalReserveBytes;
SET @ProjectedWorstCaseCFreeBytes = @CurrentCFreeBytes - @MeasuredPeakPhysicalCReserveBytes;
IF @ProjectedWorstCaseCFreeBytes < @SafetyFloorBytes
    THROW 51129, 'Projected worst-case physical C: free space would cross the 12 GiB Windows safety floor.', 1;
IF @CurrentMdfFreePages * CONVERT(BIGINT, 8192) < @MeasuredPeakDataAllocationBytes
    THROW 51136, 'Current measured MDF free pages do not cover the one-row data allocation peak.', 1;
IF @CurrentLogTotalBytes - @CurrentLogUsedBytes < @MeasuredPeakTransactionLogBytes + @MeasuredPeakRollbackBytes
    THROW 51130, 'Current transaction-log free space is below the measured one-snapshot peak.', 1;
IF @CurrentTempdbAllocatedBytes - @CurrentTempdbUsedBytes < @MeasuredPeakTempdbAllocationBytes
    THROW 51137, 'Current measured tempdb free space does not cover the one-row peak.', 1;
IF @CurrentCFreeBytes < @IndependentCFreeBytes
   OR @CurrentMdfAllocatedPages < @IndependentMdfAllocatedPages
   OR @CurrentMdfUsedPages > @IndependentMdfUsedPages
   OR @CurrentMdfFreePages < @IndependentMdfFreePages
   OR @CurrentLogTotalBytes < @IndependentLdfAllocatedBytes
   OR @CurrentLogUsedBytes > @IndependentLdfUsedBytes
   OR @CurrentLogGrowthBytes < @IndependentLdfGrowthBytes
   OR (@CurrentLogMaxSizePages <> @IndependentLdfMaxSizePages
       AND @IndependentLdfMaxSizePages <> -1)
   OR ((@CurrentTempdbAllocatedBytes - @CurrentTempdbUsedBytes) / CONVERT(BIGINT, 8192)) < @IndependentTempdbFreePages
   OR @CurrentTempdbGrowthBytes < @IndependentTempdbGrowthBytes
   OR (@CurrentTempdbMaxSizePages <> @IndependentTempdbMaxSizePages
       AND @IndependentTempdbMaxSizePages <> -1)
    THROW 51141, 'Live MDF, LDF, or tempdb state is less safe than the independent preflight evidence.', 1;
IF @IndependentActiveSqlSessions <> 0
   OR @IndependentActiveSqlRequests <> 0
   OR @IndependentActiveCollectorSessions <> 0
   OR @IndependentActiveCollectorRuns <> 0
   OR @IndependentActiveProductionLeases <> 0
   OR @IndependentActiveUserTransactions <> 0
    THROW 51142, 'Independent preflight recorded active SQL or collector activity.', 1;
IF @CurrentActiveSqlSessions > 0 OR @CurrentActiveSqlRequests > 0 OR @CurrentActiveUserTransactions > 0
    THROW 51138, 'A competing SQL session, request, or transaction is active in the target database.', 1;
IF @CurrentActiveCollectorRuns > 0 OR @CurrentActiveProductionLeases > 0
    THROW 51139, 'A collector run or production lease is active; stop the collector first.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE'
)
    THROW 51102, 'Active production lease - stop collector first.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 51106, 'A production collector run is active - stop the collector first.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.dm_exec_sessions AS s
    INNER JOIN sys.dm_exec_requests AS r ON r.session_id = s.session_id
    WHERE s.session_id <> @@SPID
      AND r.database_id = DB_ID()
      AND r.open_transaction_count > 0
)
    THROW 51107, 'Another SQL session has an active transaction in the target database.', 1;

DECLARE @Batch TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    OriginalWasNull BIT NOT NULL,
    OriginalWasEmpty BIT NOT NULL,
    OriginalLength BIGINT NOT NULL,
    OriginalUtf16Bytes VARBINARY(MAX) NOT NULL,
    OriginalHash VARBINARY(32) NOT NULL,
    CompressedLength BIGINT NOT NULL,
    CompressedHash VARBINARY(32) NOT NULL,
    CompressedPayload VARBINARY(MAX) NOT NULL,
    OriginalRunId NVARCHAR(100) NULL,
    OriginalAuditId BIGINT NULL,
    OriginalTargetIdentity NVARCHAR(200) NULL,
    OriginalSamplingTargetId INT NULL,
    OriginalLogicalDueBucket BIGINT NULL,
    OriginalObservedAtUtc DATETIME2(7) NULL,
    OriginalResponseTimestampSourceText NVARCHAR(256) NULL,
    OriginalParserVersion VARCHAR(80) NULL,
    OriginalApplicationStatus VARCHAR(80) NULL,
    OriginalPayloadHashSha256 CHAR(64) NULL,
    OriginalCreatedAtUtc DATETIME2(7) NULL
);

DECLARE @DataAllocatedBefore BIGINT;
DECLARE @DataUsedBefore BIGINT;
DECLARE @LogUsedBefore BIGINT;
DECLARE @TempdbAllocatedBefore BIGINT;
DECLARE @TempdbUsedBefore BIGINT;
DECLARE @TransactionLogBytes BIGINT = 0;
DECLARE @TransactionId BIGINT;
DECLARE @ObservedDataAllocatedAfter BIGINT;
DECLARE @ObservedDataUsedAfter BIGINT;
DECLARE @ObservedLogUsedAfter BIGINT;
DECLARE @ObservedTempdbAllocatedAfter BIGINT;
DECLARE @ObservedTempdbUsedAfter BIGINT;

BEGIN TRY
    SELECT
        @DataAllocatedBefore = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
        @DataUsedBefore = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed')))
                                      * CONVERT(BIGINT, 8192), 0)
    FROM sys.database_files AS df
    WHERE df.type = 0;
    SELECT @LogUsedBefore = CONVERT(BIGINT, used_log_space_in_bytes)
    FROM sys.dm_db_log_space_usage;
    SELECT
        @TempdbAllocatedBefore = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
        @TempdbUsedBefore = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                         * CONVERT(BIGINT, 8192), 0)
    FROM tempdb.sys.database_files AS df
    LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
    WHERE df.type = 0;

    BEGIN TRANSACTION;
    EXEC @LockResult = sys.sp_getapplock
        @Resource = N'CologneTransitIntelligence:SnapshotGzipBackfillV1',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 5000;
    IF @LockResult < 0 THROW 51103, 'Could not acquire snapshot backfill lock.', 1;

    INSERT @Batch
    (
        SnapshotId, OriginalWasNull, OriginalWasEmpty, OriginalLength,
        OriginalUtf16Bytes, OriginalHash, CompressedLength, CompressedHash,
        CompressedPayload, OriginalRunId, OriginalAuditId, OriginalTargetIdentity,
        OriginalSamplingTargetId, OriginalLogicalDueBucket, OriginalObservedAtUtc,
        OriginalResponseTimestampSourceText, OriginalParserVersion,
        OriginalApplicationStatus, OriginalPayloadHashSha256, OriginalCreatedAtUtc
    )
    SELECT TOP (@MaxRows)
        SnapshotId,
        CONVERT(BIT, CASE WHEN SnapshotJson IS NULL THEN 1 ELSE 0 END),
        CONVERT(BIT, CASE WHEN SnapshotJson IS NOT NULL AND DATALENGTH(SnapshotJson) = 0 THEN 1 ELSE 0 END),
        CONVERT(BIGINT, DATALENGTH(SnapshotJson)),
        CONVERT(VARBINARY(MAX), SnapshotJson),
        HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX), SnapshotJson)),
        CONVERT(BIGINT, DATALENGTH(COMPRESS(SnapshotJson))),
        HASHBYTES(N'SHA2_256', COMPRESS(SnapshotJson)),
        COMPRESS(SnapshotJson),
        RunId, AuditId, TargetIdentity, SamplingTargetId, LogicalDueBucket,
        ObservedAtUtc, ResponseTimestampSourceText, ParserVersion,
        ApplicationStatus, PayloadHashSha256, CreatedAtUtc
    FROM stg.MddMacSourceSnapshot WITH (UPDLOCK, HOLDLOCK)
    WHERE SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
    ORDER BY SnapshotId;

    IF (SELECT COUNT_BIG(*) FROM @Batch) > 1
        THROW 51131, 'The one-snapshot transaction bound was violated.', 1;

    UPDATE s
    SET s.SnapshotJsonGzip = b.CompressedPayload,
        s.SnapshotJson = NULL
    FROM stg.MddMacSourceSnapshot AS s
    INNER JOIN @Batch AS b ON b.SnapshotId = s.SnapshotId
    WHERE s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL;
    SET @Migrated = @@ROWCOUNT;

    IF @Migrated <> CONVERT(INT, (SELECT COUNT_BIG(*) FROM @Batch))
        THROW 51143, 'The one-row SnapshotJson update was not atomic and deterministic.', 1;

    SELECT @TransactionId = transaction_id
    FROM sys.dm_tran_current_transaction();
    SELECT @TransactionLogBytes = COALESCE(MAX(CONVERT(BIGINT, dt.database_transaction_log_bytes_used)), 0)
    FROM sys.dm_tran_database_transactions AS dt
    WHERE dt.transaction_id = @TransactionId
      AND dt.database_id = DB_ID();

    IF EXISTS
    (
        SELECT 1
        FROM @Batch AS b
        INNER JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = b.SnapshotId
        CROSS APPLY (SELECT DECOMPRESS(s.SnapshotJsonGzip) AS RestoredBytes) AS d
        CROSS APPLY (SELECT CONVERT(NVARCHAR(MAX), d.RestoredBytes) AS RestoredText) AS r
        WHERE s.SnapshotId <> b.SnapshotId
           OR s.SnapshotJson IS NOT NULL OR s.SnapshotJsonGzip IS NULL
           OR d.RestoredBytes IS NULL
           OR d.RestoredBytes <> b.OriginalUtf16Bytes
           OR DATALENGTH(d.RestoredBytes) <> b.OriginalLength
           OR CONVERT(BIT, CASE WHEN r.RestoredText IS NULL THEN 1 ELSE 0 END) <> b.OriginalWasNull
           OR CONVERT(BIT, CASE WHEN DATALENGTH(r.RestoredText) = 0 THEN 1 ELSE 0 END) <> b.OriginalWasEmpty
           OR HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX),
                    r.RestoredText)) <> b.OriginalHash
           OR DATALENGTH(s.SnapshotJsonGzip) <> b.CompressedLength
           OR HASHBYTES(N'SHA2_256', s.SnapshotJsonGzip) <> b.CompressedHash
           OR (b.OriginalLength > 0 AND ISJSON(r.RestoredText) <> 1)
           OR s.RunId <> b.OriginalRunId
           OR s.AuditId <> b.OriginalAuditId
           OR s.TargetIdentity <> b.OriginalTargetIdentity
           OR ISNULL(s.SamplingTargetId, -2147483648) <> ISNULL(b.OriginalSamplingTargetId, -2147483648)
           OR s.LogicalDueBucket <> b.OriginalLogicalDueBucket
           OR s.ObservedAtUtc <> b.OriginalObservedAtUtc
           OR ISNULL(s.ResponseTimestampSourceText, N'') <> ISNULL(b.OriginalResponseTimestampSourceText, N'')
           OR ISNULL(s.ParserVersion, '') <> ISNULL(b.OriginalParserVersion, '')
           OR ISNULL(s.ApplicationStatus, '') <> ISNULL(b.OriginalApplicationStatus, '')
           OR s.PayloadHashSha256 <> b.OriginalPayloadHashSha256
           OR s.CreatedAtUtc <> b.OriginalCreatedAtUtc
    )
        THROW 51104, 'Lossless byte, NULL/empty, SnapshotId, or audit-lineage roundtrip failed; backfill rolled back.', 1;

    IF EXISTS
    (
        SELECT 1
        FROM @Batch AS b
        INNER JOIN stg.vMddMacSourceSnapshotJson AS v ON v.SnapshotId = b.SnapshotId
        WHERE v.SnapshotJson IS NULL
           OR DATALENGTH(v.SnapshotJson) <> b.OriginalLength
           OR CONVERT(BIT, CASE WHEN DATALENGTH(v.SnapshotJson) = 0 THEN 1 ELSE 0 END) <> b.OriginalWasEmpty
           OR HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson)) <> b.OriginalHash
    )
        THROW 51132, 'Consumer readback view parity failed; backfill rolled back.', 1;

    COMMIT TRANSACTION;

    SELECT
        @ObservedDataAllocatedAfter = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
        @ObservedDataUsedAfter = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed')))
                                             * CONVERT(BIGINT, 8192), 0)
    FROM sys.database_files AS df
    WHERE df.type = 0;
    SELECT @ObservedLogUsedAfter = CONVERT(BIGINT, used_log_space_in_bytes)
    FROM sys.dm_db_log_space_usage;
    SELECT
        @ObservedTempdbAllocatedAfter = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
        @ObservedTempdbUsedAfter = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                                * CONVERT(BIGINT, 8192), 0)
    FROM tempdb.sys.database_files AS df
    LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
    WHERE df.type = 0;

    SELECT
        N'capacity.snapshot_gzip_one_row_result' AS statement_id,
        @Migrated AS migrated_rows,
        b.SnapshotId,
        b.OriginalLength AS original_plaintext_bytes,
        b.CompressedLength AS compressed_payload_bytes,
        b.OriginalLength - b.CompressedLength AS exact_payload_savings_bytes,
        CONVERT(char(64), b.OriginalHash, 2) AS original_utf16_sha256,
        b.OriginalWasNull AS original_was_null,
        b.OriginalWasEmpty AS original_was_empty,
        b.OriginalRunId AS run_id,
        b.OriginalAuditId AS audit_id,
        @TransactionLogBytes AS observed_peak_transaction_log_bytes,
        @MeasuredPeakRollbackBytes AS measured_peak_rollback_bytes,
        @MeasuredAutogrowthReserveBytes AS measured_autogrowth_reserve_bytes,
        @MeasuredExternalReserveBytes AS measured_external_c_reserve_bytes,
        @ProjectedWorstCaseCFreeBytes AS projected_worst_case_c_free_bytes,
        @SafetyFloorBytes AS required_windows_c_floor_bytes,
        @ObservedDataAllocatedAfter - @DataAllocatedBefore AS observed_data_file_allocation_delta_bytes,
        @ObservedDataUsedAfter - @DataUsedBefore AS observed_data_file_used_delta_bytes,
        @ObservedTempdbAllocatedAfter - @TempdbAllocatedBefore AS observed_tempdb_allocation_delta_bytes,
        @ObservedTempdbUsedAfter - @TempdbUsedBefore AS observed_tempdb_used_delta_bytes,
        @ObservedLogUsedAfter - @LogUsedBefore AS observed_log_used_delta_bytes,
        @VerifiedBackupEvidenceLabel AS backup_evidence_label,
        @ApprovalReference AS approval_reference
    FROM @Batch AS b;

    SELECT
        N'capacity.snapshot_gzip_resume_state' AS statement_id,
        SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END))
            AS remaining_plaintext_rows,
        SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END))
            AS compressed_rows,
        SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                                  AND DATALENGTH(SnapshotJson) = 0 THEN 1 ELSE 0 END))
            AS remaining_empty_plaintext_rows,
        MIN(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN SnapshotId END)
            AS next_snapshot_id,
        N'RETRY_SAFE_BY_SNAPSHOT_ID; EACH_RUN_COMMITS_AT_MOST_ONE_ROW' AS recovery_contract
    FROM stg.MddMacSourceSnapshot;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

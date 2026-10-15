/*
    COLOGNE TRANSIT INTELLIGENCE — SEPARATELY APPROVED MDF SPACE RECOVERY

    This is a destructive resource operation, separate from retention.  It is
    inert by default and was not run as part of S156.  It never creates a
    backup, changes recovery/autogrowth, rebuilds indexes, restarts a
    collector, creates a replacement database, or changes schema/data.

    The live database has one reviewed MDF logical name:
      CologneTransitIntelligence

    Run this file read-only first.  A separately reviewed SSMS working copy
    must replace the NULL target in the file plan with an exact target at or
    above the measured allocation high-water mark, set @ApplyShrink = 1, and
    complete every approval gate.  The apply branch
    remeasures Windows C:, the transaction log, tempdb, and active collectors
    immediately before each shrink command and verifies the actual after
    measurement.  A data-file shrink is never inferred from logical MDF free
    pages; only the Windows volume measurement is a recovery result.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52400, 'Wrong database. Expected CologneTransitIntelligence.', 1;
IF @@TRANCOUNT <> 0
    THROW 52401, 'An ambient transaction is present; refusing space recovery.', 1;

DECLARE @ApplyShrink BIT = 0;
DECLARE @ApprovalReference NVARCHAR(400) = N'';
DECLARE @ManifestVerificationReference NVARCHAR(400) = N'';
DECLARE @ConsumerReviewReference NVARCHAR(400) = N'';
DECLARE @CapacityReviewReference NVARCHAR(400) = N'';
DECLARE @CollectorDisabledConfirmed BIT = 0;
DECLARE @RecoveryPointConfirmed BIT = 0;
DECLARE @ManifestVerified BIT = 0;
DECLARE @ConsumerReviewConfirmed BIT = 0;
DECLARE @CapacityReviewConfirmed BIT = 0;
DECLARE @SchemaPreservationConfirmed BIT = 0;
DECLARE @CrossLayerRetentionConfirmed BIT = 0;
DECLARE @LogicalDataFileName sysname = N'CologneTransitIntelligence';
DECLARE @RequiredCFreeBytes BIGINT = CONVERT(BIGINT, 12) * 1024 * 1024 * 1024;
DECLARE @LiveCFreeBytes BIGINT;
DECLARE @LogTotalBytes BIGINT;
DECLARE @LogUsedBytes BIGINT;
DECLARE @LogFreeBytes BIGINT;
DECLARE @TempdbAllocatedBytes BIGINT;
DECLARE @TempdbFreeBytes BIGINT;
DECLARE @DataAllocatedBytes BIGINT;
DECLARE @DataUsedBytes BIGINT;
DECLARE @DataInternalFreeBytes BIGINT;
DECLARE @OriginalProjectFootprintBytes BIGINT;
DECLARE @ProjectedProjectFootprintBytes BIGINT;
DECLARE @ProjectedMdfBytes BIGINT;
DECLARE @ProjectedCDataReductionBytes BIGINT;
DECLARE @MinimumReachableBytes BIGINT;
DECLARE @MinimumReachableSizeMB BIGINT;
DECLARE @MdfFileId INT;
DECLARE @DatabaseAutogrowthReserveBytes BIGINT;
DECLARE @TempdbAutogrowthReserveBytes BIGINT;
DECLARE @ProjectedDatabaseBytes BIGINT;
DECLARE @ProjectedCFreeBytes BIGINT;
DECLARE @ProjectedRecoveryPercent DECIMAL(19, 6);
DECLARE @ActualBeforeBytes BIGINT;
DECLARE @ActualAfterBytes BIGINT;
DECLARE @ActualBeforeMdfBytes BIGINT;
DECLARE @ActualAfterMdfBytes BIGINT;
DECLARE @ActualBeforeCFreeBytes BIGINT;
DECLARE @ActualAfterCFreeBytes BIGINT;
DECLARE @ShrinkFileName sysname;
DECLARE @ShrinkTargetMB BIGINT;
DECLARE @ShrinkCommand NVARCHAR(1000);

DECLARE @DataFilePlan TABLE
(
    LogicalDataFileName sysname NOT NULL PRIMARY KEY,
    TargetSizeMB BIGINT NULL
);

/* The file plan is the sole target authority; NULL is intentionally disabled. */
INSERT @DataFilePlan (LogicalDataFileName, TargetSizeMB)
VALUES (@LogicalDataFileName, NULL);

IF NOT EXISTS
(
    SELECT 1 FROM sys.database_files
    WHERE type = 0 AND name = @LogicalDataFileName
)
    THROW 52402, 'The reviewed MDF logical file was not found.', 1;
IF
(
    SELECT COUNT(*) FROM sys.database_files WHERE type = 0
)
    <> (SELECT COUNT(*) FROM @DataFilePlan)
    THROW 52403, 'The reviewed file plan must cover every MDF data file.', 1;

SELECT @MdfFileId = file_id
FROM sys.database_files
WHERE type = 0 AND name = @LogicalDataFileName;
SELECT @MinimumReachableBytes = COALESCE
(
    MAX((CONVERT(BIGINT, allocated_page_page_id) + 1) * 8192),
    CONVERT(BIGINT, 0)
)
FROM sys.dm_db_database_page_allocations
(
    DB_ID(), NULL, NULL, NULL, N'DETAILED'
)
WHERE allocated_page_file_id = @MdfFileId
  AND is_allocated = 1;
SET @MinimumReachableSizeMB = CONVERT
(
    BIGINT,
    CEILING(CONVERT(DECIMAL(28, 8), @MinimumReachableBytes) / 1048576.0)
);

/* Direct current Windows, MDF, transaction-log and tempdb measurements. */
SELECT @LiveCFreeBytes = MIN(CONVERT(BIGINT, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

SELECT
    @LogTotalBytes = total_log_size_in_bytes,
    @LogUsedBytes = used_log_space_in_bytes
FROM sys.dm_db_log_space_usage;
SET @LogFreeBytes = @LogTotalBytes - @LogUsedBytes;

SELECT
    @TempdbAllocatedBytes = SUM(CONVERT(BIGINT, df.size)) * 8192,
    @TempdbFreeBytes = SUM(CONVERT(BIGINT, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT
    @DataAllocatedBytes = SUM(CONVERT(BIGINT, df.size)) * 8192,
    @DataUsedBytes = SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed'))) * 8192,
    @DataInternalFreeBytes = SUM
    (
        (CONVERT(BIGINT, df.size) - CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed'))) * 8192
    )
FROM sys.database_files AS df
WHERE df.type = 0;

SELECT @OriginalProjectFootprintBytes = SUM(CONVERT(BIGINT, df.size)) * 8192
FROM sys.database_files AS df;

SELECT @DatabaseAutogrowthReserveBytes = SUM
(
    CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:' THEN 0
         WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;

SELECT @TempdbAutogrowthReserveBytes = SUM
(
    CASE WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM tempdb.sys.database_files AS df
WHERE df.type = 0;

SELECT
    N'portfolio.recovery_live_capacity_preflight' AS statement_id,
    SYSUTCDATETIME() AS measured_at_utc,
    @LogicalDataFileName AS logical_data_file_name,
    @LiveCFreeBytes AS windows_c_free_bytes,
    @RequiredCFreeBytes AS required_c_free_bytes,
    @LiveCFreeBytes - @RequiredCFreeBytes AS c_free_headroom_bytes,
    @DataAllocatedBytes AS mdf_allocated_bytes,
    @DataUsedBytes AS mdf_used_bytes,
    @DataInternalFreeBytes AS mdf_internal_free_bytes,
    @OriginalProjectFootprintBytes AS original_project_footprint_bytes_including_mdf_and_ldf,
    @MinimumReachableSizeMB AS measured_mdf_minimum_reachable_size_mb,
    @LogTotalBytes AS transaction_log_total_bytes,
    @LogUsedBytes AS transaction_log_used_bytes,
    @LogFreeBytes AS transaction_log_free_bytes,
    @TempdbAllocatedBytes AS tempdb_allocated_bytes,
    @TempdbFreeBytes AS tempdb_unallocated_bytes,
    @DatabaseAutogrowthReserveBytes AS database_next_growth_reserve_bytes,
    @TempdbAutogrowthReserveBytes AS tempdb_next_growth_reserve_bytes,
    N'READ_ONLY_PREFLIGHT; MDF_FREE_PAGES_ARE_NOT_WINDOWS_C_RECOVERY' AS measurement_basis;

SELECT
    N'portfolio.recovery_file_plan' AS statement_id,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS configured_target_size_mb,
    @MinimumReachableSizeMB AS measured_minimum_reachable_size_mb,
    CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), FILEPROPERTY(p.LogicalDataFileName, N'SpaceUsed')) / 128.0)) AS current_used_size_mb,
    CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) / 128.0)) AS current_allocated_size_mb,
    N'REVIEWED_WORKING_COPY_MUST_SET_TARGET_AT_OR_ABOVE_ALLOCATION_HIGH_WATER_MARK_AND_BELOW_CURRENT_ALLOCATED' AS target_contract
FROM @DataFilePlan AS p
JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName AND df.type = 0;

IF @LiveCFreeBytes IS NULL
   OR @LiveCFreeBytes < @RequiredCFreeBytes
   OR @LogFreeBytes IS NULL
   OR @TempdbFreeBytes IS NULL
    THROW 52404, 'Live C:, transaction-log, or tempdb capacity evidence is missing or below the safety floor.', 1;

IF @ApplyShrink = 0
BEGIN
    SELECT
        N'portfolio.recovery_apply_gate' AS statement_id,
        N'APPLY_SHRINK_DISABLED' AS status,
        @LogicalDataFileName AS logical_data_file_name,
        (SELECT TargetSizeMB FROM @DataFilePlan) AS target_size_mb,
        N'No DBCC command was executed. Review the direct preflight result and set an exact target only in a separately approved working copy.'
            AS next_action;
    RETURN;
END;

IF @CollectorDisabledConfirmed = 0
   OR @RecoveryPointConfirmed = 0
   OR @ManifestVerified = 0
   OR @ConsumerReviewConfirmed = 0
   OR @CapacityReviewConfirmed = 0
   OR @SchemaPreservationConfirmed = 0
   OR @CrossLayerRetentionConfirmed = 0
   OR NULLIF(@ApprovalReference, N'') IS NULL
   OR NULLIF(@ManifestVerificationReference, N'') IS NULL
   OR NULLIF(@ConsumerReviewReference, N'') IS NULL
   OR NULLIF(@CapacityReviewReference, N'') IS NULL
    THROW 52405, 'Fresh recovery, manifest, consumer, capacity and approval evidence is required.', 1;

IF @MinimumReachableBytes IS NULL OR @MinimumReachableBytes <= 0
    THROW 52406, 'The MDF allocation high-water mark could not be measured.', 1;
IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName AND df.type = 0
    WHERE p.TargetSizeMB IS NULL
       OR CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
          < CONVERT(DECIMAL(38, 0), @MinimumReachableBytes)
       OR CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
          >= CONVERT(DECIMAL(38, 0), df.size) * 8192
)
    THROW 52406, 'The reviewed target cannot be reached or is not below current MDF allocation.', 1;

SET @ProjectedMdfBytes = CONVERT
(
    BIGINT,
    (SELECT SUM(CONVERT(DECIMAL(38, 0), TargetSizeMB) * 1048576)
     FROM @DataFilePlan)
);
SET @ProjectedProjectFootprintBytes = @OriginalProjectFootprintBytes
    - @DataAllocatedBytes
    + @ProjectedMdfBytes;
SET @ProjectedDatabaseBytes = @ProjectedProjectFootprintBytes;
SELECT @ProjectedCDataReductionBytes = COALESCE
(
    SUM
    (
        CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
             THEN CONVERT(BIGINT, df.size) * 8192
                  - CONVERT(BIGINT, p.TargetSizeMB) * 1048576
             ELSE CONVERT(BIGINT, 0) END
    ),
    CONVERT(BIGINT, 0)
)
FROM @DataFilePlan AS p
JOIN sys.database_files AS df
  ON df.name = p.LogicalDataFileName
 AND df.type = 0
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;
SET @ProjectedCFreeBytes = @LiveCFreeBytes
    + @ProjectedCDataReductionBytes
    - COALESCE(@DatabaseAutogrowthReserveBytes, 0)
    - COALESCE(@TempdbAutogrowthReserveBytes, 0);
SET @ProjectedRecoveryPercent = CONVERT(DECIMAL(19, 6),
    (@OriginalProjectFootprintBytes - @ProjectedProjectFootprintBytes)
    * 100.0 / NULLIF(@OriginalProjectFootprintBytes, 0));

IF @ProjectedCFreeBytes < @RequiredCFreeBytes
    THROW 52407, 'Projected worst-case Windows C: free space would cross 12 GiB.', 1;
IF @ProjectedRecoveryPercent < 80.0
    THROW 52408, 'Projected recovery of the complete original MDF plus LDF footprint is below the 80% target.', 1;
IF EXISTS
(
    SELECT 1 FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52409, 'A collector run is active; space recovery is stopped.', 1;
IF EXISTS (SELECT 1 FROM ctl.MddMacProductionLease WHERE Status = N'ACTIVE')
    THROW 52410, 'An active production lease exists; space recovery is stopped.', 1;

SELECT
    N'portfolio.recovery_exact_review' AS statement_id,
    @LiveCFreeBytes AS windows_c_free_bytes_before,
    @ProjectedCFreeBytes AS projected_worst_case_c_free_bytes,
    @RequiredCFreeBytes AS required_c_free_bytes,
    @ProjectedCDataReductionBytes AS projected_c_drive_data_file_reduction_bytes,
    @OriginalProjectFootprintBytes AS original_project_footprint_bytes_including_ldf,
    @ProjectedProjectFootprintBytes AS projected_project_footprint_bytes_including_ldf,
    @ProjectedDatabaseBytes AS projected_database_file_bytes,
    @ProjectedRecoveryPercent AS projected_project_footprint_recovery_percent,
    @MinimumReachableSizeMB AS measured_minimum_reachable_size_mb,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS target_size_mb,
    N'REVIEW_THIS_RESULT_SET_AND_EXACT_COMMAND_BEFORE_CONTINUING' AS review_gate
FROM @DataFilePlan AS p;

SELECT
    N'portfolio.recovery_exact_command' AS statement_id,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS target_size_mb,
    N'DBCC SHRINKFILE (' + QUOTENAME(p.LogicalDataFileName) + N', '
        + CONVERT(NVARCHAR(30), p.TargetSizeMB) + N') WITH NO_INFOMSGS;'
        AS exact_reviewed_command,
    N'ONLY_MDF; NO_SHRINKDATABASE; NO_AUTOGROWTH_CHANGE; NO_RECOVERY_MODEL_CHANGE'
        AS command_boundary
FROM @DataFilePlan AS p;

SELECT @ActualBeforeBytes = SUM(CONVERT(BIGINT, df.size)) * 8192
FROM sys.database_files AS df;
SELECT @ActualBeforeMdfBytes = SUM(CONVERT(BIGINT, df.size)) * 8192
FROM sys.database_files AS df
WHERE df.type = 0;
SET @ActualBeforeCFreeBytes = @LiveCFreeBytes;

DECLARE shrink_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT LogicalDataFileName, TargetSizeMB
    FROM @DataFilePlan
    ORDER BY LogicalDataFileName;
BEGIN TRY
    OPEN shrink_cursor;
    FETCH NEXT FROM shrink_cursor INTO @ShrinkFileName, @ShrinkTargetMB;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        /* Fresh measurement immediately before every DBCC command. */
        SELECT @ActualBeforeCFreeBytes = MIN(CONVERT(BIGINT, vs.available_bytes))
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';
        SELECT @LogFreeBytes = total_log_size_in_bytes - used_log_space_in_bytes
        FROM sys.dm_db_log_space_usage;
        SELECT @TempdbFreeBytes = SUM(CONVERT(BIGINT, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
        WHERE df.type = 0;
        SELECT @DatabaseAutogrowthReserveBytes = SUM
        (
            CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:' THEN 0
                 WHEN df.is_percent_growth = 1
                     THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
                 ELSE CONVERT(BIGINT, df.growth) * 8192 END
        )
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;
        SELECT @TempdbAutogrowthReserveBytes = SUM
        (
            CASE WHEN df.is_percent_growth = 1
                     THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
                 ELSE CONVERT(BIGINT, df.growth) * 8192 END
        )
        FROM tempdb.sys.database_files AS df
        WHERE df.type = 0;
        IF @ActualBeforeCFreeBytes IS NULL
           OR @ActualBeforeCFreeBytes
                - COALESCE(@DatabaseAutogrowthReserveBytes, 0)
                - COALESCE(@TempdbAutogrowthReserveBytes, 0) < @RequiredCFreeBytes
           OR @LogFreeBytes IS NULL OR @LogFreeBytes <= 0
           OR @TempdbFreeBytes IS NULL OR @TempdbFreeBytes <= 0
            THROW 52411, 'Fresh pre-shrink capacity measurement failed the safety gate.', 1;
        IF EXISTS
        (
            SELECT 1 FROM ctl.MddMacCollectorRun
            WHERE Status IN (N'RUNNING', N'CLAIMED')
              AND CollectionContext IN (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
        )
            THROW 52413, 'A collector run became active; space recovery is stopped.', 1;
        IF EXISTS (SELECT 1 FROM ctl.MddMacProductionLease WHERE Status = N'ACTIVE')
            THROW 52414, 'An active production lease exists; space recovery is stopped.', 1;
        IF EXISTS
        (
            SELECT 1
            FROM sys.dm_exec_requests
            WHERE session_id <> @@SPID
              AND database_id = DB_ID()
              AND command <> N'AWAITING COMMAND'
        )
            THROW 52415, 'Another active request exists in the target database.', 1;

        SET @ShrinkCommand = N'DBCC SHRINKFILE (' + QUOTENAME(@ShrinkFileName)
            + N', ' + CONVERT(NVARCHAR(30), @ShrinkTargetMB) + N') WITH NO_INFOMSGS;';
        EXEC sys.sp_executesql @ShrinkCommand;
        FETCH NEXT FROM shrink_cursor INTO @ShrinkFileName, @ShrinkTargetMB;
    END;
    CLOSE shrink_cursor;
    DEALLOCATE shrink_cursor;
END TRY
BEGIN CATCH
    IF CURSOR_STATUS('local', 'shrink_cursor') = 1 CLOSE shrink_cursor;
    IF CURSOR_STATUS('local', 'shrink_cursor') > -3 DEALLOCATE shrink_cursor;
    THROW;
END CATCH;

SELECT @ActualAfterBytes = SUM(CONVERT(BIGINT, df.size)) * 8192
FROM sys.database_files AS df;
SELECT @ActualAfterMdfBytes = SUM(CONVERT(BIGINT, df.size)) * 8192
FROM sys.database_files AS df
WHERE df.type = 0;
IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    JOIN sys.database_files AS df
      ON df.name = p.LogicalDataFileName
     AND df.type = 0
    WHERE CONVERT(DECIMAL(38, 0), df.size) * 8192
          > CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
)
    THROW 52417, 'The actual MDF allocation did not reach the reviewed target.', 1;
SELECT @ActualAfterCFreeBytes = MIN(CONVERT(BIGINT, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

IF @ActualAfterCFreeBytes < @RequiredCFreeBytes
    THROW 52412, 'Actual post-shrink Windows C: free space is below the 12 GiB floor.', 1;
SELECT @DatabaseAutogrowthReserveBytes = SUM
(
    CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:' THEN 0
         WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;
SELECT @TempdbAutogrowthReserveBytes = SUM
(
    CASE WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM tempdb.sys.database_files AS df
WHERE df.type = 0;
IF @ActualAfterCFreeBytes
        - COALESCE(@DatabaseAutogrowthReserveBytes, 0)
        - COALESCE(@TempdbAutogrowthReserveBytes, 0) < @RequiredCFreeBytes
    THROW 52412, 'Actual post-shrink C: headroom after growth reserves is below the 12 GiB floor.', 1;
SELECT @LogFreeBytes = total_log_size_in_bytes - used_log_space_in_bytes
FROM sys.dm_db_log_space_usage;
SELECT @TempdbFreeBytes = SUM(CONVERT(BIGINT, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;
IF @LogFreeBytes IS NULL OR @LogFreeBytes <= 0
   OR @TempdbFreeBytes IS NULL OR @TempdbFreeBytes <= 0
    THROW 52416, 'Post-shrink transaction-log or tempdb capacity evidence is invalid.', 1;

SELECT
    N'portfolio.recovery_actual_result' AS statement_id,
    @ActualBeforeBytes AS database_file_bytes_before,
    @ActualAfterBytes AS database_file_bytes_after,
    @ActualBeforeBytes - @ActualAfterBytes AS measured_file_bytes_released,
    @ActualBeforeCFreeBytes AS windows_c_free_bytes_before,
    @ActualAfterCFreeBytes AS windows_c_free_bytes_after,
    @ActualAfterCFreeBytes - @ActualBeforeCFreeBytes AS measured_windows_bytes_released,
    @ActualBeforeMdfBytes AS mdf_allocated_bytes_before,
    @ActualAfterMdfBytes AS mdf_allocated_bytes_after,
    @ActualBeforeMdfBytes - @ActualAfterMdfBytes AS measured_mdf_bytes_released,
    CONVERT(DECIMAL(19, 6),
        (@ActualBeforeMdfBytes - @ActualAfterMdfBytes) * 100.0
        / NULLIF(@ActualBeforeMdfBytes, 0)) AS actual_mdf_recovery_percent,
    CONVERT(DECIMAL(19, 6),
        (@ActualBeforeBytes - @ActualAfterBytes) * 100.0
        / NULLIF(@ActualBeforeBytes, 0)) AS actual_project_footprint_recovery_percent,
    @LogFreeBytes AS transaction_log_free_bytes_after,
    @TempdbFreeBytes AS tempdb_unallocated_bytes_after,
    N'ACTUAL_WINDOWS_MEASUREMENT_ONLY; VERIFY_READBACK_AND_FRAGMENTATION_AGAIN'
        AS measurement_basis;

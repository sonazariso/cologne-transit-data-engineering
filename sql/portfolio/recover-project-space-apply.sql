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
    above actual used space plus the reviewed MDF reserve, set @ApplyShrink =
    1, and complete every approval gate.  The apply branch
    remeasures Windows C:, the transaction log, tempdb, and active collectors
    immediately before each shrink command and verifies the actual after
    measurement.  SQL Server may leave a data file larger than the requested
    SHRINKFILE target when pages cannot be moved; the actual after measurement
    is authoritative.  A data-file shrink is never inferred from logical MDF
    free pages; only the Windows volume measurement is a recovery result.
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
DECLARE @MdfUsedPages BIGINT;
DECLARE @MdfGrowthReservePages BIGINT;
DECLARE @MdfReservePages BIGINT;
DECLARE @MinimumSafeTargetPages BIGINT;
DECLARE @MinimumSafeTargetSizeMB BIGINT;
DECLARE @DatabaseAutogrowthReserveBytes BIGINT;
DECLARE @TempdbAutogrowthReserveBytes BIGINT;
DECLARE @ProjectedDatabaseBytes BIGINT;
DECLARE @ProjectedCFreeBytes BIGINT;
DECLARE @ProjectedRecoveryPercent DECIMAL(19, 6);
DECLARE @RecoveryTargetPercent DECIMAL(19, 6) = 80.0;
DECLARE @ActualBeforeBytes BIGINT;
DECLARE @ActualAfterBytes BIGINT;
DECLARE @ActualBeforeMdfBytes BIGINT;
DECLARE @ActualAfterMdfBytes BIGINT;
DECLARE @ActualBeforeCFreeBytes BIGINT;
DECLARE @ActualAfterCFreeBytes BIGINT;
DECLARE @CurrentMdfUsedPages BIGINT;
DECLARE @CurrentMdfGrowthReservePages BIGINT;
DECLARE @CurrentMdfReservePages BIGINT;
DECLARE @CurrentMinimumSafeTargetPages BIGINT;
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

/* A SHRINKFILE target is a requested final allocation size, not the current
   highest allocated page.  SQL Server can move allocated pages while
   shrinking, and can also stop above the requested target.  Use actual
   FILEPROPERTY(SpaceUsed) pages plus one configured MDF growth increment as
   the safe target floor; when autogrowth is disabled, retain at least one
   extent so the floor remains a usable MB-sized target. */
/* 80% reports the complete original MDF plus LDF footprint; it is not an
   execution gate. */
SELECT
    @MdfUsedPages = CONVERT(BIGINT, COALESCE(FILEPROPERTY(df.name, N'SpaceUsed'), 0)),
    @MdfGrowthReservePages = CASE
        WHEN df.is_percent_growth = 1
            THEN CONVERT
            (
                BIGINT,
                CEILING
                (
                    CONVERT(DECIMAL(38, 8), CONVERT(BIGINT, df.size))
                    * CONVERT(DECIMAL(38, 8), df.growth) / 100.0
                )
            )
        ELSE CONVERT(BIGINT, df.growth)
    END
FROM sys.database_files AS df
WHERE df.type = 0
  AND df.name = @LogicalDataFileName;
SET @MdfReservePages = CASE
    WHEN COALESCE(@MdfGrowthReservePages, 0) > CONVERT(BIGINT, 8)
        THEN @MdfGrowthReservePages
    ELSE CONVERT(BIGINT, 8)
END;
SET @MinimumSafeTargetPages = @MdfUsedPages + @MdfReservePages;
SET @MinimumSafeTargetSizeMB = CONVERT
(
    BIGINT,
    CEILING(CONVERT(DECIMAL(38, 8), @MinimumSafeTargetPages) / 128.0)
);

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
    @MdfUsedPages AS mdf_used_pages,
    @MdfReservePages AS reviewed_mdf_target_reserve_pages,
    @MinimumSafeTargetSizeMB AS measured_mdf_minimum_safe_target_size_mb,
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
    @MdfUsedPages AS actual_used_pages,
    @MdfReservePages AS target_reserve_pages,
    @MinimumSafeTargetSizeMB AS measured_minimum_safe_target_size_mb,
    CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), FILEPROPERTY(p.LogicalDataFileName, N'SpaceUsed')) / 128.0)) AS current_used_size_mb,
    CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) / 128.0)) AS current_allocated_size_mb,
    N'REVIEWED_WORKING_COPY_MUST_SET_TARGET_AT_OR_ABOVE_ACTUAL_USED_SPACE_PLUS_RESERVE_AND_BELOW_CURRENT_ALLOCATED; SHRINKFILE_TARGET_IS_A_REQUEST' AS target_contract
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

IF @MdfUsedPages IS NULL
   OR @MdfUsedPages < 0
   OR @MdfReservePages IS NULL
   OR @MdfReservePages < 8
   OR @MinimumSafeTargetPages <= 0
   OR @MinimumSafeTargetSizeMB <= 0
    THROW 52406, 'Actual MDF used space plus the reviewed target reserve could not be measured.', 1;
IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName AND df.type = 0
    WHERE p.TargetSizeMB IS NULL
       OR p.TargetSizeMB <= 0
       OR CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
          < CONVERT(DECIMAL(38, 0), @MinimumSafeTargetPages) * 8192
       OR CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
          >= CONVERT(DECIMAL(38, 0), df.size) * 8192
)
    THROW 52406, 'The reviewed target must cover actual MDF used space plus the reviewed reserve and remain below current MDF allocation.', 1;

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
    @RecoveryTargetPercent AS reported_recovery_target_percent,
    CASE WHEN @ProjectedRecoveryPercent >= @RecoveryTargetPercent
         THEN CONVERT(BIT, 1) ELSE CONVERT(BIT, 0) END AS projected_recovery_target_met,
    CASE WHEN @ProjectedRecoveryPercent >= @RecoveryTargetPercent
         THEN N'REPORTED_80_PERCENT_TARGET_MET'
         ELSE N'REPORTED_80_PERCENT_TARGET_UNATTAINABLE_WITH_THIS_SAFE_PLAN; PARTIAL_RECOVERY_REMAINS_ALLOWED'
    END AS projected_recovery_target_status,
    @MinimumSafeTargetSizeMB AS measured_mdf_minimum_safe_target_size_mb,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS target_size_mb,
    N'REVIEW_THIS_RESULT_SET_AND_EXACT_COMMAND_BEFORE_CONTINUING; 80_PERCENT_IS_REPORT_ONLY' AS review_gate
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

        /* Recheck the file-specific floor immediately before DBCC.  This
           uses actual used pages plus the configured growth reserve, not an
           allocated-page high-water mark.  SHRINKFILE may still stop above
           the requested target; the post-shrink allocation is reported. */
        SELECT
            @CurrentMdfUsedPages = CONVERT
            (
                BIGINT,
                COALESCE(FILEPROPERTY(df.name, N'SpaceUsed'), 0)
            ),
            @CurrentMdfGrowthReservePages = CASE
                WHEN df.is_percent_growth = 1
                    THEN CONVERT
                    (
                        BIGINT,
                        CEILING
                        (
                            CONVERT(DECIMAL(38, 8), CONVERT(BIGINT, df.size))
                            * CONVERT(DECIMAL(38, 8), df.growth) / 100.0
                        )
                    )
                ELSE CONVERT(BIGINT, df.growth)
            END
        FROM sys.database_files AS df
        WHERE df.type = 0
          AND df.name = @ShrinkFileName;
        SET @CurrentMdfReservePages = CASE
            WHEN COALESCE(@CurrentMdfGrowthReservePages, 0) > CONVERT(BIGINT, 8)
                THEN @CurrentMdfGrowthReservePages
            ELSE CONVERT(BIGINT, 8)
        END;
        SET @CurrentMinimumSafeTargetPages =
            @CurrentMdfUsedPages + @CurrentMdfReservePages;
        IF @CurrentMdfUsedPages IS NULL
           OR @CurrentMdfUsedPages < 0
           OR @CurrentMinimumSafeTargetPages <= 0
           OR CONVERT(DECIMAL(38, 0), @ShrinkTargetMB) * 128
                < CONVERT(DECIMAL(38, 0), @CurrentMinimumSafeTargetPages)
            THROW 52418, 'Fresh MDF used-space plus reserve feasibility failed before SHRINKFILE.', 1;

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

/* DBCC SHRINKFILE can legally finish above its requested target when the
   remaining allocation cannot be moved.  That is a measured partial result,
   not a reason to discard the safe space recovery or claim the request was
   reached. */
SELECT
    N'portfolio.recovery_shrinkfile_target_readback' AS statement_id,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS requested_target_size_mb,
    CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(38, 8), df.size) / 128.0))
        AS actual_allocated_size_mb,
    CASE WHEN CONVERT(DECIMAL(38, 0), df.size) * 8192
              <= CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
         THEN CONVERT(BIT, 1) ELSE CONVERT(BIT, 0) END AS requested_target_reached,
    CASE WHEN CONVERT(DECIMAL(38, 0), df.size) * 8192
              <= CONVERT(DECIMAL(38, 0), p.TargetSizeMB) * 1048576
         THEN N'SHRINKFILE_TARGET_REACHED'
         ELSE N'The actual MDF allocation did not reach the reviewed target; SQL Server SHRINKFILE stopped above the request.'
    END AS target_status
FROM @DataFilePlan AS p
JOIN sys.database_files AS df
  ON df.name = p.LogicalDataFileName
 AND df.type = 0;
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
    @RecoveryTargetPercent AS reported_recovery_target_percent,
    CASE WHEN CONVERT(DECIMAL(19, 6),
                 (@ActualBeforeBytes - @ActualAfterBytes) * 100.0
                 / NULLIF(@ActualBeforeBytes, 0)) >= @RecoveryTargetPercent
         THEN CONVERT(BIT, 1) ELSE CONVERT(BIT, 0) END AS actual_recovery_target_met,
    CASE WHEN CONVERT(DECIMAL(19, 6),
                 (@ActualBeforeBytes - @ActualAfterBytes) * 100.0
                 / NULLIF(@ActualBeforeBytes, 0)) >= @RecoveryTargetPercent
         THEN N'REPORTED_80_PERCENT_TARGET_MET'
         ELSE N'REPORTED_80_PERCENT_TARGET_UNATTAINABLE; SAFE_PARTIAL_RECOVERY_COMPLETED'
    END AS actual_recovery_target_status,
    @LogFreeBytes AS transaction_log_free_bytes_after,
    @TempdbFreeBytes AS tempdb_unallocated_bytes_after,
    N'ACTUAL_WINDOWS_MEASUREMENT_ONLY; VERIFY_READBACK_AND_FRAGMENTATION_AGAIN'
        AS measurement_basis;

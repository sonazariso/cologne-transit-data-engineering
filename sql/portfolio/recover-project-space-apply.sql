/*
    COLOGNE TRANSIT INTELLIGENCE — MANUAL PHYSICAL SPACE RECOVERY

    DESTRUCTIVE RESOURCE OPERATION.  Inert by default and never run by Codex.
    This is a separate, operator-reviewed step after retention-apply.sql,
    readback validation, and the read-only recovery review.  It does not
    create a backup, alter recovery/autogrowth, rebuild indexes, or start a
    collector.

    The script uses a literal data-file plan populated from the read-only
    result set.  The plan must name every database data file that is in the
    reviewed project-file scope; it never creates or expands a file.  The
    target is rejected unless the measured projected C: free space stays at
    least 12 GiB and the projected project-file recovery is at least 80%.
    Actual recovery is reported only from the after-measurement result.

    This is physical file recovery only.  It preserves every schema, physical
    table, column, index, key, foreign key, view, function, procedure, and
    trigger.  A data-file shrink cannot authorize schema replacement or an
    unreviewed GTFS/warehouse/lineage data decision.
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
DECLARE @LogicalDataFileName sysname = N'REPLACE_WITH_EXACT_LOGICAL_DATA_FILE_NAME';
DECLARE @TargetSizeMB BIGINT = 0;
DECLARE @MeasuredExternalReserveBytes BIGINT = NULL;
DECLARE @MeasuredShrinkReserveBytes BIGINT = NULL;
DECLARE @MeasuredProjectBaselineBytes BIGINT = NULL;
DECLARE @RequiredCFreeBytes BIGINT =
    CONVERT(BIGINT, 12) * CONVERT(BIGINT, 1024) * CONVERT(BIGINT, 1024)
        * CONVERT(BIGINT, 1024);
DECLARE @LiveCFreeBytes BIGINT;
DECLARE @CurrentAllocatedBytes BIGINT;
DECLARE @CurrentUsedBytes BIGINT;
DECLARE @CurrentAllocatedPages BIGINT;
DECLARE @CurrentUsedPages BIGINT;
DECLARE @ProjectedDataFileBytes BIGINT;
DECLARE @ProjectedDatabaseFileBytes BIGINT;
DECLARE @ProjectedCFreeBytes BIGINT;
DECLARE @ProjectedRecoveryPercent DECIMAL(19, 6);
DECLARE @ActualBeforeBytes BIGINT;
DECLARE @ActualAfterBytes BIGINT;
DECLARE @ActualBeforeCFreeBytes BIGINT;
DECLARE @ActualAfterCFreeBytes BIGINT;
DECLARE @DataFilePlan TABLE
(
    LogicalDataFileName sysname NOT NULL PRIMARY KEY,
    TargetSizeMB BIGINT NOT NULL
);

/*
    Replace this literal with one row for every data file in the reviewed
    project-file scope, using exact logical names and reviewed targets.  The
    placeholder deliberately blocks execution and prevents file expansion.
*/
INSERT @DataFilePlan (LogicalDataFileName, TargetSizeMB)
VALUES (N'REPLACE_WITH_EXACT_LOGICAL_DATA_FILE_NAME', 0);

IF @ApplyShrink = 0
BEGIN
    SELECT
        N'portfolio.recovery_apply_gate' AS statement_id,
        N'APPLY_SHRINK_DISABLED' AS status,
        @LogicalDataFileName AS logical_data_file_name,
        @TargetSizeMB AS target_size_mb,
        N'No DBCC command was executed. Review the exact command in a manual working copy.'
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
   OR @MeasuredExternalReserveBytes IS NULL
   OR @MeasuredShrinkReserveBytes IS NULL
   OR @MeasuredProjectBaselineBytes IS NULL
    THROW 52402, 'Fresh recovery, manifest, consumer, capacity, and approval evidence is required.', 1;

IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    WHERE p.LogicalDataFileName = N'REPLACE_WITH_EXACT_LOGICAL_DATA_FILE_NAME'
       OR p.TargetSizeMB <= 0
)
    THROW 52403, 'Exact logical data-file names and targets are required.', 1;
IF (SELECT COUNT(*) FROM @DataFilePlan)
       <> (SELECT COUNT(*) FROM sys.database_files WHERE type = 0)
    THROW 52404, 'The data-file plan must cover every database data file.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.database_files AS df
    WHERE df.type = 0
      AND NOT EXISTS
          (SELECT 1 FROM @DataFilePlan AS p
           WHERE p.LogicalDataFileName = df.name)
)
    THROW 52414, 'Every database data file requires an explicit reviewed plan row.', 1;

SELECT @LiveCFreeBytes = MIN(CONVERT(bigint, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE df.type = 0
  AND UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    LEFT JOIN sys.database_files AS df
      ON df.name = p.LogicalDataFileName
     AND df.type = 0
    WHERE df.file_id IS NULL
)
    THROW 52405, 'An exact planned logical data file was not found.', 1;
IF @LiveCFreeBytes IS NULL OR @LiveCFreeBytes < @RequiredCFreeBytes
    THROW 52406, 'Live C: free space is missing or already below the 12 GiB floor.', 1;
IF EXISTS
(
    SELECT 1
    FROM @DataFilePlan AS p
    JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName
        AND df.type = 0
    WHERE p.TargetSizeMB < CONVERT(BIGINT,
              (CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) + 127) / 128)
       OR p.TargetSizeMB * CONVERT(BIGINT, 1048576)
            >= CONVERT(bigint, df.size) * CONVERT(bigint, 8192)
)
    THROW 52407, 'A target is below current used pages or does not reduce its exact data file.', 1;
IF @MeasuredExternalReserveBytes < 0 OR @MeasuredShrinkReserveBytes < 0
    THROW 52409, 'Measured reserves cannot be negative.', 1;

SELECT @CurrentAllocatedBytes = SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192),
       @CurrentUsedBytes = SUM(CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')))
           * CONVERT(bigint, 8192),
       @ProjectedDataFileBytes = SUM(p.TargetSizeMB) * CONVERT(bigint, 1048576)
FROM @DataFilePlan AS p
JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName
    AND df.type = 0;

SET @CurrentAllocatedPages = @CurrentAllocatedBytes / CONVERT(bigint, 8192);
SET @CurrentUsedPages = @CurrentUsedBytes / CONVERT(bigint, 8192);
SELECT @ProjectedDatabaseFileBytes =
    SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192)
FROM sys.database_files AS df;
SET @ProjectedDatabaseFileBytes =
    @ProjectedDatabaseFileBytes - @CurrentAllocatedBytes + @ProjectedDataFileBytes;
SET @ProjectedCFreeBytes =
    @LiveCFreeBytes - @MeasuredExternalReserveBytes
        - @MeasuredShrinkReserveBytes
        +
        (
            SELECT COALESCE(SUM(
                CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
                     THEN CONVERT(bigint, df.size) * CONVERT(bigint, 8192)
                          - p.TargetSizeMB * CONVERT(bigint, 1048576)
                     ELSE CONVERT(bigint, 0) END), 0)
            FROM @DataFilePlan AS p
            JOIN sys.database_files AS df ON df.name = p.LogicalDataFileName
                AND df.type = 0
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        );
SET @ProjectedRecoveryPercent =
    CONVERT(DECIMAL(19, 6),
        (@MeasuredProjectBaselineBytes - @ProjectedDatabaseFileBytes) * 100.0
        / NULLIF(@MeasuredProjectBaselineBytes, 0));

IF @ProjectedCFreeBytes < @RequiredCFreeBytes
    THROW 52410, 'Projected worst-case C: free space would cross 12 GiB.', 1;
IF @ProjectedRecoveryPercent < 80.0
    THROW 52411, 'Projected project-file recovery is below the 80 percent target.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN
          (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52412, 'A collector run is active; space recovery is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE Status = N'ACTIVE'
)
    THROW 52413, 'An active production lease exists; space recovery is stopped.', 1;

SELECT
    N'portfolio.recovery_apply_review' AS statement_id,
    N'ALL_PLANNED_DATA_FILES' AS logical_data_file_name,
    (SELECT COUNT_BIG(*) FROM @DataFilePlan) AS planned_data_file_count,
    @LiveCFreeBytes AS live_c_free_bytes,
    @ProjectedCFreeBytes AS projected_worst_case_c_free_bytes,
    @RequiredCFreeBytes AS required_c_free_bytes,
    @MeasuredProjectBaselineBytes AS project_baseline_bytes,
    @ProjectedDatabaseFileBytes AS projected_database_file_bytes,
    @ProjectedRecoveryPercent AS projected_recovery_percent,
    N'REVIEW THIS RESULT SET AND THE EXACT DBCC SHRINKFILE COMMAND BEFORE CONTINUING'
        AS review_gate;

SELECT
    @ActualBeforeBytes = SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192)
FROM sys.database_files AS df;
SELECT @ActualBeforeCFreeBytes = @LiveCFreeBytes;

SELECT
    N'portfolio.recovery_exact_command' AS statement_id,
    p.LogicalDataFileName AS logical_data_file_name,
    p.TargetSizeMB AS target_size_mb,
    N'DBCC SHRINKFILE (' + QUOTENAME(p.LogicalDataFileName) + N', '
        + CONVERT(nvarchar(30), p.TargetSizeMB) + N') WITH NO_INFOMSGS;'
        AS exact_reviewed_command,
    N'NO_SHRINKDATABASE; NO_AUTOGROWTH_CHANGE; NO_RECOVERY_MODEL_CHANGE'
        AS command_boundary
FROM @DataFilePlan AS p
ORDER BY p.LogicalDataFileName;

DECLARE @ShrinkFileName sysname;
DECLARE @ShrinkTargetMB BIGINT;
DECLARE @ShrinkCommand nvarchar(1000);
DECLARE shrink_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT LogicalDataFileName, TargetSizeMB
    FROM @DataFilePlan
    ORDER BY LogicalDataFileName;
OPEN shrink_cursor;
FETCH NEXT FROM shrink_cursor INTO @ShrinkFileName, @ShrinkTargetMB;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ShrinkCommand =
        N'DBCC SHRINKFILE (' + QUOTENAME(@ShrinkFileName) + N', '
        + CONVERT(nvarchar(30), @ShrinkTargetMB) + N') WITH NO_INFOMSGS;';
    EXEC sys.sp_executesql @ShrinkCommand;
    FETCH NEXT FROM shrink_cursor INTO @ShrinkFileName, @ShrinkTargetMB;
END;
CLOSE shrink_cursor;
DEALLOCATE shrink_cursor;

SELECT
    @ActualAfterBytes = SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192)
FROM sys.database_files AS df;
SELECT @ActualAfterCFreeBytes = MIN(CONVERT(bigint, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE df.type = 0
  AND UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

SELECT
    N'portfolio.recovery_actual_result' AS statement_id,
    @ActualBeforeBytes AS database_file_bytes_before,
    @ActualAfterBytes AS database_file_bytes_after,
    @ActualBeforeBytes - @ActualAfterBytes AS measured_file_bytes_released,
    @ActualBeforeCFreeBytes AS windows_c_free_bytes_before,
    @ActualAfterCFreeBytes AS windows_c_free_bytes_after,
    @ActualAfterCFreeBytes - @ActualBeforeCFreeBytes AS measured_windows_bytes_released,
    CONVERT(DECIMAL(19, 6),
        (@MeasuredProjectBaselineBytes - @ActualAfterBytes) * 100.0
        / NULLIF(@MeasuredProjectBaselineBytes, 0)) AS actual_project_recovery_percent,
    N'ACTUAL_RESULT_ONLY; VERIFY READBACK AND FRAGMENTATION AGAIN'
        AS measurement_basis;

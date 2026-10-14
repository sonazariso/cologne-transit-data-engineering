/*
    SNAPSHOT GZIP V1 — ISOLATED LAB PER-SNAPSHOT CAPACITY MEASUREMENT

    Run only on a restored copy of the verified recovery point.  The script
    deliberately commits the guarded one-row operation in the lab so SQL
    Server can report the actual transaction-log and allocation high water.
    It never runs on DataAnalyst-VM and refuses C: SQL data, log, or tempdb
    placement.  A failed run is resumable: each committed row is recorded in
    lab.SnapshotGzipCapacityMeasurement and the next run selects only remaining
    plaintext rows.

    The source-controlled default is disabled.  This is not a production
    script, does not create a duplicate table, and does not authorize the
    production backfill.  Use the production backfill only after this result,
    the existing backup/readback evidence, and a fresh C: floor check have been
    reviewed.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @RunLab BIT = 0;
DECLARE @ServerName nvarchar(128) = CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName'));
DECLARE @SafetyFloorBytes BIGINT = CONVERT(BIGINT, 12) * 1024 * 1024 * 1024;

IF @RunLab <> 1
    THROW 51140, 'Snapshot GZIP capacity lab is disabled; set @RunLab = 1 only in the isolated lab.', 1;
IF LOWER(@ServerName) = N'dataanalyst-vm' OR LOWER(@ServerName) LIKE N'dataanalyst-vm%'
    THROW 51141, 'Snapshot GZIP capacity lab cannot run on the production SQL Server.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
)
   OR EXISTS
(
    SELECT 1
    FROM tempdb.sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
)
    THROW 51142, 'Snapshot GZIP capacity lab requires SQL data, log, and tempdb files on non-C: volumes.', 1;
IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
   OR COL_LENGTH(N'stg.MddMacSourceSnapshot', N'SnapshotJsonGzip') IS NULL
    THROW 51143, 'Snapshot GZIP lab requires the installed SnapshotJsonGzip column.', 1;
IF OBJECT_ID(N'stg.vMddMacSourceSnapshotJson', N'V') IS NULL
    THROW 51144, 'Snapshot GZIP lab requires the lossless readback view.', 1;
IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot
    WHERE (SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL)
       OR (SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NOT NULL)
)
    THROW 51145, 'Snapshot GZIP lab found an invalid physical representation.', 1;

SELECT
    N'capacity.snapshot_gzip_lab_payload_savings' AS statement_id,
    COUNT_BIG(*) AS total_snapshot_rows,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END))
        AS eligible_plaintext_rows,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                             THEN DATALENGTH(SnapshotJson) ELSE 0 END)) AS plaintext_utf16_bytes,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                             THEN DATALENGTH(COMPRESS(SnapshotJson)) ELSE 0 END)) AS compressed_payload_bytes,
    SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
                             THEN DATALENGTH(SnapshotJson) - DATALENGTH(COMPRESS(SnapshotJson)) ELSE 0 END))
        AS exact_payload_savings_bytes,
    N'EXACT_COMPRESS_PAYLOAD; NOT_PHYSICAL_FILE_ALLOCATION' AS measurement_basis
FROM stg.MddMacSourceSnapshot;

IF SCHEMA_ID(N'lab') IS NULL
    EXEC(N'CREATE SCHEMA lab AUTHORIZATION dbo;');

IF OBJECT_ID(N'lab.SnapshotGzipCapacityMeasurement', N'U') IS NULL
BEGIN
    CREATE TABLE lab.SnapshotGzipCapacityMeasurement
    (
        SnapshotId BIGINT NOT NULL CONSTRAINT PK_lab_SnapshotGzipCapacityMeasurement PRIMARY KEY,
        MeasuredAtUtc DATETIME2(7) NOT NULL,
        OriginalPlaintextBytes BIGINT NOT NULL,
        CompressedPayloadBytes BIGINT NOT NULL,
        ExactPayloadSavingsBytes BIGINT NOT NULL,
        PeakTransactionLogBytes BIGINT NOT NULL,
        DataFileAllocationDeltaBytes BIGINT NOT NULL,
        DataFileUsedDeltaBytes BIGINT NOT NULL,
        TempdbAllocationDeltaBytes BIGINT NOT NULL,
        TempdbUsedDeltaBytes BIGINT NOT NULL,
        LabVolumeFreeBeforeBytes BIGINT NOT NULL,
        LabVolumeFreeDuringBytes BIGINT NOT NULL,
        LabVolumeFreeAfterBytes BIGINT NOT NULL,
        ObservedLabVolumeConsumptionBytes BIGINT NOT NULL,
        CONSTRAINT CK_lab_SnapshotGzipCapacityMeasurement_NonNegative CHECK
        (
            OriginalPlaintextBytes >= 0
            AND CompressedPayloadBytes >= 0
            AND ExactPayloadSavingsBytes >= 0
            AND PeakTransactionLogBytes >= 0
            AND DataFileAllocationDeltaBytes >= 0
            AND TempdbAllocationDeltaBytes >= 0
            AND ObservedLabVolumeConsumptionBytes >= 0
        )
    );
END;

DECLARE @SnapshotId BIGINT;
DECLARE @OriginalText NVARCHAR(MAX);
DECLARE @OriginalLength BIGINT;
DECLARE @OriginalHash VARBINARY(32);
DECLARE @CompressedPayload VARBINARY(MAX);
DECLARE @CompressedLength BIGINT;
DECLARE @CompressedHash VARBINARY(32);
DECLARE @TransactionId BIGINT;
DECLARE @PeakTransactionLogBytes BIGINT;
DECLARE @DataAllocatedBefore BIGINT;
DECLARE @DataUsedBefore BIGINT;
DECLARE @DataAllocatedDuring BIGINT;
DECLARE @DataUsedDuring BIGINT;
DECLARE @DataAllocatedAfter BIGINT;
DECLARE @DataUsedAfter BIGINT;
DECLARE @TempdbAllocatedBefore BIGINT;
DECLARE @TempdbUsedBefore BIGINT;
DECLARE @TempdbAllocatedDuring BIGINT;
DECLARE @TempdbUsedDuring BIGINT;
DECLARE @TempdbAllocatedAfter BIGINT;
DECLARE @TempdbUsedAfter BIGINT;
DECLARE @LabVolumeFreeBefore BIGINT;
DECLARE @LabVolumeFreeDuring BIGINT;
DECLARE @LabVolumeFreeAfter BIGINT;

DECLARE snapshot_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT s.SnapshotId
FROM stg.MddMacSourceSnapshot AS s
WHERE s.SnapshotJson IS NOT NULL
  AND s.SnapshotJsonGzip IS NULL
  AND NOT EXISTS
  (
      SELECT 1
      FROM lab.SnapshotGzipCapacityMeasurement AS m
      WHERE m.SnapshotId = s.SnapshotId
  )
ORDER BY s.SnapshotId;

OPEN snapshot_cursor;
FETCH NEXT FROM snapshot_cursor INTO @SnapshotId;

WHILE @@FETCH_STATUS = 0
BEGIN
    BEGIN TRY
        SELECT
            @DataAllocatedBefore = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @DataUsedBefore = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed')))
                                          * CONVERT(BIGINT, 8192), 0)
        FROM sys.database_files AS df
        WHERE df.type = 0;
        SELECT
            @TempdbAllocatedBefore = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @TempdbUsedBefore = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                             * CONVERT(BIGINT, 8192), 0)
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
        WHERE df.type = 0;
        SELECT @LabVolumeFreeBefore = MIN(CONVERT(BIGINT, vs.available_bytes))
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:';

        BEGIN TRANSACTION;
        SELECT
            @OriginalText = s.SnapshotJson,
            @OriginalLength = CONVERT(BIGINT, DATALENGTH(s.SnapshotJson)),
            @OriginalHash = HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX), s.SnapshotJson))
        FROM stg.MddMacSourceSnapshot AS s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.SnapshotId = @SnapshotId
          AND s.SnapshotJson IS NOT NULL
          AND s.SnapshotJsonGzip IS NULL;
        IF @OriginalText IS NULL
            THROW 51146, 'The selected lab snapshot is no longer a plaintext row.', 1;

        SET @CompressedPayload = COMPRESS(@OriginalText);
        SET @CompressedLength = CONVERT(BIGINT, DATALENGTH(@CompressedPayload));
        SET @CompressedHash = HASHBYTES(N'SHA2_256', @CompressedPayload);
        UPDATE stg.MddMacSourceSnapshot
        SET SnapshotJsonGzip = @CompressedPayload,
            SnapshotJson = NULL
        WHERE SnapshotId = @SnapshotId;
        IF @@ROWCOUNT <> 1
            THROW 51147, 'The lab snapshot update did not affect exactly one row.', 1;

        SELECT @TransactionId = transaction_id
        FROM sys.dm_tran_current_transaction();
        SELECT @PeakTransactionLogBytes = COALESCE(MAX(CONVERT(BIGINT, dt.database_transaction_log_bytes_used)), 0)
        FROM sys.dm_tran_database_transactions AS dt
        WHERE dt.transaction_id = @TransactionId
          AND dt.database_id = DB_ID();

        SELECT
            @DataAllocatedDuring = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @DataUsedDuring = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed')))
                                           * CONVERT(BIGINT, 8192), 0)
        FROM sys.database_files AS df
        WHERE df.type = 0;
        SELECT
            @TempdbAllocatedDuring = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @TempdbUsedDuring = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                            * CONVERT(BIGINT, 8192), 0)
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
        WHERE df.type = 0;
        SELECT @LabVolumeFreeDuring = MIN(CONVERT(BIGINT, vs.available_bytes))
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:';

        IF EXISTS
        (
            SELECT 1
            FROM stg.MddMacSourceSnapshot AS s
            INNER JOIN stg.vMddMacSourceSnapshotJson AS v ON v.SnapshotId = s.SnapshotId
            WHERE s.SnapshotId = @SnapshotId
              AND (s.SnapshotJson IS NOT NULL OR s.SnapshotJsonGzip IS NULL
                   OR DATALENGTH(CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip))) <> @OriginalLength
                   OR HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX),
                        CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip)))) <> @OriginalHash
                   OR DATALENGTH(s.SnapshotJsonGzip) <> @CompressedLength
                   OR HASHBYTES(N'SHA2_256', s.SnapshotJsonGzip) <> @CompressedHash
                   OR DATALENGTH(v.SnapshotJson) <> @OriginalLength
                   OR HASHBYTES(N'SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson)) <> @OriginalHash
                   OR ISJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip))) <> 1)
        )
            THROW 51148, 'Lab SnapshotJson lossless readback failed.', 1;

        SELECT
            @DataAllocatedAfter = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @DataUsedAfter = COALESCE(SUM(CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed')))
                                         * CONVERT(BIGINT, 8192), 0)
        FROM sys.database_files AS df
        WHERE df.type = 0;
        SELECT
            @TempdbAllocatedAfter = COALESCE(SUM(CONVERT(BIGINT, df.size)) * CONVERT(BIGINT, 8192), 0),
            @TempdbUsedAfter = COALESCE(SUM(CONVERT(BIGINT, fsu.allocated_extent_page_count))
                                            * CONVERT(BIGINT, 8192), 0)
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
        WHERE df.type = 0;
        SELECT @LabVolumeFreeAfter = MIN(CONVERT(BIGINT, vs.available_bytes))
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:';

        INSERT lab.SnapshotGzipCapacityMeasurement
        (
            SnapshotId, MeasuredAtUtc, OriginalPlaintextBytes,
            CompressedPayloadBytes, ExactPayloadSavingsBytes,
            PeakTransactionLogBytes, DataFileAllocationDeltaBytes,
            DataFileUsedDeltaBytes, TempdbAllocationDeltaBytes,
            TempdbUsedDeltaBytes, LabVolumeFreeBeforeBytes,
            LabVolumeFreeDuringBytes, LabVolumeFreeAfterBytes,
            ObservedLabVolumeConsumptionBytes
        )
        VALUES
        (
            @SnapshotId, SYSUTCDATETIME(), @OriginalLength,
            @CompressedLength, @OriginalLength - @CompressedLength,
            @PeakTransactionLogBytes,
            CASE WHEN @DataAllocatedDuring - @DataAllocatedBefore > 0
                 THEN @DataAllocatedDuring - @DataAllocatedBefore ELSE 0 END,
            @DataUsedDuring - @DataUsedBefore,
            CASE WHEN @TempdbAllocatedDuring - @TempdbAllocatedBefore > 0
                 THEN @TempdbAllocatedDuring - @TempdbAllocatedBefore ELSE 0 END,
            @TempdbUsedDuring - @TempdbUsedBefore,
            @LabVolumeFreeBefore, @LabVolumeFreeDuring, @LabVolumeFreeAfter,
            CASE WHEN @LabVolumeFreeBefore - @LabVolumeFreeDuring > 0
                 THEN @LabVolumeFreeBefore - @LabVolumeFreeDuring ELSE 0 END
        );
        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        CLOSE snapshot_cursor;
        DEALLOCATE snapshot_cursor;
        THROW;
    END CATCH;

    FETCH NEXT FROM snapshot_cursor INTO @SnapshotId;
END;

CLOSE snapshot_cursor;
DEALLOCATE snapshot_cursor;

SELECT
    N'capacity.snapshot_gzip_lab_per_snapshot' AS statement_id,
    SnapshotId,
    MeasuredAtUtc,
    OriginalPlaintextBytes AS original_plaintext_bytes,
    CompressedPayloadBytes AS compressed_payload_bytes,
    ExactPayloadSavingsBytes AS exact_payload_savings_bytes,
    PeakTransactionLogBytes AS peak_transaction_log_bytes,
    DataFileAllocationDeltaBytes AS data_file_allocation_delta_bytes,
    TempdbAllocationDeltaBytes AS tempdb_allocation_delta_bytes,
    ObservedLabVolumeConsumptionBytes AS observed_lab_volume_consumption_bytes
FROM lab.SnapshotGzipCapacityMeasurement
ORDER BY SnapshotId;

SELECT
    N'capacity.snapshot_gzip_lab_peak' AS statement_id,
    COUNT_BIG(*) AS measured_snapshot_rows,
    MAX(PeakTransactionLogBytes) AS peak_transaction_log_bytes,
    MAX(DataFileAllocationDeltaBytes) AS peak_data_file_allocation_bytes,
    MAX(TempdbAllocationDeltaBytes) AS peak_tempdb_allocation_bytes,
    MAX(ObservedLabVolumeConsumptionBytes) AS peak_observed_lab_volume_consumption_bytes,
    SUM(ExactPayloadSavingsBytes) AS exact_payload_savings_bytes,
    MIN(LabVolumeFreeDuringBytes) AS minimum_lab_volume_free_bytes,
    @SafetyFloorBytes AS production_required_windows_c_floor_bytes,
    N'NOT_A_PRODUCTION_C_FLOOR_MEASUREMENT; PRODUCTION_BACKFILL_RECHECKS_12_GIB' AS capacity_status
FROM lab.SnapshotGzipCapacityMeasurement;

SELECT
    N'capacity.snapshot_gzip_lab_resume_state' AS statement_id,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) AS total_snapshot_rows,
    (SELECT COUNT_BIG(*) FROM lab.SnapshotGzipCapacityMeasurement) AS measured_snapshot_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot
     WHERE SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL) AS remaining_plaintext_rows,
    CASE WHEN NOT EXISTS
         (
             SELECT 1
             FROM stg.MddMacSourceSnapshot AS s
             WHERE s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL
         ) THEN N'COMPLETE' ELSE N'RESUMABLE' END AS resume_status;
GO

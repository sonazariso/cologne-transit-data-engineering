/*
READ CAREFULLY: This backfill is OPT-IN and defaults to zero production writes.
Run only after validating optimize-snapshot-gzip-v1.sql on a restored copy,
verifying the existing backup + recovery point, checking free disk and log,
and confirming all Tiered50 writers are stopped. Does not SHRINK or DROP.
Run repeatedly with @Apply=1, @MaxRows=1 only after separate approval.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @Apply BIT = 0;            -- CHANGE only with explicit release approval.
DECLARE @MaxRows INT = 1;          -- Single-snapshot bound limits log spikes.
DECLARE @LockResult INT;
DECLARE @Migrated INT = 0;

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', 'U') IS NULL
   OR COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJsonGzip') IS NULL
    THROW 51100, 'Snapshot GZIP migration not installed.', 1;
IF OBJECT_ID(N'stg.vMddMacSourceSnapshotJson', 'V') IS NULL
    THROW 51105, 'Lossless SnapshotJson readback view is not installed.', 1;

IF @MaxRows NOT BETWEEN 1 AND 5
    THROW 51101, 'MaxRows must be between 1 and 5.', 1;

SELECT
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END) AS PlainRows,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS GzipRows,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN DATALENGTH(SnapshotJson) ELSE 0 END) AS PlaintextBytes,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN DATALENGTH(COMPRESS(SnapshotJson)) ELSE 0 END) AS EstimatedGzipBytes,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
             THEN DATALENGTH(SnapshotJson) - DATALENGTH(COMPRESS(SnapshotJson)) ELSE 0 END) AS EstimatedPayloadSavingsBytes,
    CONVERT(DECIMAL(19,3), SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
             THEN DATALENGTH(SnapshotJson) - DATALENGTH(COMPRESS(SnapshotJson)) ELSE 0 END) / 1073741824.0) AS EstimatedPayloadSavingsGiB,
    COUNT_BIG(*) AS TotalRows
FROM stg.MddMacSourceSnapshot;

IF @Apply = 0
BEGIN
    PRINT 'DRY RUN ONLY: NO SNAPSHOTS MODIFIED';
    RETURN;
END;

-- Require no production writer holding the lease; no blind mass rewrite.
IF EXISTS (
    SELECT 1 FROM ctl.MddMacProductionLease
    WHERE Status = 'ACTIVE'
)
    THROW 51102, 'Active production lease - stop collector first.', 1;

IF EXISTS (
    SELECT 1 FROM ctl.MddMacCollectorRun
    WHERE Status IN ('RUNNING', 'CLAIMED')
      AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
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

DECLARE @Batch TABLE (
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    OriginalLength BIGINT NOT NULL,
    OriginalHash VARBINARY(32) NOT NULL
);

BEGIN TRY
    BEGIN TRANSACTION;
    EXEC @LockResult = sys.sp_getapplock
        @Resource = N'CologneTransitIntelligence:SnapshotGzipBackfillV1',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 5000;
    IF @LockResult < 0 THROW 51103, 'Could not acquire snapshot backfill lock.', 1;

    INSERT @Batch (SnapshotId, OriginalLength, OriginalHash)
    SELECT TOP (@MaxRows)
        SnapshotId,
        DATALENGTH(SnapshotJson),
        HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), SnapshotJson))
    FROM stg.MddMacSourceSnapshot WITH (UPDLOCK, HOLDLOCK)
    WHERE SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
    ORDER BY SnapshotId;

    UPDATE s
    SET s.SnapshotJsonGzip = COMPRESS(s.SnapshotJson),
        s.SnapshotJson = NULL
    FROM stg.MddMacSourceSnapshot AS s
    INNER JOIN @Batch AS b ON b.SnapshotId = s.SnapshotId;
    SET @Migrated = @@ROWCOUNT;

    IF EXISTS (
        SELECT 1
        FROM @Batch AS b
        INNER JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId=b.SnapshotId
        WHERE s.SnapshotJson IS NOT NULL OR s.SnapshotJsonGzip IS NULL
           OR DATALENGTH(CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip)))
               <> b.OriginalLength
           OR HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX),
                    CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip))))
               <> b.OriginalHash
    )
        THROW 51104, 'Lossless roundtrip failed; backfill rolled back.', 1;
    COMMIT TRANSACTION;
    SELECT @Migrated AS MigratedRows;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

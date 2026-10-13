/* Strictly read-only health check. No full-Lob historical scans. */
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;
IF COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJsonGzip') IS NULL
    THROW 51110, 'GZIP column not installed.', 1;

SELECT
    COUNT_BIG(*) AS Snapshots,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END) AS LegacyPlain,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS Gzip,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL
           OR SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NOT NULL
             THEN 1 ELSE 0 END) AS CorruptRepresentation
FROM stg.MddMacSourceSnapshot;

DECLARE @Latest BIGINT = (SELECT MAX(SnapshotId) FROM stg.MddMacSourceSnapshot);
SELECT TOP (1)
    s.SnapshotId,
    s.ObservedAtUtc,
    s.CreatedAtUtc,
    CASE WHEN s.SnapshotJsonGzip IS NOT NULL THEN 'GZIP' ELSE 'LEGACY' END AS StorageMode,
    DATALENGTH(s.SnapshotJsonGzip) AS CompressedBytes,
    DATALENGTH(s.SnapshotJson) AS LegacyBytes,
    DATALENGTH(v.SnapshotJson) AS RestoredUnicodeBytes,
    ISJSON(v.SnapshotJson) AS RestoredIsJson,
    CASE WHEN s.SnapshotJsonGzip IS NOT NULL
         AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson))
             = HASHBYTES('SHA2_256', DECOMPRESS(s.SnapshotJsonGzip))
         THEN 1 ELSE 0 END AS ExactUnicodeRoundtrip
FROM stg.MddMacSourceSnapshot AS s
JOIN stg.vMddMacSourceSnapshotJson AS v ON v.SnapshotId=s.SnapshotId
WHERE s.SnapshotId=@Latest;
GO

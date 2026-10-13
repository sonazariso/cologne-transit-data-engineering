/*
    SNAPSHOT GZIP V1 — READ-ONLY INTEGRITY CHECK

    This validates every stored compressed snapshot without returning payload
    text. Rows that still retain both physical representations are compared
    by exact UTF-16LE bytes, length, and the compatible readback view. A
    compressed-only row was compared inside the lossless backfill transaction;
    here it is checked for a valid decompression, JSON shape, and view parity.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJsonGzip') IS NULL
    THROW 51110, 'GZIP column not installed.', 1;
IF OBJECT_ID(N'stg.vMddMacSourceSnapshotJson', 'V') IS NULL
    THROW 51111, 'Lossless SnapshotJson readback view is not installed.', 1;

IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    WHERE s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NULL
)
    THROW 51112, 'SnapshotJson physical representations violate the XOR contract.', 1;

/* Exact original UTF-16LE comparison for rows that still have both copies. */
IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    CROSS APPLY
    (
        SELECT DECOMPRESS(s.SnapshotJsonGzip) AS RestoredBytes
    ) AS d
    WHERE s.SnapshotJson IS NOT NULL
      AND s.SnapshotJsonGzip IS NOT NULL
      AND
      (
          DATALENGTH(s.SnapshotJson) <> DATALENGTH(d.RestoredBytes)
          OR HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), s.SnapshotJson))
             <> HASHBYTES('SHA2_256', d.RestoredBytes)
      )
)
    THROW 51113, 'SnapshotJson GZIP exact UTF-16LE parity failed.', 1;

IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    WHERE s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NOT NULL
)
    THROW 51115, 'SnapshotJson has duplicate plaintext and compressed representations.', 1;

/* Every compressed-only row must be decompressible, valid JSON, and exposed
   identically through the consumer readback view. */
IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    INNER JOIN stg.vMddMacSourceSnapshotJson AS v ON v.SnapshotId = s.SnapshotId
    CROSS APPLY
    (
        SELECT CONVERT(NVARCHAR(MAX), DECOMPRESS(s.SnapshotJsonGzip)) AS RestoredText
    ) AS d
    WHERE s.SnapshotJson IS NULL
      AND s.SnapshotJsonGzip IS NOT NULL
      AND
      (
          d.RestoredText IS NULL
          OR ISJSON(d.RestoredText) <> 1
          OR DATALENGTH(v.SnapshotJson) <> DATALENGTH(d.RestoredText)
          OR HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson))
             <> HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), d.RestoredText))
      )
)
    THROW 51114, 'Compressed-only SnapshotJson readback parity failed.', 1;

SELECT
    COUNT_BIG(*) AS Snapshots,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END) AS LegacyPlain,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS Gzip,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL
              OR SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NOT NULL
             THEN 1 ELSE 0 END) AS InvalidRepresentationRows,
    SUM(CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL
             THEN DATALENGTH(SnapshotJson) ELSE 0 END) AS LegacyPlainBytes,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL
             THEN DATALENGTH(SnapshotJsonGzip) ELSE 0 END) AS GzipBytes,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL
             THEN DATALENGTH(DECOMPRESS(SnapshotJsonGzip)) ELSE 0 END) AS RestoredUtf16Bytes
FROM stg.MddMacSourceSnapshot;

SELECT
    SUM(CASE WHEN s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS ExactDualRepresentationRows,
    SUM(CASE WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS CompressedOnlyRows,
    SUM(CASE WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL
             THEN DATALENGTH(s.SnapshotJsonGzip) ELSE 0 END) AS CompressedOnlyBytes,
    SUM(CASE WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL
             THEN DATALENGTH(DECOMPRESS(s.SnapshotJsonGzip)) - DATALENGTH(s.SnapshotJsonGzip) ELSE 0 END) AS PayloadBytesAvoided
FROM stg.MddMacSourceSnapshot AS s;
GO

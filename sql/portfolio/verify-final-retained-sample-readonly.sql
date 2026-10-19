/*
  Cologne Transit Data Engineering — FINAL SAMPLE READBACK (READ ONLY).
  Supplied for an operator to run on the real Windows SQL Server instance.
  Does NOT prove hashes, full dependency closure, valid backups or restore.
  NO DML, NO DDL, NO DBCC SHRINK. Safe for inspection, not data cleanup.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52901, 'Wrong database', 1;
IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
    THROW 52902, 'Source snapshot table does not exist', 1;

DECLARE @Keep TABLE (SnapshotId BIGINT PRIMARY KEY);
INSERT INTO @Keep (SnapshotId)
VALUES (22),(93),(185),(189),(207),(224),(225),(226),(228),(229);

SELECT
    SYSUTCDATETIME() AS MeasuredAtUtc,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) AS AllSourceSnapshotsNow,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot s JOIN @Keep k ON k.SnapshotId=s.SnapshotId) AS ApprovedSnapshotsFound,
    (SELECT COUNT_BIG(*) FROM @Keep k LEFT JOIN stg.MddMacSourceSnapshot s ON s.SnapshotId=k.SnapshotId WHERE s.SnapshotId IS NULL) AS ApprovedSnapshotsMissing;

SELECT k.SnapshotId, s.RunId, s.AuditId, s.TargetIdentity,
       s.ObservedAtUtc, s.PayloadHashSha256,
       CASE WHEN s.SnapshotId IS NULL THEN 'MISSING' ELSE 'PRESENT' END AS ReadbackStatus
FROM @Keep k
LEFT JOIN stg.MddMacSourceSnapshot s ON s.SnapshotId=k.SnapshotId
ORDER BY k.SnapshotId;

SELECT 'Call' AS EvidenceFamily, COUNT_BIG(*) AS RetainedRows FROM stg.MddMacCallObservation x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'Timing', COUNT_BIG(*) FROM stg.MddMacTimingObservation x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'Situation', COUNT_BIG(*) FROM stg.MddMacSituationEvidence x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'ServiceSection', COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'Attribute', COUNT_BIG(*) FROM stg.MddMacAttributeEvidence x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'ResponseError', COUNT_BIG(*) FROM stg.MddMacResponseError x JOIN @Keep k ON k.SnapshotId=x.SnapshotId
UNION ALL SELECT 'ParserGap', COUNT_BIG(*) FROM stg.MddMacParserGap x JOIN @Keep k ON k.SnapshotId=x.SnapshotId;

SELECT name AS LogicalFile, type_desc, CAST(size / 128.0 AS DECIMAL(18, 2)) AS FileSizeMiB,
       CAST(FILEPROPERTY(name, 'SpaceUsed') / 128.0 AS DECIMAL(18,2)) AS UsedMiB
FROM sys.database_files;

SELECT TOP (5)
       bs.backup_finish_date, bs.type, bs.is_copy_only, bs.has_backup_checksums,
       CAST(bs.backup_size/1024.0/1024.0 AS DECIMAL(18,2)) AS BackupSizeMiB,
       bmf.device_type
FROM msdb.dbo.backupset bs
LEFT JOIN msdb.dbo.backupmediafamily bmf ON bs.media_set_id=bmf.media_set_id
WHERE bs.database_name = DB_NAME() AND bs.type = 'D'
ORDER BY bs.backup_finish_date DESC;

/*
BOUNDED READ-ONLY STORAGE V3 AUDIT.

Run in SSMS/sqlcmd with the collector stopped.  This script reads metadata,
partition allocation, compression configuration, procedure metadata, volume
headroom, and small length-only samples.  It never selects raw payload text
and contains no INSERT, UPDATE, DELETE, DDL, DBCC, backup, or MDD operation.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

SELECT
    DB_NAME() AS database_name,
    @@SERVERNAME AS server_name,
    DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), N'Recovery') AS recovery_model;

SELECT
    name,
    type_desc,
    CONVERT(bigint, size) * 8192 AS allocated_bytes,
    CONVERT(bigint, FILEPROPERTY(name, N'SpaceUsed')) * 8192 AS used_bytes,
    growth,
    is_percent_growth,
    max_size
FROM sys.database_files
ORDER BY file_id;

SELECT DISTINCT
    volume_mount_point,
    total_bytes,
    available_bytes
FROM sys.database_files AS f
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), f.file_id) AS vs
ORDER BY volume_mount_point;

SELECT
    SCHEMA_NAME(o.schema_id) AS schema_name,
    o.name AS table_name,
    i.index_id,
    i.name AS index_name,
    i.type_desc,
    p.data_compression_desc,
    SUM(CONVERT(bigint, d.row_count)) AS row_count,
    SUM(CONVERT(bigint, d.reserved_page_count)) * 8192 AS reserved_bytes,
    SUM(CONVERT(bigint, d.used_page_count)) * 8192 AS used_bytes,
    SUM(CONVERT(bigint, d.in_row_data_page_count)) * 8192 AS in_row_bytes,
    SUM(CONVERT(bigint, d.lob_reserved_page_count)) * 8192 AS lob_reserved_bytes,
    SUM(CONVERT(bigint, d.row_overflow_reserved_page_count)) * 8192 AS row_overflow_reserved_bytes
FROM sys.objects AS o
INNER JOIN sys.indexes AS i ON i.object_id = o.object_id
INNER JOIN sys.dm_db_partition_stats AS d
    ON d.object_id = i.object_id AND d.index_id = i.index_id
INNER JOIN sys.partitions AS p
    ON p.object_id = d.object_id
   AND p.index_id = d.index_id
   AND p.partition_number = d.partition_number
WHERE o.type = N'U'
  AND SCHEMA_NAME(o.schema_id) = N'stg'
  AND o.name IN (
      N'MddMacSourceSnapshot', N'MddMacCallObservation',
      N'MddMacTimingObservation', N'MddMacSituationEvidence',
      N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
      N'MddMacResponseError', N'MddMacParserGap'
  )
GROUP BY o.schema_id, o.name, i.index_id, i.name, i.type_desc,
         p.data_compression_desc
ORDER BY o.name, i.index_id;

SELECT
    SCHEMA_NAME(t.schema_id) AS schema_name,
    t.name AS table_name,
    c.column_id,
    c.name AS column_name,
    ty.name AS data_type,
    c.max_length,
    c.is_nullable
FROM sys.tables AS t
INNER JOIN sys.columns AS c ON c.object_id = t.object_id
INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
WHERE SCHEMA_NAME(t.schema_id) = N'stg'
  AND t.name IN (
      N'MddMacSourceSnapshot', N'MddMacCallObservation',
      N'MddMacTimingObservation', N'MddMacSituationEvidence',
      N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
      N'MddMacResponseError', N'MddMacParserGap'
  )
  AND (ty.name IN (N'nvarchar', N'varbinary', N'varchar', N'xml') AND c.max_length = -1)
ORDER BY t.name, c.column_id;

SELECT
    SCHEMA_NAME(o.schema_id) AS schema_name,
    o.name AS object_name,
    o.type_desc,
    o.modify_date
FROM sys.objects AS o
WHERE (SCHEMA_NAME(o.schema_id) = N'stg' AND o.name IN (
           N'uspPersistMddMacSnapshot', N'vMddMacSourceSnapshotJson',
           N'vMddMacCallObservationJson', N'vMddMacTimingObservationJson',
           N'vMddMacSituationEvidenceJson', N'vMddMacServiceSectionEvidenceJson',
           N'vMddMacAttributeEvidenceJson', N'vMddMacParserGapJson',
           N'trg_MddMacTimingObservation_StorageV3',
           N'trg_MddMacSituationEvidence_StorageV3',
           N'trg_MddMacServiceSectionEvidence_StorageV3',
           N'trg_MddMacAttributeEvidence_StorageV3',
           N'trg_MddMacParserGap_StorageV3'
       ))
   OR (SCHEMA_NAME(o.schema_id) = N'analytics' AND o.name IN (
           N'vwMddMacRecordedAtTimeHistory', N'vwMddMacSourceFieldCoverage'
       ));

SELECT
    o.modify_date,
    HASHBYTES(N'SHA2_256', CONVERT(varbinary(max), OBJECT_DEFINITION(o.object_id))) AS procedure_sha256,
    LEN(OBJECT_DEFINITION(o.object_id)) AS definition_characters,
    CASE WHEN OBJECT_DEFINITION(o.object_id) LIKE N'%COMPRESS(@SnapshotJson)%' THEN 1 ELSE 0 END AS snapshot_gzip_marker,
    CASE WHEN OBJECT_DEFINITION(o.object_id) LIKE N'%COMPRESS(SourcePathsJson)%' THEN 1 ELSE 0 END AS observation_gzip_marker,
    CASE WHEN OBJECT_DEFINITION(o.object_id) LIKE N'%PERSISTED_PARSED_SNAPSHOT%' THEN 1 ELSE 0 END AS persistence_marker
FROM sys.objects AS o
WHERE o.object_id = OBJECT_ID(N'stg.uspPersistMddMacSnapshot');

-- Length-only bounded samples; no source text or raw JSON is returned.
SELECT TOP (20)
    SnapshotId,
    DATALENGTH(SnapshotJson) AS plain_bytes,
    DATALENGTH(SnapshotJsonGzip) AS gzip_bytes,
    CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN N'GZIP'
         WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN N'PLAIN'
         WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL THEN N'MISSING'
         ELSE N'BOTH' END AS storage_mode
FROM stg.MddMacSourceSnapshot
ORDER BY SnapshotId DESC;

SELECT TOP (20)
    CallObservationId,
    SnapshotId,
    DATALENGTH(SourcePathsJson) AS source_paths_plain_bytes,
    DATALENGTH(SourcePathsJsonGzip) AS source_paths_gzip_bytes,
    DATALENGTH(SourceEventJson) AS source_event_plain_bytes,
    DATALENGTH(SourceEventJsonGzip) AS source_event_gzip_bytes
FROM stg.MddMacCallObservation
ORDER BY CallObservationId DESC;

SELECT TOP (20)
    TimingObservationId,
    SnapshotId,
    DATALENGTH(SourcePathsJson) AS source_paths_plain_bytes,
    DATALENGTH(SourcePathsJsonGzip) AS source_paths_gzip_bytes,
    DATALENGTH(SourceEventIdentityBasisJson) AS basis_plain_bytes,
    DATALENGTH(SourceEventIdentityBasisJsonGzip) AS basis_gzip_bytes,
    DATALENGTH(SourceEventIdentityMissingFieldsJson) AS missing_plain_bytes,
    DATALENGTH(SourceEventIdentityMissingFieldsJsonGzip) AS missing_gzip_bytes,
    DATALENGTH(SourceEventIdentityAmbiguityJson) AS ambiguity_plain_bytes,
    DATALENGTH(SourceEventIdentityAmbiguityJsonGzip) AS ambiguity_gzip_bytes
FROM stg.MddMacTimingObservation
ORDER BY TimingObservationId DESC;
GO

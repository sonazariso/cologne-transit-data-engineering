/*
    S143 CURRENT-STATE AUDIT — READ ONLY

    This script returns metadata, counts, lengths, hashes, dependencies, and
    allocation only.  It never returns raw JSON, never changes isolation or
    configuration, and never writes a project row.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

SELECT
    DB_NAME() AS database_name,
    DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model,
    DATABASEPROPERTYEX(DB_NAME(), 'UserAccess') AS user_access,
    d.log_reuse_wait_desc,
    d.compatibility_level,
    d.collation_name
FROM sys.databases AS d
WHERE d.database_id = DB_ID();

SELECT
    df.file_id, df.name AS logical_file_name, df.type_desc, df.physical_name,
    df.size * 8192 AS allocated_bytes,
    FILEPROPERTY(df.name, 'SpaceUsed') * 8192 AS used_bytes,
    (df.size - FILEPROPERTY(df.name, 'SpaceUsed')) * 8192 AS internally_unallocated_bytes,
    df.growth, df.is_percent_growth, df.max_size,
    vs.volume_mount_point, vs.total_bytes AS volume_total_bytes,
    vs.available_bytes AS volume_available_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent
FROM sys.dm_db_log_space_usage
WHERE database_id = DB_ID();

SELECT
    s.name AS schema_name, o.name AS table_name,
    p.index_id, i.name AS index_name, i.type_desc AS index_type,
    p.rows, p.reserved_page_count, p.used_page_count,
    p.lob_reserved_page_count, p.lob_used_page_count,
    p.row_overflow_reserved_page_count, p.row_overflow_used_page_count,
    p.data_compression_desc
FROM sys.dm_db_partition_stats AS p
INNER JOIN sys.objects AS o ON o.object_id = p.object_id
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
LEFT JOIN sys.indexes AS i ON i.object_id = p.object_id AND i.index_id = p.index_id
WHERE s.name = N'stg'
  AND o.name IN
  (
      N'MddMacSourceSnapshot', N'MddMacCallObservation',
      N'MddMacTimingObservation', N'MddMacSituationEvidence',
      N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
      N'MddMacResponseError', N'MddMacParserGap',
      N'MddMacCompactObservationEnvelope'
  )
ORDER BY o.name, p.index_id;

SELECT
    s.name AS schema_name, o.name AS object_name, c.column_id,
    c.name AS column_name, ty.name AS data_type, c.max_length,
    c.is_nullable, c.is_identity, c.is_computed
FROM sys.columns AS c
INNER JOIN sys.objects AS o ON o.object_id = c.object_id
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
WHERE s.name IN (N'stg', N'analytics')
  AND (o.name LIKE N'MddMac%' OR o.name LIKE N'vwMddMac%' OR o.name LIKE N'vMddMac%')
ORDER BY s.name, o.name, c.column_id;

SELECT
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS parent_schema,
    OBJECT_NAME(fk.parent_object_id) AS parent_object,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS referenced_schema,
    OBJECT_NAME(fk.referenced_object_id) AS referenced_object,
    fk.is_disabled, fk.is_not_trusted
FROM sys.foreign_keys AS fk
WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) IN (N'stg', N'ctl')
   OR OBJECT_SCHEMA_NAME(fk.referenced_object_id) IN (N'stg', N'ctl')
ORDER BY fk.name;

SELECT
    referencing_schema_name, referencing_entity_name,
    referenced_schema_name, referenced_entity_name, referenced_minor_name
FROM sys.sql_expression_dependencies
WHERE referenced_schema_name IN (N'stg', N'analytics', N'ctl')
  AND (referenced_entity_name LIKE N'MddMac%'
       OR referenced_entity_name LIKE N'vMddMac%'
       OR referenced_entity_name LIKE N'vwMddMac%')
ORDER BY referencing_schema_name, referencing_entity_name;

SELECT
    s.name AS schema_name, o.name AS object_name, o.type_desc,
    CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), OBJECT_DEFINITION(o.object_id))), 2)
        AS definition_sha256,
    CASE WHEN OBJECT_DEFINITION(o.object_id) LIKE N'%COMPRESS%' THEN 1 ELSE 0 END AS has_compress,
    CASE WHEN OBJECT_DEFINITION(o.object_id) LIKE N'%DECOMPRESS%' THEN 1 ELSE 0 END AS has_decompress
FROM sys.objects AS o
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name IN (N'stg', N'analytics')
  AND (o.name LIKE N'%MddMac%' OR o.name LIKE N'%MddMac%')
  AND o.type IN ('P', 'V', 'TR');

SELECT
    'MddMacSourceSnapshot' AS table_name,
    COUNT_BIG(*) AS row_count,
    SUM(CASE WHEN SnapshotJson IS NOT NULL THEN 1 ELSE 0 END) AS plaintext_payload_rows,
    SUM(CASE WHEN SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS gzip_payload_rows,
    SUM(CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END) AS missing_payload_rows,
    SUM(CASE WHEN SnapshotJson IS NOT NULL THEN DATALENGTH(SnapshotJson) ELSE 0 END) AS plaintext_bytes,
    SUM(CASE WHEN SnapshotJsonGzip IS NOT NULL THEN DATALENGTH(SnapshotJsonGzip) ELSE 0 END) AS gzip_bytes
FROM stg.MddMacSourceSnapshot;

IF COL_LENGTH(N'stg.MddMacCallObservation', N'SourcePathsJsonGzip') IS NOT NULL
   AND COL_LENGTH(N'stg.MddMacCallObservation', N'SourceEventJsonGzip') IS NOT NULL
BEGIN
    EXEC sys.sp_executesql N'
        SELECT
            ''MddMacCallObservation'' AS table_name, COUNT_BIG(*) AS row_count,
            SUM(CASE WHEN SourcePathsJson IS NOT NULL THEN 1 ELSE 0 END) AS paths_plaintext_rows,
            SUM(CASE WHEN SourcePathsJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS paths_gzip_rows,
            SUM(CASE WHEN SourceEventJson IS NOT NULL THEN 1 ELSE 0 END) AS event_plaintext_rows,
            SUM(CASE WHEN SourceEventJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS event_gzip_rows
        FROM stg.MddMacCallObservation;';
END
ELSE
    SELECT CAST('MddMacCallObservation' AS VARCHAR(80)) AS table_name,
           CAST('V2_GZIP_COLUMNS_NOT_INSTALLED' AS VARCHAR(80)) AS status;

IF COL_LENGTH(N'stg.MddMacTimingObservation', N'SourcePathsJsonGzip') IS NOT NULL
   AND COL_LENGTH(N'stg.MddMacTimingObservation', N'SourceEventIdentityBasisJsonGzip') IS NOT NULL
   AND COL_LENGTH(N'stg.MddMacTimingObservation', N'SourceEventIdentityMissingFieldsJsonGzip') IS NOT NULL
   AND COL_LENGTH(N'stg.MddMacTimingObservation', N'SourceEventIdentityAmbiguityJsonGzip') IS NOT NULL
BEGIN
    EXEC sys.sp_executesql N'
        SELECT
            ''MddMacTimingObservation'' AS table_name, COUNT_BIG(*) AS row_count,
            SUM(CASE WHEN SourcePathsJson IS NOT NULL THEN 1 ELSE 0 END) AS paths_plaintext_rows,
            SUM(CASE WHEN SourcePathsJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS paths_gzip_rows,
            SUM(CASE WHEN SourceEventIdentityBasisJson IS NOT NULL THEN 1 ELSE 0 END) AS basis_plaintext_rows,
            SUM(CASE WHEN SourceEventIdentityBasisJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS basis_gzip_rows,
            SUM(CASE WHEN SourceEventIdentityMissingFieldsJson IS NOT NULL THEN 1 ELSE 0 END) AS missing_plaintext_rows,
            SUM(CASE WHEN SourceEventIdentityMissingFieldsJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS missing_gzip_rows,
            SUM(CASE WHEN SourceEventIdentityAmbiguityJson IS NOT NULL THEN 1 ELSE 0 END) AS ambiguity_plaintext_rows,
            SUM(CASE WHEN SourceEventIdentityAmbiguityJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS ambiguity_gzip_rows
        FROM stg.MddMacTimingObservation;';
END
ELSE
    SELECT CAST('MddMacTimingObservation' AS VARCHAR(80)) AS table_name,
           CAST('V2_GZIP_COLUMNS_NOT_INSTALLED' AS VARCHAR(80)) AS status;

IF OBJECT_ID(N'stg.MddMacCompactObservationEnvelope', N'U') IS NOT NULL
BEGIN
    SELECT VerificationStatus, COUNT_BIG(*) AS envelope_count,
           SUM(EnvelopeLogicalBytes) AS logical_bytes,
           SUM(EnvelopeCompressedBytes) AS compressed_bytes,
           MIN(VerifiedAtUtc) AS first_verified_at_utc,
           MAX(VerifiedAtUtc) AS last_verified_at_utc
    FROM stg.MddMacCompactObservationEnvelope
    GROUP BY VerificationStatus;
    SELECT * FROM stg.MddMacCompactStorageState;
    SELECT * FROM stg.MddMacCompactMigration;
END;
ELSE
    SELECT CAST('NOT_INSTALLED' AS VARCHAR(20)) AS compact_storage_status;
GO

/*
    COLOGNE TRANSIT INTELLIGENCE — FULL-HISTORY CAPACITY LAB CAPTURE

    Run this file only on an isolated SQL Server restored from the verified
    October 14 recovery point.  The lab copy must use an existing non-C:
    SQL data/log volume.  The production instance DataAnalyst-VM is refused.

    Execute once immediately before the full-history lab migration with
    @CapturePhase = 'BEFORE', and once immediately after it with
    @CapturePhase = 'AFTER'.  This file is read-only.  It does not infer a
    compact-envelope allocation from string lengths and it does not write a
    capacity plan.  The operator compares the two captured result sets and
    combines them with the existing migration output and
    estimate-storage-v3-compression.sql result for the signed plan.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @CapturePhase varchar(10) = 'BEFORE';
DECLARE @ServerName nvarchar(128) = CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName'));
IF @CapturePhase NOT IN ('BEFORE', 'AFTER')
    THROW 51440, 'Capture phase must be BEFORE or AFTER.', 1;
IF @ServerName = N'DataAnalyst-VM' OR @ServerName LIKE N'DataAnalyst-VM\%'
    THROW 51441, 'The full-history capacity capture cannot run on the production instance.', 1;

SELECT
    N'capacity.database_identity' AS statement_id,
    @CapturePhase AS capture_phase,
    DB_NAME() AS database_name,
    @ServerName AS server_name,
    DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), N'UserAccess') AS user_access,
    CONVERT(int, DATABASEPROPERTYEX(DB_NAME(), N'CompatibilityLevel')) AS compatibility_level,
    SUSER_SNAME() AS effective_login,
    SYSUTCDATETIME() AS measurement_timestamp_utc;

SELECT
    CASE WHEN @CapturePhase = 'BEFORE'
         THEN N'capacity.database_file_snapshot'
         ELSE N'capacity.database_file_snapshot' END AS statement_id,
    @CapturePhase AS capture_phase,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS volume_available_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'capacity.database_files' AS statement_id,
    @CapturePhase AS capture_phase,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS available_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    CASE WHEN @CapturePhase = 'BEFORE'
         THEN N'capacity.sample_allocation_before'
         ELSE N'capacity.sample_allocation_after' END AS statement_id,
    @CapturePhase AS capture_phase,
    SUM(CASE WHEN df.type = 0
             THEN CONVERT(bigint, df.size) * CONVERT(bigint, 8192)
             ELSE CONVERT(bigint, 0) END) AS database_file_allocated_bytes,
    SUM(CASE WHEN df.type = 1
             THEN CONVERT(bigint, df.size) * CONVERT(bigint, 8192)
             ELSE CONVERT(bigint, 0) END) AS log_file_allocated_bytes,
    (SELECT SUM(CONVERT(bigint, tdf.size) * CONVERT(bigint, 8192))
     FROM tempdb.sys.database_files AS tdf) AS tempdb_file_allocated_bytes,
    (SELECT CONVERT(bigint, used_log_space_in_bytes)
     FROM sys.dm_db_log_space_usage) AS transaction_log_used_bytes,
    (SELECT MIN(CONVERT(bigint, vs.volume_available_bytes))
     FROM
     (
         SELECT vs.available_bytes AS volume_available_bytes
         FROM sys.database_files AS cdf
         CROSS APPLY sys.dm_os_volume_stats(DB_ID(), cdf.file_id) AS vs
         WHERE vs.volume_mount_point LIKE N'C:%'
     ) AS vs) AS windows_c_available_bytes
FROM sys.database_files AS df;

SELECT
    N'capacity.snapshot_population' AS statement_id,
    @CapturePhase AS capture_phase,
    COUNT_BIG(*) AS snapshot_count,
    MIN(SnapshotId) AS first_snapshot_id,
    MAX(SnapshotId) AS last_snapshot_id
FROM stg.MddMacSourceSnapshot;

/* The exact full-history run uses every current SnapshotId, in this order. */
SELECT
    N'capacity.sample_selection' AS statement_id,
    @CapturePhase AS capture_phase,
    SnapshotId,
    ROW_NUMBER() OVER (ORDER BY SnapshotId) AS selection_ordinal
FROM stg.MddMacSourceSnapshot
ORDER BY SnapshotId;

SELECT
    N'capacity.compact_object_allocation' AS statement_id,
    @CapturePhase AS capture_phase,
    sch.name AS schema_name,
    obj.name AS object_name,
    idx.index_id,
    idx.name AS index_name,
    idx.type_desc AS index_type,
    SUM(CONVERT(bigint, ps.row_count)) AS row_count,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes,
    SUM(CONVERT(bigint, ps.lob_reserved_page_count)) * CONVERT(bigint, 8192) AS lob_reserved_bytes
FROM sys.dm_db_partition_stats AS ps
INNER JOIN sys.objects AS obj ON obj.object_id = ps.object_id
INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
INNER JOIN sys.indexes AS idx
    ON idx.object_id = ps.object_id AND idx.index_id = ps.index_id
WHERE sch.name = N'stg'
  AND obj.name IN
  (
      N'MddMacCompactObservationEnvelope',
      N'MddMacCompactMigration',
      N'MddMacCompactMigrationUnit'
  )
GROUP BY sch.name, obj.name, idx.index_id, idx.name, idx.type_desc
ORDER BY obj.name, idx.index_id;

SELECT
    N'capacity.transaction_log' AS statement_id,
    @CapturePhase AS capture_phase,
    total_log_size_in_bytes,
    used_log_space_in_bytes,
    CONVERT(bigint, total_log_size_in_bytes - used_log_space_in_bytes) AS free_log_bytes,
    used_log_space_in_percent,
    d.log_reuse_wait_desc
FROM sys.dm_db_log_space_usage AS l
INNER JOIN sys.databases AS d ON d.database_id = DB_ID();

SELECT
    N'capacity.tempdb' AS statement_id,
    @CapturePhase AS capture_phase,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, fsu.allocated_extent_page_count) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS volume_available_bytes
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu
    ON fsu.database_id = DB_ID(N'tempdb') AND fsu.file_id = df.file_id
CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'capacity.rollback' AS statement_id,
    @CapturePhase AS capture_phase,
    COUNT_BIG(DISTINCT st.session_id) AS active_user_transaction_sessions,
    COUNT_BIG(*) AS active_transaction_rows,
    COALESCE(SUM(CONVERT(bigint, dt.database_transaction_log_bytes_used)), 0)
        AS active_transaction_log_bytes_used,
    N'LAB_ROLLBACK_RESERVE_MUST_BE_CAPTURED_BY_ABORTING_A_SEPARATE_BOUNDED_SAMPLE_TRANSACTION' AS rollback_measurement_status
FROM sys.dm_tran_session_transactions AS st
INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
LEFT JOIN sys.dm_tran_database_transactions AS dt ON dt.transaction_id = st.transaction_id
WHERE st.session_id <> @@SPID AND es.is_user_process = 1;

SELECT
    N'capacity.autogrowth' AS statement_id,
    @CapturePhase AS capture_phase,
    N'CologneTransitIntelligence' AS database_scope,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    CASE WHEN df.max_size < 0 THEN NULL
         ELSE (CONVERT(bigint, df.max_size) - CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) END
        AS remaining_file_growth_bytes
FROM sys.database_files AS df
UNION ALL
SELECT
    N'capacity.autogrowth', @CapturePhase, N'tempdb', df.file_id, df.name,
    df.type_desc, CONVERT(bigint, df.size) * CONVERT(bigint, 8192),
    CONVERT(bigint, df.growth), CONVERT(bit, df.is_percent_growth),
    CONVERT(bigint, df.max_size),
    CASE WHEN df.max_size < 0 THEN NULL
         ELSE (CONVERT(bigint, df.max_size) - CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) END
FROM tempdb.sys.database_files AS df
ORDER BY database_scope, file_id;

SELECT
    N'capacity.migration_state' AS statement_id,
    @CapturePhase AS capture_phase,
    MigrationId,
    MigrationKind,
    Status,
    VerifiedUnitCount,
    QuarantinedUnitCount,
    LastSnapshotId,
    CompletedAtUtc
FROM stg.MddMacCompactMigration
WHERE MigrationKind = N'HISTORICAL'
ORDER BY StartedAtUtc, MigrationId;

SELECT
    N'capacity.sample_observation_sizes' AS statement_id,
    @CapturePhase AS capture_phase,
    e.SnapshotId,
    e.SourceSnapshotRowCount,
    e.CallRowCount,
    e.TimingRowCount,
    e.SituationRowCount,
    e.ServiceSectionRowCount,
    e.AttributeRowCount,
    e.ResponseErrorRowCount,
    e.ParserGapRowCount,
    e.EnvelopeLogicalBytes,
    e.EnvelopeCompressedBytes,
    DATALENGTH(e.EnvelopeJsonGzip) AS stored_gzip_bytes
FROM stg.MddMacCompactObservationEnvelope AS e
WHERE e.VerificationStatus = N'VERIFIED'
ORDER BY e.SnapshotId;
GO

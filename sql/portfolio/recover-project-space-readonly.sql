/*
    COLOGNE TRANSIT INTELLIGENCE — PHYSICAL SPACE RECOVERY REVIEW

    READ ONLY.  Run after the ten-snapshot retention result has been verified.
    This measures the current database files, internal MDF free pages, C:
    volume bytes, LDF/tempdb state, active requests, and limited index
    fragmentation across all data files.  It also records the complete
    schema/object inventory boundary: physical tables may have their DATA
    reduced, while schemas, columns, indexes, keys, foreign keys, views,
    functions, procedures, and triggers are preserved.  It does not shrink,
    rebuild, change growth settings, or claim that the 80 percent target has
    been achieved.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52300, 'Wrong database. Expected CologneTransitIntelligence.', 1;

SELECT
    N'portfolio.recovery_schema_object_contract' AS statement_id,
    (SELECT COUNT_BIG(*) FROM sys.schemas
     WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest'))
        AS user_schema_count,
    COUNT_BIG(CASE WHEN obj.type = 'U' THEN 1 END) AS physical_table_count,
    COUNT_BIG(CASE WHEN obj.type = 'V' THEN 1 END) AS view_count,
    COUNT_BIG(CASE WHEN obj.type IN ('P', 'PC') THEN 1 END) AS procedure_count,
    COUNT_BIG(CASE WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN 1 END)
        AS function_count,
    COUNT_BIG(CASE WHEN obj.type = 'TR' THEN 1 END) AS trigger_count,
    N'PRESERVE_ALL_OBJECT_DEFINITIONS; DATA_ONLY_SPACE_RECOVERY'
        AS recovery_boundary
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0;

SELECT
    N'portfolio.recovery_file_before' AS statement_id,
    df.file_id,
    df.name AS logical_file_name,
    df.physical_name,
    df.type_desc,
    CONVERT(bigint, df.size) AS allocated_pages,
    CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) AS used_pages,
    CONVERT(bigint, df.size)
        - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) AS internal_free_pages,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) * CONVERT(bigint, 8192)
        AS used_bytes,
    (CONVERT(bigint, df.size)
        - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))) * CONVERT(bigint, 8192)
        AS internal_free_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS windows_free_bytes,
    CONVERT(bigint, vs.total_bytes) AS windows_total_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'portfolio.recovery_database_file_total' AS statement_id,
    SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) AS database_file_allocated_bytes,
    SUM(CASE WHEN df.type = 0
             THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))
             ELSE CONVERT(bigint, 0) END) * CONVERT(bigint, 8192) AS data_used_bytes,
    SUM(CASE WHEN df.type = 0
             THEN CONVERT(bigint, df.size)
                    - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))
             ELSE CONVERT(bigint, 0) END) * CONVERT(bigint, 8192) AS data_internal_free_bytes,
    N'PROJECT_FILE_BASELINE; VERIFY WHICH FILES ARE IN THE 80 PERCENT SCOPE'
        AS measurement_basis
FROM sys.database_files AS df;

SELECT
    N'portfolio.recovery_data_domain_allocations' AS statement_id,
    sch.name AS schema_name,
    tab.name AS table_name,
    MAX(CASE
        WHEN LOWER(sch.name) LIKE N'%gtfs%'
          OR LOWER(tab.name) LIKE N'%gtfs%' THEN N'GTFS'
        WHEN LOWER(sch.name) IN (N'dw', N'dwh', N'warehouse')
          OR LOWER(tab.name) LIKE N'%warehouse%'
          OR LOWER(tab.name) LIKE N'fact%'
          OR LOWER(tab.name) LIKE N'dim%' THEN N'WAREHOUSE'
        WHEN LOWER(sch.name) IN (N'ctl', N'stg', N'src', N'wrk', N'core', N'analytics')
          OR LOWER(tab.name) LIKE N'%lineage%'
          OR LOWER(tab.name) LIKE N'%config%'
          OR LOWER(tab.name) LIKE N'%audit%'
          OR LOWER(tab.name) LIKE N'%run%'
          OR LOWER(tab.name) LIKE N'%claim%'
          OR LOWER(tab.name) LIKE N'%lease%' THEN N'ETL_LINEAGE_OR_SOURCE'
        ELSE N'OTHER'
    END) AS data_domain,
    SUM(CONVERT(bigint, ps.row_count)) AS physical_rows,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192)
        AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192)
        AS used_bytes,
    N'DATA_ONLY_REVIEW; STRUCTURE_PRESERVED' AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.tables AS tab ON tab.object_id = ps.object_id
JOIN sys.schemas AS sch ON sch.schema_id = tab.schema_id
WHERE ps.index_id IN (0, 1)
GROUP BY sch.name, tab.name
ORDER BY reserved_bytes DESC, sch.name, tab.name;

SELECT
    N'portfolio.recovery_log_space' AS statement_id,
    total_log_size_in_bytes,
    used_log_space_in_bytes,
    used_log_space_in_percent,
    recovery_model
FROM sys.dm_db_log_space_usage
CROSS JOIN
(
    SELECT CONVERT(nvarchar(60), DATABASEPROPERTYEX(DB_NAME(), N'Recovery'))
        AS recovery_model
) AS m;

SELECT
    N'portfolio.recovery_tempdb_space' AS statement_id,
    SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) AS tempdb_allocated_bytes,
    SUM(CONVERT(bigint, COALESCE(fsu.unallocated_extent_page_count, 0)))
        * CONVERT(bigint, 8192) AS tempdb_unallocated_bytes,
    SUM(CONVERT(bigint, COALESCE(fsu.version_store_reserved_page_count, 0)))
        * CONVERT(bigint, 8192) AS tempdb_version_store_bytes,
    SUM(CONVERT(bigint, COALESCE(fsu.user_object_reserved_page_count, 0)))
        * CONVERT(bigint, 8192) AS tempdb_user_object_bytes
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT
    N'portfolio.recovery_active_requests' AS statement_id,
    req.session_id,
    req.status,
    req.command,
    req.wait_type,
    req.blocking_session_id,
    req.database_id,
    ses.login_name,
    ses.host_name,
    ses.program_name,
    req.start_time
FROM sys.dm_exec_requests AS req
JOIN sys.dm_exec_sessions AS ses ON ses.session_id = req.session_id
WHERE req.session_id <> @@SPID
  AND req.database_id = DB_ID()
ORDER BY req.start_time, req.session_id;

SELECT
    N'portfolio.recovery_collector_state' AS statement_id,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
     WHERE Status IN (N'RUNNING', N'CLAIMED')
       AND CollectionContext IN
           (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION'))
        AS active_collector_run_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
     WHERE Status = N'ACTIVE') AS active_production_lease_rows,
    N'EXPECTED_ZERO; COLLECTORS REMAIN DISABLED' AS expected_state;

SELECT
    N'portfolio.recovery_index_fragmentation' AS statement_id,
    OBJECT_SCHEMA_NAME(ips.object_id) AS schema_name,
    OBJECT_NAME(ips.object_id) AS object_name,
    ips.index_id,
    ips.index_type_desc,
    ips.page_count,
    ips.avg_fragmentation_in_percent,
    ips.avg_page_space_used_in_percent,
    N'LIMITED READ_ONLY REVIEW; NOT A REBUILD AUTHORIZATION' AS measurement_basis
FROM sys.dm_db_index_physical_stats
(
    DB_ID(), NULL, NULL, NULL, 'LIMITED'
) AS ips
WHERE ips.object_id IN
(
    OBJECT_ID(N'stg.MddMacSourceSnapshot'),
    OBJECT_ID(N'stg.MddMacTimingObservation'),
    OBJECT_ID(N'stg.MddMacCallObservation'),
    OBJECT_ID(N'stg.MddMacSituationEvidence'),
    OBJECT_ID(N'stg.MddMacServiceSectionEvidence'),
    OBJECT_ID(N'stg.MddMacAttributeEvidence'),
    OBJECT_ID(N'stg.MddMacResponseError'),
    OBJECT_ID(N'stg.MddMacParserGap')
)
ORDER BY schema_name, object_name, ips.index_id;

SELECT
    N'portfolio.recovery_decision' AS statement_id,
    N'REVIEW_REQUIRED' AS status,
    N'Internal MDF free pages are not Windows recovery. A separately reviewed
      DBCC SHRINKFILE may be considered only after the ten-row data result,
      fresh C: reserve, exact logical data-file name, consumer review, and
      before/after measurement are approved.' AS required_action;

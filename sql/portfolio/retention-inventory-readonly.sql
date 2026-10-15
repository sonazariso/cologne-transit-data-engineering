/*
    COLOGNE TRANSIT INTELLIGENCE — PORTFOLIO RETENTION INVENTORY

    READ ONLY.  Run manually in SSMS under an authorized read-only/admin
    account while every collector is disabled.  This file does not select a
    retention set, delete rows, create a database, take a backup, shrink a
    file, or change collector state.

    The result sets are evidence inputs for the separately reviewed
    select-ten-snapshots-readonly.sql and retention-*-apply.sql files.  The
    database name is deliberately exact: do not adapt it by guesswork.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52000, 'Wrong database. Expected CologneTransitIntelligence.', 1;

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
    OR OBJECT_ID(N'ctl.MddMacCollectorRun', N'U') IS NULL
    OR OBJECT_ID(N'ctl.MddMacTargetAudit', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacCallObservation', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacSituationEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacServiceSectionEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacAttributeEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacResponseError', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacParserGap', N'U') IS NULL
    THROW 52001, 'Required ctl/stg retention tables are missing.', 1;

SELECT
    N'portfolio.database_identity' AS statement_id,
    DB_NAME() AS database_name,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName')) AS server_name,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ProductVersion')) AS product_version,
    DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), N'UserAccess') AS user_access,
    DATABASEPROPERTYEX(DB_NAME(), N'Recovery') AS recovery_model,
    SYSDATETIMEOFFSET() AS measured_at_local,
    SYSUTCDATETIME() AS measured_at_utc,
    SUSER_SNAME() AS effective_login,
    N'READ_ONLY_INVENTORY; NO_RETENTION_SET_SELECTED' AS measurement_basis;

SELECT
    N'portfolio.database_files' AS statement_id,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
    CONVERT(bigint, df.size) AS allocated_pages,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))
         ELSE NULL END AS used_pages,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, df.size)
            - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))
         ELSE NULL END AS internal_free_pages,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CASE WHEN df.type = 0
         THEN (CONVERT(bigint, df.size)
             - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))) * CONVERT(bigint, 8192)
         ELSE NULL END AS internal_free_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CASE WHEN df.is_percent_growth = 0
         THEN CONVERT(bigint, df.growth) * CONVERT(bigint, 8192)
         ELSE NULL END AS fixed_growth_bytes,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    CASE WHEN df.max_size < 0 THEN NULL
         ELSE CONVERT(bigint, df.max_size) * CONVERT(bigint, 8192) END AS max_size_bytes,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS windows_volume_available_bytes,
    CONVERT(bigint, vs.total_bytes) AS windows_volume_total_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'portfolio.table_allocations' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CASE WHEN ps.index_id IN (0, 1)
             THEN CONVERT(bigint, ps.row_count) ELSE CONVERT(bigint, 0) END) AS base_rows,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes_all_indexes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes_all_indexes,
    SUM(CONVERT(bigint, ps.reserved_page_count) - CONVERT(bigint, ps.used_page_count))
        * CONVERT(bigint, 8192) AS unused_reserved_bytes_all_indexes
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE sch.name IN (N'ctl', N'stg', N'dw', N'analytics')
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes_all_indexes DESC, sch.name, obj.name;

SELECT
    N'portfolio.snapshot_population' AS statement_id,
    COUNT_BIG(*) AS snapshot_rows,
    MIN(s.SnapshotId) AS first_snapshot_id,
    MAX(s.SnapshotId) AS last_snapshot_id,
    MIN(s.ObservedAtUtc) AS first_observed_at_utc,
    MAX(s.ObservedAtUtc) AS last_observed_at_utc,
    SUM(CASE WHEN s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END)
        AS plaintext_rows,
    SUM(CASE WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)
        AS gzip_rows,
    SUM(CASE WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NULL
                  OR s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NOT NULL
             THEN 1 ELSE 0 END) AS invalid_representation_rows,
    SUM(CONVERT(bigint, DATALENGTH(s.SnapshotJson))) AS plaintext_utf16_bytes,
    SUM(CONVERT(bigint, DATALENGTH(s.SnapshotJsonGzip))) AS gzip_bytes
FROM stg.MddMacSourceSnapshot AS s;

SELECT
    N'portfolio.successful_persisted_statuses' AS statement_id,
    r.CollectionContext,
    r.Status AS run_status,
    a.Status AS audit_status,
    a.HttpStatus,
    a.ParserOutcome,
    a.PersistenceOutcome,
    COUNT_BIG(*) AS snapshot_count,
    MIN(s.ObservedAtUtc) AS earliest_observed_at_utc,
    MAX(s.ObservedAtUtc) AS latest_observed_at_utc
FROM stg.MddMacSourceSnapshot AS s
JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
JOIN ctl.MddMacCollectorRun AS r ON r.RunId = s.RunId
GROUP BY r.CollectionContext, r.Status, a.Status, a.HttpStatus,
         a.ParserOutcome, a.PersistenceOutcome
ORDER BY snapshot_count DESC, earliest_observed_at_utc, r.CollectionContext;

SELECT
    N'portfolio.foreign_keys' AS statement_id,
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS child_column,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS parent_column,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) IN (N'ctl', N'stg')
   OR OBJECT_SCHEMA_NAME(fk.referenced_object_id) IN (N'ctl', N'stg')
ORDER BY child_schema, child_table, foreign_key_name, fkc.constraint_column_id;

SELECT
    N'portfolio.sql_module_dependencies' AS statement_id,
    OBJECT_SCHEMA_NAME(dep.referencing_id) AS referencing_schema,
    OBJECT_NAME(dep.referencing_id) AS referencing_object,
    o.type_desc AS referencing_type,
    OBJECT_SCHEMA_NAME(dep.referenced_id) AS referenced_schema,
    OBJECT_NAME(dep.referenced_id) AS referenced_object,
    COL_NAME(dep.referenced_id, dep.referenced_minor_id) AS referenced_column
FROM sys.sql_expression_dependencies AS dep
JOIN sys.objects AS o ON o.object_id = dep.referencing_id
WHERE dep.referenced_id IN
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
ORDER BY referencing_schema, referencing_object, referenced_schema, referenced_object;

IF DB_ID(N'msdb') IS NOT NULL
BEGIN
    SELECT
        N'portfolio.sql_agent_job_dependencies' AS statement_id,
        j.name AS job_name,
        js.step_id,
        js.step_name,
        js.database_name,
        CASE WHEN js.command LIKE N'%Snapshot%'
               OR js.command LIKE N'%MddMac%'
             THEN 1 ELSE 0 END AS mentions_snapshot_pipeline,
        js.command
    FROM msdb.dbo.sysjobs AS j
    JOIN msdb.dbo.sysjobsteps AS js ON js.job_id = j.job_id
    WHERE js.command LIKE N'%MddMac%'
       OR js.command LIKE N'%Snapshot%'
       OR js.command LIKE N'%CologneTransitIntelligence%';
END;

SELECT
    N'portfolio.external_consumer_review' AS statement_id,
    N'BLOCKED_EXTERNAL_CONSUMER_REVIEW' AS status,
    N'SQL metadata cannot prove the absence of direct clients, Power BI extracts,
      notebooks, or manually scheduled scripts. Operator must attach current
      consumer evidence before physical payload changes.' AS required_action;

SELECT
    N'portfolio.collector_state_review' AS statement_id,
    COUNT_BIG(CASE WHEN r.Status IN (N'RUNNING', N'CLAIMED') THEN 1 END)
        AS active_collector_run_rows,
    COUNT_BIG(CASE WHEN l.Status = N'ACTIVE' THEN 1 END)
        AS active_production_lease_rows,
    N'EXPECTED_ZERO; COLLECTORS MUST REMAIN DISABLED' AS expected_state
FROM ctl.MddMacCollectorRun AS r
FULL OUTER JOIN ctl.MddMacProductionLease AS l
    ON l.Status = N'ACTIVE';

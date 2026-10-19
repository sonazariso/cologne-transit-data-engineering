/*
    COLOGNE TRANSIT INTELLIGENCE — PORTFOLIO RETENTION INVENTORY

    READ ONLY.  Run manually in SSMS under an authorized read-only/admin
    account while every collector is disabled.  This file does not select a
    retention set, delete rows, create a database, take a backup, shrink a
    file, or change collector state.

    The result sets are evidence inputs for the separately reviewed
    select-ten-snapshots-readonly.sql, replacement-database-dry-run.sql and
    retention-dry-run.sql files.  The database name is deliberately exact: do
    not adapt it by guesswork.

    S155 correction: the complete deployed SQL Server structure is preserved.
    This inventory therefore reports every user schema and every user object,
    explicitly distinguishing physical tables from views and programmable
    objects.  Only reviewed historical DATA may later be reduced; no schema,
    column, index, key, foreign key, view, function, procedure, or trigger is
    an automatic cleanup target.  GTFS, warehouse dimensions/facts, and
    lineage/configuration/audit data are separate data-retention domains, not
    reasons to replace or discard the database structure.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52000, 'Wrong database. Expected CologneTransitIntelligence.', 1;

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
    N'portfolio.schema_object_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    obj.object_id,
    obj.type AS object_type,
    obj.type_desc AS object_type_desc,
    CASE
        WHEN obj.type = 'U' THEN N'PHYSICAL_TABLE'
        WHEN obj.type = 'V' THEN N'VIEW'
        WHEN obj.type IN ('P', 'PC') THEN N'STORED_PROCEDURE'
        WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN N'FUNCTION'
        WHEN obj.type = 'TR' THEN N'TRIGGER'
        WHEN obj.type IN ('SN', 'SO') THEN N'SYNONYM'
        ELSE N'OTHER_USER_OBJECT'
    END AS physical_or_logical_kind,
    CASE
        WHEN obj.type = 'U' THEN N'DATA_RETENTION_REVIEW_REQUIRED'
        ELSE N'PRESERVE_OBJECT_DEFINITION'
    END AS disposition,
    obj.create_date,
    obj.modify_date,
    obj.is_ms_shipped
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0
ORDER BY sch.name, physical_or_logical_kind, obj.name;

SELECT
    N'portfolio.schema_inventory' AS statement_id,
    sch.schema_id,
    sch.name AS schema_name,
    USER_NAME(sch.principal_id) AS schema_owner,
    N'PRESERVE_SCHEMA_DEFINITION_EVEN_IF_EMPTY' AS disposition
FROM sys.schemas AS sch
WHERE sch.name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest')
ORDER BY sch.schema_id;

SELECT
    N'portfolio.schema_preservation_contract' AS statement_id,
    (SELECT COUNT_BIG(*) FROM sys.schemas
     WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest'))
        AS user_schema_count,
    COUNT_BIG(CASE WHEN obj.type = 'U' THEN 1 END) AS physical_table_count,
    COUNT_BIG(CASE WHEN obj.type = 'V' THEN 1 END) AS view_count,
    COUNT_BIG(CASE WHEN obj.type IN ('P', 'PC') THEN 1 END) AS procedure_count,
    COUNT_BIG(CASE WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN 1 END)
        AS function_count,
    COUNT_BIG(CASE WHEN obj.type = 'TR' THEN 1 END) AS trigger_count,
    N'PRESERVE_ALL_SCHEMAS_AND_OBJECT_DEFINITIONS; REDUCE_DATA_ONLY'
        AS required_boundary
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0;

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
    THROW 52001, 'Required ctl/stg retention tables are missing after object inventory.', 1;

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
    MAX(CASE
        WHEN LOWER(sch.name) LIKE N'%gtfs%'
          OR LOWER(obj.name) LIKE N'%gtfs%' THEN N'GTFS'
        WHEN LOWER(sch.name) IN (N'dw', N'dwh', N'warehouse')
          OR LOWER(obj.name) LIKE N'%warehouse%'
          OR LOWER(obj.name) LIKE N'fact%'
          OR LOWER(obj.name) LIKE N'dim%' THEN N'WAREHOUSE'
        WHEN LOWER(sch.name) IN (N'ctl', N'stg', N'src', N'wrk', N'core', N'analytics')
          OR LOWER(obj.name) LIKE N'%lineage%'
          OR LOWER(obj.name) LIKE N'%config%'
          OR LOWER(obj.name) LIKE N'%audit%'
          OR LOWER(obj.name) LIKE N'%run%'
          OR LOWER(obj.name) LIKE N'%claim%'
          OR LOWER(obj.name) LIKE N'%lease%' THEN N'ETL_LINEAGE_OR_SOURCE'
        ELSE N'OTHER'
    END) AS data_domain,
    SUM(CASE WHEN ps.index_id IN (0, 1)
             THEN CONVERT(bigint, ps.row_count) ELSE CONVERT(bigint, 0) END) AS base_rows,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes_all_indexes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes_all_indexes,
    SUM(CONVERT(bigint, ps.reserved_page_count) - CONVERT(bigint, ps.used_page_count))
        * CONVERT(bigint, 8192) AS unused_reserved_bytes_all_indexes
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes_all_indexes DESC, sch.name, obj.name;

SELECT
    N'portfolio.physical_table_row_counts' AS statement_id,
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
    SUM(CONVERT(bigint, ps.row_count)) AS physical_row_count,
    N'RETAIN_OR_REDUCE_ONLY_AFTER_TABLE_LEVEL_RETENTION_MANIFEST'
        AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.tables AS tab ON tab.object_id = ps.object_id
JOIN sys.schemas AS sch ON sch.schema_id = tab.schema_id
WHERE ps.index_id IN (0, 1)
GROUP BY sch.name, tab.name
ORDER BY sch.name, tab.name;

SELECT
    N'portfolio.index_key_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    idx.name AS index_name,
    idx.type_desc AS index_type_desc,
    idx.is_unique,
    idx.is_primary_key,
    idx.is_unique_constraint,
    kc.type_desc AS key_type_desc,
    N'PRESERVE_INDEXES_AND_KEYS; DATA_ONLY_CLEANUP' AS disposition
FROM sys.indexes AS idx
JOIN sys.objects AS obj ON obj.object_id = idx.object_id
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
LEFT JOIN sys.key_constraints AS kc ON kc.parent_object_id = idx.object_id
    AND kc.unique_index_id = idx.index_id
WHERE obj.type = 'U'
  AND idx.index_id > 0
  AND idx.is_hypothetical = 0
  AND obj.is_ms_shipped = 0
ORDER BY sch.name, obj.name, idx.index_id;

SELECT
    N'portfolio.column_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    obj.type_desc AS object_type_desc,
    col.column_id,
    col.name AS column_name,
    typ.name AS data_type,
    col.max_length,
    col.precision,
    col.scale,
    col.is_nullable,
    col.is_identity,
    col.is_computed,
    N'PRESERVE_COLUMN_DEFINITION; DATA_ONLY_CLEANUP' AS disposition
FROM sys.columns AS col
JOIN sys.objects AS obj ON obj.object_id = col.object_id
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
JOIN sys.types AS typ ON typ.user_type_id = col.user_type_id
WHERE obj.is_ms_shipped = 0
ORDER BY sch.name, obj.name, col.column_id;

SELECT
    N'portfolio.constraint_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS constraint_name,
    obj.type,
    obj.type_desc,
    parent_sch.name AS parent_schema_name,
    parent_obj.name AS parent_object_name,
    N'PRESERVE_PRIMARY_UNIQUE_CHECK_DEFAULT_AND_OTHER_CONSTRAINTS'
        AS disposition
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
LEFT JOIN sys.objects AS parent_obj ON parent_obj.object_id = obj.parent_object_id
LEFT JOIN sys.schemas AS parent_sch ON parent_sch.schema_id = parent_obj.schema_id
WHERE obj.is_ms_shipped = 0
  AND obj.type IN ('C', 'D', 'F', 'PK', 'UQ')
ORDER BY parent_sch.name, parent_obj.name, obj.type_desc, obj.name;

SELECT
    N'portfolio.index_column_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    idx.name AS index_name,
    ic.key_ordinal,
    ic.index_column_id,
    col.name AS column_name,
    ic.is_descending_key,
    ic.is_included_column,
    N'PRESERVE_INDEX_COLUMN_ORDER_AND_COVERAGE' AS disposition
FROM sys.index_columns AS ic
JOIN sys.indexes AS idx ON idx.object_id = ic.object_id
    AND idx.index_id = ic.index_id
JOIN sys.objects AS obj ON obj.object_id = ic.object_id
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
LEFT JOIN sys.columns AS col ON col.object_id = ic.object_id
    AND col.column_id = ic.column_id
WHERE obj.type = 'U'
  AND obj.is_ms_shipped = 0
ORDER BY sch.name, obj.name, idx.index_id, ic.index_column_id;

SELECT
    N'portfolio.gtfs_table_review' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes,
    N'RETAIN_SMALL_COHERENT_REAL_SAMPLE; REVIEW_FK_CLOSURE; NO_AUTOMATIC_DISCARD'
        AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE LOWER(sch.name) LIKE N'%gtfs%'
   OR LOWER(obj.name) LIKE N'%gtfs%'
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes DESC, sch.name, obj.name;

SELECT
    N'portfolio.warehouse_table_review' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes,
    MAX(CASE
        WHEN LOWER(obj.name) LIKE N'dim%' THEN N'RETAIN_RELEVANT_DIMENSION_ROWS'
        WHEN LOWER(obj.name) LIKE N'fact%' THEN N'RETAIN_RELEVANT_FACT_ROWS'
        ELSE N'REVIEW_RELATED_WAREHOUSE_ROWS'
    END) AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE LOWER(sch.name) IN (N'dw', N'dwh', N'warehouse')
   OR LOWER(obj.name) LIKE N'%warehouse%'
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes DESC, sch.name, obj.name;

SELECT
    N'portfolio.analytics_table_review' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes,
    N'REVIEW_SEPARATELY; PRESERVE WORKING SQL STRUCTURES' AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE LOWER(sch.name) = N'analytics'
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes DESC, sch.name, obj.name;

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
    N'portfolio.foreign_keys_all_schemas' AS statement_id,
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS child_column,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS parent_column,
    fk.is_disabled,
    fk.is_not_trusted,
    fk.delete_referential_action_desc,
    fk.update_referential_action_desc,
    N'PRESERVE_FOREIGN_KEY; RETENTION_MUST_KEEP_PARENT_CHILD_CLOSURE'
        AS retention_contract
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
ORDER BY child_schema, child_table, foreign_key_name, fkc.constraint_column_id;

SELECT
    N'portfolio.sql_module_dependencies_all_schemas' AS statement_id,
    OBJECT_SCHEMA_NAME(dep.referencing_id) AS referencing_schema,
    OBJECT_NAME(dep.referencing_id) AS referencing_object,
    o.type_desc AS referencing_type,
    OBJECT_SCHEMA_NAME(dep.referenced_id) AS referenced_schema,
    OBJECT_NAME(dep.referenced_id) AS referenced_object,
    COL_NAME(dep.referenced_id, dep.referenced_minor_id) AS referenced_column,
    N'PRESERVE_MODULE_DEFINITION; REVIEW_DATA_DEPENDENCY_BEFORE_PURGE'
        AS retention_contract
FROM sys.sql_expression_dependencies AS dep
JOIN sys.objects AS o ON o.object_id = dep.referencing_id
WHERE o.is_ms_shipped = 0
ORDER BY referencing_schema, referencing_object, referenced_schema, referenced_object;

SELECT
    N'portfolio.foreign_key_retention_closure_contract' AS statement_id,
    N'MDD_GTFS_WAREHOUSE_LINEAGE' AS data_scope,
    N'RECURSIVELY_INVENTORY_ALL_FK_DESCENDANTS_AND_PARENTS_BEFORE_ANY_DATA_DELETE'
        AS required_action,
    N'NO_CASCADE_ASSUMPTION; KEEP_ALL_REQUIRED_DIMENSION_FACT_AND_LINEAGE_ROWS'
        AS safety_rule;

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

    SELECT TOP (20)
        N'portfolio.backup_verification' AS statement_id,
        bs.database_name,
        bs.backup_start_date,
        bs.backup_finish_date,
        bs.is_copy_only,
        bs.has_backup_checksums,
        bs.compressed_backup_size,
        bmf.physical_device_name,
        N'RESTORE_VERIFYONLY_WITH_CHECKSUM_REQUIRED; ATTACH_OPERATOR_RESULT'
            AS verification_status
    FROM msdb.dbo.backupset AS bs
    LEFT JOIN msdb.dbo.backupmediafamily AS bmf
        ON bmf.media_set_id = bs.media_set_id
    WHERE bs.database_name = DB_NAME()
      AND bs.type = 'D'
    ORDER BY bs.backup_finish_date DESC, bs.backup_set_id DESC;
END;

SELECT
    N'portfolio.active_sessions' AS statement_id,
    req.session_id,
    req.status,
    req.command,
    req.wait_type,
    req.blocking_session_id,
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
    N'portfolio.external_consumer_review' AS statement_id,
    N'BLOCKED_EXTERNAL_CONSUMER_REVIEW' AS status,
    N'SQL metadata cannot prove the absence of direct clients, Power BI extracts,
      notebooks, or manually scheduled scripts. Operator must attach current
      consumer evidence before physical payload changes.' AS required_action;

SELECT
    N'portfolio.replacement_database_fit_gate' AS statement_id,
    N'PREFERRED_IF_INDEPENDENTLY_VALIDATED_PEAK_FITS' AS preferred_method,
    CONVERT(bigint, NULL) AS measured_current_c_free_bytes,
    CONVERT(bigint, NULL) AS measured_replacement_peak_reserve_bytes,
    CONVERT(bigint, 12) * CONVERT(bigint, 1024) * CONVERT(bigint, 1024)
        * CONVERT(bigint, 1024) AS required_c_free_bytes,
    N'BLOCKED_UNTIL_ISOLATED_REPLACEMENT_PEAK_IS_MEASURED; NO DATABASE COPY WAS CREATED'
        AS status,
    N'Use the separate replacement-database-dry-run.sql result and preserve the 12 GiB floor.'
        AS required_action;

SELECT
    N'portfolio.before_after_measurement_contract' AS statement_id,
    N'REQUIRED_BEFORE_AND_AFTER' AS status,
    N'Measure Windows C: free bytes, MDF/LDF allocation and used pages,
      tempdb, autogrowth, active sessions, retained row/hash/FK counts, and
      external-consumer readback before and after any separately authorized
      operation.' AS measurement_contract;

SELECT
    N'portfolio.collector_state_review' AS statement_id,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun AS r
     WHERE r.Status IN (N'RUNNING', N'CLAIMED'))
        AS active_collector_run_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease AS l
     WHERE l.Status = N'ACTIVE')
        AS active_production_lease_rows,
    N'EXPECTED_ZERO; COLLECTORS MUST REMAIN DISABLED' AS expected_state
;

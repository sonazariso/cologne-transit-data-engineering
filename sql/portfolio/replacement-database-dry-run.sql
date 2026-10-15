/*
    COLOGNE TRANSIT INTELLIGENCE — REPLACEMENT DATABASE FEASIBILITY REVIEW

    READ ONLY.  This script does not create a database, copy rows, alter
    schema, delete data, shrink files, take/overwrite a backup, or change
    collector state.  It locks the S154 operator-confirmed ID set and measures
    the evidence needed to decide whether a tiny independently validated
    replacement database can fit on the existing C: drive.

    Preferred method: an isolated, separately measured replacement database
    containing the existing working schema and the ten complete snapshots.
    Required proof:

        measured live C: free bytes - measured replacement peak reserve
            >= 12 GiB

    The peak must include the copy, transaction log, tempdb, rollback,
    autogrowth, and external-use reserves.  A NULL peak is BLOCKED; this file
    never estimates safety from payload bytes alone.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52500, 'Wrong database. Expected CologneTransitIntelligence.', 1;

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
    THROW 52501, 'Required ctl/stg replacement-review tables are missing.', 1;

/* The exact S154 set.  Do not replace with TOP, a range, or a guessed ID. */
WITH ApprovedSnapshotIds AS
(
    SELECT CONVERT(bigint, v.SnapshotId) AS SnapshotId
    FROM (VALUES
        (22), (93), (185), (189), (207),
        (224), (225), (226), (228), (229)
    ) AS v(SnapshotId)
)
SELECT
    N'portfolio.replacement_approved_id_set' AS statement_id,
    a.SnapshotId,
    CASE WHEN s.SnapshotId IS NULL THEN N'MISSING' ELSE N'PRESENT' END
        AS source_snapshot_status,
    s.RunId,
    s.AuditId,
    s.TargetIdentity,
    s.ObservedAtUtc,
    cr.Status AS run_status,
    ta.Status AS audit_status,
    ta.HttpStatus,
    ta.ParserOutcome,
    ta.PersistenceOutcome,
    s.PayloadHashSha256,
    CASE
        WHEN s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL THEN N'PLAINTEXT'
        WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL THEN N'GZIP'
        WHEN s.SnapshotId IS NULL THEN N'NOT_FOUND'
        ELSE N'INVALID'
    END AS payload_representation,
    CONVERT(bigint, CASE WHEN s.SnapshotJson IS NOT NULL
                         THEN DATALENGTH(s.SnapshotJson)
                         ELSE DATALENGTH(DECOMPRESS(s.SnapshotJsonGzip)) END)
        AS original_utf16_bytes,
    CONVERT(varchar(64), CASE WHEN s.SnapshotId IS NULL THEN NULL ELSE HASHBYTES
    (
        'SHA2_256',
        CASE WHEN s.SnapshotJson IS NOT NULL
             THEN CONVERT(varbinary(max), s.SnapshotJson)
             ELSE DECOMPRESS(s.SnapshotJsonGzip) END
    ) END, 2) AS original_payload_sha256,
    t.TimingRows,
    c.CallRows,
    sit.SituationRows,
    sec.ServiceSectionRows,
    att.AttributeRows,
    err.ResponseErrorRows,
    gap.ParserGapRows
FROM ApprovedSnapshotIds AS a
LEFT JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = a.SnapshotId
LEFT JOIN ctl.MddMacTargetAudit AS ta ON ta.AuditId = s.AuditId
LEFT JOIN ctl.MddMacCollectorRun AS cr ON cr.RunId = s.RunId
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS TimingRows
    FROM stg.MddMacTimingObservation AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS t
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS CallRows
    FROM stg.MddMacCallObservation AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS c
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS SituationRows
    FROM stg.MddMacSituationEvidence AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS sit
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ServiceSectionRows
    FROM stg.MddMacServiceSectionEvidence AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS sec
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS AttributeRows
    FROM stg.MddMacAttributeEvidence AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS att
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ResponseErrorRows
    FROM stg.MddMacResponseError AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS err
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ParserGapRows
    FROM stg.MddMacParserGap AS x
    WHERE x.SnapshotId = a.SnapshotId
) AS gap
ORDER BY a.SnapshotId;

WITH ApprovedSnapshotIds AS
(
    SELECT CONVERT(bigint, v.SnapshotId) AS SnapshotId
    FROM (VALUES
        (22), (93), (185), (189), (207),
        (224), (225), (226), (228), (229)
    ) AS v(SnapshotId)
)
SELECT
    N'portfolio.replacement_id_set_summary' AS statement_id,
    COUNT_BIG(*) AS approved_snapshot_ids,
    COUNT_BIG(s.SnapshotId) AS present_snapshot_rows,
    COUNT_BIG(DISTINCT s.RunId) AS present_run_ids,
    COUNT_BIG(DISTINCT s.AuditId) AS present_audit_ids,
    COUNT_BIG(CASE WHEN ta.HttpStatus = 200
                    AND UPPER(COALESCE(cr.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
                    AND UPPER(COALESCE(ta.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
                    AND UPPER(COALESCE(ta.ParserOutcome, '')) = 'PARSED'
                    AND UPPER(COALESCE(ta.PersistenceOutcome, '')) = 'PERSISTED_PARSED_SNAPSHOT'
                   THEN 1 END) AS successful_persisted_rows,
    CASE WHEN COUNT_BIG(s.SnapshotId) = 10
              AND COUNT_BIG(DISTINCT s.RunId) > 0
              AND COUNT_BIG(DISTINCT s.AuditId) = 10
              AND COUNT_BIG(CASE WHEN ta.HttpStatus = 200
                                   AND UPPER(COALESCE(cr.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
                                   AND UPPER(COALESCE(ta.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
                                   AND UPPER(COALESCE(ta.ParserOutcome, '')) = 'PARSED'
                                   AND UPPER(COALESCE(ta.PersistenceOutcome, '')) = 'PERSISTED_PARSED_SNAPSHOT'
                                  THEN 1 END) = 10
         THEN N'REVIEW_EVIDENCE'
         ELSE N'BLOCKED_ID_SET_NOT_COMPLETE'
    END AS status
FROM ApprovedSnapshotIds AS a
LEFT JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = a.SnapshotId
LEFT JOIN ctl.MddMacTargetAudit AS ta ON ta.AuditId = s.AuditId
LEFT JOIN ctl.MddMacCollectorRun AS cr ON cr.RunId = s.RunId;

WITH ApprovedSnapshotIds AS
(
    SELECT CONVERT(bigint, v.SnapshotId) AS SnapshotId
    FROM (VALUES
        (22), (93), (185), (189), (207),
        (224), (225), (226), (228), (229)
    ) AS v(SnapshotId)
)
SELECT
    N'portfolio.replacement_child_counts' AS statement_id,
    family_name,
    retained_rows,
    N'KEEP_ALL_ROWS_FOR_APPROVED_SNAPSHOT_IDS' AS disposition
FROM
(
    SELECT N'stg.MddMacTimingObservation' AS family_name,
           (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId) AS retained_rows
    UNION ALL
    SELECT N'stg.MddMacCallObservation',
           (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
    UNION ALL
    SELECT N'stg.MddMacSituationEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
    UNION ALL
    SELECT N'stg.MddMacServiceSectionEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
    UNION ALL
    SELECT N'stg.MddMacAttributeEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
    UNION ALL
    SELECT N'stg.MddMacResponseError',
           (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
    UNION ALL
    SELECT N'stg.MddMacParserGap',
           (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap AS x
            JOIN ApprovedSnapshotIds AS a ON a.SnapshotId = x.SnapshotId)
) AS counts(family_name, retained_rows)
ORDER BY family_name;

SELECT
    N'portfolio.replacement_database_files' AS statement_id,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
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
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS windows_volume_available_bytes,
    CONVERT(bigint, vs.total_bytes) AS windows_volume_total_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'portfolio.replacement_log_and_tempdb' AS statement_id,
    N'LOG' AS resource_name,
    total_log_size_in_bytes AS allocated_bytes,
    used_log_space_in_bytes AS used_bytes,
    CONVERT(bigint, total_log_size_in_bytes - used_log_space_in_bytes)
        AS free_bytes,
    used_log_space_in_percent,
    CONVERT(nvarchar(60), DATABASEPROPERTYEX(DB_NAME(), N'Recovery'))
        AS recovery_model
FROM sys.dm_db_log_space_usage;

SELECT
    N'portfolio.replacement_log_and_tempdb' AS statement_id,
    N'TEMPDB' AS resource_name,
    SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) AS allocated_bytes,
    SUM(CONVERT(bigint, COALESCE(fsu.user_object_reserved_page_count, 0)
        + COALESCE(fsu.internal_object_reserved_page_count, 0)
        + COALESCE(fsu.version_store_reserved_page_count, 0)))
        * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, COALESCE(fsu.unallocated_extent_page_count, 0)))
        * CONVERT(bigint, 8192) AS unallocated_bytes
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT
    N'portfolio.replacement_active_sessions' AS statement_id,
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
    N'portfolio.replacement_table_allocations' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CASE WHEN ps.index_id IN (0, 1)
             THEN CONVERT(bigint, ps.row_count) ELSE CONVERT(bigint, 0) END) AS base_rows,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes DESC, sch.name, obj.name;

SELECT
    N'portfolio.gtfs_table_review' AS statement_id,
    sch.name AS schema_name,
    obj.name AS table_name,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    N'REVIEW_SEPARATELY; NO_AUTOMATIC_DISCARD' AS disposition
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
    N'REVIEW_SEPARATELY; NO_AUTOMATIC_DISCARD' AS disposition
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
    obj.name AS object_name,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    N'REVIEW_SEPARATELY; PRESERVE WORKING SQL STRUCTURES' AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS obj ON obj.object_id = ps.object_id AND obj.type = 'U'
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE LOWER(sch.name) = N'analytics'
GROUP BY sch.name, obj.name
ORDER BY reserved_bytes DESC, sch.name, obj.name;

SELECT
    N'portfolio.replacement_module_dependencies' AS statement_id,
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
        N'portfolio.replacement_sql_agent_dependencies' AS statement_id,
        j.name AS job_name,
        js.step_id,
        js.step_name,
        js.database_name,
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
    N'portfolio.external_consumer_review' AS statement_id,
    N'BLOCKED_EXTERNAL_CONSUMER_REVIEW' AS status,
    N'SQL metadata cannot prove the absence of direct clients, notebooks,
      exports, Power BI queries, or manually scheduled scripts. Attach the
      current consumer review before any replacement or retirement decision.'
        AS required_action;

SELECT
    N'portfolio.replacement_peak_fit_gate' AS statement_id,
    N'PREFERRED_METHOD_NOT_YET_VALIDATED' AS status,
    CONVERT(bigint, NULL) AS measured_live_c_free_bytes,
    CONVERT(bigint, NULL) AS measured_replacement_peak_reserve_bytes,
    CONVERT(bigint, 12) * CONVERT(bigint, 1024) * CONVERT(bigint, 1024)
        * CONVERT(bigint, 1024) AS required_c_free_bytes,
    N'BLOCKED_UNTIL_ISOLATED_COPY_MEASURES_PEAK_AND_ROLLBACK_RESERVE'
        AS required_action;

SELECT
    N'portfolio.before_after_measurement_contract' AS statement_id,
    N'REQUIRED_BEFORE_AND_AFTER' AS status,
    N'Measure C: free bytes, database MDF/LDF allocation and used pages,
      tempdb, autogrowth, active sessions, retained row/hash/FK counts, and
      external-consumer readback before and after any separately authorized
      operation.' AS measurement_contract;

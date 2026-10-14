/*
    COLOGNE TRANSIT INTELLIGENCE — HISTORICAL STORAGE RECOVERY AUDIT

    This is the single consolidated, read-only SSMS audit for the current
    production database.  It measures SQL Server allocation, the complete
    historical population, bounded length-only observation samples, log and
    tempdb headroom, active-writer state, and server-visible compatibility
    dependencies.

    Every allocation calculation converts SQL page counts to BIGINT before
    multiplying by 8192.  Result sets have stable statement_id values so the
    operator can capture and hash them for the separately signed capacity
    evidence artifact.

    This file does not produce a compact-envelope capacity claim.  Logical
    payload lengths and the bounded samples below are diagnostic evidence
    only.  The measured full-history physical allocation plan must come from
    the isolated lab capture and the existing migration/lab scripts.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

SELECT
    N'capacity.database_identity' AS statement_id,
    DB_NAME() AS database_name,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName')) AS server_name,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ProductVersion')) AS product_version,
    DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), N'UserAccess') AS user_access,
    DATABASEPROPERTYEX(DB_NAME(), N'Recovery') AS recovery_model,
    CONVERT(int, DATABASEPROPERTYEX(DB_NAME(), N'CompatibilityLevel')) AS compatibility_level,
    DATABASEPROPERTYEX(DB_NAME(), N'Collation') AS collation_name,
    SUSER_SNAME() AS effective_login,
    SYSDATETIMEOFFSET() AS captured_at_local;

SELECT
    N'capacity.database_files' AS statement_id,
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
    CONVERT(bigint, vs.total_bytes) AS volume_total_bytes,
    CONVERT(bigint, vs.available_bytes) AS volume_available_bytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'capacity.snapshot_population' AS statement_id,
    COUNT_BIG(*) AS snapshot_count,
    MIN(SnapshotId) AS first_snapshot_id,
    MAX(SnapshotId) AS last_snapshot_id,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJson IS NOT NULL THEN 1 ELSE 0 END)) AS plaintext_snapshot_rows,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)) AS gzip_snapshot_rows,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END)) AS missing_snapshot_payload_rows,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(SnapshotJson), 0))) AS plaintext_snapshot_bytes,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(SnapshotJsonGzip), 0))) AS gzip_snapshot_bytes
FROM stg.MddMacSourceSnapshot;

SELECT
    N'capacity.historical_row_counts' AS statement_id,
    N'MddMacSourceSnapshot' AS table_name,
    COUNT_BIG(*) AS row_count,
    MIN(SnapshotId) AS first_id,
    MAX(SnapshotId) AS last_id
FROM stg.MddMacSourceSnapshot
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacCallObservation', COUNT_BIG(*), MIN(CallObservationId), MAX(CallObservationId)
FROM stg.MddMacCallObservation
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacTimingObservation', COUNT_BIG(*), MIN(TimingObservationId), MAX(TimingObservationId)
FROM stg.MddMacTimingObservation
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacSituationEvidence', COUNT_BIG(*), MIN(SituationEvidenceId), MAX(SituationEvidenceId)
FROM stg.MddMacSituationEvidence
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacServiceSectionEvidence', COUNT_BIG(*), MIN(ServiceSectionEvidenceId), MAX(ServiceSectionEvidenceId)
FROM stg.MddMacServiceSectionEvidence
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacAttributeEvidence', COUNT_BIG(*), MIN(AttributeEvidenceId), MAX(AttributeEvidenceId)
FROM stg.MddMacAttributeEvidence
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacResponseError', COUNT_BIG(*), MIN(ResponseErrorId), MAX(ResponseErrorId)
FROM stg.MddMacResponseError
UNION ALL
SELECT N'capacity.historical_row_counts', N'MddMacParserGap', COUNT_BIG(*), MIN(ParserGapId), MAX(ParserGapId)
FROM stg.MddMacParserGap;

SELECT
    N'capacity.production_object_allocation' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    idx.index_id,
    idx.name AS index_name,
    idx.type_desc AS index_type,
    SUM(CONVERT(bigint, ps.row_count)) AS row_count,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192) AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192) AS used_bytes,
    SUM(CONVERT(bigint, ps.in_row_data_page_count)) * CONVERT(bigint, 8192) AS in_row_bytes,
    SUM(CONVERT(bigint, ps.lob_reserved_page_count)) * CONVERT(bigint, 8192) AS lob_reserved_bytes,
    SUM(CONVERT(bigint, ps.row_overflow_reserved_page_count)) * CONVERT(bigint, 8192) AS row_overflow_reserved_bytes,
    p.data_compression_desc
FROM sys.dm_db_partition_stats AS ps
INNER JOIN sys.objects AS obj ON obj.object_id = ps.object_id
INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
INNER JOIN sys.indexes AS idx
    ON idx.object_id = ps.object_id
   AND idx.index_id = ps.index_id
INNER JOIN sys.partitions AS p
    ON p.object_id = ps.object_id
   AND p.index_id = ps.index_id
   AND p.partition_number = ps.partition_number
WHERE sch.name = N'stg'
  AND obj.name IN
  (
      N'MddMacSourceSnapshot', N'MddMacCallObservation',
      N'MddMacTimingObservation', N'MddMacSituationEvidence',
      N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
      N'MddMacResponseError', N'MddMacParserGap',
      N'MddMacCompactObservationEnvelope', N'MddMacCompactMigration',
      N'MddMacCompactMigrationUnit'
  )
GROUP BY sch.name, obj.name, idx.index_id, idx.name, idx.type_desc,
         p.data_compression_desc
ORDER BY obj.name, idx.index_id;

/* Deterministic IDs for length-only representative samples. */
;WITH OrderedSnapshots AS
(
    SELECT SnapshotId,
           ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
           COUNT_BIG(*) OVER () AS population_count
    FROM stg.MddMacSourceSnapshot
)
SELECT
    N'capacity.representative_snapshot_selection' AS statement_id,
    SnapshotId,
    ordinal,
    population_count,
    CASE WHEN ordinal = 1 THEN N'FIRST'
         WHEN ordinal = population_count THEN N'LAST'
         ELSE N'MIDDLE' END AS sample_position
FROM OrderedSnapshots
WHERE ordinal = 1
   OR ordinal = (population_count + 1) / 2
   OR ordinal = population_count
ORDER BY ordinal;

/* No source text or JSON is returned by the following result sets. */
SELECT
    N'capacity.representative_observation_sizes' AS statement_id,
    N'MddMacSourceSnapshot' AS family_name,
    s.SnapshotId,
    COUNT_BIG(*) AS row_count,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(s.SnapshotJson), 0))) AS plain_lob_bytes,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(s.SnapshotJsonGzip), 0))) AS gzip_lob_bytes,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(s.SnapshotJson), 0)))
        + SUM(CONVERT(bigint, COALESCE(DATALENGTH(s.SnapshotJsonGzip), 0))) AS total_payload_bytes
FROM stg.MddMacSourceSnapshot AS s
WHERE s.SnapshotId IN
(
    SELECT SnapshotId
    FROM
    (
        SELECT SnapshotId,
               ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1
       OR ordinal = (population_count + 1) / 2
       OR ordinal = population_count
)
GROUP BY s.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacCallObservation', c.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(c.SourcePathsJson), 0) + COALESCE(DATALENGTH(c.SourceEventJson), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(c.SourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(c.SourceEventJsonGzip), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(c.SourcePathsJson), 0) + COALESCE(DATALENGTH(c.SourceEventJson), 0)
        + COALESCE(DATALENGTH(c.SourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(c.SourceEventJsonGzip), 0)))
FROM stg.MddMacCallObservation AS c
WHERE c.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY c.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacTimingObservation', t.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(t.SourceEventIdentityBasisJson), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityMissingFieldsJson), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityAmbiguityJson), 0)
        + COALESCE(DATALENGTH(t.SourcePathsJson), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(t.SourceEventIdentityBasisJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityMissingFieldsJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityAmbiguityJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourcePathsJsonGzip), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(t.SourceEventIdentityBasisJson), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityMissingFieldsJson), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityAmbiguityJson), 0)
        + COALESCE(DATALENGTH(t.SourcePathsJson), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityBasisJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityMissingFieldsJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourceEventIdentityAmbiguityJsonGzip), 0)
        + COALESCE(DATALENGTH(t.SourcePathsJsonGzip), 0)))
FROM stg.MddMacTimingObservation AS t
WHERE t.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY t.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacSituationEvidence', e.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.Description), 0) + COALESCE(DATALENGTH(e.Detail), 0)
        + COALESCE(DATALENGTH(e.AffectsJson), 0) + COALESCE(DATALENGTH(e.AffectedEntityJson), 0)
        + COALESCE(DATALENGTH(e.AffectedJourneyJson), 0) + COALESCE(DATALENGTH(e.AffectedLineJson), 0)
        + COALESCE(DATALENGTH(e.FieldPathsJson), 0) + COALESCE(DATALENGTH(e.AffectedEntityFieldPathsJson), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.DescriptionGzip), 0) + COALESCE(DATALENGTH(e.DetailGzip), 0)
        + COALESCE(DATALENGTH(e.AffectsJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedEntityJsonGzip), 0)
        + COALESCE(DATALENGTH(e.AffectedJourneyJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedLineJsonGzip), 0)
        + COALESCE(DATALENGTH(e.FieldPathsJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedEntityFieldPathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.Description), 0) + COALESCE(DATALENGTH(e.Detail), 0)
        + COALESCE(DATALENGTH(e.AffectsJson), 0) + COALESCE(DATALENGTH(e.AffectedEntityJson), 0)
        + COALESCE(DATALENGTH(e.AffectedJourneyJson), 0) + COALESCE(DATALENGTH(e.AffectedLineJson), 0)
        + COALESCE(DATALENGTH(e.FieldPathsJson), 0) + COALESCE(DATALENGTH(e.AffectedEntityFieldPathsJson), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0)
        + COALESCE(DATALENGTH(e.DescriptionGzip), 0) + COALESCE(DATALENGTH(e.DetailGzip), 0)
        + COALESCE(DATALENGTH(e.AffectsJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedEntityJsonGzip), 0)
        + COALESCE(DATALENGTH(e.AffectedJourneyJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedLineJsonGzip), 0)
        + COALESCE(DATALENGTH(e.FieldPathsJsonGzip), 0) + COALESCE(DATALENGTH(e.AffectedEntityFieldPathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0)))
FROM stg.MddMacSituationEvidence AS e
WHERE e.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY e.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacServiceSectionEvidence', e.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.Description), 0) + COALESCE(DATALENGTH(e.RouteDescription), 0)
        + COALESCE(DATALENGTH(e.Via), 0) + COALESCE(DATALENGTH(e.ModeSourceJson), 0)
        + COALESCE(DATALENGTH(e.SourcePathsJson), 0) + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.DescriptionGzip), 0) + COALESCE(DATALENGTH(e.RouteDescriptionGzip), 0)
        + COALESCE(DATALENGTH(e.ViaGzip), 0) + COALESCE(DATALENGTH(e.ModeSourceJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.Description), 0) + COALESCE(DATALENGTH(e.RouteDescription), 0)
        + COALESCE(DATALENGTH(e.Via), 0) + COALESCE(DATALENGTH(e.ModeSourceJson), 0)
        + COALESCE(DATALENGTH(e.SourcePathsJson), 0) + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0)
        + COALESCE(DATALENGTH(e.DescriptionGzip), 0) + COALESCE(DATALENGTH(e.RouteDescriptionGzip), 0)
        + COALESCE(DATALENGTH(e.ViaGzip), 0) + COALESCE(DATALENGTH(e.ModeSourceJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0)))
FROM stg.MddMacServiceSectionEvidence AS e
WHERE e.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY e.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacAttributeEvidence', e.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.TextSourceText), 0) + COALESCE(DATALENGTH(e.CodeSourcePathsJson), 0)
        + COALESCE(DATALENGTH(e.TextSourcePathsJson), 0) + COALESCE(DATALENGTH(e.SourceFieldPathsJson), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.TextSourceTextGzip), 0) + COALESCE(DATALENGTH(e.CodeSourcePathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.TextSourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(e.SourceFieldPathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0))),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.TextSourceText), 0) + COALESCE(DATALENGTH(e.CodeSourcePathsJson), 0)
        + COALESCE(DATALENGTH(e.TextSourcePathsJson), 0) + COALESCE(DATALENGTH(e.SourceFieldPathsJson), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJson), 0)
        + COALESCE(DATALENGTH(e.TextSourceTextGzip), 0) + COALESCE(DATALENGTH(e.CodeSourcePathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.TextSourcePathsJsonGzip), 0) + COALESCE(DATALENGTH(e.SourceFieldPathsJsonGzip), 0)
        + COALESCE(DATALENGTH(e.SourceRepresentationJsonGzip), 0)))
FROM stg.MddMacAttributeEvidence AS e
WHERE e.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY e.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacResponseError', e.SnapshotId,
    COUNT_BIG(*), SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.ErrorValue), 0))), 0,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.ErrorValue), 0)))
FROM stg.MddMacResponseError AS e
WHERE e.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY e.SnapshotId
UNION ALL
SELECT
    N'capacity.representative_observation_sizes', N'MddMacParserGap', e.SnapshotId,
    COUNT_BIG(*),
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.SourceRepresentationJson), 0))), 0,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(e.SourceRepresentationJson), 0)))
FROM stg.MddMacParserGap AS e
WHERE e.SnapshotId IN
(
    SELECT SnapshotId FROM
    (
        SELECT SnapshotId, ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM stg.MddMacSourceSnapshot
    ) AS sample
    WHERE ordinal = 1 OR ordinal = (population_count + 1) / 2 OR ordinal = population_count
)
GROUP BY e.SnapshotId
ORDER BY family_name, SnapshotId;

SELECT
    N'capacity.transaction_log' AS statement_id,
    total_log_size_in_bytes,
    used_log_space_in_bytes,
    CONVERT(bigint, total_log_size_in_bytes - used_log_space_in_bytes) AS free_log_bytes,
    used_log_space_in_percent,
    d.log_reuse_wait_desc
FROM sys.dm_db_log_space_usage AS l
INNER JOIN sys.databases AS d ON d.database_id = DB_ID();

SELECT
    N'capacity.tempdb' AS statement_id,
    df.file_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    df.physical_name,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, fsu.allocated_extent_page_count) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, fsu.unallocated_extent_page_count) * CONVERT(bigint, 8192)
         ELSE NULL END AS internally_free_bytes,
    CONVERT(bigint, df.growth) AS growth_value,
    CONVERT(bit, df.is_percent_growth) AS is_percent_growth,
    CONVERT(bigint, df.max_size) AS max_size_pages,
    vs.volume_mount_point,
    CONVERT(bigint, vs.total_bytes) AS volume_total_bytes,
    CONVERT(bigint, vs.available_bytes) AS volume_available_bytes
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu
    ON fsu.database_id = DB_ID(N'tempdb')
   AND fsu.file_id = df.file_id
CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'capacity.rollback' AS statement_id,
    COUNT_BIG(DISTINCT st.session_id) AS active_user_transaction_sessions,
    COUNT_BIG(*) AS active_transaction_rows,
    COALESCE(SUM(CONVERT(bigint, dt.database_transaction_log_record_count)), 0)
        AS active_transaction_log_record_count,
    COALESCE(SUM(CONVERT(bigint, dt.database_transaction_log_bytes_used)), 0)
        AS active_transaction_log_bytes_used,
    N'READ_ONLY_CURRENT_ACTIVITY; ROLLBACK_RESERVE_REQUIRES_ISOLATED_LAB_MEASUREMENT' AS rollback_measurement_status
FROM sys.dm_tran_session_transactions AS st
INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
LEFT JOIN sys.dm_tran_database_transactions AS dt
    ON dt.transaction_id = st.transaction_id
WHERE st.session_id <> @@SPID
  AND es.is_user_process = 1;

SELECT
    N'capacity.autogrowth' AS statement_id,
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
    N'capacity.autogrowth', N'tempdb', df.file_id, df.name, df.type_desc,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192),
    CONVERT(bigint, df.growth), CONVERT(bit, df.is_percent_growth),
    CONVERT(bigint, df.max_size),
    CASE WHEN df.max_size < 0 THEN NULL
         ELSE (CONVERT(bigint, df.max_size) - CONVERT(bigint, df.size)) * CONVERT(bigint, 8192) END
FROM tempdb.sys.database_files AS df
ORDER BY database_scope, file_id;

SELECT
    N'capacity.active_writer_state' AS statement_id,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
     WHERE Status IN ('RUNNING', 'CLAIMED')
       AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) AS active_collector_runs,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
     WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE') AS active_production_leases,
    (SELECT COUNT_BIG(*)
     FROM sys.dm_exec_sessions AS es
     WHERE es.session_id <> @@SPID
       AND es.is_user_process = 1
       AND es.database_id = DB_ID()
       AND (LOWER(COALESCE(es.program_name, N'')) LIKE N'%cologne%transit%'
            OR LOWER(COALESCE(es.program_name, N'')) LIKE N'%mdd%collector%'
            OR LOWER(COALESCE(es.host_name, N'')) LIKE N'%mdd%collector%')) AS active_collector_sessions,
    (SELECT COUNT_BIG(*)
     FROM sys.dm_tran_session_transactions AS st
     INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
     WHERE st.session_id <> @@SPID AND es.is_user_process = 1) AS active_user_transactions;

SELECT
    N'capacity.compatibility_columns' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    obj.type_desc,
    col.column_id,
    col.name AS column_name,
    typ.name AS data_type,
    col.max_length,
    col.precision,
    col.scale,
    col.is_nullable,
    col.is_identity,
    col.is_computed
FROM sys.objects AS obj
INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
INNER JOIN sys.columns AS col ON col.object_id = obj.object_id
INNER JOIN sys.types AS typ ON typ.user_type_id = col.user_type_id
WHERE (sch.name = N'stg' AND obj.name IN
       (N'MddMacSourceSnapshot', N'MddMacCallObservation',
        N'MddMacTimingObservation', N'MddMacSituationEvidence',
        N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
        N'MddMacResponseError', N'MddMacParserGap',
        N'MddMacCompactObservationEnvelope',
        N'vMddMacCompactSourceSnapshot', N'vMddMacCompactCallObservation',
        N'vMddMacCompactTimingObservation', N'vMddMacCompactSituationEvidence',
        N'vMddMacCompactServiceSectionEvidence', N'vMddMacCompactAttributeEvidence',
        N'vMddMacCompactResponseError', N'vMddMacCompactParserGap'))
   OR (sch.name = N'analytics' AND obj.name IN
       (N'vwMddMacCompactSourceFieldCoverage', N'vwMddMacCompactRecordedAtTimeHistory'))
ORDER BY sch.name, obj.name, col.column_id;

SELECT
    N'capacity.compatibility_dependencies' AS statement_id,
    OBJECT_SCHEMA_NAME(dep.referencing_id) AS referencing_schema_name,
    OBJECT_NAME(dep.referencing_id) AS referencing_object_name,
    OBJECT_SCHEMA_NAME(dep.referenced_id) AS referenced_schema_name,
    OBJECT_NAME(dep.referenced_id) AS referenced_object_name,
    dep.referenced_minor_name,
    dep.is_schema_bound_reference
FROM sys.sql_expression_dependencies AS dep
WHERE dep.referenced_id IN
(
    SELECT obj.object_id
    FROM sys.objects AS obj
    INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
    WHERE (sch.name = N'stg' AND obj.name IN
           (N'MddMacSourceSnapshot', N'MddMacCallObservation',
            N'MddMacTimingObservation', N'MddMacSituationEvidence',
            N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
            N'MddMacResponseError', N'MddMacParserGap',
            N'MddMacCompactObservationEnvelope'))
       OR (sch.name = N'analytics' AND obj.name IN
           (N'vwMddMacCompactSourceFieldCoverage', N'vwMddMacCompactRecordedAtTimeHistory'))
)
ORDER BY referencing_schema_name, referencing_object_name, referenced_schema_name, referenced_object_name;

SELECT
    N'capacity.compatibility_foreign_keys' AS statement_id,
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS parent_schema_name,
    OBJECT_NAME(fk.parent_object_id) AS parent_object_name,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS referenced_schema_name,
    OBJECT_NAME(fk.referenced_object_id) AS referenced_object_name,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
WHERE fk.parent_object_id IN
      (SELECT object_id FROM sys.tables WHERE schema_id = SCHEMA_ID(N'stg'))
   OR fk.referenced_object_id IN
      (SELECT object_id FROM sys.tables WHERE schema_id = SCHEMA_ID(N'stg'))
ORDER BY foreign_key_name;

SELECT
    N'capacity.server_side_modules' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    obj.type_desc,
    CONVERT(char(64), HASHBYTES(N'SHA2_256', CONVERT(varbinary(max), mod.definition)), 2) AS definition_sha256,
    CASE WHEN mod.definition LIKE N'%MddMacSourceSnapshot%' THEN 1 ELSE 0 END AS mentions_legacy_snapshot,
    CASE WHEN mod.definition LIKE N'%MddMacCompactObservationEnvelope%' THEN 1 ELSE 0 END AS mentions_compact_envelope,
    CASE WHEN mod.definition LIKE N'%SnapshotJsonGzip%' THEN 1 ELSE 0 END AS mentions_snapshot_gzip
FROM sys.sql_modules AS mod
INNER JOIN sys.objects AS obj ON obj.object_id = mod.object_id
INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE mod.definition LIKE N'%MddMacSourceSnapshot%'
   OR mod.definition LIKE N'%MddMacCompactObservationEnvelope%'
   OR mod.definition LIKE N'%SnapshotJsonGzip%'
ORDER BY sch.name, obj.name;

IF OBJECT_ID(N'stg.MddMacCompactStorageState', N'U') IS NOT NULL
BEGIN
    SELECT
        N'capacity.compact_gate_state' AS statement_id,
        StateId,
        StorageFormatVersion,
        FutureWriteEnabled,
        CompatibilityVerified,
        HistoricalReadbackVerified,
        LegacyRetirementApproved,
        UpdatedAtUtc,
        UpdatedBy
    FROM stg.MddMacCompactStorageState
    WHERE StateId = 1;
END
ELSE
BEGIN
    SELECT N'capacity.compact_gate_state' AS statement_id,
           N'NOT_INSTALLED' AS status;
END;

SELECT
    N'capacity.audit_summary' AS statement_id,
    CASE WHEN DATABASEPROPERTYEX(DB_NAME(), N'Status') = N'ONLINE'
           AND DATABASEPROPERTYEX(DB_NAME(), N'UserAccess') = N'MULTI_USER'
           AND (SELECT COUNT(*)
                FROM sys.tables
                WHERE schema_id = SCHEMA_ID(N'stg')
                  AND name IN
                  (N'MddMacSourceSnapshot', N'MddMacCallObservation',
                   N'MddMacTimingObservation', N'MddMacSituationEvidence',
                   N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
                   N'MddMacResponseError', N'MddMacParserGap')) = 8
           AND COALESCE((SELECT MIN(CONVERT(bigint, vs.available_bytes))
                         FROM sys.database_files AS df
                         CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
                         WHERE vs.volume_mount_point LIKE N'C:%'), 0)
               >= CONVERT(bigint, 12 * 1024 * 1024 * 1024)
           AND (SELECT used_log_space_in_percent
                FROM sys.dm_db_log_space_usage) <= 70.0
           AND (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
                WHERE Status IN ('RUNNING', 'CLAIMED')
                  AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) = 0
           AND (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
                WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE') = 0
           AND (SELECT COUNT_BIG(*)
                FROM sys.dm_tran_session_transactions AS st
                INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
                WHERE st.session_id <> @@SPID AND es.is_user_process = 1) = 0
         THEN N'PASS' ELSE N'BLOCKED' END AS audit_status,
    COALESCE((SELECT MIN(CONVERT(bigint, vs.available_bytes))
              FROM sys.database_files AS df
              CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
              WHERE vs.volume_mount_point LIKE N'C:%'), 0) AS c_volume_available_bytes,
    CONVERT(bigint, 12 * 1024 * 1024 * 1024) AS required_c_floor_bytes,
    (SELECT used_log_space_in_percent FROM sys.dm_db_log_space_usage) AS log_used_percent,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
     WHERE Status IN ('RUNNING', 'CLAIMED')
       AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) AS active_collector_runs,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
     WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE') AS active_production_leases,
    (SELECT COUNT_BIG(*)
     FROM sys.dm_tran_session_transactions AS st
     INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
     WHERE st.session_id <> @@SPID AND es.is_user_process = 1) AS active_user_transactions;
GO

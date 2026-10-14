/*
    COLOGNE TRANSIT INTELLIGENCE — HISTORICAL STORAGE RECOVERY AUDIT

    This is the single consolidated, read-only SSMS audit for the current
    production database.  It measures SQL Server allocation, the complete
    historical population, actual per-column TimingObservation bytes,
    bounded length-only observation samples, log and tempdb headroom,
    active-writer state, and server-visible compatibility dependencies.

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
    CONVERT(int, DB_ID()) AS database_id,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName')) AS server_name,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'InstanceName')) AS instance_name,
    CONVERT(nvarchar(128), drs.database_guid) AS database_guid,
    CONVERT(nvarchar(128), SERVERPROPERTY(N'ProductVersion')) AS product_version,
    DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status,
    DATABASEPROPERTYEX(DB_NAME(), N'UserAccess') AS user_access,
    DATABASEPROPERTYEX(DB_NAME(), N'Recovery') AS recovery_model,
    CONVERT(int, DATABASEPROPERTYEX(DB_NAME(), N'CompatibilityLevel')) AS compatibility_level,
    DATABASEPROPERTYEX(DB_NAME(), N'Collation') AS collation_name,
    SUSER_SNAME() AS effective_login,
    SYSDATETIMEOFFSET() AS captured_at_local,
    SYSUTCDATETIME() AS measurement_timestamp_utc
FROM sys.databases AS d
LEFT JOIN sys.database_recovery_status AS drs ON drs.database_id = d.database_id
WHERE d.database_id = DB_ID();

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

/*
    The source tables pre-date Storage V3 in some production databases.  Keep
    the expected payload contract in metadata first, then generate payload
    expressions only from columns that sys.columns proves are installed.
    This table-variable/catalog work is read-only and deliberately does not
    install, alter, backfill, or migrate any column.
*/
DECLARE @RepresentativePayloadColumns TABLE
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    FamilyName nvarchar(128) NOT NULL,
    ColumnName sysname NOT NULL,
    IsOptional bit NOT NULL,
    IsGzip bit NOT NULL,
    ColumnOrdinal int NOT NULL
);

INSERT INTO @RepresentativePayloadColumns
(
    SchemaName, ObjectName, FamilyName, ColumnName,
    IsOptional, IsGzip, ColumnOrdinal
)
VALUES
    (N'stg', N'MddMacSourceSnapshot', N'MddMacSourceSnapshot', N'SnapshotJson', 0, 0, 10),
    (N'stg', N'MddMacSourceSnapshot', N'MddMacSourceSnapshot', N'SnapshotJsonGzip', 1, 1, 20),
    (N'stg', N'MddMacCallObservation', N'MddMacCallObservation', N'SourcePathsJson', 0, 0, 10),
    (N'stg', N'MddMacCallObservation', N'MddMacCallObservation', N'SourceEventJson', 0, 0, 20),
    (N'stg', N'MddMacCallObservation', N'MddMacCallObservation', N'SourcePathsJsonGzip', 1, 1, 30),
    (N'stg', N'MddMacCallObservation', N'MddMacCallObservation', N'SourceEventJsonGzip', 1, 1, 40),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityBasisJson', 0, 0, 10),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityMissingFieldsJson', 0, 0, 20),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityAmbiguityJson', 0, 0, 30),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourcePathsJson', 0, 0, 40),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityBasisJsonGzip', 1, 1, 50),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityMissingFieldsJsonGzip', 1, 1, 60),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourceEventIdentityAmbiguityJsonGzip', 1, 1, 70),
    (N'stg', N'MddMacTimingObservation', N'MddMacTimingObservation', N'SourcePathsJsonGzip', 1, 1, 80),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'Description', 0, 0, 10),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'Detail', 0, 0, 20),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectsJson', 0, 0, 30),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedEntityJson', 0, 0, 40),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedJourneyJson', 0, 0, 50),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedLineJson', 0, 0, 60),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'FieldPathsJson', 0, 0, 70),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedEntityFieldPathsJson', 0, 0, 80),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'SourceRepresentationJson', 0, 0, 90),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'DescriptionGzip', 1, 1, 110),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'DetailGzip', 1, 1, 120),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectsJsonGzip', 1, 1, 130),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedEntityJsonGzip', 1, 1, 140),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedJourneyJsonGzip', 1, 1, 150),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedLineJsonGzip', 1, 1, 160),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'FieldPathsJsonGzip', 1, 1, 170),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'AffectedEntityFieldPathsJsonGzip', 1, 1, 180),
    (N'stg', N'MddMacSituationEvidence', N'MddMacSituationEvidence', N'SourceRepresentationJsonGzip', 1, 1, 190),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'Description', 0, 0, 10),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'RouteDescription', 0, 0, 20),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'Via', 0, 0, 30),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'ModeSourceJson', 0, 0, 40),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'SourcePathsJson', 0, 0, 50),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'SourceRepresentationJson', 0, 0, 60),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'DescriptionGzip', 1, 1, 70),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'RouteDescriptionGzip', 1, 1, 80),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'ViaGzip', 1, 1, 90),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'ModeSourceJsonGzip', 1, 1, 100),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'SourcePathsJsonGzip', 1, 1, 110),
    (N'stg', N'MddMacServiceSectionEvidence', N'MddMacServiceSectionEvidence', N'SourceRepresentationJsonGzip', 1, 1, 120),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'TextSourceText', 0, 0, 10),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'CodeSourcePathsJson', 0, 0, 20),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'TextSourcePathsJson', 0, 0, 30),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'SourceFieldPathsJson', 0, 0, 40),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'SourceRepresentationJson', 0, 0, 50),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'TextSourceTextGzip', 1, 1, 60),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'CodeSourcePathsJsonGzip', 1, 1, 70),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'TextSourcePathsJsonGzip', 1, 1, 80),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'SourceFieldPathsJsonGzip', 1, 1, 90),
    (N'stg', N'MddMacAttributeEvidence', N'MddMacAttributeEvidence', N'SourceRepresentationJsonGzip', 1, 1, 100),
    (N'stg', N'MddMacResponseError', N'MddMacResponseError', N'ErrorValue', 0, 0, 10),
    (N'stg', N'MddMacParserGap', N'MddMacParserGap', N'SourceRepresentationJson', 0, 0, 10),
    (N'stg', N'MddMacParserGap', N'MddMacParserGap', N'SourceRepresentationJsonGzip', 1, 1, 20);

DECLARE @RepresentativePayloadColumnStatus TABLE
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    FamilyName nvarchar(128) NOT NULL,
    ColumnName sysname NOT NULL,
    IsOptional bit NOT NULL,
    IsGzip bit NOT NULL,
    ColumnOrdinal int NOT NULL,
    PhysicalColumnId int NULL,
    DataType sysname NULL,
    MaxLength smallint NULL,
    IsNullable bit NULL,
    IsPresent bit NOT NULL
);

INSERT INTO @RepresentativePayloadColumnStatus
(
    SchemaName, ObjectName, FamilyName, ColumnName,
    IsOptional, IsGzip, ColumnOrdinal,
    PhysicalColumnId, DataType, MaxLength, IsNullable, IsPresent
)
SELECT
    expected.SchemaName,
    expected.ObjectName,
    expected.FamilyName,
    expected.ColumnName,
    expected.IsOptional,
    expected.IsGzip,
    expected.ColumnOrdinal,
    col.column_id,
    typ.name,
    col.max_length,
    col.is_nullable,
    CONVERT(bit, CASE WHEN col.column_id IS NULL THEN 0 ELSE 1 END)
FROM @RepresentativePayloadColumns AS expected
LEFT JOIN sys.objects AS obj
    ON obj.schema_id = SCHEMA_ID(expected.SchemaName)
   AND obj.name = expected.ObjectName
   AND obj.type = N'U'
LEFT JOIN sys.columns AS col
    ON col.object_id = obj.object_id
   AND col.name = expected.ColumnName
LEFT JOIN sys.types AS typ
    ON typ.user_type_id = col.user_type_id;

SELECT
    N'capacity.representative_payload_schema' AS statement_id,
    SchemaName AS schema_name,
    ObjectName AS object_name,
    FamilyName AS family_name,
    ColumnName AS column_name,
    IsOptional AS is_optional,
    IsGzip AS is_gzip,
    CASE WHEN IsPresent = 1 THEN N'PRESENT'
         WHEN IsOptional = 1 THEN N'MISSING_OPTIONAL'
         ELSE N'MISSING_REQUIRED' END AS column_status,
    PhysicalColumnId AS physical_column_id,
    DataType AS data_type,
    MaxLength AS max_length,
    IsNullable AS is_nullable
FROM @RepresentativePayloadColumnStatus
ORDER BY FamilyName, ColumnOrdinal;

/* Keep the snapshot population result executable on V1/V2-only schemas. */
DECLARE @SnapshotPopulationSql nvarchar(max);
DECLARE @SnapshotJsonPresent bit;
DECLARE @SnapshotGzipPresent bit;

SELECT @SnapshotJsonPresent = IsPresent
FROM @RepresentativePayloadColumnStatus
WHERE ObjectName = N'MddMacSourceSnapshot'
  AND ColumnName = N'SnapshotJson';

SELECT @SnapshotGzipPresent = IsPresent
FROM @RepresentativePayloadColumnStatus
WHERE ObjectName = N'MddMacSourceSnapshot'
  AND ColumnName = N'SnapshotJsonGzip';

IF COALESCE(@SnapshotJsonPresent, 0) = 1
BEGIN
    SET @SnapshotPopulationSql = N'
SELECT
    N''capacity.snapshot_population'' AS statement_id,
    N''PASS'' AS population_status,
    N''' + CASE WHEN @SnapshotGzipPresent = 1 THEN N'PRESENT' ELSE N'MISSING_OPTIONAL' END + N''' AS gzip_column_status,
    COUNT_BIG(*) AS snapshot_count,
    MIN(SnapshotId) AS first_snapshot_id,
    MAX(SnapshotId) AS last_snapshot_id,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJson IS NOT NULL THEN 1 ELSE 0 END)) AS plaintext_snapshot_rows,
';

    IF @SnapshotGzipPresent = 1
    BEGIN
        SET @SnapshotPopulationSql = @SnapshotPopulationSql + N'
    SUM(CONVERT(bigint, CASE WHEN SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)) AS gzip_snapshot_rows,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END)) AS missing_snapshot_payload_rows,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(SnapshotJson), 0))) AS plaintext_snapshot_bytes,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(SnapshotJsonGzip), 0))) AS gzip_snapshot_bytes,
    CAST(NULL AS sysname) AS missing_required_column
FROM stg.MddMacSourceSnapshot;';
    END
    ELSE
    BEGIN
        SET @SnapshotPopulationSql = @SnapshotPopulationSql + N'
    CONVERT(bigint, 0) AS gzip_snapshot_rows,
    SUM(CONVERT(bigint, CASE WHEN SnapshotJson IS NULL THEN 1 ELSE 0 END)) AS missing_snapshot_payload_rows,
    SUM(CONVERT(bigint, COALESCE(DATALENGTH(SnapshotJson), 0))) AS plaintext_snapshot_bytes,
    CONVERT(bigint, 0) AS gzip_snapshot_bytes,
    CAST(NULL AS sysname) AS missing_required_column
FROM stg.MddMacSourceSnapshot;';
    END;

    EXEC sys.sp_executesql @SnapshotPopulationSql;
END
ELSE
BEGIN
    SELECT
        N'capacity.snapshot_population' AS statement_id,
        N'BLOCKED_MISSING_REQUIRED_COLUMN' AS population_status,
        N'NOT_EVALUATED' AS gzip_column_status,
        CAST(NULL AS bigint) AS snapshot_count,
        CAST(NULL AS bigint) AS first_snapshot_id,
        CAST(NULL AS bigint) AS last_snapshot_id,
        CAST(NULL AS bigint) AS plaintext_snapshot_rows,
        CAST(NULL AS bigint) AS gzip_snapshot_rows,
        CAST(NULL AS bigint) AS missing_snapshot_payload_rows,
        CAST(NULL AS bigint) AS plaintext_snapshot_bytes,
        CAST(NULL AS bigint) AS gzip_snapshot_bytes,
        N'SnapshotJson' AS missing_required_column;
END;

SELECT
    N'capacity.snapshot_ids_complete' AS statement_id,
    SnapshotId,
    ROW_NUMBER() OVER (ORDER BY SnapshotId) AS selection_ordinal
FROM stg.MddMacSourceSnapshot
ORDER BY SnapshotId;

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

/*
    Actual TimingObservation column-byte accounting.

    DATALENGTH is measured against every physical value, not inferred from
    declared max_length and not estimated from JSON text.  NULL contributes
    zero bytes but is counted separately, so a later compact design can prove
    that NULL and empty values were not conflated.  The category is a
    diagnostic grouping only; it does not authorize a schema change.
*/
IF OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
BEGIN
    SELECT
        N'capacity.timing_column_bytes' AS statement_id,
        N'BLOCKED_MISSING_TIMING_TABLE' AS audit_status;
END
ELSE
BEGIN
    DECLARE @TimingColumnValues nvarchar(max);
    DECLARE @TimingColumnBytesSql nvarchar(max);

    SELECT @TimingColumnValues = STUFF
    (
        (
            SELECT
                N',' + NCHAR(13) + NCHAR(10) +
                N'        (' + CONVERT(nvarchar(20), c.column_id) +
                N', N''' + REPLACE(c.name, N'''', N'''''') + N''',' +
                N' N''' + REPLACE(ty.name, N'''', N'''''') + N''',' +
                N' ' + CONVERT(nvarchar(20), c.max_length) +
                N', CONVERT(bit, ' + CONVERT(nvarchar(1), c.is_nullable) + N'),' +
                N' CONVERT(bit, ' + CONVERT(nvarchar(1), c.is_identity) + N'),' +
                N' N''' +
                CASE
                    WHEN c.name LIKE N'%SourcePath%' THEN N'SOURCE_PATH'
                    WHEN c.name LIKE N'%Identity%' THEN N'IDENTITY'
                    WHEN c.name LIKE N'%Json%' OR c.name LIKE N'%Gzip%' THEN N'JSON_PAYLOAD'
                    WHEN c.name IN
                    (
                        N'ResultId', N'ResultOrdinal', N'EventOrdinal', N'CallScope',
                        N'CallOrdinal', N'TimingContext', N'OperatingDayRef',
                        N'JourneyRef', N'VehicleJourneyRef', N'DatedVehicleJourneyRef',
                        N'LineRef', N'DirectionRef', N'OperatorRef', N'StopPointRef',
                        N'StopSeqNumber', N'TimetabledTimeSourceText',
                        N'EstimatedTimeSourceText', N'EstimatedTimeLowSourceText',
                        N'EstimatedTimeHighSourceText', N'RecordedAtTimeSourceText'
                    ) THEN N'REPEATED_SCALAR'
                    ELSE N'KEY_OR_OTHER_SCALAR'
                END + N''',' +
                N' ' + CONVERT(nvarchar(20), COALESCE(index_counts.key_index_count, 0)) +
                N', ' + CONVERT(nvarchar(20), COALESCE(index_counts.include_index_count, 0)) +
                N', CONVERT(bigint, DATALENGTH(t.' + QUOTENAME(c.name) + N')))'
            FROM sys.columns AS c
            INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
            LEFT JOIN
            (
                SELECT
                    ic.object_id,
                    ic.column_id,
                    COUNT(DISTINCT CASE WHEN ic.is_included_column = 0 THEN ic.index_id END)
                        AS key_index_count,
                    COUNT(DISTINCT CASE WHEN ic.is_included_column = 1 THEN ic.index_id END)
                        AS include_index_count
                FROM sys.index_columns AS ic
                WHERE ic.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
                GROUP BY ic.object_id, ic.column_id
            ) AS index_counts
                ON index_counts.object_id = c.object_id
               AND index_counts.column_id = c.column_id
            WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
              AND c.is_computed = 0
            ORDER BY c.column_id
            FOR XML PATH(N''), TYPE
        ).value(N'.', N'nvarchar(max)'),
        1, 1, N''
    );

    SET @TimingColumnBytesSql = N'
;WITH ColumnValues AS
(
    SELECT v.column_id, v.column_name, v.data_type, v.max_length,
           v.is_nullable, v.is_identity, v.storage_category,
           v.key_index_count, v.include_index_count, v.value_bytes
    FROM stg.MddMacTimingObservation AS t
    CROSS APPLY
    (
        VALUES
' + @TimingColumnValues + N'
    ) AS v
    (
        column_id, column_name, data_type, max_length, is_nullable,
        is_identity, storage_category, key_index_count, include_index_count,
        value_bytes
    )
), Aggregated AS
(
    SELECT column_id, column_name, data_type, max_length, is_nullable,
           is_identity, storage_category, key_index_count, include_index_count,
           COUNT_BIG(CASE WHEN value_bytes IS NOT NULL THEN 1 END) AS non_null_rows,
           COUNT_BIG(CASE WHEN value_bytes IS NULL THEN 1 END) AS null_rows,
           COALESCE(SUM(value_bytes), CONVERT(bigint, 0)) AS actual_value_bytes
    FROM ColumnValues
    GROUP BY column_id, column_name, data_type, max_length, is_nullable,
             is_identity, storage_category, key_index_count, include_index_count
)
SELECT
    N''capacity.timing_column_bytes'' AS statement_id,
    column_id,
    column_name,
    data_type,
    max_length,
    is_nullable,
    is_identity,
    storage_category,
    key_index_count,
    include_index_count,
    non_null_rows,
    null_rows,
    actual_value_bytes,
    CONVERT(decimal(19, 3),
        actual_value_bytes * CONVERT(decimal(28, 6), 100.0)
        / NULLIF(SUM(actual_value_bytes) OVER (), 0)) AS percent_of_actual_column_bytes,
    CONVERT(decimal(19, 3),
        actual_value_bytes * CONVERT(decimal(28, 6), 1.0)
        / NULLIF(non_null_rows, 0)) AS average_non_null_value_bytes
FROM Aggregated
ORDER BY column_id;';

    EXEC sys.sp_executesql @TimingColumnBytesSql;
END;

/*
    Index design and physical allocation for TimingObservation.  The result
    is one row per index column, which keeps key order, INCLUDE membership,
    uniqueness, filters, compression, and allocation auditable without
    returning any observation payload.
*/
IF OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
BEGIN
    SELECT
        N'capacity.timing_index_design' AS statement_id,
        N'BLOCKED_MISSING_TIMING_TABLE' AS audit_status;
END
ELSE
BEGIN
    SELECT
        N'capacity.timing_index_design' AS statement_id,
        idx.index_id,
        idx.name AS index_name,
        idx.type_desc AS index_type,
        idx.is_unique,
        idx.is_primary_key,
        idx.is_unique_constraint,
        idx.has_filter,
        MAX(CONVERT(nvarchar(4000), idx.filter_definition)) AS filter_definition,
        ic.index_column_id,
        ic.key_ordinal,
        CONVERT(bit, ic.is_included_column) AS is_included_column,
        col.name AS column_name,
        typ.name AS data_type,
        col.max_length,
        MAX(partition_info.data_compression_desc) AS data_compression_desc,
        COALESCE(MAX(allocation.reserved_bytes), CONVERT(bigint, 0)) AS reserved_bytes,
        COALESCE(MAX(allocation.used_bytes), CONVERT(bigint, 0)) AS used_bytes,
        COALESCE(MAX(allocation.row_count), CONVERT(bigint, 0)) AS row_count
    FROM sys.indexes AS idx
    LEFT JOIN sys.index_columns AS ic
        ON ic.object_id = idx.object_id AND ic.index_id = idx.index_id
    LEFT JOIN sys.columns AS col
        ON col.object_id = ic.object_id AND col.column_id = ic.column_id
    LEFT JOIN sys.types AS typ ON typ.user_type_id = col.user_type_id
    OUTER APPLY
    (
        SELECT
            MAX(p.data_compression_desc) AS data_compression_desc
        FROM sys.partitions AS p
        WHERE p.object_id = idx.object_id AND p.index_id = idx.index_id
    ) AS partition_info
    OUTER APPLY
    (
        SELECT
            SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192)
                AS reserved_bytes,
            SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192)
                AS used_bytes,
            SUM(CONVERT(bigint, ps.row_count)) AS row_count
        FROM sys.dm_db_partition_stats AS ps
        WHERE ps.object_id = idx.object_id AND ps.index_id = idx.index_id
    ) AS allocation
    WHERE idx.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
      AND idx.index_id > 0
    GROUP BY idx.index_id, idx.name, idx.type_desc, idx.is_unique,
             idx.is_primary_key, idx.is_unique_constraint, idx.has_filter,
             ic.index_column_id, ic.key_ordinal,
             ic.is_included_column, col.name, typ.name, col.max_length
    ORDER BY idx.index_id, ic.key_ordinal, ic.index_column_id;
END;

/*
    SQL can identify the C: allocation that a separate VMware disk could
    potentially receive, but it cannot prove that a VMDK was attached,
    formatted, backed up, or granted to the SQL Server service.  Keep those
    facts as an explicit external gate rather than guessing them here.
*/
;WITH RelocationFiles AS
(
    SELECT
        N'USER_DATABASE' AS file_scope,
        df.file_id,
        df.name AS logical_file_name,
        df.type_desc AS file_type,
        df.physical_name,
        vs.volume_mount_point,
        CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
        CONVERT(bigint, vs.available_bytes) AS source_volume_available_bytes
    FROM sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
    UNION ALL
    SELECT
        N'TEMPDB' AS file_scope,
        df.file_id,
        df.name,
        df.type_desc,
        df.physical_name,
        vs.volume_mount_point,
        CONVERT(bigint, df.size) * CONVERT(bigint, 8192),
        CONVERT(bigint, vs.available_bytes)
    FROM tempdb.sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
)
SELECT
    N'capacity.vmware_relocation_assessment' AS statement_id,
    file_scope,
    file_id,
    logical_file_name,
    file_type,
    physical_name,
    volume_mount_point AS source_volume,
    allocated_bytes,
    source_volume_available_bytes,
    CONVERT(bit, CASE WHEN UPPER(LEFT(volume_mount_point, 2)) = N'C:' THEN 1 ELSE 0 END)
        AS on_windows_c,
    CONVERT(bigint, CASE
        WHEN file_scope = N'USER_DATABASE'
         AND UPPER(LEFT(volume_mount_point, 2)) = N'C:' THEN allocated_bytes
        ELSE 0 END) AS estimated_c_bytes_recovered_if_moved,
    SUM(CASE
        WHEN file_scope = N'USER_DATABASE'
         AND UPPER(LEFT(volume_mount_point, 2)) = N'C:' THEN allocated_bytes
        ELSE CONVERT(bigint, 0) END) OVER () AS user_database_c_target_free_required_bytes,
    CASE
        WHEN file_scope = N'TEMPDB' THEN N'TEMPDB_MOVE_REQUIRES_PLANNED_SQL_RESTART'
        WHEN file_type = N'LOG' THEN N'USER_DATABASE_FILE_MOVE_REQUIRES_OFFLINE_WINDOW'
        ELSE N'USER_DATABASE_FILE_MOVE_REQUIRES_OFFLINE_WINDOW'
    END AS relocation_prerequisite,
    N'NOT_VERIFIED_VMWARE_DESTINATION' AS assessment_status
FROM RelocationFiles
ORDER BY file_scope, file_id;

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
    N'capacity.representative_payload_audit' AS statement_id,
    CASE WHEN EXISTS
         (
             SELECT 1
             FROM @RepresentativePayloadColumnStatus
             WHERE IsOptional = 0 AND IsPresent = 0
         )
         THEN N'BLOCKED_MISSING_REQUIRED_COLUMNS' ELSE N'PASS' END AS audit_status,
    COALESCE(SUM(CONVERT(bigint, CASE WHEN IsOptional = 1 AND IsPresent = 0 THEN 1 ELSE 0 END)), 0)
        AS missing_optional_column_count,
    COALESCE(SUM(CONVERT(bigint, CASE WHEN IsOptional = 0 AND IsPresent = 0 THEN 1 ELSE 0 END)), 0)
        AS missing_required_column_count
FROM @RepresentativePayloadColumnStatus;

DECLARE @RepresentativePayloadSql nvarchar(max) = N'';
DECLARE @RepresentativePayloadUnion nvarchar(max) = N'';
DECLARE @PayloadSchemaName sysname;
DECLARE @PayloadFamilyName nvarchar(128);
DECLARE @PayloadObjectName sysname;
DECLARE @PlainExpression nvarchar(max);
DECLARE @GzipExpression nvarchar(max);

DECLARE representative_payload_family_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT SchemaName, FamilyName, ObjectName
FROM @RepresentativePayloadColumnStatus
GROUP BY SchemaName, FamilyName, ObjectName
ORDER BY SchemaName, FamilyName, ObjectName;

OPEN representative_payload_family_cursor;
FETCH NEXT FROM representative_payload_family_cursor
    INTO @PayloadSchemaName, @PayloadFamilyName, @PayloadObjectName;

WHILE @@FETCH_STATUS = 0
BEGIN
    IF NOT EXISTS
       (
           SELECT 1
           FROM @RepresentativePayloadColumnStatus
           WHERE SchemaName = @PayloadSchemaName
             AND FamilyName = @PayloadFamilyName
             AND ObjectName = @PayloadObjectName
             AND IsOptional = 0
             AND IsPresent = 0
       )
    BEGIN
        SELECT @PlainExpression = STUFF(
            (
                SELECT N' + CONVERT(bigint, COALESCE(DATALENGTH(x.'
                       + QUOTENAME(pc.ColumnName) + N'), 0))'
                FROM @RepresentativePayloadColumnStatus AS pc
                WHERE pc.SchemaName = @PayloadSchemaName
                  AND pc.FamilyName = @PayloadFamilyName
                  AND pc.ObjectName = @PayloadObjectName
                  AND pc.IsGzip = 0
                  AND pc.IsPresent = 1
                ORDER BY pc.ColumnOrdinal
                FOR XML PATH(''), TYPE
            ).value(N'.', N'nvarchar(max)'),
            1,
            3,
            N''
        );
        SET @PlainExpression = COALESCE(@PlainExpression, N'CONVERT(bigint, 0)');

        SELECT @GzipExpression = STUFF(
            (
                SELECT N' + CONVERT(bigint, COALESCE(DATALENGTH(x.'
                       + QUOTENAME(pc.ColumnName) + N'), 0))'
                FROM @RepresentativePayloadColumnStatus AS pc
                WHERE pc.SchemaName = @PayloadSchemaName
                  AND pc.FamilyName = @PayloadFamilyName
                  AND pc.ObjectName = @PayloadObjectName
                  AND pc.IsGzip = 1
                  AND pc.IsPresent = 1
                ORDER BY pc.ColumnOrdinal
                FOR XML PATH(''), TYPE
            ).value(N'.', N'nvarchar(max)'),
            1,
            3,
            N''
        );
        SET @GzipExpression = COALESCE(@GzipExpression, N'CONVERT(bigint, 0)');

        SET @RepresentativePayloadUnion = @RepresentativePayloadUnion
            + CASE WHEN LEN(@RepresentativePayloadUnion) = 0 THEN N''
                   ELSE NCHAR(13) + NCHAR(10) + N'UNION ALL' + NCHAR(13) + NCHAR(10) END
            + N'SELECT N''' + @PayloadFamilyName + N''' AS family_name,
       x.SnapshotId,
       COUNT_BIG(*) AS row_count,
       SUM(' + @PlainExpression + N') AS plain_lob_bytes,
       SUM(' + @GzipExpression + N') AS gzip_lob_bytes,
       SUM(' + @PlainExpression + N') + SUM(' + @GzipExpression + N') AS total_payload_bytes
FROM ' + QUOTENAME(@PayloadSchemaName) + N'.' + QUOTENAME(@PayloadObjectName) + N' AS x
WHERE x.SnapshotId IN
(
    SELECT SnapshotId
    FROM
    (
        SELECT SnapshotId,
               ROW_NUMBER() OVER (ORDER BY SnapshotId) AS ordinal,
               COUNT_BIG(*) OVER () AS population_count
        FROM ' + QUOTENAME(@PayloadSchemaName) + N'.' + QUOTENAME(N'MddMacSourceSnapshot') + N'
    ) AS sample
    WHERE ordinal = 1
       OR ordinal = (population_count + 1) / 2
       OR ordinal = population_count
)
GROUP BY x.SnapshotId';
    END;

    FETCH NEXT FROM representative_payload_family_cursor
        INTO @PayloadSchemaName, @PayloadFamilyName, @PayloadObjectName;
END;

CLOSE representative_payload_family_cursor;
DEALLOCATE representative_payload_family_cursor;

IF LEN(@RepresentativePayloadUnion) > 0
BEGIN
    SET @RepresentativePayloadSql = N'
SELECT
    N''capacity.representative_observation_sizes'' AS statement_id,
    representative.family_name,
    representative.SnapshotId,
    representative.row_count,
    representative.plain_lob_bytes,
    representative.gzip_lob_bytes,
    representative.total_payload_bytes
FROM
(
' + @RepresentativePayloadUnion + N'
) AS representative
ORDER BY representative.family_name, representative.SnapshotId;';

    EXEC sys.sp_executesql @RepresentativePayloadSql;
END
ELSE
BEGIN
    SELECT
        N'capacity.representative_observation_sizes' AS statement_id,
        N'BLOCKED_MISSING_REQUIRED_COLUMNS' AS audit_status;
END;

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
    COL_NAME(dep.referenced_id, dep.referenced_minor_id) AS referenced_minor_name,
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
    IF COL_LENGTH(N'stg.MddMacCompactStorageState', N'MigrationImplementationVersion') IS NULL
        SELECT N'capacity.compact_gate_state' AS statement_id,
               StateId, StorageFormatVersion,
               CAST(NULL AS VARCHAR(100)) AS MigrationImplementationVersion,
               FutureWriteEnabled, CompatibilityVerified,
               HistoricalReadbackVerified, LegacyRetirementApproved,
               UpdatedAtUtc, UpdatedBy
        FROM stg.MddMacCompactStorageState
        WHERE StateId = 1;
    ELSE
        EXEC sys.sp_executesql N'
            SELECT N''capacity.compact_gate_state'' AS statement_id,
                   StateId, StorageFormatVersion, MigrationImplementationVersion,
                   FutureWriteEnabled, CompatibilityVerified,
                   HistoricalReadbackVerified, LegacyRetirementApproved,
                   UpdatedAtUtc, UpdatedBy
            FROM stg.MddMacCompactStorageState
            WHERE StateId = 1;';
END
ELSE
BEGIN
    SELECT N'capacity.compact_gate_state' AS statement_id,
           N'NOT_INSTALLED' AS status;
END;

SELECT
    N'capacity.migration_implementation' AS statement_id,
    N'compact-historical-migration-v2' AS migration_implementation_version,
    CONVERT(bit, 1) AS read_only_measurement;

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
               >= CONVERT(bigint, 12) * 1024 * 1024 * 1024
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
    CONVERT(bigint, 12) * 1024 * 1024 * 1024 AS required_c_floor_bytes,
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

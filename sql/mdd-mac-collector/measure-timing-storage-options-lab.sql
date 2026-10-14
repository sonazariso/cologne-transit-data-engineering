/*
    COLOGNE TRANSIT INTELLIGENCE — TIMING STORAGE OPTIONS LAB MEASUREMENT

    This is an isolated-lab measurement, not a production migration.  It
    creates and rebuilds disposable candidate tables in the lab database so
    SQL Server reports actual reserved pages for:

      1. the current TimingObservation row shape with the source indexes and
         lossless PAGE compression; and
      2. a lossless dictionary candidate with one row per observation,
         dictionary references for every variable-width physical column, and
         the original fixed-width values retained in the base row.

    The candidate tables preserve every source row by TimingObservationId.
    NULL has no dictionary reference, while an empty value has a reference to
    a zero-byte dictionary value.  VARBINARY values are copied byte-for-byte,
    so the original JSON and GZIP companions are not replaced by a lossy
    textual estimate.

    The script is intentionally disabled.  It is safe to enable only on a
    restored, isolated SQL Server copy whose data, log, and tempdb files are
    all on a non-C: volume.  It must never be run on DataAnalyst-VM.  The
    candidate tables may consume substantial data-file, log, tempdb, and
    transaction space; keep the lab recovery point until the results are
    captured and reviewed.

    The allocation result is checkpointed after each physical operation.  A
    separate read-only sampler should poll the same file/log/tempdb DMVs while
    a long CREATE INDEX or ALTER INDEX is running; the reported
    observed_checkpoint_peak is not silently relabeled as an in-flight peak.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @RunLab bit = 0;
DECLARE @ServerName nvarchar(128) = CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName'));
DECLARE @DatabaseName sysname = DB_NAME();

IF @RunLab <> 1
    THROW 51500, 'Timing storage lab measurement is disabled; set @RunLab = 1 only in the isolated lab.', 1;
IF LOWER(@ServerName) = N'dataanalyst-vm' OR LOWER(@ServerName) LIKE N'dataanalyst-vm%'
    THROW 51501, 'Timing storage lab measurement cannot run on the production SQL Server.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
)
   OR EXISTS
(
    SELECT 1
    FROM tempdb.sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
    WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
)
    THROW 51502, 'Timing storage lab requires SQL data, log, and tempdb files on non-C: volumes.', 1;
IF OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
    THROW 51503, 'TimingObservation source table is missing in the lab copy.', 1;
IF (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation) = 0
    THROW 51504, 'TimingObservation source table is empty; a full-history lab measurement is required.', 1;

IF SCHEMA_ID(N'lab') IS NULL
    EXEC(N'CREATE SCHEMA lab AUTHORIZATION dbo;');

/* Disposable lab objects from an interrupted measurement may be replaced. */
IF OBJECT_ID(N'lab.TimingPageCandidate', N'U') IS NOT NULL DROP TABLE lab.TimingPageCandidate;
IF OBJECT_ID(N'lab.TimingDictionaryReference', N'U') IS NOT NULL DROP TABLE lab.TimingDictionaryReference;
IF OBJECT_ID(N'lab.TimingDictionaryValue', N'U') IS NOT NULL DROP TABLE lab.TimingDictionaryValue;
IF OBJECT_ID(N'lab.TimingDictionaryBase', N'U') IS NOT NULL DROP TABLE lab.TimingDictionaryBase;
IF OBJECT_ID(N'lab.TimingDictionaryColumn', N'U') IS NOT NULL DROP TABLE lab.TimingDictionaryColumn;

CREATE TABLE #StorageMeasurements
(
    phase nvarchar(80) NOT NULL,
    measured_at_utc datetime2(7) NOT NULL,
    source_reserved_pages bigint NULL,
    page_candidate_reserved_pages bigint NULL,
    page_candidate_used_pages bigint NULL,
    dictionary_base_reserved_pages bigint NULL,
    dictionary_value_reserved_pages bigint NULL,
    dictionary_reference_reserved_pages bigint NULL,
    database_data_allocated_bytes bigint NULL,
    database_log_allocated_bytes bigint NULL,
    tempdb_allocated_bytes bigint NULL,
    transaction_log_used_bytes bigint NULL
);

DECLARE @MeasureSql nvarchar(max) = N'
INSERT #StorageMeasurements
(
    phase, measured_at_utc, source_reserved_pages,
    page_candidate_reserved_pages, page_candidate_used_pages,
    dictionary_base_reserved_pages, dictionary_value_reserved_pages,
    dictionary_reference_reserved_pages, database_data_allocated_bytes,
    database_log_allocated_bytes, tempdb_allocated_bytes,
    transaction_log_used_bytes
)
SELECT
    @Phase, SYSUTCDATETIME(),
    (SELECT SUM(CONVERT(bigint, reserved_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''stg.MddMacTimingObservation'')),
    (SELECT SUM(CONVERT(bigint, reserved_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''lab.TimingPageCandidate'')),
    (SELECT SUM(CONVERT(bigint, used_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''lab.TimingPageCandidate'')),
    (SELECT SUM(CONVERT(bigint, reserved_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''lab.TimingDictionaryBase'')),
    (SELECT SUM(CONVERT(bigint, reserved_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''lab.TimingDictionaryValue'')),
    (SELECT SUM(CONVERT(bigint, reserved_page_count))
     FROM sys.dm_db_partition_stats
     WHERE object_id = OBJECT_ID(N''lab.TimingDictionaryReference'')),
    (SELECT SUM(CONVERT(bigint, size)) * CONVERT(bigint, 8192)
     FROM sys.database_files WHERE type = 0),
    (SELECT SUM(CONVERT(bigint, size)) * CONVERT(bigint, 8192)
     FROM sys.database_files WHERE type = 1),
    (SELECT SUM(CONVERT(bigint, size)) * CONVERT(bigint, 8192)
     FROM tempdb.sys.database_files),
    (SELECT CONVERT(bigint, used_log_space_in_bytes)
     FROM sys.dm_db_log_space_usage);';

DECLARE @Phase nvarchar(80) = N'BEFORE';
EXEC sys.sp_executesql @MeasureSql, N'@Phase nvarchar(80)', @Phase = @Phase;

/* Candidate A: complete source row shape and equivalent source indexes. */
SELECT *
INTO lab.TimingPageCandidate
FROM stg.MddMacTimingObservation;

CREATE CLUSTERED INDEX CX_lab_TimingPageCandidate
    ON lab.TimingPageCandidate (TimingObservationId)
    WITH (DATA_COMPRESSION = NONE, SORT_IN_TEMPDB = ON);
CREATE UNIQUE INDEX UX_lab_TimingPageCandidate_Occurrence
    ON lab.TimingPageCandidate
       (SnapshotId, EventOrdinal, CallScope, CallOrdinal, TimingContext)
    WITH (DATA_COMPRESSION = NONE, SORT_IN_TEMPDB = ON);
CREATE INDEX IX_lab_TimingPageCandidate_Identity_Observed
    ON lab.TimingPageCandidate (SourceEventIdentity, TimingContext, SnapshotId)
    WITH (DATA_COMPRESSION = NONE, SORT_IN_TEMPDB = ON);

SET @Phase = N'PAGE_CANDIDATE_ALL_INDEXES_NONE';
EXEC sys.sp_executesql @MeasureSql, N'@Phase nvarchar(80)', @Phase = @Phase;

ALTER INDEX ALL ON lab.TimingPageCandidate
    REBUILD WITH (DATA_COMPRESSION = PAGE, SORT_IN_TEMPDB = ON);

SET @Phase = N'PAGE_CANDIDATE_ALL_INDEXES_PAGE';
EXEC sys.sp_executesql @MeasureSql, N'@Phase nvarchar(80)', @Phase = @Phase;

/*
    Candidate B: dictionary references for all variable-width physical
    columns.  The fixed-width projection is generated from sys.columns so a
    lab restored before/after Storage V3 still measures its real contract.
*/
DECLARE @VariableCellSql nvarchar(max);
DECLARE @FixedColumnList nvarchar(max);

SELECT @VariableCellSql = STUFF
(
    (
        SELECT
            N' UNION ALL ' +
            N'SELECT s.TimingObservationId, ' + CONVERT(nvarchar(20), c.column_id) +
            N', N''' + REPLACE(c.name, N'''', N'''''') + N''',' +
            N' CONVERT(varbinary(max), s.' + QUOTENAME(c.name) + N')' +
            N' FROM stg.MddMacTimingObservation AS s' +
            N' WHERE s.' + QUOTENAME(c.name) + N' IS NOT NULL'
        FROM sys.columns AS c
        INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
        WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
          AND c.is_computed = 0
          AND ty.name IN (N'varchar', N'nvarchar', N'char', N'nchar', N'varbinary', N'binary')
        ORDER BY c.column_id
        FOR XML PATH(N''), TYPE
    ).value(N'.', N'nvarchar(max)'),
    1, 11, N''
);

SELECT @FixedColumnList = STUFF
(
    (
        SELECT N', ' + QUOTENAME(c.name)
        FROM sys.columns AS c
        INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
        WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
          AND c.is_computed = 0
          AND ty.name NOT IN (N'varchar', N'nvarchar', N'char', N'nchar', N'varbinary', N'binary')
        ORDER BY c.column_id
        FOR XML PATH(N''), TYPE
    ).value(N'.', N'nvarchar(max)'),
    1, 2, N''
);

IF NULLIF(@VariableCellSql, N'') IS NULL OR NULLIF(@FixedColumnList, N'') IS NULL
    THROW 51505, 'The lab dictionary candidate could not derive variable and fixed Timing columns.', 1;

SELECT
    c.column_id,
    c.name AS column_name,
    ty.name AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.is_identity
INTO lab.TimingDictionaryColumn
FROM sys.columns AS c
INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
  AND c.is_computed = 0;
ALTER TABLE lab.TimingDictionaryColumn
    ADD CONSTRAINT PK_lab_TimingDictionaryColumn PRIMARY KEY CLUSTERED (column_id);

CREATE TABLE #DictionaryCells
(
    TimingObservationId bigint NOT NULL,
    ColumnId int NOT NULL,
    FieldName sysname NOT NULL,
    ValueBytes varbinary(max) NOT NULL
);

SET @VariableCellSql = N'
INSERT #DictionaryCells (TimingObservationId, ColumnId, FieldName, ValueBytes)
' + @VariableCellSql + N';';
EXEC sys.sp_executesql @VariableCellSql;

SELECT
    TimingObservationId,
    ColumnId,
    FieldName,
    HASHBYTES(N'SHA2_256', ValueBytes) AS ValueHash,
    CONVERT(bigint, DATALENGTH(ValueBytes)) AS ValueLength,
    ValueBytes
INTO #DictionaryCellsHashed
FROM #DictionaryCells;

SELECT
    IDENTITY(bigint, 1, 1) AS DictionaryId,
    FieldName,
    ValueHash,
    ValueLength,
    ValueBytes
INTO lab.TimingDictionaryValue
FROM
(
    SELECT DISTINCT FieldName, ValueHash, ValueLength, ValueBytes
    FROM #DictionaryCellsHashed
) AS distinct_values;

ALTER TABLE lab.TimingDictionaryValue
    ADD CONSTRAINT PK_lab_TimingDictionaryValue PRIMARY KEY CLUSTERED (DictionaryId);
CREATE INDEX IX_lab_TimingDictionaryValue_Lookup
    ON lab.TimingDictionaryValue (FieldName, ValueHash, ValueLength)
    WITH (DATA_COMPRESSION = NONE);

SELECT
    h.TimingObservationId,
    h.ColumnId,
    v.DictionaryId
INTO lab.TimingDictionaryReference
FROM #DictionaryCellsHashed AS h
INNER JOIN lab.TimingDictionaryValue AS v
    ON v.FieldName = h.FieldName
   AND v.ValueHash = h.ValueHash
   AND v.ValueLength = h.ValueLength
   AND v.ValueBytes = h.ValueBytes;

ALTER TABLE lab.TimingDictionaryReference
    ADD CONSTRAINT PK_lab_TimingDictionaryReference
        PRIMARY KEY CLUSTERED (TimingObservationId, ColumnId);
CREATE INDEX IX_lab_TimingDictionaryReference_Dictionary
    ON lab.TimingDictionaryReference (ColumnId, DictionaryId)
    WITH (DATA_COMPRESSION = NONE);

DECLARE @CreateBaseSql nvarchar(max) =
    N'SELECT ' + @FixedColumnList +
    N' INTO lab.TimingDictionaryBase' +
    N' FROM stg.MddMacTimingObservation;';
EXEC sys.sp_executesql @CreateBaseSql;

CREATE CLUSTERED INDEX CX_lab_TimingDictionaryBase
    ON lab.TimingDictionaryBase (TimingObservationId)
    WITH (DATA_COMPRESSION = NONE, SORT_IN_TEMPDB = ON);

SET @Phase = N'DICTIONARY_CANDIDATE_INDEXED_NONE';
EXEC sys.sp_executesql @MeasureSql, N'@Phase nvarchar(80)', @Phase = @Phase;

/* Capture the full physical allocation result, including reserved pages. */
SELECT
    N'capacity.timing_storage_option_allocation' AS statement_id,
    phase,
    measured_at_utc,
    source_reserved_pages,
    page_candidate_reserved_pages,
    page_candidate_used_pages,
    dictionary_base_reserved_pages,
    dictionary_value_reserved_pages,
    dictionary_reference_reserved_pages,
    database_data_allocated_bytes,
    database_log_allocated_bytes,
    tempdb_allocated_bytes,
    transaction_log_used_bytes
FROM #StorageMeasurements
ORDER BY measured_at_utc, phase;

/* Actual reserved-page comparison from the lab checkpoints. */
;WITH Options AS
(
    SELECT
        MAX(CASE WHEN phase = N'PAGE_CANDIDATE_ALL_INDEXES_NONE'
                 THEN page_candidate_reserved_pages END) AS none_pages,
        MAX(CASE WHEN phase = N'PAGE_CANDIDATE_ALL_INDEXES_PAGE'
                 THEN page_candidate_reserved_pages END) AS page_pages,
        MAX(CASE WHEN phase = N'DICTIONARY_CANDIDATE_INDEXED_NONE'
                 THEN COALESCE(dictionary_base_reserved_pages, 0)
                    + COALESCE(dictionary_value_reserved_pages, 0)
                    + COALESCE(dictionary_reference_reserved_pages, 0)
            END) AS dictionary_pages
    FROM #StorageMeasurements
)
SELECT
    N'capacity.timing_storage_option_savings' AS statement_id,
    comparison_name,
    baseline_reserved_pages,
    candidate_reserved_pages,
    baseline_reserved_pages - candidate_reserved_pages AS savings_reserved_pages,
    (baseline_reserved_pages - candidate_reserved_pages)
        * CONVERT(bigint, 8192) AS savings_bytes,
    CONVERT(decimal(19, 3),
        (baseline_reserved_pages - candidate_reserved_pages)
        * CONVERT(decimal(28, 6), 100.0)
        / NULLIF(baseline_reserved_pages, 0)) AS savings_percent,
    N'PASS_ACTUAL_RESERVED_PAGES' AS measurement_status
FROM Options
CROSS APPLY
(
    VALUES
        (N'PAGE_VS_UNCOMPRESSED_SOURCE_SHAPE', none_pages, page_pages),
        (N'DICTIONARY_VS_PAGE', page_pages, dictionary_pages),
        (N'DICTIONARY_VS_UNCOMPRESSED_SOURCE_SHAPE', none_pages, dictionary_pages)
) AS comparisons(comparison_name, baseline_reserved_pages, candidate_reserved_pages)
WHERE baseline_reserved_pages IS NOT NULL
  AND candidate_reserved_pages IS NOT NULL
ORDER BY comparison_name;

/*
    This is the high-water calculation from the captured checkpoints.  It is
    intentionally named observed_checkpoint_peak; an external sampler must
    replace it with an in-flight peak before the result can authorize a
    production migration.
*/
SELECT
    N'capacity.timing_storage_option_peak' AS statement_id,
    N'OBSERVED_CHECKPOINT_MAX' AS peak_measurement_status,
    MIN(CASE WHEN phase = N'BEFORE' THEN database_data_allocated_bytes END)
        AS baseline_database_data_allocated_bytes,
    MAX(database_data_allocated_bytes)
        - MIN(CASE WHEN phase = N'BEFORE' THEN database_data_allocated_bytes END)
        AS observed_peak_database_growth_bytes,
    MAX(tempdb_allocated_bytes)
        - MIN(CASE WHEN phase = N'BEFORE' THEN tempdb_allocated_bytes END)
        AS observed_peak_tempdb_growth_bytes,
    MAX(transaction_log_used_bytes)
        - MIN(CASE WHEN phase = N'BEFORE' THEN transaction_log_used_bytes END)
        AS observed_peak_transaction_log_growth_bytes,
    (
        MAX(database_data_allocated_bytes)
            - MIN(CASE WHEN phase = N'BEFORE' THEN database_data_allocated_bytes END)
        + MAX(tempdb_allocated_bytes)
            - MIN(CASE WHEN phase = N'BEFORE' THEN tempdb_allocated_bytes END)
        + MAX(transaction_log_used_bytes)
            - MIN(CASE WHEN phase = N'BEFORE' THEN transaction_log_used_bytes END)
    ) AS observed_checkpoint_peak_migration_space_bytes,
    N'REQUIRES_EXTERNAL_IN_FLIGHT_SAMPLER_BEFORE_CAPACITY_AUTHORIZATION'
        AS authorization_status;

/*
    Losslessness checks: fixed values compare by the original typed columns;
    variable-width values compare exact bytes through the dictionary.
*/
CREATE TABLE #LosslessMetrics
(
    metric_name sysname NOT NULL,
    metric_value bigint NOT NULL
);

DECLARE @FixedMismatchSql nvarchar(max) = N'
INSERT #LosslessMetrics (metric_name, metric_value)
SELECT N''fixed_source_row_count'', COUNT_BIG(*)
FROM stg.MddMacTimingObservation
UNION ALL
SELECT N''fixed_dictionary_row_count'', COUNT_BIG(*)
FROM lab.TimingDictionaryBase
UNION ALL
SELECT N''fixed_forward_mismatch_count'', COUNT_BIG(*)
FROM
(
    SELECT ' + @FixedColumnList + N' FROM stg.MddMacTimingObservation
    EXCEPT
    SELECT ' + @FixedColumnList + N' FROM lab.TimingDictionaryBase
) AS forward_mismatch
UNION ALL
SELECT N''fixed_reverse_mismatch_count'', COUNT_BIG(*)
FROM
(
    SELECT ' + @FixedColumnList + N' FROM lab.TimingDictionaryBase
    EXCEPT
    SELECT ' + @FixedColumnList + N' FROM stg.MddMacTimingObservation
) AS reverse_mismatch;';
EXEC sys.sp_executesql @FixedMismatchSql;

INSERT #LosslessMetrics (metric_name, metric_value)
SELECT N'variable_source_non_null_cell_count', COUNT_BIG(*)
FROM #DictionaryCellsHashed
UNION ALL
SELECT N'dictionary_reference_count', COUNT_BIG(*)
FROM lab.TimingDictionaryReference
UNION ALL
SELECT N'dictionary_roundtrip_mismatch_count', COUNT_BIG(*)
FROM #DictionaryCellsHashed AS h
LEFT JOIN lab.TimingDictionaryReference AS r
    ON r.TimingObservationId = h.TimingObservationId
   AND r.ColumnId = h.ColumnId
LEFT JOIN lab.TimingDictionaryValue AS v
    ON v.DictionaryId = r.DictionaryId
WHERE r.DictionaryId IS NULL
   OR v.ValueHash <> h.ValueHash
   OR v.ValueLength <> h.ValueLength
   OR v.ValueBytes <> h.ValueBytes
UNION ALL
SELECT N'dictionary_zero_length_values', COUNT_BIG(*)
FROM lab.TimingDictionaryValue
WHERE ValueLength = 0;

DECLARE @VariableNullCellSql nvarchar(max);
DECLARE @VariableZeroLengthCellSql nvarchar(max);
DECLARE @VariableNullCellCount bigint = 0;
DECLARE @VariableZeroLengthCellCount bigint = 0;

SELECT @VariableNullCellSql = STUFF
(
    (
        SELECT N' + (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation'
             + N' WHERE ' + QUOTENAME(c.name) + N' IS NULL)'
        FROM sys.columns AS c
        INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
        WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
          AND c.is_computed = 0
          AND ty.name IN (N'varchar', N'nvarchar', N'char', N'nchar', N'varbinary', N'binary')
        ORDER BY c.column_id
        FOR XML PATH(N''), TYPE
    ).value(N'.', N'nvarchar(max)'),
    1, 3, N''
);
SELECT @VariableZeroLengthCellSql = STUFF
(
    (
        SELECT N' + (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation'
             + N' WHERE ' + QUOTENAME(c.name) + N' IS NOT NULL'
             + N' AND DATALENGTH(' + QUOTENAME(c.name) + N') = 0)'
        FROM sys.columns AS c
        INNER JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
        WHERE c.object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
          AND c.is_computed = 0
          AND ty.name IN (N'varchar', N'nvarchar', N'char', N'nchar', N'varbinary', N'binary')
        ORDER BY c.column_id
        FOR XML PATH(N''), TYPE
    ).value(N'.', N'nvarchar(max)'),
    1, 3, N''
);
EXEC sys.sp_executesql
    N'SELECT @Out = ' + @VariableNullCellSql,
    N'@Out bigint OUTPUT',
    @Out = @VariableNullCellCount OUTPUT;
EXEC sys.sp_executesql
    N'SELECT @Out = ' + @VariableZeroLengthCellSql,
    N'@Out bigint OUTPUT',
    @Out = @VariableZeroLengthCellCount OUTPUT;
INSERT #LosslessMetrics (metric_name, metric_value)
VALUES
    (N'variable_source_null_cell_count', @VariableNullCellCount),
    (N'variable_source_zero_length_cell_count', @VariableZeroLengthCellCount);

SELECT
    N'capacity.timing_storage_losslessness' AS statement_id,
    metric_name,
    metric_value,
    CASE
        WHEN metric_name IN
        (
            N'fixed_forward_mismatch_count',
            N'fixed_reverse_mismatch_count',
            N'dictionary_roundtrip_mismatch_count'
        ) AND metric_value = 0 THEN N'PASS'
        WHEN metric_name IN
        (
            N'fixed_forward_mismatch_count',
            N'fixed_reverse_mismatch_count',
            N'dictionary_roundtrip_mismatch_count'
        ) THEN N'BLOCKED'
        ELSE N'MEASURED'
    END AS metric_status
FROM #LosslessMetrics
ORDER BY metric_name;
GO

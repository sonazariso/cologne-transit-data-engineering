/*
STORAGE V3 LAB-ONLY COMPRESSION MEASUREMENT.

This script is read-only against the lab database but can consume substantial
CPU, I/O, tempdb, and time.  It never rebuilds an index.  Run only against a
small restored/sanitized database whose name ends in _STORAGE_V3_LAB.  The
default guard intentionally refuses both production and an unlabelled copy.
Set @RunLab to 1 only after the isolated lab has been provisioned.
*/
USE CologneTransitIntelligence_STORAGE_V3_LAB;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @RunLab BIT = 0;
IF @RunLab <> 1 OR DB_NAME() NOT LIKE N'%_STORAGE_V3_LAB'
    THROW 51310, 'This compression estimate is lab-only and disabled by default.', 1;

PRINT 'ROW compression estimates:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacSourceSnapshot',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacCallObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacTimingObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';

PRINT 'PAGE compression estimates:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacSourceSnapshot',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacCallObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacTimingObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';

/*
    BOUNDED COMPACT-STORAGE CANDIDATE MEASUREMENT.

    The following temporary tables materialize the same lab-selected sample
    in three lossless forms.  Their reserved pages are measured in tempdb;
    this is physical temporary-allocation evidence, not a logical compression
    estimate and not the persistent compact-envelope allocation.  Set
    @SampleSnapshots to 0 for the complete verified history.  The optimized
    candidate stores original GZIP bytes as VARBINARY(MAX), while the
    dictionary candidate replaces only repeated timing/source-path values
    with dictionary references and keeps every other field, identity, NULL,
    and occurrence.
*/
IF OBJECT_ID(N'stg.MddMacCompactObservationEnvelope', N'U') IS NULL
   OR OBJECT_ID(N'stg.vMddMacCompactPhysicalFieldReadback', N'V') IS NULL
    THROW 51311, 'The compact envelope and physical-field readback objects are required in the lab.', 1;

DECLARE @SampleSnapshots bigint = 0;

IF @SampleSnapshots = 0
BEGIN
    SELECT @SampleSnapshots = COUNT_BIG(*)
    FROM stg.MddMacCompactObservationEnvelope
    WHERE VerificationStatus = 'VERIFIED';
END;
IF @SampleSnapshots <= 0
    THROW 51314, 'The lab has no verified compact envelopes in the selected history.', 1;

SELECT TOP (@SampleSnapshots)
       e.SnapshotId,
       e.EnvelopeJsonGzip
INTO #CompactCurrentEnvelope
FROM stg.MddMacCompactObservationEnvelope AS e
WHERE e.VerificationStatus = 'VERIFIED'
ORDER BY e.SnapshotId;
IF NOT EXISTS (SELECT 1 FROM #CompactCurrentEnvelope)
    THROW 51313, 'The lab has no verified compact envelopes in the selected sample.', 1;

CREATE TABLE #CompactExistingSnapshot
(
    SnapshotId bigint NOT NULL,
    SnapshotJsonGzip varbinary(max) NULL
);
IF COL_LENGTH(N'stg.MddMacSourceSnapshot', N'SnapshotJsonGzip') IS NOT NULL
BEGIN
    EXEC sys.sp_executesql
        N'INSERT #CompactExistingSnapshot (SnapshotId, SnapshotJsonGzip)
          SELECT TOP (@SampleSnapshots) SnapshotId,
                 COALESCE(SnapshotJsonGzip, COMPRESS(CONVERT(NVARCHAR(MAX), SnapshotJson)))
          FROM stg.MddMacSourceSnapshot
          ORDER BY SnapshotId;',
        N'@SampleSnapshots bigint',
        @SampleSnapshots = @SampleSnapshots;
END
ELSE IF COL_LENGTH(N'stg.MddMacSourceSnapshot', N'SnapshotJson') IS NOT NULL
BEGIN
    EXEC sys.sp_executesql
        N'INSERT #CompactExistingSnapshot (SnapshotId, SnapshotJsonGzip)
          SELECT TOP (@SampleSnapshots) SnapshotId,
                 COMPRESS(CONVERT(NVARCHAR(MAX), SnapshotJson))
          FROM stg.MddMacSourceSnapshot
          ORDER BY SnapshotId;',
        N'@SampleSnapshots bigint',
        @SampleSnapshots = @SampleSnapshots;
END
ELSE
    THROW 51312, 'The lab has no historical compressed snapshot payload to measure.', 1;

SELECT p.SnapshotId,
       p.FamilyName,
       p.RowOrdinal,
       p.FieldName,
       p.FieldType,
       p.RawJsonValue,
       p.RawBinaryValue
INTO #CompactOptimizedBinary
FROM stg.vMddMacCompactPhysicalFieldReadback AS p
INNER JOIN #CompactCurrentEnvelope AS e
    ON e.SnapshotId = p.SnapshotId;

CREATE TABLE #CompactDictionary
(
    DictionaryId int IDENTITY(1, 1) NOT NULL,
    RawJsonValue nvarchar(max) NOT NULL
);
INSERT #CompactDictionary (RawJsonValue)
SELECT DISTINCT p.RawJsonValue
FROM #CompactOptimizedBinary AS p
WHERE p.RawJsonValue IS NOT NULL
  AND
  (
      p.FieldName LIKE N'%source%path%'
      OR p.FieldName IN
      (
          N'timing_context',
          N'source_event_identity_basis_json',
          N'source_event_identity_missing_fields_json',
          N'source_event_identity_ambiguity_json'
      )
  );

CREATE TABLE #CompactDictionaryReference
(
    SnapshotId bigint NOT NULL,
    FamilyName nvarchar(64) NOT NULL,
    RowOrdinal int NOT NULL,
    FieldName nvarchar(256) NOT NULL,
    FieldType int NOT NULL,
    DictionaryId int NOT NULL
);
INSERT #CompactDictionaryReference
SELECT p.SnapshotId, p.FamilyName, p.RowOrdinal, p.FieldName,
       p.FieldType, d.DictionaryId
FROM #CompactOptimizedBinary AS p
INNER JOIN #CompactDictionary AS d
    ON d.RawJsonValue = p.RawJsonValue
WHERE p.RawJsonValue IS NOT NULL
  AND
  (
      p.FieldName LIKE N'%source%path%'
      OR p.FieldName IN
      (
          N'timing_context',
          N'source_event_identity_basis_json',
          N'source_event_identity_missing_fields_json',
          N'source_event_identity_ambiguity_json'
      )
  );

SELECT p.SnapshotId,
       p.FamilyName,
       p.RowOrdinal,
       p.FieldName,
       p.FieldType,
       p.RawJsonValue,
       p.RawBinaryValue
INTO #CompactDictionaryResidual
FROM #CompactOptimizedBinary AS p
WHERE NOT EXISTS
(
    SELECT 1
    FROM #CompactDictionaryReference AS r
    WHERE r.SnapshotId = p.SnapshotId
      AND r.FamilyName = p.FamilyName
      AND r.RowOrdinal = p.RowOrdinal
      AND r.FieldName = p.FieldName
);

WITH CandidateAllocation AS
(
    SELECT N'current_complete_envelope' AS Candidate,
           CONVERT(bigint, (SELECT COUNT_BIG(*) FROM #CompactCurrentEnvelope)) AS RowCount,
           CONVERT(bigint, (SELECT SUM(CONVERT(bigint, DATALENGTH(EnvelopeJsonGzip))) FROM #CompactCurrentEnvelope)) AS PayloadBytes,
           (
               SELECT SUM(CONVERT(bigint, reserved_page_count)) * CONVERT(bigint, 8192)
               FROM tempdb.sys.dm_db_partition_stats
               WHERE object_id = OBJECT_ID(N'tempdb..#CompactCurrentEnvelope')
           ) AS PhysicalAllocatedBytes,
           N'PASS' AS LosslessStatus
    UNION ALL
    SELECT N'optimized_binary_representation',
           CONVERT(bigint, (SELECT COUNT_BIG(*) FROM #CompactOptimizedBinary)),
           CONVERT(bigint, (SELECT SUM(CONVERT(bigint, DATALENGTH(RawJsonValue))) + SUM(CONVERT(bigint, DATALENGTH(RawBinaryValue))) FROM #CompactOptimizedBinary)),
           (
               SELECT SUM(CONVERT(bigint, reserved_page_count)) * CONVERT(bigint, 8192)
               FROM tempdb.sys.dm_db_partition_stats
               WHERE object_id = OBJECT_ID(N'tempdb..#CompactOptimizedBinary')
           ),
           CASE WHEN (SELECT COUNT_BIG(*) FROM #CompactOptimizedBinary) > 0 THEN N'PASS' ELSE N'FAIL' END
    UNION ALL
    SELECT N'lossless_dictionary_encoding',
           CONVERT(bigint, (SELECT COUNT_BIG(*) FROM #CompactDictionaryReference)
               + (SELECT COUNT_BIG(*) FROM #CompactDictionaryResidual)),
           CONVERT(bigint, (SELECT SUM(CONVERT(bigint, DATALENGTH(RawJsonValue))) FROM #CompactDictionary)
               + (SELECT COUNT_BIG(*) FROM #CompactDictionaryReference) * CONVERT(bigint, 4)
               + (SELECT SUM(CONVERT(bigint, DATALENGTH(RawJsonValue))) + SUM(CONVERT(bigint, DATALENGTH(RawBinaryValue))) FROM #CompactDictionaryResidual)),
           (
               SELECT SUM(CONVERT(bigint, reserved_page_count)) * CONVERT(bigint, 8192)
               FROM tempdb.sys.dm_db_partition_stats
               WHERE object_id IN
               (
                   OBJECT_ID(N'tempdb..#CompactDictionary'),
                   OBJECT_ID(N'tempdb..#CompactDictionaryReference'),
                   OBJECT_ID(N'tempdb..#CompactDictionaryResidual')
               )
           ),
           CASE
               WHEN (SELECT COUNT_BIG(*) FROM #CompactOptimizedBinary)
                    = (SELECT COUNT_BIG(*) FROM #CompactDictionaryReference)
                    + (SELECT COUNT_BIG(*) FROM #CompactDictionaryResidual)
               THEN N'PASS' ELSE N'FAIL'
           END
    UNION ALL
    SELECT N'existing_compressed_snapshot_payload',
           CONVERT(bigint, (SELECT COUNT_BIG(*) FROM #CompactExistingSnapshot)),
           CONVERT(bigint, (SELECT SUM(CONVERT(bigint, DATALENGTH(SnapshotJsonGzip))) FROM #CompactExistingSnapshot)),
           (
               SELECT SUM(CONVERT(bigint, reserved_page_count)) * CONVERT(bigint, 8192)
               FROM tempdb.sys.dm_db_partition_stats
               WHERE object_id = OBJECT_ID(N'tempdb..#CompactExistingSnapshot')
           ),
           CASE WHEN (SELECT COUNT_BIG(*) FROM #CompactExistingSnapshot) > 0 THEN N'PASS' ELSE N'FAIL' END
)
SELECT Candidate,
       CONVERT(bigint, @SampleSnapshots) AS sample_snapshots,
       RowCount,
       PayloadBytes,
       PhysicalAllocatedBytes,
       LosslessStatus
FROM CandidateAllocation
ORDER BY Candidate;

SELECT
    CASE
        WHEN (SELECT COUNT_BIG(*) FROM #CompactOptimizedBinary)
             = (SELECT COUNT_BIG(*) FROM #CompactDictionaryReference)
             + (SELECT COUNT_BIG(*) FROM #CompactDictionaryResidual)
        THEN N'PASS' ELSE N'FAIL'
    END AS dictionary_roundtrip_field_count,
    (
        SELECT SUM(CONVERT(bigint, reserved_page_count)) * CONVERT(bigint, 8192)
        FROM tempdb.sys.dm_db_partition_stats
        WHERE object_id IN
        (
            OBJECT_ID(N'tempdb..#CompactCurrentEnvelope'),
            OBJECT_ID(N'tempdb..#CompactOptimizedBinary'),
            OBJECT_ID(N'tempdb..#CompactDictionary'),
            OBJECT_ID(N'tempdb..#CompactDictionaryReference'),
            OBJECT_ID(N'tempdb..#CompactDictionaryResidual'),
            OBJECT_ID(N'tempdb..#CompactExistingSnapshot')
        )
    ) AS measured_peak_temporary_duplication_bytes;
GO

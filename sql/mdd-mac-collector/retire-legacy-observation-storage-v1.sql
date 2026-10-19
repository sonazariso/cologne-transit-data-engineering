/*
    RETIRE LEGACY OBSERVATION ROWSTORE V1

    This script is intentionally separate from compact-observation-storage-v1.
    It is the only script in this package that can drop the historical
    rowstore tables.  Every gate below must pass, including a current verified
    recovery point and complete compact parity recorded in the durable ledger.
    The default is a no-op.

    It creates same-name compatibility views after the old tables are removed,
    keeps the old persistence procedure signature as a compact wrapper, and
    reapplies existing table grants to the replacement views.  It does not
    shrink a data file; use the bounded reclamation runner after this commit.
*/

USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @ApplyLegacyRetirement BIT = 0;
IF @ApplyLegacyRetirement <> 1
BEGIN
    PRINT 'LEGACY OBSERVATION RETIREMENT NOT APPLIED: @ApplyLegacyRetirement is 0.';
    RETURN;
END;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 51300, 'Legacy retirement must run in CologneTransitIntelligence.', 1;

IF OBJECT_ID(N'stg.MddMacCompactStorageState', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacCompactObservationEnvelope', N'U') IS NULL
    THROW 51301, 'Compact storage state is not installed.', 1;

IF EXISTS
(
    SELECT 1 FROM stg.MddMacCompactStorageState
    WHERE StateId = 1
      AND (CompatibilityVerified <> 1 OR HistoricalReadbackVerified <> 1
           OR LegacyRetirementApproved <> 1)
)
    THROW 51302, 'Compact compatibility, historical parity, or retirement approval is not recorded.', 1;

IF EXISTS
(
    SELECT 1 FROM stg.MddMacCompactObservationEnvelope
    WHERE VerificationStatus <> 'VERIFIED'
)
    THROW 51303, 'One or more compact envelopes are not VERIFIED.', 1;

DECLARE @OldSnapshots BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot);
DECLARE @OldCalls BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation);
DECLARE @OldTimings BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation);
DECLARE @OldSituations BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence);
DECLARE @OldSections BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence);
DECLARE @OldAttributes BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence);
DECLARE @OldErrors BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError);
DECLARE @OldGaps BIGINT =
    (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap);

IF @OldSnapshots <> (SELECT COUNT_BIG(*) FROM stg.MddMacCompactObservationEnvelope)
   OR @OldSnapshots <> (SELECT COUNT_BIG(*) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldCalls <> (SELECT COALESCE(SUM(CallRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldTimings <> (SELECT COALESCE(SUM(TimingRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldSituations <> (SELECT COALESCE(SUM(SituationRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldSections <> (SELECT COALESCE(SUM(ServiceSectionRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldAttributes <> (SELECT COALESCE(SUM(AttributeRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldErrors <> (SELECT COALESCE(SUM(ResponseErrorRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
   OR @OldGaps <> (SELECT COALESCE(SUM(ParserGapRowCount), 0) FROM stg.MddMacCompactObservationEnvelope WHERE VerificationStatus = 'VERIFIED')
    THROW 51304, 'Compact and legacy row counts do not reconcile exactly.', 1;

IF EXISTS
(
    SELECT 1 FROM ctl.MddMacCollectorRun
    WHERE Status IN ('RUNNING', 'CLAIMED')
      AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
)
    THROW 51305, 'A production collector run is still active.', 1;

IF EXISTS
(
    SELECT 1 FROM ctl.MddMacProductionLease
    WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE'
)
    THROW 51306, 'The production lease is still active.', 1;

DECLARE @LegacyObjects TABLE
(
    ObjectId INT NOT NULL PRIMARY KEY,
    SchemaName SYSNAME NOT NULL,
    ObjectName SYSNAME NOT NULL
);
INSERT INTO @LegacyObjects (ObjectId, SchemaName, ObjectName)
SELECT o.object_id, s.name, o.name
FROM sys.objects AS o
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name = N'stg'
  AND o.name IN
  (
      N'MddMacSourceSnapshot', N'MddMacCallObservation',
      N'MddMacTimingObservation', N'MddMacSituationEvidence',
      N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
      N'MddMacResponseError', N'MddMacParserGap'
  )
  AND o.type = 'U';

IF (SELECT COUNT(*) FROM @LegacyObjects) <> 8
    THROW 51307, 'All eight legacy observation tables must be present before retirement.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys AS fk
    WHERE fk.referenced_object_id IN (SELECT ObjectId FROM @LegacyObjects)
      AND fk.parent_object_id NOT IN (SELECT ObjectId FROM @LegacyObjects)
)
    THROW 51308, 'An external foreign key depends on a legacy observation table.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.sql_expression_dependencies AS dep
    INNER JOIN sys.objects AS referencing_object
        ON referencing_object.object_id = dep.referencing_id
    INNER JOIN sys.schemas AS referencing_schema
        ON referencing_schema.schema_id = referencing_object.schema_id
    WHERE dep.referenced_id IN (SELECT ObjectId FROM @LegacyObjects)
      AND NOT
      (
          (referencing_schema.name = N'stg' AND referencing_object.name IN
           (
               N'uspPersistMddMacSnapshot', N'vMddMacSourceSnapshotJson',
               N'vMddMacCallObservationJson', N'vMddMacTimingObservationJson',
               N'vMddMacSituationEvidenceJson', N'vMddMacServiceSectionEvidenceJson',
               N'vMddMacAttributeEvidenceJson', N'vMddMacParserGapJson'
           ))
          OR
          (referencing_schema.name = N'analytics' AND referencing_object.name IN
           (N'vwMddMacRecordedAtTimeHistory', N'vwMddMacSourceFieldCoverage'))
      )
)
    THROW 51309, 'An unreviewed stored procedure or view depends on a legacy observation table.', 1;

/* Preserve object-level grants before replacing tables with same-name views. */
DECLARE @ObjectPermissions TABLE
(
    SchemaName SYSNAME NOT NULL,
    ObjectName SYSNAME NOT NULL,
    PrincipalName SYSNAME NOT NULL,
    PermissionName NVARCHAR(128) NOT NULL,
    StateDesc NVARCHAR(60) NOT NULL
);
INSERT INTO @ObjectPermissions (SchemaName, ObjectName, PrincipalName, PermissionName, StateDesc)
SELECT lo.SchemaName, lo.ObjectName, dp.name, p.permission_name, p.state_desc
FROM @LegacyObjects AS lo
INNER JOIN sys.database_permissions AS p ON p.major_id = lo.ObjectId AND p.class = 1
INNER JOIN sys.database_principals AS dp ON dp.principal_id = p.grantee_principal_id
WHERE p.permission_name IN (N'SELECT', N'VIEW DEFINITION');

DECLARE @ExpectedCompactColumns TABLE
(
    ObjectName SYSNAME NOT NULL,
    ColumnName SYSNAME NOT NULL,
    TypeName SYSNAME NOT NULL,
    MaxLength SMALLINT NOT NULL,
    IsNullable BIT NOT NULL
);
INSERT INTO @ExpectedCompactColumns (ObjectName, ColumnName, TypeName, MaxLength, IsNullable)
VALUES
    (N'stg.MddMacCompactStorageState', N'StateId', N'tinyint', 1, 0),
    (N'stg.MddMacCompactStorageState', N'StorageFormatVersion', N'smallint', 2, 0),
    (N'stg.MddMacCompactStorageState', N'MigrationImplementationVersion', N'varchar', 100, 0),
    (N'stg.MddMacCompactStorageState', N'FutureWriteEnabled', N'bit', 1, 0),
    (N'stg.MddMacCompactObservationEnvelope', N'SnapshotId', N'bigint', 8, 0),
    (N'stg.MddMacCompactObservationEnvelope', N'EnvelopeJsonGzip', N'varbinary', -1, 0),
    (N'stg.MddMacCompactObservationEnvelope', N'EnvelopeSha256', N'char', 64, 0),
    (N'stg.MddMacCompactObservationEnvelope', N'VerificationStatus', N'varchar', 20, 0);

IF EXISTS
(
    SELECT 1
    FROM @ExpectedCompactColumns AS expected
    LEFT JOIN sys.columns AS c
        ON c.object_id = OBJECT_ID(expected.ObjectName)
       AND c.name = expected.ColumnName
    LEFT JOIN sys.types AS t ON t.user_type_id = c.user_type_id
    WHERE c.column_id IS NULL
       OR t.name <> expected.TypeName
       OR c.max_length <> expected.MaxLength
       OR c.is_nullable <> expected.IsNullable
)
    THROW 51317, 'Compact storage schema contract is not validated for retirement.', 1;

/*
    These values are deliberately supplied by the reviewed operator artifact
    on the supported runner.  A direct SSMS invocation must replace the NULLs
    with the same measured values before the DROP path can proceed.
*/
DECLARE @RetirementTransactionLogReserveBytes BIGINT = NULL;
DECLARE @RetirementTempdbReserveBytes BIGINT = NULL;
DECLARE @RetirementRollbackReserveBytes BIGINT = NULL;
DECLARE @RetirementDdlIndexReserveBytes BIGINT = NULL;
DECLARE @RetirementAutogrowthReserveBytes BIGINT = NULL;
DECLARE @RetirementExternalDiskReserveBytes BIGINT = NULL;

IF @RetirementTransactionLogReserveBytes IS NULL
   OR @RetirementTempdbReserveBytes IS NULL
   OR @RetirementRollbackReserveBytes IS NULL
   OR @RetirementDdlIndexReserveBytes IS NULL
   OR @RetirementAutogrowthReserveBytes IS NULL
   OR @RetirementExternalDiskReserveBytes IS NULL
   OR @RetirementTransactionLogReserveBytes <= 0
   OR @RetirementTempdbReserveBytes <= 0
   OR @RetirementRollbackReserveBytes <= 0
   OR @RetirementDdlIndexReserveBytes <= 0
   OR @RetirementAutogrowthReserveBytes <= 0
   OR @RetirementExternalDiskReserveBytes <= 0
    THROW 51310, 'Measured retirement capacity reserves are required before DROP.', 1;

DECLARE @WindowsCFreeBytes BIGINT =
(
    SELECT MIN(CONVERT(BIGINT, vs.available_bytes))
    FROM
    (
        SELECT DB_ID() AS database_id, file_id FROM sys.database_files
        UNION ALL
        SELECT DB_ID(N'tempdb') AS database_id, file_id FROM tempdb.sys.database_files
    ) AS files
    CROSS APPLY sys.dm_os_volume_stats(files.database_id, files.file_id) AS vs
    WHERE vs.volume_mount_point LIKE N'C:%'
);
DECLARE @LogTotalBytes BIGINT = NULL;
DECLARE @LogUsedBytes BIGINT = NULL;
DECLARE @LogUsedPercent DECIMAL(9, 3) = NULL;
SELECT @LogTotalBytes = CONVERT(BIGINT, total_log_size_in_bytes),
       @LogUsedBytes = CONVERT(BIGINT, used_log_space_in_bytes),
       @LogUsedPercent = CONVERT(DECIMAL(9, 3), used_log_space_in_percent)
FROM sys.dm_db_log_space_usage
WHERE database_id = DB_ID();

DECLARE @TempdbInternalFreeBytes BIGINT =
(
    SELECT COALESCE(SUM(
        (CONVERT(BIGINT, df.size) - CONVERT(BIGINT, COALESCE(fsu.allocated_extent_page_count, 0)))
        * CONVERT(BIGINT, 8192)), 0)
    FROM tempdb.sys.database_files AS df
    LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
    WHERE df.type = 0
);
DECLARE @TempdbWindowsFreeBytes BIGINT =
(
    SELECT MIN(CONVERT(BIGINT, vs.available_bytes))
    FROM tempdb.sys.database_files AS df
    CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
);
DECLARE @LegacyReservedBytes BIGINT =
(
    SELECT COALESCE(SUM(CONVERT(BIGINT, ps.reserved_page_count)) * CONVERT(BIGINT, 8192), 0)
    FROM sys.dm_db_partition_stats AS ps
    INNER JOIN sys.objects AS o ON o.object_id = ps.object_id
    INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
    WHERE s.name = N'stg' AND o.object_id IN (SELECT ObjectId FROM @LegacyObjects)
);
DECLARE @LegacyIndexReservedBytes BIGINT =
(
    SELECT COALESCE(SUM(CONVERT(BIGINT, ps.index_reserved_page_count)) * CONVERT(BIGINT, 8192), 0)
    FROM sys.dm_db_partition_stats AS ps
    INNER JOIN sys.objects AS o ON o.object_id = ps.object_id
    INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
    WHERE s.name = N'stg' AND o.object_id IN (SELECT ObjectId FROM @LegacyObjects)
);
DECLARE @ActiveUserTransactions BIGINT =
(
    SELECT COUNT_BIG(*)
    FROM sys.dm_tran_session_transactions AS st
    INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
    WHERE st.session_id <> @@SPID AND es.is_user_process = 1
);

IF @WindowsCFreeBytes IS NULL
   OR @WindowsCFreeBytes -
      (
          @RetirementTransactionLogReserveBytes
          + @RetirementTempdbReserveBytes
          + @RetirementRollbackReserveBytes
          + @RetirementDdlIndexReserveBytes
          + @RetirementAutogrowthReserveBytes
          + @RetirementExternalDiskReserveBytes
      ) < CONVERT(BIGINT, 12) * 1024 * 1024 * 1024
    THROW 51311, 'Windows C: headroom would fall below the 12 GiB retirement floor.', 1;

IF @LogTotalBytes IS NULL OR @LogUsedBytes IS NULL OR @LogUsedPercent IS NULL
   OR @LogUsedPercent > 70
   OR @LogTotalBytes - @LogUsedBytes < @RetirementTransactionLogReserveBytes
    THROW 51312, 'Transaction-log reserve or utilization preflight failed.', 1;

IF @TempdbWindowsFreeBytes IS NULL
   OR @TempdbInternalFreeBytes < @RetirementTempdbReserveBytes
   OR @TempdbWindowsFreeBytes < @RetirementTempdbReserveBytes
    THROW 51313, 'Tempdb reserve preflight failed.', 1;

IF @ActiveUserTransactions <> 0
    THROW 51314, 'Another SQL user transaction is active; legacy retirement is blocked.', 1;

IF @RetirementDdlIndexReserveBytes < @LegacyReservedBytes
   OR @RetirementDdlIndexReserveBytes < @LegacyIndexReservedBytes
    THROW 51315, 'DDL/index reserve is smaller than measured legacy allocation.', 1;

IF EXISTS
(
    SELECT 1
    FROM
    (
        SELECT df.type_desc, df.name,
               CONVERT(BIGINT, df.size) * CONVERT(BIGINT, 8192) AS allocated_bytes,
               CONVERT(BIGINT, df.max_size) * CONVERT(BIGINT, 8192) AS max_bytes,
               CONVERT(BIGINT, df.growth) AS growth_value,
               CAST(0 AS BIT) AS is_tempdb
        FROM sys.database_files AS df
        UNION ALL
        SELECT df.type_desc, df.name,
               CONVERT(BIGINT, df.size) * CONVERT(BIGINT, 8192),
               CONVERT(BIGINT, df.max_size) * CONVERT(BIGINT, 8192),
               CONVERT(BIGINT, df.growth),
               CAST(1 AS BIT)
        FROM tempdb.sys.database_files AS df
    ) AS files
    WHERE files.growth_value <= 0
       OR
       (
           files.max_bytes >= 0
           AND files.max_bytes - files.allocated_bytes <
               CASE
                   WHEN files.is_tempdb = 1 THEN @RetirementTempdbReserveBytes + @RetirementAutogrowthReserveBytes
                   WHEN files.type_desc = N'LOG' THEN @RetirementTransactionLogReserveBytes + @RetirementAutogrowthReserveBytes
                   ELSE @RetirementDdlIndexReserveBytes + @RetirementRollbackReserveBytes + @RetirementAutogrowthReserveBytes
               END
       )
)
    THROW 51316, 'Finite file growth or autogrowth headroom is insufficient for retirement.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    /* Existing readback views and analytics definitions refer to the tables. */
    IF OBJECT_ID(N'stg.vMddMacSourceSnapshotJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacSourceSnapshotJson;
    IF OBJECT_ID(N'stg.vMddMacCallObservationJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacCallObservationJson;
    IF OBJECT_ID(N'stg.vMddMacTimingObservationJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacTimingObservationJson;
    IF OBJECT_ID(N'stg.vMddMacSituationEvidenceJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacSituationEvidenceJson;
    IF OBJECT_ID(N'stg.vMddMacServiceSectionEvidenceJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacServiceSectionEvidenceJson;
    IF OBJECT_ID(N'stg.vMddMacAttributeEvidenceJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacAttributeEvidenceJson;
    IF OBJECT_ID(N'stg.vMddMacParserGapJson', N'V') IS NOT NULL
        DROP VIEW stg.vMddMacParserGapJson;

    /* Retain the existing procedure signature; its body now writes only the
       compact envelope and therefore remains a safe compatibility boundary. */
    EXEC(N'
    CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot
        @RunId NVARCHAR(100) = NULL,
        @AuditId BIGINT = NULL,
        @TargetIdentity NVARCHAR(200) = NULL,
        @SamplingTargetId INT = NULL,
        @LogicalDueBucket BIGINT = NULL,
        @ObservedAtUtc DATETIME2(7) = NULL,
        @ResponseTimestampSourceText NVARCHAR(256) = NULL,
        @ParserVersion VARCHAR(80) = NULL,
        @ApplicationStatus VARCHAR(80) = NULL,
        @PayloadHashSha256 CHAR(64) = NULL,
        @SnapshotJson NVARCHAR(MAX) = NULL,
        @CallsJson NVARCHAR(MAX) = NULL,
        @TimingsJson NVARCHAR(MAX) = NULL,
        @SituationEvidenceJson NVARCHAR(MAX) = NULL,
        @ServiceSectionsJson NVARCHAR(MAX) = NULL,
        @AttributesJson NVARCHAR(MAX) = NULL,
        @ResponseErrorsJson NVARCHAR(MAX) = NULL,
        @ParserGapsJson NVARCHAR(MAX) = NULL,
        @CompletedRunStatus VARCHAR(40) = NULL,
        @CompletedAtUtc DATETIME2(7) = NULL,
        @RunSummaryJson NVARCHAR(MAX) = NULL
    AS
    BEGIN
        SET NOCOUNT ON;
        DECLARE @SourceSnapshotJson NVARCHAR(MAX);
        DECLARE @EnvelopeJson NVARCHAR(MAX);
        DECLARE @EnvelopeSha256 CHAR(64);
        IF @CompletedRunStatus IS NOT NULL
        BEGIN
            EXEC stg.uspPersistMddMacSnapshotCompact
                @RunId=@RunId, @CompletedRunStatus=@CompletedRunStatus,
                @CompletedAtUtc=@CompletedAtUtc, @RunSummaryJson=@RunSummaryJson;
            RETURN;
        END;
        IF ISJSON(@SnapshotJson) <> 1 OR ISJSON(@CallsJson) <> 1
           OR ISJSON(@TimingsJson) <> 1 OR ISJSON(@SituationEvidenceJson) <> 1
           OR ISJSON(@ServiceSectionsJson) <> 1 OR ISJSON(@AttributesJson) <> 1
           OR ISJSON(@ResponseErrorsJson) <> 1 OR ISJSON(@ParserGapsJson) <> 1
            THROW 51320, ''legacy persistence JSON is invalid'', 1;
        SET @SourceSnapshotJson =
        (
            SELECT CAST(NULL AS BIGINT) AS snapshot_id, @RunId AS run_id,
                   @AuditId AS audit_id, @TargetIdentity AS target_identity,
                   @SamplingTargetId AS sampling_target_id,
                   @LogicalDueBucket AS logical_due_bucket,
                   @ObservedAtUtc AS observed_at_utc,
                   @ResponseTimestampSourceText AS response_timestamp_source_text,
                   @ParserVersion AS parser_version,
                   @ApplicationStatus AS application_status,
                   @PayloadHashSha256 AS payload_hash_sha256,
                   @SnapshotJson AS snapshot_json,
                   JSON_VALUE(@SnapshotJson, ''$.result_count'') AS result_count
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES
        );
        SET @EnvelopeJson =
        (
            SELECT ''COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE'' AS envelope_type,
                   1 AS storage_format_version,
                   ''CTI_CANONICAL_JSON_UTF16LE_V1'' AS serialization,
                   ''SHA2_256_UTF16LE'' AS hash_algorithm,
                   JSON_QUERY(@SourceSnapshotJson) AS source_snapshot,
                   JSON_QUERY(@CallsJson) AS calls,
                   JSON_QUERY(@TimingsJson) AS timings,
                   JSON_QUERY(@SituationEvidenceJson) AS situations,
                   JSON_QUERY(@ServiceSectionsJson) AS service_sections,
                   JSON_QUERY(@AttributesJson) AS attributes,
                   JSON_QUERY(@ResponseErrorsJson) AS response_errors,
                   JSON_QUERY(@ParserGapsJson) AS parser_gaps
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES
        );
        SET @EnvelopeSha256 = CONVERT(CHAR(64), HASHBYTES(''SHA2_256'', CONVERT(VARBINARY(MAX), @EnvelopeJson)), 2);
        EXEC stg.uspPersistMddMacSnapshotCompact
            @RunId=@RunId, @AuditId=@AuditId, @TargetIdentity=@TargetIdentity,
            @SamplingTargetId=@SamplingTargetId, @LogicalDueBucket=@LogicalDueBucket,
            @ObservedAtUtc=@ObservedAtUtc,
            @ResponseTimestampSourceText=@ResponseTimestampSourceText,
            @ParserVersion=@ParserVersion, @ApplicationStatus=@ApplicationStatus,
            @PayloadHashSha256=@PayloadHashSha256, @EnvelopeJson=@EnvelopeJson,
            @EnvelopeSha256=@EnvelopeSha256;
    END');

    IF OBJECT_ID(N'analytics.vwMddMacRecordedAtTimeHistory', N'V') IS NOT NULL
        DROP VIEW analytics.vwMddMacRecordedAtTimeHistory;
    IF OBJECT_ID(N'analytics.vwMddMacSourceFieldCoverage', N'V') IS NOT NULL
        DROP VIEW analytics.vwMddMacSourceFieldCoverage;

    DROP TABLE stg.MddMacParserGap;
    DROP TABLE stg.MddMacResponseError;
    DROP TABLE stg.MddMacAttributeEvidence;
    DROP TABLE stg.MddMacServiceSectionEvidence;
    DROP TABLE stg.MddMacSituationEvidence;
    DROP TABLE stg.MddMacTimingObservation;
    DROP TABLE stg.MddMacCallObservation;
    DROP TABLE stg.MddMacSourceSnapshot;

    /* Same-name views preserve the existing object contract for consumers. */
    EXEC(N'
    CREATE VIEW stg.MddMacSourceSnapshot AS
    SELECT SnapshotId, RunId, AuditId, TargetIdentity, SamplingTargetId,
           LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
           ParserVersion, ApplicationStatus, PayloadHashSha256, SnapshotJson,
           SnapshotJsonGzip, CreatedAtUtc
    FROM stg.vMddMacCompactSourceSnapshot;');
    EXEC(N'
    CREATE VIEW stg.MddMacCallObservation AS
    SELECT * FROM stg.vMddMacCompactCallObservation;');
    EXEC(N'
    CREATE VIEW stg.MddMacTimingObservation AS
    SELECT * FROM stg.vMddMacCompactTimingObservation;');
    EXEC(N'
    CREATE VIEW stg.MddMacSituationEvidence AS
    SELECT * FROM stg.vMddMacCompactSituationEvidence;');
    EXEC(N'
    CREATE VIEW stg.MddMacServiceSectionEvidence AS
    SELECT * FROM stg.vMddMacCompactServiceSectionEvidence;');
    EXEC(N'
    CREATE VIEW stg.MddMacAttributeEvidence AS
    SELECT * FROM stg.vMddMacCompactAttributeEvidence;');
    EXEC(N'
    CREATE VIEW stg.MddMacResponseError AS
    SELECT * FROM stg.vMddMacCompactResponseError;');
    EXEC(N'
    CREATE VIEW stg.MddMacParserGap AS
    SELECT * FROM stg.vMddMacCompactParserGap;');

    EXEC(N'
    CREATE VIEW stg.vMddMacSourceSnapshotJson AS
    SELECT * FROM stg.MddMacSourceSnapshot;');
    EXEC(N'
    CREATE VIEW stg.vMddMacCallObservationJson AS
    SELECT * FROM stg.MddMacCallObservation;');
    EXEC(N'
    CREATE VIEW stg.vMddMacTimingObservationJson AS
    SELECT * FROM stg.MddMacTimingObservation;');
    EXEC(N'
    CREATE VIEW stg.vMddMacSituationEvidenceJson AS
    SELECT * FROM stg.MddMacSituationEvidence;');
    EXEC(N'
    CREATE VIEW stg.vMddMacServiceSectionEvidenceJson AS
    SELECT * FROM stg.MddMacServiceSectionEvidence;');
    EXEC(N'
    CREATE VIEW stg.vMddMacAttributeEvidenceJson AS
    SELECT * FROM stg.MddMacAttributeEvidence;');
    EXEC(N'
    CREATE VIEW stg.vMddMacParserGapJson AS
    SELECT * FROM stg.MddMacParserGap;');

    EXEC(N'
    CREATE VIEW analytics.vwMddMacRecordedAtTimeHistory AS
    SELECT * FROM analytics.vwMddMacCompactRecordedAtTimeHistory;');
    EXEC(N'
    CREATE VIEW analytics.vwMddMacSourceFieldCoverage AS
    SELECT * FROM analytics.vwMddMacCompactSourceFieldCoverage;');

    /* Verify the replacement object surface before the destructive transaction
       can commit.  A post-commit client check is useful evidence, but cannot
       protect the legacy tables if a compatibility view is malformed. */
    IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacCallObservation', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacTimingObservation', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacSituationEvidence', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacServiceSectionEvidence', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacAttributeEvidence', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacResponseError', N'V') IS NULL
       OR OBJECT_ID(N'stg.MddMacParserGap', N'V') IS NULL
       OR OBJECT_ID(N'analytics.vwMddMacRecordedAtTimeHistory', N'V') IS NULL
       OR OBJECT_ID(N'analytics.vwMddMacSourceFieldCoverage', N'V') IS NULL
        THROW 51310, 'Replacement compatibility views were not installed completely.', 1;

    IF @OldSnapshots <> (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot)
       OR @OldCalls <> (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation)
       OR @OldTimings <> (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation)
       OR @OldSituations <> (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence)
       OR @OldSections <> (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence)
       OR @OldAttributes <> (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence)
       OR @OldErrors <> (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError)
       OR @OldGaps <> (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap)
        THROW 51311, 'Replacement compatibility-view counts do not reconcile before commit.', 1;

    /* Reapply only the old table-level SELECT/VIEW DEFINITION contract. */
    DECLARE @GrantSql NVARCHAR(MAX) = N'';
    SELECT @GrantSql = @GrantSql +
        CASE WHEN StateDesc = N'DENY' THEN N'DENY ' ELSE N'GRANT ' END
        + PermissionName + N' ON OBJECT::' + QUOTENAME(SchemaName) + N'.' + QUOTENAME(ObjectName)
        + N' TO ' + QUOTENAME(PrincipalName)
        + CASE WHEN StateDesc = N'GRANT_WITH_GRANT_OPTION' THEN N' WITH GRANT OPTION' ELSE N'' END + N';'
    FROM @ObjectPermissions;
    IF LEN(@GrantSql) > 0 EXEC sys.sp_executesql @GrantSql;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

PRINT 'LEGACY OBSERVATION ROWSTORE RETIRED; COMPACT COMPATIBILITY VIEWS INSTALLED.';

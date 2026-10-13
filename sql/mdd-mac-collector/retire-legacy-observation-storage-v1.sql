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

    /* Reapply only the old table-level SELECT/VIEW DEFINITION contract. */
    DECLARE @GrantSql NVARCHAR(MAX) = N'';
    SELECT @GrantSql = @GrantSql +
        N'GRANT ' + PermissionName + N' ON OBJECT::' + QUOTENAME(SchemaName) + N'.' + QUOTENAME(ObjectName)
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

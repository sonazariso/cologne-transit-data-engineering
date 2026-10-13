/*
    COMPACT OBSERVATION STORAGE V1

    This is a source-controlled, additive migration.  It installs the
    immutable per-snapshot envelope and its readback contract, but it is
    fail-closed by default in the supported runner.  Because SQL Server GO
    separators create separate batches, do not paste this file directly into
    SSMS: use scripts/apply_compact_storage_migration.py --apply, which checks
    the stopped/disabled collector gate and changes the marker only for the
    explicitly authorized deployment.

    Historical migration is performed by the bounded Python runner.  The
    runner reads one SnapshotId at a time, serializes every current SQL row
    column (including identity values and physical V1/V2/V3 representations),
    and commits one envelope unit.  SQL Server COMPRESS/DECOMPRESS remains the
    authoritative physical encoding/readback path.

    No legacy row is deleted by this file.  Retiring legacy tables and
    shrinking a data file are separate, recovery-gated operations.
*/

USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @ApplyCompactStorage BIT = 0;
IF @ApplyCompactStorage <> 1
BEGIN
    PRINT 'COMPACT STORAGE V1 NOT APPLIED: @ApplyCompactStorage is 0.';
    RETURN;
END;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 51200, 'Compact observation storage must run in CologneTransitIntelligence.', 1;

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacCallObservation', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
    THROW 51201, 'The legacy source-faithful observation tables are required before compact storage.', 1;

IF OBJECT_ID(N'stg.MddMacCompactStorageState', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCompactStorageState
    (
        StateId TINYINT NOT NULL
            CONSTRAINT PK_stg_MddMacCompactStorageState PRIMARY KEY,
        StorageFormatVersion SMALLINT NOT NULL,
        FutureWriteEnabled BIT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactStorageState_FutureWriteEnabled DEFAULT (0),
        CompatibilityVerified BIT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactStorageState_CompatibilityVerified DEFAULT (0),
        HistoricalReadbackVerified BIT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactStorageState_HistoricalReadbackVerified DEFAULT (0),
        LegacyRetirementApproved BIT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactStorageState_LegacyRetirementApproved DEFAULT (0),
        UpdatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactStorageState_UpdatedAtUtc DEFAULT (SYSUTCDATETIME()),
        UpdatedBy NVARCHAR(128) NULL,
        CONSTRAINT CK_stg_MddMacCompactStorageState_Singleton CHECK (StateId = 1),
        CONSTRAINT CK_stg_MddMacCompactStorageState_Version CHECK (StorageFormatVersion = 1)
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactStorageState WHERE StateId = 1)
BEGIN
    INSERT INTO stg.MddMacCompactStorageState
        (StateId, StorageFormatVersion, FutureWriteEnabled,
         CompatibilityVerified, HistoricalReadbackVerified,
         LegacyRetirementApproved, UpdatedBy)
    VALUES
        (1, 1, 0, 0, 0, 0, SUSER_SNAME());
END;
GO

IF OBJECT_ID(N'stg.MddMacCompactIdentityAllocator', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCompactIdentityAllocator
    (
        FamilyCode VARCHAR(40) NOT NULL
            CONSTRAINT PK_stg_MddMacCompactIdentityAllocator PRIMARY KEY,
        NextIdentity BIGINT NOT NULL,
        UpdatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactIdentityAllocator_UpdatedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_stg_MddMacCompactIdentityAllocator_NextIdentity CHECK (NextIdentity > 0)
    );
END;
GO

IF OBJECT_ID(N'stg.MddMacCompactObservationEnvelope', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCompactObservationEnvelope
    (
        SnapshotId BIGINT NOT NULL
            CONSTRAINT PK_stg_MddMacCompactObservationEnvelope PRIMARY KEY CLUSTERED,
        StorageFormatVersion SMALLINT NOT NULL,
        EnvelopeType VARCHAR(120) NOT NULL,
        Serialization VARCHAR(100) NOT NULL,
        HashAlgorithm VARCHAR(40) NOT NULL,
        RunId NVARCHAR(100) NULL,
        AuditId BIGINT NULL,
        TargetIdentity NVARCHAR(200) NULL,
        SamplingTargetId INT NULL,
        LogicalDueBucket BIGINT NULL,
        ObservedAtUtc DATETIME2(7) NULL,
        ResponseTimestampSourceText NVARCHAR(256) NULL,
        ParserVersion VARCHAR(80) NULL,
        ApplicationStatus VARCHAR(80) NULL,
        PayloadHashSha256 CHAR(64) NULL,
        SourceSnapshotRowCount BIGINT NOT NULL,
        CallRowCount BIGINT NOT NULL,
        TimingRowCount BIGINT NOT NULL,
        SituationRowCount BIGINT NOT NULL,
        ServiceSectionRowCount BIGINT NOT NULL,
        AttributeRowCount BIGINT NOT NULL,
        ResponseErrorRowCount BIGINT NOT NULL,
        ParserGapRowCount BIGINT NOT NULL,
        EnvelopeJsonGzip VARBINARY(MAX) NOT NULL,
        EnvelopeSha256 CHAR(64) NOT NULL,
        EnvelopeLogicalBytes BIGINT NOT NULL,
        EnvelopeCompressedBytes BIGINT NOT NULL,
        VerificationStatus VARCHAR(20) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactObservationEnvelope_VerificationStatus DEFAULT ('BUILT'),
        VerifiedAtUtc DATETIME2(7) NULL,
        CreatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactObservationEnvelope_CreatedAtUtc DEFAULT (SYSUTCDATETIME()),
        MigrationId UNIQUEIDENTIFIER NULL,
        CallObservationIdStart BIGINT NULL,
        TimingObservationIdStart BIGINT NULL,
        SituationEvidenceIdStart BIGINT NULL,
        ServiceSectionEvidenceIdStart BIGINT NULL,
        AttributeEvidenceIdStart BIGINT NULL,
        ResponseErrorIdStart BIGINT NULL,
        ParserGapIdStart BIGINT NULL,
        CONSTRAINT CK_stg_MddMacCompactObservationEnvelope_Version CHECK (StorageFormatVersion = 1),
        CONSTRAINT CK_stg_MddMacCompactObservationEnvelope_Hash CHECK (LEN(EnvelopeSha256) = 64),
        CONSTRAINT CK_stg_MddMacCompactObservationEnvelope_Status
            CHECK (VerificationStatus IN ('BUILT', 'VERIFIED', 'QUARANTINED')),
        CONSTRAINT CK_stg_MddMacCompactObservationEnvelope_Counts CHECK
        (
            SourceSnapshotRowCount >= 1
            AND CallRowCount >= 0
            AND TimingRowCount >= 0
            AND SituationRowCount >= 0
            AND ServiceSectionRowCount >= 0
            AND AttributeRowCount >= 0
            AND ResponseErrorRowCount >= 0
            AND ParserGapRowCount >= 0
        )
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacCompactObservationEnvelope')
      AND name = N'UX_stg_MddMacCompactObservationEnvelope_AuditId'
)
BEGIN
    CREATE UNIQUE INDEX UX_stg_MddMacCompactObservationEnvelope_AuditId
        ON stg.MddMacCompactObservationEnvelope (AuditId)
        WHERE AuditId IS NOT NULL;
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacCompactObservationEnvelope')
      AND name = N'IX_stg_MddMacCompactObservationEnvelope_ObservedAt'
)
BEGIN
    CREATE INDEX IX_stg_MddMacCompactObservationEnvelope_ObservedAt
        ON stg.MddMacCompactObservationEnvelope (ObservedAtUtc, SnapshotId)
        INCLUDE (TargetIdentity, TimingRowCount, CallRowCount, VerificationStatus);
END;
GO

IF OBJECT_ID(N'stg.MddMacCompactMigration', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCompactMigration
    (
        MigrationId UNIQUEIDENTIFIER NOT NULL
            CONSTRAINT PK_stg_MddMacCompactMigration PRIMARY KEY,
        StorageFormatVersion SMALLINT NOT NULL,
        MigrationKind VARCHAR(30) NOT NULL,
        Status VARCHAR(30) NOT NULL,
        StartedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactMigration_StartedAtUtc DEFAULT (SYSUTCDATETIME()),
        CompletedAtUtc DATETIME2(7) NULL,
        LastSnapshotId BIGINT NULL,
        VerifiedUnitCount BIGINT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactMigration_VerifiedUnitCount DEFAULT (0),
        QuarantinedUnitCount BIGINT NOT NULL
            CONSTRAINT DF_stg_MddMacCompactMigration_QuarantinedUnitCount DEFAULT (0),
        FailureCode VARCHAR(80) NULL,
        FailureMessage NVARCHAR(2000) NULL,
        CONSTRAINT CK_stg_MddMacCompactMigration_Version CHECK (StorageFormatVersion = 1),
        CONSTRAINT CK_stg_MddMacCompactMigration_Kind CHECK (MigrationKind IN ('HISTORICAL', 'FUTURE_WRITE')), 
        CONSTRAINT CK_stg_MddMacCompactMigration_Status CHECK
            (Status IN ('RUNNING', 'COMPLETE', 'FAILED', 'BLOCKED'))
    );
END;
GO

IF OBJECT_ID(N'stg.MddMacCompactMigrationUnit', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCompactMigrationUnit
    (
        MigrationId UNIQUEIDENTIFIER NOT NULL,
        SnapshotId BIGINT NOT NULL,
        Status VARCHAR(20) NOT NULL,
        SourceSnapshotRowCount BIGINT NULL,
        CallRowCount BIGINT NULL,
        TimingRowCount BIGINT NULL,
        SituationRowCount BIGINT NULL,
        ServiceSectionRowCount BIGINT NULL,
        AttributeRowCount BIGINT NULL,
        ResponseErrorRowCount BIGINT NULL,
        ParserGapRowCount BIGINT NULL,
        EnvelopeSha256 CHAR(64) NULL,
        StartedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacCompactMigrationUnit_StartedAtUtc DEFAULT (SYSUTCDATETIME()),
        VerifiedAtUtc DATETIME2(7) NULL,
        FailureCode VARCHAR(80) NULL,
        FailureMessage NVARCHAR(2000) NULL,
        CONSTRAINT PK_stg_MddMacCompactMigrationUnit PRIMARY KEY CLUSTERED (MigrationId, SnapshotId),
        CONSTRAINT FK_stg_MddMacCompactMigrationUnit_Migration
            FOREIGN KEY (MigrationId) REFERENCES stg.MddMacCompactMigration (MigrationId),
        CONSTRAINT UQ_stg_MddMacCompactMigrationUnit_Snapshot
            UNIQUE (SnapshotId, MigrationId),
        CONSTRAINT CK_stg_MddMacCompactMigrationUnit_Status
            CHECK (Status IN ('PENDING', 'BUILT', 'VERIFIED', 'QUARANTINED'))
    );
END;
GO

/* Initialize durable identity ranges above the current legacy identities. */
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'Snapshot')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'Snapshot', CONVERT(BIGINT, COALESCE(MAX(SnapshotId), 0)) + 1
    FROM stg.MddMacSourceSnapshot;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'CallObservation')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'CallObservation', CONVERT(BIGINT, COALESCE(MAX(CallObservationId), 0)) + 1
    FROM stg.MddMacCallObservation;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'TimingObservation')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'TimingObservation', CONVERT(BIGINT, COALESCE(MAX(TimingObservationId), 0)) + 1
    FROM stg.MddMacTimingObservation;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'SituationEvidence')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'SituationEvidence', CONVERT(BIGINT, COALESCE(MAX(SituationEvidenceId), 0)) + 1
    FROM stg.MddMacSituationEvidence;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'ServiceSectionEvidence')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'ServiceSectionEvidence', CONVERT(BIGINT, COALESCE(MAX(ServiceSectionEvidenceId), 0)) + 1
    FROM stg.MddMacServiceSectionEvidence;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'AttributeEvidence')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'AttributeEvidence', CONVERT(BIGINT, COALESCE(MAX(AttributeEvidenceId), 0)) + 1
    FROM stg.MddMacAttributeEvidence;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'ResponseError')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'ResponseError', CONVERT(BIGINT, COALESCE(MAX(ResponseErrorId), 0)) + 1
    FROM stg.MddMacResponseError;
IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactIdentityAllocator WHERE FamilyCode = 'ParserGap')
    INSERT INTO stg.MddMacCompactIdentityAllocator (FamilyCode, NextIdentity)
    SELECT 'ParserGap', CONVERT(BIGINT, COALESCE(MAX(ParserGapId), 0)) + 1
    FROM stg.MddMacParserGap;
GO

/*
    Store one already-parity-checked historical envelope.  This procedure is
    intentionally not a delete/backfill routine: it only adds one immutable
    unit and its durable ledger state.  The Python runner performs the exact
    readback comparison before calling the mark-verified procedure.
*/
CREATE OR ALTER PROCEDURE stg.uspMddMacCompactStoreHistoricalEnvelope
    @MigrationId UNIQUEIDENTIFIER,
    @SnapshotId BIGINT,
    @EnvelopeJson NVARCHAR(MAX),
    @EnvelopeSha256 CHAR(64),
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
    @SourceSnapshotRowCount BIGINT,
    @CallRowCount BIGINT,
    @TimingRowCount BIGINT,
    @SituationRowCount BIGINT,
    @ServiceSectionRowCount BIGINT,
    @AttributeRowCount BIGINT,
    @ResponseErrorRowCount BIGINT,
    @ParserGapRowCount BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @MigrationId IS NULL OR @SnapshotId IS NULL OR @SnapshotId <= 0
        THROW 51210, 'Historical compact unit identity is invalid.', 1;
    IF ISJSON(@EnvelopeJson) <> 1
        THROW 51211, 'Historical compact envelope JSON is invalid.', 1;
    IF @SourceSnapshotRowCount <> 1
        THROW 51212, 'Exactly one source snapshot row is required per compact unit.', 1;
    IF LOWER(CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), @EnvelopeJson)), 2))
        <> LOWER(@EnvelopeSha256)
        THROW 51213, 'Historical compact envelope SHA-256 does not match the canonical UTF-16 payload.', 1;
    IF JSON_VALUE(@EnvelopeJson, '$.envelope_type') <> 'COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE'
       OR TRY_CONVERT(INT, JSON_VALUE(@EnvelopeJson, '$.storage_format_version')) <> 1
       OR JSON_VALUE(@EnvelopeJson, '$.serialization') <> 'CTI_CANONICAL_JSON_UTF16LE_V1'
       OR JSON_VALUE(@EnvelopeJson, '$.hash_algorithm') <> 'SHA2_256_UTF16LE'
        THROW 51218, 'Historical compact envelope type or version is unsupported.', 1;
    IF DATALENGTH(@EnvelopeJson) > 536870912
        THROW 51214, 'Historical compact envelope exceeds the 512 MiB logical safety bound.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;

        IF NOT EXISTS
        (
            SELECT 1 FROM stg.MddMacCompactMigration
            WHERE MigrationId = @MigrationId AND Status = 'RUNNING'
        )
            THROW 51215, 'Historical compact migration is not RUNNING.', 1;

        DECLARE @ExistingStatus VARCHAR(20);
        DECLARE @ExistingMigrationId UNIQUEIDENTIFIER;
        SELECT @ExistingStatus = VerificationStatus,
               @ExistingMigrationId = MigrationId
        FROM stg.MddMacCompactObservationEnvelope WITH (UPDLOCK, HOLDLOCK)
        WHERE SnapshotId = @SnapshotId;
        IF @ExistingStatus IS NOT NULL
           AND (@ExistingMigrationId IS NULL OR @ExistingMigrationId <> @MigrationId)
            THROW 51219, 'The compact snapshot is already owned by another migration.', 1;

        IF @ExistingStatus = 'VERIFIED'
        BEGIN
            IF EXISTS
            (
                SELECT 1 FROM stg.MddMacCompactObservationEnvelope
                WHERE SnapshotId = @SnapshotId AND EnvelopeSha256 <> @EnvelopeSha256
            )
                THROW 51216, 'A verified compact snapshot already exists with a different hash.', 1;
            IF NOT EXISTS
            (
                SELECT 1 FROM stg.MddMacCompactMigrationUnit
                WHERE MigrationId = @MigrationId AND SnapshotId = @SnapshotId
            )
                INSERT INTO stg.MddMacCompactMigrationUnit
                    (MigrationId, SnapshotId, Status, SourceSnapshotRowCount,
                     CallRowCount, TimingRowCount, SituationRowCount,
                     ServiceSectionRowCount, AttributeRowCount,
                     ResponseErrorRowCount, ParserGapRowCount, EnvelopeSha256,
                     VerifiedAtUtc)
                VALUES
                    (@MigrationId, @SnapshotId, 'VERIFIED', @SourceSnapshotRowCount,
                     @CallRowCount, @TimingRowCount, @SituationRowCount,
                     @ServiceSectionRowCount, @AttributeRowCount,
                     @ResponseErrorRowCount, @ParserGapRowCount, @EnvelopeSha256,
                     SYSUTCDATETIME());
            COMMIT TRANSACTION;
            SELECT @SnapshotId AS SnapshotId, 'ALREADY_VERIFIED' AS Outcome;
            RETURN;
        END;

        IF @ExistingStatus = 'BUILT'
        BEGIN
            COMMIT TRANSACTION;
            SELECT @SnapshotId AS SnapshotId, 'ALREADY_BUILT' AS Outcome;
            RETURN;
        END;

        IF @ExistingStatus IS NOT NULL
            THROW 51217, 'An unverified compact unit already exists; verify or quarantine it before retrying.', 1;

        INSERT INTO stg.MddMacCompactObservationEnvelope
        (
            SnapshotId, StorageFormatVersion, EnvelopeType, Serialization,
            HashAlgorithm, RunId, AuditId, TargetIdentity, SamplingTargetId,
            LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
            ParserVersion, ApplicationStatus, PayloadHashSha256,
            SourceSnapshotRowCount, CallRowCount, TimingRowCount,
            SituationRowCount, ServiceSectionRowCount, AttributeRowCount,
            ResponseErrorRowCount, ParserGapRowCount, EnvelopeJsonGzip,
            EnvelopeSha256, EnvelopeLogicalBytes, EnvelopeCompressedBytes,
            VerificationStatus, MigrationId
        )
        SELECT
            @SnapshotId, 1,
            'COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE',
            'CTI_CANONICAL_JSON_UTF16LE_V1',
            'SHA2_256_UTF16LE', @RunId, @AuditId, @TargetIdentity,
            @SamplingTargetId, @LogicalDueBucket, @ObservedAtUtc,
            @ResponseTimestampSourceText, @ParserVersion, @ApplicationStatus,
            @PayloadHashSha256, @SourceSnapshotRowCount, @CallRowCount,
            @TimingRowCount, @SituationRowCount, @ServiceSectionRowCount,
            @AttributeRowCount, @ResponseErrorRowCount, @ParserGapRowCount,
            COMPRESS(@EnvelopeJson), @EnvelopeSha256, DATALENGTH(@EnvelopeJson),
            DATALENGTH(COMPRESS(@EnvelopeJson)), 'BUILT', @MigrationId;

        INSERT INTO stg.MddMacCompactMigrationUnit
        (
            MigrationId, SnapshotId, Status, SourceSnapshotRowCount,
            CallRowCount, TimingRowCount, SituationRowCount,
            ServiceSectionRowCount, AttributeRowCount, ResponseErrorRowCount,
            ParserGapRowCount, EnvelopeSha256
        )
        VALUES
        (
            @MigrationId, @SnapshotId, 'BUILT', @SourceSnapshotRowCount,
            @CallRowCount, @TimingRowCount, @SituationRowCount,
            @ServiceSectionRowCount, @AttributeRowCount, @ResponseErrorRowCount,
            @ParserGapRowCount, @EnvelopeSha256
        );

        COMMIT TRANSACTION;
        SELECT @SnapshotId AS SnapshotId, 'BUILT' AS Outcome;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE stg.uspMddMacCompactMarkUnit
    @MigrationId UNIQUEIDENTIFIER,
    @SnapshotId BIGINT,
    @Status VARCHAR(20),
    @FailureCode VARCHAR(80) = NULL,
    @FailureMessage NVARCHAR(2000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @Status NOT IN ('VERIFIED', 'QUARANTINED')
        THROW 51220, 'A compact unit can only be marked VERIFIED or QUARANTINED.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;
        IF NOT EXISTS
        (
            SELECT 1 FROM stg.MddMacCompactMigrationUnit WITH (UPDLOCK, HOLDLOCK)
            WHERE MigrationId = @MigrationId AND SnapshotId = @SnapshotId
        )
            THROW 51221, 'Compact migration unit does not exist.', 1;

        UPDATE stg.MddMacCompactMigrationUnit
        SET Status = @Status,
            VerifiedAtUtc = CASE WHEN @Status = 'VERIFIED' THEN SYSUTCDATETIME() ELSE NULL END,
            FailureCode = @FailureCode,
            FailureMessage = @FailureMessage
        WHERE MigrationId = @MigrationId AND SnapshotId = @SnapshotId;

        UPDATE stg.MddMacCompactObservationEnvelope
        SET VerificationStatus = @Status,
            VerifiedAtUtc = CASE WHEN @Status = 'VERIFIED' THEN SYSUTCDATETIME() ELSE NULL END
        WHERE SnapshotId = @SnapshotId AND MigrationId = @MigrationId;

        UPDATE m
        SET VerifiedUnitCount =
                (SELECT COUNT_BIG(*) FROM stg.MddMacCompactMigrationUnit
                 WHERE MigrationId = @MigrationId AND Status = 'VERIFIED'),
            QuarantinedUnitCount =
                (SELECT COUNT_BIG(*) FROM stg.MddMacCompactMigrationUnit
                 WHERE MigrationId = @MigrationId AND Status = 'QUARANTINED'),
            LastSnapshotId = @SnapshotId
        FROM stg.MddMacCompactMigration AS m
        WHERE m.MigrationId = @MigrationId;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

/*
    Future-write procedure.  It accepts the exact same logical rowsets that
    the Python adapter already builds, but receives them as one canonical
    envelope so no legacy full rowstore representation is written.  The
    identity allocator makes the compatibility views expose stable BIGINT
    identities without adding one physical row per observation.
*/
CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshotCompact
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
    @EnvelopeJson NVARCHAR(MAX) = NULL,
    @EnvelopeSha256 CHAR(64) = NULL,
    @CompletedRunStatus VARCHAR(40) = NULL,
    @CompletedAtUtc DATETIME2(7) = NULL,
    @RunSummaryJson NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @CompletedRunStatus IS NOT NULL
    BEGIN
        IF @RunId IS NULL OR @CompletedAtUtc IS NULL OR ISJSON(@RunSummaryJson) <> 1
            THROW 51230, 'Compact MDD Mac run finalization arguments are invalid.', 1;
        BEGIN TRY
            BEGIN TRANSACTION;
            UPDATE ctl.MddMacCollectorRun
            SET CompletedAtUtc = @CompletedAtUtc,
                Status = @CompletedRunStatus,
                SummaryJson = @RunSummaryJson
            WHERE RunId = @RunId;
            UPDATE ctl.MddMacDispatcherClaim
            SET Status = @CompletedRunStatus,
                CompletedAtUtc = @CompletedAtUtc
            WHERE RunId = @RunId;
            COMMIT TRANSACTION;
            SELECT CAST(NULL AS BIGINT) AS SnapshotId;
            RETURN;
        END TRY
        BEGIN CATCH
            IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
            THROW;
        END CATCH;
    END;

    IF ISJSON(@EnvelopeJson) <> 1 OR @EnvelopeSha256 IS NULL
        THROW 51231, 'Compact envelope arguments are invalid.', 1;
    IF LOWER(CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), @EnvelopeJson)), 2))
        <> LOWER(@EnvelopeSha256)
        THROW 51232, 'Compact future-write envelope hash mismatch.', 1;
    IF JSON_VALUE(@EnvelopeJson, '$.envelope_type') <> 'COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE'
       OR TRY_CONVERT(INT, JSON_VALUE(@EnvelopeJson, '$.storage_format_version')) <> 1
       OR JSON_VALUE(@EnvelopeJson, '$.serialization') <> 'CTI_CANONICAL_JSON_UTF16LE_V1'
       OR JSON_VALUE(@EnvelopeJson, '$.hash_algorithm') <> 'SHA2_256_UTF16LE'
        THROW 51237, 'Compact future-write envelope type or version is unsupported.', 1;

    IF NOT EXISTS
    (
        SELECT 1 FROM stg.MddMacCompactStorageState
        WHERE StateId = 1 AND StorageFormatVersion = 1
          AND FutureWriteEnabled = 1 AND CompatibilityVerified = 1
    )
        THROW 51233, 'Compact future writes are disabled until compatibility verification is recorded.', 1;

    DECLARE @CallCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.calls'));
    DECLARE @TimingCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.timings'));
    DECLARE @SituationCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.situations'));
    DECLARE @SectionCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.service_sections'));
    DECLARE @AttributeCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.attributes'));
    DECLARE @ErrorCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.response_errors'));
    DECLARE @GapCount BIGINT = (SELECT COUNT_BIG(*) FROM OPENJSON(@EnvelopeJson, '$.parser_gaps'));
    DECLARE @SnapshotId BIGINT;
    DECLARE @NextIdentity BIGINT;
    DECLARE @CallStart BIGINT = NULL;
    DECLARE @TimingStart BIGINT = NULL;
    DECLARE @SituationStart BIGINT = NULL;
    DECLARE @SectionStart BIGINT = NULL;
    DECLARE @AttributeStart BIGINT = NULL;
    DECLARE @ErrorStart BIGINT = NULL;
    DECLARE @GapStart BIGINT = NULL;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT @SnapshotId = SnapshotId
        FROM stg.MddMacCompactObservationEnvelope WITH (UPDLOCK, HOLDLOCK)
        WHERE AuditId = @AuditId;
        IF @SnapshotId IS NOT NULL
        BEGIN
            IF EXISTS
            (
                SELECT 1 FROM stg.MddMacCompactObservationEnvelope
                WHERE SnapshotId = @SnapshotId AND EnvelopeSha256 <> @EnvelopeSha256
            )
                THROW 51234, 'A compact retry reused an AuditId with different logical evidence.', 1;
            UPDATE ctl.MddMacTargetAudit
            SET PersistenceOutcome = 'PERSISTED_COMPACT_SNAPSHOT'
            WHERE AuditId = @AuditId;
            COMMIT TRANSACTION;
            SELECT @SnapshotId AS SnapshotId, 'ALREADY_PERSISTED' AS Outcome;
            RETURN;
        END;

        SELECT @NextIdentity = NextIdentity
        FROM stg.MddMacCompactIdentityAllocator WITH (UPDLOCK, HOLDLOCK)
        WHERE FamilyCode = 'Snapshot';
        IF @NextIdentity IS NULL THROW 51235, 'Compact Snapshot identity allocator is missing.', 1;
        SET @SnapshotId = @NextIdentity;
        UPDATE stg.MddMacCompactIdentityAllocator
        SET NextIdentity = NextIdentity + 1, UpdatedAtUtc = SYSUTCDATETIME()
        WHERE FamilyCode = 'Snapshot';

        DECLARE @FamilyCode VARCHAR(40);
        DECLARE @FamilyCount BIGINT;
        DECLARE @FamilyStart BIGINT;
        DECLARE family_cursor CURSOR LOCAL FAST_FORWARD FOR
            SELECT FamilyCode, FamilyCount
            FROM (VALUES
                ('CallObservation', @CallCount),
                ('TimingObservation', @TimingCount),
                ('SituationEvidence', @SituationCount),
                ('ServiceSectionEvidence', @SectionCount),
                ('AttributeEvidence', @AttributeCount),
                ('ResponseError', @ErrorCount),
                ('ParserGap', @GapCount)
            ) AS families(FamilyCode, FamilyCount);
        OPEN family_cursor;
        FETCH NEXT FROM family_cursor INTO @FamilyCode, @FamilyCount;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            IF @FamilyCount > 0
            BEGIN
                SELECT @FamilyStart = NextIdentity
                FROM stg.MddMacCompactIdentityAllocator WITH (UPDLOCK, HOLDLOCK)
                WHERE FamilyCode = @FamilyCode;
                IF @FamilyStart IS NULL THROW 51236, 'Compact family identity allocator is missing.', 1;
                UPDATE stg.MddMacCompactIdentityAllocator
                SET NextIdentity = NextIdentity + @FamilyCount,
                    UpdatedAtUtc = SYSUTCDATETIME()
                WHERE FamilyCode = @FamilyCode;
                IF @FamilyCode = 'CallObservation' SET @CallStart = @FamilyStart;
                IF @FamilyCode = 'TimingObservation' SET @TimingStart = @FamilyStart;
                IF @FamilyCode = 'SituationEvidence' SET @SituationStart = @FamilyStart;
                IF @FamilyCode = 'ServiceSectionEvidence' SET @SectionStart = @FamilyStart;
                IF @FamilyCode = 'AttributeEvidence' SET @AttributeStart = @FamilyStart;
                IF @FamilyCode = 'ResponseError' SET @ErrorStart = @FamilyStart;
                IF @FamilyCode = 'ParserGap' SET @GapStart = @FamilyStart;
            END;
            FETCH NEXT FROM family_cursor INTO @FamilyCode, @FamilyCount;
        END;
        CLOSE family_cursor;
        DEALLOCATE family_cursor;

        INSERT INTO stg.MddMacCompactObservationEnvelope
        (
            SnapshotId, StorageFormatVersion, EnvelopeType, Serialization,
            HashAlgorithm, RunId, AuditId, TargetIdentity, SamplingTargetId,
            LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
            ParserVersion, ApplicationStatus, PayloadHashSha256,
            SourceSnapshotRowCount, CallRowCount, TimingRowCount,
            SituationRowCount, ServiceSectionRowCount, AttributeRowCount,
            ResponseErrorRowCount, ParserGapRowCount, EnvelopeJsonGzip,
            EnvelopeSha256, EnvelopeLogicalBytes, EnvelopeCompressedBytes,
            VerificationStatus, CallObservationIdStart, TimingObservationIdStart,
            SituationEvidenceIdStart, ServiceSectionEvidenceIdStart,
            AttributeEvidenceIdStart, ResponseErrorIdStart, ParserGapIdStart
        )
        VALUES
        (
            @SnapshotId, 1, 'COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE',
            'CTI_CANONICAL_JSON_UTF16LE_V1', 'SHA2_256_UTF16LE', @RunId,
            @AuditId, @TargetIdentity, @SamplingTargetId, @LogicalDueBucket,
            @ObservedAtUtc, @ResponseTimestampSourceText, @ParserVersion,
            @ApplicationStatus, @PayloadHashSha256, 1, @CallCount,
            @TimingCount, @SituationCount, @SectionCount, @AttributeCount,
            @ErrorCount, @GapCount, COMPRESS(@EnvelopeJson), @EnvelopeSha256,
            DATALENGTH(@EnvelopeJson), DATALENGTH(COMPRESS(@EnvelopeJson)),
            'VERIFIED', @CallStart, @TimingStart, @SituationStart, @SectionStart,
            @AttributeStart, @ErrorStart, @GapStart
        );

        UPDATE ctl.MddMacTargetAudit
        SET PersistenceOutcome = 'PERSISTED_COMPACT_SNAPSHOT',
            StopEventResultCount = JSON_VALUE(@EnvelopeJson, '$.source_snapshot.result_count')
        WHERE AuditId = @AuditId;

        COMMIT TRANSACTION;
        SELECT @SnapshotId AS SnapshotId, 'PERSISTED' AS Outcome;
    END TRY
    BEGIN CATCH
        IF CURSOR_STATUS('local', 'family_cursor') >= 0
        BEGIN
            CLOSE family_cursor;
            DEALLOCATE family_cursor;
        END;
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

/* Readback of the physical envelope for the bounded parity validator. */
CREATE OR ALTER VIEW stg.vMddMacCompactObservationEnvelopeJson
AS
SELECT
    e.SnapshotId, e.StorageFormatVersion, e.EnvelopeType, e.Serialization,
    e.HashAlgorithm, e.RunId, e.AuditId, e.TargetIdentity, e.SamplingTargetId,
    e.LogicalDueBucket, e.ObservedAtUtc, e.ResponseTimestampSourceText,
    e.ParserVersion, e.ApplicationStatus, e.PayloadHashSha256,
    e.SourceSnapshotRowCount, e.CallRowCount, e.TimingRowCount,
    e.SituationRowCount, e.ServiceSectionRowCount, e.AttributeRowCount,
    e.ResponseErrorRowCount, e.ParserGapRowCount, e.EnvelopeSha256,
    e.EnvelopeLogicalBytes, e.EnvelopeCompressedBytes, e.VerificationStatus,
    e.VerifiedAtUtc, e.CreatedAtUtc, e.MigrationId,
    CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)) AS EnvelopeJson,
    e.EnvelopeJsonGzip
FROM stg.MddMacCompactObservationEnvelope AS e;
GO

/* Compact source snapshot compatibility projection. */
CREATE OR ALTER VIEW stg.vMddMacCompactSourceSnapshot
AS
SELECT
    e.SnapshotId, e.RunId, e.AuditId, e.TargetIdentity, e.SamplingTargetId,
    e.LogicalDueBucket, e.ObservedAtUtc, e.ResponseTimestampSourceText,
    e.ParserVersion, e.ApplicationStatus, e.PayloadHashSha256,
    COALESCE(
        source_row.SnapshotJson,
        CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), source_row.SnapshotJsonGzipHex, 2)))
    ) AS SnapshotJson,
    TRY_CONVERT(VARBINARY(MAX), source_row.SnapshotJsonGzipHex, 2) AS SnapshotJsonGzip,
    COALESCE(source_row.CreatedAtUtc, e.CreatedAtUtc) AS CreatedAtUtc
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.source_snapshot')
WITH
(
    SnapshotJson NVARCHAR(MAX) '$.snapshot_json',
    SnapshotJsonGzipHex NVARCHAR(MAX) '$.snapshot_json_gzip_hex',
    CreatedAtUtc DATETIME2(7) '$.created_at_utc'
) AS source_row
WHERE e.VerificationStatus = 'VERIFIED';
GO

/*
    Compact readback views use explicit OPENJSON WITH schemas.  They expose
    the existing logical columns and decode any historical gzip companion
    values retained inside the envelope; future envelopes leave those legacy
    companion columns NULL because the envelope is the single representation.
*/
CREATE OR ALTER VIEW stg.vMddMacCompactCallObservation
AS
SELECT
    COALESCE(j.CallObservationId, e.CallObservationIdStart + CONVERT(BIGINT, a.[key])) AS CallObservationId,
    COALESCE(j.SnapshotId, e.SnapshotId) AS SnapshotId,
    j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.CallScope, j.CallOrdinal,
    j.SourcePath, j.CallAtStopSourcePath, j.StopPointRef, j.StopDisplayText,
    j.StopSeqNumber, j.PlannedBay, j.EstimatedBay, j.UnplannedStop,
    j.NotServicedStop, j.NoBoardingAtStop, j.NoAlightingAtStop, j.Cancelled,
    j.Deviation, j.OperatingDayRef, j.JourneyRef, j.VehicleJourneyRef,
    j.DatedVehicleJourneyRef, j.LineRef, j.DirectionRef, j.OperatorRef,
    j.PtMode, j.Submode, j.OriginStopPointRef, j.OriginText,
    j.DestinationStopPointRef, j.DestinationText,
    COALESCE(j.SourcePathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2)))) AS SourcePathsJson,
    COALESCE(j.SourceEventJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceEventJsonGzipHex, 2)))) AS SourceEventJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2) AS SourcePathsJsonGzip,
    TRY_CONVERT(VARBINARY(MAX), j.SourceEventJsonGzipHex, 2) AS SourceEventJsonGzip
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.calls') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    CallObservationId BIGINT '$.call_observation_id',
    SnapshotId BIGINT '$.snapshot_id',
    ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal',
    EventOrdinal INT '$.event_ordinal',
    CallScope VARCHAR(20) '$.call_scope',
    CallOrdinal INT '$.call_ordinal',
    SourcePath NVARCHAR(2048) '$.source_path',
    CallAtStopSourcePath NVARCHAR(2048) '$.call_at_stop_source_path',
    StopPointRef NVARCHAR(400) '$.stop_point_ref',
    StopDisplayText NVARCHAR(1000) '$.stop_display_text',
    StopSeqNumber INT '$.stop_seq_number',
    PlannedBay NVARCHAR(400) '$.planned_bay',
    EstimatedBay NVARCHAR(400) '$.estimated_bay',
    UnplannedStop BIT '$.unplanned_stop',
    NotServicedStop BIT '$.not_serviced_stop',
    NoBoardingAtStop BIT '$.no_boarding_at_stop',
    NoAlightingAtStop BIT '$.no_alighting_at_stop',
    Cancelled BIT '$.cancelled',
    Deviation BIT '$.deviation',
    OperatingDayRef NVARCHAR(200) '$.operating_day_ref',
    JourneyRef NVARCHAR(500) '$.journey_ref',
    VehicleJourneyRef NVARCHAR(500) '$.vehicle_journey_ref',
    DatedVehicleJourneyRef NVARCHAR(500) '$.dated_vehicle_journey_ref',
    LineRef NVARCHAR(400) '$.line_ref',
    DirectionRef NVARCHAR(200) '$.direction_ref',
    OperatorRef NVARCHAR(300) '$.operator_ref',
    PtMode NVARCHAR(200) '$.pt_mode',
    Submode NVARCHAR(300) '$.submode',
    OriginStopPointRef NVARCHAR(400) '$.origin_stop_point_ref',
    OriginText NVARCHAR(1000) '$.origin_text',
    DestinationStopPointRef NVARCHAR(400) '$.destination_stop_point_ref',
    DestinationText NVARCHAR(1000) '$.destination_text',
    SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
    SourceEventJson NVARCHAR(MAX) '$.source_event_json',
    SourcePathsJsonGzipHex NVARCHAR(MAX) '$.source_paths_json_gzip_hex',
    SourceEventJsonGzipHex NVARCHAR(MAX) '$.source_event_json_gzip_hex'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW stg.vMddMacCompactTimingObservation
AS
SELECT
    COALESCE(j.TimingObservationId, e.TimingObservationIdStart + CONVERT(BIGINT, a.[key])) AS TimingObservationId,
    COALESCE(j.SnapshotId, e.SnapshotId) AS SnapshotId,
    j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.CallScope, j.CallOrdinal,
    j.TimingContext, j.OperatingDayRef, j.JourneyRef, j.VehicleJourneyRef,
    j.DatedVehicleJourneyRef, j.LineRef, j.DirectionRef, j.OperatorRef,
    j.StopPointRef, j.StopSeqNumber, j.TimetabledTimeSourceText,
    j.EstimatedTimeSourceText, j.EstimatedTimeLowSourceText,
    j.EstimatedTimeHighSourceText, j.RecordedAtTimeSourceText,
    j.TimingSourcePath, j.TimetabledTimeSourcePath, j.EstimatedTimeSourcePath,
    j.EstimatedTimeLowSourcePath, j.EstimatedTimeHighSourcePath,
    j.RecordedAtTimeSourcePath, j.StopPointRefSourcePath,
    j.StopSeqNumberSourcePath, j.SourceEventIdentity,
    j.SourceEventIdentityVersion, j.SourceEventIdentityStatus,
    COALESCE(j.SourceEventIdentityBasisJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityBasisJsonGzipHex, 2)))) AS SourceEventIdentityBasisJson,
    COALESCE(j.SourceEventIdentityMissingFieldsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityMissingFieldsJsonGzipHex, 2)))) AS SourceEventIdentityMissingFieldsJson,
    COALESCE(j.SourceEventIdentityAmbiguityJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityAmbiguityJsonGzipHex, 2)))) AS SourceEventIdentityAmbiguityJson,
    COALESCE(j.SourcePathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2)))) AS SourcePathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityBasisJsonGzipHex, 2) AS SourceEventIdentityBasisJsonGzip,
    TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityMissingFieldsJsonGzipHex, 2) AS SourceEventIdentityMissingFieldsJsonGzip,
    TRY_CONVERT(VARBINARY(MAX), j.SourceEventIdentityAmbiguityJsonGzipHex, 2) AS SourceEventIdentityAmbiguityJsonGzip,
    TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2) AS SourcePathsJsonGzip
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.timings') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    TimingObservationId BIGINT '$.timing_observation_id',
    SnapshotId BIGINT '$.snapshot_id',
    ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal',
    EventOrdinal INT '$.event_ordinal',
    CallScope VARCHAR(20) '$.call_scope',
    CallOrdinal INT '$.call_ordinal',
    TimingContext VARCHAR(20) '$.timing_context',
    OperatingDayRef NVARCHAR(200) '$.operating_day_ref',
    JourneyRef NVARCHAR(500) '$.journey_ref',
    VehicleJourneyRef NVARCHAR(500) '$.vehicle_journey_ref',
    DatedVehicleJourneyRef NVARCHAR(500) '$.dated_vehicle_journey_ref',
    LineRef NVARCHAR(400) '$.line_ref',
    DirectionRef NVARCHAR(200) '$.direction_ref',
    OperatorRef NVARCHAR(300) '$.operator_ref',
    StopPointRef NVARCHAR(400) '$.stop_point_ref',
    StopSeqNumber INT '$.stop_seq_number',
    TimetabledTimeSourceText NVARCHAR(256) '$.timetabled_time',
    EstimatedTimeSourceText NVARCHAR(256) '$.estimated_time',
    EstimatedTimeLowSourceText NVARCHAR(256) '$.estimated_time_low',
    EstimatedTimeHighSourceText NVARCHAR(256) '$.estimated_time_high',
    RecordedAtTimeSourceText NVARCHAR(256) '$.recorded_at_time',
    TimingSourcePath NVARCHAR(2048) '$.source_path',
    TimetabledTimeSourcePath NVARCHAR(2048) '$.timetabled_time_source_path',
    EstimatedTimeSourcePath NVARCHAR(2048) '$.estimated_time_source_path',
    EstimatedTimeLowSourcePath NVARCHAR(2048) '$.estimated_time_low_source_path',
    EstimatedTimeHighSourcePath NVARCHAR(2048) '$.estimated_time_high_source_path',
    RecordedAtTimeSourcePath NVARCHAR(2048) '$.recorded_at_time_source_path',
    StopPointRefSourcePath NVARCHAR(2048) '$.stop_point_ref_source_path',
    StopSeqNumberSourcePath NVARCHAR(2048) '$.stop_seq_number_source_path',
    SourceEventIdentity NVARCHAR(200) '$.source_event_identity',
    SourceEventIdentityVersion VARCHAR(40) '$.source_event_identity_version',
    SourceEventIdentityStatus VARCHAR(60) '$.source_event_identity_status',
    SourceEventIdentityBasisJson NVARCHAR(MAX) '$.source_event_identity_basis_json',
    SourceEventIdentityMissingFieldsJson NVARCHAR(MAX) '$.source_event_identity_missing_fields_json',
    SourceEventIdentityAmbiguityJson NVARCHAR(MAX) '$.source_event_identity_ambiguity_json',
    SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
    SourceEventIdentityBasisJsonGzipHex NVARCHAR(MAX) '$.source_event_identity_basis_json_gzip_hex',
    SourceEventIdentityMissingFieldsJsonGzipHex NVARCHAR(MAX) '$.source_event_identity_missing_fields_json_gzip_hex',
    SourceEventIdentityAmbiguityJsonGzipHex NVARCHAR(MAX) '$.source_event_identity_ambiguity_json_gzip_hex',
    SourcePathsJsonGzipHex NVARCHAR(MAX) '$.source_paths_json_gzip_hex'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

/* The remaining evidence families are exposed with a compact, bounded JSON
   contract.  They are separate views so consumers can select only the
   evidence grain they need; no interactive query must decompress unrelated
   families. */
CREATE OR ALTER VIEW stg.vMddMacCompactSituationEvidence
AS
SELECT
    COALESCE(j.SituationEvidenceId, e.SituationEvidenceIdStart + CONVERT(BIGINT, a.[key])) AS SituationEvidenceId,
    e.SnapshotId, j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.EvidenceType,
    j.SituationType, j.ReferenceType, j.ReferenceValue, j.ParticipantRef,
    j.SituationNumber, j.VersionSourceText, j.Identifier, j.Summary,
    COALESCE(j.Description, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.DescriptionGzipHex, 2)))) AS Description,
    TRY_CONVERT(VARBINARY(MAX), j.DescriptionGzipHex, 2) AS DescriptionGzip,
    COALESCE(j.Detail, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.DetailGzipHex, 2)))) AS Detail,
    TRY_CONVERT(VARBINARY(MAX), j.DetailGzipHex, 2) AS DetailGzip,
    j.CreationTimeSourceText, j.ValidFromSourceText,
    j.ValidToSourceText, j.ProviderSource, j.ProviderSourceType,
    j.PrioritySourceText,
    COALESCE(j.AffectsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.AffectsJsonGzipHex, 2)))) AS AffectsJson,
    TRY_CONVERT(VARBINARY(MAX), j.AffectsJsonGzipHex, 2) AS AffectsJsonGzip,
    COALESCE(j.AffectedEntityJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.AffectedEntityJsonGzipHex, 2)))) AS AffectedEntityJson,
    TRY_CONVERT(VARBINARY(MAX), j.AffectedEntityJsonGzipHex, 2) AS AffectedEntityJsonGzip,
    COALESCE(j.AffectedJourneyJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.AffectedJourneyJsonGzipHex, 2)))) AS AffectedJourneyJson,
    TRY_CONVERT(VARBINARY(MAX), j.AffectedJourneyJsonGzipHex, 2) AS AffectedJourneyJsonGzip,
    COALESCE(j.AffectedLineJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.AffectedLineJsonGzipHex, 2)))) AS AffectedLineJson,
    TRY_CONVERT(VARBINARY(MAX), j.AffectedLineJsonGzipHex, 2) AS AffectedLineJsonGzip,
    j.IdentityKey,
    j.AssociationScope, j.CallScope, j.SourcePath,
    COALESCE(j.FieldPathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.FieldPathsJsonGzipHex, 2)))) AS FieldPathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.FieldPathsJsonGzipHex, 2) AS FieldPathsJsonGzip,
    COALESCE(j.AffectedEntityFieldPathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.AffectedEntityFieldPathsJsonGzipHex, 2)))) AS AffectedEntityFieldPathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.AffectedEntityFieldPathsJsonGzipHex, 2) AS AffectedEntityFieldPathsJsonGzip,
    COALESCE(j.SourceRepresentationJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2)))) AS SourceRepresentationJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2) AS SourceRepresentationJsonGzip
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.situations') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    SituationEvidenceId BIGINT '$.situation_evidence_id',
    ResultId NVARCHAR(400) '$.result_id', ResultOrdinal INT '$.result_ordinal',
    EventOrdinal INT '$.event_ordinal', EvidenceType VARCHAR(40) '$.evidence_type',
    SituationType VARCHAR(80) '$.situation_type', ReferenceType VARCHAR(40) '$.reference_type',
    ReferenceValue NVARCHAR(2000) '$.reference_value', ParticipantRef NVARCHAR(400) '$.participant_ref',
    SituationNumber NVARCHAR(500) '$.situation_number', VersionSourceText NVARCHAR(200) '$.version_source_text',
    Identifier NVARCHAR(500) '$.identifier', Summary NVARCHAR(4000) '$.summary',
    Description NVARCHAR(MAX) '$.description', DescriptionGzipHex NVARCHAR(MAX) '$.description_gzip_hex',
    Detail NVARCHAR(MAX) '$.detail', DetailGzipHex NVARCHAR(MAX) '$.detail_gzip_hex',
    CreationTimeSourceText NVARCHAR(256) '$.creation_time_source_text',
    ValidFromSourceText NVARCHAR(256) '$.valid_from_source_text', ValidToSourceText NVARCHAR(256) '$.valid_to_source_text',
    ProviderSource NVARCHAR(1000) '$.source', ProviderSourceType NVARCHAR(1000) '$.source_type',
    PrioritySourceText NVARCHAR(200) '$.priority_source_text', AffectsJson NVARCHAR(MAX) '$.affects_json',
    AffectsJsonGzipHex NVARCHAR(MAX) '$.affects_json_gzip_hex', AffectedEntityJson NVARCHAR(MAX) '$.affected_entity_json',
    AffectedEntityJsonGzipHex NVARCHAR(MAX) '$.affected_entity_json_gzip_hex', AffectedJourneyJson NVARCHAR(MAX) '$.affected_journey_json',
    AffectedJourneyJsonGzipHex NVARCHAR(MAX) '$.affected_journey_json_gzip_hex', AffectedLineJson NVARCHAR(MAX) '$.affected_line_json',
    AffectedLineJsonGzipHex NVARCHAR(MAX) '$.affected_line_json_gzip_hex', IdentityKey NVARCHAR(1000) '$.identity_key',
    AssociationScope VARCHAR(40) '$.association_scope', CallScope VARCHAR(20) '$.call_scope',
    SourcePath NVARCHAR(2048) '$.source_path', FieldPathsJson NVARCHAR(MAX) '$.field_paths_json',
    FieldPathsJsonGzipHex NVARCHAR(MAX) '$.field_paths_json_gzip_hex', AffectedEntityFieldPathsJson NVARCHAR(MAX) '$.affected_entity_field_paths_json',
    AffectedEntityFieldPathsJsonGzipHex NVARCHAR(MAX) '$.affected_entity_field_paths_json_gzip_hex',
    SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json', SourceRepresentationJsonGzipHex NVARCHAR(MAX) '$.source_representation_json_gzip_hex'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW stg.vMddMacCompactServiceSectionEvidence
AS
SELECT
    COALESCE(j.ServiceSectionEvidenceId, e.ServiceSectionEvidenceIdStart + CONVERT(BIGINT, a.[key])) AS ServiceSectionEvidenceId,
    e.SnapshotId, j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.SectionOrdinal,
    j.SourcePath, j.LineRef, j.DirectionRef, j.OperatorRef, j.PtMode,
    j.RailSubmode, j.BusSubmode, j.TramSubmode, j.Name, j.ShortName,
    COALESCE(j.Description, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.DescriptionGzipHex, 2)))) AS Description,
    TRY_CONVERT(VARBINARY(MAX), j.DescriptionGzipHex, 2) AS DescriptionGzip,
    COALESCE(j.RouteDescription, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.RouteDescriptionGzipHex, 2)))) AS RouteDescription,
    TRY_CONVERT(VARBINARY(MAX), j.RouteDescriptionGzipHex, 2) AS RouteDescriptionGzip,
    COALESCE(j.Via, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.ViaGzipHex, 2)))) AS Via,
    TRY_CONVERT(VARBINARY(MAX), j.ViaGzipHex, 2) AS ViaGzip,
    j.PublishedLineName,
    COALESCE(j.ModeSourceJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.ModeSourceJsonGzipHex, 2)))) AS ModeSourceJson,
    TRY_CONVERT(VARBINARY(MAX), j.ModeSourceJsonGzipHex, 2) AS ModeSourceJsonGzip,
    COALESCE(j.SourcePathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2)))) AS SourcePathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourcePathsJsonGzipHex, 2) AS SourcePathsJsonGzip,
    COALESCE(j.SourceRepresentationJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2)))) AS SourceRepresentationJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2) AS SourceRepresentationJsonGzip,
    j.SemanticRole
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.service_sections') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    ServiceSectionEvidenceId BIGINT '$.service_section_evidence_id', ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal', EventOrdinal INT '$.event_ordinal', SectionOrdinal INT '$.section_ordinal',
    SourcePath NVARCHAR(2048) '$.source_path', LineRef NVARCHAR(400) '$.line_ref', DirectionRef NVARCHAR(200) '$.direction_ref',
    OperatorRef NVARCHAR(300) '$.operator_ref', PtMode NVARCHAR(200) '$.pt_mode', RailSubmode NVARCHAR(300) '$.rail_submode',
    BusSubmode NVARCHAR(300) '$.bus_submode', TramSubmode NVARCHAR(300) '$.tram_submode', Name NVARCHAR(1000) '$.name',
    ShortName NVARCHAR(1000) '$.short_name', Description NVARCHAR(MAX) '$.description',
    DescriptionGzipHex NVARCHAR(MAX) '$.description_gzip_hex', RouteDescription NVARCHAR(MAX) '$.route_description',
    RouteDescriptionGzipHex NVARCHAR(MAX) '$.route_description_gzip_hex', Via NVARCHAR(MAX) '$.via',
    ViaGzipHex NVARCHAR(MAX) '$.via_gzip_hex',
    PublishedLineName NVARCHAR(1000) '$.published_line_name_text', ModeSourceJson NVARCHAR(MAX) '$.mode_source_representation',
    ModeSourceJsonGzipHex NVARCHAR(MAX) '$.mode_source_json_gzip_hex', SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
    SourcePathsJsonGzipHex NVARCHAR(MAX) '$.source_paths_json_gzip_hex', SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
    SourceRepresentationJsonGzipHex NVARCHAR(MAX) '$.source_representation_json_gzip_hex',
    SemanticRole VARCHAR(80) '$.semantic_role'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW stg.vMddMacCompactAttributeEvidence
AS
SELECT
    COALESCE(j.AttributeEvidenceId, e.AttributeEvidenceIdStart + CONVERT(BIGINT, a.[key])) AS AttributeEvidenceId,
    e.SnapshotId, j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.AttributeOrdinal,
    j.AttributeType, j.SourcePath, j.AssociationContextPath, j.AssociationScope,
    j.CallScope, j.CodeSourceText,
    COALESCE(j.TextSourceText, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.TextSourceTextGzipHex, 2)))) AS TextSourceText,
    TRY_CONVERT(VARBINARY(MAX), j.TextSourceTextGzipHex, 2) AS TextSourceTextGzip,
    j.IncidentCallSourcePath, j.InfoCallSourcePath,
    COALESCE(j.CodeSourcePathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.CodeSourcePathsJsonGzipHex, 2)))) AS CodeSourcePathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.CodeSourcePathsJsonGzipHex, 2) AS CodeSourcePathsJsonGzip,
    COALESCE(j.TextSourcePathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.TextSourcePathsJsonGzipHex, 2)))) AS TextSourcePathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.TextSourcePathsJsonGzipHex, 2) AS TextSourcePathsJsonGzip,
    COALESCE(j.SourceFieldPathsJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceFieldPathsJsonGzipHex, 2)))) AS SourceFieldPathsJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourceFieldPathsJsonGzipHex, 2) AS SourceFieldPathsJsonGzip,
    COALESCE(j.SourceRepresentationJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2)))) AS SourceRepresentationJson,
    TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2) AS SourceRepresentationJsonGzip,
    j.SemanticRole
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.attributes') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    AttributeEvidenceId BIGINT '$.attribute_evidence_id', ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal', EventOrdinal INT '$.event_ordinal', AttributeOrdinal INT '$.attribute_ordinal',
    AttributeType VARCHAR(80) '$.attribute_type', SourcePath NVARCHAR(2048) '$.source_path',
    AssociationContextPath NVARCHAR(2048) '$.association_context_path', AssociationScope VARCHAR(40) '$.association_scope',
    CallScope VARCHAR(20) '$.call_scope', CodeSourceText NVARCHAR(2000) '$.code_source_text',
    TextSourceText NVARCHAR(MAX) '$.text_source_text', TextSourceTextGzipHex NVARCHAR(MAX) '$.text_source_text_gzip_hex',
    IncidentCallSourcePath NVARCHAR(2048) '$.incident_call_source_path',
    InfoCallSourcePath NVARCHAR(2048) '$.info_call_source_path', CodeSourcePathsJson NVARCHAR(MAX) '$.code_source_paths_json',
    CodeSourcePathsJsonGzipHex NVARCHAR(MAX) '$.code_source_paths_json_gzip_hex', TextSourcePathsJson NVARCHAR(MAX) '$.text_source_paths_json',
    TextSourcePathsJsonGzipHex NVARCHAR(MAX) '$.text_source_paths_json_gzip_hex', SourceFieldPathsJson NVARCHAR(MAX) '$.source_field_paths_json',
    SourceFieldPathsJsonGzipHex NVARCHAR(MAX) '$.source_field_paths_json_gzip_hex', SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
    SourceRepresentationJsonGzipHex NVARCHAR(MAX) '$.source_representation_json_gzip_hex', SemanticRole VARCHAR(100) '$.semantic_role'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW stg.vMddMacCompactResponseError
AS
SELECT COALESCE(j.ResponseErrorId, e.ResponseErrorIdStart + CONVERT(BIGINT, a.[key])) AS ResponseErrorId,
       e.SnapshotId, j.ResultId, j.ResultOrdinal, j.ErrorFieldName, j.ErrorValue,
       j.ErrorScope, j.SourcePath, j.ResultSourcePath
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.response_errors') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    ResponseErrorId BIGINT '$.response_error_id', ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal', ErrorFieldName VARCHAR(100) '$.error_field_name',
    ErrorValue NVARCHAR(MAX) '$.value', ErrorScope VARCHAR(40) '$.error_scope',
    SourcePath NVARCHAR(2048) '$.source_path', ResultSourcePath NVARCHAR(2048) '$.result_source_path'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW stg.vMddMacCompactParserGap
AS
SELECT COALESCE(j.ParserGapId, e.ParserGapIdStart + CONVERT(BIGINT, a.[key])) AS ParserGapId,
       e.SnapshotId, j.ResultId, j.ResultOrdinal, j.EventOrdinal, j.FieldName,
       j.NormalizedFieldName, j.SourcePath, j.ValueType, j.SourceValue,
       COALESCE(j.SourceRepresentationJson, CONVERT(NVARCHAR(MAX), DECOMPRESS(TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2)))) AS SourceRepresentationJson,
       TRY_CONVERT(VARBINARY(MAX), j.SourceRepresentationJsonGzipHex, 2) AS SourceRepresentationJsonGzip,
       j.ValueExcerpt, j.MappingStatus,
       j.SemanticHint, j.EventContext, j.AssociationScope, j.CallScope
FROM stg.MddMacCompactObservationEnvelope AS e
CROSS APPLY OPENJSON(CONVERT(NVARCHAR(MAX), DECOMPRESS(e.EnvelopeJsonGzip)), '$.parser_gaps') AS a
CROSS APPLY OPENJSON(a.value)
WITH
(
    ParserGapId BIGINT '$.parser_gap_id', ResultId NVARCHAR(400) '$.result_id',
    ResultOrdinal INT '$.result_ordinal', EventOrdinal INT '$.event_ordinal', FieldName NVARCHAR(400) '$.field_name',
    NormalizedFieldName NVARCHAR(400) '$.normalized_field_name', SourcePath NVARCHAR(2048) '$.source_path',
    ValueType VARCHAR(80) '$.value_type', SourceValue NVARCHAR(2000) '$.source_value',
    SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json', SourceRepresentationJsonGzipHex NVARCHAR(MAX) '$.source_representation_json_gzip_hex',
    ValueExcerpt NVARCHAR(2000) '$.value_excerpt',
    MappingStatus VARCHAR(100) '$.mapping_status', SemanticHint VARCHAR(200) '$.semantic_hint',
    EventContext VARCHAR(80) '$.event_context', AssociationScope VARCHAR(40) '$.association_scope',
    CallScope VARCHAR(20) '$.call_scope'
) AS j
WHERE e.VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW analytics.vwMddMacCompactSourceFieldCoverage
AS
SELECT
    SnapshotId, RunId, TargetIdentity, ObservedAtUtc,
    TimingRowCount AS TimingObservationCount,
    (SELECT COUNT_BIG(*) FROM stg.vMddMacCompactTimingObservation AS t
     WHERE t.SnapshotId = e.SnapshotId
       AND t.RecordedAtTimeSourceText IS NOT NULL) AS RecordedAtTimeOccurrenceCount,
    ServiceSectionRowCount AS ServiceSectionOccurrenceCount,
    AttributeRowCount AS AttributeOccurrenceCount,
    SituationRowCount AS SituationEvidenceOccurrenceCount,
    ParserGapRowCount AS ParserGapOccurrenceCount
FROM stg.MddMacCompactObservationEnvelope AS e
WHERE VerificationStatus = 'VERIFIED';
GO

CREATE OR ALTER VIEW analytics.vwMddMacCompactRecordedAtTimeHistory
AS
WITH LatestObservation AS
(
    SELECT t.*, s.ObservedAtUtc,
           ROW_NUMBER() OVER
           (PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc DESC, t.TimingObservationId DESC) AS LatestRowNumber
    FROM stg.vMddMacCompactTimingObservation AS t
    INNER JOIN stg.vMddMacCompactSourceSnapshot AS s ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
), LastObserved AS
(
    SELECT t.SourceEventIdentity, t.TimingContext, t.RecordedAtTimeSourceText,
           t.RecordedAtTimeSourcePath, s.ObservedAtUtc, t.SnapshotId,
           t.TimingObservationId,
           ROW_NUMBER() OVER
           (PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc DESC, t.TimingObservationId DESC) AS RowNumber
    FROM stg.vMddMacCompactTimingObservation AS t
    INNER JOIN stg.vMddMacCompactSourceSnapshot AS s ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.TimingContext IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
      AND t.RecordedAtTimeSourceText IS NOT NULL
), FirstObserved AS
(
    SELECT t.SourceEventIdentity, t.TimingContext, t.RecordedAtTimeSourceText,
           t.RecordedAtTimeSourcePath, s.ObservedAtUtc, t.SnapshotId,
           t.TimingObservationId,
           ROW_NUMBER() OVER
           (PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc ASC, t.TimingObservationId ASC) AS RowNumber
    FROM stg.vMddMacCompactTimingObservation AS t
    INNER JOIN stg.vMddMacCompactSourceSnapshot AS s ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.TimingContext IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
      AND t.RecordedAtTimeSourceText IS NOT NULL
)
SELECT latest.SourceEventIdentity, latest.TimingContext,
       latest.RecordedAtTimeSourceText AS LatestSourceRecordedAtTime,
       latest.RecordedAtTimeSourcePath AS LatestSourceRecordedAtTimeSourcePath,
       latest.ObservedAtUtc AS LatestSourceRecordedAtTimeObservedAtUtc,
       latest.SnapshotId AS LatestSourceRecordedAtTimeSnapshotId,
       latest.TimingObservationId AS LatestSourceRecordedAtTimeObservationId,
       last_seen.RecordedAtTimeSourceText AS LastObservedRecordedAtTime,
       last_seen.RecordedAtTimeSourcePath AS LastObservedRecordedAtTimeSourcePath,
       last_seen.ObservedAtUtc AS LastObservedRecordedAtTimeObservedAtUtc,
       last_seen.SnapshotId AS LastObservedRecordedAtTimeSnapshotId,
       last_seen.TimingObservationId AS LastObservedRecordedAtTimeObservationId,
       first_seen.RecordedAtTimeSourceText AS FirstObservedRecordedAtTime,
       first_seen.RecordedAtTimeSourcePath AS FirstObservedRecordedAtTimeSourcePath,
       first_seen.ObservedAtUtc AS FirstObservedRecordedAtTimeObservedAtUtc,
       first_seen.SnapshotId AS FirstObservedRecordedAtTimeSnapshotId,
       first_seen.TimingObservationId AS FirstObservedRecordedAtTimeObservationId,
       (SELECT COUNT_BIG(*) FROM stg.vMddMacCompactTimingObservation AS count_rows
        WHERE count_rows.SourceEventIdentity = latest.SourceEventIdentity
          AND count_rows.TimingContext = latest.TimingContext
          AND count_rows.RecordedAtTimeSourceText IS NOT NULL) AS RecordedAtTimeNonNullObservationCount
FROM LatestObservation AS latest
LEFT JOIN LastObserved AS last_seen
  ON last_seen.SourceEventIdentity = latest.SourceEventIdentity
 AND last_seen.TimingContext = latest.TimingContext AND last_seen.RowNumber = 1
LEFT JOIN FirstObserved AS first_seen
  ON first_seen.SourceEventIdentity = latest.SourceEventIdentity
 AND first_seen.TimingContext = latest.TimingContext AND first_seen.RowNumber = 1
WHERE latest.LatestRowNumber = 1;
GO

PRINT 'COMPACT OBSERVATION STORAGE V1 INSTALLED WITH FUTURE WRITES DISABLED.';

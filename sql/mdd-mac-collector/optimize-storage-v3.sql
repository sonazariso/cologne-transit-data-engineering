/*
STORAGE V3 - FUTURE-WRITE SOURCE-EVIDENCE COMPRESSION.

This is a source-controlled migration only.  It is deliberately disabled by
default: change @ApplyStorageV3 to 1 only during a separately authorized,
collector-stopped deployment after the V1/V2 procedure drift gate and an
isolated SQL Server compression test have passed.

V3 adds nullable VARBINARY(MAX) companions for the remaining nullable MAX
source-evidence fields and installs AFTER INSERT triggers.  The triggers run
inside the existing persistence transaction, preserve NULL versus empty text,
and leave observation keys, row grain, timestamps, and the Python procedure
signature unchanged.  There is no historical backfill, index rebuild, shrink,
retention change, or production execution in this file.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @ApplyStorageV3 BIT = 0;
IF @ApplyStorageV3 <> 1
    THROW 51300, 'Storage V3 is source-only by default; explicit deployment authorization is required.', 1;

IF OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacSituationEvidence', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacServiceSectionEvidence', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacAttributeEvidence', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacParserGap', N'U') IS NULL
   OR OBJECT_ID(N'stg.uspPersistMddMacSnapshot', N'P') IS NULL
    THROW 51301, 'Storage V2 schema or persistence procedure is missing; refusing V3.', 1;

IF COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJsonGzip') IS NULL
   OR COL_LENGTH(N'stg.MddMacCallObservation', 'SourcePathsJsonGzip') IS NULL
   OR COL_LENGTH(N'stg.MddMacCallObservation', 'SourceEventJsonGzip') IS NULL
   OR COL_LENGTH(N'stg.MddMacTimingObservation', 'SourcePathsJsonGzip') IS NULL
   OR COL_LENGTH(N'stg.MddMacTimingObservation', 'SourceEventIdentityBasisJsonGzip') IS NULL
    THROW 51302, 'Storage V1/V2 columns are missing; apply and validate those releases first.', 1;

-- Keep this transaction open across the following batches.  SQL Server rolls
-- back the DDL if any later statement fails; the deployment operator must run
-- the whole file in one session.
BEGIN TRANSACTION;
GO

IF COL_LENGTH(N'stg.MddMacTimingObservation', 'SourceEventIdentityMissingFieldsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacTimingObservation
        ADD SourceEventIdentityMissingFieldsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacTimingObservation', 'SourceEventIdentityAmbiguityJsonGzip') IS NULL
    ALTER TABLE stg.MddMacTimingObservation
        ADD SourceEventIdentityAmbiguityJsonGzip VARBINARY(MAX) NULL;
GO

IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'DescriptionGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD DescriptionGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'DetailGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD DetailGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'AffectsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD AffectsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'AffectedEntityJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD AffectedEntityJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'AffectedJourneyJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD AffectedJourneyJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'AffectedLineJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD AffectedLineJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'FieldPathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD FieldPathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'AffectedEntityFieldPathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD AffectedEntityFieldPathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacSituationEvidence', 'SourceRepresentationJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSituationEvidence ADD SourceRepresentationJsonGzip VARBINARY(MAX) NULL;
GO

IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'DescriptionGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD DescriptionGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'RouteDescriptionGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD RouteDescriptionGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'ViaGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD ViaGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'ModeSourceJsonGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD ModeSourceJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'SourcePathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD SourcePathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacServiceSectionEvidence', 'SourceRepresentationJsonGzip') IS NULL
    ALTER TABLE stg.MddMacServiceSectionEvidence ADD SourceRepresentationJsonGzip VARBINARY(MAX) NULL;
GO

IF COL_LENGTH(N'stg.MddMacAttributeEvidence', 'TextSourceTextGzip') IS NULL
    ALTER TABLE stg.MddMacAttributeEvidence ADD TextSourceTextGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacAttributeEvidence', 'CodeSourcePathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacAttributeEvidence ADD CodeSourcePathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacAttributeEvidence', 'TextSourcePathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacAttributeEvidence ADD TextSourcePathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacAttributeEvidence', 'SourceFieldPathsJsonGzip') IS NULL
    ALTER TABLE stg.MddMacAttributeEvidence ADD SourceFieldPathsJsonGzip VARBINARY(MAX) NULL;
GO
IF COL_LENGTH(N'stg.MddMacAttributeEvidence', 'SourceRepresentationJsonGzip') IS NULL
    ALTER TABLE stg.MddMacAttributeEvidence ADD SourceRepresentationJsonGzip VARBINARY(MAX) NULL;
GO

IF COL_LENGTH(N'stg.MddMacParserGap', 'SourceRepresentationJsonGzip') IS NULL
    ALTER TABLE stg.MddMacParserGap ADD SourceRepresentationJsonGzip VARBINARY(MAX) NULL;
GO

CREATE OR ALTER TRIGGER stg.trg_MddMacTimingObservation_StorageV3
ON stg.MddMacTimingObservation
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE target
    SET SourceEventIdentityMissingFieldsJsonGzip =
            CASE WHEN target.SourceEventIdentityMissingFieldsJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceEventIdentityMissingFieldsJson))
                            < DATALENGTH(inserted.SourceEventIdentityMissingFieldsJson)
                 THEN COMPRESS(inserted.SourceEventIdentityMissingFieldsJson)
                 ELSE target.SourceEventIdentityMissingFieldsJsonGzip END,
        SourceEventIdentityMissingFieldsJson =
            CASE WHEN target.SourceEventIdentityMissingFieldsJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceEventIdentityMissingFieldsJson))
                            < DATALENGTH(inserted.SourceEventIdentityMissingFieldsJson)
                 THEN NULL ELSE target.SourceEventIdentityMissingFieldsJson END,
        SourceEventIdentityAmbiguityJsonGzip =
            CASE WHEN target.SourceEventIdentityAmbiguityJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceEventIdentityAmbiguityJson))
                            < DATALENGTH(inserted.SourceEventIdentityAmbiguityJson)
                 THEN COMPRESS(inserted.SourceEventIdentityAmbiguityJson)
                 ELSE target.SourceEventIdentityAmbiguityJsonGzip END,
        SourceEventIdentityAmbiguityJson =
            CASE WHEN target.SourceEventIdentityAmbiguityJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceEventIdentityAmbiguityJson))
                            < DATALENGTH(inserted.SourceEventIdentityAmbiguityJson)
                 THEN NULL ELSE target.SourceEventIdentityAmbiguityJson END
    FROM stg.MddMacTimingObservation AS target
    INNER JOIN inserted
        ON inserted.TimingObservationId = target.TimingObservationId
    WHERE target.SourceEventIdentityMissingFieldsJsonGzip IS NULL
       OR target.SourceEventIdentityAmbiguityJsonGzip IS NULL;
END;
GO

CREATE OR ALTER TRIGGER stg.trg_MddMacSituationEvidence_StorageV3
ON stg.MddMacSituationEvidence
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE target
    SET DescriptionGzip = CASE WHEN target.DescriptionGzip IS NULL
                                    AND DATALENGTH(COMPRESS(inserted.Description))
                                          < DATALENGTH(inserted.Description)
                               THEN COMPRESS(inserted.Description)
                               ELSE target.DescriptionGzip END,
        Description = CASE WHEN target.DescriptionGzip IS NULL
                                AND DATALENGTH(COMPRESS(inserted.Description))
                                      < DATALENGTH(inserted.Description)
                           THEN NULL ELSE target.Description END,
        DetailGzip = CASE WHEN target.DetailGzip IS NULL
                               AND DATALENGTH(COMPRESS(inserted.Detail))
                                     < DATALENGTH(inserted.Detail)
                          THEN COMPRESS(inserted.Detail)
                          ELSE target.DetailGzip END,
        Detail = CASE WHEN target.DetailGzip IS NULL
                           AND DATALENGTH(COMPRESS(inserted.Detail))
                                 < DATALENGTH(inserted.Detail)
                      THEN NULL ELSE target.Detail END,
        AffectsJsonGzip = CASE WHEN target.AffectsJsonGzip IS NULL
                                    AND DATALENGTH(COMPRESS(inserted.AffectsJson))
                                          < DATALENGTH(inserted.AffectsJson)
                               THEN COMPRESS(inserted.AffectsJson)
                               ELSE target.AffectsJsonGzip END,
        AffectsJson = CASE WHEN target.AffectsJsonGzip IS NULL
                                AND DATALENGTH(COMPRESS(inserted.AffectsJson))
                                      < DATALENGTH(inserted.AffectsJson)
                           THEN NULL ELSE target.AffectsJson END,
        AffectedEntityJsonGzip = CASE WHEN target.AffectedEntityJsonGzip IS NULL
                                           AND DATALENGTH(COMPRESS(inserted.AffectedEntityJson))
                                                 < DATALENGTH(inserted.AffectedEntityJson)
                                      THEN COMPRESS(inserted.AffectedEntityJson)
                                      ELSE target.AffectedEntityJsonGzip END,
        AffectedEntityJson = CASE WHEN target.AffectedEntityJsonGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.AffectedEntityJson))
                                             < DATALENGTH(inserted.AffectedEntityJson)
                                  THEN NULL ELSE target.AffectedEntityJson END,
        AffectedJourneyJsonGzip = CASE WHEN target.AffectedJourneyJsonGzip IS NULL
                                            AND DATALENGTH(COMPRESS(inserted.AffectedJourneyJson))
                                                  < DATALENGTH(inserted.AffectedJourneyJson)
                                       THEN COMPRESS(inserted.AffectedJourneyJson)
                                       ELSE target.AffectedJourneyJsonGzip END,
        AffectedJourneyJson = CASE WHEN target.AffectedJourneyJsonGzip IS NULL
                                        AND DATALENGTH(COMPRESS(inserted.AffectedJourneyJson))
                                              < DATALENGTH(inserted.AffectedJourneyJson)
                                   THEN NULL ELSE target.AffectedJourneyJson END,
        AffectedLineJsonGzip = CASE WHEN target.AffectedLineJsonGzip IS NULL
                                         AND DATALENGTH(COMPRESS(inserted.AffectedLineJson))
                                               < DATALENGTH(inserted.AffectedLineJson)
                                    THEN COMPRESS(inserted.AffectedLineJson)
                                    ELSE target.AffectedLineJsonGzip END,
        AffectedLineJson = CASE WHEN target.AffectedLineJsonGzip IS NULL
                                      AND DATALENGTH(COMPRESS(inserted.AffectedLineJson))
                                            < DATALENGTH(inserted.AffectedLineJson)
                                THEN NULL ELSE target.AffectedLineJson END,
        FieldPathsJsonGzip = CASE WHEN target.FieldPathsJsonGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.FieldPathsJson))
                                             < DATALENGTH(inserted.FieldPathsJson)
                                  THEN COMPRESS(inserted.FieldPathsJson)
                                  ELSE target.FieldPathsJsonGzip END,
        FieldPathsJson = CASE WHEN target.FieldPathsJsonGzip IS NULL
                                   AND DATALENGTH(COMPRESS(inserted.FieldPathsJson))
                                         < DATALENGTH(inserted.FieldPathsJson)
                              THEN NULL ELSE target.FieldPathsJson END,
        AffectedEntityFieldPathsJsonGzip =
            CASE WHEN target.AffectedEntityFieldPathsJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.AffectedEntityFieldPathsJson))
                            < DATALENGTH(inserted.AffectedEntityFieldPathsJson)
                 THEN COMPRESS(inserted.AffectedEntityFieldPathsJson)
                 ELSE target.AffectedEntityFieldPathsJsonGzip END,
        AffectedEntityFieldPathsJson =
            CASE WHEN target.AffectedEntityFieldPathsJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.AffectedEntityFieldPathsJson))
                            < DATALENGTH(inserted.AffectedEntityFieldPathsJson)
                 THEN NULL ELSE target.AffectedEntityFieldPathsJson END,
        SourceRepresentationJsonGzip =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN COMPRESS(inserted.SourceRepresentationJson)
                 ELSE target.SourceRepresentationJsonGzip END,
        SourceRepresentationJson =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN NULL ELSE target.SourceRepresentationJson END
    FROM stg.MddMacSituationEvidence AS target
    INNER JOIN inserted
        ON inserted.SituationEvidenceId = target.SituationEvidenceId
    WHERE target.DescriptionGzip IS NULL
       OR target.DetailGzip IS NULL
       OR target.AffectsJsonGzip IS NULL
       OR target.AffectedEntityJsonGzip IS NULL
       OR target.AffectedJourneyJsonGzip IS NULL
       OR target.AffectedLineJsonGzip IS NULL
       OR target.FieldPathsJsonGzip IS NULL
       OR target.AffectedEntityFieldPathsJsonGzip IS NULL
       OR target.SourceRepresentationJsonGzip IS NULL;
END;
GO

CREATE OR ALTER TRIGGER stg.trg_MddMacServiceSectionEvidence_StorageV3
ON stg.MddMacServiceSectionEvidence
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE target
    SET DescriptionGzip = CASE WHEN target.DescriptionGzip IS NULL
                                    AND DATALENGTH(COMPRESS(inserted.Description))
                                          < DATALENGTH(inserted.Description)
                               THEN COMPRESS(inserted.Description)
                               ELSE target.DescriptionGzip END,
        Description = CASE WHEN target.DescriptionGzip IS NULL
                                AND DATALENGTH(COMPRESS(inserted.Description))
                                      < DATALENGTH(inserted.Description)
                           THEN NULL ELSE target.Description END,
        RouteDescriptionGzip = CASE WHEN target.RouteDescriptionGzip IS NULL
                                         AND DATALENGTH(COMPRESS(inserted.RouteDescription))
                                               < DATALENGTH(inserted.RouteDescription)
                                    THEN COMPRESS(inserted.RouteDescription)
                                    ELSE target.RouteDescriptionGzip END,
        RouteDescription = CASE WHEN target.RouteDescriptionGzip IS NULL
                                      AND DATALENGTH(COMPRESS(inserted.RouteDescription))
                                            < DATALENGTH(inserted.RouteDescription)
                                THEN NULL ELSE target.RouteDescription END,
        ViaGzip = CASE WHEN target.ViaGzip IS NULL
                            AND DATALENGTH(COMPRESS(inserted.Via))
                                  < DATALENGTH(inserted.Via)
                       THEN COMPRESS(inserted.Via)
                       ELSE target.ViaGzip END,
        Via = CASE WHEN target.ViaGzip IS NULL
                        AND DATALENGTH(COMPRESS(inserted.Via))
                              < DATALENGTH(inserted.Via)
                   THEN NULL ELSE target.Via END,
        ModeSourceJsonGzip = CASE WHEN target.ModeSourceJsonGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.ModeSourceJson))
                                             < DATALENGTH(inserted.ModeSourceJson)
                                  THEN COMPRESS(inserted.ModeSourceJson)
                                  ELSE target.ModeSourceJsonGzip END,
        ModeSourceJson = CASE WHEN target.ModeSourceJsonGzip IS NULL
                                   AND DATALENGTH(COMPRESS(inserted.ModeSourceJson))
                                         < DATALENGTH(inserted.ModeSourceJson)
                              THEN NULL ELSE target.ModeSourceJson END,
        SourcePathsJsonGzip = CASE WHEN target.SourcePathsJsonGzip IS NULL
                                        AND DATALENGTH(COMPRESS(inserted.SourcePathsJson))
                                              < DATALENGTH(inserted.SourcePathsJson)
                                   THEN COMPRESS(inserted.SourcePathsJson)
                                   ELSE target.SourcePathsJsonGzip END,
        SourcePathsJson = CASE WHEN target.SourcePathsJsonGzip IS NULL
                                    AND DATALENGTH(COMPRESS(inserted.SourcePathsJson))
                                          < DATALENGTH(inserted.SourcePathsJson)
                               THEN NULL ELSE target.SourcePathsJson END,
        SourceRepresentationJsonGzip =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN COMPRESS(inserted.SourceRepresentationJson)
                 ELSE target.SourceRepresentationJsonGzip END,
        SourceRepresentationJson =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN NULL ELSE target.SourceRepresentationJson END
    FROM stg.MddMacServiceSectionEvidence AS target
    INNER JOIN inserted
        ON inserted.ServiceSectionEvidenceId = target.ServiceSectionEvidenceId
    WHERE target.DescriptionGzip IS NULL
       OR target.RouteDescriptionGzip IS NULL
       OR target.ViaGzip IS NULL
       OR target.ModeSourceJsonGzip IS NULL
       OR target.SourcePathsJsonGzip IS NULL
       OR target.SourceRepresentationJsonGzip IS NULL;
END;
GO

CREATE OR ALTER TRIGGER stg.trg_MddMacAttributeEvidence_StorageV3
ON stg.MddMacAttributeEvidence
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE target
    SET TextSourceTextGzip = CASE WHEN target.TextSourceTextGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.TextSourceText))
                                             < DATALENGTH(inserted.TextSourceText)
                                  THEN COMPRESS(inserted.TextSourceText)
                                  ELSE target.TextSourceTextGzip END,
        TextSourceText = CASE WHEN target.TextSourceTextGzip IS NULL
                                   AND DATALENGTH(COMPRESS(inserted.TextSourceText))
                                         < DATALENGTH(inserted.TextSourceText)
                              THEN NULL ELSE target.TextSourceText END,
        CodeSourcePathsJsonGzip = CASE WHEN target.CodeSourcePathsJsonGzip IS NULL
                                           AND DATALENGTH(COMPRESS(inserted.CodeSourcePathsJson))
                                                 < DATALENGTH(inserted.CodeSourcePathsJson)
                                       THEN COMPRESS(inserted.CodeSourcePathsJson)
                                       ELSE target.CodeSourcePathsJsonGzip END,
        CodeSourcePathsJson = CASE WHEN target.CodeSourcePathsJsonGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.CodeSourcePathsJson))
                                             < DATALENGTH(inserted.CodeSourcePathsJson)
                                   THEN NULL ELSE target.CodeSourcePathsJson END,
        TextSourcePathsJsonGzip = CASE WHEN target.TextSourcePathsJsonGzip IS NULL
                                           AND DATALENGTH(COMPRESS(inserted.TextSourcePathsJson))
                                                 < DATALENGTH(inserted.TextSourcePathsJson)
                                       THEN COMPRESS(inserted.TextSourcePathsJson)
                                       ELSE target.TextSourcePathsJsonGzip END,
        TextSourcePathsJson = CASE WHEN target.TextSourcePathsJsonGzip IS NULL
                                       AND DATALENGTH(COMPRESS(inserted.TextSourcePathsJson))
                                             < DATALENGTH(inserted.TextSourcePathsJson)
                                   THEN NULL ELSE target.TextSourcePathsJson END,
        SourceFieldPathsJsonGzip = CASE WHEN target.SourceFieldPathsJsonGzip IS NULL
                                             AND DATALENGTH(COMPRESS(inserted.SourceFieldPathsJson))
                                                   < DATALENGTH(inserted.SourceFieldPathsJson)
                                        THEN COMPRESS(inserted.SourceFieldPathsJson)
                                        ELSE target.SourceFieldPathsJsonGzip END,
        SourceFieldPathsJson = CASE WHEN target.SourceFieldPathsJsonGzip IS NULL
                                         AND DATALENGTH(COMPRESS(inserted.SourceFieldPathsJson))
                                               < DATALENGTH(inserted.SourceFieldPathsJson)
                                    THEN NULL ELSE target.SourceFieldPathsJson END,
        SourceRepresentationJsonGzip =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN COMPRESS(inserted.SourceRepresentationJson)
                 ELSE target.SourceRepresentationJsonGzip END,
        SourceRepresentationJson =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN NULL ELSE target.SourceRepresentationJson END
    FROM stg.MddMacAttributeEvidence AS target
    INNER JOIN inserted
        ON inserted.AttributeEvidenceId = target.AttributeEvidenceId
    WHERE target.TextSourceTextGzip IS NULL
       OR target.CodeSourcePathsJsonGzip IS NULL
       OR target.TextSourcePathsJsonGzip IS NULL
       OR target.SourceFieldPathsJsonGzip IS NULL
       OR target.SourceRepresentationJsonGzip IS NULL;
END;
GO

CREATE OR ALTER TRIGGER stg.trg_MddMacParserGap_StorageV3
ON stg.MddMacParserGap
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE target
    SET SourceRepresentationJsonGzip =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN COMPRESS(inserted.SourceRepresentationJson)
                 ELSE target.SourceRepresentationJsonGzip END,
        SourceRepresentationJson =
            CASE WHEN target.SourceRepresentationJsonGzip IS NULL
                      AND DATALENGTH(COMPRESS(inserted.SourceRepresentationJson))
                            < DATALENGTH(inserted.SourceRepresentationJson)
                 THEN NULL ELSE target.SourceRepresentationJson END
    FROM stg.MddMacParserGap AS target
    INNER JOIN inserted
        ON inserted.ParserGapId = target.ParserGapId
    WHERE target.SourceRepresentationJsonGzip IS NULL;
END;
GO

CREATE OR ALTER VIEW stg.vMddMacTimingObservationJson
AS
SELECT
    [TimingObservationId], [SnapshotId], [ResultId], [ResultOrdinal],
    [EventOrdinal], [CallScope], [CallOrdinal], [TimingContext],
    [OperatingDayRef], [JourneyRef], [VehicleJourneyRef],
    [DatedVehicleJourneyRef], [LineRef], [DirectionRef], [OperatorRef],
    [StopPointRef], [StopSeqNumber], [TimetabledTimeSourceText],
    [EstimatedTimeSourceText], [EstimatedTimeLowSourceText],
    [EstimatedTimeHighSourceText], [RecordedAtTimeSourceText],
    [TimingSourcePath], [TimetabledTimeSourcePath], [EstimatedTimeSourcePath],
    [EstimatedTimeLowSourcePath], [EstimatedTimeHighSourcePath],
    [RecordedAtTimeSourcePath], [StopPointRefSourcePath],
    [StopSeqNumberSourcePath], [SourceEventIdentity],
    [SourceEventIdentityVersion], [SourceEventIdentityStatus],
    COALESCE([SourceEventIdentityBasisJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceEventIdentityBasisJsonGzip]))) AS [SourceEventIdentityBasisJson],
    COALESCE([SourceEventIdentityMissingFieldsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceEventIdentityMissingFieldsJsonGzip]))) AS [SourceEventIdentityMissingFieldsJson],
    COALESCE([SourceEventIdentityAmbiguityJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceEventIdentityAmbiguityJsonGzip]))) AS [SourceEventIdentityAmbiguityJson],
    COALESCE([SourcePathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourcePathsJsonGzip]))) AS [SourcePathsJson]
FROM stg.MddMacTimingObservation;
GO

CREATE OR ALTER VIEW stg.vMddMacSituationEvidenceJson
AS
SELECT
    [SituationEvidenceId], [SnapshotId], [ResultId], [ResultOrdinal],
    [EventOrdinal], [EvidenceType], [SituationType], [ReferenceType],
    [ReferenceValue], [ParticipantRef], [SituationNumber],
    [VersionSourceText], [Identifier], [Summary],
    COALESCE([Description], CONVERT(NVARCHAR(MAX), DECOMPRESS([DescriptionGzip]))) AS [Description],
    COALESCE([Detail], CONVERT(NVARCHAR(MAX), DECOMPRESS([DetailGzip]))) AS [Detail],
    [CreationTimeSourceText], [ValidFromSourceText], [ValidToSourceText],
    [ProviderSource], [ProviderSourceType], [PrioritySourceText],
    COALESCE([AffectsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([AffectsJsonGzip]))) AS [AffectsJson],
    COALESCE([AffectedEntityJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([AffectedEntityJsonGzip]))) AS [AffectedEntityJson],
    COALESCE([AffectedJourneyJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([AffectedJourneyJsonGzip]))) AS [AffectedJourneyJson],
    COALESCE([AffectedLineJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([AffectedLineJsonGzip]))) AS [AffectedLineJson],
    [IdentityKey], [AssociationScope], [CallScope], [SourcePath],
    COALESCE([FieldPathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([FieldPathsJsonGzip]))) AS [FieldPathsJson],
    COALESCE([AffectedEntityFieldPathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([AffectedEntityFieldPathsJsonGzip]))) AS [AffectedEntityFieldPathsJson],
    COALESCE([SourceRepresentationJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceRepresentationJsonGzip]))) AS [SourceRepresentationJson]
FROM stg.MddMacSituationEvidence;
GO

CREATE OR ALTER VIEW stg.vMddMacServiceSectionEvidenceJson
AS
SELECT
    [ServiceSectionEvidenceId], [SnapshotId], [ResultId], [ResultOrdinal],
    [EventOrdinal], [SectionOrdinal], [SourcePath], [LineRef],
    [DirectionRef], [OperatorRef], [PtMode], [RailSubmode], [BusSubmode],
    [TramSubmode], [Name], [ShortName],
    COALESCE([Description], CONVERT(NVARCHAR(MAX), DECOMPRESS([DescriptionGzip]))) AS [Description],
    COALESCE([RouteDescription], CONVERT(NVARCHAR(MAX), DECOMPRESS([RouteDescriptionGzip]))) AS [RouteDescription],
    COALESCE([Via], CONVERT(NVARCHAR(MAX), DECOMPRESS([ViaGzip]))) AS [Via],
    [PublishedLineName],
    COALESCE([ModeSourceJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([ModeSourceJsonGzip]))) AS [ModeSourceJson],
    COALESCE([SourcePathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourcePathsJsonGzip]))) AS [SourcePathsJson],
    COALESCE([SourceRepresentationJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceRepresentationJsonGzip]))) AS [SourceRepresentationJson],
    [SemanticRole]
FROM stg.MddMacServiceSectionEvidence;
GO

CREATE OR ALTER VIEW stg.vMddMacAttributeEvidenceJson
AS
SELECT
    [AttributeEvidenceId], [SnapshotId], [ResultId], [ResultOrdinal],
    [EventOrdinal], [AttributeOrdinal], [AttributeType], [SourcePath],
    [AssociationContextPath], [AssociationScope], [CallScope],
    [CodeSourceText],
    COALESCE([TextSourceText], CONVERT(NVARCHAR(MAX), DECOMPRESS([TextSourceTextGzip]))) AS [TextSourceText],
    [IncidentCallSourcePath], [InfoCallSourcePath],
    COALESCE([CodeSourcePathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([CodeSourcePathsJsonGzip]))) AS [CodeSourcePathsJson],
    COALESCE([TextSourcePathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([TextSourcePathsJsonGzip]))) AS [TextSourcePathsJson],
    COALESCE([SourceFieldPathsJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceFieldPathsJsonGzip]))) AS [SourceFieldPathsJson],
    COALESCE([SourceRepresentationJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceRepresentationJsonGzip]))) AS [SourceRepresentationJson],
    [SemanticRole]
FROM stg.MddMacAttributeEvidence;
GO

CREATE OR ALTER VIEW stg.vMddMacParserGapJson
AS
SELECT
    [ParserGapId], [SnapshotId], [ResultId], [ResultOrdinal],
    [EventOrdinal], [FieldName], [NormalizedFieldName], [SourcePath],
    [ValueType], [SourceValue],
    COALESCE([SourceRepresentationJson], CONVERT(NVARCHAR(MAX), DECOMPRESS([SourceRepresentationJsonGzip]))) AS [SourceRepresentationJson],
    [ValueExcerpt], [MappingStatus], [SemanticHint], [EventContext],
    [AssociationScope], [CallScope]
FROM stg.MddMacParserGap;
GO

COMMIT TRANSACTION;
GO


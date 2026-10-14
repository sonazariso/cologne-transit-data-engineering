/*
STORAGE V1 - DEPLOY ON AN ISOLATED RESTORED COPY FIRST.
Scope: source snapshot GZIP without changing Python / MDD calls / normalized tables.
Prerequisite: collector STOPPED, previous backup VERIFIED and contains the
required history; the older backup does NOT include post-backup observations.
Do NOT execute on production until the procedure drift gate and available
transaction-log/disk capacity have been reviewed.
This script is additive and idempotent on the intended source schema.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', 'U') IS NULL
   OR OBJECT_ID(N'stg.uspPersistMddMacSnapshot', 'P') IS NULL
    THROW 51090, 'Missing storage objects; refusing migration.', 1;

-- Refuse deployment if the installed procedure does not match the expected
-- persistence contract (do not overwrite unknown newer procedure changes).
DECLARE @ProcedureSource NVARCHAR(MAX) = OBJECT_DEFINITION(
    OBJECT_ID(N'stg.uspPersistMddMacSnapshot', 'P'));
IF @ProcedureSource IS NULL
    OR CHARINDEX(N'@SnapshotJson', @ProcedureSource) = 0
    OR CHARINDEX(N'INSERT INTO stg.MddMacSourceSnapshot', @ProcedureSource) = 0
    OR CHARINDEX(N'PERSISTED_PARSED_SNAPSHOT', @ProcedureSource) = 0
    THROW 51091, 'Procedure signature drift; manual review required.', 1;
IF COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJson') = -1
BEGIN
    -- COL_LENGTH returns -1 for NVARCHAR(MAX); exact metadata checked below.
    DECLARE @ColumnType SYSNAME, @MaxLength SMALLINT, @IsNullable BIT;
    SELECT @ColumnType=ty.name, @MaxLength=c.max_length,
           @IsNullable=c.is_nullable
    FROM sys.columns AS c
    JOIN sys.types AS ty ON ty.user_type_id=c.user_type_id
    WHERE c.object_id=OBJECT_ID(N'stg.MddMacSourceSnapshot')
      AND c.name=N'SnapshotJson';
    IF @ColumnType <> N'nvarchar' OR @MaxLength <> -1
        THROW 51092, 'SnapshotJson must be NVARCHAR(MAX).', 1;
END
ELSE
    THROW 51092, 'SnapshotJson schema drift.', 1;
GO

IF COL_LENGTH(N'stg.MddMacSourceSnapshot', 'SnapshotJsonGzip') IS NULL
    ALTER TABLE stg.MddMacSourceSnapshot
        ADD SnapshotJsonGzip VARBINARY(MAX) NULL;
GO

-- SQL allows existing rows to remain as plain JSON; no backfill here.
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID(N'stg.MddMacSourceSnapshot')
      AND name=N'SnapshotJson' AND is_nullable=0
)
    ALTER TABLE stg.MddMacSourceSnapshot
        ALTER COLUMN SnapshotJson NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'stg.MddMacSourceSnapshot')
      AND name=N'CK_stg_MddMacSourceSnapshot_OnePayload'
)
    ALTER TABLE stg.MddMacSourceSnapshot WITH CHECK ADD CONSTRAINT
       CK_stg_MddMacSourceSnapshot_OnePayload
       CHECK ((SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL)
           OR (SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL));
GO

-- The canonical procedure below is sourced from the version-controlled
-- source-faithful persistence definition. It changes only the target payload
-- column pair; Python still sends @SnapshotJson exactly as before.
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
    SET XACT_ABORT ON;

    IF @CompletedRunStatus IS NOT NULL
    BEGIN
        IF @RunId IS NULL OR @CompletedAtUtc IS NULL OR ISJSON(@RunSummaryJson) <> 1
            THROW 51112, 'MDD Mac run finalization arguments are invalid.', 1;

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
            IF XACT_STATE() <> 0
                ROLLBACK TRANSACTION;
            THROW;
        END CATCH;
    END;

    IF ISJSON(@SnapshotJson) <> 1
        THROW 51110, 'MDD Mac snapshot JSON is invalid.', 1;
    IF ISJSON(@CallsJson) <> 1 OR ISJSON(@TimingsJson) <> 1
       OR ISJSON(@SituationEvidenceJson) <> 1 OR ISJSON(@ServiceSectionsJson) <> 1
       OR ISJSON(@AttributesJson) <> 1 OR ISJSON(@ResponseErrorsJson) <> 1
       OR ISJSON(@ParserGapsJson) <> 1
        THROW 51111, 'MDD Mac normalized evidence JSON is invalid.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;

        DECLARE @SnapshotId BIGINT;
        SELECT @SnapshotId = SnapshotId
        FROM stg.MddMacSourceSnapshot WITH (UPDLOCK, HOLDLOCK)
        WHERE AuditId = @AuditId;

        IF @SnapshotId IS NULL
        BEGIN
            INSERT INTO stg.MddMacSourceSnapshot
            (
                RunId, AuditId, TargetIdentity, SamplingTargetId,
                LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
                ParserVersion, ApplicationStatus, PayloadHashSha256,
                SnapshotJson, SnapshotJsonGzip
            )
            VALUES
            (
                @RunId, @AuditId, @TargetIdentity, @SamplingTargetId,
                @LogicalDueBucket, @ObservedAtUtc, @ResponseTimestampSourceText,
                @ParserVersion, @ApplicationStatus, @PayloadHashSha256,
                NULL, COMPRESS(@SnapshotJson)
            );
            SET @SnapshotId = CONVERT(BIGINT, SCOPE_IDENTITY());

            INSERT INTO stg.MddMacCallObservation
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, SourcePath, CallAtStopSourcePath, StopPointRef,
                StopDisplayText, StopSeqNumber, PlannedBay, EstimatedBay,
                UnplannedStop, NotServicedStop, NoBoardingAtStop,
                NoAlightingAtStop, Cancelled, Deviation, OperatingDayRef,
                JourneyRef, VehicleJourneyRef, DatedVehicleJourneyRef, LineRef,
                DirectionRef, OperatorRef, PtMode, Submode,
                OriginStopPointRef, OriginText, DestinationStopPointRef,
                DestinationText, SourcePathsJson, SourceEventJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, SourcePath, CallAtStopSourcePath, StopPointRef,
                StopDisplayText, StopSeqNumber, PlannedBay, EstimatedBay,
                UnplannedStop, NotServicedStop, NoBoardingAtStop,
                NoAlightingAtStop, Cancelled, Deviation, OperatingDayRef,
                JourneyRef, VehicleJourneyRef, DatedVehicleJourneyRef, LineRef,
                DirectionRef, OperatorRef, PtMode, Submode,
                OriginStopPointRef, OriginText, DestinationStopPointRef,
                DestinationText, SourcePathsJson, SourceEventJson
            FROM OPENJSON(@CallsJson)
            WITH
            (
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
                SourceEventJson NVARCHAR(MAX) '$.source_event_json'
            );

            INSERT INTO stg.MddMacTimingObservation
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, TimingContext, OperatingDayRef, JourneyRef,
                VehicleJourneyRef, DatedVehicleJourneyRef, LineRef, DirectionRef,
                OperatorRef, StopPointRef, StopSeqNumber,
                TimetabledTimeSourceText, EstimatedTimeSourceText,
                EstimatedTimeLowSourceText, EstimatedTimeHighSourceText,
                RecordedAtTimeSourceText, TimingSourcePath,
                TimetabledTimeSourcePath, EstimatedTimeSourcePath,
                EstimatedTimeLowSourcePath, EstimatedTimeHighSourcePath,
                RecordedAtTimeSourcePath, StopPointRefSourcePath,
                StopSeqNumberSourcePath, SourceEventIdentity,
                SourceEventIdentityVersion, SourceEventIdentityStatus,
                SourceEventIdentityBasisJson, SourceEventIdentityMissingFieldsJson,
                SourceEventIdentityAmbiguityJson, SourcePathsJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, TimingContext, OperatingDayRef, JourneyRef,
                VehicleJourneyRef, DatedVehicleJourneyRef, LineRef, DirectionRef,
                OperatorRef, StopPointRef, StopSeqNumber,
                TimetabledTimeSourceText, EstimatedTimeSourceText,
                EstimatedTimeLowSourceText, EstimatedTimeHighSourceText,
                RecordedAtTimeSourceText, TimingSourcePath,
                TimetabledTimeSourcePath, EstimatedTimeSourcePath,
                EstimatedTimeLowSourcePath, EstimatedTimeHighSourcePath,
                RecordedAtTimeSourcePath, StopPointRefSourcePath,
                StopSeqNumberSourcePath, SourceEventIdentity,
                SourceEventIdentityVersion, SourceEventIdentityStatus,
                SourceEventIdentityBasisJson, SourceEventIdentityMissingFieldsJson,
                SourceEventIdentityAmbiguityJson, SourcePathsJson
            FROM OPENJSON(@TimingsJson)
            WITH
            (
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
                SourcePathsJson NVARCHAR(MAX) '$.source_paths_json'
            );

            INSERT INTO stg.MddMacSituationEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, EvidenceType,
                SituationType, ReferenceType, ReferenceValue, ParticipantRef,
                SituationNumber, VersionSourceText, Identifier, Summary,
                Description, Detail, CreationTimeSourceText, ValidFromSourceText,
                ValidToSourceText, ProviderSource, ProviderSourceType,
                PrioritySourceText, AffectsJson, AffectedEntityJson,
                AffectedJourneyJson, AffectedLineJson, IdentityKey,
                AssociationScope, CallScope, SourcePath, FieldPathsJson,
                AffectedEntityFieldPathsJson, SourceRepresentationJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, EvidenceType,
                SituationType, ReferenceType, ReferenceValue, ParticipantRef,
                SituationNumber, VersionSourceText, Identifier, Summary,
                Description, Detail, CreationTimeSourceText, ValidFromSourceText,
                ValidToSourceText, ProviderSource, ProviderSourceType,
                PrioritySourceText, AffectsJson, AffectedEntityJson,
                AffectedJourneyJson, AffectedLineJson, IdentityKey,
                AssociationScope, CallScope, SourcePath, FieldPathsJson,
                AffectedEntityFieldPathsJson, SourceRepresentationJson
            FROM OPENJSON(@SituationEvidenceJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                EvidenceType VARCHAR(40) '$.evidence_type',
                SituationType VARCHAR(80) '$.situation_type',
                ReferenceType VARCHAR(40) '$.reference_type',
                ReferenceValue NVARCHAR(2000) '$.reference_value',
                ParticipantRef NVARCHAR(400) '$.participant_ref',
                SituationNumber NVARCHAR(500) '$.situation_number',
                VersionSourceText NVARCHAR(200) '$.version_source_text',
                Identifier NVARCHAR(500) '$.identifier',
                Summary NVARCHAR(4000) '$.summary',
                Description NVARCHAR(MAX) '$.description',
                Detail NVARCHAR(MAX) '$.detail',
                CreationTimeSourceText NVARCHAR(256) '$.creation_time_source_text',
                ValidFromSourceText NVARCHAR(256) '$.valid_from_source_text',
                ValidToSourceText NVARCHAR(256) '$.valid_to_source_text',
                ProviderSource NVARCHAR(1000) '$.source',
                ProviderSourceType NVARCHAR(1000) '$.source_type',
                PrioritySourceText NVARCHAR(200) '$.priority_source_text',
                AffectsJson NVARCHAR(MAX) '$.affects_json',
                AffectedEntityJson NVARCHAR(MAX) '$.affected_entity_json',
                AffectedJourneyJson NVARCHAR(MAX) '$.affected_journey_json',
                AffectedLineJson NVARCHAR(MAX) '$.affected_line_json',
                IdentityKey NVARCHAR(1000) '$.identity_key',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope',
                SourcePath NVARCHAR(2048) '$.source_path',
                FieldPathsJson NVARCHAR(MAX) '$.field_paths_json',
                AffectedEntityFieldPathsJson NVARCHAR(MAX) '$.affected_entity_field_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json'
            );

            INSERT INTO stg.MddMacServiceSectionEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, SectionOrdinal,
                SourcePath, LineRef, DirectionRef, OperatorRef, PtMode,
                RailSubmode, BusSubmode, TramSubmode, Name, ShortName,
                Description, RouteDescription, Via, PublishedLineName,
                ModeSourceJson, SourcePathsJson, SourceRepresentationJson,
                SemanticRole
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, SectionOrdinal,
                SourcePath, LineRef, DirectionRef, OperatorRef, PtMode,
                RailSubmode, BusSubmode, TramSubmode, Name, ShortName,
                Description, RouteDescription, Via, PublishedLineName,
                ModeSourceJson, SourcePathsJson, SourceRepresentationJson,
                SemanticRole
            FROM OPENJSON(@ServiceSectionsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                SectionOrdinal INT '$.section_ordinal',
                SourcePath NVARCHAR(2048) '$.source_path',
                LineRef NVARCHAR(400) '$.line_ref',
                DirectionRef NVARCHAR(200) '$.direction_ref',
                OperatorRef NVARCHAR(300) '$.operator_ref',
                PtMode NVARCHAR(200) '$.pt_mode',
                RailSubmode NVARCHAR(300) '$.rail_submode',
                BusSubmode NVARCHAR(300) '$.bus_submode',
                TramSubmode NVARCHAR(300) '$.tram_submode',
                Name NVARCHAR(1000) '$.name',
                ShortName NVARCHAR(1000) '$.short_name',
                Description NVARCHAR(MAX) '$.description',
                RouteDescription NVARCHAR(MAX) '$.route_description',
                Via NVARCHAR(MAX) '$.via',
                PublishedLineName NVARCHAR(1000) '$.published_line_name_text',
                ModeSourceJson NVARCHAR(MAX) '$.mode_source_representation',
                SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                SemanticRole VARCHAR(80) '$.semantic_role'
            );

            INSERT INTO stg.MddMacAttributeEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                AttributeOrdinal, AttributeType, SourcePath,
                AssociationContextPath, AssociationScope, CallScope,
                CodeSourceText, TextSourceText, IncidentCallSourcePath,
                InfoCallSourcePath, CodeSourcePathsJson, TextSourcePathsJson,
                SourceFieldPathsJson, SourceRepresentationJson, SemanticRole
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                AttributeOrdinal, AttributeType, SourcePath,
                AssociationContextPath, AssociationScope, CallScope,
                CodeSourceText, TextSourceText, IncidentCallSourcePath,
                InfoCallSourcePath, CodeSourcePathsJson, TextSourcePathsJson,
                SourceFieldPathsJson, SourceRepresentationJson, SemanticRole
            FROM OPENJSON(@AttributesJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                AttributeOrdinal INT '$.attribute_ordinal',
                AttributeType VARCHAR(80) '$.attribute_type',
                SourcePath NVARCHAR(2048) '$.source_path',
                AssociationContextPath NVARCHAR(2048) '$.association_context_path',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope',
                CodeSourceText NVARCHAR(2000) '$.code_source_text',
                TextSourceText NVARCHAR(MAX) '$.text_source_text',
                IncidentCallSourcePath NVARCHAR(2048) '$.incident_call_source_path',
                InfoCallSourcePath NVARCHAR(2048) '$.info_call_source_path',
                CodeSourcePathsJson NVARCHAR(MAX) '$.code_source_paths_json',
                TextSourcePathsJson NVARCHAR(MAX) '$.text_source_paths_json',
                SourceFieldPathsJson NVARCHAR(MAX) '$.source_field_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                SemanticRole VARCHAR(100) '$.semantic_role'
            );

            INSERT INTO stg.MddMacResponseError
            (
                SnapshotId, ResultId, ResultOrdinal, ErrorFieldName, ErrorValue,
                ErrorScope, SourcePath, ResultSourcePath
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, ErrorFieldName, ErrorValue,
                ErrorScope, SourcePath, ResultSourcePath
            FROM OPENJSON(@ResponseErrorsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                ErrorFieldName VARCHAR(100) '$.field_name',
                ErrorValue NVARCHAR(MAX) '$.value',
                ErrorScope VARCHAR(40) '$.error_scope',
                SourcePath NVARCHAR(2048) '$.source_path',
                ResultSourcePath NVARCHAR(2048) '$.result_source_path'
            );

            INSERT INTO stg.MddMacParserGap
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, FieldName,
                NormalizedFieldName, SourcePath, ValueType, SourceValue,
                SourceRepresentationJson, ValueExcerpt, MappingStatus,
                SemanticHint, EventContext, AssociationScope, CallScope
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, FieldName,
                NormalizedFieldName, SourcePath, ValueType, SourceValue,
                SourceRepresentationJson, ValueExcerpt, MappingStatus,
                SemanticHint, EventContext, AssociationScope, CallScope
            FROM OPENJSON(@ParserGapsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                FieldName NVARCHAR(400) '$.field_name',
                NormalizedFieldName NVARCHAR(400) '$.normalized_field_name',
                SourcePath NVARCHAR(2048) '$.source_path',
                ValueType VARCHAR(80) '$.value_type',
                SourceValue NVARCHAR(2000) '$.source_value',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                ValueExcerpt NVARCHAR(2000) '$.value_excerpt',
                MappingStatus VARCHAR(100) '$.mapping_status',
                SemanticHint VARCHAR(200) '$.semantic_hint',
                EventContext VARCHAR(80) '$.event_context',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope'
            );

            UPDATE ctl.MddMacTargetAudit
            SET PersistenceOutcome = 'PERSISTED_PARSED_SNAPSHOT',
                StopEventResultCount = JSON_VALUE(@SnapshotJson, '$.result_count')
            WHERE AuditId = @AuditId;
        END;

        COMMIT TRANSACTION;
        SELECT @SnapshotId AS SnapshotId;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO


-- Physical contract: exactly one payload column is populated in the base row.
-- Logical contract: this accessor reconstructs the original SnapshotJson text
-- for every row.  A compressed row has a physically NULL SnapshotJson, but a
-- non-NULL logical value; an original empty string remains empty, and is not
-- treated as NULL.  Consumers must use this view rather than silently reading
-- the physical payload column. No history is lost.
CREATE OR ALTER VIEW stg.vMddMacSourceSnapshotJson
AS
SELECT
    SnapshotId, RunId, AuditId, TargetIdentity, SamplingTargetId,
    LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
    ParserVersion, ApplicationStatus, PayloadHashSha256, CreatedAtUtc,
    COALESCE(SnapshotJson,
        CONVERT(NVARCHAR(MAX), DECOMPRESS(SnapshotJsonGzip))) AS SnapshotJson
FROM stg.MddMacSourceSnapshot;
GO

-- Post-deploy checks; these DO NOT send MDD requests or modify old rows.
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID(N'stg.MddMacSourceSnapshot')
      AND name=N'SnapshotJsonGzip')
    THROW 51093, 'GZIP column not installed.', 1;
IF OBJECT_DEFINITION(OBJECT_ID(N'stg.uspPersistMddMacSnapshot'))
    NOT LIKE N'%COMPRESS(@SnapshotJson)%'
    THROW 51094, 'Procedure compression is not installed.', 1;
PRINT 'SNAPSHOT STORAGE V1 DDL: INSTALLED - NO BACKFILL PERFORMED';

/*
    COLOGNE TRANSIT INTELLIGENCE — BOUNDED TEN-SNAPSHOT RETENTION APPLY

    DESTRUCTIVE OPERATOR SCRIPT.  It is inert by default and was not run as
    part of S156.  A separately reviewed SSMS working copy may set @Apply = 1
    only after the approval gates and evidence references below are completed.

    This is an in-place, data-only cleanup.  It preserves every existing
    schema, physical table, column, index, key, foreign key, view, function,
    procedure, and trigger.  It does not create a replacement database, take
    a backup, change recovery or autogrowth, restart a collector, or shrink a
    file.  Each delete is a small committed batch and can be resumed by
    rerunning the reviewed working copy after an interruption.

    The exact approved MDD set is locked to:
      22, 93, 185, 189, 207, 224, 225, 226, 228, 229

    The cross-layer manifest is executable.  Its retention predicates are
    used for retained-row counts, FK-closure checks, and the actual bounded
    DELETE TOP batches.  Tables not listed here are never deleted by this
    script.  A listed parent with an unlisted FK child is a hard stop.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52200, 'Wrong database. Expected CologneTransitIntelligence.', 1;
IF @@TRANCOUNT <> 0
    THROW 52201, 'An ambient transaction is present; refusing retention apply.', 1;

DECLARE @Apply BIT = 0;
DECLARE @ManifestApproved BIT = 0;
DECLARE @BackupCoverageConfirmed BIT = 0;
DECLARE @BackupVerificationConfirmed BIT = 0;
DECLARE @DependencyReviewConfirmed BIT = 0;
DECLARE @ExternalConsumerReviewConfirmed BIT = 0;
DECLARE @CapacityProjectionConfirmed BIT = 0;
DECLARE @InPlaceRetentionApproved BIT = 0;
DECLARE @CollectorsDisabledConfirmed BIT = 0;
DECLARE @SchemaInventoryReviewed BIT = 0;
DECLARE @CrossLayerRetentionManifestConfirmed BIT = 0;
DECLARE @ApprovalReference NVARCHAR(400) = N'';
DECLARE @BackupEvidenceReference NVARCHAR(400) = N'';
DECLARE @DependencyEvidenceReference NVARCHAR(400) = N'';
DECLARE @ExternalConsumerEvidenceReference NVARCHAR(400) = N'';
DECLARE @CapacityEvidenceReference NVARCHAR(400) = N'';
DECLARE @SchemaInventoryEvidenceReference NVARCHAR(400) = N'';
DECLARE @CrossLayerRetentionEvidenceReference NVARCHAR(400) = N'';
DECLARE @RequiredCFreeBytes BIGINT = CONVERT(BIGINT, 12) * 1024 * 1024 * 1024;
DECLARE @Rows BIGINT = 0;
DECLARE @TotalRows BIGINT = 0;
DECLARE @AppLockResult INT = -999;

DECLARE @Manifest TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    RunId NVARCHAR(200) NOT NULL,
    AuditId BIGINT NOT NULL,
    TargetIdentity NVARCHAR(400) NOT NULL,
    ObservedAtUtc DATETIME2(6) NOT NULL,
    OriginalWasEmpty BIT NOT NULL,
    OriginalPayloadSha256 CHAR(64) NOT NULL,
    PayloadHashSha256 CHAR(64) NOT NULL,
    TimingRows BIGINT NOT NULL,
    CallRows BIGINT NOT NULL,
    SituationRows BIGINT NOT NULL,
    ServiceSectionRows BIGINT NOT NULL,
    AttributeRows BIGINT NOT NULL,
    ResponseErrorRows BIGINT NOT NULL,
    ParserGapRows BIGINT NOT NULL
);

INSERT @Manifest
(
    SnapshotId, RunId, AuditId, TargetIdentity, ObservedAtUtc,
    OriginalWasEmpty, OriginalPayloadSha256, PayloadHashSha256,
    TimingRows, CallRows, SituationRows, ServiceSectionRows, AttributeRows,
    ResponseErrorRows, ParserGapRows
)
VALUES
    (22, N'LR_0edc9b1a56_debcd106-2fcc-47f0-99e9-53717125be6f', 2,
     N'de:05315:11110', '2026-10-01T11:30:58.440000', 0,
     'C0362E08E8B87BDA0E997B68F17960739B0078CDABA347CF3BD9D41D24FF4349',
     '4511e4f27c1e4376ee587ff15f5f4586e78bd76f2e61d116ed90d98d75633800',
     995, 526, 0, 20, 0, 0, 121),
    (93, N'127809b0-70fa-4770-9943-664942be5fc3', 95,
     N'de:05315:11201', '2026-10-07T16:25:08.673000', 0,
     'A557056A0DE9F41E23C03FFC35AE8716D4C63ECFB77D1FEB60EB0C3BAC7D8C2B',
     '6f568d9bb369cbba51aa01cfb55a9c976a548b3f651713b91ec711f3248a4b94',
     178, 96, 0, 5, 22, 0, 0),
    (185, N'177056c4-1e5a-4db8-9f8d-fa3eaabf0661', 195,
     N'de:05315:11111', '2026-10-08T12:30:29.218000', 0,
     '5381047347CF962D617B35E9E0AC303C219EB1EBE3E5F74760E6B2BBC9E696E4',
     'f82ee6b33b436f849e93d95ea435e8151dba931ed7bb21e551f0510458f66562',
     1114, 585, 0, 20, 0, 0, 0),
    (189, N'2d7b436d-dd36-433a-b24a-f9283a36ee16', 439,
     N'de:05315:11212', '2026-10-12T14:10:28.819000', 0,
     'DE20071DA027AF416FCEE97B16A70BCE95F14FDF87758A8611688E2ABE5DA46B',
     '207a298f359f68eae0e48fe6227ed3cb99584321412ad55aae023dff1aab0f85',
     1258, 653, 0, 20, 0, 0, 0),
    (207, N'77e2f199-bd93-4117-be76-f984736c2c0c', 457,
     N'de:05315:11511', '2026-10-12T14:15:24.831000', 0,
     'D50023DC66291CC3A5C9B0D916C340D06F66F9829B454B863F872C5893BDC1B0',
     'e2e561c72f0e089bfab7f1d2d74649535e65469a7387188201fe61f9c55ec036',
     1946, 1018, 0, 30, 0, 0, 0),
    (224, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 474,
     N'de:05315:11112', '2026-10-12T14:20:23.455000', 0,
     '0FBD70D0DC823DD167DC782C4F2171A6AFFAA3FCF6FD38769F4AD8E6F6C12D18',
     '9ad8037155225ebcaa469252a7b9f2f881e4f19817fa5fe3399ba230bace5c09',
     2174, 1132, 0, 30, 0, 0, 0),
    (225, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 475,
     N'de:05315:11209', '2026-10-12T14:20:29.248000', 0,
     '824BEE6A4F8F7BF8498FB45F15803742F0E04721288E93F068556822CD29B42F',
     '16c4a57133a7e556037762d5933ac7fde632f5dbe0a7a2465494d3cab7c6013b',
     1858, 974, 0, 30, 0, 0, 0),
    (226, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 476,
     N'de:05315:11211', '2026-10-12T14:20:38.705000', 0,
     'C879CC9B55C4E590D499403A4C135824D1CE4978A88F4E83026D57911A15F221',
     'e65a9776c5ab5958db9209bab4f066279a40d97530e4c92fe64c1bdc19f897bf',
     1627, 855, 0, 30, 0, 0, 0),
    (228, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 478,
     N'de:05315:11410', '2026-10-12T14:20:52.288000', 0,
     '086AA4E43901784D54ED2B4CF9BEF0EDDB71E0743585C388A92C7D8138A08A0E',
     'e9db01cd0ab36276b05cb07142d689ced456635f2d5e9bfa12f02325b2c0c150',
     1748, 915, 0, 30, 0, 0, 0),
    (229, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 479,
     N'de:05315:11411', '2026-10-12T14:20:58.068000', 0,
     'E5E248E4A5A1B06A6F40B3410B2D05B5D8BA5EAC28444E4E4D428721397BC8A6',
     '62f505f81b3db86b9d8e36c76cacdf1ab97a91a49e6f01b8ce243aa6541264c8',
     1410, 750, 0, 30, 0, 0, 0);

IF (SELECT COUNT_BIG(*) FROM @Manifest) <> 10
    THROW 52202, 'Exactly ten literal manifest rows are required.', 1;
IF EXISTS
(
    SELECT 1
    FROM @Manifest
    WHERE SnapshotId NOT IN (22, 93, 185, 189, 207, 224, 225, 226, 228, 229)
       OR LEN(OriginalPayloadSha256) <> 64
       OR OriginalPayloadSha256 LIKE '%[^0-9A-Fa-f]%'
       OR LEN(PayloadHashSha256) <> 64
       OR PayloadHashSha256 LIKE '%[^0-9A-Fa-f]%'
)
    THROW 52203, 'The literal manifest contains an invalid ID or SHA-256 value.', 1;
IF
(
    SELECT COUNT_BIG(*)
    FROM @Manifest
    WHERE SnapshotId IN (22, 93, 185, 189, 207, 224, 225, 226, 228, 229)
)
    <> 10
    THROW 52204, 'The literal manifest does not exactly match the approved ID set.', 1;

CREATE TABLE #RetainedSnapshotIds (SnapshotId BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedSnapshotIds SELECT SnapshotId FROM @Manifest;
CREATE TABLE #RetainedRunIds (RunId NVARCHAR(200) COLLATE DATABASE_DEFAULT NOT NULL PRIMARY KEY);
INSERT #RetainedRunIds SELECT DISTINCT RunId FROM @Manifest;
CREATE TABLE #RetainedAuditIds (AuditId BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedAuditIds SELECT AuditId FROM @Manifest;
CREATE TABLE #RetainedPermitIds (PermitId UNIQUEIDENTIFIER NOT NULL PRIMARY KEY);
INSERT #RetainedPermitIds (PermitId)
SELECT DISTINCT h.QuotaPermitId
FROM ctl.MddMacHttpAttempt AS h
JOIN #RetainedAuditIds AS a ON a.AuditId = h.AuditId
WHERE h.QuotaPermitId IS NOT NULL;

CREATE TABLE #RetainedGtfsTripIds (TripId NVARCHAR(510) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsTripIds VALUES
(N'10536-1025-005-36.7.72:102100-20-3_DF3BB3B8-1D8B-46B1-A405-B4B30149F393');
CREATE TABLE #RetainedGtfsStopIds (StopId NVARCHAR(200) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsStopIds VALUES
(N'de:05315:11201'), (N'de:05315:11201:7:80');
CREATE TABLE #RetainedGtfsRouteIds (RouteId NVARCHAR(200) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsRouteIds VALUES (N'de:nrw:rb25:');
CREATE TABLE #RetainedGtfsServiceIds (ServiceId NVARCHAR(100) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsServiceIds VALUES (N'50126');
CREATE TABLE #RetainedGtfsAgencyIds (AgencyId NVARCHAR(100) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsAgencyIds VALUES (N'5');
CREATE TABLE #RetainedGtfsShapeIds (ShapeId NVARCHAR(200) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsShapeIds VALUES (N'22');

CREATE TABLE #RetainedWarehouseLoadBatchIds (WarehouseLoadBatchId BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedWarehouseLoadBatchIds VALUES (3);
CREATE TABLE #RetainedGtfsLoadBatchIds (LoadBatchId BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedGtfsLoadBatchIds VALUES (2);
CREATE TABLE #RetainedDimAgencyKeys (AgencyKey INT NOT NULL PRIMARY KEY);
INSERT #RetainedDimAgencyKeys VALUES (13);
CREATE TABLE #RetainedDimDateKeys (DateKey INT NOT NULL PRIMARY KEY);
INSERT #RetainedDimDateKeys VALUES (20260905);
CREATE TABLE #RetainedDimModeKeys (ModeKey SMALLINT NOT NULL PRIMARY KEY);
INSERT #RetainedDimModeKeys VALUES (3);
CREATE TABLE #RetainedDimRouteKeys (RouteKey INT NOT NULL PRIMARY KEY);
INSERT #RetainedDimRouteKeys VALUES (2);
CREATE TABLE #RetainedDimServiceKeys (ServiceKey INT NOT NULL PRIMARY KEY);
INSERT #RetainedDimServiceKeys VALUES (892);
CREATE TABLE #RetainedDimStopKeys (StopKey INT NOT NULL PRIMARY KEY);
INSERT #RetainedDimStopKeys VALUES (8), (1017);
CREATE TABLE #RetainedScheduledTripKeys (TripKey BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedScheduledTripKeys VALUES (563);
CREATE TABLE #RetainedScheduledStopEventKeys (ScheduledStopEventKey BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedScheduledStopEventKeys VALUES (615);
CREATE TABLE #RetainedOperationalOutcomeKeys (OperationalStopOutcomeKey BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedOperationalOutcomeKeys VALUES (467);
CREATE TABLE #RetainedObservationKeys (ObservationKey BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedObservationKeys VALUES (4);
CREATE TABLE #RetainedSituationKeys (SituationObservationKey BIGINT NOT NULL PRIMARY KEY);
INSERT #RetainedSituationKeys VALUES (2);

CREATE TABLE #DataRetentionManifest
(
    ManifestId INT IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    DomainName VARCHAR(40) COLLATE DATABASE_DEFAULT NOT NULL,
    SchemaName sysname COLLATE DATABASE_DEFAULT NOT NULL,
    TableName sysname COLLATE DATABASE_DEFAULT NOT NULL,
    PhysicalTableReviewed BIT NOT NULL,
    RetentionPredicate NVARCHAR(MAX) COLLATE DATABASE_DEFAULT NOT NULL,
    ExpectedRetainedRows BIGINT NOT NULL,
    DeleteOrder INT NOT NULL,
    BatchSize INT NOT NULL,
    EvidenceReference NVARCHAR(400) COLLATE DATABASE_DEFAULT NOT NULL,
    UNIQUE (SchemaName, TableName)
);

/*
    Exact live row-selection contract captured on 2026-10-15.  The values are
    deliberately concrete: there is no TOP, date range, or guessed sample in
    this manifest.  The source-faithful MDD counts total 14,308 timing rows,
    7,504 call rows, 245 service-section rows, 22 attribute rows and 121
    parser-gap rows across the ten snapshots.
*/
INSERT #DataRetentionManifest
(
    DomainName, SchemaName, TableName, PhysicalTableReviewed,
    RetentionPredicate, ExpectedRetainedRows, DeleteOrder, BatchSize,
    EvidenceReference
)
VALUES
    ('MDD', N'stg', N'MddMacResponseError', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 0, 10, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacParserGap', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 121, 11, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacAttributeEvidence', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 22, 12, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacServiceSectionEvidence', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 245, 13, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacSituationEvidence', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 0, 14, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacCallObservation', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 7504, 15, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacTimingObservation', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 14308, 16, 500, N'live S156 readback; ten-row manifest'),
    ('MDD', N'stg', N'MddMacSourceSnapshot', 1, N'EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = x.SnapshotId)', 10, 17, 1, N'live S156 readback; ten-row manifest'),
    ('CONTROL', N'ctl', N'MddMacHttpAttempt', 1, N'EXISTS (SELECT 1 FROM #RetainedAuditIds k WHERE k.AuditId = x.AuditId)', 10, 20, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacTargetClaimEvent', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 81, 21, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacTargetClaim', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 81, 22, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacDispatcherClaimEvent', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 6, 23, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacDispatcherClaim', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 6, 24, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacQuotaAudit', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 65, 25, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacProductionLeaseEvent', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId OR k.RunId = x.PreviousRunId)', 9, 26, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacProductionLease', 1, N'1 = 0', 0, 27, 500, N'live S156 control-lineage readback; no retained lease rows'),
    ('CONTROL', N'ctl', N'MddMacTargetAudit', 1, N'EXISTS (SELECT 1 FROM #RetainedAuditIds k WHERE k.AuditId = x.AuditId)', 10, 29, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacQuotaPermit', 1, N'EXISTS (SELECT 1 FROM #RetainedPermitIds k WHERE k.PermitId = x.PermitId)', 9, 30, 500, N'live S156 control-lineage readback'),
    ('CONTROL', N'ctl', N'MddMacCollectorRun', 1, N'EXISTS (SELECT 1 FROM #RetainedRunIds k WHERE k.RunId = x.RunId)', 6, 31, 500, N'live S156 control-lineage readback'),
    ('GTFS', N'stg', N'GtfsStopTimes', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsTripIds t WHERE t.TripId = x.TripId) AND EXISTS (SELECT 1 FROM #RetainedGtfsStopIds s WHERE s.StopId = x.StopId) AND x.StopSequence = N''2''', 1, 40, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsFrequencies', 1, N'1 = 0', 0, 41, 1000, N'live S156 coherent GTFS sample; none retained'),
    ('GTFS', N'stg', N'GtfsTransfers', 1, N'1 = 0', 0, 42, 1000, N'live S156 coherent GTFS sample; none retained'),
    ('GTFS', N'stg', N'GtfsTrips', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsTripIds k WHERE k.TripId = x.TripId)', 1, 43, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsRoutes', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsRouteIds k WHERE k.RouteId = x.RouteId)', 1, 44, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsShapes', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsShapeIds k WHERE k.ShapeId = x.ShapeId) AND x.ShapePtSequence = N''0''', 1, 45, 1000, N'live S156 coherent GTFS sample; ShapePtSequence 0'),
    ('GTFS', N'stg', N'GtfsCalendarDates', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsServiceIds k WHERE k.ServiceId = x.ServiceId) AND x.ServiceDate = N''20260103'' AND x.ExceptionType = N''2''', 1, 46, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsCalendar', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsServiceIds k WHERE k.ServiceId = x.ServiceId)', 1, 47, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsStops', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsStopIds k WHERE k.StopId = x.StopId)', 2, 48, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsAgency', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsAgencyIds k WHERE k.AgencyId = x.AgencyId)', 1, 49, 1000, N'live S156 coherent GTFS sample'),
    ('GTFS', N'stg', N'GtfsFeedInfo', 1, N'1 = 1', 1, 50, 1000, N'live S156 feed-info singleton'),
    ('GTFS_CONTROL', N'ctl', N'GtfsValidationResult', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsLoadBatchIds k WHERE k.LoadBatchId = x.LoadBatchId)', 23, 55, 500, N'live S156 GTFS control readback'),
    ('GTFS_CONTROL', N'ctl', N'GtfsStagingState', 1, N'x.StagingStateId = 1 AND x.CurrentLoadBatchId = 2', 1, 56, 1, N'live S156 GTFS control readback'),
    ('WAREHOUSE', N'dw', N'FactOperationalStopOutcome', 1, N'EXISTS (SELECT 1 FROM #RetainedOperationalOutcomeKeys k WHERE k.OperationalStopOutcomeKey = x.OperationalStopOutcomeKey)', 1, 60, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'FactScheduledStopEvent', 1, N'EXISTS (SELECT 1 FROM #RetainedScheduledStopEventKeys k WHERE k.ScheduledStopEventKey = x.ScheduledStopEventKey)', 1, 61, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'FactScheduledTrip', 1, N'EXISTS (SELECT 1 FROM #RetainedScheduledTripKeys k WHERE k.TripKey = x.TripKey)', 1, 62, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'BridgeServiceDate', 1, N'EXISTS (SELECT 1 FROM #RetainedDimServiceKeys k WHERE k.ServiceKey = x.ServiceKey) AND EXISTS (SELECT 1 FROM #RetainedDimDateKeys d WHERE d.DateKey = x.DateKey) AND x.WarehouseLoadBatchId = 3', 1, 63, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimRoute', 1, N'EXISTS (SELECT 1 FROM #RetainedDimRouteKeys k WHERE k.RouteKey = x.RouteKey)', 1, 64, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimService', 1, N'EXISTS (SELECT 1 FROM #RetainedDimServiceKeys k WHERE k.ServiceKey = x.ServiceKey)', 1, 65, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimMode', 1, N'EXISTS (SELECT 1 FROM #RetainedDimModeKeys k WHERE k.ModeKey = x.ModeKey)', 1, 66, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimDate', 1, N'EXISTS (SELECT 1 FROM #RetainedDimDateKeys k WHERE k.DateKey = x.DateKey)', 1, 67, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimStop', 1, N'EXISTS (SELECT 1 FROM #RetainedDimStopKeys k WHERE k.StopKey = x.StopKey)', 2, 68, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE', N'dw', N'DimAgency', 1, N'EXISTS (SELECT 1 FROM #RetainedDimAgencyKeys k WHERE k.AgencyKey = x.AgencyKey)', 1, 69, 100, N'live S156 warehouse sample'),
    ('WAREHOUSE_CONTROL', N'ctl', N'StaticWarehouseLoadBatch', 1, N'EXISTS (SELECT 1 FROM #RetainedWarehouseLoadBatchIds k WHERE k.WarehouseLoadBatchId = x.WarehouseLoadBatchId)', 1, 70, 100, N'live S156 warehouse-lineage sample'),
    ('GTFS_CONTROL', N'ctl', N'GtfsLoadBatch', 1, N'EXISTS (SELECT 1 FROM #RetainedGtfsLoadBatchIds k WHERE k.LoadBatchId = x.LoadBatchId)', 1, 71, 100, N'live S156 GTFS control readback'),
    ('REALTIME', N'stg', N'MddRealtimeStopSituationLink', 1, N'EXISTS (SELECT 1 FROM #RetainedObservationKeys o WHERE o.ObservationKey = x.ObservationKey) AND EXISTS (SELECT 1 FROM #RetainedSituationKeys s WHERE s.SituationObservationKey = x.SituationObservationKey)', 1, 75, 100, N'live S156 realtime sample'),
    ('REALTIME', N'stg', N'MddRealtimeStopObservation', 1, N'EXISTS (SELECT 1 FROM #RetainedObservationKeys k WHERE k.ObservationKey = x.ObservationKey)', 1, 76, 100, N'live S156 realtime sample'),
    ('REALTIME', N'stg', N'MddRealtimeSituationObservation', 1, N'EXISTS (SELECT 1 FROM #RetainedSituationKeys k WHERE k.SituationObservationKey = x.SituationObservationKey)', 1, 77, 100, N'live S156 realtime sample');

IF EXISTS
(
    SELECT 1
    FROM #DataRetentionManifest AS m
    WHERE m.PhysicalTableReviewed = 0
       OR OBJECT_ID(QUOTENAME(m.SchemaName) + N'.' + QUOTENAME(m.TableName), N'U') IS NULL
)
    THROW 52205, 'The executable retention manifest names a missing or unreviewed physical table.', 1;
IF EXISTS
(
    SELECT 1
    FROM #DataRetentionManifest AS m
    WHERE m.BatchSize <= 0
       OR m.BatchSize > 1000
)
    THROW 52231, 'Every retention manifest batch size must be between 1 and 1000 rows.', 1;

/* Capture the complete current structure before any operator-authorized data change. */
CREATE TABLE #SchemaInventoryBefore
(
    SchemaName sysname NOT NULL,
    SchemaOwner sysname NULL,
    PRIMARY KEY (SchemaName)
);
INSERT #SchemaInventoryBefore
SELECT s.name, USER_NAME(s.principal_id)
FROM sys.schemas AS s
WHERE s.name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest');

CREATE TABLE #ObjectInventoryBefore
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    ObjectType CHAR(2) NOT NULL,
    TypeDescription NVARCHAR(60) NOT NULL,
    PRIMARY KEY (SchemaName, ObjectName)
);
INSERT #ObjectInventoryBefore
SELECT s.name, o.name, o.type, o.type_desc
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0;

CREATE TABLE #ColumnInventoryBefore
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    ColumnId INT NOT NULL,
    ColumnName sysname NOT NULL,
    TypeName sysname NOT NULL,
    MaxLength SMALLINT NOT NULL,
    PrecisionValue TINYINT NOT NULL,
    ScaleValue TINYINT NOT NULL,
    IsNullable BIT NOT NULL,
    IsIdentity BIT NOT NULL,
    IsComputed BIT NOT NULL,
    PRIMARY KEY (SchemaName, ObjectName, ColumnId)
);
INSERT #ColumnInventoryBefore
SELECT s.name, o.name, c.column_id, c.name, t.name, c.max_length,
       c.precision, c.scale, c.is_nullable, c.is_identity, c.is_computed
FROM sys.columns AS c
JOIN sys.objects AS o ON o.object_id = c.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE o.is_ms_shipped = 0;

CREATE TABLE #IndexInventoryBefore
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    IndexId INT NOT NULL,
    IndexName sysname NULL,
    IndexType INT NOT NULL,
    IsUnique BIT NOT NULL,
    IsPrimaryKey BIT NOT NULL,
    IsUniqueConstraint BIT NOT NULL,
    IsDisabled BIT NOT NULL,
    PRIMARY KEY (SchemaName, ObjectName, IndexId)
);
INSERT #IndexInventoryBefore
SELECT s.name, o.name, i.index_id, i.name, i.type, i.is_unique,
       i.is_primary_key, i.is_unique_constraint, i.is_disabled
FROM sys.indexes AS i
JOIN sys.objects AS o ON o.object_id = i.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0 AND i.index_id > 0 AND i.is_hypothetical = 0;

CREATE TABLE #ForeignKeyInventoryBefore
(
    ForeignKeyObjectId INT NOT NULL PRIMARY KEY,
    ForeignKeyName sysname NOT NULL,
    ChildSchema sysname NOT NULL,
    ChildTable sysname NOT NULL,
    ParentSchema sysname NOT NULL,
    ParentTable sysname NOT NULL,
    DeleteAction NVARCHAR(60) NOT NULL,
    UpdateAction NVARCHAR(60) NOT NULL,
    IsDisabled BIT NOT NULL,
    IsNotTrusted BIT NOT NULL
);
INSERT #ForeignKeyInventoryBefore
SELECT fk.object_id, fk.name,
       OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id),
       OBJECT_SCHEMA_NAME(fk.referenced_object_id), OBJECT_NAME(fk.referenced_object_id),
       fk.delete_referential_action_desc, fk.update_referential_action_desc,
       fk.is_disabled, fk.is_not_trusted
FROM sys.foreign_keys AS fk
WHERE OBJECTPROPERTYEX(fk.parent_object_id, 'IsMSShipped') = 0
   OR OBJECTPROPERTYEX(fk.referenced_object_id, 'IsMSShipped') = 0;

CREATE TABLE #ModuleHashBefore
(
    SchemaName sysname NOT NULL,
    ObjectName sysname NOT NULL,
    DefinitionSha256 CHAR(64) NULL,
    PRIMARY KEY (SchemaName, ObjectName)
);
INSERT #ModuleHashBefore
SELECT s.name, o.name,
       CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), sm.definition)), 2)
FROM sys.sql_modules AS sm
JOIN sys.objects AS o ON o.object_id = sm.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0;

/* A listed parent cannot have a hidden, unlisted FK child. */
IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys AS fk
    JOIN #DataRetentionManifest AS p
      ON p.SchemaName = OBJECT_SCHEMA_NAME(fk.referenced_object_id)
     AND p.TableName = OBJECT_NAME(fk.referenced_object_id)
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM #DataRetentionManifest AS c
        WHERE c.SchemaName = OBJECT_SCHEMA_NAME(fk.parent_object_id)
          AND c.TableName = OBJECT_NAME(fk.parent_object_id)
    )
)
    THROW 52206, 'A listed retention parent has an unlisted FK child; no delete is allowed.', 1;

/* Validate the exact ten live snapshot rows, payload representation and hashes. */
IF EXISTS
(
    SELECT 1
    FROM @Manifest AS m
    LEFT JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = m.SnapshotId
    LEFT JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
    LEFT JOIN ctl.MddMacCollectorRun AS r ON r.RunId = s.RunId
    WHERE s.SnapshotId IS NULL
       OR s.RunId <> m.RunId
       OR s.AuditId <> m.AuditId
       OR s.TargetIdentity <> m.TargetIdentity
       OR s.ObservedAtUtc <> m.ObservedAtUtc
       OR a.HttpStatus <> 200
       OR UPPER(COALESCE(r.Status, '')) NOT IN ('COMPLETE', 'SUCCEEDED')
       OR UPPER(COALESCE(a.Status, '')) NOT IN ('COMPLETE', 'SUCCEEDED')
       OR UPPER(COALESCE(a.ParserOutcome, '')) <> 'PARSED'
       OR UPPER(COALESCE(a.PersistenceOutcome, '')) <> 'PERSISTED_PARSED_SNAPSHOT'
       OR (s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NULL)
       OR (s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NOT NULL)
       OR CONVERT(VARCHAR(64), HASHBYTES
       (
           'SHA2_256',
           CASE WHEN s.SnapshotJson IS NOT NULL
                THEN CONVERT(VARBINARY(MAX), s.SnapshotJson)
                ELSE DECOMPRESS(s.SnapshotJsonGzip) END
       ), 2) <> UPPER(m.OriginalPayloadSha256)
       OR CONVERT(BIT, CASE WHEN s.SnapshotJson IS NOT NULL
                            AND DATALENGTH(s.SnapshotJson) = 0
                            THEN 1 ELSE 0 END) <> m.OriginalWasEmpty
       OR UPPER(RTRIM(s.PayloadHashSha256)) <> UPPER(m.PayloadHashSha256)
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation x WHERE x.SnapshotId = m.SnapshotId) <> m.TimingRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation x WHERE x.SnapshotId = m.SnapshotId) <> m.CallRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.SituationRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.ServiceSectionRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.AttributeRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError x WHERE x.SnapshotId = m.SnapshotId) <> m.ResponseErrorRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap x WHERE x.SnapshotId = m.SnapshotId) <> m.ParserGapRows
)
    THROW 52207, 'The literal manifest no longer matches live SQL evidence.', 1;

/* Count every executable selector before any apply. */
CREATE TABLE #ManifestCounts
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    ExpectedRetainedRows BIGINT NOT NULL,
    ActualRetainedRows BIGINT NOT NULL,
    CurrentTotalRows BIGINT NOT NULL,
    BatchSize INT NOT NULL,
    MaxNonRetainedRowBytes BIGINT NOT NULL,
    IndexCount INT NOT NULL,
    WorstCaseBatchReserveBytes BIGINT NOT NULL,
    NonRetainedRows AS (CurrentTotalRows - ActualRetainedRows),
    PRIMARY KEY (SchemaName, TableName)
);

DECLARE @CountSchema sysname;
DECLARE @CountTable sysname;
DECLARE @CountPredicate NVARCHAR(MAX);
DECLARE @ExpectedRows BIGINT;
DECLARE @ActualRows BIGINT;
DECLARE @CurrentRows BIGINT;
DECLARE @CountBatchSize INT;
DECLARE @MaxNonRetainedRowBytes BIGINT;
DECLARE @IndexCount INT;
DECLARE @BatchReserveDecimal DECIMAL(38, 0);
DECLARE @WorstCaseBatchReserveBytes BIGINT;
DECLARE @PostTotalRows BIGINT;
DECLARE @RowBytesExpression NVARCHAR(MAX);
DECLARE @Sql NVARCHAR(MAX);
DECLARE count_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT SchemaName, TableName, RetentionPredicate, ExpectedRetainedRows, BatchSize
    FROM #DataRetentionManifest
    ORDER BY DeleteOrder, ManifestId;
OPEN count_cursor;
FETCH NEXT FROM count_cursor INTO
    @CountSchema, @CountTable, @CountPredicate, @ExpectedRows, @CountBatchSize;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ActualRows = NULL;
    SET @CurrentRows = NULL;
    SET @MaxNonRetainedRowBytes = NULL;
    SET @IndexCount = NULL;
    SET @RowBytesExpression = NULL;
    SELECT @RowBytesExpression = STUFF
    (
        (
            SELECT N' + COALESCE(CONVERT(BIGINT, DATALENGTH(x.'
                + QUOTENAME(c.name) + N')), CONVERT(BIGINT, 0))'
            FROM sys.columns AS c
            WHERE c.object_id = OBJECT_ID(QUOTENAME(@CountSchema) + N'.' + QUOTENAME(@CountTable))
              AND c.is_hidden = 0
            ORDER BY c.column_id
            FOR XML PATH(N''), TYPE
        ).value(N'.', N'NVARCHAR(MAX)'), 1, 3, N''
    );
    SET @RowBytesExpression = COALESCE(@RowBytesExpression, N'CONVERT(BIGINT, 0)');
    SET @Sql = N'SELECT
            @Actual = COALESCE(SUM(CASE WHEN ' + @CountPredicate + N'
                                       THEN CONVERT(BIGINT, 1)
                                       ELSE CONVERT(BIGINT, 0) END), CONVERT(BIGINT, 0)),
            @Total = COUNT_BIG(*),
            @MaxRowBytes = COALESCE(MAX(CASE WHEN NOT (' + @CountPredicate + N')
                                             THEN ' + @RowBytesExpression + N'
                                             ELSE CONVERT(BIGINT, 0) END), CONVERT(BIGINT, 0))
        FROM ' + QUOTENAME(@CountSchema) + N'.' + QUOTENAME(@CountTable) + N' AS x;';
    EXEC sys.sp_executesql @Sql,
        N'@Actual BIGINT OUTPUT, @Total BIGINT OUTPUT, @MaxRowBytes BIGINT OUTPUT',
        @Actual = @ActualRows OUTPUT,
        @Total = @CurrentRows OUTPUT,
        @MaxRowBytes = @MaxNonRetainedRowBytes OUTPUT;
    IF @ActualRows <> @ExpectedRows
        THROW 52208, 'A cross-layer retention selector count differs from live evidence.', 1;

    SELECT @IndexCount = COUNT(*)
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(QUOTENAME(@CountSchema) + N'.' + QUOTENAME(@CountTable))
      AND index_id > 0
      AND is_hypothetical = 0;

    SET @BatchReserveDecimal =
        (
            CONVERT(DECIMAL(38, 0), COALESCE(@MaxNonRetainedRowBytes, 0))
            + CONVERT(DECIMAL(38, 0), 8192)
        )
        * CONVERT(DECIMAL(38, 0), @CountBatchSize)
        * CONVERT(DECIMAL(38, 0), COALESCE(@IndexCount, 0) + 1)
        * CONVERT(DECIMAL(38, 0), 4)
        + CONVERT(DECIMAL(38, 0), 65536);
    IF @BatchReserveDecimal > CONVERT(DECIMAL(38, 0), 9223372036854775807)
        THROW 52230, 'The measured maximum row payload cannot be represented by a bounded reserve.', 1;
    SET @WorstCaseBatchReserveBytes = CONVERT(BIGINT, @BatchReserveDecimal);
    INSERT #ManifestCounts
        (
            SchemaName, TableName, ExpectedRetainedRows, ActualRetainedRows,
            CurrentTotalRows, BatchSize, MaxNonRetainedRowBytes, IndexCount,
            WorstCaseBatchReserveBytes
        )
    VALUES
        (
            @CountSchema, @CountTable, @ExpectedRows, @ActualRows, @CurrentRows,
            @CountBatchSize, COALESCE(@MaxNonRetainedRowBytes, 0),
            COALESCE(@IndexCount, 0), @WorstCaseBatchReserveBytes
        );
    FETCH NEXT FROM count_cursor INTO
        @CountSchema, @CountTable, @CountPredicate, @ExpectedRows, @CountBatchSize;
END;
CLOSE count_cursor;
DEALLOCATE count_cursor;

/* Direct SQL Server capacity and reserve measurement. */
DECLARE @MeasuredAtUtc DATETIME2(7) = SYSUTCDATETIME();
DECLARE @CFreeBytes BIGINT;
DECLARE @LogTotalBytes BIGINT;
DECLARE @LogUsedBytes BIGINT;
DECLARE @LogFreeBytes BIGINT;
DECLARE @TempdbFreeBytes BIGINT;
DECLARE @TempdbAllocatedBytes BIGINT;
DECLARE @DataInternalFreeBytes BIGINT;
DECLARE @DatabaseAutogrowthReserveBytes BIGINT;
DECLARE @TempdbAutogrowthReserveBytes BIGINT;
DECLARE @MeasuredWorstCaseBatchReserveBytes BIGINT;

SELECT @CFreeBytes = MIN(CONVERT(BIGINT, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

SELECT
    @LogTotalBytes = total_log_size_in_bytes,
    @LogUsedBytes = used_log_space_in_bytes
FROM sys.dm_db_log_space_usage;
SET @LogFreeBytes = @LogTotalBytes - @LogUsedBytes;

SELECT
    @TempdbAllocatedBytes = SUM(CONVERT(BIGINT, df.size)) * 8192,
    @TempdbFreeBytes = SUM(CONVERT(BIGINT, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT @DataInternalFreeBytes = SUM
(
    CASE WHEN df.type = 0
         THEN (CONVERT(BIGINT, df.size) - CONVERT(BIGINT, FILEPROPERTY(df.name, N'SpaceUsed'))) * 8192
         ELSE 0 END
)
FROM sys.database_files AS df;

SELECT @DatabaseAutogrowthReserveBytes = SUM
(
    CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:' THEN 0
         WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;

SELECT @TempdbAutogrowthReserveBytes = SUM
(
    CASE WHEN df.is_percent_growth = 1
             THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
         ELSE CONVERT(BIGINT, df.growth) * 8192 END
)
FROM tempdb.sys.database_files AS df
WHERE df.type = 0;

SELECT @MeasuredWorstCaseBatchReserveBytes = COALESCE
(
    MAX(WorstCaseBatchReserveBytes),
    CONVERT(BIGINT, 0)
)
FROM #ManifestCounts;

SELECT
    N'portfolio.retention_capacity_preflight' AS statement_id,
    @MeasuredAtUtc AS measured_at_utc,
    @CFreeBytes AS windows_c_free_bytes,
    @RequiredCFreeBytes AS required_c_free_bytes,
    @CFreeBytes - @RequiredCFreeBytes AS c_free_headroom_bytes,
    @LogTotalBytes AS transaction_log_total_bytes,
    @LogUsedBytes AS transaction_log_used_bytes,
    @LogFreeBytes AS transaction_log_free_bytes,
    @TempdbAllocatedBytes AS tempdb_allocated_bytes,
    @TempdbFreeBytes AS tempdb_unallocated_bytes,
    @DataInternalFreeBytes AS mdf_internal_free_bytes,
    @DatabaseAutogrowthReserveBytes AS database_next_growth_reserve_bytes,
    @TempdbAutogrowthReserveBytes AS tempdb_next_growth_reserve_bytes,
    @MeasuredWorstCaseBatchReserveBytes AS measured_worst_case_batch_reserve_bytes,
    N'MAXIMUM_MEASURED_NONRETAINED_ROW_PAYLOAD; INDEX_AWARE_RESERVE; REMEASURE_BEFORE_EVERY_BATCH; STOP_IF_C_FREE_MINUS_BATCH_AND_NEXT_GROWTH_RESERVE_IS_BELOW_12_GIB'
        AS safety_contract;

IF @CFreeBytes IS NULL
   OR @LogFreeBytes IS NULL
   OR @TempdbFreeBytes IS NULL
   OR @CFreeBytes - @MeasuredWorstCaseBatchReserveBytes
        - COALESCE(@DatabaseAutogrowthReserveBytes, 0)
        - COALESCE(@TempdbAutogrowthReserveBytes, 0) < @RequiredCFreeBytes
    THROW 52209, 'Initial measured capacity cannot prove the 12 GiB floor for a batch.', 1;
IF @LogFreeBytes < @MeasuredWorstCaseBatchReserveBytes
    THROW 52210, 'Initial transaction-log reserve is too small for the measured batch.', 1;
IF @TempdbFreeBytes < @MeasuredWorstCaseBatchReserveBytes
    THROW 52211, 'Initial tempdb reserve is too small for the measured batch.', 1;

SELECT
    N'portfolio.retention_before_counts' AS statement_id,
    mc.SchemaName AS schema_name,
    mc.TableName AS table_name,
    mc.ExpectedRetainedRows AS expected_retained_rows,
    mc.ActualRetainedRows AS actual_retained_rows,
    mc.CurrentTotalRows AS current_total_rows,
    mc.NonRetainedRows AS rows_eligible_for_bounded_delete,
    mc.BatchSize AS reviewed_batch_size,
    mc.MaxNonRetainedRowBytes AS maximum_nonretained_row_payload_bytes,
    mc.IndexCount AS index_count_used_for_reserve,
    mc.WorstCaseBatchReserveBytes AS worst_case_batch_reserve_bytes
FROM #ManifestCounts AS mc
ORDER BY mc.SchemaName, mc.TableName;

SELECT
    N'portfolio.retention_schema_object_inventory_before' AS statement_id,
    (SELECT COUNT_BIG(*) FROM #SchemaInventoryBefore) AS user_schema_count,
    (SELECT COUNT_BIG(*) FROM #ObjectInventoryBefore WHERE ObjectType = 'U') AS physical_table_count,
    (SELECT COUNT_BIG(*) FROM #ObjectInventoryBefore WHERE ObjectType = 'V') AS view_count,
    (SELECT COUNT_BIG(*) FROM #ObjectInventoryBefore WHERE ObjectType IN ('P', 'PC')) AS procedure_count,
    (SELECT COUNT_BIG(*) FROM #ObjectInventoryBefore WHERE ObjectType IN ('FN', 'IF', 'TF', 'FS', 'FT')) AS function_count,
    (SELECT COUNT_BIG(*) FROM #ObjectInventoryBefore WHERE ObjectType = 'TR') AS trigger_count,
    (SELECT COUNT_BIG(*) FROM #IndexInventoryBefore) AS index_count,
    (SELECT COUNT_BIG(*) FROM #ForeignKeyInventoryBefore) AS foreign_key_count,
    N'ALL_SCHEMA_AND_OBJECT_DEFINITIONS_ARE_REQUIRED_TO_MATCH_AFTER_APPLY' AS contract;

IF @Apply = 0
BEGIN
    SELECT
        N'portfolio.retention_apply_gate' AS statement_id,
        N'APPLY_DISABLED' AS status,
        N'No base rows were changed. Use this result set, the exact manifest,
          and the dry-run readback for operator approval before creating a
          reviewed working copy with @Apply = 1.' AS next_action;
    RETURN;
END;

IF @ManifestApproved = 0
   OR @BackupCoverageConfirmed = 0
   OR @BackupVerificationConfirmed = 0
   OR @DependencyReviewConfirmed = 0
   OR @ExternalConsumerReviewConfirmed = 0
   OR @CapacityProjectionConfirmed = 0
   OR @InPlaceRetentionApproved = 0
   OR @CollectorsDisabledConfirmed = 0
   OR @SchemaInventoryReviewed = 0
   OR @CrossLayerRetentionManifestConfirmed = 0
   OR NULLIF(@ApprovalReference, N'') IS NULL
   OR NULLIF(@BackupEvidenceReference, N'') IS NULL
   OR NULLIF(@DependencyEvidenceReference, N'') IS NULL
   OR NULLIF(@ExternalConsumerEvidenceReference, N'') IS NULL
   OR NULLIF(@CapacityEvidenceReference, N'') IS NULL
   OR NULLIF(@SchemaInventoryEvidenceReference, N'') IS NULL
   OR NULLIF(@CrossLayerRetentionEvidenceReference, N'') IS NULL
    THROW 52212, 'All reviewed retention gates and evidence references are required.', 1;

DECLARE @CurrentSchema sysname;
DECLARE @CurrentTable sysname;
DECLARE @CurrentPredicate NVARCHAR(MAX);
DECLARE @CurrentBatchSize INT;
DECLARE @CurrentCFreeBytes BIGINT;
DECLARE @CurrentLogFreeBytes BIGINT;
DECLARE @CurrentTempdbFreeBytes BIGINT;
DECLARE @CurrentGrowthReserveBytes BIGINT;
DECLARE @CurrentTempdbGrowthReserveBytes BIGINT;
DECLARE @CurrentBatchReserveBytes BIGINT;
DECLARE @DeleteSql NVARCHAR(MAX);

BEGIN TRY
EXEC @AppLockResult = sys.sp_getapplock
    @Resource = N'CologneTransitIntelligence.portfolio.retention',
    @LockMode = N'Exclusive',
    @LockOwner = N'Session',
    @LockTimeout = 0;
IF @AppLockResult < 0
    THROW 52213, 'Another portfolio retention session holds the application lock.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52214, 'A collector run is active; retention apply is stopped.', 1;
IF EXISTS (SELECT 1 FROM ctl.MddMacProductionLease WHERE Status = N'ACTIVE')
    THROW 52215, 'An active production lease exists; retention apply is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.dm_exec_requests
    WHERE session_id <> @@SPID AND database_id = DB_ID()
      AND command <> N'AWAITING COMMAND'
)
    THROW 52216, 'Another active request exists in the target database.', 1;

DECLARE delete_cursor CURSOR LOCAL FAST_FORWARD FOR
                       SELECT SchemaName, TableName, RetentionPredicate, BatchSize
    FROM #DataRetentionManifest
    ORDER BY DeleteOrder, ManifestId;

    OPEN delete_cursor;
    FETCH NEXT FROM delete_cursor INTO
        @CurrentSchema, @CurrentTable, @CurrentPredicate, @CurrentBatchSize;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        WHILE 1 = 1
        BEGIN
            /* Every batch obtains fresh SQL Server volume/log/tempdb evidence. */
            SELECT @CurrentCFreeBytes = MIN(CONVERT(BIGINT, vs.available_bytes))
            FROM sys.database_files AS df
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
            WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';
            SELECT @CurrentLogFreeBytes = total_log_size_in_bytes - used_log_space_in_bytes
            FROM sys.dm_db_log_space_usage;
            SELECT @CurrentTempdbFreeBytes = SUM(CONVERT(BIGINT, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192
            FROM tempdb.sys.database_files AS df
            LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
            WHERE df.type = 0;
            SELECT @CurrentGrowthReserveBytes = SUM
            (
                CASE WHEN UPPER(LEFT(vs.volume_mount_point, 2)) <> N'C:' THEN 0
                     WHEN df.is_percent_growth = 1
                         THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
                     ELSE CONVERT(BIGINT, df.growth) * 8192 END
            )
            FROM sys.database_files AS df
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;
            SELECT @CurrentTempdbGrowthReserveBytes = SUM
            (
                CASE WHEN df.is_percent_growth = 1
                         THEN CONVERT(BIGINT, CEILING(CONVERT(DECIMAL(28, 8), df.size) * df.growth / 100.0)) * 8192
                     ELSE CONVERT(BIGINT, df.growth) * 8192 END
            )
            FROM tempdb.sys.database_files AS df
            WHERE df.type = 0;
            SELECT @CurrentBatchReserveBytes = WorstCaseBatchReserveBytes
            FROM #ManifestCounts
            WHERE SchemaName = @CurrentSchema
              AND TableName = @CurrentTable;

            IF @CurrentCFreeBytes IS NULL
               OR @CurrentLogFreeBytes IS NULL
               OR @CurrentTempdbFreeBytes IS NULL
               OR @CurrentBatchReserveBytes IS NULL
               OR @CurrentCFreeBytes - @CurrentBatchReserveBytes
                    - COALESCE(@CurrentGrowthReserveBytes, 0)
                    - COALESCE(@CurrentTempdbGrowthReserveBytes, 0) < @RequiredCFreeBytes
                THROW 52217, 'Measured C: headroom would cross the 12 GiB floor; no batch started.', 1;
            IF @CurrentLogFreeBytes < @CurrentBatchReserveBytes
                THROW 52218, 'Measured transaction-log reserve is too small; no batch started.', 1;
            IF @CurrentTempdbFreeBytes < @CurrentBatchReserveBytes
                THROW 52219, 'Measured tempdb reserve is too small; no batch started.', 1;

            SET @Rows = NULL;
            SET @DeleteSql = N'DELETE TOP (@BatchSize) FROM '
                + QUOTENAME(@CurrentSchema) + N'.' + QUOTENAME(@CurrentTable)
                + N' AS x WHERE NOT (' + @CurrentPredicate + N');
                SET @RowsOut = @@ROWCOUNT;';

            /* DimStop has a self-FK.  Delete leaves first so parent/child
               hierarchy rows remain valid while the bounded loop progresses. */
            IF @CurrentSchema = N'dw' AND @CurrentTable = N'DimStop'
                SET @DeleteSql = N'DELETE TOP (@BatchSize) FROM dw.DimStop AS x
                    WHERE NOT (' + @CurrentPredicate + N')
                      AND NOT EXISTS
                      (SELECT 1 FROM dw.DimStop AS child
                       WHERE child.ParentStopKey = x.StopKey
                         AND NOT (EXISTS (SELECT 1 FROM #RetainedDimStopKeys k WHERE k.StopKey = child.StopKey)));
                    SET @RowsOut = @@ROWCOUNT;';

            BEGIN TRANSACTION;
            EXEC sys.sp_executesql @DeleteSql,
                N'@BatchSize INT, @RowsOut BIGINT OUTPUT',
                @BatchSize = @CurrentBatchSize, @RowsOut = @Rows OUTPUT;
            COMMIT TRANSACTION;
            SET @TotalRows += COALESCE(@Rows, 0);
            IF COALESCE(@Rows, 0) = 0 BREAK;
        END;
        FETCH NEXT FROM delete_cursor INTO
            @CurrentSchema, @CurrentTable, @CurrentPredicate, @CurrentBatchSize;
    END;
    CLOSE delete_cursor;
    DEALLOCATE delete_cursor;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    IF CURSOR_STATUS('local', 'delete_cursor') = 1 CLOSE delete_cursor;
    IF CURSOR_STATUS('local', 'delete_cursor') > -3 DEALLOCATE delete_cursor;
    IF @AppLockResult >= 0 EXEC sys.sp_releaseapplock
        @Resource = N'CologneTransitIntelligence.portfolio.retention',
        @LockOwner = N'Session';
    THROW;
END CATCH;

/* Release the writer lock before read-only post-verification. */
IF @AppLockResult >= 0
    EXEC sys.sp_releaseapplock
        @Resource = N'CologneTransitIntelligence.portfolio.retention',
        @LockOwner = N'Session';

/* Exact post-readback for retained snapshots, hashes and child counts. */
IF (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) <> 10
    THROW 52220, 'Post-apply source snapshot count is not exactly ten.', 1;
IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    WHERE NOT EXISTS (SELECT 1 FROM #RetainedSnapshotIds k WHERE k.SnapshotId = s.SnapshotId)
)
    THROW 52221, 'A nonretained MDD snapshot remains after bounded apply.', 1;
IF EXISTS
(
    SELECT 1
    FROM @Manifest AS m
    JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = m.SnapshotId
    WHERE CONVERT(VARCHAR(64), HASHBYTES
       ('SHA2_256', CASE WHEN s.SnapshotJson IS NOT NULL
                         THEN CONVERT(VARBINARY(MAX), s.SnapshotJson)
                         ELSE DECOMPRESS(s.SnapshotJsonGzip) END), 2)
             <> UPPER(m.OriginalPayloadSha256)
       OR UPPER(RTRIM(s.PayloadHashSha256)) <> UPPER(m.PayloadHashSha256)
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation x WHERE x.SnapshotId = m.SnapshotId) <> m.TimingRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation x WHERE x.SnapshotId = m.SnapshotId) <> m.CallRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.SituationRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.ServiceSectionRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence x WHERE x.SnapshotId = m.SnapshotId) <> m.AttributeRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError x WHERE x.SnapshotId = m.SnapshotId) <> m.ResponseErrorRows
       OR (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap x WHERE x.SnapshotId = m.SnapshotId) <> m.ParserGapRows
)
    THROW 52222, 'A retained snapshot hash or child count failed post-apply verification.', 1;

/* Every manifest table must now contain exactly its approved retained total. */
CREATE TABLE #PostManifestCounts
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    ExpectedRetainedRows BIGINT NOT NULL,
    SelectorRows BIGINT NOT NULL,
    TotalRows BIGINT NOT NULL,
    PRIMARY KEY (SchemaName, TableName)
);
DECLARE post_count_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT SchemaName, TableName, RetentionPredicate, ExpectedRetainedRows
    FROM #DataRetentionManifest
    ORDER BY DeleteOrder, ManifestId;
OPEN post_count_cursor;
FETCH NEXT FROM post_count_cursor INTO @CountSchema, @CountTable, @CountPredicate, @ExpectedRows;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @ActualRows = NULL;
    SET @PostTotalRows = NULL;
    SET @Sql = N'SELECT @Actual = COALESCE(SUM(CASE WHEN '
        + @CountPredicate + N' THEN CONVERT(BIGINT, 1) ELSE CONVERT(BIGINT, 0) END), CONVERT(BIGINT, 0)),
        @Total = COUNT_BIG(*) FROM '
        + QUOTENAME(@CountSchema) + N'.' + QUOTENAME(@CountTable)
        + N' AS x;';
    EXEC sys.sp_executesql @Sql,
        N'@Actual BIGINT OUTPUT, @Total BIGINT OUTPUT',
        @Actual = @ActualRows OUTPUT,
        @Total = @PostTotalRows OUTPUT;
    IF @ActualRows <> @ExpectedRows
       OR @PostTotalRows <> @ExpectedRows
        THROW 52223, 'A manifest table retained selector or total-row count failed post-apply verification.', 1;
    INSERT #PostManifestCounts
        (SchemaName, TableName, ExpectedRetainedRows, SelectorRows, TotalRows)
    VALUES (@CountSchema, @CountTable, @ExpectedRows, @ActualRows, @PostTotalRows);
    FETCH NEXT FROM post_count_cursor INTO @CountSchema, @CountTable, @CountPredicate, @ExpectedRows;
END;
CLOSE post_count_cursor;
DEALLOCATE post_count_cursor;

SELECT
    N'portfolio.retention_after_counts' AS statement_id,
    SchemaName AS schema_name,
    TableName AS table_name,
    ExpectedRetainedRows AS approved_retained_rows,
    SelectorRows AS selector_rows,
    TotalRows AS total_rows,
    CASE WHEN SelectorRows = ExpectedRetainedRows
              AND TotalRows = ExpectedRetainedRows
         THEN N'PASS' ELSE N'FAIL' END AS status
FROM #PostManifestCounts
ORDER BY SchemaName, TableName;

/* Explicit row-level FK closure check, including composite and self-FKs. */
CREATE TABLE #ForeignKeyClosureResults
(
    ForeignKeyObjectId INT NOT NULL PRIMARY KEY,
    ForeignKeyName sysname NOT NULL,
    ViolationRows BIGINT NOT NULL
);
DECLARE @FkObjectId INT;
DECLARE @FkName sysname;
DECLARE @FkChildSchema sysname;
DECLARE @FkChildTable sysname;
DECLARE @FkParentSchema sysname;
DECLARE @FkParentTable sysname;
DECLARE @FkJoinPredicate NVARCHAR(MAX);
DECLARE @FkNotNullPredicate NVARCHAR(MAX);
DECLARE @FkViolationRows BIGINT;
DECLARE post_fk_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT fk.object_id,
           fk.name,
           OBJECT_SCHEMA_NAME(fk.parent_object_id),
           OBJECT_NAME(fk.parent_object_id),
           OBJECT_SCHEMA_NAME(fk.referenced_object_id),
           OBJECT_NAME(fk.referenced_object_id)
    FROM sys.foreign_keys AS fk
    JOIN sys.objects AS child_object
      ON child_object.object_id = fk.parent_object_id
    JOIN sys.objects AS parent_object
      ON parent_object.object_id = fk.referenced_object_id
    WHERE child_object.is_ms_shipped = 0
      AND parent_object.is_ms_shipped = 0;
OPEN post_fk_cursor;
FETCH NEXT FROM post_fk_cursor INTO
    @FkObjectId, @FkName, @FkChildSchema, @FkChildTable,
    @FkParentSchema, @FkParentTable;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @FkJoinPredicate = NULL;
    SET @FkNotNullPredicate = NULL;
    SELECT @FkJoinPredicate = STUFF
    (
        (
            SELECT N' AND p.' + QUOTENAME(parent_column.name)
                + N' = c.' + QUOTENAME(child_column.name)
            FROM sys.foreign_key_columns AS fkc
            JOIN sys.columns AS child_column
              ON child_column.object_id = fkc.parent_object_id
             AND child_column.column_id = fkc.parent_column_id
            JOIN sys.columns AS parent_column
              ON parent_column.object_id = fkc.referenced_object_id
             AND parent_column.column_id = fkc.referenced_column_id
            WHERE fkc.constraint_object_id = @FkObjectId
            ORDER BY fkc.constraint_column_id
            FOR XML PATH(N''), TYPE
        ).value(N'.', N'NVARCHAR(MAX)'), 1, 5, N''
    );
    SELECT @FkNotNullPredicate = STUFF
    (
        (
            SELECT N' AND c.' + QUOTENAME(child_column.name) + N' IS NOT NULL'
            FROM sys.foreign_key_columns AS fkc
            JOIN sys.columns AS child_column
              ON child_column.object_id = fkc.parent_object_id
             AND child_column.column_id = fkc.parent_column_id
            WHERE fkc.constraint_object_id = @FkObjectId
            ORDER BY fkc.constraint_column_id
            FOR XML PATH(N''), TYPE
        ).value(N'.', N'NVARCHAR(MAX)'), 1, 5, N''
    );
    SET @FkViolationRows = NULL;
    SET @Sql = N'SELECT @Violations = COUNT_BIG(*) FROM '
        + QUOTENAME(@FkChildSchema) + N'.' + QUOTENAME(@FkChildTable) + N' AS c
        WHERE ' + COALESCE(@FkNotNullPredicate, N'1 = 0') + N'
          AND NOT EXISTS
          (
              SELECT 1
              FROM ' + QUOTENAME(@FkParentSchema) + N'.' + QUOTENAME(@FkParentTable) + N' AS p
              WHERE ' + COALESCE(@FkJoinPredicate, N'1 = 0') + N'
          );';
    EXEC sys.sp_executesql @Sql,
        N'@Violations BIGINT OUTPUT',
        @Violations = @FkViolationRows OUTPUT;
    INSERT #ForeignKeyClosureResults
        (ForeignKeyObjectId, ForeignKeyName, ViolationRows)
    VALUES (@FkObjectId, @FkName, COALESCE(@FkViolationRows, 0));
    FETCH NEXT FROM post_fk_cursor INTO
        @FkObjectId, @FkName, @FkChildSchema, @FkChildTable,
        @FkParentSchema, @FkParentTable;
END;
CLOSE post_fk_cursor;
DEALLOCATE post_fk_cursor;

SELECT
    N'portfolio.retention_fk_row_closure' AS statement_id,
    ForeignKeyName AS foreign_key_name,
    ViolationRows AS violation_rows,
    CASE WHEN ViolationRows = 0 THEN N'PASS' ELSE N'FAIL' END AS status
FROM #ForeignKeyClosureResults
ORDER BY ForeignKeyName;
IF EXISTS (SELECT 1 FROM #ForeignKeyClosureResults WHERE ViolationRows > 0)
    THROW 52232, 'A row-level foreign-key closure check found orphaned rows.', 1;

/* Retained DimStop rows must include the complete ancestor closure and no
   unapproved descendant may remain under an approved hierarchy row. */
CREATE TABLE #RetainedDimStopHierarchy
(
    StopKey INT NOT NULL PRIMARY KEY
);
;WITH stop_hierarchy AS
(
    SELECT d.StopKey, d.ParentStopKey
    FROM dw.DimStop AS d
    JOIN #RetainedDimStopKeys AS k ON k.StopKey = d.StopKey
    UNION ALL
    SELECT parent_stop.StopKey, parent_stop.ParentStopKey
    FROM dw.DimStop AS parent_stop
    JOIN stop_hierarchy AS h ON h.ParentStopKey = parent_stop.StopKey
)
INSERT #RetainedDimStopHierarchy (StopKey)
SELECT DISTINCT StopKey
FROM stop_hierarchy
OPTION (MAXRECURSION 32767);

IF EXISTS
(
    SELECT 1
    FROM #RetainedDimStopHierarchy AS h
    WHERE NOT EXISTS
    (
        SELECT 1 FROM #RetainedDimStopKeys AS k WHERE k.StopKey = h.StopKey
    )
)
    THROW 52233, 'The retained DimStop hierarchy requires an unapproved ancestor.', 1;
IF EXISTS
(
    SELECT 1
    FROM dw.DimStop AS d
    JOIN #RetainedDimStopKeys AS k ON k.StopKey = d.StopKey
    WHERE d.ParentStopKey IS NOT NULL
      AND NOT EXISTS
      (
          SELECT 1 FROM dw.DimStop AS parent_stop
          WHERE parent_stop.StopKey = d.ParentStopKey
      )
)
    THROW 52234, 'A retained DimStop row has a missing parent.', 1;
IF EXISTS
(
    SELECT 1
    FROM dw.DimStop AS child_stop
    JOIN #RetainedDimStopKeys AS parent_key
      ON parent_key.StopKey = child_stop.ParentStopKey
    WHERE NOT EXISTS
    (
        SELECT 1 FROM #RetainedDimStopKeys AS child_key
        WHERE child_key.StopKey = child_stop.StopKey
    )
)
    THROW 52235, 'DimStop deletion is incomplete; an unapproved child remains.', 1;

/* Verify the full user schema/object/column/index/FK/module inventory. */
IF EXISTS
(
    SELECT SchemaName, SchemaOwner FROM #SchemaInventoryBefore
    EXCEPT
    SELECT name, USER_NAME(principal_id) FROM sys.schemas
    WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest')
)
OR EXISTS
(
    SELECT name, USER_NAME(principal_id) FROM sys.schemas
    WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest')
    EXCEPT SELECT SchemaName, SchemaOwner FROM #SchemaInventoryBefore
)
    THROW 52224, 'Schema inventory changed during retention apply.', 1;

IF EXISTS
(
    SELECT SchemaName, ObjectName, ObjectType, TypeDescription FROM #ObjectInventoryBefore
    EXCEPT
    SELECT s.name, o.name, o.type, o.type_desc FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id WHERE o.is_ms_shipped = 0
)
OR EXISTS
(
    SELECT s.name, o.name, o.type, o.type_desc FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id WHERE o.is_ms_shipped = 0
    EXCEPT SELECT SchemaName, ObjectName, ObjectType, TypeDescription FROM #ObjectInventoryBefore
)
    THROW 52225, 'Object inventory changed during retention apply.', 1;

IF EXISTS
(
    SELECT SchemaName, ObjectName, ColumnId, ColumnName, TypeName, MaxLength,
           PrecisionValue, ScaleValue, IsNullable, IsIdentity, IsComputed
    FROM #ColumnInventoryBefore
    EXCEPT
    SELECT s.name, o.name, c.column_id, c.name, t.name, c.max_length,
           c.precision, c.scale, c.is_nullable, c.is_identity, c.is_computed
    FROM sys.columns c
    JOIN sys.objects o ON o.object_id = c.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE o.is_ms_shipped = 0
)
OR EXISTS
(
    SELECT s.name, o.name, c.column_id, c.name, t.name, c.max_length,
           c.precision, c.scale, c.is_nullable, c.is_identity, c.is_computed
    FROM sys.columns c
    JOIN sys.objects o ON o.object_id = c.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE o.is_ms_shipped = 0
    EXCEPT SELECT SchemaName, ObjectName, ColumnId, ColumnName, TypeName, MaxLength,
                  PrecisionValue, ScaleValue, IsNullable, IsIdentity, IsComputed
           FROM #ColumnInventoryBefore
)
    THROW 52226, 'Column inventory changed during retention apply.', 1;

IF EXISTS
(
    SELECT SchemaName, ObjectName, IndexId, IndexName, IndexType, IsUnique,
           IsPrimaryKey, IsUniqueConstraint, IsDisabled FROM #IndexInventoryBefore
    EXCEPT
    SELECT s.name, o.name, i.index_id, i.name, i.type, i.is_unique,
           i.is_primary_key, i.is_unique_constraint, i.is_disabled
    FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    WHERE o.is_ms_shipped = 0 AND i.index_id > 0 AND i.is_hypothetical = 0
)
OR EXISTS
(
    SELECT s.name, o.name, i.index_id, i.name, i.type, i.is_unique,
           i.is_primary_key, i.is_unique_constraint, i.is_disabled
    FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    WHERE o.is_ms_shipped = 0 AND i.index_id > 0 AND i.is_hypothetical = 0
    EXCEPT SELECT SchemaName, ObjectName, IndexId, IndexName, IndexType, IsUnique,
                  IsPrimaryKey, IsUniqueConstraint, IsDisabled FROM #IndexInventoryBefore
)
    THROW 52227, 'Index inventory changed during retention apply.', 1;

IF EXISTS
(
    SELECT ForeignKeyName, ChildSchema, ChildTable, ParentSchema, ParentTable,
           DeleteAction, UpdateAction, IsDisabled, IsNotTrusted
    FROM #ForeignKeyInventoryBefore
    EXCEPT
    SELECT fk.name, OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id),
           OBJECT_SCHEMA_NAME(fk.referenced_object_id), OBJECT_NAME(fk.referenced_object_id),
           fk.delete_referential_action_desc, fk.update_referential_action_desc,
           fk.is_disabled, fk.is_not_trusted
    FROM sys.foreign_keys fk
    WHERE OBJECTPROPERTYEX(fk.parent_object_id, 'IsMSShipped') = 0
       OR OBJECTPROPERTYEX(fk.referenced_object_id, 'IsMSShipped') = 0
)
OR EXISTS
(
    SELECT fk.name, OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id),
           OBJECT_SCHEMA_NAME(fk.referenced_object_id), OBJECT_NAME(fk.referenced_object_id),
           fk.delete_referential_action_desc, fk.update_referential_action_desc,
           fk.is_disabled, fk.is_not_trusted
    FROM sys.foreign_keys fk
    WHERE OBJECTPROPERTYEX(fk.parent_object_id, 'IsMSShipped') = 0
       OR OBJECTPROPERTYEX(fk.referenced_object_id, 'IsMSShipped') = 0
    EXCEPT SELECT ForeignKeyName, ChildSchema, ChildTable, ParentSchema, ParentTable,
                  DeleteAction, UpdateAction, IsDisabled, IsNotTrusted
           FROM #ForeignKeyInventoryBefore
)
    THROW 52228, 'Foreign-key inventory changed during retention apply.', 1;

IF EXISTS
(
    SELECT SchemaName, ObjectName, DefinitionSha256 FROM #ModuleHashBefore
    EXCEPT
    SELECT s.name, o.name,
           CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), sm.definition)), 2)
    FROM sys.sql_modules sm JOIN sys.objects o ON o.object_id = sm.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id WHERE o.is_ms_shipped = 0
)
OR EXISTS
(
    SELECT s.name, o.name,
           CONVERT(CHAR(64), HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), sm.definition)), 2)
    FROM sys.sql_modules sm JOIN sys.objects o ON o.object_id = sm.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id WHERE o.is_ms_shipped = 0
    EXCEPT SELECT SchemaName, ObjectName, DefinitionSha256 FROM #ModuleHashBefore
)
    THROW 52229, 'SQL module definition inventory changed during retention apply.', 1;

/* DBCC CHECKCONSTRAINTS is a read-only data-integrity verification.  Capture
   its violation rows so a nonempty result can never be followed by success. */
CREATE TABLE #DbccCheckConstraints
(
    TableName NVARCHAR(4000) NULL,
    ConstraintName NVARCHAR(4000) NULL,
    WhereClause NVARCHAR(4000) NULL
);
INSERT #DbccCheckConstraints (TableName, ConstraintName, WhereClause)
EXEC (N'DBCC CHECKCONSTRAINTS WITH NO_INFOMSGS;');

SELECT
    N'portfolio.retention_dbcc_checkconstraints' AS statement_id,
    COUNT_BIG(*) AS violation_rows,
    CASE WHEN COUNT_BIG(*) = 0 THEN N'PASS' ELSE N'FAIL' END AS status
FROM #DbccCheckConstraints;
IF EXISTS (SELECT 1 FROM #DbccCheckConstraints)
    THROW 52236, 'DBCC CHECKCONSTRAINTS found violations; apply cannot succeed.', 1;

SELECT
    N'portfolio.retention_apply_result' AS statement_id,
    N'OPERATOR_APPLY_COMPLETED; VERIFY_BEFORE_SEPARATE_MDF_SHRINK' AS status,
    @TotalRows AS rows_removed_from_reviewed_manifest_tables,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) AS retained_snapshot_rows,
    N'ALL_SCHEMA_OBJECT_DEFINITIONS_PRESERVED; CROSS_LAYER_ROWS_SELECTED_BY_EXECUTABLE_MANIFEST; RERUN_IS_RESUMABLE'
        AS verification_boundary,
    N'MDF_INTERNAL_FREE_PAGES_ARE_NOT_WINDOWS_C_RECOVERY' AS physical_recovery_warning;

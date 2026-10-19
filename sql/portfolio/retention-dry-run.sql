/*
    COLOGNE TRANSIT INTELLIGENCE — TEN-SNAPSHOT RETENTION DRY RUN

    Read-only against the base database.  This script intentionally contains
    no base-table mutation.  The approved ID set is locked to
    22, 93, 185, 189, 207, 224, 225, 226, 228, 229 and the exact live
    RunId/AuditId/hash/child-count evidence is embedded below.

    This dry run inventories the complete deployed structure first, then
    reports the seven stg child families, connected ctl lineage/configuration/
    audit rows, GTFS sample contract, and warehouse dimension/fact contract.
    The cleanup method is bounded in-place.  This file is not a backup,
    replacement-database build, or proof of Windows recovery.
    It never removes a schema or object and never assumes that a view is a
    physical table.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52100, 'Wrong database. Expected CologneTransitIntelligence.', 1;

SELECT
    N'portfolio.retention_dry_run_schema_object_inventory' AS statement_id,
    sch.name AS schema_name,
    obj.name AS object_name,
    obj.object_id,
    obj.type,
    obj.type_desc,
    CASE
        WHEN obj.type = 'U' THEN N'PHYSICAL_TABLE'
        WHEN obj.type = 'V' THEN N'VIEW'
        WHEN obj.type IN ('P', 'PC') THEN N'STORED_PROCEDURE'
        WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN N'FUNCTION'
        WHEN obj.type = 'TR' THEN N'TRIGGER'
        ELSE N'OTHER_USER_OBJECT'
    END AS physical_or_logical_kind,
    CASE
        WHEN obj.type = 'U' THEN N'DATA_RETENTION_REVIEW_REQUIRED'
        ELSE N'PRESERVE_OBJECT_DEFINITION'
    END AS disposition
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0
ORDER BY sch.name, physical_or_logical_kind, obj.name;

SELECT
    N'portfolio.retention_dry_run_schema_contract' AS statement_id,
    (SELECT COUNT_BIG(*) FROM sys.schemas
     WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest'))
        AS user_schema_count,
    COUNT_BIG(CASE WHEN obj.type = 'U' THEN 1 END) AS physical_table_count,
    COUNT_BIG(CASE WHEN obj.type = 'V' THEN 1 END) AS view_count,
    COUNT_BIG(CASE WHEN obj.type IN ('P', 'PC') THEN 1 END) AS procedure_count,
    COUNT_BIG(CASE WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN 1 END)
        AS function_count,
    COUNT_BIG(CASE WHEN obj.type = 'TR' THEN 1 END) AS trigger_count,
    N'PRESERVE_ALL_EXISTING_SCHEMAS_AND_OBJECTS; DATA_ONLY_RETENTION'
        AS required_boundary
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0;

DECLARE @ManifestApproved BIT = 1;
DECLARE @SchemaInventoryReviewed BIT = 1;
DECLARE @CrossLayerManifestApproved BIT = 1;
DECLARE @GtfsManifestApproved BIT = 1;
DECLARE @WarehouseManifestApproved BIT = 1;
DECLARE @LineageManifestApproved BIT = 1;
DECLARE @OperatorEvidenceReference NVARCHAR(400) = N'S156 live read-only SQL evidence captured 2026-10-15';
DECLARE @SchemaInventoryReference NVARCHAR(400) = N'S156 live schema/object inventory captured 2026-10-15';
DECLARE @CrossLayerEvidenceReference NVARCHAR(400) = N'S156 live GTFS/warehouse/control readback captured 2026-10-15';
DECLARE @DataRetentionManifest TABLE
(
    DomainName VARCHAR(40) NOT NULL,
    SchemaName sysname NULL,
    TableName sysname NULL,
    PhysicalTableReviewed BIT NOT NULL,
    RetentionRule NVARCHAR(400) NOT NULL,
    EvidenceReference NVARCHAR(400) NOT NULL
);
DECLARE @Manifest TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    RunId NVARCHAR(100) NOT NULL,
    AuditId BIGINT NOT NULL,
    OriginalPayloadSha256 VARCHAR(64) NOT NULL,
    PayloadHashSha256 CHAR(64) NULL
);

INSERT @Manifest
    (SnapshotId, RunId, AuditId, OriginalPayloadSha256, PayloadHashSha256)
VALUES
    (22, N'LR_0edc9b1a56_debcd106-2fcc-47f0-99e9-53717125be6f', 2,
     'C0362E08E8B87BDA0E997B68F17960739B0078CDABA347CF3BD9D41D24FF4349',
     '4511e4f27c1e4376ee587ff15f5f4586e78bd76f2e61d116ed90d98d75633800'),
    (93, N'127809b0-70fa-4770-9943-664942be5fc3', 95,
     'A557056A0DE9F41E23C03FFC35AE8716D4C63ECFB77D1FEB60EB0C3BAC7D8C2B',
     '6f568d9bb369cbba51aa01cfb55a9c976a548b3f651713b91ec711f3248a4b94'),
    (185, N'177056c4-1e5a-4db8-9f8d-fa3eaabf0661', 195,
     '5381047347CF962D617B35E9E0AC303C219EB1EBE3E5F74760E6B2BBC9E696E4',
     'f82ee6b33b436f849e93d95ea435e8151dba931ed7bb21e551f0510458f66562'),
    (189, N'2d7b436d-dd36-433a-b24a-f9283a36ee16', 439,
     'DE20071DA027AF416FCEE97B16A70BCE95F14FDF87758A8611688E2ABE5DA46B',
     '207a298f359f68eae0e48fe6227ed3cb99584321412ad55aae023dff1aab0f85'),
    (207, N'77e2f199-bd93-4117-be76-f984736c2c0c', 457,
     'D50023DC66291CC3A5C9B0D916C340D06F66F9829B454B863F872C5893BDC1B0',
     'e2e561c72f0e089bfab7f1d2d74649535e65469a7387188201fe61f9c55ec036'),
    (224, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 474,
     '0FBD70D0DC823DD167DC782C4F2171A6AFFAA3FCF6FD38769F4AD8E6F6C12D18',
     '9ad8037155225ebcaa469252a7b9f2f881e4f19817fa5fe3399ba230bace5c09'),
    (225, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 475,
     '824BEE6A4F8F7BF8498FB45F15803742F0E04721288E93F068556822CD29B42F',
     '16c4a57133a7e556037762d5933ac7fde632f5dbe0a7a2465494d3cab7c6013b'),
    (226, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 476,
     'C879CC9B55C4E590D499403A4C135824D1CE4978A88F4E83026D57911A15F221',
     'e65a9776c5ab5958db9209bab4f066279a40d97530e4c92fe64c1bdc19f897bf'),
    (228, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 478,
     '086AA4E43901784D54ED2B4CF9BEF0EDDB71E0743585C388A92C7D8138A08A0E',
     'e9db01cd0ab36276b05cb07142d689ced456635f2d5e9bfa12f02325b2c0c150'),
    (229, N'f528e321-ccb4-452f-87e9-aaa37c1042ba', 479,
     'E5E248E4A5A1B06A6F40B3410B2D05B5D8BA5EAC28444E4E4D428721397BC8A6',
     '62f505f81b3db86b9d8e36c76cacdf1ab97a91a49e6f01b8ce243aa6541264c8');

INSERT @DataRetentionManifest
    (DomainName, SchemaName, TableName, PhysicalTableReviewed,
     RetentionRule, EvidenceReference)
VALUES
    ('GTFS', N'stg', N'GtfsAgency', 1, N'Keep AgencyId 5', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsCalendar', 1, N'Keep ServiceId 50126', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsCalendarDates', 1, N'Keep ServiceId 50126 / 20260103 / exception 2', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsFeedInfo', 1, N'Keep the live singleton', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsRoutes', 1, N'Keep RouteId de:nrw:rb25:', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsShapes', 1, N'Keep ShapeId 22 at ShapePtSequence 0', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsStops', 1, N'Keep the two selected stop IDs', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsStopTimes', 1, N'Keep the selected trip/stop/sequence 2 row', N'S156 live coherent GTFS readback'),
    ('GTFS', N'stg', N'GtfsTrips', 1, N'Keep the selected TripId', N'S156 live coherent GTFS readback'),
    ('GTFS_CONTROL', N'ctl', N'GtfsLoadBatch', 1, N'Keep LoadBatchId 2', N'S156 live control readback'),
    ('GTFS_CONTROL', N'ctl', N'GtfsStagingState', 1, N'Keep singleton current batch 2', N'S156 live control readback'),
    ('GTFS_CONTROL', N'ctl', N'GtfsValidationResult', 1, N'Keep all 23 rows for LoadBatchId 2', N'S156 live control readback'),
    ('WAREHOUSE', N'dw', N'FactOperationalStopOutcome', 1, N'Keep OperationalStopOutcomeKey 467', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'FactScheduledStopEvent', 1, N'Keep ScheduledStopEventKey 615', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'FactScheduledTrip', 1, N'Keep TripKey 563', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimAgency', 1, N'Keep AgencyKey 13', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimDate', 1, N'Keep DateKey 20260905', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimMode', 1, N'Keep ModeKey 3', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimRoute', 1, N'Keep RouteKey 2', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimService', 1, N'Keep ServiceKey 892', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'DimStop', 1, N'Keep StopKeys 8 and 1017', N'S156 live warehouse readback'),
    ('WAREHOUSE', N'dw', N'BridgeServiceDate', 1, N'Keep ServiceKey 892 / DateKey 20260905 / batch 3', N'S156 live warehouse readback'),
    ('WAREHOUSE_CONTROL', N'ctl', N'StaticWarehouseLoadBatch', 1, N'Keep WarehouseLoadBatchId 3', N'S156 live warehouse readback'),
    ('REALTIME', N'stg', N'MddRealtimeStopObservation', 1, N'Keep ObservationKey 4', N'S156 live realtime readback'),
    ('REALTIME', N'stg', N'MddRealtimeSituationObservation', 1, N'Keep SituationObservationKey 2', N'S156 live realtime readback'),
    ('REALTIME', N'stg', N'MddRealtimeStopSituationLink', 1, N'Keep link (4,2)', N'S156 live realtime readback'),
    ('CONTROL', N'ctl', N'MddMacCollectorRun', 1, N'Keep six exact RunIds', N'S156 live MDD control readback'),
    ('CONTROL', N'ctl', N'MddMacTargetAudit', 1, N'Keep ten exact AuditIds', N'S156 live MDD control readback'),
    ('CONTROL', N'ctl', N'MddMacHttpAttempt', 1, N'Keep attempts for the ten exact AuditIds', N'S156 live MDD control readback'),
    ('CONTROL', N'ctl', N'MddMacQuotaPermit', 1, N'Keep the nine referenced permits', N'S156 live MDD control readback'),
    ('CONTROL', N'ctl', N'MddMacQuotaAudit', 1, N'Keep 65 rows for the six exact RunIds', N'S156 live MDD control readback');

IF @ManifestApproved = 0
   OR NULLIF(@OperatorEvidenceReference, N'') IS NULL
   OR (SELECT COUNT_BIG(*) FROM @Manifest) <> 10
BEGIN
    SELECT
        N'portfolio.retention_dry_run_gate' AS statement_id,
        N'BLOCKED_MANIFEST_REVIEW_REQUIRED' AS status,
        (SELECT COUNT_BIG(*) FROM @Manifest) AS supplied_manifest_rows,
        N'Paste exactly ten literal rows and attach the reviewed SQL result.'
            AS required_action;
    RETURN;
END;

IF @SchemaInventoryReviewed = 0
   OR @CrossLayerManifestApproved = 0
   OR @GtfsManifestApproved = 0
   OR @WarehouseManifestApproved = 0
   OR @LineageManifestApproved = 0
   OR NULLIF(@SchemaInventoryReference, N'') IS NULL
   OR NULLIF(@CrossLayerEvidenceReference, N'') IS NULL
BEGIN
    SELECT
        N'portfolio.retention_cross_layer_gate' AS statement_id,
        N'BLOCKED_SCHEMA_AND_DATA_MANIFEST_REVIEW_REQUIRED' AS status,
        N'Inventory all actual objects, distinguish physical tables from views,
          preserve every schema/object definition, and attach reviewed GTFS,
          warehouse dimension/fact, and ETL lineage/configuration/audit
          retention evidence with FK closure.' AS required_action,
        N'No base rows were changed.' AS mutation_status;
    RETURN;
END;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN
          (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52106, 'A collector run is active; retention dry run is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE Status = N'ACTIVE'
)
    THROW 52107, 'An active production lease exists; retention dry run is stopped.', 1;

IF EXISTS
(
    SELECT 1
    FROM @Manifest
    WHERE SnapshotId <= 0
       OR LEN(OriginalPayloadSha256) <> 64
       OR OriginalPayloadSha256 LIKE '%[^0-9A-Fa-f]%'
)
    THROW 52101, 'Manifest contains an invalid SnapshotId or SHA-256 value.', 1;

IF
(
    SELECT COUNT_BIG(*) FROM @Manifest
) <> 10
    THROW 52102, 'The retention manifest must contain exactly ten rows.', 1;

IF
(
    SELECT COUNT_BIG(*)
    FROM @Manifest AS m
    JOIN (VALUES
        (CONVERT(bigint, 22)), (CONVERT(bigint, 93)),
        (CONVERT(bigint, 185)), (CONVERT(bigint, 189)),
        (CONVERT(bigint, 207)), (CONVERT(bigint, 224)),
        (CONVERT(bigint, 225)), (CONVERT(bigint, 226)),
        (CONVERT(bigint, 228)), (CONVERT(bigint, 229))
    ) AS a(SnapshotId) ON a.SnapshotId = m.SnapshotId
) <> 10
    THROW 52108, 'Manifest SnapshotIds do not exactly match the S154 approved ID set.', 1;

IF
(
    SELECT COUNT_BIG(DISTINCT RunId) FROM @Manifest
) = 0
    THROW 52103, 'Manifest RunId evidence is missing.', 1;

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
       OR a.HttpStatus <> 200
       OR UPPER(COALESCE(r.Status, '')) NOT IN ('COMPLETE', 'SUCCEEDED')
       OR UPPER(COALESCE(a.Status, '')) NOT IN ('COMPLETE', 'SUCCEEDED')
       OR UPPER(COALESCE(a.ParserOutcome, '')) <> 'PARSED'
       OR UPPER(COALESCE(a.PersistenceOutcome, '')) <> 'PERSISTED_PARSED_SNAPSHOT'
       OR
       (
           s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NULL
           OR s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NOT NULL
       )
       OR CONVERT(varchar(64), HASHBYTES
       (
           'SHA2_256',
           CASE WHEN s.SnapshotJson IS NOT NULL
                THEN CONVERT(varbinary(max), s.SnapshotJson)
                ELSE DECOMPRESS(s.SnapshotJsonGzip) END
       ), 2) <> UPPER(m.OriginalPayloadSha256)
       OR (m.PayloadHashSha256 IS NOT NULL
           AND s.PayloadHashSha256 <> m.PayloadHashSha256)
)
    THROW 52104, 'Manifest row does not match successful persisted SQL evidence.', 1;

IF
(
    SELECT COUNT_BIG(DISTINCT AuditId) FROM @Manifest
) <> 10
    THROW 52105, 'Each retained snapshot must have a distinct AuditId.', 1;

SELECT
    N'portfolio.retention_manifest_evidence' AS statement_id,
    m.SnapshotId,
    m.RunId,
    m.AuditId,
    s.TargetIdentity,
    s.ObservedAtUtc,
    CASE WHEN s.SnapshotJson IS NOT NULL THEN N'PLAINTEXT' ELSE N'GZIP' END
        AS payload_representation,
    CONVERT(bigint, CASE WHEN s.SnapshotJson IS NOT NULL
                         THEN DATALENGTH(s.SnapshotJson)
                         ELSE DATALENGTH(DECOMPRESS(s.SnapshotJsonGzip)) END)
        AS original_utf16_bytes,
    CONVERT(varchar(64), HASHBYTES
    (
        'SHA2_256',
        CASE WHEN s.SnapshotJson IS NOT NULL
             THEN CONVERT(varbinary(max), s.SnapshotJson)
             ELSE DECOMPRESS(s.SnapshotJsonGzip) END
    ), 2) AS original_payload_sha256,
    t.TimingRows,
    c.CallRows,
    sit.SituationRows,
    sec.ServiceSectionRows,
    att.AttributeRows,
    err.ResponseErrorRows,
    gap.ParserGapRows
FROM @Manifest AS m
JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = m.SnapshotId
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS TimingRows
    FROM stg.MddMacTimingObservation AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS t
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS CallRows
    FROM stg.MddMacCallObservation AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS c
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS SituationRows
    FROM stg.MddMacSituationEvidence AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS sit
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ServiceSectionRows
    FROM stg.MddMacServiceSectionEvidence AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS sec
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS AttributeRows
    FROM stg.MddMacAttributeEvidence AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS att
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ResponseErrorRows
    FROM stg.MddMacResponseError AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS err
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS ParserGapRows
    FROM stg.MddMacParserGap AS x
    WHERE x.SnapshotId = m.SnapshotId
) AS gap
ORDER BY m.SnapshotId;

SELECT
    N'portfolio.retention_family_counts' AS statement_id,
    v.family_name,
    v.retained_rows,
    v.nonretained_rows,
    v.total_rows,
    CASE WHEN v.retained_rows + v.nonretained_rows = v.total_rows
         THEN N'PASS' ELSE N'BLOCKED' END AS count_reconciliation
FROM
(
    SELECT N'stg.MddMacTimingObservation' AS family_name,
           (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId) AS retained_rows,
           (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId))
                AS nonretained_rows,
           (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation) AS total_rows
    UNION ALL
    SELECT N'stg.MddMacCallObservation',
           (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation)
    UNION ALL
    SELECT N'stg.MddMacSituationEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence)
    UNION ALL
    SELECT N'stg.MddMacServiceSectionEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence)
    UNION ALL
    SELECT N'stg.MddMacAttributeEvidence',
           (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence)
    UNION ALL
    SELECT N'stg.MddMacResponseError',
           (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacResponseError)
    UNION ALL
    SELECT N'stg.MddMacParserGap',
           (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap)
    UNION ALL
    SELECT N'stg.MddMacSourceSnapshot',
           (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot AS x
            JOIN @Manifest AS m ON m.SnapshotId = x.SnapshotId),
           (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot AS x
            WHERE NOT EXISTS
                (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId)),
           (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot)
) AS v(family_name, retained_rows, nonretained_rows, total_rows)
ORDER BY v.family_name;

SELECT
    N'portfolio.retention_control_lineage' AS statement_id,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun AS r
     JOIN @Manifest AS m ON m.RunId = r.RunId) AS retained_run_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacTargetAudit AS a
     JOIN @Manifest AS m ON m.AuditId = a.AuditId) AS retained_target_audit_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacHttpAttempt AS h
     JOIN @Manifest AS m ON m.AuditId = h.AuditId) AS retained_http_attempt_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacQuotaPermit AS q
     WHERE EXISTS
     (
         SELECT 1
         FROM ctl.MddMacHttpAttempt AS h
         JOIN @Manifest AS m ON m.AuditId = h.AuditId
         WHERE h.QuotaPermitId = q.PermitId
     )) AS retained_quota_permit_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacDispatcherClaim AS x
     JOIN @Manifest AS m ON m.RunId = x.RunId) AS retained_dispatcher_claim_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacDispatcherClaimEvent AS x
     JOIN @Manifest AS m ON m.RunId = x.RunId) AS retained_dispatcher_claim_event_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacTargetClaim AS x
     JOIN @Manifest AS m ON m.RunId = x.RunId) AS retained_target_claim_rows,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacTargetClaimEvent AS x
     JOIN @Manifest AS m ON m.RunId = x.RunId) AS retained_target_claim_event_rows;

SELECT
    N'portfolio.retention_cross_layer_manifest' AS statement_id,
    DomainName AS domain_name,
    SchemaName AS schema_name,
    TableName AS table_name,
    PhysicalTableReviewed AS physical_table_reviewed,
    RetentionRule AS retention_rule,
    EvidenceReference AS evidence_reference,
    N'RETAIN_DATA_ONLY; PRESERVE_TABLE_AND_ALL_OBJECT_DEFINITIONS'
        AS schema_disposition
FROM @DataRetentionManifest
ORDER BY DomainName, SchemaName, TableName;

SELECT
    N'portfolio.retention_cross_layer_selected_counts' AS statement_id,
    v.table_name,
    v.expected_rows,
    v.actual_rows,
    CASE WHEN v.expected_rows = v.actual_rows THEN N'PASS' ELSE N'BLOCKED' END AS status
FROM
(
    SELECT N'stg.GtfsAgency' AS table_name, CONVERT(bigint, 1) AS expected_rows,
           (SELECT COUNT_BIG(*) FROM stg.GtfsAgency WHERE AgencyId = N'5') AS actual_rows
    UNION ALL SELECT N'stg.GtfsCalendar', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsCalendar WHERE ServiceId = N'50126')
    UNION ALL SELECT N'stg.GtfsCalendarDates', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsCalendarDates WHERE ServiceId = N'50126' AND ServiceDate = N'20260103' AND ExceptionType = N'2')
    UNION ALL SELECT N'stg.GtfsFeedInfo', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsFeedInfo)
    UNION ALL SELECT N'stg.GtfsRoutes', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsRoutes WHERE RouteId = N'de:nrw:rb25:')
    UNION ALL SELECT N'stg.GtfsShapes', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsShapes WHERE ShapeId = N'22' AND ShapePtSequence = N'0')
    UNION ALL SELECT N'stg.GtfsStops', 2,
           (SELECT COUNT_BIG(*) FROM stg.GtfsStops WHERE StopId IN (N'de:05315:11201', N'de:05315:11201:7:80'))
    UNION ALL SELECT N'stg.GtfsStopTimes', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsStopTimes WHERE TripId = N'10536-1025-005-36.7.72:102100-20-3_DF3BB3B8-1D8B-46B1-A405-B4B30149F393' AND StopId IN (N'de:05315:11201', N'de:05315:11201:7:80') AND StopSequence = N'2')
    UNION ALL SELECT N'stg.GtfsTrips', 1,
           (SELECT COUNT_BIG(*) FROM stg.GtfsTrips WHERE TripId = N'10536-1025-005-36.7.72:102100-20-3_DF3BB3B8-1D8B-46B1-A405-B4B30149F393')
    UNION ALL SELECT N'ctl.GtfsLoadBatch', 1,
           (SELECT COUNT_BIG(*) FROM ctl.GtfsLoadBatch WHERE LoadBatchId = 2)
    UNION ALL SELECT N'ctl.GtfsStagingState', 1,
           (SELECT COUNT_BIG(*) FROM ctl.GtfsStagingState WHERE StagingStateId = 1 AND CurrentLoadBatchId = 2)
    UNION ALL SELECT N'ctl.GtfsValidationResult', 23,
           (SELECT COUNT_BIG(*) FROM ctl.GtfsValidationResult WHERE LoadBatchId = 2)
    UNION ALL SELECT N'ctl.StaticWarehouseLoadBatch', 1,
           (SELECT COUNT_BIG(*) FROM ctl.StaticWarehouseLoadBatch WHERE WarehouseLoadBatchId = 3)
    UNION ALL SELECT N'dw.FactOperationalStopOutcome', 1,
           (SELECT COUNT_BIG(*) FROM dw.FactOperationalStopOutcome WHERE OperationalStopOutcomeKey = 467)
    UNION ALL SELECT N'dw.FactScheduledStopEvent', 1,
           (SELECT COUNT_BIG(*) FROM dw.FactScheduledStopEvent WHERE ScheduledStopEventKey = 615)
    UNION ALL SELECT N'dw.FactScheduledTrip', 1,
           (SELECT COUNT_BIG(*) FROM dw.FactScheduledTrip WHERE TripKey = 563)
    UNION ALL SELECT N'dw.BridgeServiceDate', 1,
           (SELECT COUNT_BIG(*) FROM dw.BridgeServiceDate WHERE ServiceKey = 892 AND DateKey = 20260905 AND WarehouseLoadBatchId = 3)
    UNION ALL SELECT N'dw.DimAgency', 1,
           (SELECT COUNT_BIG(*) FROM dw.DimAgency WHERE AgencyKey = 13)
    UNION ALL SELECT N'dw.DimDate', 1,
           (SELECT COUNT_BIG(*) FROM dw.DimDate WHERE DateKey = 20260905)
    UNION ALL SELECT N'dw.DimMode', 1,
           (SELECT COUNT_BIG(*) FROM dw.DimMode WHERE ModeKey = 3)
    UNION ALL SELECT N'dw.DimRoute', 1,
           (SELECT COUNT_BIG(*) FROM dw.DimRoute WHERE RouteKey = 2)
    UNION ALL SELECT N'dw.DimService', 1,
           (SELECT COUNT_BIG(*) FROM dw.DimService WHERE ServiceKey = 892)
    UNION ALL SELECT N'dw.DimStop', 2,
           (SELECT COUNT_BIG(*) FROM dw.DimStop WHERE StopKey IN (8, 1017))
    UNION ALL SELECT N'stg.MddRealtimeStopObservation', 1,
           (SELECT COUNT_BIG(*) FROM stg.MddRealtimeStopObservation WHERE ObservationKey = 4)
    UNION ALL SELECT N'stg.MddRealtimeSituationObservation', 1,
           (SELECT COUNT_BIG(*) FROM stg.MddRealtimeSituationObservation WHERE SituationObservationKey = 2)
    UNION ALL SELECT N'stg.MddRealtimeStopSituationLink', 1,
           (SELECT COUNT_BIG(*) FROM stg.MddRealtimeStopSituationLink WHERE ObservationKey = 4 AND SituationObservationKey = 2)
) AS v(table_name, expected_rows, actual_rows)
ORDER BY v.table_name;

SELECT
    N'portfolio.retention_data_domain_allocations' AS statement_id,
    sch.name AS schema_name,
    tab.name AS table_name,
    MAX(CASE
        WHEN LOWER(sch.name) LIKE N'%gtfs%'
          OR LOWER(tab.name) LIKE N'%gtfs%' THEN N'GTFS'
        WHEN LOWER(sch.name) IN (N'dw', N'dwh', N'warehouse')
          OR LOWER(tab.name) LIKE N'%warehouse%'
          OR LOWER(tab.name) LIKE N'fact%'
          OR LOWER(tab.name) LIKE N'dim%' THEN N'WAREHOUSE'
        WHEN LOWER(sch.name) IN (N'ctl', N'stg', N'src', N'wrk', N'core', N'analytics')
          OR LOWER(tab.name) LIKE N'%lineage%'
          OR LOWER(tab.name) LIKE N'%config%'
          OR LOWER(tab.name) LIKE N'%audit%'
          OR LOWER(tab.name) LIKE N'%run%'
          OR LOWER(tab.name) LIKE N'%claim%'
          OR LOWER(tab.name) LIKE N'%lease%' THEN N'ETL_LINEAGE_OR_SOURCE'
        ELSE N'OTHER'
    END) AS data_domain,
    SUM(CONVERT(bigint, ps.row_count)) AS physical_rows,
    SUM(CONVERT(bigint, ps.reserved_page_count)) * CONVERT(bigint, 8192)
        AS reserved_bytes,
    SUM(CONVERT(bigint, ps.used_page_count)) * CONVERT(bigint, 8192)
        AS used_bytes,
    N'PRESERVE_STRUCTURE; RETAIN_OR_REDUCE_ROWS_ONLY_AFTER_DOMAIN_MANIFEST'
        AS disposition
FROM sys.dm_db_partition_stats AS ps
JOIN sys.tables AS tab ON tab.object_id = ps.object_id
JOIN sys.schemas AS sch ON sch.schema_id = tab.schema_id
WHERE ps.index_id IN (0, 1)
GROUP BY sch.name, tab.name
ORDER BY reserved_bytes DESC, sch.name, tab.name;

SELECT
    N'portfolio.retention_foreign_keys_all_schemas' AS statement_id,
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    fk.delete_referential_action_desc,
    fk.update_referential_action_desc,
    fk.is_disabled,
    fk.is_not_trusted,
    N'RETAIN_PARENT_CHILD_CLOSURE; NEVER_ASSUME_CASCADE_IS_SAFE'
        AS retention_contract
FROM sys.foreign_keys AS fk
WHERE OBJECTPROPERTYEX(fk.parent_object_id, 'IsMSShipped') = 0
   OR OBJECTPROPERTYEX(fk.referenced_object_id, 'IsMSShipped') = 0
ORDER BY child_schema, child_table, foreign_key_name;

SELECT
    N'portfolio.retention_domain_contract' AS statement_id,
    N'MDD' AS domain_name,
    N'EXACTLY_TEN_APPROVED_SNAPSHOTS_AND_ALL_DEPENDENT_ROWS' AS required_data,
    N'GTFS' AS next_domain_name,
    N'SMALL_REAL_COHERENT_DATASET_AND_ALL_REQUIRED_FK_ROWS' AS next_required_data,
    N'WAREHOUSE' AS next_next_domain_name,
    N'RELEVANT_DIMENSION_AND_FACT_ROWS_AND_ALL_REQUIRED_FK_ROWS'
        AS next_next_required_data,
    N'ETL_LINEAGE_CONFIGURATION_AUDIT' AS lineage_domain,
    N'RETAIN_ROWS_NEEDED_TO_EXPLAIN_THE_SAMPLE' AS lineage_required_data;

SELECT
    N'portfolio.retention_capacity_baseline' AS statement_id,
    df.name AS logical_file_name,
    df.type_desc AS file_type,
    CONVERT(bigint, df.size) * CONVERT(bigint, 8192) AS allocated_bytes,
    CASE WHEN df.type = 0
         THEN CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed')) * CONVERT(bigint, 8192)
         ELSE NULL END AS used_bytes,
    CASE WHEN df.type = 0
         THEN (CONVERT(bigint, df.size)
             - CONVERT(bigint, FILEPROPERTY(df.name, N'SpaceUsed'))) * CONVERT(bigint, 8192)
         ELSE NULL END AS internal_free_bytes,
    vs.volume_mount_point,
    CONVERT(bigint, vs.available_bytes) AS windows_free_bytes,
    N'BASELINE_ONLY; MDF FREE PAGES ARE NOT WINDOWS RECOVERY' AS measurement_basis
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
ORDER BY df.file_id;

SELECT
    N'portfolio.in_place_capacity_decision' AS statement_id,
    N'BOUNDED_IN_PLACE_APPROVED_IMPLEMENTATION; NO_REPLACEMENT_COPY_REQUIRED' AS selected_method,
    CONVERT(bigint, MIN(vs.available_bytes)) AS measured_current_c_free_bytes,
    CONVERT(bigint, 0) AS measured_external_copy_reserve_bytes,
    CONVERT(bigint, 12) * CONVERT(bigint, 1024) * CONVERT(bigint, 1024)
        * CONVERT(bigint, 1024) AS required_c_free_bytes,
    N'READ_ONLY_CAPACITY_MEASURED; BOUNDED_IN_PLACE_REQUIRES_PER_BATCH_GATE'
        AS status,
    N'No second disk or full database copy is required. Review the direct C:/log/tempdb measurements and the executable apply gate.'
        AS required_action
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:';

SELECT
    N'portfolio.log_tempdb_capacity' AS statement_id,
    N'TRANSACTION_LOG' AS resource_name,
    total_log_size_in_bytes AS allocated_bytes,
    used_log_space_in_bytes AS used_bytes,
    total_log_size_in_bytes - used_log_space_in_bytes AS available_bytes,
    used_log_space_in_percent
FROM sys.dm_db_log_space_usage
UNION ALL
SELECT
    N'portfolio.log_tempdb_capacity',
    N'TEMPDB',
    SUM(CONVERT(bigint, df.size)) * 8192,
    SUM(CONVERT(bigint, COALESCE(fsu.user_object_reserved_page_count, 0) + COALESCE(fsu.internal_object_reserved_page_count, 0) + COALESCE(fsu.version_store_reserved_page_count, 0))) * 8192,
    SUM(CONVERT(bigint, COALESCE(fsu.unallocated_extent_page_count, 0))) * 8192,
    NULL
FROM tempdb.sys.database_files AS df
LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu ON fsu.file_id = df.file_id
WHERE df.type = 0;

SELECT
    N'portfolio.external_consumer_review' AS statement_id,
    N'REVIEW_REQUIRED; REPORTING_PRODUCT_OUT_OF_SCOPE' AS status,
    N'Attach current direct SQL, notebook, export and raw SnapshotJson consumer evidence before cleanup. Power BI is out of scope.'
        AS required_action;

SELECT
    N'portfolio.backup_verification' AS statement_id,
    N'REVIEW_REQUIRED' AS status,
    N'Attach off-C backup metadata and RESTORE VERIFYONLY WITH CHECKSUM evidence; do not overwrite the October 14 recovery point.'
        AS required_action;

SELECT
    N'portfolio.retention_dry_run_gate' AS statement_id,
    N'DRY_RUN_ONLY_EVIDENCE' AS status,
    10 AS required_manifest_rows,
    (SELECT COUNT_BIG(*) FROM @Manifest) AS supplied_manifest_rows,
    (SELECT STRING_AGG(CONVERT(nvarchar(20), a.SnapshotId), N', ')
     FROM (VALUES
         (CONVERT(bigint, 22)), (CONVERT(bigint, 93)),
         (CONVERT(bigint, 185)), (CONVERT(bigint, 189)),
         (CONVERT(bigint, 207)), (CONVERT(bigint, 224)),
         (CONVERT(bigint, 225)), (CONVERT(bigint, 226)),
         (CONVERT(bigint, 228)), (CONVERT(bigint, 229))
     ) AS a(SnapshotId)) AS approved_snapshot_ids,
    @OperatorEvidenceReference AS operator_evidence_reference,
    N'No base rows were changed. Review the complete object inventory, exact
      executable cross-layer manifest, FK closure, backup coverage, consumer
      evidence, direct capacity measurements, and the exact APPLY file separately.'
        AS next_gate;

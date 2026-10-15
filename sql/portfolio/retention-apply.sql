/*
    COLOGNE TRANSIT INTELLIGENCE — BOUNDED TEN-SNAPSHOT RETENTION APPLY

    DESTRUCTIVE OPERATOR SCRIPT.  It is inert by default and is not run by
    Codex.  The S154 ID set is locked to:
      22, 93, 185, 189, 207, 224, 225, 226, 228, 229

    It is inert by default:
      @Apply = 0
      @ManifestApproved = 0
      all recovery, capacity, consumer, replacement-choice, and collector
      gates are 0/NULL

    Codex must not run this file.  An administrator may create a reviewed
    working copy only after the exact ten rows from
    select-ten-snapshots-readonly.sql, the dry-run result sets, backup
    coverage, dependency review, and the C: projection have been signed off.

    Scope is data-only and schema-preserving: retain the ten literal approved
    SnapshotIds and all dependent rows, plus the reviewed GTFS, warehouse
    dimension/fact, and ETL lineage/configuration/audit data manifest.  The
    existing complete structure remains in place: every schema, physical
    table, column, index, key, foreign key, view, function, procedure, and
    trigger is preserved.  Views are not physical tables and are never
    included in a data-delete list.

    The only built-in deletes are the seven source-faithful stg child families
    and their stg.MddMacSourceSnapshot parent.  ctl lineage/configuration/audit,
    GTFS, warehouse, src, wrk, core, analytics, and any other physical table
    require an explicit table-level retention manifest and an operator-reviewed
    extension; none is discarded automatically by this template.  The
    preflight inventories all FK relationships and blocks on an unexpected
    dependent table.  Every permitted table is processed in small committed
    batches; rerunning after a verified interruption is idempotent.  No
    schema, recovery model, autogrowth, collector state, or existing backup is
    changed.
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
IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
    OR OBJECT_ID(N'ctl.MddMacCollectorRun', N'U') IS NULL
    OR OBJECT_ID(N'ctl.MddMacTargetAudit', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacCallObservation', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacSituationEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacServiceSectionEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacAttributeEvidence', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacResponseError', N'U') IS NULL
    OR OBJECT_ID(N'stg.MddMacParserGap', N'U') IS NULL
    THROW 52221, 'Required MDD/control tables are missing; no apply is allowed.', 1;

DECLARE @Apply BIT = 0;
DECLARE @ManifestApproved BIT = 0;
DECLARE @BackupCoverageConfirmed BIT = 0;
DECLARE @BackupVerificationConfirmed BIT = 0;
DECLARE @DependencyReviewConfirmed BIT = 0;
DECLARE @ExternalConsumerReviewConfirmed BIT = 0;
DECLARE @CapacityProjectionConfirmed BIT = 0;
DECLARE @ReplacementDecisionConfirmed BIT = 0;
DECLARE @InPlaceFallbackApproved BIT = 0;
DECLARE @CollectorsDisabledConfirmed BIT = 0;
DECLARE @SchemaInventoryReviewed BIT = 0;
DECLARE @CrossLayerRetentionManifestConfirmed BIT = 0;
DECLARE @GtfsRetentionConfirmed BIT = 0;
DECLARE @WarehouseRetentionConfirmed BIT = 0;
DECLARE @LineageConfigurationAuditRetentionConfirmed BIT = 0;
DECLARE @ApprovalReference NVARCHAR(400) = N'';
DECLARE @BackupEvidenceReference NVARCHAR(400) = N'';
DECLARE @DependencyEvidenceReference NVARCHAR(400) = N'';
DECLARE @ExternalConsumerEvidenceReference NVARCHAR(400) = N'';
DECLARE @CapacityEvidenceReference NVARCHAR(400) = N'';
DECLARE @SchemaInventoryEvidenceReference NVARCHAR(400) = N'';
DECLARE @CrossLayerRetentionEvidenceReference NVARCHAR(400) = N'';
DECLARE @MeasuredCurrentCFreeBytes BIGINT = NULL;
DECLARE @MeasuredWorstCasePurgeReserveBytes BIGINT = NULL;
DECLARE @ProjectBaselineBytes BIGINT = NULL;
DECLARE @ProjectedProjectBytesAfterPurge BIGINT = NULL;
DECLARE @RequiredCFreeBytes BIGINT =
    CONVERT(BIGINT, 12) * CONVERT(BIGINT, 1024) * CONVERT(BIGINT, 1024)
        * CONVERT(BIGINT, 1024);
DECLARE @BatchSize INT = 1000;
DECLARE @Rows BIGINT;
DECLARE @TotalRows BIGINT = 0;

DECLARE @DataRetentionManifest TABLE
(
    DomainName VARCHAR(40) NOT NULL,
    SchemaName sysname NULL,
    TableName sysname NULL,
    PhysicalTableReviewed BIT NOT NULL,
    RetentionRule NVARCHAR(400) NOT NULL,
    EvidenceReference NVARCHAR(400) NOT NULL
);

DECLARE @ApprovedSnapshotIds TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY
);

INSERT @ApprovedSnapshotIds (SnapshotId)
VALUES (22), (93), (185), (189), (207), (224), (225), (226), (228), (229);

DECLARE @Manifest TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    RunId NVARCHAR(100) NOT NULL,
    AuditId BIGINT NOT NULL,
    OriginalPayloadSha256 VARCHAR(64) NOT NULL,
    PayloadHashSha256 CHAR(64) NULL
);

/*
    Paste exactly ten literal rows from the reviewed candidate output here.
    Do not use TOP, a range, a subquery, or a guessed ID.

    INSERT @Manifest
        (SnapshotId, RunId, AuditId, OriginalPayloadSha256, PayloadHashSha256)
    VALUES
        (123, N'<exact RunId>', 456, '<64 hex chars>', '<64 hex chars>'),
        ... nine more reviewed literal rows ...;

    Paste the separately reviewed @DataRetentionManifest rows for the actual
    GTFS, warehouse dimension/fact, and ETL lineage/configuration/audit tables.
    This manifest is a data-row retention contract, not a schema/object list;
    all object definitions remain untouched.  ctl history is preserved unless
    a separately reviewed table-specific data operation is approved.

    INSERT @DataRetentionManifest
        (DomainName, SchemaName, TableName, PhysicalTableReviewed,
         RetentionRule, EvidenceReference)
    VALUES
        ('GTFS', N'<actual schema>', N'<actual table>', 1,
         N'<coherent sample and FK closure>', N'<evidence>'),
        ('WAREHOUSE', N'<actual schema>', N'<dimension/fact table>', 1,
         N'<relevant rows and FK closure>', N'<evidence>'),
        ('ETL_LINEAGE_CONFIG_AUDIT', N'<actual schema>', N'<actual table>', 1,
         N'<rows required by the retained sample>', N'<evidence>');
*/

IF @Apply = 0
BEGIN
    SELECT
        N'portfolio.retention_apply_gate' AS statement_id,
        N'APPLY_DISABLED' AS status,
        N'No base rows were changed. Keep this default until the reviewed
          working copy is manually authorized.' AS next_action;
    RETURN;
END;

IF @ManifestApproved = 0
   OR @BackupCoverageConfirmed = 0
   OR @BackupVerificationConfirmed = 0
   OR @DependencyReviewConfirmed = 0
   OR @ExternalConsumerReviewConfirmed = 0
   OR @CapacityProjectionConfirmed = 0
   OR @ReplacementDecisionConfirmed = 0
   OR @InPlaceFallbackApproved = 0
   OR @CollectorsDisabledConfirmed = 0
   OR @SchemaInventoryReviewed = 0
   OR @CrossLayerRetentionManifestConfirmed = 0
   OR @GtfsRetentionConfirmed = 0
   OR @WarehouseRetentionConfirmed = 0
   OR @LineageConfigurationAuditRetentionConfirmed = 0
   OR NULLIF(@ApprovalReference, N'') IS NULL
   OR NULLIF(@BackupEvidenceReference, N'') IS NULL
   OR NULLIF(@DependencyEvidenceReference, N'') IS NULL
   OR NULLIF(@ExternalConsumerEvidenceReference, N'') IS NULL
   OR NULLIF(@CapacityEvidenceReference, N'') IS NULL
   OR NULLIF(@SchemaInventoryEvidenceReference, N'') IS NULL
   OR NULLIF(@CrossLayerRetentionEvidenceReference, N'') IS NULL
    THROW 52202, 'All reviewed retention gates and evidence references are required.', 1;

IF @BatchSize < 1 OR @BatchSize > 1000
    THROW 52203, 'BatchSize must remain between 1 and 1000.', 1;
IF (SELECT COUNT_BIG(*) FROM @Manifest) <> 10
    THROW 52204, 'Exactly ten literal manifest rows are required.', 1;
IF
(
    SELECT COUNT_BIG(*)
    FROM @Manifest AS m
    JOIN @ApprovedSnapshotIds AS a ON a.SnapshotId = m.SnapshotId
) <> 10
    THROW 52216, 'Manifest SnapshotIds do not exactly match the S154 approved ID set.', 1;

IF (SELECT COUNT_BIG(*) FROM @DataRetentionManifest) = 0
    THROW 52217, 'A reviewed cross-layer data retention manifest is required.', 1;
IF EXISTS
(
    SELECT 1
    FROM @DataRetentionManifest AS d
    WHERE d.PhysicalTableReviewed = 0
       OR NULLIF(d.DomainName, '') IS NULL
       OR NULLIF(d.RetentionRule, N'') IS NULL
       OR NULLIF(d.EvidenceReference, N'') IS NULL
       OR d.SchemaName IS NULL
       OR d.TableName IS NULL
       OR OBJECT_ID(QUOTENAME(d.SchemaName) + N'.' + QUOTENAME(d.TableName), N'U') IS NULL
)
    THROW 52218, 'Cross-layer manifest must identify actual physical tables and evidence.', 1;
IF EXISTS
(
    SELECT 1
    FROM @DataRetentionManifest AS d
    JOIN sys.views AS v
      ON v.schema_id = SCHEMA_ID(d.SchemaName)
     AND v.name = d.TableName
)
    THROW 52219, 'A view was supplied as a physical data table; preserve the view definition.', 1;

SELECT
    N'portfolio.retention_apply_schema_inventory' AS statement_id,
    (SELECT COUNT_BIG(*) FROM sys.schemas
     WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest'))
        AS user_schema_count,
    COUNT_BIG(CASE WHEN obj.type = 'U' THEN 1 END) AS physical_table_count,
    COUNT_BIG(CASE WHEN obj.type = 'V' THEN 1 END) AS view_count,
    COUNT_BIG(CASE WHEN obj.type IN ('P', 'PC') THEN 1 END) AS procedure_count,
    COUNT_BIG(CASE WHEN obj.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN 1 END)
        AS function_count,
    COUNT_BIG(CASE WHEN obj.type = 'TR' THEN 1 END) AS trigger_count,
    N'NO_SCHEMA_OR_OBJECT_MUTATION_ALLOWED' AS schema_boundary
FROM sys.objects AS obj
JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
WHERE obj.is_ms_shipped = 0;

SELECT
    N'portfolio.retention_apply_cross_layer_manifest' AS statement_id,
    d.DomainName AS domain_name,
    d.SchemaName AS schema_name,
    d.TableName AS table_name,
    d.RetentionRule AS retention_rule,
    d.EvidenceReference AS evidence_reference,
    N'REVIEWED_DATA_ROWS_ONLY; PRESERVE_OBJECT_DEFINITION' AS scope_boundary
FROM @DataRetentionManifest AS d
ORDER BY d.DomainName, d.SchemaName, d.TableName;

SELECT
    N'portfolio.retention_apply_foreign_keys_all_schemas' AS statement_id,
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,
    OBJECT_NAME(fk.parent_object_id) AS child_table,
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,
    OBJECT_NAME(fk.referenced_object_id) AS parent_table,
    fk.delete_referential_action_desc,
    fk.update_referential_action_desc,
    fk.is_disabled,
    fk.is_not_trusted,
    N'PRESERVE_FK_AND_RETAIN_REQUIRED_PARENT_CHILD_ROWS'
        AS retention_contract
FROM sys.foreign_keys AS fk
WHERE OBJECTPROPERTYEX(fk.parent_object_id, 'IsMSShipped') = 0
   OR OBJECTPROPERTYEX(fk.referenced_object_id, 'IsMSShipped') = 0
ORDER BY child_schema, child_table, foreign_key_name;

DECLARE @MddRetentionTables TABLE
(
    ObjectId INT NOT NULL PRIMARY KEY
);

INSERT @MddRetentionTables (ObjectId)
SELECT OBJECT_ID(v.ObjectName, N'U')
FROM (VALUES
    (N'stg.MddMacSourceSnapshot'),
    (N'stg.MddMacTimingObservation'),
    (N'stg.MddMacCallObservation'),
    (N'stg.MddMacSituationEvidence'),
    (N'stg.MddMacServiceSectionEvidence'),
    (N'stg.MddMacAttributeEvidence'),
    (N'stg.MddMacResponseError'),
    (N'stg.MddMacParserGap')
) AS v(ObjectName)
WHERE OBJECT_ID(v.ObjectName, N'U') IS NOT NULL;

IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys AS fk
    WHERE fk.referenced_object_id IN (SELECT ObjectId FROM @MddRetentionTables)
      AND fk.parent_object_id NOT IN (SELECT ObjectId FROM @MddRetentionTables)
)
    THROW 52220, 'An unregistered FK-dependent table would be affected by MDD data deletion.', 1;
IF @MeasuredCurrentCFreeBytes IS NULL
   OR @MeasuredWorstCasePurgeReserveBytes IS NULL
   OR @ProjectBaselineBytes IS NULL
   OR @ProjectedProjectBytesAfterPurge IS NULL
    THROW 52205, 'Fresh C:, purge-reserve, and project-baseline evidence is required.', 1;
IF @MeasuredCurrentCFreeBytes - @MeasuredWorstCasePurgeReserveBytes
       < @RequiredCFreeBytes
    THROW 52206, 'Projected worst-case C: free space would cross the 12 GiB floor.', 1;
IF @ProjectBaselineBytes <= 0
   OR @ProjectedProjectBytesAfterPurge < 0
   OR @ProjectedProjectBytesAfterPurge > @ProjectBaselineBytes
   OR
   (
       CONVERT(DECIMAL(19, 6), @ProjectBaselineBytes
             - @ProjectedProjectBytesAfterPurge) * 100.0
       / CONVERT(DECIMAL(19, 6), @ProjectBaselineBytes)
   ) < 80.0
    THROW 52207, 'The reviewed projection does not target at least 80 percent project-byte recovery.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN
          (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52208, 'A collector run is active; retention apply is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE Status = N'ACTIVE'
)
    THROW 52209, 'An active production lease exists; retention apply is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.dm_exec_requests AS req
    WHERE req.session_id <> @@SPID
      AND req.database_id = DB_ID()
      AND req.command <> N'AWAITING COMMAND'
)
    THROW 52210, 'Another active request exists in the target database.', 1;

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
    THROW 52211, 'Reviewed manifest no longer matches live SQL evidence.', 1;

SELECT
    N'portfolio.retention_apply_before' AS statement_id,
    COUNT_BIG(*) AS current_snapshot_rows,
    (SELECT COUNT_BIG(*) FROM @Manifest) AS retained_snapshot_rows,
    @MeasuredCurrentCFreeBytes AS measured_c_free_bytes,
    @MeasuredWorstCasePurgeReserveBytes AS measured_worst_case_purge_reserve_bytes,
    @MeasuredCurrentCFreeBytes - @MeasuredWorstCasePurgeReserveBytes
        AS projected_worst_case_c_free_bytes,
    @ProjectBaselineBytes AS project_baseline_bytes,
    @ProjectedProjectBytesAfterPurge AS projected_project_bytes_after_purge,
    CONVERT(DECIMAL(19, 6),
        (@ProjectBaselineBytes - @ProjectedProjectBytesAfterPurge) * 100.0
        / NULLIF(@ProjectBaselineBytes, 0)) AS projected_project_recovery_percent
FROM stg.MddMacSourceSnapshot;

BEGIN TRY
    /* Child families are processed before their SnapshotId parent. */
    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacResponseError AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacParserGap AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacAttributeEvidence AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacServiceSectionEvidence AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacSituationEvidence AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacCallObservation AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacTimingObservation AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;

    WHILE 1 = 1
    BEGIN
        BEGIN TRANSACTION;
        DELETE TOP (@BatchSize)
        FROM stg.MddMacSourceSnapshot AS x
        WHERE NOT EXISTS
            (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = x.SnapshotId);
        SET @Rows = @@ROWCOUNT;
        COMMIT TRANSACTION;
        SET @TotalRows += @Rows;
        IF @Rows = 0 BREAK;
    END;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

IF
(
    SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot
) <> 10
    THROW 52213, 'Post-apply source snapshot count is not exactly ten.', 1;

IF EXISTS
(
    SELECT 1
    FROM stg.MddMacSourceSnapshot AS s
    WHERE NOT EXISTS
        (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = s.SnapshotId)
)
    THROW 52214, 'A nonretained source snapshot remains after bounded apply.', 1;

IF EXISTS
(
    SELECT 1
    FROM
    (
        SELECT SnapshotId FROM stg.MddMacTimingObservation
        UNION ALL SELECT SnapshotId FROM stg.MddMacCallObservation
        UNION ALL SELECT SnapshotId FROM stg.MddMacSituationEvidence
        UNION ALL SELECT SnapshotId FROM stg.MddMacServiceSectionEvidence
        UNION ALL SELECT SnapshotId FROM stg.MddMacAttributeEvidence
        UNION ALL SELECT SnapshotId FROM stg.MddMacResponseError
        UNION ALL SELECT SnapshotId FROM stg.MddMacParserGap
    ) AS child_rows
    WHERE NOT EXISTS
        (SELECT 1 FROM @Manifest AS m WHERE m.SnapshotId = child_rows.SnapshotId)
)
    THROW 52215, 'A nonretained child row remains after bounded apply.', 1;

SELECT
    N'portfolio.retention_apply_cross_layer_postcheck' AS statement_id,
    (SELECT COUNT_BIG(*) FROM @DataRetentionManifest)
        AS reviewed_cross_layer_table_count,
    (SELECT COUNT_BIG(*) FROM sys.schemas
     WHERE name NOT IN (N'sys', N'INFORMATION_SCHEMA', N'guest'))
        AS user_schema_count_after,
    (SELECT COUNT_BIG(*) FROM sys.views WHERE is_ms_shipped = 0)
        AS view_count_after,
    N'ALL_SCHEMA_OBJECT_DEFINITIONS_PRESERVED; DATA_ONLY_OPERATION'
        AS schema_verification,
    N'GTFS_WAREHOUSE_LINEAGE_CONFIGURATION_AUDIT_REQUIRE_POST_READBACK'
        AS cross_layer_verification;

SELECT
    N'portfolio.retention_apply_result' AS statement_id,
    N'OPERATOR_APPLY_COMPLETED; VERIFY_BEFORE_SHRINK' AS status,
    @TotalRows AS rows_removed_from_stg,
    10 AS retained_snapshot_rows,
    @ApprovalReference AS approval_reference,
    @BackupEvidenceReference AS backup_evidence_reference,
    @DependencyEvidenceReference AS dependency_evidence_reference,
    @ExternalConsumerEvidenceReference AS external_consumer_evidence_reference,
    @CapacityEvidenceReference AS capacity_evidence_reference,
    N'No ctl rows were removed by this template. GTFS, warehouse, src, wrk,
      core, analytics, and other non-MDD data were not automatically removed.
      MDF free pages are not Windows C: recovery.'
        AS physical_recovery_warning;

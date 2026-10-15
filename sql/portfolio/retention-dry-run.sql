/*
    COLOGNE TRANSIT INTELLIGENCE — TEN-SNAPSHOT RETENTION DRY RUN

    Read-only against the base database.  This script intentionally contains
    no base-table mutation.  The S154 operator-confirmed ID set is locked to
    22, 93, 185, 189, 207, 224, 225, 226, 228, 229.  The operator must paste
    exactly ten evidence rows for these approved IDs from the read-only
    candidate/readback results into the table variable and set
    @ManifestApproved = 1 only after reviewing them.
    The default exits with a blocked result and cannot act on an empty,
    guessed, or different set.

    This dry run inventories the complete deployed structure first, then
    reports the seven stg child families, connected ctl lineage/configuration/
    audit rows, GTFS sample contract, and warehouse dimension/fact contract.
    A small replacement database is preferred if an independent peak
    measurement fits on C: while preserving the 12 GiB floor.  This file is
    not a backup, replacement-database build, or proof of Windows recovery.
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

DECLARE @ManifestApproved BIT = 0;
DECLARE @SchemaInventoryReviewed BIT = 0;
DECLARE @CrossLayerManifestApproved BIT = 0;
DECLARE @GtfsManifestApproved BIT = 0;
DECLARE @WarehouseManifestApproved BIT = 0;
DECLARE @LineageManifestApproved BIT = 0;
DECLARE @OperatorEvidenceReference NVARCHAR(400) = N'';
DECLARE @SchemaInventoryReference NVARCHAR(400) = N'';
DECLARE @CrossLayerEvidenceReference NVARCHAR(400) = N'';
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

/*
    Paste exactly ten reviewed rows here, using only the approved IDs:

    INSERT @Manifest
        (SnapshotId, RunId, AuditId, OriginalPayloadSha256, PayloadHashSha256)
    VALUES
        (22, N'<exact RunId>', 456, '<64 hex chars>', '<64 hex chars>'),
        ... nine more literal rows for 93, 185, 189, 207, 224, 225, 226, 228 and 229 ...;

    Do not replace the values with TOP, a range, or a subquery.

    Also paste the reviewed table-level data manifest before setting the
    cross-layer gates.  It must identify the actual live physical tables and
    retain a small coherent GTFS sample, relevant warehouse dimensions and
    facts, and the lineage/configuration/audit rows needed by the retained
    MDD sample.  Views and programmable objects are never entered as data
    tables; their definitions remain preserved.

    INSERT @DataRetentionManifest
        (DomainName, SchemaName, TableName, PhysicalTableReviewed,
         RetentionRule, EvidenceReference)
    VALUES
        ('GTFS', N'<actual schema>', N'<actual table>', 1,
         N'<reviewed coherent sample and FK closure>', N'<evidence>'),
        ('WAREHOUSE', N'<actual schema>', N'<dimension or fact table>', 1,
         N'<reviewed relevant rows and FK closure>', N'<evidence>'),
        ('ETL_LINEAGE_CONFIG_AUDIT', N'<actual schema>', N'<actual table>', 1,
         N'<connected rows required for the retained sample>', N'<evidence>');
*/

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
    N'portfolio.replacement_database_fit_gate' AS statement_id,
    N'PREFERRED_IF_INDEPENDENTLY_VALIDATED_PEAK_FITS' AS preferred_method,
    CONVERT(bigint, NULL) AS measured_current_c_free_bytes,
    CONVERT(bigint, NULL) AS measured_replacement_peak_reserve_bytes,
    CONVERT(bigint, 12) * CONVERT(bigint, 1024) * CONVERT(bigint, 1024)
        * CONVERT(bigint, 1024) AS required_c_free_bytes,
    N'BLOCKED_UNTIL_ISOLATED_REPLACEMENT_PEAK_IS_MEASURED; NO DATABASE COPY WAS CREATED'
        AS status,
    N'If the peak does not fit, document the separately reviewed bounded in-place fallback.'
        AS required_action;

SELECT
    N'portfolio.external_consumer_review' AS statement_id,
    N'BLOCKED_EXTERNAL_CONSUMER_REVIEW' AS status,
    N'Attach current direct SQL, notebook, export, Power BI and raw SnapshotJson consumer evidence before cleanup.'
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
    N'No base rows were changed. Review the complete object inventory, data
      manifest, FK closure, backup coverage, consumer evidence, replacement
      peak or fallback capacity projection, and the exact APPLY file separately.'
        AS next_gate;

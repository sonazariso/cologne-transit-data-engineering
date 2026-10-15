/*
    COLOGNE TRANSIT INTELLIGENCE — TEN-SNAPSHOT RETENTION DRY RUN

    Read-only against the base database.  This script intentionally contains
    no base-table mutation.  The operator must paste exactly ten rows from
    select-ten-snapshots-readonly.sql into the table variable and set
    @ManifestApproved = 1 after reviewing the manifest.  The default exits
    with a blocked result and cannot act on an empty or guessed set.

    This dry run keeps all ctl history in scope and reports the seven stg
    child families that the later bounded purge would remove for nonretained
    snapshots.  It is not a backup or a proof of Windows disk recovery.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;
SET IMPLICIT_TRANSACTIONS OFF;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52100, 'Wrong database. Expected CologneTransitIntelligence.', 1;

DECLARE @ManifestApproved BIT = 0;
DECLARE @OperatorEvidenceReference NVARCHAR(400) = N'';
DECLARE @Manifest TABLE
(
    SnapshotId BIGINT NOT NULL PRIMARY KEY,
    RunId NVARCHAR(100) NOT NULL,
    AuditId BIGINT NOT NULL,
    OriginalPayloadSha256 VARCHAR(64) NOT NULL,
    PayloadHashSha256 CHAR(64) NULL
);

/*
    Paste exactly ten reviewed rows here, for example:

    INSERT @Manifest
        (SnapshotId, RunId, AuditId, OriginalPayloadSha256, PayloadHashSha256)
    VALUES
        (123, N'<exact RunId>', 456, '<64 hex chars>', '<64 hex chars>'),
        ... nine more literal rows ...;

    Do not replace the values with TOP, a range, or a subquery.
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
    N'portfolio.retention_dry_run_gate' AS statement_id,
    N'PASS_FOR_REVIEW_ONLY' AS status,
    10 AS required_manifest_rows,
    (SELECT COUNT_BIG(*) FROM @Manifest) AS supplied_manifest_rows,
    @OperatorEvidenceReference AS operator_evidence_reference,
    N'No base rows were changed. Review the result sets, backup coverage,
      consumer evidence, capacity projection, and exact APPLY file separately.'
        AS next_gate;

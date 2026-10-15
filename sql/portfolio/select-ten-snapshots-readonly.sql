/*
    COLOGNE TRANSIT INTELLIGENCE — EXACTLY TEN SNAPSHOT CANDIDATES

    READ ONLY.  This query uses the deployed SQL Server rows, not guessed
    SnapshotIds.  It returns exactly ten rows only when at least ten
    successfully persisted, referentially linked snapshots satisfy the
    evidence gate below.  It does not create a manifest, write a table, or
    delete anything.

    Selection policy:
      * HTTP 200, parsed, persisted, completed run/audit, valid one-payload
        physical representation;
      * deterministic coverage score for timing, planned/estimated/recorded
        fields, identity, call/service/situation/attribute evidence, errors
        and parser gaps;
      * round-robin by TargetIdentity after the coverage score, then UTC
        observation time and SnapshotId.  This reduces target concentration
        without claiming representativeness.

    The operator must review the returned ten rows and copy their complete
    evidence into docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md before any APPLY
    script is considered.  For the S154 closeout, the operator-confirmed IDs
    are 22, 93, 185, 189, 207, 224, 225, 226, 228 and 229; this candidate
    query must not override that approved set.  Use the exact-ID readback in
    replacement-database-dry-run.sql to prove those IDs.  If a desired case
    is not present, the query does not invent it; the rationale must say which
    available case was retained.

    This candidate query intentionally selects only the MDD SnapshotId set;
    it is not a cross-layer cleanup authorization.  Run the complete object
    inventory and review GTFS, warehouse dimension/fact, ETL lineage,
    configuration, audit and all-FK retention closure separately before any
    data-only apply.
*/
USE [CologneTransitIntelligence];
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 52010, 'Wrong database. Expected CologneTransitIntelligence.', 1;

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
    THROW 52011, 'Required ctl/stg retention tables are missing.', 1;

IF COL_LENGTH(N'stg.MddMacSourceSnapshot', N'SnapshotJsonGzip') IS NULL
    THROW 52012, 'SnapshotJsonGzip is missing; validate the deployed schema first.', 1;

IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN (N'RUNNING', N'CLAIMED')
      AND CollectionContext IN
          (N'MAC_LEGACY7_PRODUCTION', N'MAC_TIERED50_PRODUCTION')
)
    THROW 52014, 'A collector run is active; snapshot selection is stopped.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE Status = N'ACTIVE'
)
    THROW 52015, 'An active production lease exists; snapshot selection is stopped.', 1;

SELECT
    N'portfolio.candidate_status_distribution' AS statement_id,
    r.CollectionContext,
    r.Status AS run_status,
    a.Status AS audit_status,
    a.HttpStatus,
    a.ParserOutcome,
    a.PersistenceOutcome,
    COUNT_BIG(*) AS snapshot_count
FROM stg.MddMacSourceSnapshot AS s
JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
JOIN ctl.MddMacCollectorRun AS r ON r.RunId = s.RunId
GROUP BY r.CollectionContext, r.Status, a.Status, a.HttpStatus,
         a.ParserOutcome, a.PersistenceOutcome
ORDER BY snapshot_count DESC, r.CollectionContext, r.Status, a.Status;

DECLARE @EligibleCount BIGINT;

SELECT @EligibleCount = COUNT_BIG(*)
FROM stg.MddMacSourceSnapshot AS s
JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
JOIN ctl.MddMacCollectorRun AS r ON r.RunId = s.RunId
WHERE a.HttpStatus = 200
  AND UPPER(COALESCE(r.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
  AND UPPER(COALESCE(a.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
  AND UPPER(COALESCE(a.ParserOutcome, '')) = 'PARSED'
  AND UPPER(COALESCE(a.PersistenceOutcome, '')) = 'PERSISTED_PARSED_SNAPSHOT'
  AND
  (
      s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL
      OR s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL
  );

IF @EligibleCount < 10
    THROW 52013, 'Fewer than ten successful persisted snapshots satisfy the evidence gate.', 1;

;WITH CandidateEvidence AS
(
    SELECT
        s.SnapshotId,
        s.RunId,
        s.AuditId,
        s.TargetIdentity,
        s.SamplingTargetId,
        s.LogicalDueBucket,
        s.ObservedAtUtc,
        s.ResponseTimestampSourceText,
        s.ParserVersion,
        s.ApplicationStatus,
        s.PayloadHashSha256,
        r.CollectionContext,
        r.Status AS RunStatus,
        a.Status AS AuditStatus,
        a.HttpStatus,
        a.ParserOutcome,
        a.PersistenceOutcome,
        CASE
            WHEN s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL
                THEN N'PLAINTEXT'
            WHEN s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL
                THEN N'GZIP'
            ELSE N'INVALID'
        END AS PayloadRepresentation,
        CONVERT(bigint, DATALENGTH(s.SnapshotJson)) AS SnapshotJsonBytes,
        CONVERT(bigint, DATALENGTH(s.SnapshotJsonGzip)) AS SnapshotJsonGzipBytes,
        CONVERT(bigint,
            CASE WHEN s.SnapshotJson IS NOT NULL
                 THEN DATALENGTH(s.SnapshotJson)
                 ELSE DATALENGTH(DECOMPRESS(s.SnapshotJsonGzip)) END) AS OriginalUtf16Bytes,
        CONVERT(varchar(64), HASHBYTES
        (
            'SHA2_256',
            CASE WHEN s.SnapshotJson IS NOT NULL
                 THEN CONVERT(varbinary(max), s.SnapshotJson)
                 ELSE DECOMPRESS(s.SnapshotJsonGzip) END
        ), 2) AS OriginalPayloadSha256,
        CONVERT(varchar(64), HASHBYTES('SHA2_256', s.SnapshotJsonGzip), 2)
            AS PhysicalGzipSha256,
        CONVERT(bigint, CASE WHEN s.SnapshotJson IS NOT NULL
                                  AND DATALENGTH(s.SnapshotJson) = 0
                             THEN 1 ELSE 0 END) AS OriginalWasEmpty,
        t.TimingRows,
        t.EstimatedTimeRows,
        t.RecordedAtTimeRows,
        t.EstablishedIdentityRows,
        t.ArrivalTimingRows,
        t.DepartureTimingRows,
        c.CallRows,
        c.CallScopeCount,
        sit.SituationRows,
        sec.ServiceSectionRows,
        att.AttributeRows,
        err.ResponseErrorRows,
        gap.ParserGapRows,
        ctl.RunRows,
        ctl.TargetAuditRows,
        ctl.HttpAttemptRows,
        ctl.QuotaPermitRows,
        ctl.DispatcherClaimRows,
        ctl.DispatcherClaimEventRows,
        ctl.TargetClaimRows,
        ctl.TargetClaimEventRows,
        ctl.ProductionLeaseRows,
        ctl.ProductionLeaseEventRows
    FROM stg.MddMacSourceSnapshot AS s
    JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
    JOIN ctl.MddMacCollectorRun AS r ON r.RunId = s.RunId
    OUTER APPLY
    (
        SELECT
            COUNT_BIG(*) AS TimingRows,
            COUNT_BIG(CASE WHEN x.EstimatedTimeSourceText IS NOT NULL THEN 1 END)
                AS EstimatedTimeRows,
            COUNT_BIG(CASE WHEN x.RecordedAtTimeSourceText IS NOT NULL THEN 1 END)
                AS RecordedAtTimeRows,
            COUNT_BIG(CASE WHEN x.SourceEventIdentityStatus
                                  = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
                            THEN 1 END) AS EstablishedIdentityRows,
            COUNT_BIG(CASE WHEN x.TimingContext = 'ARRIVAL' THEN 1 END)
                AS ArrivalTimingRows,
            COUNT_BIG(CASE WHEN x.TimingContext = 'DEPARTURE' THEN 1 END)
                AS DepartureTimingRows
        FROM stg.MddMacTimingObservation AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS t
    OUTER APPLY
    (
        SELECT
            COUNT_BIG(*) AS CallRows,
            COUNT_BIG(DISTINCT x.CallScope) AS CallScopeCount
        FROM stg.MddMacCallObservation AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS c
    OUTER APPLY
    (
        SELECT COUNT_BIG(*) AS SituationRows
        FROM stg.MddMacSituationEvidence AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS sit
    OUTER APPLY
    (
        SELECT COUNT_BIG(*) AS ServiceSectionRows
        FROM stg.MddMacServiceSectionEvidence AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS sec
    OUTER APPLY
    (
        SELECT COUNT_BIG(*) AS AttributeRows
        FROM stg.MddMacAttributeEvidence AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS att
    OUTER APPLY
    (
        SELECT COUNT_BIG(*) AS ResponseErrorRows
        FROM stg.MddMacResponseError AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS err
    OUTER APPLY
    (
        SELECT COUNT_BIG(*) AS ParserGapRows
        FROM stg.MddMacParserGap AS x
        WHERE x.SnapshotId = s.SnapshotId
    ) AS gap
    OUTER APPLY
    (
        SELECT
            COUNT_BIG(*) AS RunRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacTargetAudit AS x
             WHERE x.AuditId = s.AuditId) AS TargetAuditRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacHttpAttempt AS x
             WHERE x.AuditId = s.AuditId) AS HttpAttemptRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacQuotaPermit AS q
             WHERE EXISTS
             (
                 SELECT 1
                 FROM ctl.MddMacHttpAttempt AS h
                 WHERE h.AuditId = s.AuditId
                   AND h.QuotaPermitId = q.PermitId
             )) AS QuotaPermitRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacDispatcherClaim AS x
             WHERE x.RunId = s.RunId) AS DispatcherClaimRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacDispatcherClaimEvent AS x
             WHERE x.RunId = s.RunId) AS DispatcherClaimEventRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacTargetClaim AS x
             WHERE x.RunId = s.RunId) AS TargetClaimRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacTargetClaimEvent AS x
             WHERE x.RunId = s.RunId) AS TargetClaimEventRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacProductionLease AS x
             WHERE x.RunId = s.RunId) AS ProductionLeaseRows,
            (SELECT COUNT_BIG(*)
             FROM ctl.MddMacProductionLeaseEvent AS x
             WHERE x.RunId = s.RunId) AS ProductionLeaseEventRows
        FROM ctl.MddMacCollectorRun AS x
        WHERE x.RunId = s.RunId
    ) AS ctl
    WHERE a.HttpStatus = 200
      AND UPPER(COALESCE(r.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
      AND UPPER(COALESCE(a.Status, '')) IN ('COMPLETE', 'SUCCEEDED')
      AND UPPER(COALESCE(a.ParserOutcome, '')) = 'PARSED'
      AND UPPER(COALESCE(a.PersistenceOutcome, '')) = 'PERSISTED_PARSED_SNAPSHOT'
      AND
      (
          s.SnapshotJson IS NOT NULL AND s.SnapshotJsonGzip IS NULL
          OR s.SnapshotJson IS NULL AND s.SnapshotJsonGzip IS NOT NULL
      )
),
Scored AS
(
    SELECT
        c.*,
        (
            CASE WHEN c.TimingRows > 0 THEN 4 ELSE 0 END
          + CASE WHEN c.CallRows > 0 THEN 3 ELSE 0 END
          + CASE WHEN c.EstimatedTimeRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.RecordedAtTimeRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.EstablishedIdentityRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.SituationRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.ServiceSectionRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.AttributeRows > 0 THEN 2 ELSE 0 END
          + CASE WHEN c.ResponseErrorRows > 0 THEN 1 ELSE 0 END
          + CASE WHEN c.ParserGapRows > 0 THEN 1 ELSE 0 END
          + CASE WHEN c.RecordedAtTimeRows = 0 AND c.TimingRows > 0 THEN 1 ELSE 0 END
        ) AS CoverageScore,
        ROW_NUMBER() OVER
        (
            PARTITION BY c.TargetIdentity
            ORDER BY
                (
                    CASE WHEN c.TimingRows > 0 THEN 4 ELSE 0 END
                  + CASE WHEN c.CallRows > 0 THEN 3 ELSE 0 END
                  + CASE WHEN c.EstimatedTimeRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.RecordedAtTimeRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.EstablishedIdentityRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.SituationRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.ServiceSectionRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.AttributeRows > 0 THEN 2 ELSE 0 END
                  + CASE WHEN c.ResponseErrorRows > 0 THEN 1 ELSE 0 END
                  + CASE WHEN c.ParserGapRows > 0 THEN 1 ELSE 0 END
                  + CASE WHEN c.RecordedAtTimeRows = 0 AND c.TimingRows > 0 THEN 1 ELSE 0 END
                ) DESC,
                c.ObservedAtUtc,
                c.SnapshotId
        ) AS TargetCaseRank
    FROM CandidateEvidence AS c
),
RoundRobin AS
(
    SELECT
        s.*,
        ROW_NUMBER() OVER
        (
            ORDER BY s.TargetCaseRank, s.TargetIdentity, s.ObservedAtUtc, s.SnapshotId
        ) AS RetentionSlot
    FROM Scored AS s
)
SELECT
    N'portfolio.ten_snapshot_candidates' AS statement_id,
    r.RetentionSlot,
    COUNT_BIG(*) OVER () AS SelectedCount,
    r.SnapshotId,
    r.RunId,
    r.AuditId,
    r.TargetIdentity,
    r.SamplingTargetId,
    r.LogicalDueBucket,
    r.ObservedAtUtc,
    r.CollectionContext,
    r.RunStatus,
    r.AuditStatus,
    r.HttpStatus,
    r.ParserOutcome,
    r.PersistenceOutcome,
    r.ParserVersion,
    r.ApplicationStatus,
    r.PayloadRepresentation,
    r.SnapshotJsonBytes,
    r.SnapshotJsonGzipBytes,
    r.OriginalUtf16Bytes,
    r.OriginalWasEmpty,
    r.PayloadHashSha256,
    r.OriginalPayloadSha256,
    r.PhysicalGzipSha256,
    r.CoverageScore,
    CONCAT(
        CASE WHEN r.TimingRows > 0 THEN N'TIMING;' ELSE N'' END,
        CASE WHEN r.CallRows > 0 THEN N'CALL;' ELSE N'' END,
        CASE WHEN r.EstimatedTimeRows > 0 THEN N'ESTIMATED;' ELSE N'' END,
        CASE WHEN r.RecordedAtTimeRows > 0 THEN N'RECORDED;' ELSE N'MISSING_RECORDED;' END,
        CASE WHEN r.EstablishedIdentityRows > 0 THEN N'IDENTITY_ESTABLISHED;' ELSE N'IDENTITY_NOT_ESTABLISHED;' END,
        CASE WHEN r.SituationRows > 0 THEN N'SITUATION;' ELSE N'' END,
        CASE WHEN r.ServiceSectionRows > 0 THEN N'SERVICE_SECTION;' ELSE N'' END,
        CASE WHEN r.AttributeRows > 0 THEN N'ATTRIBUTE;' ELSE N'' END,
        CASE WHEN r.ResponseErrorRows > 0 THEN N'RESPONSE_ERROR;' ELSE N'' END,
        CASE WHEN r.ParserGapRows > 0 THEN N'PARSER_GAP;' ELSE N'' END
    ) AS SourceCoverage,
    r.TimingRows,
    r.EstimatedTimeRows,
    r.RecordedAtTimeRows,
    r.EstablishedIdentityRows,
    r.ArrivalTimingRows,
    r.DepartureTimingRows,
    r.CallRows,
    r.CallScopeCount,
    r.SituationRows,
    r.ServiceSectionRows,
    r.AttributeRows,
    r.ResponseErrorRows,
    r.ParserGapRows,
    r.RunRows,
    r.TargetAuditRows,
    r.HttpAttemptRows,
    r.QuotaPermitRows,
    r.DispatcherClaimRows,
    r.DispatcherClaimEventRows,
    r.TargetClaimRows,
    r.TargetClaimEventRows,
    r.ProductionLeaseRows,
    r.ProductionLeaseEventRows,
    r.TargetCaseRank
FROM RoundRobin AS r
WHERE r.RetentionSlot <= 10
ORDER BY r.RetentionSlot;

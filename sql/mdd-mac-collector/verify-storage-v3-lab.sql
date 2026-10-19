/*
STORAGE V3 LAB-ONLY READBACK CHECKS.

Run after a bounded sanitized fixture has been persisted on a database whose
name ends in _STORAGE_V3_LAB.  These checks do not change data and do not
issue MDD requests.  They validate representation integrity, view readback,
row counts, identity uniqueness, and the untouched timing fields.
*/
USE CologneTransitIntelligence_STORAGE_V3_LAB;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

IF DB_NAME() NOT LIKE N'%_STORAGE_V3_LAB'
    THROW 51320, 'This readback check is lab-only.', 1;

SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc
FROM sys.objects AS o
INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name = N'stg'
  AND o.name IN (
      N'trg_MddMacTimingObservation_StorageV3',
      N'trg_MddMacSituationEvidence_StorageV3',
      N'trg_MddMacServiceSectionEvidence_StorageV3',
      N'trg_MddMacAttributeEvidence_StorageV3',
      N'trg_MddMacParserGap_StorageV3',
      N'vMddMacTimingObservationJson',
      N'vMddMacSituationEvidenceJson',
      N'vMddMacServiceSectionEvidenceJson',
      N'vMddMacAttributeEvidenceJson',
      N'vMddMacParserGapJson'
  )
ORDER BY o.name;

-- A compressed row may have a NULL logical source value; a row may not have
-- both physical representations populated at once.
SELECT 'timing_missing_fields' AS field_name,
       SUM(CASE WHEN SourceEventIdentityMissingFieldsJson IS NOT NULL
                     AND SourceEventIdentityMissingFieldsJsonGzip IS NOT NULL THEN 1 ELSE 0 END) AS both_representations,
       SUM(CASE WHEN SourceEventIdentityMissingFieldsJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceEventIdentityMissingFieldsJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceEventIdentityMissingFieldsJsonGzip)) THEN 1 ELSE 0 END) AS gzip_roundtrip_failures
FROM stg.MddMacTimingObservation
UNION ALL
SELECT 'timing_ambiguity',
       SUM(CASE WHEN SourceEventIdentityAmbiguityJson IS NOT NULL
                     AND SourceEventIdentityAmbiguityJsonGzip IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN SourceEventIdentityAmbiguityJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceEventIdentityAmbiguityJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceEventIdentityAmbiguityJsonGzip)) THEN 1 ELSE 0 END)
FROM stg.MddMacTimingObservation
UNION ALL
SELECT 'situation_source_representation',
       SUM(CASE WHEN SourceRepresentationJson IS NOT NULL
                     AND SourceRepresentationJsonGzip IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN SourceRepresentationJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceRepresentationJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceRepresentationJsonGzip)) THEN 1 ELSE 0 END)
FROM stg.MddMacSituationEvidence
UNION ALL
SELECT 'service_section_source_representation',
       SUM(CASE WHEN SourceRepresentationJson IS NOT NULL
                     AND SourceRepresentationJsonGzip IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN SourceRepresentationJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceRepresentationJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceRepresentationJsonGzip)) THEN 1 ELSE 0 END)
FROM stg.MddMacServiceSectionEvidence
UNION ALL
SELECT 'attribute_source_representation',
       SUM(CASE WHEN SourceRepresentationJson IS NOT NULL
                     AND SourceRepresentationJsonGzip IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN SourceRepresentationJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceRepresentationJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceRepresentationJsonGzip)) THEN 1 ELSE 0 END)
FROM stg.MddMacAttributeEvidence
UNION ALL
SELECT 'parser_gap_source_representation',
       SUM(CASE WHEN SourceRepresentationJson IS NOT NULL
                     AND SourceRepresentationJsonGzip IS NOT NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN SourceRepresentationJsonGzip IS NOT NULL
                 AND HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), CONVERT(NVARCHAR(MAX), DECOMPRESS(SourceRepresentationJsonGzip))))
                     <> HASHBYTES('SHA2_256', DECOMPRESS(SourceRepresentationJsonGzip)) THEN 1 ELSE 0 END)
FROM stg.MddMacParserGap;

SELECT
    (SELECT COUNT_BIG(*) FROM stg.MddMacSourceSnapshot) AS snapshot_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacCallObservation) AS call_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacTimingObservation) AS timing_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacSituationEvidence) AS situation_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacServiceSectionEvidence) AS service_section_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacAttributeEvidence) AS attribute_rows,
    (SELECT COUNT_BIG(*) FROM stg.MddMacParserGap) AS parser_gap_rows;

SELECT TOP (100)
    TimingObservationId, SnapshotId, EventOrdinal, CallScope, CallOrdinal,
    TimingContext, EstimatedTimeSourceText, RecordedAtTimeSourceText,
    EstimatedTimeSourcePath, RecordedAtTimeSourcePath,
    SourceEventIdentity,
    SourceEventIdentityMissingFieldsJson,
    SourceEventIdentityAmbiguityJson
FROM stg.vMddMacTimingObservationJson
ORDER BY TimingObservationId DESC;

SELECT
    t.SnapshotId,
    COUNT_BIG(*) AS timing_rows,
    COUNT_BIG(CASE WHEN t.EstimatedTimeSourceText IS NOT NULL THEN 1 END) AS estimated_time_rows,
    COUNT_BIG(CASE WHEN t.RecordedAtTimeSourceText IS NOT NULL THEN 1 END) AS recorded_at_time_rows
FROM stg.vMddMacTimingObservationJson AS t
GROUP BY t.SnapshotId
ORDER BY t.SnapshotId DESC;
GO

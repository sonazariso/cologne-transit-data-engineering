-- Schema-4 reporting definition retained for the view-replacement regression.
-- The three RecordedAtTime lineage columns intentionally expose the broader
-- timing context path; schema 5 must replace them with the leaf path.
DROP VIEW IF EXISTS recorded_at_time_reporting;
DROP VIEW IF EXISTS timing_recorded_at_time_reporting;

CREATE VIEW timing_recorded_at_time_reporting AS
WITH base AS (
    SELECT
        timing_observation_id,
        source_event_identity,
        source_event_identity_status,
        CASE
            WHEN source_event_identity_status = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
                 AND source_event_identity IS NOT NULL
            THEN source_event_identity
            ELSE 'SOURCE_EVENT_IDENTITY_INSUFFICIENT:' || timing_observation_id
        END AS source_event_identity_group,
        recorded_at_time,
        observed_at_utc,
        snapshot_id,
        audit_id,
        source_path,
        panel_version,
        source_parent_station_id,
        logical_due_bucket,
        collection_context,
        event_ordinal,
        result_id,
        call_scope,
        call_ordinal,
        timing_context,
        timetabled_time,
        estimated_time,
        estimated_time_low,
        estimated_time_high,
        ROW_NUMBER() OVER (
            PARTITION BY CASE
                WHEN source_event_identity_status = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
                     AND source_event_identity IS NOT NULL
                THEN source_event_identity
                ELSE 'SOURCE_EVENT_IDENTITY_INSUFFICIENT:' || timing_observation_id
            END
            ORDER BY observed_at_utc DESC, timing_observation_id DESC
        ) AS latest_rank
    FROM timing_observations
), nonnull AS (
    SELECT
        base.*,
        ROW_NUMBER() OVER (
            PARTITION BY source_event_identity_group
            ORDER BY observed_at_utc DESC, timing_observation_id DESC
        ) AS last_nonnull_rank,
        ROW_NUMBER() OVER (
            PARTITION BY source_event_identity_group
            ORDER BY observed_at_utc ASC, timing_observation_id ASC
        ) AS first_nonnull_rank
    FROM base
    WHERE recorded_at_time IS NOT NULL
), counts AS (
    SELECT source_event_identity_group, COUNT(*) AS nonnull_count
    FROM nonnull
    GROUP BY source_event_identity_group
)
SELECT
    latest.source_event_identity AS SourceEventIdentity,
    latest.source_event_identity_status AS SourceEventIdentityStatus,
    latest.source_event_identity_group AS SourceEventIdentityGroup,
    latest.panel_version AS PanelVersion,
    latest.source_parent_station_id AS SourceParentStationId,
    latest.logical_due_bucket AS LogicalDueBucket,
    latest.collection_context AS CollectionContext,
    latest.event_ordinal AS EventOrdinal,
    latest.result_id AS ResultId,
    latest.call_scope AS CallScope,
    latest.call_ordinal AS CallOrdinal,
    latest.timing_context AS TimingContext,
    latest.timetabled_time AS LatestTimetabledTime,
    latest.estimated_time AS LatestEstimatedTime,
    latest.estimated_time_low AS LatestEstimatedTimeLow,
    latest.estimated_time_high AS LatestEstimatedTimeHigh,
    latest.recorded_at_time AS LatestSourceRecordedAtTime,
    latest.observed_at_utc AS LatestSourceRecordedAtTimeObservedAt,
    latest.snapshot_id AS LatestSourceRecordedAtTimeSnapshotId,
    latest.audit_id AS LatestSourceRecordedAtTimeAuditId,
    latest.source_path AS LatestSourceRecordedAtTimeSourcePath,
    latest.timing_observation_id AS LatestSourceRecordedAtTimeObservationId,
    last_nonnull.recorded_at_time AS LastObservedRecordedAtTime,
    last_nonnull.observed_at_utc AS LastObservedRecordedAtTimeObservedAt,
    last_nonnull.snapshot_id AS LastObservedRecordedAtTimeSnapshotId,
    last_nonnull.audit_id AS LastObservedRecordedAtTimeAuditId,
    last_nonnull.source_path AS LastObservedRecordedAtTimeSourcePath,
    last_nonnull.timing_observation_id AS LastObservedRecordedAtTimeObservationId,
    COALESCE(counts.nonnull_count, 0) AS RecordedAtTimeNonNullObservationCount,
    first_nonnull.recorded_at_time AS FirstObservedRecordedAtTime,
    first_nonnull.observed_at_utc AS FirstObservedRecordedAtTimeObservedAt,
    first_nonnull.snapshot_id AS FirstObservedRecordedAtTimeSnapshotId,
    first_nonnull.audit_id AS FirstObservedRecordedAtTimeAuditId,
    first_nonnull.source_path AS FirstObservedRecordedAtTimeSourcePath,
    first_nonnull.timing_observation_id AS FirstObservedRecordedAtTimeObservationId
FROM base AS latest
LEFT JOIN nonnull AS last_nonnull
    ON last_nonnull.source_event_identity_group = latest.source_event_identity_group
   AND last_nonnull.last_nonnull_rank = 1
LEFT JOIN nonnull AS first_nonnull
    ON first_nonnull.source_event_identity_group = latest.source_event_identity_group
   AND first_nonnull.first_nonnull_rank = 1
LEFT JOIN counts
    ON counts.source_event_identity_group = latest.source_event_identity_group
WHERE latest.latest_rank = 1;

CREATE VIEW recorded_at_time_reporting AS
    SELECT * FROM timing_recorded_at_time_reporting;

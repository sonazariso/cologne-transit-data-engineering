-- Stage 9 — Matching & Data Quality foundation
-- Target: PostgreSQL 18 (latest supported minor at implementation time)
--
-- This migration creates only matching/core/control structures.  It does not
-- create an operational fact, activate a collector, promote Actual Delay, or
-- change the approved Tiered50 production decision.  Source identifiers remain
-- namespace-qualified text and source observations remain append-only.

BEGIN;

CREATE SCHEMA IF NOT EXISTS ctl;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS wrk;

CREATE TABLE IF NOT EXISTS ctl.matching_run (
    matching_run_key       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matching_version       text NOT NULL,
    run_type               text NOT NULL DEFAULT 'STAGE_09_MATCHING',
    started_at_utc         timestamptz NOT NULL DEFAULT clock_timestamp(),
    ended_at_utc           timestamptz,
    status                 text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED', 'BLOCKED')),
    source_scope           text NOT NULL DEFAULT 'COLOGNE_STOP_EVENT_FIRST',
    configuration          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at_utc         timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS ctl.matching_validation_result (
    matching_validation_result_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matching_run_key               bigint NOT NULL REFERENCES ctl.matching_run(matching_run_key),
    check_code                     text NOT NULL,
    entity_type                    text NOT NULL,
    outcome                        text NOT NULL CHECK (outcome IN ('PASS', 'WARN', 'FAIL', 'NOT_EVALUATED')),
    observed_count                 bigint,
    expected_count                 bigint,
    metrics                        jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_ref                   text,
    evaluated_at_utc               timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (matching_run_key, check_code, entity_type)
);

CREATE TABLE IF NOT EXISTS ctl.data_quality_issue (
    data_quality_issue_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matching_run_key       bigint NOT NULL REFERENCES ctl.matching_run(matching_run_key),
    source_system          text,
    source_namespace       text,
    entity_type            text NOT NULL,
    entity_identity        text,
    classification         text NOT NULL CHECK (classification IN (
        'VALIDATED_MATCH',
        'CONTEXTUAL_MATCH',
        'WEAK_CONTEXT_MATCH',
        'AMBIGUOUS_MATCH',
        'UNMATCHED',
        'MISSING_REQUIRED_IDENTITY',
        'STATIC_COVERAGE_GAP',
        'STATIC_STOP_MISSING',
        'STATIC_ROUTE_MISSING',
        'STATIC_SERVICE_DATE_NOT_ACTIVE',
        'STATIC_EVENT_NOT_FOUND',
        'SOURCE_IDENTITY_UNRESOLVED',
        'SOURCE_LINE_IDENTITY_UNRESOLVED',
        'SOURCE_LINE_DISAGREEMENT',
        'IDENTITY_CONFLICT',
        'AMBIGUOUS_MULTIPLE_CANDIDATES',
        'INSUFFICIENT_EVIDENCE',
        'INVALID_SERVICE_DATE',
        'INVALID_SCHEDULE_TIME',
        'LINEAGE_INCOMPLETE',
        'DUPLICATE_SOURCE_OBSERVATION',
        'CROSS_SOURCE_DISAGREEMENT',
        'OUT_OF_CORE_SCOPE'
    )),
    severity               text NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR')),
    field_name             text,
    source_path            text,
    detail                 text,
    evidence               jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at_utc        timestamptz,
    recorded_at_utc        timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS core.canonical_stop (
    canonical_stop_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mapping_version    text NOT NULL,
    canonical_stop_id  text NOT NULL,
    display_name       text,
    cologne_scope_code text NOT NULL CHECK (cologne_scope_code IN ('IN_SCOPE', 'OUT_OF_SCOPE', 'UNRESOLVED')),
    mapping_status     text NOT NULL CHECK (mapping_status IN ('VALIDATED', 'UNRESOLVED', 'AMBIGUOUS', 'RETIRED')),
    valid_from         date,
    valid_to           date,
    evidence           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mapping_version, canonical_stop_id),
    UNIQUE (mapping_version, canonical_stop_key),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS core.canonical_stop_source_identity (
    canonical_stop_source_identity_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mapping_version                    text NOT NULL,
    canonical_stop_key                 bigint REFERENCES core.canonical_stop(canonical_stop_key),
    source_system                      text NOT NULL,
    source_namespace                   text NOT NULL,
    source_stop_id                     text NOT NULL,
    mapping_status                     text NOT NULL CHECK (mapping_status IN ('VALIDATED', 'UNRESOLVED', 'AMBIGUOUS', 'RETIRED')),
    mapping_method                     text NOT NULL,
    match_confidence                   text NOT NULL CHECK (match_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'UNMATCHED')),
    evidence_ref                       text,
    valid_from                         date,
    valid_to                           date,
    evidence                           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mapping_version, source_system, source_namespace, source_stop_id),
    FOREIGN KEY (mapping_version, canonical_stop_key)
        REFERENCES core.canonical_stop(mapping_version, canonical_stop_key),
    CHECK (mapping_status <> 'VALIDATED' OR canonical_stop_key IS NOT NULL),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS core.canonical_line (
    canonical_line_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mapping_version    text NOT NULL,
    canonical_line_id  text NOT NULL,
    display_name       text,
    mode_code          text,
    operator_id        text,
    mapping_status     text NOT NULL CHECK (mapping_status IN ('VALIDATED', 'UNRESOLVED', 'AMBIGUOUS', 'RETIRED')),
    valid_from         date,
    valid_to           date,
    evidence           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mapping_version, canonical_line_id),
    UNIQUE (mapping_version, canonical_line_key),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS core.canonical_line_source_identity (
    canonical_line_source_identity_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mapping_version                    text NOT NULL,
    canonical_line_key                 bigint REFERENCES core.canonical_line(canonical_line_key),
    source_system                      text NOT NULL,
    source_namespace                   text NOT NULL,
    source_line_id                     text NOT NULL,
    mapping_status                     text NOT NULL CHECK (mapping_status IN ('VALIDATED', 'UNRESOLVED', 'AMBIGUOUS', 'RETIRED')),
    mapping_method                     text NOT NULL,
    match_confidence                   text NOT NULL CHECK (match_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'UNMATCHED')),
    evidence_ref                       text,
    valid_from                         date,
    valid_to                           date,
    evidence                           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mapping_version, source_system, source_namespace, source_line_id),
    FOREIGN KEY (mapping_version, canonical_line_key)
        REFERENCES core.canonical_line(mapping_version, canonical_line_key),
    CHECK (mapping_status <> 'VALIDATED' OR canonical_line_key IS NOT NULL),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS core.canonical_service_event (
    canonical_service_event_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matching_version            text NOT NULL,
    feed_snapshot_key           text NOT NULL,
    canonical_service_event_id  text NOT NULL,
    service_date                date NOT NULL,
    canonical_stop_id           text NOT NULL,
    canonical_line_id           text,
    event_type                  text NOT NULL CHECK (event_type IN ('ARRIVAL', 'DEPARTURE')),
    scheduled_time_seconds      integer NOT NULL CHECK (scheduled_time_seconds >= 0),
    scheduled_time_source       text,
    direction                   text,
    stop_sequence               integer,
    operator_id                 text,
    source_namespace            text NOT NULL,
    source_trip_id              text,
    source_route_id             text,
    source_stop_id              text,
    source_row_identity         text,
    sequence_signature          jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (matching_version, feed_snapshot_key, canonical_service_event_id),
    UNIQUE (matching_version, canonical_service_event_key)
);

CREATE TABLE IF NOT EXISTS core.source_event_match (
    source_event_match_key       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    matching_run_key             bigint NOT NULL REFERENCES ctl.matching_run(matching_run_key),
    matching_version             text NOT NULL,
    source_system                text NOT NULL,
    source_namespace             text NOT NULL,
    source_event_identity        text,
    source_observation_identity  text NOT NULL CHECK (length(btrim(source_observation_identity)) > 0),
    stop_mapping_version         text,
    line_mapping_version         text,
    source_stop_id               text,
    source_line_id               text,
    source_trip_id               text,
    source_journey_ref            text,
    vehicle_journey_ref           text,
    dated_vehicle_journey_ref    text,
    affected_vehicle_journey     jsonb,
    vehicle_journeys             jsonb,
    operating_days_description   text,
    source_direction              text,
    source_stop_sequence          integer,
    source_scheduled_time_seconds integer,
    source_response_id            text,
    source_observed_at_utc        timestamptz,
    correlation_group_id          text,
    collector_run_id               text,
    sampling_policy_id             text,
    sampling_era_id                text,
    correlation_basis               text,
    correlation_validated           boolean NOT NULL DEFAULT false,
    candidate_identifiers          jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_paths                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    canonical_service_event_key  bigint REFERENCES core.canonical_service_event(canonical_service_event_key),
    FOREIGN KEY (matching_version, canonical_service_event_key)
        REFERENCES core.canonical_service_event(matching_version, canonical_service_event_key),
    match_confidence             text NOT NULL CHECK (match_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'UNMATCHED')),
    match_method                 text NOT NULL CHECK (match_method IN (
        'EXACT_STOP_DATE_TIME_DIRECTION',
        'EXACT_STOP_DATE_TIME_SEQUENCE',
        'MULTI_STOP_SEQUENCE_MATCH',
        'ROUTE_TIME_CONTEXT_ONLY',
        'STOP_TIME_CONTEXT_ONLY',
        'UNMATCHED'
    )),
    data_quality_classification  text NOT NULL CHECK (data_quality_classification IN (
        'VALIDATED_MATCH',
        'CONTEXTUAL_MATCH',
        'WEAK_CONTEXT_MATCH',
        'AMBIGUOUS_MATCH',
        'UNMATCHED',
        'MISSING_REQUIRED_IDENTITY',
        'STATIC_COVERAGE_GAP',
        'STATIC_STOP_MISSING',
        'STATIC_ROUTE_MISSING',
        'STATIC_SERVICE_DATE_NOT_ACTIVE',
        'STATIC_EVENT_NOT_FOUND',
        'SOURCE_IDENTITY_UNRESOLVED',
        'SOURCE_LINE_IDENTITY_UNRESOLVED',
        'SOURCE_LINE_DISAGREEMENT',
        'IDENTITY_CONFLICT',
        'AMBIGUOUS_MULTIPLE_CANDIDATES',
        'INSUFFICIENT_EVIDENCE',
        'INVALID_SERVICE_DATE',
        'INVALID_SCHEDULE_TIME',
        'LINEAGE_INCOMPLETE',
        'DUPLICATE_SOURCE_OBSERVATION',
        'CROSS_SOURCE_DISAGREEMENT',
        'OUT_OF_CORE_SCOPE'
    )),
    candidate_count              integer NOT NULL CHECK (candidate_count >= 0),
    selected_score               integer,
    candidate_event_ids          jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    decided_at_utc               timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (matching_run_key, source_system, source_namespace, source_observation_identity),
    CHECK (
        (match_confidence = 'UNMATCHED'
         AND match_method = 'UNMATCHED'
         AND canonical_service_event_key IS NULL)
        OR
        (match_confidence IN ('HIGH', 'MEDIUM', 'LOW')
         AND match_method <> 'UNMATCHED'
         AND canonical_service_event_key IS NOT NULL)
    ),
    CHECK (canonical_service_event_key IS NULL OR stop_mapping_version IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS core.source_event_match_candidate (
    source_event_match_candidate_key bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_event_match_key           bigint NOT NULL REFERENCES core.source_event_match(source_event_match_key),
    candidate_rank                   integer NOT NULL CHECK (candidate_rank > 0),
    canonical_service_event_key      bigint REFERENCES core.canonical_service_event(canonical_service_event_key),
    candidate_score                  integer NOT NULL,
    selected                        boolean NOT NULL DEFAULT false,
    rejection_reason                 text,
    evidence                         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_event_match_key, candidate_rank),
    CHECK (NOT selected OR canonical_service_event_key IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_core_canonical_service_event_lookup
    ON core.canonical_service_event
       (matching_version, service_date, canonical_stop_id, event_type, scheduled_time_seconds);

CREATE INDEX IF NOT EXISTS ix_core_canonical_service_event_line_lookup
    ON core.canonical_service_event
       (matching_version, service_date, canonical_line_id, canonical_stop_id, event_type, scheduled_time_seconds);

CREATE INDEX IF NOT EXISTS ix_core_canonical_service_event_trip_lookup
    ON core.canonical_service_event
       (feed_snapshot_key, source_namespace, source_trip_id, service_date, stop_sequence);

CREATE INDEX IF NOT EXISTS ix_core_stop_source_identity_lookup
    ON core.canonical_stop_source_identity
       (mapping_version, source_system, source_namespace, source_stop_id);

CREATE INDEX IF NOT EXISTS ix_core_line_source_identity_lookup
    ON core.canonical_line_source_identity
       (mapping_version, source_system, source_namespace, source_line_id);

CREATE INDEX IF NOT EXISTS ix_core_stop_source_identity_validity
    ON core.canonical_stop_source_identity
       (source_system, source_namespace, source_stop_id, mapping_version, valid_from, valid_to);

CREATE INDEX IF NOT EXISTS ix_core_line_source_identity_validity
    ON core.canonical_line_source_identity
       (source_system, source_namespace, source_line_id, mapping_version, valid_from, valid_to);

CREATE INDEX IF NOT EXISTS ix_core_source_event_match_review
    ON core.source_event_match
       (matching_run_key, source_system, sampling_era_id, match_confidence, data_quality_classification);

CREATE INDEX IF NOT EXISTS ix_core_source_event_match_event
    ON core.source_event_match
       (canonical_service_event_key, source_system, decided_at_utc);

CREATE INDEX IF NOT EXISTS ix_core_source_event_match_observation_lineage
    ON core.source_event_match
       (source_system, source_namespace, source_observed_at_utc, sampling_era_id);

CREATE INDEX IF NOT EXISTS ix_core_match_candidate_rank
    ON core.source_event_match_candidate
       (source_event_match_key, candidate_rank, candidate_score DESC);

CREATE INDEX IF NOT EXISTS ix_ctl_data_quality_issue_review
    ON ctl.data_quality_issue
       (matching_run_key, classification, severity, source_system, observed_at_utc);

-- These source indexes are conditional because the Stage 5 source tables are
-- created/loaded by their owning source implementation.  If those tables are
-- present, the indexes make the Stage 9 lookup contract explicit without
-- forcing an empty source layer to be created by this migration.
DO $$
BEGIN
    IF to_regclass('src.mdd_stop_event_observation') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS vehicle_journey_ref text';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS dated_vehicle_journey_ref text';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS affected_vehicle_journey jsonb';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS vehicle_journeys jsonb';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS operating_days_description text';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS source_direction text';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS origins jsonb';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS destinations jsonb';
        EXECUTE 'ALTER TABLE src.mdd_stop_event_observation ADD COLUMN IF NOT EXISTS source_identity_extensions jsonb';
    END IF;
    IF to_regclass('src.mdd_stop_event_observation') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_src_mdd_stop_event_matching_lookup ON src.mdd_stop_event_observation (operating_day_ref, stop_point_ref, line_ref, journey_ref)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_src_mdd_stop_event_richer_identity ON src.mdd_stop_event_observation (operating_day_ref, dated_vehicle_journey_ref, vehicle_journey_ref)';
    END IF;
    IF to_regclass('src.mdd_call_observation') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_src_mdd_call_matching_lookup ON src.mdd_call_observation (observed_at_utc, stop_point_ref, stop_seq_number, call_scope)';
    END IF;
    IF to_regclass('src.gtfsrt_trip_update_observation') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_src_gtfsrt_trip_matching_lookup ON src.gtfsrt_trip_update_observation (observed_at_utc, trip_id, route_id)';
    END IF;
    IF to_regclass('src.gtfsrt_stop_time_update_observation') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS ix_src_gtfsrt_stop_time_matching_lookup ON src.gtfsrt_stop_time_update_observation (observed_at_utc, trip_id, stop_id, stop_sequence)';
    END IF;
END $$;

CREATE OR REPLACE VIEW wrk.v_source_event_match_review AS
SELECT
    sem.source_event_match_key,
    sem.matching_run_key,
    sem.matching_version,
    sem.source_system,
    sem.source_namespace,
    sem.source_event_identity,
    sem.source_observation_identity,
    sem.stop_mapping_version,
    sem.line_mapping_version,
    sem.correlation_basis,
    sem.correlation_validated,
    sem.sampling_policy_id,
    sem.sampling_era_id,
    sem.canonical_service_event_key,
    sem.match_confidence,
    sem.match_method,
    sem.data_quality_classification,
    sem.candidate_count,
    sem.selected_score,
    candidate.candidate_rank,
    candidate.canonical_service_event_key AS candidate_canonical_service_event_key,
    candidate.candidate_score,
    candidate.selected,
    candidate.rejection_reason,
    sem.evidence AS match_evidence,
    candidate.evidence AS candidate_evidence
FROM core.source_event_match AS sem
LEFT JOIN core.source_event_match_candidate AS candidate
    ON candidate.source_event_match_key = sem.source_event_match_key;

CREATE OR REPLACE VIEW wrk.v_data_quality_summary AS
SELECT
    matching_run_key,
    source_system,
    classification,
    severity,
    count(*) AS issue_count,
    min(recorded_at_utc) AS first_recorded_at_utc,
    max(recorded_at_utc) AS last_recorded_at_utc
FROM ctl.data_quality_issue
GROUP BY matching_run_key, source_system, classification, severity;

CREATE OR REPLACE VIEW wrk.v_canonical_service_event_coverage AS
SELECT
    matching_version,
    feed_snapshot_key,
    service_date,
    canonical_stop_id,
    canonical_line_id,
    event_type,
    count(*) AS canonical_event_count
FROM core.canonical_service_event
GROUP BY matching_version, feed_snapshot_key, service_date,
         canonical_stop_id, canonical_line_id, event_type;

CREATE OR REPLACE VIEW wrk.v_identity_mapping_overlap_conflicts AS
SELECT
    'STOP'::text AS identity_type,
    left_row.canonical_stop_source_identity_key AS left_identity_key,
    right_row.canonical_stop_source_identity_key AS right_identity_key,
    left_row.mapping_version,
    left_row.source_system,
    left_row.source_namespace,
    left_row.source_stop_id AS source_identifier,
    left_row.canonical_stop_key AS left_canonical_key,
    right_row.canonical_stop_key AS right_canonical_key,
    (left_row.canonical_stop_key IS DISTINCT FROM right_row.canonical_stop_key) AS conflicting
FROM core.canonical_stop_source_identity AS left_row
JOIN core.canonical_stop_source_identity AS right_row
  ON right_row.canonical_stop_source_identity_key > left_row.canonical_stop_source_identity_key
 AND right_row.mapping_version = left_row.mapping_version
 AND right_row.source_system = left_row.source_system
 AND right_row.source_namespace = left_row.source_namespace
 AND right_row.source_stop_id = left_row.source_stop_id
 AND (left_row.valid_from IS NULL OR right_row.valid_to IS NULL OR left_row.valid_from <= right_row.valid_to)
 AND (right_row.valid_from IS NULL OR left_row.valid_to IS NULL OR right_row.valid_from <= left_row.valid_to)
UNION ALL
SELECT
    'LINE'::text AS identity_type,
    left_row.canonical_line_source_identity_key AS left_identity_key,
    right_row.canonical_line_source_identity_key AS right_identity_key,
    left_row.mapping_version,
    left_row.source_system,
    left_row.source_namespace,
    left_row.source_line_id AS source_identifier,
    left_row.canonical_line_key AS left_canonical_key,
    right_row.canonical_line_key AS right_canonical_key,
    (left_row.canonical_line_key IS DISTINCT FROM right_row.canonical_line_key) AS conflicting
FROM core.canonical_line_source_identity AS left_row
JOIN core.canonical_line_source_identity AS right_row
  ON right_row.canonical_line_source_identity_key > left_row.canonical_line_source_identity_key
 AND right_row.mapping_version = left_row.mapping_version
 AND right_row.source_system = left_row.source_system
 AND right_row.source_namespace = left_row.source_namespace
 AND right_row.source_line_id = left_row.source_line_id
 AND (left_row.valid_from IS NULL OR right_row.valid_to IS NULL OR left_row.valid_from <= right_row.valid_to)
 AND (right_row.valid_from IS NULL OR left_row.valid_to IS NULL OR right_row.valid_from <= left_row.valid_to);

COMMENT ON TABLE core.canonical_stop IS
    'Versioned canonical stop identities; unresolved and ambiguous mappings remain explicit.';
COMMENT ON TABLE core.canonical_line IS
    'Versioned canonical line identities; source route/line namespaces remain distinct.';
COMMENT ON TABLE core.canonical_service_event IS
    'Stop-event-first canonical schedule evidence; not an operational outcome fact.';
COMMENT ON TABLE core.source_event_match IS
    'Versioned source observation decisions, including UNMATCHED and ambiguous outcomes.';
COMMENT ON TABLE core.source_event_match_candidate IS
    'Auditable candidate evidence retained for deterministic matching review.';
COMMENT ON TABLE ctl.data_quality_issue IS
    'Append-only Stage 9 data-quality classifications; no issue is silently discarded.';

COMMIT;

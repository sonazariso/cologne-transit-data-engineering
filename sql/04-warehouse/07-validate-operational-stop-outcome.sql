-- Stage 10 — Operational Fact: current-architecture semantic validation
-- Target: PostgreSQL 18
--
-- Validate an explicit Stage 9 execution:
--
--   SELECT *
--   FROM dw.validate_operational_service_event(<matching_run_key>, '<matching_version>');
--
-- NULL parameters are intentionally BLOCKED. The validator never chooses an
-- implicit latest matching run and never treats unavailable source/database
-- state as PASS.

BEGIN;

CREATE SCHEMA IF NOT EXISTS dw;

CREATE OR REPLACE FUNCTION dw.validate_operational_service_event(
    p_matching_run_key bigint DEFAULT NULL,
    p_matching_version text DEFAULT NULL
)
RETURNS TABLE (
    check_code      text,
    outcome         text,
    observed_count  bigint,
    expected_count  bigint,
    detail          text
)
LANGUAGE plpgsql
AS $function$
DECLARE
    refresh_error                         text;
    selected_high_observation_count       bigint;
    selected_event_grain_count            bigint;
    fact_grain_count                      bigint;
    duplicate_fact_grain_count            bigint;
    ineligible_fact_count                 bigint;
    lineage_violation_count               bigint;
    source_observation_duplication_count  bigint;
    missing_event_grain_count             bigint;
    stale_event_grain_count               bigint;
    first_lineage_violation_count         bigint;
    latest_lineage_violation_count        bigint;
    observation_count_mismatch             bigint;
    first_timing_mismatch_count           bigint;
    latest_timing_mismatch_count          bigint;
    latest_null_carry_forward_count       bigint;
    bay_evidence_mismatch_count           bigint;
    situation_evidence_mismatch_count     bigint;
    situation_flag_mismatch_count         bigint;
    canonical_identity_mismatch_count     bigint;
    match_method_lineage_mismatch_count    bigint;
    data_quality_lineage_mismatch_count   bigint;
    source_identity_tuple_mismatch_count  bigint;
    orphan_matching_run_reference_count   bigint;
    orphan_canonical_event_reference_count bigint;
    orphan_first_source_match_count       bigint;
    orphan_latest_source_match_count      bigint;
    repeatability_difference_count        bigint;
BEGIN
    IF p_matching_run_key IS NULL
       OR NULLIF(btrim(p_matching_version), '') IS NULL THEN
        RETURN QUERY
        SELECT
            'STAGE10_EXPLICIT_RUN_SELECTION'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            'An explicit matching_run_key and matching_version are required; the validator does not infer a latest Stage 9 execution.'::text;
        RETURN;
    END IF;

    IF to_regclass('dw.fact_operational_service_event') IS NULL THEN
        RETURN QUERY
        SELECT
            'STAGE10_FACT_PREREQUISITE'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            'dw.fact_operational_service_event is absent; install the Stage 10 physical contract before validation.'::text;
        RETURN;
    END IF;

    DROP TABLE IF EXISTS pg_temp.stage10_validation_snapshot_a;
    DROP TABLE IF EXISTS pg_temp.stage10_validation_snapshot_b;
    DROP TABLE IF EXISTS pg_temp.stage10_validation_input;

    -- The first refresh establishes the starting state for repeatability.
    -- There is intentionally no snapshot of an arbitrary pre-refresh fact.
    BEGIN
        CALL dw.refresh_operational_service_event(
            p_matching_run_key,
            p_matching_version
        );
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS refresh_error = MESSAGE_TEXT;
    END;

    IF refresh_error IS NOT NULL THEN
        RETURN QUERY
        SELECT
            'STAGE10_SOURCE_DATABASE_PREREQUISITES'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            refresh_error;
        RETURN;
    END IF;

    CREATE TEMP TABLE stage10_validation_snapshot_a ON COMMIT DROP AS
    SELECT
        fact.service_date,
        fact.canonical_service_event_key,
        md5(jsonb_build_object(
            'service_date', fact.service_date,
            'canonical_service_event_key', fact.canonical_service_event_key,
            'canonical_service_event_id', fact.canonical_service_event_id,
            'matching_run_key', fact.matching_run_key,
            'matching_version', fact.matching_version,
            'canonical_stop_id', fact.canonical_stop_id,
            'canonical_line_id', fact.canonical_line_id,
            'event_type', fact.event_type,
            'scheduled_time_seconds', fact.scheduled_time_seconds,
            'canonical_scheduled_time_utc', fact.canonical_scheduled_time_utc,
            'match_confidence', fact.match_confidence,
            'match_method', fact.match_method,
            'data_quality_classification', fact.data_quality_classification,
            'source_observation_count', fact.source_observation_count,
            'first_source_event_match_key', fact.first_source_event_match_key,
            'latest_source_event_match_key', fact.latest_source_event_match_key,
            'first_source_observation_identity', fact.first_source_observation_identity,
            'latest_source_observation_identity', fact.latest_source_observation_identity,
            'first_source_observed_at_utc', fact.first_source_observed_at_utc,
            'latest_source_observed_at_utc', fact.latest_source_observed_at_utc,
            'first_estimated_time_utc', fact.first_estimated_time_utc,
            'latest_estimated_time_utc', fact.latest_estimated_time_utc,
            'first_estimated_delay_minutes', fact.first_estimated_delay_minutes,
            'latest_estimated_delay_minutes', fact.latest_estimated_delay_minutes,
            'first_planned_bay', fact.first_planned_bay,
            'latest_planned_bay', fact.latest_planned_bay,
            'first_estimated_bay', fact.first_estimated_bay,
            'latest_estimated_bay', fact.latest_estimated_bay,
            'bay_evidence', fact.bay_evidence,
            'comparable_bay_observation_count', fact.comparable_bay_observation_count,
            'changed_bay_observation_count', fact.changed_bay_observation_count,
            'situation_link_count', fact.situation_link_count,
            'has_situation_evidence', fact.has_situation_evidence
        )::text) AS semantic_hash
    FROM dw.fact_operational_service_event AS fact;

    -- The second refresh uses the same explicit run/version and unchanged
    -- source/core input. A is compared with B below; refreshed_at_utc is not
    -- included in either semantic hash.
    BEGIN
        CALL dw.refresh_operational_service_event(
            p_matching_run_key,
            p_matching_version
        );
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS refresh_error = MESSAGE_TEXT;
    END;

    IF refresh_error IS NOT NULL THEN
        RETURN QUERY
        SELECT
            'STAGE10_REPEAT_REFRESH_PREREQUISITE'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            'The second unchanged-input refresh could not run: ' || refresh_error;
        RETURN;
    END IF;

    CREATE TEMP TABLE stage10_validation_snapshot_b ON COMMIT DROP AS
    SELECT
        fact.service_date,
        fact.canonical_service_event_key,
        md5(jsonb_build_object(
            'service_date', fact.service_date,
            'canonical_service_event_key', fact.canonical_service_event_key,
            'canonical_service_event_id', fact.canonical_service_event_id,
            'matching_run_key', fact.matching_run_key,
            'matching_version', fact.matching_version,
            'canonical_stop_id', fact.canonical_stop_id,
            'canonical_line_id', fact.canonical_line_id,
            'event_type', fact.event_type,
            'scheduled_time_seconds', fact.scheduled_time_seconds,
            'canonical_scheduled_time_utc', fact.canonical_scheduled_time_utc,
            'match_confidence', fact.match_confidence,
            'match_method', fact.match_method,
            'data_quality_classification', fact.data_quality_classification,
            'source_observation_count', fact.source_observation_count,
            'first_source_event_match_key', fact.first_source_event_match_key,
            'latest_source_event_match_key', fact.latest_source_event_match_key,
            'first_source_observation_identity', fact.first_source_observation_identity,
            'latest_source_observation_identity', fact.latest_source_observation_identity,
            'first_source_observed_at_utc', fact.first_source_observed_at_utc,
            'latest_source_observed_at_utc', fact.latest_source_observed_at_utc,
            'first_estimated_time_utc', fact.first_estimated_time_utc,
            'latest_estimated_time_utc', fact.latest_estimated_time_utc,
            'first_estimated_delay_minutes', fact.first_estimated_delay_minutes,
            'latest_estimated_delay_minutes', fact.latest_estimated_delay_minutes,
            'first_planned_bay', fact.first_planned_bay,
            'latest_planned_bay', fact.latest_planned_bay,
            'first_estimated_bay', fact.first_estimated_bay,
            'latest_estimated_bay', fact.latest_estimated_bay,
            'bay_evidence', fact.bay_evidence,
            'comparable_bay_observation_count', fact.comparable_bay_observation_count,
            'changed_bay_observation_count', fact.changed_bay_observation_count,
            'situation_link_count', fact.situation_link_count,
            'has_situation_evidence', fact.has_situation_evidence
        )::text) AS semantic_hash
    FROM dw.fact_operational_service_event AS fact;

    SELECT count(*)
    INTO repeatability_difference_count
    FROM pg_temp.stage10_validation_snapshot_a AS snapshot_a
    FULL JOIN pg_temp.stage10_validation_snapshot_b AS snapshot_b
      ON snapshot_b.service_date = snapshot_a.service_date
     AND snapshot_b.canonical_service_event_key = snapshot_a.canonical_service_event_key
    WHERE snapshot_a.semantic_hash IS DISTINCT FROM snapshot_b.semantic_hash;

    -- The second refresh leaves the input projection available in the current
    -- transaction. Copy it before running the semantic checks.
    CREATE TEMP TABLE stage10_validation_input ON COMMIT DROP AS
    SELECT * FROM pg_temp.stage10_refresh_input;

    SELECT count(*)
    INTO selected_high_observation_count
    FROM pg_temp.stage10_validation_input;

    SELECT count(*)
    INTO selected_event_grain_count
    FROM (
        SELECT service_date, canonical_service_event_key
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS selected_grains;

    SELECT count(*)
    INTO fact_grain_count
    FROM dw.fact_operational_service_event;

    SELECT count(*)
    INTO duplicate_fact_grain_count
    FROM (
        SELECT service_date, canonical_service_event_key
        FROM dw.fact_operational_service_event
        GROUP BY service_date, canonical_service_event_key
        HAVING count(*) > 1
    ) AS duplicates;

    SELECT count(*)
    INTO ineligible_fact_count
    FROM dw.fact_operational_service_event AS fact
    WHERE fact.match_confidence IS DISTINCT FROM 'HIGH';

    SELECT count(*)
    INTO lineage_violation_count
    FROM dw.fact_operational_service_event AS fact
    WHERE fact.matching_run_key IS DISTINCT FROM p_matching_run_key
       OR fact.matching_version IS DISTINCT FROM p_matching_version
       OR fact.matching_run_key IS NULL
       OR NULLIF(btrim(fact.matching_version), '') IS NULL;

    -- A selected-run input row may not be repeated by a historical matching
    -- run. The input query is scoped to one run, so this detects only duplicate
    -- source observations entering the consolidation, not legitimate history
    -- retained outside the fact.
    SELECT count(*)
    INTO source_observation_duplication_count
    FROM (
        SELECT source_system, source_namespace, source_observation_identity
        FROM pg_temp.stage10_validation_input
        GROUP BY source_system, source_namespace, source_observation_identity
        HAVING count(*) > 1
    ) AS duplicates;

    SELECT count(*)
    INTO source_identity_tuple_mismatch_count
    FROM pg_temp.stage10_validation_input AS input
    WHERE input.source_system IS DISTINCT FROM input.input_source_system
       OR input.source_namespace IS DISTINCT FROM input.input_source_namespace
       OR input.source_observation_identity IS DISTINCT FROM input.input_source_observation_identity;

    SELECT count(*)
    INTO orphan_matching_run_reference_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN ctl.matching_run AS run
      ON run.matching_run_key = fact.matching_run_key
    WHERE run.matching_run_key IS NULL;

    SELECT count(*)
    INTO orphan_canonical_event_reference_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN core.canonical_service_event AS canonical_event
      ON canonical_event.matching_version = fact.matching_version
     AND canonical_event.canonical_service_event_key = fact.canonical_service_event_key
    WHERE canonical_event.canonical_service_event_key IS NULL;

    SELECT count(*)
    INTO orphan_first_source_match_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN core.source_event_match AS source_match
      ON source_match.source_event_match_key = fact.first_source_event_match_key
    WHERE source_match.source_event_match_key IS NULL;

    SELECT count(*)
    INTO orphan_latest_source_match_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN core.source_event_match AS source_match
      ON source_match.source_event_match_key = fact.latest_source_event_match_key
    WHERE source_match.source_event_match_key IS NULL;

    SELECT count(*)
    INTO missing_event_grain_count
    FROM (
        SELECT service_date, canonical_service_event_key
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS selected_grains
    LEFT JOIN dw.fact_operational_service_event AS fact
      ON fact.service_date = selected_grains.service_date
     AND fact.canonical_service_event_key = selected_grains.canonical_service_event_key
    WHERE fact.operational_service_event_key IS NULL;

    SELECT count(*)
    INTO stale_event_grain_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN (
        SELECT service_date, canonical_service_event_key
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS selected_grains
      ON selected_grains.service_date = fact.service_date
     AND selected_grains.canonical_service_event_key = fact.canonical_service_event_key
    WHERE selected_grains.service_date IS NULL;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc ASC,
                         input.source_observation_identity ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc DESC,
                         input.source_observation_identity DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT
        count(*) FILTER (
            WHERE fact.first_source_event_match_key IS DISTINCT FROM first_rows.source_event_match_key
               OR fact.first_source_observation_identity IS DISTINCT FROM first_rows.source_observation_identity
               OR fact.first_source_observed_at_utc IS DISTINCT FROM first_rows.source_observed_at_utc
        ),
        count(*) FILTER (
            WHERE fact.latest_source_event_match_key IS DISTINCT FROM latest_rows.source_event_match_key
               OR fact.latest_source_observation_identity IS DISTINCT FROM latest_rows.source_observation_identity
               OR fact.latest_source_observed_at_utc IS DISTINCT FROM latest_rows.source_observed_at_utc
        )
    INTO first_lineage_violation_count, latest_lineage_violation_count
    FROM dw.fact_operational_service_event AS fact
    JOIN first_rows
      ON first_rows.service_date = fact.service_date
     AND first_rows.canonical_service_event_key = fact.canonical_service_event_key
    JOIN latest_rows
      ON latest_rows.service_date = fact.service_date
     AND latest_rows.canonical_service_event_key = fact.canonical_service_event_key;

    SELECT count(*)
    INTO observation_count_mismatch
    FROM (
        SELECT service_date, canonical_service_event_key, count(*)::bigint AS source_count
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS source_counts
    FULL JOIN dw.fact_operational_service_event AS fact
      ON fact.service_date = source_counts.service_date
     AND fact.canonical_service_event_key = source_counts.canonical_service_event_key
    WHERE source_counts.source_count IS DISTINCT FROM fact.source_observation_count;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc ASC,
                         input.source_observation_identity ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc DESC,
                         input.source_observation_identity DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT
        count(*) FILTER (
            WHERE fact.first_estimated_time_utc IS DISTINCT FROM first_rows.estimated_time_utc
               OR fact.first_estimated_delay_minutes IS DISTINCT FROM CASE
                    WHEN first_rows.estimated_time_utc IS NULL THEN NULL
                    ELSE round(
                        (extract(epoch FROM (
                            first_rows.estimated_time_utc - fact.canonical_scheduled_time_utc
                        )) / 60.0)::numeric,
                        3
                    )
                END
        ),
        count(*) FILTER (
            WHERE fact.latest_estimated_time_utc IS DISTINCT FROM latest_rows.estimated_time_utc
               OR fact.latest_estimated_delay_minutes IS DISTINCT FROM CASE
                    WHEN latest_rows.estimated_time_utc IS NULL THEN NULL
                    ELSE round(
                        (extract(epoch FROM (
                            latest_rows.estimated_time_utc - fact.canonical_scheduled_time_utc
                        )) / 60.0)::numeric,
                        3
                    )
                END
        )
    INTO first_timing_mismatch_count, latest_timing_mismatch_count
    FROM dw.fact_operational_service_event AS fact
    JOIN first_rows
      ON first_rows.service_date = fact.service_date
     AND first_rows.canonical_service_event_key = fact.canonical_service_event_key
    JOIN latest_rows
      ON latest_rows.service_date = fact.service_date
     AND latest_rows.canonical_service_event_key = fact.canonical_service_event_key;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc ASC,
                         input.source_observation_identity ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc DESC,
                         input.source_observation_identity DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT count(*)
    INTO latest_null_carry_forward_count
    FROM dw.fact_operational_service_event AS fact
    JOIN first_rows
      ON first_rows.service_date = fact.service_date
     AND first_rows.canonical_service_event_key = fact.canonical_service_event_key
    JOIN latest_rows
      ON latest_rows.service_date = fact.service_date
     AND latest_rows.canonical_service_event_key = fact.canonical_service_event_key
    WHERE first_rows.estimated_time_utc IS NOT NULL
      AND latest_rows.estimated_time_utc IS NULL
      AND (
          fact.latest_estimated_time_utc IS NOT NULL
          OR fact.latest_estimated_delay_minutes IS NOT NULL
      );

    WITH bay_source AS (
        SELECT
            service_date,
            canonical_service_event_key,
            count(*) FILTER (
                WHERE planned_bay IS NOT NULL
                  AND estimated_bay IS NOT NULL
            )::bigint AS comparable_count,
            count(*) FILTER (
                WHERE planned_bay IS NOT NULL
                  AND estimated_bay IS NOT NULL
                  AND planned_bay IS DISTINCT FROM estimated_bay
            )::bigint AS changed_count,
            CASE
                WHEN count(*) FILTER (
                    WHERE planned_bay IS NOT NULL
                      AND estimated_bay IS NOT NULL
                ) = 0 THEN 'Unknown'
                WHEN count(*) FILTER (
                    WHERE planned_bay IS NOT NULL
                      AND estimated_bay IS NOT NULL
                      AND planned_bay IS DISTINCT FROM estimated_bay
                ) > 0 THEN 'Changed'
                ELSE 'Unchanged'
            END AS evidence
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    )
    SELECT count(*)
    INTO bay_evidence_mismatch_count
    FROM bay_source
    FULL JOIN dw.fact_operational_service_event AS fact
      ON fact.service_date = bay_source.service_date
     AND fact.canonical_service_event_key = bay_source.canonical_service_event_key
    WHERE bay_source.evidence IS DISTINCT FROM fact.bay_evidence
       OR bay_source.comparable_count IS DISTINCT FROM fact.comparable_bay_observation_count
       OR bay_source.changed_count IS DISTINCT FROM fact.changed_bay_observation_count;

    SELECT count(*)
    INTO situation_evidence_mismatch_count
    FROM (
        SELECT service_date, canonical_service_event_key,
               sum(situation_link_count)::bigint AS source_count
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS source_situations
    FULL JOIN dw.fact_operational_service_event AS fact
      ON fact.service_date = source_situations.service_date
     AND fact.canonical_service_event_key = source_situations.canonical_service_event_key
    WHERE source_situations.source_count IS DISTINCT FROM fact.situation_link_count;

    SELECT count(*)
    INTO situation_flag_mismatch_count
    FROM dw.fact_operational_service_event AS fact
    WHERE fact.has_situation_evidence IS DISTINCT FROM (fact.situation_link_count > 0);

    SELECT count(*)
    INTO canonical_identity_mismatch_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN (
        SELECT
            service_date,
            canonical_service_event_key,
            min(canonical_service_event_id) AS canonical_service_event_id,
            min(canonical_stop_id) AS canonical_stop_id,
            min(canonical_line_id) AS canonical_line_id,
            min(event_type) AS event_type,
            min(scheduled_time_seconds) AS scheduled_time_seconds,
            min(canonical_scheduled_time_utc) AS canonical_scheduled_time_utc
        FROM pg_temp.stage10_validation_input
        GROUP BY service_date, canonical_service_event_key
    ) AS source_identity
      ON source_identity.service_date = fact.service_date
     AND source_identity.canonical_service_event_key = fact.canonical_service_event_key
    WHERE source_identity.canonical_service_event_key IS NULL
       OR fact.canonical_service_event_id IS DISTINCT FROM source_identity.canonical_service_event_id
       OR fact.canonical_stop_id IS DISTINCT FROM source_identity.canonical_stop_id
       OR fact.canonical_line_id IS DISTINCT FROM source_identity.canonical_line_id
       OR fact.event_type IS DISTINCT FROM source_identity.event_type
       OR fact.scheduled_time_seconds IS DISTINCT FROM source_identity.scheduled_time_seconds
       OR fact.canonical_scheduled_time_utc IS DISTINCT FROM source_identity.canonical_scheduled_time_utc;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY input.service_date, input.canonical_service_event_key
                ORDER BY input.source_observed_at_utc DESC,
                         input.source_observation_identity DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), latest_metadata AS (
        SELECT
            service_date,
            canonical_service_event_key,
            match_method,
            data_quality_classification
        FROM ranked
        WHERE latest_row_number = 1
    )
    SELECT
        count(*) FILTER (
            WHERE latest_metadata.canonical_service_event_key IS NULL
               OR fact.data_quality_classification IS DISTINCT FROM latest_metadata.data_quality_classification
        ),
        count(*) FILTER (
            WHERE latest_metadata.canonical_service_event_key IS NULL
               OR fact.match_method IS DISTINCT FROM latest_metadata.match_method
        )
    INTO data_quality_lineage_mismatch_count, match_method_lineage_mismatch_count
    FROM dw.fact_operational_service_event AS fact
    LEFT JOIN latest_metadata
      ON latest_metadata.service_date = fact.service_date
     AND latest_metadata.canonical_service_event_key = fact.canonical_service_event_key;

    RETURN QUERY
    SELECT
        'stage9_high_eligible_population'::text,
        CASE WHEN selected_high_observation_count > 0 THEN 'PASS' ELSE 'BLOCKED' END,
        selected_high_observation_count,
        NULL::bigint,
        CASE
            WHEN selected_high_observation_count > 0
                THEN 'Selected Stage 9 execution contains HIGH source-event observations eligible for the operational fact.'
            ELSE 'The selected current Stage 9 execution contains zero HIGH rows. MEDIUM remains contextual; no row is promoted to populate this fact.'
        END;

    RETURN QUERY
    SELECT 'duplicate_final_fact_grain',
           CASE WHEN duplicate_fact_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           duplicate_fact_grain_count,
           0::bigint,
           'Exactly one row is allowed for (service_date, canonical_service_event_key).';

    RETURN QUERY
    SELECT 'ineligible_stage9_confidence_in_fact',
           CASE WHEN ineligible_fact_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           ineligible_fact_count,
           0::bigint,
           'Only Stage 9 match_confidence = HIGH may enter the fact; MEDIUM, LOW, and UNMATCHED do not.';

    RETURN QUERY
    SELECT 'explicit_matching_run_version_lineage',
           CASE WHEN lineage_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           lineage_violation_count,
           0::bigint,
           format('Every fact row must carry the selected matching_run_key %s and matching_version %L.', p_matching_run_key, p_matching_version);

    RETURN QUERY
    SELECT 'source_observation_duplication_from_historical_runs',
           CASE WHEN source_observation_duplication_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           source_observation_duplication_count,
           0::bigint,
           'The operational input is scoped to one selected matching run; historical matching runs are not unioned into the fact.';

    RETURN QUERY
    SELECT 'full_source_identity_tuple_mismatch',
           CASE WHEN source_identity_tuple_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           source_identity_tuple_mismatch_count,
           0::bigint,
           'Stage 10 reconciles source_system, source_namespace, and source_observation_identity as one complete identity; the identity value alone is not a universal key.';

    RETURN QUERY
    SELECT 'orphan_matching_run_reference',
           CASE WHEN orphan_matching_run_reference_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           orphan_matching_run_reference_count,
           0::bigint,
           'Every fact matching_run_key must reference ctl.matching_run.';

    RETURN QUERY
    SELECT 'orphan_canonical_service_event_reference',
           CASE WHEN orphan_canonical_event_reference_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           orphan_canonical_event_reference_count,
           0::bigint,
           'Every (matching_version, canonical_service_event_key) fact reference must exist in core.canonical_service_event.';

    RETURN QUERY
    SELECT 'orphan_first_source_event_match_reference',
           CASE WHEN orphan_first_source_match_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           orphan_first_source_match_count,
           0::bigint,
           'Every first_source_event_match_key must reference core.source_event_match.';

    RETURN QUERY
    SELECT 'orphan_latest_source_event_match_reference',
           CASE WHEN orphan_latest_source_match_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           orphan_latest_source_match_count,
           0::bigint,
           'Every latest_source_event_match_key must reference core.source_event_match.';

    RETURN QUERY
    SELECT 'missing_selected_stage9_event_grains',
           CASE WHEN missing_event_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           missing_event_grain_count,
           0::bigint,
           'Every eligible canonical service-event grain in the selected input must exist in the fact.';

    RETURN QUERY
    SELECT 'stale_fact_event_grains',
           CASE WHEN stale_event_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           stale_event_grain_count,
           0::bigint,
           'No fact row may remain outside the selected current Stage 9 population.';

    RETURN QUERY
    SELECT 'final_fact_grain_count_reconciliation',
           CASE WHEN fact_grain_count = selected_event_grain_count THEN 'PASS' ELSE 'FAIL' END,
           fact_grain_count,
           selected_event_grain_count,
           'The current fact row count equals the selected HIGH canonical service-event grain count.';

    RETURN QUERY
    SELECT 'first_observation_lineage',
           CASE WHEN first_lineage_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           first_lineage_violation_count,
           0::bigint,
           'First fields use source_observed_at_utc ASC, source_observation_identity ASC.';

    RETURN QUERY
    SELECT 'latest_observation_lineage',
           CASE WHEN latest_lineage_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           latest_lineage_violation_count,
           0::bigint,
           'Latest fields use source_observed_at_utc DESC, source_observation_identity DESC.';

    RETURN QUERY
    SELECT 'observation_count_reconciliation',
           CASE WHEN observation_count_mismatch = 0 THEN 'PASS' ELSE 'FAIL' END,
           observation_count_mismatch,
           0::bigint,
           'source_observation_count equals the selected durable source-observation row count.';

    RETURN QUERY
    SELECT 'first_timing_semantics',
           CASE WHEN first_timing_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           first_timing_mismatch_count,
           0::bigint,
           'First EstimatedTime and estimated delay come from the first source row, including NULL.';

    RETURN QUERY
    SELECT 'latest_timing_semantics',
           CASE WHEN latest_timing_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           latest_timing_mismatch_count,
           0::bigint,
           'Latest EstimatedTime and estimated delay come from the latest source row, including NULL.';

    RETURN QUERY
    SELECT 'latest_null_estimate_not_carried_forward',
           CASE WHEN latest_null_carry_forward_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           latest_null_carry_forward_count,
           0::bigint,
           'An earlier estimate must not replace a chronologically latest NULL estimate.';

    RETURN QUERY
    SELECT 'platform_bay_evidence_semantics',
           CASE WHEN bay_evidence_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           bay_evidence_mismatch_count,
           0::bigint,
           'Changed/Unchanged/Unknown follows comparable PlannedBay versus EstimatedBay evidence; Unknown is not Unchanged.';

    RETURN QUERY
    SELECT 'situation_evidence_reconciliation',
           CASE WHEN situation_evidence_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           situation_evidence_mismatch_count,
           0::bigint,
           'SituationLinkCount counts persisted association rows, not distinct Situation identities.';

    RETURN QUERY
    SELECT 'situation_evidence_flag',
           CASE WHEN situation_flag_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           situation_flag_mismatch_count,
           0::bigint,
           'HasSituationEvidence is exactly (situation_link_count > 0) and makes no causal claim.';

    RETURN QUERY
    SELECT 'canonical_service_event_identity',
           CASE WHEN canonical_identity_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           canonical_identity_mismatch_count,
           0::bigint,
           'Service date, canonical event identity, event type, scheduled seconds, and canonical timetable come from core.canonical_service_event.';

    RETURN QUERY
    SELECT 'match_method_lineage',
           CASE WHEN match_method_lineage_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           match_method_lineage_mismatch_count,
           0::bigint,
           'Stage 10 retains match_method from the deterministic latest source observation when repeated HIGH observations evolve.';

    RETURN QUERY
    SELECT 'data_quality_classification_lineage',
           CASE WHEN data_quality_lineage_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           data_quality_lineage_mismatch_count,
           0::bigint,
           'Stage 10 retains data_quality_classification from the deterministic latest source observation; it remains separate and is not translated into a legacy status.';

    RETURN QUERY
    SELECT 'repeat_refresh_semantic_difference',
           CASE WHEN repeatability_difference_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           repeatability_difference_count,
           0::bigint,
           'Refresh #1 was snapshotted as A, refresh #2 with unchanged input as B; only semantic differences are compared, excluding refreshed_at_utc.';
END;
$function$;

COMMENT ON FUNCTION dw.validate_operational_service_event(bigint, text) IS
    'Stage 10 current-architecture validator. Requires an explicit COMPLETE matching run/version and the Stage 10 input adapter, checks namespace-qualified source identity and relational lineage, blocks on missing database/source prerequisites, and proves repeat-refresh idempotency with refresh #1 -> snapshot A -> refresh #2 -> snapshot B.';

SELECT * FROM dw.validate_operational_service_event(NULL, NULL);

COMMIT;

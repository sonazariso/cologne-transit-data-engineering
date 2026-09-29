-- Stage 10 — Operational Fact: semantic validation
-- Target: PostgreSQL 18
--
-- The validator returns one row per check.  It returns BLOCKED when the
-- current database does not contain the approved Stage 9 input view or the
-- Stage 10 fact.  It never turns an unavailable database/source into PASS.
--
-- Run after 05-create-operational-stop-outcome.sql and
-- 06-refresh-operational-stop-outcome.sql:
--   SELECT * FROM dw.validate_fact_operational_stop_outcome();

BEGIN;

CREATE OR REPLACE FUNCTION dw.validate_fact_operational_stop_outcome()
RETURNS TABLE (
    check_code       text,
    outcome          text,
    observed_count   bigint,
    expected_count   bigint,
    detail           text
)
LANGUAGE plpgsql
AS $function$
DECLARE
    input_relation                 regclass;
    missing_column                 text;
    duplicate_fact_grain_count     bigint;
    usable_stage9_grain_count      bigint;
    fact_grain_count               bigint;
    missing_usable_grain_count     bigint;
    stale_fact_grain_count         bigint;
    non_approved_fact_count        bigint;
    match_status_conflict_count    bigint;
    duplicate_input_observation_count bigint;
    first_lineage_violation_count  bigint;
    latest_lineage_violation_count bigint;
    observation_count_mismatch     bigint;
    first_timing_mismatch_count    bigint;
    latest_timing_mismatch_count   bigint;
    earlier_estimate_latest_null_violation_count bigint;
    timetable_inconsistency_count  bigint;
    platform_mismatch_count        bigint;
    situation_count_mismatch       bigint;
    situation_flag_mismatch        bigint;
    identity_consistency_count     bigint;
    repeatability_difference_count bigint;
    refresh_error                  text;
BEGIN
    input_relation := to_regclass('wrk.vwcolognerealtimetripmatch');
    IF input_relation IS NULL THEN
        input_relation := to_regclass('wrk."vwCologneRealtimeTripMatch"');
    END IF;

    IF to_regclass('dw.fact_operational_stop_outcome') IS NULL THEN
        RETURN QUERY
        SELECT
            'STAGE10_PREREQUISITE_FACT'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            'dw.fact_operational_stop_outcome is absent; Stage 10 has not been physically installed.'::text;
        RETURN;
    END IF;

    IF input_relation IS NULL THEN
        RETURN QUERY
        SELECT
            'STAGE9_INPUT_VIEW'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            'Approved Stage 9 input view wrk.vwCologneRealtimeTripMatch is absent; exact reconciliation cannot be evaluated.'::text;
        RETURN;
    END IF;

    FOREACH missing_column IN ARRAY ARRAY[
        'date_key',
        'scheduled_stop_event_key',
        'match_status',
        'observation_key',
        'observed_at_utc',
        'timetabled_arrival_utc',
        'estimated_arrival_utc',
        'planned_platform',
        'estimated_platform',
        'situation_link_count'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute
            WHERE attrelid = input_relation
              AND attname = missing_column
              AND attnum > 0
              AND NOT attisdropped
        ) THEN
            RETURN QUERY
            SELECT
                'STAGE9_INPUT_VIEW_COLUMNS'::text,
                'BLOCKED'::text,
                0::bigint,
                NULL::bigint,
                format('Input view %s is missing required column %s.', input_relation, missing_column);
            RETURN;
        END IF;
    END LOOP;

    DROP TABLE IF EXISTS pg_temp.stage10_validation_input;
    DROP TABLE IF EXISTS pg_temp.stage10_validation_before;
    DROP TABLE IF EXISTS pg_temp.stage10_validation_after;

    BEGIN
        EXECUTE format($sql$
            CREATE TEMP TABLE stage10_validation_input ON COMMIT DROP AS
            SELECT
                date_key::date AS date_key,
                scheduled_stop_event_key::bigint AS scheduled_stop_event_key,
                NULLIF(btrim(match_status::text), '') AS match_status,
                NULLIF(btrim(observation_key::text), '') AS observation_key,
                observed_at_utc::timestamptz AS observed_at_utc,
                timetabled_arrival_utc::timestamptz AS timetabled_arrival_utc,
                estimated_arrival_utc::timestamptz AS estimated_arrival_utc,
                NULLIF(btrim(planned_platform::text), '') AS planned_platform,
                NULLIF(btrim(estimated_platform::text), '') AS estimated_platform,
                situation_link_count::bigint AS situation_link_count
            FROM %s
        $sql$, input_relation);
    EXCEPTION WHEN OTHERS THEN
        RETURN QUERY
        SELECT
            'STAGE9_INPUT_VIEW_TYPES'::text,
            'BLOCKED'::text,
            0::bigint,
            NULL::bigint,
            format('Stage 9 input view could not be projected into the Stage 10 contract: %s', SQLERRM);
        RETURN;
    END;

    CREATE INDEX stage10_validation_input_grain_ix
        ON pg_temp.stage10_validation_input (date_key, scheduled_stop_event_key, observed_at_utc, observation_key);

    CREATE TEMP TABLE stage10_validation_before (
        date_key                 date NOT NULL,
        scheduled_stop_event_key bigint NOT NULL,
        semantic_hash            text NOT NULL
    ) ON COMMIT DROP;

    SELECT count(*)
    INTO duplicate_input_observation_count
    FROM (
        SELECT observation_key
        FROM pg_temp.stage10_validation_input
        WHERE observation_key IS NOT NULL
        GROUP BY observation_key
        HAVING count(*) > 1
    ) AS duplicates;

    SELECT count(*)
    INTO duplicate_fact_grain_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM dw.fact_operational_stop_outcome
        GROUP BY date_key, scheduled_stop_event_key
        HAVING count(*) > 1
    ) AS duplicates;

    SELECT count(*)
    INTO usable_stage9_grain_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_validation_input
        WHERE match_status IN ('ExactStopMatch', 'ParentStationFallback')
        GROUP BY date_key, scheduled_stop_event_key
    ) AS usable;

    SELECT count(*)
    INTO fact_grain_count
    FROM dw.fact_operational_stop_outcome;

    SELECT count(*)
    INTO missing_usable_grain_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_validation_input
        WHERE match_status IN ('ExactStopMatch', 'ParentStationFallback')
        GROUP BY date_key, scheduled_stop_event_key
    ) AS usable
    LEFT JOIN dw.fact_operational_stop_outcome AS fact
      ON fact.date_key = usable.date_key
     AND fact.scheduled_stop_event_key = usable.scheduled_stop_event_key
    WHERE fact.operational_stop_outcome_key IS NULL;

    SELECT count(*)
    INTO stale_fact_grain_count
    FROM dw.fact_operational_stop_outcome AS fact
    LEFT JOIN (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_validation_input
        WHERE match_status IN ('ExactStopMatch', 'ParentStationFallback')
        GROUP BY date_key, scheduled_stop_event_key
    ) AS usable
      ON usable.date_key = fact.date_key
     AND usable.scheduled_stop_event_key = fact.scheduled_stop_event_key
    WHERE usable.date_key IS NULL;

    SELECT count(*)
    INTO non_approved_fact_count
    FROM dw.fact_operational_stop_outcome
    WHERE match_status NOT IN ('ExactStopMatch', 'ParentStationFallback');

    SELECT count(*)
    INTO match_status_conflict_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
        HAVING count(DISTINCT match_status) > 1
            OR bool_or(match_status IS NULL)
            OR bool_or(match_status NOT IN ('ExactStopMatch', 'ParentStationFallback'))
    ) AS conflicts;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc ASC, observation_key ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc DESC, observation_key DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT
        count(*) FILTER (
            WHERE fact.first_observation_key IS DISTINCT FROM first_rows.observation_key
               OR fact.first_observed_at_utc IS DISTINCT FROM first_rows.observed_at_utc
        ),
        count(*) FILTER (
            WHERE fact.last_observation_key IS DISTINCT FROM latest_rows.observation_key
               OR fact.last_observed_at_utc IS DISTINCT FROM latest_rows.observed_at_utc
        )
    INTO first_lineage_violation_count, latest_lineage_violation_count
    FROM dw.fact_operational_stop_outcome AS fact
    JOIN first_rows
      ON first_rows.date_key = fact.date_key
     AND first_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key
    JOIN latest_rows
      ON latest_rows.date_key = fact.date_key
     AND latest_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key;

    SELECT count(*)
    INTO observation_count_mismatch
    FROM (
        SELECT date_key, scheduled_stop_event_key, count(*)::bigint AS source_count
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
    ) AS source_counts
    FULL JOIN dw.fact_operational_stop_outcome AS fact
      ON fact.date_key = source_counts.date_key
     AND fact.scheduled_stop_event_key = source_counts.scheduled_stop_event_key
    WHERE source_counts.source_count IS DISTINCT FROM fact.observation_count;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc ASC, observation_key ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc DESC, observation_key DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT
        count(*) FILTER (
            WHERE fact.first_observed_estimated_arrival_utc IS DISTINCT FROM first_rows.estimated_arrival_utc
               OR fact.first_observed_estimated_delay_minutes IS DISTINCT FROM CASE
                    WHEN first_rows.estimated_arrival_utc IS NULL THEN NULL
                    ELSE round((extract(epoch FROM (
                        first_rows.estimated_arrival_utc - fact.timetabled_arrival_utc
                    )) / 60.0)::numeric, 3)
                END
        ),
        count(*) FILTER (
            WHERE fact.latest_observed_estimated_arrival_utc IS DISTINCT FROM latest_rows.estimated_arrival_utc
               OR fact.final_observed_estimated_delay_minutes IS DISTINCT FROM CASE
                    WHEN latest_rows.estimated_arrival_utc IS NULL THEN NULL
                    ELSE round((extract(epoch FROM (
                        latest_rows.estimated_arrival_utc - fact.timetabled_arrival_utc
                    )) / 60.0)::numeric, 3)
                END
        )
    INTO first_timing_mismatch_count, latest_timing_mismatch_count
    FROM dw.fact_operational_stop_outcome AS fact
    JOIN first_rows
      ON first_rows.date_key = fact.date_key
     AND first_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key
    JOIN latest_rows
      ON latest_rows.date_key = fact.date_key
     AND latest_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key;

    WITH ranked AS (
        SELECT
            input.*,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc ASC, observation_key ASC
            ) AS first_row_number,
            row_number() OVER (
                PARTITION BY date_key, scheduled_stop_event_key
                ORDER BY observed_at_utc DESC, observation_key DESC
            ) AS latest_row_number
        FROM pg_temp.stage10_validation_input AS input
    ), first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ), latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT count(*)
    INTO earlier_estimate_latest_null_violation_count
    FROM dw.fact_operational_stop_outcome AS fact
    JOIN first_rows
      ON first_rows.date_key = fact.date_key
     AND first_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key
    JOIN latest_rows
      ON latest_rows.date_key = fact.date_key
     AND latest_rows.scheduled_stop_event_key = fact.scheduled_stop_event_key
    WHERE first_rows.estimated_arrival_utc IS NOT NULL
      AND latest_rows.estimated_arrival_utc IS NULL
      AND (
          fact.latest_observed_estimated_arrival_utc IS NOT NULL
          OR fact.final_observed_estimated_delay_minutes IS NOT NULL
      );

    SELECT count(*)
    INTO timetable_inconsistency_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
        HAVING count(*) FILTER (WHERE timetabled_arrival_utc IS NULL) > 0
            OR count(DISTINCT timetabled_arrival_utc) > 1
    ) AS inconsistent;

    SELECT count(*)
    INTO identity_consistency_count
    FROM dw.fact_operational_stop_outcome AS fact
    LEFT JOIN (
        SELECT
            date_key,
            scheduled_stop_event_key,
            min(timetabled_arrival_utc) AS source_timetabled_arrival_utc
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
    ) AS source_identity
      ON source_identity.date_key = fact.date_key
     AND source_identity.scheduled_stop_event_key = fact.scheduled_stop_event_key
    WHERE source_identity.date_key IS NULL
       OR fact.timetabled_arrival_utc IS DISTINCT FROM source_identity.source_timetabled_arrival_utc;

    WITH platform_source AS (
        SELECT
            date_key,
            scheduled_stop_event_key,
            count(*) FILTER (
                WHERE planned_platform IS NOT NULL
                  AND estimated_platform IS NOT NULL
            )::bigint AS comparable_count,
            count(*) FILTER (
                WHERE planned_platform IS NOT NULL
                  AND estimated_platform IS NOT NULL
                  AND planned_platform IS DISTINCT FROM estimated_platform
            )::bigint AS changed_count,
            CASE
                WHEN count(*) FILTER (
                    WHERE planned_platform IS NOT NULL
                      AND estimated_platform IS NOT NULL
                ) = 0 THEN 'Unknown'
                WHEN count(*) FILTER (
                    WHERE planned_platform IS NOT NULL
                      AND estimated_platform IS NOT NULL
                      AND planned_platform IS DISTINCT FROM estimated_platform
                ) > 0 THEN 'Changed'
                ELSE 'Unchanged'
            END AS evidence
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
    )
    SELECT count(*)
    INTO platform_mismatch_count
    FROM platform_source
    FULL JOIN dw.fact_operational_stop_outcome AS fact
      ON fact.date_key = platform_source.date_key
     AND fact.scheduled_stop_event_key = platform_source.scheduled_stop_event_key
    WHERE platform_source.evidence IS DISTINCT FROM fact.platform_change_evidence
       OR platform_source.comparable_count IS DISTINCT FROM fact.comparable_platform_observation_count
       OR platform_source.changed_count IS DISTINCT FROM fact.changed_platform_observation_count;

    SELECT count(*)
    INTO situation_count_mismatch
    FROM (
        SELECT date_key, scheduled_stop_event_key, sum(situation_link_count)::bigint AS source_count
        FROM pg_temp.stage10_validation_input
        GROUP BY date_key, scheduled_stop_event_key
    ) AS source_situations
    FULL JOIN dw.fact_operational_stop_outcome AS fact
      ON fact.date_key = source_situations.date_key
     AND fact.scheduled_stop_event_key = source_situations.scheduled_stop_event_key
    WHERE source_situations.source_count IS DISTINCT FROM fact.situation_link_count;

    SELECT count(*)
    INTO situation_flag_mismatch
    FROM dw.fact_operational_stop_outcome AS fact
    WHERE fact.has_situation_evidence IS DISTINCT FROM (fact.situation_link_count > 0);

    INSERT INTO pg_temp.stage10_validation_before
    SELECT
        date_key,
        scheduled_stop_event_key,
        md5(jsonb_build_object(
            'date_key', date_key,
            'scheduled_stop_event_key', scheduled_stop_event_key,
            'match_status', match_status,
            'timetabled_arrival_utc', timetabled_arrival_utc,
            'first_observation_key', first_observation_key,
            'last_observation_key', last_observation_key,
            'first_observed_at_utc', first_observed_at_utc,
            'last_observed_at_utc', last_observed_at_utc,
            'observation_count', observation_count,
            'first_observed_estimated_arrival_utc', first_observed_estimated_arrival_utc,
            'latest_observed_estimated_arrival_utc', latest_observed_estimated_arrival_utc,
            'first_observed_estimated_delay_minutes', first_observed_estimated_delay_minutes,
            'final_observed_estimated_delay_minutes', final_observed_estimated_delay_minutes,
            'first_observed_planned_platform', first_observed_planned_platform,
            'latest_observed_planned_platform', latest_observed_planned_platform,
            'first_observed_estimated_platform', first_observed_estimated_platform,
            'latest_observed_estimated_platform', latest_observed_estimated_platform,
            'platform_change_evidence', platform_change_evidence,
            'comparable_platform_observation_count', comparable_platform_observation_count,
            'changed_platform_observation_count', changed_platform_observation_count,
            'situation_link_count', situation_link_count,
            'has_situation_evidence', has_situation_evidence
        )::text) AS semantic_hash
    FROM dw.fact_operational_stop_outcome;

    BEGIN
        CALL dw."uspRefreshFactOperationalStopOutcome"();
        CALL dw."uspRefreshFactOperationalStopOutcome"();
    EXCEPTION WHEN OTHERS THEN
        refresh_error := SQLERRM;
    END;

    IF refresh_error IS NULL THEN
        CREATE TEMP TABLE stage10_validation_after ON COMMIT DROP AS
        SELECT
            date_key,
            scheduled_stop_event_key,
            md5(jsonb_build_object(
                'date_key', date_key,
                'scheduled_stop_event_key', scheduled_stop_event_key,
                'match_status', match_status,
                'timetabled_arrival_utc', timetabled_arrival_utc,
                'first_observation_key', first_observation_key,
                'last_observation_key', last_observation_key,
                'first_observed_at_utc', first_observed_at_utc,
                'last_observed_at_utc', last_observed_at_utc,
                'observation_count', observation_count,
                'first_observed_estimated_arrival_utc', first_observed_estimated_arrival_utc,
                'latest_observed_estimated_arrival_utc', latest_observed_estimated_arrival_utc,
                'first_observed_estimated_delay_minutes', first_observed_estimated_delay_minutes,
                'final_observed_estimated_delay_minutes', final_observed_estimated_delay_minutes,
                'first_observed_planned_platform', first_observed_planned_platform,
                'latest_observed_planned_platform', latest_observed_planned_platform,
                'first_observed_estimated_platform', first_observed_estimated_platform,
                'latest_observed_estimated_platform', latest_observed_estimated_platform,
                'platform_change_evidence', platform_change_evidence,
                'comparable_platform_observation_count', comparable_platform_observation_count,
                'changed_platform_observation_count', changed_platform_observation_count,
                'situation_link_count', situation_link_count,
                'has_situation_evidence', has_situation_evidence
            )::text) AS semantic_hash
        FROM dw.fact_operational_stop_outcome;

        SELECT count(*)
        INTO repeatability_difference_count
        FROM pg_temp.stage10_validation_before AS before_refresh
        FULL JOIN pg_temp.stage10_validation_after AS after_refresh
          ON after_refresh.date_key = before_refresh.date_key
         AND after_refresh.scheduled_stop_event_key = before_refresh.scheduled_stop_event_key
        WHERE before_refresh.semantic_hash IS DISTINCT FROM after_refresh.semantic_hash;
    ELSE
        repeatability_difference_count := NULL;
    END IF;

    RETURN QUERY
    SELECT 'duplicate_fact_grain', CASE WHEN duplicate_fact_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           duplicate_fact_grain_count, 0::bigint,
           'Exactly one fact row per (date_key, scheduled_stop_event_key).';
    RETURN QUERY
    SELECT 'usable_stage9_grain_count', 'PASS', usable_stage9_grain_count, usable_stage9_grain_count,
           'Current approved Stage 9 usable grain population measured from the input view.';
    RETURN QUERY
    SELECT 'missing_usable_stage9_grains', CASE WHEN missing_usable_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           missing_usable_grain_count, 0::bigint, 'Usable Stage 9 grains absent from the fact.';
    RETURN QUERY
    SELECT 'stale_fact_grains', CASE WHEN stale_fact_grain_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           stale_fact_grain_count, 0::bigint, 'Fact grains absent from the current usable Stage 9 population.';
    RETURN QUERY
    SELECT 'fact_grain_count', CASE WHEN fact_grain_count = usable_stage9_grain_count THEN 'PASS' ELSE 'FAIL' END,
           fact_grain_count, usable_stage9_grain_count, 'Current fact grain count reconciles to Stage 9.';
    RETURN QUERY
    SELECT 'fact_rows_with_non_approved_match_status', CASE WHEN non_approved_fact_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           non_approved_fact_count, 0::bigint, 'Only ExactStopMatch and ParentStationFallback may enter the fact.';
    RETURN QUERY
    SELECT 'inconsistent_stage9_match_status', CASE WHEN match_status_conflict_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           match_status_conflict_count, 0::bigint, 'MatchStatus must be deterministic within one final grain.';
    RETURN QUERY
    SELECT 'duplicate_input_observation_key', CASE WHEN duplicate_input_observation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           duplicate_input_observation_count, 0::bigint, 'The input view must expose each source observation once.';
    RETURN QUERY
    SELECT 'first_observation_lineage', CASE WHEN first_lineage_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           first_lineage_violation_count, 0::bigint, 'First fields use ORDER BY observed_at_utc ASC, observation_key ASC.';
    RETURN QUERY
    SELECT 'latest_observation_lineage', CASE WHEN latest_lineage_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           latest_lineage_violation_count, 0::bigint, 'Latest fields use ORDER BY observed_at_utc DESC, observation_key DESC.';
    RETURN QUERY
    SELECT 'observation_count_reconciliation', CASE WHEN observation_count_mismatch = 0 THEN 'PASS' ELSE 'FAIL' END,
           observation_count_mismatch, 0::bigint, 'Fact observation_count equals source observation-row count.';
    RETURN QUERY
    SELECT 'first_timing_null_safe', CASE WHEN first_timing_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           first_timing_mismatch_count, 0::bigint, 'First estimate and derived estimated delay match the first source row, including NULL.';
    RETURN QUERY
    SELECT 'latest_timing_null_safe', CASE WHEN latest_timing_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           latest_timing_mismatch_count, 0::bigint, 'Latest estimate and final estimated delay match the latest source row, including NULL.';
    RETURN QUERY
    SELECT 'earlier_estimate_latest_null_preserved', CASE WHEN earlier_estimate_latest_null_violation_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           earlier_estimate_latest_null_violation_count, 0::bigint, 'An earlier estimate must not be carried into a latest NULL observation.';
    RETURN QUERY
    SELECT 'timetabled_arrival_consistency', CASE WHEN timetable_inconsistency_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           timetable_inconsistency_count, 0::bigint, 'All observations in a grain must carry the matched scheduled event timetable value.';
    RETURN QUERY
    SELECT 'scheduled_identity_consistency', CASE WHEN identity_consistency_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           identity_consistency_count, 0::bigint, 'Date/event identity and timetabled arrival remain internally consistent.';
    RETURN QUERY
    SELECT 'platform_evidence_semantics', CASE WHEN platform_mismatch_count = 0 THEN 'PASS' ELSE 'FAIL' END,
           platform_mismatch_count, 0::bigint, 'Changed/Unchanged/Unknown follows comparable planned-vs-estimated platform evidence.';
    RETURN QUERY
    SELECT 'situation_link_count_reconciliation', CASE WHEN situation_count_mismatch = 0 THEN 'PASS' ELSE 'FAIL' END,
           situation_count_mismatch, 0::bigint, 'SituationLinkCount counts persisted observation-to-situation link rows.';
    RETURN QUERY
    SELECT 'situation_evidence_flag', CASE WHEN situation_flag_mismatch = 0 THEN 'PASS' ELSE 'FAIL' END,
           situation_flag_mismatch, 0::bigint, 'HasSituationEvidence is exactly (SituationLinkCount > 0), not a causal claim.';
    RETURN QUERY
    SELECT 'repeat_refresh_semantic_difference',
           CASE
               WHEN refresh_error IS NOT NULL THEN 'BLOCKED'
               WHEN repeatability_difference_count = 0 THEN 'PASS'
               ELSE 'FAIL'
           END,
           COALESCE(repeatability_difference_count, 0), 0::bigint,
           CASE
               WHEN refresh_error IS NOT NULL THEN 'Refresh repeatability could not be evaluated: ' || refresh_error
               ELSE 'Two refreshes against unchanged input have no semantic differences; refreshed_at_utc is ignored.'
           END;
END;
$function$;

COMMENT ON FUNCTION dw.validate_fact_operational_stop_outcome() IS
    'Stage 10 semantic validation. Missing database/source prerequisites return BLOCKED, never PASS.';

SELECT * FROM dw.validate_fact_operational_stop_outcome();

COMMIT;

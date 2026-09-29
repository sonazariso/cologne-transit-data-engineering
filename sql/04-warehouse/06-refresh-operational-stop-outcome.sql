-- Stage 10 — Operational Fact: deterministic, transactional, idempotent refresh
-- Target: PostgreSQL 18
--
-- Required Stage 9 input contract (one row per persisted source observation):
--   wrk.vwCologneRealtimeTripMatch
--   date_key date
--   scheduled_stop_event_key bigint
--   match_status text
--   observation_key text
--   observed_at_utc timestamptz
--   timetabled_arrival_utc timestamptz
--   estimated_arrival_utc timestamptz NULL
--   planned_platform text NULL
--   estimated_platform text NULL
--   situation_link_count bigint
--
-- The input view is intentionally not created here.  It belongs to the
-- approved Stage 9/source implementation.  The procedure fails explicitly
-- when the view or a required column is absent; an empty substitute would
-- make exact Stage 9-to-fact reconciliation appear to pass.

BEGIN;

CREATE OR REPLACE PROCEDURE dw."uspRefreshFactOperationalStopOutcome"()
LANGUAGE plpgsql
AS $procedure$
DECLARE
    input_relation             regclass;
    missing_column             text;
    invalid_count              bigint;
    duplicate_observation_count bigint;
    conflicting_status_count   bigint;
    inconsistent_timetable_count bigint;
BEGIN
    -- Support either an unquoted PostgreSQL identifier or the exact quoted
    -- logical name without guessing at a different source object.
    input_relation := to_regclass('wrk.vwcolognerealtimetripmatch');
    IF input_relation IS NULL THEN
        input_relation := to_regclass('wrk."vwCologneRealtimeTripMatch"');
    END IF;

    IF input_relation IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Stage 10 refresh is blocked: approved Stage 9 input view wrk.vwCologneRealtimeTripMatch is absent';
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
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = format(
                    'Stage 10 refresh is blocked: input view %s is missing required column %s',
                    input_relation,
                    missing_column
                );
        END IF;
    END LOOP;

    DROP TABLE IF EXISTS pg_temp.stage10_input;
    DROP TABLE IF EXISTS pg_temp.stage10_build;

    EXECUTE format($sql$
        CREATE TEMP TABLE stage10_input ON COMMIT DROP AS
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

    CREATE INDEX stage10_input_grain_ix
        ON pg_temp.stage10_input (date_key, scheduled_stop_event_key, observed_at_utc, observation_key);

    SELECT count(*)
    INTO invalid_count
    FROM pg_temp.stage10_input
    WHERE date_key IS NULL
       OR scheduled_stop_event_key IS NULL
       OR match_status IS NULL
       OR observation_key IS NULL
       OR observed_at_utc IS NULL
       OR timetabled_arrival_utc IS NULL
       OR situation_link_count IS NULL
       OR situation_link_count < 0
       OR match_status NOT IN ('ExactStopMatch', 'ParentStationFallback');

    IF invalid_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'Stage 10 refresh rejected %s invalid Stage 9 input rows',
                invalid_count
            );
    END IF;

    SELECT count(*)
    INTO duplicate_observation_count
    FROM (
        SELECT observation_key
        FROM pg_temp.stage10_input
        GROUP BY observation_key
        HAVING count(*) > 1
    ) AS duplicates;

    IF duplicate_observation_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = format(
                'Stage 10 refresh rejected %s duplicated observation keys; source-observation multiplicity must be resolved before consolidation',
                duplicate_observation_count
            );
    END IF;

    SELECT count(*)
    INTO conflicting_status_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_input
        GROUP BY date_key, scheduled_stop_event_key
        HAVING count(DISTINCT match_status) > 1
    ) AS inconsistent_status;

    IF conflicting_status_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'Stage 10 refresh rejected %s grains with inconsistent eligible MatchStatus values',
                conflicting_status_count
            );
    END IF;

    SELECT count(*)
    INTO inconsistent_timetable_count
    FROM (
        SELECT date_key, scheduled_stop_event_key
        FROM pg_temp.stage10_input
        GROUP BY date_key, scheduled_stop_event_key
        HAVING count(*) FILTER (WHERE timetabled_arrival_utc IS NULL) > 0
            OR count(DISTINCT timetabled_arrival_utc) > 1
    ) AS inconsistent_timetable;

    IF inconsistent_timetable_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'Stage 10 refresh rejected %s grains with inconsistent TimetabledArrivalUtc values',
                inconsistent_timetable_count
            );
    END IF;

    CREATE TEMP TABLE stage10_build ON COMMIT DROP AS
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
        FROM pg_temp.stage10_input AS input
    ),
    grouped AS (
        SELECT
            date_key,
            scheduled_stop_event_key,
            min(match_status) AS match_status,
            min(timetabled_arrival_utc) AS timetabled_arrival_utc,
            count(*)::bigint AS observation_count,
            count(*) FILTER (
                WHERE planned_platform IS NOT NULL
                  AND estimated_platform IS NOT NULL
            )::bigint AS comparable_platform_observation_count,
            count(*) FILTER (
                WHERE planned_platform IS NOT NULL
                  AND estimated_platform IS NOT NULL
                  AND planned_platform IS DISTINCT FROM estimated_platform
            )::bigint AS changed_platform_observation_count,
            sum(situation_link_count)::bigint AS situation_link_count
        FROM ranked
        GROUP BY date_key, scheduled_stop_event_key
    ),
    first_rows AS (
        SELECT *
        FROM ranked
        WHERE first_row_number = 1
    ),
    latest_rows AS (
        SELECT *
        FROM ranked
        WHERE latest_row_number = 1
    )
    SELECT
        grouped.date_key,
        grouped.scheduled_stop_event_key,
        grouped.match_status,
        grouped.timetabled_arrival_utc,
        first_rows.observation_key AS first_observation_key,
        latest_rows.observation_key AS last_observation_key,
        first_rows.observed_at_utc AS first_observed_at_utc,
        latest_rows.observed_at_utc AS last_observed_at_utc,
        grouped.observation_count,
        first_rows.estimated_arrival_utc AS first_observed_estimated_arrival_utc,
        latest_rows.estimated_arrival_utc AS latest_observed_estimated_arrival_utc,
        CASE
            WHEN first_rows.estimated_arrival_utc IS NULL THEN NULL
            ELSE round(
                (extract(epoch FROM (
                    first_rows.estimated_arrival_utc - grouped.timetabled_arrival_utc
                )) / 60.0)::numeric,
                3
            )
        END AS first_observed_estimated_delay_minutes,
        CASE
            WHEN latest_rows.estimated_arrival_utc IS NULL THEN NULL
            ELSE round(
                (extract(epoch FROM (
                    latest_rows.estimated_arrival_utc - grouped.timetabled_arrival_utc
                )) / 60.0)::numeric,
                3
            )
        END AS final_observed_estimated_delay_minutes,
        first_rows.planned_platform AS first_observed_planned_platform,
        latest_rows.planned_platform AS latest_observed_planned_platform,
        first_rows.estimated_platform AS first_observed_estimated_platform,
        latest_rows.estimated_platform AS latest_observed_estimated_platform,
        CASE
            WHEN grouped.comparable_platform_observation_count = 0 THEN 'Unknown'
            WHEN grouped.changed_platform_observation_count > 0 THEN 'Changed'
            ELSE 'Unchanged'
        END AS platform_change_evidence,
        grouped.comparable_platform_observation_count,
        grouped.changed_platform_observation_count,
        grouped.situation_link_count,
        (grouped.situation_link_count > 0) AS has_situation_evidence
    FROM grouped
    JOIN first_rows
      ON first_rows.date_key = grouped.date_key
     AND first_rows.scheduled_stop_event_key = grouped.scheduled_stop_event_key
    JOIN latest_rows
      ON latest_rows.date_key = grouped.date_key
     AND latest_rows.scheduled_stop_event_key = grouped.scheduled_stop_event_key;

    CREATE UNIQUE INDEX stage10_build_grain_uq
        ON pg_temp.stage10_build (date_key, scheduled_stop_event_key);

    INSERT INTO dw.fact_operational_stop_outcome (
        date_key,
        scheduled_stop_event_key,
        match_status,
        timetabled_arrival_utc,
        first_observation_key,
        last_observation_key,
        first_observed_at_utc,
        last_observed_at_utc,
        observation_count,
        first_observed_estimated_arrival_utc,
        latest_observed_estimated_arrival_utc,
        first_observed_estimated_delay_minutes,
        final_observed_estimated_delay_minutes,
        first_observed_planned_platform,
        latest_observed_planned_platform,
        first_observed_estimated_platform,
        latest_observed_estimated_platform,
        platform_change_evidence,
        comparable_platform_observation_count,
        changed_platform_observation_count,
        situation_link_count,
        has_situation_evidence,
        refreshed_at_utc
    )
    SELECT
        date_key,
        scheduled_stop_event_key,
        match_status,
        timetabled_arrival_utc,
        first_observation_key,
        last_observation_key,
        first_observed_at_utc,
        last_observed_at_utc,
        observation_count,
        first_observed_estimated_arrival_utc,
        latest_observed_estimated_arrival_utc,
        first_observed_estimated_delay_minutes,
        final_observed_estimated_delay_minutes,
        first_observed_planned_platform,
        latest_observed_planned_platform,
        first_observed_estimated_platform,
        latest_observed_estimated_platform,
        platform_change_evidence,
        comparable_platform_observation_count,
        changed_platform_observation_count,
        situation_link_count,
        has_situation_evidence,
        clock_timestamp()
    FROM pg_temp.stage10_build
    ON CONFLICT (date_key, scheduled_stop_event_key)
    DO UPDATE SET
        match_status = EXCLUDED.match_status,
        timetabled_arrival_utc = EXCLUDED.timetabled_arrival_utc,
        first_observation_key = EXCLUDED.first_observation_key,
        last_observation_key = EXCLUDED.last_observation_key,
        first_observed_at_utc = EXCLUDED.first_observed_at_utc,
        last_observed_at_utc = EXCLUDED.last_observed_at_utc,
        observation_count = EXCLUDED.observation_count,
        first_observed_estimated_arrival_utc = EXCLUDED.first_observed_estimated_arrival_utc,
        latest_observed_estimated_arrival_utc = EXCLUDED.latest_observed_estimated_arrival_utc,
        first_observed_estimated_delay_minutes = EXCLUDED.first_observed_estimated_delay_minutes,
        final_observed_estimated_delay_minutes = EXCLUDED.final_observed_estimated_delay_minutes,
        first_observed_planned_platform = EXCLUDED.first_observed_planned_platform,
        latest_observed_planned_platform = EXCLUDED.latest_observed_planned_platform,
        first_observed_estimated_platform = EXCLUDED.first_observed_estimated_platform,
        latest_observed_estimated_platform = EXCLUDED.latest_observed_estimated_platform,
        platform_change_evidence = EXCLUDED.platform_change_evidence,
        comparable_platform_observation_count = EXCLUDED.comparable_platform_observation_count,
        changed_platform_observation_count = EXCLUDED.changed_platform_observation_count,
        situation_link_count = EXCLUDED.situation_link_count,
        has_situation_evidence = EXCLUDED.has_situation_evidence,
        refreshed_at_utc = EXCLUDED.refreshed_at_utc;

    -- Reconcile stale derived rows after the complete current build is ready.
    -- No source/staging/control table is deleted or updated here.
    DELETE FROM dw.fact_operational_stop_outcome AS fact
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_temp.stage10_build AS current_build
        WHERE current_build.date_key = fact.date_key
          AND current_build.scheduled_stop_event_key = fact.scheduled_stop_event_key
    );
END;
$procedure$;

-- Project naming convention wrapper for callers that use snake_case names.
CREATE OR REPLACE PROCEDURE dw.usp_refresh_fact_operational_stop_outcome()
LANGUAGE plpgsql
AS $procedure$
BEGIN
    CALL dw."uspRefreshFactOperationalStopOutcome"();
END;
$procedure$;

COMMIT;

-- Stage 10 — Operational Fact: deterministic, explicit-run refresh
-- Target: PostgreSQL 18
--
-- The caller must select the exact COMPLETE Stage 9 execution:
--
--   CALL dw.refresh_operational_service_event(<matching_run_key>, '<matching_version>');
--
-- There is deliberately no implicit "latest" run selection. The input is
-- scoped to that one run/version and starts with:
--
--   core.source_event_match
--       JOIN core.canonical_service_event
--
-- Matching decisions do not contain the operational fields. The procedure
-- therefore requires the future source-owned Stage 10 input projection:
--
--   wrk.v_stage10_operational_observation_input
--
-- This is a derived working-layer interface between future durable source
-- persistence and this fact. It is not a source table and is not created here.
-- It must expose exactly one operational evidence row per source observation
-- and event type, with these logical fields:
--   source_system text, source_namespace text,
--   source_observation_identity text, source_observed_at_utc timestamptz,
--   event_type text, estimated_time_utc timestamptz,
--   planned_bay text, estimated_bay text, situation_link_count bigint
--
-- Stage 10 deliberately does not name the future physical source tables,
-- source internal keys, call collections, or Situation-link storage. The
-- adapter owns that translation without redefining the underlying source
-- persistence contract.
--
-- Arrival/departure semantics remain explicit through event_type. TimetabledTime
-- is not used as the canonical identity: core.canonical_service_event supplies
-- service_date and scheduled_time_seconds.

BEGIN;

CREATE SCHEMA IF NOT EXISTS dw;

CREATE OR REPLACE PROCEDURE dw.refresh_operational_service_event(
    IN p_matching_run_key bigint,
    IN p_matching_version text
)
LANGUAGE plpgsql
AS $procedure$
DECLARE
    adapter_relation          regclass;
    required_column           text;
    invalid_count             bigint;
    expected_high_observation_count bigint;
    joined_high_observation_count   bigint;
    duplicate_observation_count bigint;
    conflicting_event_metadata_count bigint;
    unsupported_source_count  bigint;
BEGIN
    IF p_matching_run_key IS NULL OR NULLIF(btrim(p_matching_version), '') IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'Stage 10 refresh is blocked: an explicit matching_run_key and matching_version are required; implicit latest-run selection is not allowed';
    END IF;

    IF to_regclass('dw.fact_operational_service_event') IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Stage 10 refresh is blocked: dw.fact_operational_service_event is absent; install the Stage 10 physical contract first';
    END IF;

    IF to_regclass('ctl.matching_run') IS NULL
       OR to_regclass('core.source_event_match') IS NULL
       OR to_regclass('core.canonical_service_event') IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Stage 10 refresh is blocked: the current Stage 9 control/core relations are absent (ctl.matching_run, core.source_event_match, core.canonical_service_event)';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM ctl.matching_run AS run
        WHERE run.matching_run_key = p_matching_run_key
          AND run.matching_version = p_matching_version
          AND run.status = 'COMPLETE'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = format(
                'Stage 10 refresh is blocked: matching_run_key %s and matching_version %L are not the same COMPLETE Stage 9 execution',
                p_matching_run_key,
                p_matching_version
            );
    END IF;

    SELECT count(*)
    INTO unsupported_source_count
    FROM core.source_event_match AS sem
    WHERE sem.matching_run_key = p_matching_run_key
      AND sem.matching_version = p_matching_version
      AND sem.match_confidence = 'HIGH'
      AND sem.source_system <> 'MDD_TRIAS';

    IF unsupported_source_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = format(
                'Stage 10 refresh is blocked: %s HIGH Stage 9 rows use a source_system without an approved Stage 10 operational-evidence projection',
                unsupported_source_count
            );
    END IF;

    adapter_relation := to_regclass('wrk.v_stage10_operational_observation_input');

    IF adapter_relation IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'Stage 10 refresh is blocked: required input adapter wrk.v_stage10_operational_observation_input is absent; Stage 10 does not create or guess a physical source schema';
    END IF;

    FOREACH required_column IN ARRAY ARRAY[
        'source_system',
        'source_namespace',
        'source_observation_identity',
        'source_observed_at_utc',
        'event_type',
        'estimated_time_utc',
        'planned_bay',
        'estimated_bay',
        'situation_link_count'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute
            WHERE attrelid = adapter_relation
              AND attname = required_column
              AND attnum > 0
              AND NOT attisdropped
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = format(
                    'Stage 10 refresh is blocked: %s is missing required Stage 10 input column %s',
                    adapter_relation,
                    required_column
                );
        END IF;
    END LOOP;

    DROP TABLE IF EXISTS pg_temp.stage10_refresh_input;
    DROP TABLE IF EXISTS pg_temp.stage10_refresh_build;

    -- This is the only operational input. It is deliberately scoped to the
    -- caller-selected run/version and joins Stage 9 matching lineage to the
    -- canonical scheduled event before reading source operational evidence.
    EXECUTE format($sql$
        CREATE TEMP TABLE stage10_refresh_input ON COMMIT DROP AS
        SELECT
            sem.source_event_match_key,
            sem.matching_run_key,
            sem.matching_version,
            sem.source_system,
            sem.source_namespace,
            sem.source_observation_identity,
            sem.source_observed_at_utc,
            operational_input.source_system AS input_source_system,
            operational_input.source_namespace AS input_source_namespace,
            operational_input.source_observation_identity AS input_source_observation_identity,
            operational_input.source_observed_at_utc AS input_source_observed_at_utc,
            operational_input.event_type AS input_event_type,
            sem.match_confidence,
            sem.match_method,
            sem.data_quality_classification,
            cse.canonical_service_event_key,
            cse.canonical_service_event_id,
            cse.service_date,
            cse.canonical_stop_id,
            cse.canonical_line_id,
            cse.event_type,
            cse.scheduled_time_seconds,
            (
                cse.service_date::timestamp
                + (cse.scheduled_time_seconds * interval '1 second')
            ) AT TIME ZONE 'Europe/Berlin' AS canonical_scheduled_time_utc,
            operational_input.estimated_time_utc,
            NULLIF(btrim(operational_input.planned_bay::text), '') AS planned_bay,
            NULLIF(btrim(operational_input.estimated_bay::text), '') AS estimated_bay,
            operational_input.situation_link_count
        FROM core.source_event_match AS sem
        JOIN core.canonical_service_event AS cse
          ON cse.canonical_service_event_key = sem.canonical_service_event_key
         AND cse.matching_version = sem.matching_version
        JOIN %s AS operational_input
          ON operational_input.source_system = sem.source_system
         AND operational_input.source_namespace = sem.source_namespace
         AND operational_input.source_observation_identity = sem.source_observation_identity
         AND operational_input.event_type = cse.event_type
        WHERE sem.matching_run_key = $1
          AND sem.matching_version = $2
          AND sem.source_system = 'MDD_TRIAS'
          AND sem.match_confidence = 'HIGH'
          AND sem.canonical_service_event_key IS NOT NULL
    $sql$, adapter_relation)
    USING p_matching_run_key, p_matching_version;

    CREATE INDEX stage10_refresh_input_event_ix
        ON pg_temp.stage10_refresh_input
           (service_date, canonical_service_event_key, source_observed_at_utc, source_observation_identity);

    SELECT count(*)
    INTO expected_high_observation_count
    FROM core.source_event_match AS sem
    WHERE sem.matching_run_key = p_matching_run_key
      AND sem.matching_version = p_matching_version
      AND sem.source_system = 'MDD_TRIAS'
      AND sem.match_confidence = 'HIGH';

    SELECT count(*)
    INTO joined_high_observation_count
    FROM pg_temp.stage10_refresh_input;

    IF joined_high_observation_count IS DISTINCT FROM expected_high_observation_count THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = format(
                'Stage 10 refresh is blocked: %s HIGH Stage 9 source observations were eligible but only %s joined to the Stage 10 input adapter; no eligible row is silently omitted',
                expected_high_observation_count,
                joined_high_observation_count
            );
    END IF;

    SELECT count(*)
    INTO invalid_count
    FROM pg_temp.stage10_refresh_input
    WHERE matching_run_key IS DISTINCT FROM p_matching_run_key
       OR matching_version IS DISTINCT FROM p_matching_version
       OR canonical_service_event_key IS NULL
       OR service_date IS NULL
       OR match_confidence IS DISTINCT FROM 'HIGH'
       OR NULLIF(btrim(source_observation_identity), '') IS NULL
       OR source_observed_at_utc IS NULL
       OR input_source_observed_at_utc IS NULL
       OR input_source_observed_at_utc IS DISTINCT FROM source_observed_at_utc
       OR input_event_type IS DISTINCT FROM event_type
       OR situation_link_count IS NULL
       OR situation_link_count < 0;

    IF invalid_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'Stage 10 refresh rejected %s source-backed rows with incomplete or inconsistent selected-run lineage',
                invalid_count
            );
    END IF;

    SELECT count(*)
    INTO duplicate_observation_count
    FROM (
        SELECT source_system, source_namespace, source_observation_identity
        FROM pg_temp.stage10_refresh_input
        GROUP BY source_system, source_namespace, source_observation_identity
        HAVING count(*) > 1
    ) AS duplicates;

    IF duplicate_observation_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = format(
                'Stage 10 refresh rejected %s duplicated selected-run source observations; historical matching runs are not aggregated',
                duplicate_observation_count
            );
    END IF;

    -- Stage 9 does not require match_method or data_quality_classification to
    -- remain identical across repeated HIGH observations for one canonical
    -- event. Those valid values are governed below by the deterministic latest
    -- source observation; MIN() is deliberately not used as a textual tie-break.
    SELECT count(*)
    INTO conflicting_event_metadata_count
    FROM (
        SELECT service_date, canonical_service_event_key
        FROM pg_temp.stage10_refresh_input
        GROUP BY service_date, canonical_service_event_key
        HAVING count(DISTINCT matching_run_key) > 1
            OR count(DISTINCT matching_version) > 1
            OR count(DISTINCT match_confidence) > 1
    ) AS conflicts;

    IF conflicting_event_metadata_count > 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'Stage 10 refresh rejected %s canonical service-event grains with conflicting selected-run or Stage 9 confidence metadata',
                conflicting_event_metadata_count
            );
    END IF;

    CREATE TEMP TABLE stage10_refresh_build ON COMMIT DROP AS
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
        FROM pg_temp.stage10_refresh_input AS input
    ),
    grouped AS (
        SELECT
            service_date,
            canonical_service_event_key,
            min(canonical_service_event_id) AS canonical_service_event_id,
            min(matching_run_key) AS matching_run_key,
            min(matching_version) AS matching_version,
            min(canonical_stop_id) AS canonical_stop_id,
            min(canonical_line_id) AS canonical_line_id,
            min(event_type) AS event_type,
            min(scheduled_time_seconds) AS scheduled_time_seconds,
            min(canonical_scheduled_time_utc) AS canonical_scheduled_time_utc,
            min(match_confidence) AS match_confidence,
            count(*)::bigint AS source_observation_count,
            count(*) FILTER (
                WHERE planned_bay IS NOT NULL
                  AND estimated_bay IS NOT NULL
            )::bigint AS comparable_bay_observation_count,
            count(*) FILTER (
                WHERE planned_bay IS NOT NULL
                  AND estimated_bay IS NOT NULL
                  AND planned_bay IS DISTINCT FROM estimated_bay
            )::bigint AS changed_bay_observation_count,
            sum(situation_link_count)::bigint AS situation_link_count
        FROM ranked
        GROUP BY service_date, canonical_service_event_key
    ),
    first_rows AS (
        SELECT * FROM ranked WHERE first_row_number = 1
    ),
    latest_rows AS (
        SELECT * FROM ranked WHERE latest_row_number = 1
    )
    SELECT
        grouped.service_date,
        grouped.canonical_service_event_key,
        grouped.canonical_service_event_id,
        grouped.matching_run_key,
        grouped.matching_version,
        grouped.canonical_stop_id,
        grouped.canonical_line_id,
        grouped.event_type,
        grouped.scheduled_time_seconds,
        grouped.canonical_scheduled_time_utc,
        grouped.match_confidence,
        latest_rows.match_method,
        latest_rows.data_quality_classification,
        grouped.source_observation_count,
        first_rows.source_event_match_key AS first_source_event_match_key,
        latest_rows.source_event_match_key AS latest_source_event_match_key,
        first_rows.source_observation_identity AS first_source_observation_identity,
        latest_rows.source_observation_identity AS latest_source_observation_identity,
        first_rows.source_observed_at_utc AS first_source_observed_at_utc,
        latest_rows.source_observed_at_utc AS latest_source_observed_at_utc,
        first_rows.estimated_time_utc AS first_estimated_time_utc,
        latest_rows.estimated_time_utc AS latest_estimated_time_utc,
        CASE
            WHEN first_rows.estimated_time_utc IS NULL THEN NULL
            ELSE round(
                (extract(epoch FROM (
                    first_rows.estimated_time_utc - grouped.canonical_scheduled_time_utc
                )) / 60.0)::numeric,
                3
            )
        END AS first_estimated_delay_minutes,
        CASE
            WHEN latest_rows.estimated_time_utc IS NULL THEN NULL
            ELSE round(
                (extract(epoch FROM (
                    latest_rows.estimated_time_utc - grouped.canonical_scheduled_time_utc
                )) / 60.0)::numeric,
                3
            )
        END AS latest_estimated_delay_minutes,
        first_rows.planned_bay AS first_planned_bay,
        latest_rows.planned_bay AS latest_planned_bay,
        first_rows.estimated_bay AS first_estimated_bay,
        latest_rows.estimated_bay AS latest_estimated_bay,
        CASE
            WHEN grouped.comparable_bay_observation_count = 0 THEN 'Unknown'
            WHEN grouped.changed_bay_observation_count > 0 THEN 'Changed'
            ELSE 'Unchanged'
        END AS bay_evidence,
        grouped.comparable_bay_observation_count,
        grouped.changed_bay_observation_count,
        grouped.situation_link_count,
        (grouped.situation_link_count > 0) AS has_situation_evidence
    FROM grouped
    JOIN first_rows
      ON first_rows.service_date = grouped.service_date
     AND first_rows.canonical_service_event_key = grouped.canonical_service_event_key
    JOIN latest_rows
      ON latest_rows.service_date = grouped.service_date
     AND latest_rows.canonical_service_event_key = grouped.canonical_service_event_key;

    CREATE UNIQUE INDEX stage10_refresh_build_grain_uq
        ON pg_temp.stage10_refresh_build (service_date, canonical_service_event_key);

    INSERT INTO dw.fact_operational_service_event (
        service_date,
        canonical_service_event_key,
        canonical_service_event_id,
        matching_run_key,
        matching_version,
        canonical_stop_id,
        canonical_line_id,
        event_type,
        scheduled_time_seconds,
        canonical_scheduled_time_utc,
        match_confidence,
        match_method,
        data_quality_classification,
        source_observation_count,
        first_source_event_match_key,
        latest_source_event_match_key,
        first_source_observation_identity,
        latest_source_observation_identity,
        first_source_observed_at_utc,
        latest_source_observed_at_utc,
        first_estimated_time_utc,
        latest_estimated_time_utc,
        first_estimated_delay_minutes,
        latest_estimated_delay_minutes,
        first_planned_bay,
        latest_planned_bay,
        first_estimated_bay,
        latest_estimated_bay,
        bay_evidence,
        comparable_bay_observation_count,
        changed_bay_observation_count,
        situation_link_count,
        has_situation_evidence,
        refreshed_at_utc
    )
    SELECT
        service_date,
        canonical_service_event_key,
        canonical_service_event_id,
        matching_run_key,
        matching_version,
        canonical_stop_id,
        canonical_line_id,
        event_type,
        scheduled_time_seconds,
        canonical_scheduled_time_utc,
        match_confidence,
        match_method,
        data_quality_classification,
        source_observation_count,
        first_source_event_match_key,
        latest_source_event_match_key,
        first_source_observation_identity,
        latest_source_observation_identity,
        first_source_observed_at_utc,
        latest_source_observed_at_utc,
        first_estimated_time_utc,
        latest_estimated_time_utc,
        first_estimated_delay_minutes,
        latest_estimated_delay_minutes,
        first_planned_bay,
        latest_planned_bay,
        first_estimated_bay,
        latest_estimated_bay,
        bay_evidence,
        comparable_bay_observation_count,
        changed_bay_observation_count,
        situation_link_count,
        has_situation_evidence,
        clock_timestamp()
    FROM pg_temp.stage10_refresh_build
    ON CONFLICT (service_date, canonical_service_event_key)
    DO UPDATE SET
        canonical_service_event_id = EXCLUDED.canonical_service_event_id,
        matching_run_key = EXCLUDED.matching_run_key,
        matching_version = EXCLUDED.matching_version,
        canonical_stop_id = EXCLUDED.canonical_stop_id,
        canonical_line_id = EXCLUDED.canonical_line_id,
        event_type = EXCLUDED.event_type,
        scheduled_time_seconds = EXCLUDED.scheduled_time_seconds,
        canonical_scheduled_time_utc = EXCLUDED.canonical_scheduled_time_utc,
        match_confidence = EXCLUDED.match_confidence,
        match_method = EXCLUDED.match_method,
        data_quality_classification = EXCLUDED.data_quality_classification,
        source_observation_count = EXCLUDED.source_observation_count,
        first_source_event_match_key = EXCLUDED.first_source_event_match_key,
        latest_source_event_match_key = EXCLUDED.latest_source_event_match_key,
        first_source_observation_identity = EXCLUDED.first_source_observation_identity,
        latest_source_observation_identity = EXCLUDED.latest_source_observation_identity,
        first_source_observed_at_utc = EXCLUDED.first_source_observed_at_utc,
        latest_source_observed_at_utc = EXCLUDED.latest_source_observed_at_utc,
        first_estimated_time_utc = EXCLUDED.first_estimated_time_utc,
        latest_estimated_time_utc = EXCLUDED.latest_estimated_time_utc,
        first_estimated_delay_minutes = EXCLUDED.first_estimated_delay_minutes,
        latest_estimated_delay_minutes = EXCLUDED.latest_estimated_delay_minutes,
        first_planned_bay = EXCLUDED.first_planned_bay,
        latest_planned_bay = EXCLUDED.latest_planned_bay,
        first_estimated_bay = EXCLUDED.first_estimated_bay,
        latest_estimated_bay = EXCLUDED.latest_estimated_bay,
        bay_evidence = EXCLUDED.bay_evidence,
        comparable_bay_observation_count = EXCLUDED.comparable_bay_observation_count,
        changed_bay_observation_count = EXCLUDED.changed_bay_observation_count,
        situation_link_count = EXCLUDED.situation_link_count,
        has_situation_evidence = EXCLUDED.has_situation_evidence,
        refreshed_at_utc = EXCLUDED.refreshed_at_utc;

    -- The fact is a current derived projection of the selected execution.
    -- Reconciliation removes stale derived rows only; source/core/control
    -- history is never deleted or updated here.
    DELETE FROM dw.fact_operational_service_event AS fact
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_temp.stage10_refresh_build AS current_build
        WHERE current_build.service_date = fact.service_date
          AND current_build.canonical_service_event_key = fact.canonical_service_event_key
    );
END;
$procedure$;

COMMENT ON PROCEDURE dw.refresh_operational_service_event(bigint, text) IS
    'Stage 10 explicit-run refresh. Uses only one COMPLETE matching_run_key/matching_version, admits HIGH decisions only, joins the namespace-qualified Stage 9 source identity tuple to the Stage 10 input adapter, takes match_method and data_quality_classification from the deterministic latest observation, and reads no guessed physical source schema.';

COMMIT;

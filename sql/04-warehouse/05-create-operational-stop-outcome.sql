-- Stage 10 — Operational Fact: physical contract
-- Target: PostgreSQL 18 (latest supported minor at implementation time)
--
-- This file creates only the derived operational outcome fact.  It does not
-- create or mutate source observation history, the collector, the Stage 9
-- matcher, the static scheduled-event load, or any downstream model.
--
-- The canonical PostgreSQL name is snake_case.  The quoted compatibility view
-- at the end preserves the Stage 10 logical name used in the project brief:
-- dw.FactOperationalStopOutcome.

BEGIN;

CREATE SCHEMA IF NOT EXISTS dw;

CREATE TABLE IF NOT EXISTS dw.fact_operational_stop_outcome (
    operational_stop_outcome_key                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date_key                                    date NOT NULL,
    scheduled_stop_event_key                    bigint NOT NULL,
    match_status                                text NOT NULL CHECK (
        match_status IN ('ExactStopMatch', 'ParentStationFallback')
    ),

    -- The matched scheduled event supplies one timetable value.  The refresh
    -- procedure rejects a grain whose source rows disagree on that value.
    timetabled_arrival_utc                      timestamptz NOT NULL,

    first_observation_key                        text NOT NULL,
    last_observation_key                         text NOT NULL,
    first_observed_at_utc                        timestamptz NOT NULL,
    last_observed_at_utc                         timestamptz NOT NULL,
    observation_count                            bigint NOT NULL CHECK (observation_count > 0),

    first_observed_estimated_arrival_utc         timestamptz,
    latest_observed_estimated_arrival_utc        timestamptz,
    first_observed_estimated_delay_minutes      numeric(12, 3),
    final_observed_estimated_delay_minutes      numeric(12, 3),

    first_observed_planned_platform              text,
    latest_observed_planned_platform             text,
    first_observed_estimated_platform            text,
    latest_observed_estimated_platform           text,
    platform_change_evidence                     text NOT NULL CHECK (
        platform_change_evidence IN ('Changed', 'Unchanged', 'Unknown')
    ),
    comparable_platform_observation_count        bigint NOT NULL CHECK (
        comparable_platform_observation_count >= 0
    ),
    changed_platform_observation_count           bigint NOT NULL CHECK (
        changed_platform_observation_count >= 0
    ),

    -- This is the count of persisted observation-to-situation link rows for
    -- the consolidated observations.  It is not a count of distinct
    -- situations and it is not causal evidence.
    situation_link_count                         bigint NOT NULL CHECK (situation_link_count >= 0),
    has_situation_evidence                       boolean NOT NULL,

    refreshed_at_utc                             timestamptz NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_fact_operational_stop_outcome_grain
        UNIQUE (date_key, scheduled_stop_event_key),
    CONSTRAINT ck_fact_operational_stop_outcome_observation_order
        CHECK (last_observed_at_utc >= first_observed_at_utc),
    CONSTRAINT ck_fact_operational_stop_outcome_platform_counts
        CHECK (changed_platform_observation_count <= comparable_platform_observation_count),
    CONSTRAINT ck_fact_operational_stop_outcome_situation_evidence
        CHECK (has_situation_evidence = (situation_link_count > 0))
);

CREATE INDEX IF NOT EXISTS ix_fact_operational_stop_outcome_date
    ON dw.fact_operational_stop_outcome (date_key, scheduled_stop_event_key);

CREATE OR REPLACE VIEW dw."FactOperationalStopOutcome" AS
SELECT
    operational_stop_outcome_key,
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
FROM dw.fact_operational_stop_outcome;

COMMENT ON TABLE dw.fact_operational_stop_outcome IS
    'Stage 10 derived fact: exactly one consolidated operational outcome per approved usable matched scheduled stop event and service date.';

COMMENT ON COLUMN dw.fact_operational_stop_outcome.observation_count IS
    'Count of source observation rows in wrk.vwCologneRealtimeTripMatch for this DateKey/ScheduledStopEventKey grain.';

COMMENT ON COLUMN dw.fact_operational_stop_outcome.latest_observed_estimated_arrival_utc IS
    'Estimated arrival from the chronologically latest observation, including NULL; never the latest non-NULL historical estimate.';

COMMENT ON COLUMN dw.fact_operational_stop_outcome.situation_link_count IS
    'Count of persisted observation-to-situation link rows across the consolidated source observations; not distinct situations and not causal evidence.';

COMMENT ON COLUMN dw.fact_operational_stop_outcome.platform_change_evidence IS
    'Changed when any comparable planned/estimated platform pair differs; Unchanged when comparable pairs exist and all are equal; Unknown when no comparable pair exists.';

COMMIT;

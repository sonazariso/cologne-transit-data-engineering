-- Stage 10 — Operational Fact: physical contract
-- Target: PostgreSQL 18 (latest supported minor at implementation time)
--
-- The canonical Stage 10 object is dw.fact_operational_service_event.
-- This is a current, rebuildable analytical fact. It is not a second source
-- observation store and it does not recreate any legacy SQL Server object.
--
-- The final grain is one consolidated operational outcome for one approved
-- canonical service event on its canonical service_date, refreshed from one
-- explicitly selected COMPLETE Stage 9 matching run/version. Source
-- observation lineage and source operational evidence remain separate from
-- the Stage 9 matching decision.

BEGIN;

CREATE SCHEMA IF NOT EXISTS dw;

CREATE TABLE IF NOT EXISTS dw.fact_operational_service_event (
    operational_service_event_key          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Canonical scheduled-event identity and explicit Stage 9 execution lineage.
    service_date                            date NOT NULL,
    canonical_service_event_key             bigint NOT NULL,
    canonical_service_event_id              text NOT NULL,
    matching_run_key                        bigint NOT NULL,
    matching_version                        text NOT NULL CHECK (length(btrim(matching_version)) > 0),
    canonical_stop_id                       text NOT NULL CHECK (length(btrim(canonical_stop_id)) > 0),
    canonical_line_id                       text,
    event_type                              text NOT NULL CHECK (event_type IN ('ARRIVAL', 'DEPARTURE')),
    scheduled_time_seconds                  integer NOT NULL CHECK (scheduled_time_seconds >= 0),
    canonical_scheduled_time_utc            timestamptz NOT NULL,

    -- Stage 9 decision and data-quality lineage. Only HIGH decisions are
    -- eligible for this fact; the classification is retained separately.
    match_confidence                        text NOT NULL CHECK (match_confidence = 'HIGH'),
    match_method                            text NOT NULL CHECK (length(btrim(match_method)) > 0),
    data_quality_classification             text NOT NULL CHECK (length(btrim(data_quality_classification)) > 0),

    -- One row per durable source observation is consolidated below. The
    -- observation identity and source-observed timestamp are the deterministic
    -- first/latest lineage, never matching execution time.
    source_observation_count               bigint NOT NULL CHECK (source_observation_count > 0),
    first_source_event_match_key           bigint NOT NULL,
    latest_source_event_match_key          bigint NOT NULL,
    first_source_observation_identity      text NOT NULL,
    latest_source_observation_identity     text NOT NULL,
    first_source_observed_at_utc           timestamptz NOT NULL,
    latest_source_observed_at_utc          timestamptz NOT NULL,

    -- Source operational evidence. These are estimates, not actual times.
    first_estimated_time_utc               timestamptz,
    latest_estimated_time_utc              timestamptz,
    first_estimated_delay_minutes          numeric(12, 3),
    latest_estimated_delay_minutes         numeric(12, 3),

    -- PlannedBay and EstimatedBay remain distinct source meanings.
    first_planned_bay                       text,
    latest_planned_bay                      text,
    first_estimated_bay                     text,
    latest_estimated_bay                    text,
    bay_evidence                            text NOT NULL CHECK (
        bay_evidence IN ('Changed', 'Unchanged', 'Unknown')
    ),
    comparable_bay_observation_count        bigint NOT NULL CHECK (comparable_bay_observation_count >= 0),
    changed_bay_observation_count           bigint NOT NULL CHECK (changed_bay_observation_count >= 0),

    -- Count of persisted observation-to-Situation association rows. This is
    -- evidence only and is not a distinct-Situation count or causal claim.
    situation_link_count                    bigint NOT NULL CHECK (situation_link_count >= 0),
    has_situation_evidence                  boolean NOT NULL,

    refreshed_at_utc                        timestamptz NOT NULL DEFAULT clock_timestamp(),

    -- The selected run/version is refresh lineage, not an additional business
    -- grain. A current fact contains one row per service_date/event identity.
    CONSTRAINT uq_fact_operational_service_event_grain
        UNIQUE (service_date, canonical_service_event_key),
    CONSTRAINT fk_fact_operational_service_event_matching_run
        FOREIGN KEY (matching_run_key)
        REFERENCES ctl.matching_run (matching_run_key),
    CONSTRAINT fk_fact_operational_service_event_canonical_event
        FOREIGN KEY (matching_version, canonical_service_event_key)
        REFERENCES core.canonical_service_event (matching_version, canonical_service_event_key),
    CONSTRAINT fk_fact_operational_service_event_first_source_match
        FOREIGN KEY (first_source_event_match_key)
        REFERENCES core.source_event_match (source_event_match_key),
    CONSTRAINT fk_fact_operational_service_event_latest_source_match
        FOREIGN KEY (latest_source_event_match_key)
        REFERENCES core.source_event_match (source_event_match_key),
    CONSTRAINT ck_fact_operational_service_event_observation_order
        CHECK (latest_source_observed_at_utc >= first_source_observed_at_utc),
    CONSTRAINT ck_fact_operational_service_event_bay_counts
        CHECK (changed_bay_observation_count <= comparable_bay_observation_count),
    CONSTRAINT ck_fact_operational_service_event_situation_evidence
        CHECK (has_situation_evidence = (situation_link_count > 0))
);

-- CREATE TABLE IF NOT EXISTS does not add constraints to a fact table created
-- by an earlier Stage 10 implementation. Reassert the same Stage 9-supported
-- relationships idempotently for that upgrade path.
DO $constraint_upgrade$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dw.fact_operational_service_event'::regclass
          AND conname = 'fk_fact_operational_service_event_matching_run'
    ) THEN
        ALTER TABLE dw.fact_operational_service_event
            ADD CONSTRAINT fk_fact_operational_service_event_matching_run
            FOREIGN KEY (matching_run_key)
            REFERENCES ctl.matching_run (matching_run_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dw.fact_operational_service_event'::regclass
          AND conname = 'fk_fact_operational_service_event_canonical_event'
    ) THEN
        ALTER TABLE dw.fact_operational_service_event
            ADD CONSTRAINT fk_fact_operational_service_event_canonical_event
            FOREIGN KEY (matching_version, canonical_service_event_key)
            REFERENCES core.canonical_service_event (matching_version, canonical_service_event_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dw.fact_operational_service_event'::regclass
          AND conname = 'fk_fact_operational_service_event_first_source_match'
    ) THEN
        ALTER TABLE dw.fact_operational_service_event
            ADD CONSTRAINT fk_fact_operational_service_event_first_source_match
            FOREIGN KEY (first_source_event_match_key)
            REFERENCES core.source_event_match (source_event_match_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'dw.fact_operational_service_event'::regclass
          AND conname = 'fk_fact_operational_service_event_latest_source_match'
    ) THEN
        ALTER TABLE dw.fact_operational_service_event
            ADD CONSTRAINT fk_fact_operational_service_event_latest_source_match
            FOREIGN KEY (latest_source_event_match_key)
            REFERENCES core.source_event_match (source_event_match_key);
    END IF;
END;
$constraint_upgrade$;

CREATE INDEX IF NOT EXISTS ix_fact_operational_service_event_lineage
    ON dw.fact_operational_service_event
       (matching_run_key, matching_version, service_date, canonical_service_event_key);

CREATE INDEX IF NOT EXISTS ix_fact_operational_service_event_date
    ON dw.fact_operational_service_event
       (service_date, canonical_stop_id, event_type, canonical_service_event_key);

COMMENT ON TABLE dw.fact_operational_service_event IS
    'Stage 10 current derived fact: one consolidated operational outcome per approved HIGH-confidence canonical service event and canonical service_date for one explicitly selected Stage 9 matching run/version.';

COMMENT ON COLUMN dw.fact_operational_service_event.service_date IS
    'Authoritative business date from core.canonical_service_event.service_date; this is not a date surrogate key.';

COMMENT ON COLUMN dw.fact_operational_service_event.canonical_scheduled_time_utc IS
    'Canonical timetable derived from service_date plus scheduled_time_seconds using the approved Europe/Berlin service-day interpretation; it is not source ObservedAt or ActualTime.';

COMMENT ON COLUMN dw.fact_operational_service_event.match_confidence IS
    'Stage 9 eligibility is exact: only HIGH is admitted. MEDIUM remains contextual and is not promoted into this fact.';

COMMENT ON COLUMN dw.fact_operational_service_event.latest_estimated_time_utc IS
    'EstimatedTime from the chronologically latest source observation, including NULL; it is never the latest non-NULL estimate and is not ActualTime.';

COMMENT ON COLUMN dw.fact_operational_service_event.bay_evidence IS
    'Changed when a comparable PlannedBay/EstimatedBay pair differs; Unchanged when comparable pairs exist and all agree; Unknown when no comparable pair exists. This is not proof of the physical bay used.';

COMMENT ON COLUMN dw.fact_operational_service_event.situation_link_count IS
    'Count of persisted observation-to-Situation association rows across the consolidated source observations; not a distinct Situation count and not causal evidence.';

COMMIT;

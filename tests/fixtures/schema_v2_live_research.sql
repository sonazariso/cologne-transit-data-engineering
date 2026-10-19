-- Retained pre-S79 LIVE_RESEARCH contract (schema version 2).
-- timing_observations, service_section_observations, and their reporting views
-- intentionally do not exist in this fixture.
PRAGMA foreign_keys = ON;

CREATE TABLE manifest_targets (
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    station_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    cadence_minutes INTEGER NOT NULL,
    number_of_results INTEGER NOT NULL,
    phase_index INTEGER NOT NULL,
    phase_count INTEGER NOT NULL,
    continuity_classification TEXT NOT NULL,
    sampling_era TEXT NOT NULL,
    production_sampling_target_id TEXT,
    legacy_sampling_target_id INTEGER,
    PRIMARY KEY (panel_version, source_parent_station_id)
);

CREATE TABLE collector_runs (
    run_id TEXT PRIMARY KEY,
    panel_version TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    mode TEXT NOT NULL,
    collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT,
    summary_json TEXT
);

CREATE TABLE dispatcher_claims (
    panel_version TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    collector_run_id TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    claimed_at_utc TEXT NOT NULL,
    stale_after_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (panel_version, logical_due_bucket)
);

CREATE TABLE dispatcher_claim_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_version TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    collector_run_id TEXT NOT NULL,
    event_at_utc TEXT NOT NULL,
    outcome TEXT NOT NULL,
    existing_status TEXT
);

CREATE TABLE target_claims (
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    collector_run_id TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    claimed_at_utc TEXT NOT NULL,
    stale_after_utc TEXT NOT NULL,
    started_at_utc TEXT,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (panel_version, source_parent_station_id, logical_due_bucket)
);

CREATE TABLE claim_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    collector_run_id TEXT NOT NULL,
    event_at_utc TEXT NOT NULL,
    outcome TEXT NOT NULL,
    existing_status TEXT
);

CREATE TABLE target_audits (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_run_id TEXT NOT NULL,
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    station_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    phase_index INTEGER NOT NULL,
    number_of_results INTEGER NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    sampling_era TEXT NOT NULL,
    continuity_classification TEXT NOT NULL,
    collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT,
    http_status INTEGER,
    http_attempts INTEGER NOT NULL DEFAULT 0,
    stop_event_result_count INTEGER,
    parser_outcome TEXT,
    persistence_outcome TEXT,
    duration_ms REAL,
    skip_reason TEXT,
    error_category TEXT,
    retry_decision TEXT,
    retry_outcome TEXT
);

CREATE TABLE http_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    http_status INTEGER,
    transport_failure_category TEXT,
    outcome TEXT NOT NULL,
    retry_decision TEXT,
    retry_after_seconds REAL,
    duration_ms REAL NOT NULL,
    transport_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    network_attempted INTEGER NOT NULL DEFAULT 0,
    http_response_received INTEGER NOT NULL DEFAULT 0,
    live_client_invocation INTEGER NOT NULL DEFAULT 0,
    conservative_quota_accounted INTEGER NOT NULL DEFAULT 0,
    provider_quota_status TEXT NOT NULL DEFAULT 'NOT_CONSUMED',
    provider_attempt_confirmed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id)
);

CREATE TABLE parsed_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL,
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    persisted_at_utc TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    raw_payload_persisted INTEGER NOT NULL DEFAULT 0,
    collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
    FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id)
);

CREATE TABLE quota_audit (
    quota_audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    collector_run_id TEXT NOT NULL,
    source_parent_station_id TEXT,
    logical_due_bucket INTEGER,
    decision_kind TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'
);

CREATE TABLE quota_permits (
    permit_id TEXT PRIMARY KEY,
    month_key TEXT NOT NULL,
    decision_kind TEXT NOT NULL,
    reservation_scope TEXT NOT NULL,
    amount INTEGER NOT NULL,
    boundary INTEGER NOT NULL,
    reserved_at_utc TEXT NOT NULL,
    released_at_utc TEXT,
    finalized_at_utc TEXT,
    finalization_reason TEXT,
    status TEXT NOT NULL
);

CREATE TABLE recorded_at_time_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL,
    snapshot_id INTEGER NOT NULL,
    panel_version TEXT NOT NULL,
    source_parent_station_id TEXT NOT NULL,
    logical_due_bucket INTEGER NOT NULL,
    event_ordinal INTEGER,
    result_id TEXT,
    call_scope TEXT NOT NULL,
    call_ordinal INTEGER NOT NULL,
    timing_context TEXT NOT NULL,
    recorded_at_time TEXT NOT NULL,
    source_path TEXT,
    observed_at_utc TEXT NOT NULL,
    collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
    FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id),
    FOREIGN KEY (snapshot_id) REFERENCES parsed_snapshots(snapshot_id)
);

CREATE TABLE shared_usage (
    month_key TEXT PRIMARY KEY,
    confirmed_provider_attempts INTEGER NOT NULL DEFAULT 0,
    reserved_shared_usage INTEGER NOT NULL DEFAULT 0,
    projected_non_retry_attempts INTEGER NOT NULL DEFAULT 0,
    conservative_live_attempts INTEGER NOT NULL DEFAULT 0,
    live_client_invocations INTEGER NOT NULL DEFAULT 0,
    http_responses_received INTEGER NOT NULL DEFAULT 0,
    provider_quota_unknown_attempts INTEGER NOT NULL DEFAULT 0,
    state_available INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'sqlite-development'
);

CREATE TABLE sampling_eras (
    era_code TEXT NOT NULL,
    panel_version TEXT NOT NULL,
    effective_at_utc TEXT,
    timestamp_kind TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (era_code, panel_version)
);

CREATE TABLE database_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value TEXT NOT NULL
);

CREATE TABLE raw_evidence (
    raw_evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER,
    collector_run_id TEXT,
    source_parent_station_id TEXT,
    station_name TEXT,
    logical_due_bucket INTEGER,
    collection_context TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    delete_after_utc TEXT NOT NULL,
    content_type TEXT,
    byte_length INTEGER NOT NULL DEFAULT 0,
    file_path TEXT,
    status TEXT NOT NULL,
    deleted_at_utc TEXT,
    refusal_reason TEXT
);

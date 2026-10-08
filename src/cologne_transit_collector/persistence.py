"""SQLite persistence for offline development and the S78 research pilot.

The same implementation serves two explicit profiles.  Development state is
still disposable and is used by the existing simulation harness.  The
``LIVE_RESEARCH`` profile is durable append-only evidence with additive schema
metadata, protected temporary raw evidence, and SQLite-consistent daily
backups.  No database-server adapter is introduced here.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .domain import (
    HttpResponse,
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    MAX_ATTEMPTS,
    PersistenceProfile,
    SamplingEra,
    Target,
    TransportFailure,
)
from .quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
    UsageSnapshot,
)
from .timing import iter_timing_observations
from .trias import iter_service_section_observations, parse_trias_response


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _month_key(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def _safe_error_category(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).upper()
    allowed = {
        "HTTP_ERROR",
        "TRANSPORT_FAILURE",
        "TIMEOUT",
        "DNS_RESOLUTION_FAILED",
        "NETWORK_UNREACHABLE",
        "CONNECTION_REFUSED",
        "TLS_FAILURE",
        "PROXY_OR_ENVIRONMENT_FAILURE",
        "OTHER_NETWORK_FAILURE",
        "PARSER_FAILURE",
        "TRIAS_APPLICATION_ERROR",
        "PERSISTENCE_FAILURE",
        "QUOTA_DENIED",
        "SHUTDOWN",
        "CLAIM_DUPLICATE",
        "CREDENTIAL_MISSING",
        "CREDENTIAL_FORMAT_INVALID",
        "REQUEST_CONTRACT_FAILURE",
        "RESPONSE_SIZE_EXCEEDED",
        "SANITIZED_ERROR",
    }
    return text if text in allowed else "SANITIZED_ERROR"


SCHEMA_VERSION = "5"
DEFAULT_COLLECTION_CONTEXT = "OFFLINE_DEVELOPMENT"
UNKNOWN_SHARED_QUOTA_SOURCE = "UNKNOWN_SHARED_PROVIDER_BASELINE"
RAW_RETENTION_HOURS = 24
SCHEMA_MIGRATION_STATUS_KEY = "schema_migration_status"
SCHEMA_MIGRATION_SOURCE_VERSION_KEY = "schema_migration_source_version"
SCHEMA_MIGRATION_STARTED_AT_KEY = "schema_migration_started_at_utc"
SCHEMA_MIGRATION_COMPLETE = "COMPLETE"
SCHEMA_MIGRATION_IN_PROGRESS = "IN_PROGRESS"
_BACKUP_NAME = re.compile(r"^collector-(?P<date>\d{4}-\d{2}-\d{2})\.sqlite3$")
_BACKUP_TEMP_NAME = re.compile(
    r"^\.collector-\d{4}-\d{2}-\d{2}-[0-9a-f]{32}\.tmp(?:-(?:wal|shm|journal))?$"
)


_RECORDED_AT_TIME_REPORTING_SELECT = """
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
        recorded_at_time_source_path,
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
    CASE
        WHEN latest.recorded_at_time IS NOT NULL
        THEN latest.recorded_at_time_source_path
        ELSE NULL
    END AS LatestSourceRecordedAtTimeSourcePath,
    latest.timing_observation_id AS LatestSourceRecordedAtTimeObservationId,
    last_nonnull.recorded_at_time AS LastObservedRecordedAtTime,
    last_nonnull.observed_at_utc AS LastObservedRecordedAtTimeObservedAt,
    last_nonnull.snapshot_id AS LastObservedRecordedAtTimeSnapshotId,
    last_nonnull.audit_id AS LastObservedRecordedAtTimeAuditId,
    last_nonnull.recorded_at_time_source_path AS LastObservedRecordedAtTimeSourcePath,
    last_nonnull.timing_observation_id AS LastObservedRecordedAtTimeObservationId,
    COALESCE(counts.nonnull_count, 0) AS RecordedAtTimeNonNullObservationCount,
    first_nonnull.recorded_at_time AS FirstObservedRecordedAtTime,
    first_nonnull.observed_at_utc AS FirstObservedRecordedAtTimeObservedAt,
    first_nonnull.snapshot_id AS FirstObservedRecordedAtTimeSnapshotId,
    first_nonnull.audit_id AS FirstObservedRecordedAtTimeAuditId,
    first_nonnull.recorded_at_time_source_path AS FirstObservedRecordedAtTimeSourcePath,
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
WHERE latest.latest_rank = 1
"""


class LiveResearchSafetyError(ValueError):
    """The requested live-research storage path is unsafe or unavailable."""


def _profile(value: PersistenceProfile | str) -> PersistenceProfile:
    try:
        return value if isinstance(value, PersistenceProfile) else PersistenceProfile(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown SQLite persistence profile: {value}") from exc


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_live_research_path(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    """Reject repository, test, and fixture locations for live evidence."""

    resolved = Path(path).expanduser().resolve()
    repo = Path(repository_root).expanduser().resolve() if repository_root else None
    if repo is not None and _path_is_within(resolved, repo):
        raise LiveResearchSafetyError(
            "LIVE_RESEARCH database must be outside the Git repository"
        )
    components = {part.casefold() for part in resolved.parts}
    suspicious = {"test", "tests", "fixture", "fixtures"}
    if components & suspicious or any(
        marker in resolved.name.casefold() for marker in ("test", "fixture")
    ):
        raise LiveResearchSafetyError(
            "LIVE_RESEARCH database path resembles a test or fixture path"
        )
    if resolved.name.casefold() not in {"collector.sqlite3", "collector.db"}:
        # Explicit paths are allowed, but make accidental source-tree database
        # names harder to use by requiring a recognizable SQLite suffix.
        if resolved.suffix.casefold() not in {".sqlite3", ".sqlite", ".db"}:
            raise LiveResearchSafetyError(
                "LIVE_RESEARCH database path must have a SQLite database suffix"
            )
    return resolved


@dataclass(frozen=True)
class ClaimResult:
    won: bool
    claim_key: tuple[str, str, int]
    existing_status: str | None
    claim_token: str | None
    event_id: int


@dataclass(frozen=True)
class DispatcherClaimResult:
    won: bool
    claim_key: tuple[str, int]
    existing_status: str | None
    claim_token: str | None
    event_id: int


class SQLitePersistence:
    """One SQLite implementation with an explicit disposable/live profile."""

    is_production_ready = False
    is_disposable_development_backend = True
    supports_atomic_quota_finalization = True
    claim_identity_kind = "development dispatcher identity: (panel_version, logical_due_bucket); target identity adds source_parent_station_id"

    def __init__(
        self,
        path: str | Path,
        *,
        profile: PersistenceProfile | str = PersistenceProfile.DEVELOPMENT,
        repository_root: str | Path | None = None,
        backup_dir: str | Path | None = None,
        raw_evidence_dir: str | Path | None = None,
        collection_context: str | None = None,
    ) -> None:
        self.profile = _profile(profile)
        self.is_live_research = self.profile is PersistenceProfile.LIVE_RESEARCH
        self.is_disposable_development_backend = not self.is_live_research
        self.repository_root = (
            Path(repository_root).expanduser().resolve() if repository_root is not None else None
        )
        if self.is_live_research and self.repository_root is None:
            working_directory = Path.cwd().resolve()
            if (working_directory / ".git").exists():
                self.repository_root = working_directory
        self.path = Path(path).expanduser().resolve()
        if self.is_live_research:
            self.path = validate_live_research_path(
                self.path,
                repository_root=self.repository_root,
            )
        self.collection_context = str(
            collection_context
            or (LIVE_RESEARCH_COLLECTION_CONTEXT if self.is_live_research else DEFAULT_COLLECTION_CONTEXT)
        )
        self.backup_dir = (
            Path(backup_dir).expanduser().resolve()
            if backup_dir is not None
            else self.path.parent / "backups"
        )
        self.raw_evidence_dir = (
            Path(raw_evidence_dir).expanduser().resolve()
            if raw_evidence_dir is not None
            else self.path.parent / "raw-evidence"
        )
        if self.is_live_research:
            for directory in (self.path.parent, self.backup_dir, self.raw_evidence_dir):
                if self.repository_root is not None and _path_is_within(directory, self.repository_root):
                    raise LiveResearchSafetyError(
                        "LIVE_RESEARCH data directories must be outside the Git repository"
                    )
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._owner_only_directory(directory)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15.0)
        self._connection.row_factory = sqlite3.Row
        self._migration_report: Mapping[str, Any] | None = None
        try:
            with self._lock:
                self._connection.execute("PRAGMA foreign_keys = ON")
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA busy_timeout = 15000")
                self._create_schema()
                if self.is_live_research:
                    for database_file in (
                        self.path,
                        self.path.with_name(self.path.name + "-wal"),
                        self.path.with_name(self.path.name + "-shm"),
                    ):
                        if database_file.exists():
                            self._owner_only_file(database_file)
        except Exception:
            # A migration can fail after additive schema work has committed.
            # Close this connection before the caller attempts safety-backup
            # restoration; the durable in-progress marker remains available for
            # a later explicit retry when no safety restore is performed.
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            raise

    @staticmethod
    def _owner_only_directory(path: Path) -> None:
        try:
            os.chmod(path, 0o700)
        except (NotImplementedError, OSError):
            # Windows and some mounted filesystems do not expose POSIX modes.
            pass

    @staticmethod
    def _owner_only_file(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except (NotImplementedError, OSError):
            pass

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS manifest_targets (
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
            CREATE TABLE IF NOT EXISTS collector_runs (
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
            CREATE TABLE IF NOT EXISTS dispatcher_claims (
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
            CREATE TABLE IF NOT EXISTS dispatcher_claim_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_version TEXT NOT NULL,
                logical_due_bucket INTEGER NOT NULL,
                collector_run_id TEXT NOT NULL,
                event_at_utc TEXT NOT NULL,
                outcome TEXT NOT NULL,
                existing_status TEXT
            );
            CREATE TABLE IF NOT EXISTS target_claims (
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
            CREATE TABLE IF NOT EXISTS claim_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_version TEXT NOT NULL,
                source_parent_station_id TEXT NOT NULL,
                logical_due_bucket INTEGER NOT NULL,
                collector_run_id TEXT NOT NULL,
                event_at_utc TEXT NOT NULL,
                outcome TEXT NOT NULL,
                existing_status TEXT
            );
            CREATE TABLE IF NOT EXISTS target_audits (
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
                retry_outcome TEXT,
                outcome_json TEXT
            );
            CREATE TABLE IF NOT EXISTS http_attempts (
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
            CREATE TABLE IF NOT EXISTS parsed_snapshots (
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
            CREATE TABLE IF NOT EXISTS quota_audit (
                quota_audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_run_id TEXT NOT NULL,
                source_parent_station_id TEXT,
                logical_due_bucket INTEGER,
                decision_kind TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                recorded_at_utc TEXT NOT NULL,
                collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'
            );
            CREATE TABLE IF NOT EXISTS quota_permits (
                permit_id TEXT PRIMARY KEY,
                month_key TEXT NOT NULL,
                decision_kind TEXT NOT NULL,
                reservation_scope TEXT NOT NULL,
                amount INTEGER NOT NULL,
                boundary INTEGER NOT NULL,
                reserved_at_utc TEXT NOT NULL,
                authorization_key TEXT,
                released_at_utc TEXT,
                finalized_at_utc TEXT,
                finalization_reason TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recorded_at_time_evidence (
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
            CREATE TABLE IF NOT EXISTS timing_observations (
                timing_observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                audit_id INTEGER NOT NULL,
                collector_run_id TEXT,
                panel_version TEXT NOT NULL,
                source_parent_station_id TEXT NOT NULL,
                logical_due_bucket INTEGER NOT NULL,
                collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
                event_ordinal INTEGER,
                result_id TEXT,
                source_event_identity TEXT,
                source_event_identity_version TEXT,
                source_event_identity_status TEXT NOT NULL,
                source_event_identity_basis_json TEXT,
                source_event_identity_missing_fields_json TEXT,
                source_event_identity_ambiguity_json TEXT,
                call_scope TEXT NOT NULL,
                call_ordinal INTEGER NOT NULL,
                timing_context TEXT NOT NULL,
                operating_day_ref TEXT,
                journey_ref TEXT,
                vehicle_journey_ref TEXT,
                dated_vehicle_journey_ref TEXT,
                line_ref TEXT,
                direction_ref TEXT,
                operator_ref TEXT,
                stop_point_ref TEXT,
                stop_seq_number INTEGER,
                timetabled_time TEXT,
                estimated_time TEXT,
                estimated_time_low TEXT,
                estimated_time_high TEXT,
                recorded_at_time TEXT,
                observed_at_utc TEXT NOT NULL,
                captured_at_utc TEXT NOT NULL,
                source_path TEXT,
                timetabled_time_source_path TEXT,
                estimated_time_source_path TEXT,
                estimated_time_low_source_path TEXT,
                estimated_time_high_source_path TEXT,
                recorded_at_time_source_path TEXT,
                stop_point_ref_source_path TEXT,
                stop_seq_number_source_path TEXT,
                source_paths_json TEXT,
                FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id),
                FOREIGN KEY (snapshot_id) REFERENCES parsed_snapshots(snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS service_section_observations (
                service_section_observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                audit_id INTEGER NOT NULL,
                collector_run_id TEXT,
                panel_version TEXT NOT NULL,
                source_parent_station_id TEXT NOT NULL,
                logical_due_bucket INTEGER NOT NULL,
                collection_context TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT',
                event_ordinal INTEGER,
                result_ordinal INTEGER,
                result_id TEXT,
                result_source_path TEXT,
                event_source_path TEXT,
                service_source_path TEXT,
                section_ordinal INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                line_ref TEXT,
                line_ref_source_path TEXT,
                line_ref_source_representation_json TEXT,
                direction_ref TEXT,
                direction_ref_source_path TEXT,
                direction_ref_source_representation_json TEXT,
                operator_ref TEXT,
                operator_ref_source_path TEXT,
                operator_ref_source_representation_json TEXT,
                mode_source_path TEXT,
                mode_source_representation_json TEXT,
                mode_source_paths_json TEXT,
                mode_source_representations_json TEXT,
                pt_mode TEXT,
                pt_mode_source_path TEXT,
                rail_submode TEXT,
                rail_submode_source_path TEXT,
                bus_submode TEXT,
                bus_submode_source_path TEXT,
                tram_submode TEXT,
                tram_submode_source_path TEXT,
                name TEXT,
                name_source_path TEXT,
                name_source_representation_json TEXT,
                short_name TEXT,
                short_name_source_path TEXT,
                short_name_source_representation_json TEXT,
                description TEXT,
                description_source_path TEXT,
                description_source_representation_json TEXT,
                route_description TEXT,
                route_description_source_path TEXT,
                route_description_source_representation_json TEXT,
                via TEXT,
                via_source_path TEXT,
                via_source_representation_json TEXT,
                published_line_name_json TEXT,
                published_line_name_source_path TEXT,
                published_line_name_source_representation_json TEXT,
                published_line_name_text_json TEXT,
                published_line_name_text_source_paths_json TEXT,
                source_paths_json TEXT,
                source_field_names_json TEXT,
                source_representation_json TEXT,
                FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id),
                FOREIGN KEY (snapshot_id) REFERENCES parsed_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_timing_observations_identity_observed
                ON timing_observations (source_event_identity, observed_at_utc, timing_observation_id);
            CREATE INDEX IF NOT EXISTS idx_timing_observations_snapshot
                ON timing_observations (snapshot_id, timing_observation_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_timing_observations_snapshot_grain
                ON timing_observations (snapshot_id, event_ordinal, call_scope, call_ordinal, timing_context);
            CREATE INDEX IF NOT EXISTS idx_service_section_observations_snapshot
                ON service_section_observations (snapshot_id, service_section_observation_id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_service_section_observations_snapshot_grain
                ON service_section_observations (snapshot_id, event_ordinal, section_ordinal, source_path);
            CREATE TABLE IF NOT EXISTS shared_usage (
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
            CREATE TABLE IF NOT EXISTS sampling_eras (
                era_code TEXT NOT NULL,
                panel_version TEXT NOT NULL,
                effective_at_utc TEXT,
                timestamp_kind TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                is_synthetic INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (era_code, panel_version)
            );
            CREATE TABLE IF NOT EXISTS database_metadata (
                metadata_key TEXT PRIMARY KEY,
                metadata_value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raw_evidence (
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
            """
        )
        # Keep disposable local files created by the earlier Stage 14 version
        # readable while giving new state the corrected columns.
        self._ensure_column("manifest_targets", "legacy_sampling_target_id", "INTEGER")
        self._ensure_column("http_attempts", "transport_kind", "TEXT NOT NULL DEFAULT 'UNKNOWN'")
        self._ensure_column("http_attempts", "network_attempted", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("http_attempts", "http_response_received", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("http_attempts", "live_client_invocation", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("http_attempts", "conservative_quota_accounted", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("http_attempts", "provider_quota_status", "TEXT NOT NULL DEFAULT 'NOT_CONSUMED'")
        self._ensure_column("http_attempts", "provider_attempt_confirmed", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("quota_permits", "finalized_at_utc", "TEXT")
        self._ensure_column("quota_permits", "finalization_reason", "TEXT")
        self._ensure_column("quota_permits", "authorization_key", "TEXT")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_quota_permits_authorization_key "
            "ON quota_permits(authorization_key) WHERE authorization_key IS NOT NULL"
        )
        self._ensure_column("shared_usage", "confirmed_provider_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "conservative_live_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "live_client_invocations", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "http_responses_received", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "provider_quota_unknown_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("collector_runs", "collection_context", "TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'")
        self._ensure_column("target_audits", "collection_context", "TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'")
        self._ensure_column("target_audits", "outcome_json", "TEXT")
        self._ensure_column("parsed_snapshots", "collection_context", "TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'")
        self._ensure_column("quota_audit", "collection_context", "TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'")
        self._ensure_column("recorded_at_time_evidence", "collection_context", "TEXT NOT NULL DEFAULT 'OFFLINE_DEVELOPMENT'")
        self._ensure_column("timing_observations", "source_event_identity_version", "TEXT")
        self._ensure_column("timing_observations", "source_event_identity_ambiguity_json", "TEXT")
        self._connection.execute(
            "UPDATE quota_permits SET status = ? WHERE status = 'PENDING'",
            (PENDING_AUTHORIZATION,),
        )

        # Schema objects are additive, but reporting views are definitions and
        # therefore need an explicit replacement path.  Read the old metadata
        # before updating it.  Historical projection backfill is completed
        # before the durable schema version is advanced; an explicit marker
        # makes an interrupted open resumable instead of falsely complete.
        self._connection.commit()
        existing = {
            str(row[0]): str(row[1])
            for row in self._connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata"
            ).fetchall()
        }
        previous_schema_version = existing.get("schema_version")
        if previous_schema_version is not None:
            try:
                if int(previous_schema_version) > int(SCHEMA_VERSION):
                    raise LiveResearchSafetyError(
                        f"SQLite schema version {previous_schema_version} is newer than supported {SCHEMA_VERSION}"
                    )
            except ValueError as exc:
                raise LiveResearchSafetyError(
                    f"SQLite schema version is not numeric: {previous_schema_version}"
                ) from exc

        migration_status = existing.get(SCHEMA_MIGRATION_STATUS_KEY)
        if migration_status not in {
            None,
            SCHEMA_MIGRATION_COMPLETE,
            SCHEMA_MIGRATION_IN_PROGRESS,
        }:
            raise LiveResearchSafetyError(
                f"SQLite schema migration status is not recognized: {migration_status}"
            )
        migration_required = migration_status == SCHEMA_MIGRATION_IN_PROGRESS
        if previous_schema_version is not None:
            migration_required = migration_required or int(previous_schema_version) < int(SCHEMA_VERSION)
        reporting_views_need_replacement = not self._reporting_views_are_current()
        migration_source_schema_version = (
            existing.get(SCHEMA_MIGRATION_SOURCE_VERSION_KEY)
            if migration_status == SCHEMA_MIGRATION_IN_PROGRESS
            else previous_schema_version
        ) or "UNKNOWN"

        if migration_required:
            self._mark_schema_migration_in_progress(
                source_schema_version=migration_source_schema_version,
            )
            self._connection.commit()
            if reporting_views_need_replacement:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._recreate_reporting_views()
                    self._connection.commit()
                except Exception:
                    self._connection.rollback()
                    raise
            migration_report = self.backfill_existing_snapshots()
            reconciliation = migration_report.get("recorded_at_time_reconciliation")
            if not isinstance(reconciliation, Mapping) or reconciliation.get("status") != "PASS":
                raise LiveResearchSafetyError(
                    "historical projection migration did not reconcile RecordedAtTime evidence"
                )
            self._initialize_metadata(migration_completed=True)
            self._connection.commit()
            self._migration_report = dict(migration_report)
            self._migration_report.update(
                {
                    "source_schema_version": (
                        migration_source_schema_version
                    ),
                    "schema_version": SCHEMA_VERSION,
                    "completion_status": SCHEMA_MIGRATION_COMPLETE,
                }
            )
        elif reporting_views_need_replacement:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._recreate_reporting_views()
                self._initialize_metadata()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        else:
            self._initialize_metadata()
            self._connection.commit()
        if self._migration_report is None:
            self._migration_report = {
                "status": "NOT_REQUIRED",
                "source_schema_version": previous_schema_version,
                "schema_version": SCHEMA_VERSION,
                "completion_status": SCHEMA_MIGRATION_COMPLETE,
                "recorded_at_time_reconciliation": {"status": "PASS"},
            }

    def _mark_schema_migration_in_progress(self, *, source_schema_version: str) -> None:
        existing = {
            str(row[0]): str(row[1])
            for row in self._connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata"
            ).fetchall()
        }
        values = {
            SCHEMA_MIGRATION_STATUS_KEY: SCHEMA_MIGRATION_IN_PROGRESS,
            SCHEMA_MIGRATION_SOURCE_VERSION_KEY: str(source_schema_version),
            SCHEMA_MIGRATION_STARTED_AT_KEY: existing.get(
                SCHEMA_MIGRATION_STARTED_AT_KEY,
                _iso(datetime.now(timezone.utc)),
            ),
        }
        for key, value in values.items():
            self._connection.execute(
                "INSERT OR REPLACE INTO database_metadata (metadata_key, metadata_value) VALUES (?, ?)",
                (key, str(value)),
            )

    def _reporting_views_are_current(self) -> bool:
        rows = self._connection.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'view'
              AND name IN ('timing_recorded_at_time_reporting', 'recorded_at_time_reporting')
            """
        ).fetchall()
        definitions = {str(row[0]): str(row[1] or "") for row in rows}
        timing_sql = definitions.get("timing_recorded_at_time_reporting", "")
        alias_sql = definitions.get("recorded_at_time_reporting", "")
        return (
            bool(timing_sql)
            and bool(alias_sql)
            and "timing_recorded_at_time_reporting" in alias_sql
            and "recorded_at_time_source_path" in timing_sql
            and "WHEN latest.recorded_at_time IS NOT NULL" in timing_sql
            and "LatestSourceRecordedAtTimeSourcePath" in timing_sql
            and "LastObservedRecordedAtTimeSourcePath" in timing_sql
            and "FirstObservedRecordedAtTimeSourcePath" in timing_sql
        )

    def _recreate_reporting_views(self) -> None:
        """Replace both current-state reporting views in the caller's transaction."""

        self._connection.execute("DROP VIEW IF EXISTS recorded_at_time_reporting")
        self._connection.execute("DROP VIEW IF EXISTS timing_recorded_at_time_reporting")
        self._connection.execute(
            "CREATE VIEW timing_recorded_at_time_reporting AS "
            + _RECORDED_AT_TIME_REPORTING_SELECT
        )
        self._connection.execute(
            "CREATE VIEW recorded_at_time_reporting AS "
            "SELECT * FROM timing_recorded_at_time_reporting"
        )

    def _initialize_metadata(self, *, migration_completed: bool = False) -> None:
        now = _iso(datetime.now(timezone.utc))
        existing = {
            str(row[0]): str(row[1])
            for row in self._connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata"
            ).fetchall()
        }
        if existing:
            role = existing.get("database_role")
            expected = self.profile.value
            if role is not None and role != expected:
                raise LiveResearchSafetyError(
                    f"SQLite database role is {role}, not the requested {expected} profile"
                )
        previous_schema_version = existing.get("schema_version")
        schema_version_changed = previous_schema_version != SCHEMA_VERSION
        previous_migration_at = existing.get("last_schema_migration_at_utc")
        values = {
            "database_role": self.profile.value,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": existing.get("created_at_utc", now),
            # This timestamp describes a real schema-version transition (or
            # initial durable schema creation), not an ordinary database open.
            "last_schema_migration_at_utc": (
                now
                if migration_completed or schema_version_changed
                else (previous_migration_at or now)
            ),
            "collection_context": self.collection_context,
            SCHEMA_MIGRATION_STATUS_KEY: SCHEMA_MIGRATION_COMPLETE,
        }
        if SCHEMA_MIGRATION_SOURCE_VERSION_KEY in existing:
            values[SCHEMA_MIGRATION_SOURCE_VERSION_KEY] = existing[
                SCHEMA_MIGRATION_SOURCE_VERSION_KEY
            ]
        if SCHEMA_MIGRATION_STARTED_AT_KEY in existing:
            values[SCHEMA_MIGRATION_STARTED_AT_KEY] = existing[
                SCHEMA_MIGRATION_STARTED_AT_KEY
            ]
        for key, value in values.items():
            self._connection.execute(
                "INSERT OR REPLACE INTO database_metadata (metadata_key, metadata_value) VALUES (?, ?)",
                (key, str(value)),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def materialize_manifest(self, manifest: Any) -> None:
        with self._lock:
            for target in manifest.targets:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO manifest_targets
                    (panel_version, source_parent_station_id, station_name, tier,
                     cadence_minutes, number_of_results, phase_index, phase_count,
                     continuity_classification, sampling_era, production_sampling_target_id,
                     legacy_sampling_target_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target.panel_version,
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        target.cadence_minutes,
                        target.number_of_results,
                        target.phase_index,
                        target.phase_count,
                        target.continuity_classification,
                        target.sampling_era.value,
                        target.production_sampling_target_id,
                        target.legacy_sampling_target_id,
                    ),
                )
            self._connection.commit()

    def record_sampling_era(
        self,
        *,
        era_code: str,
        panel_version: str,
        effective_at_utc: datetime | None = None,
        timestamp_kind: str = "NOT_RECORDED",
        is_active: bool = False,
        is_synthetic: bool = False,
    ) -> None:
        if is_active and effective_at_utc is None:
            raise ValueError("active sampling era requires an explicit timestamp")
        if self.is_live_research and str(era_code) == "Tiered50" and is_active:
            raise LiveResearchSafetyError(
                "LIVE_RESEARCH cannot create an active Tiered50 cutover row"
            )
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO sampling_eras
                (era_code, panel_version, effective_at_utc, timestamp_kind, is_active, is_synthetic)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (era_code, panel_version, _iso(effective_at_utc) if effective_at_utc else None, timestamp_kind, int(is_active), int(is_synthetic)),
            )
            self._connection.commit()

    def start_collector_run(
        self,
        *,
        run_id: str,
        panel_version: str,
        logical_due_bucket: int,
        mode: str,
        started_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        context = str(collection_context or self.collection_context)
        with self._lock:
            self._connection.execute(
                "INSERT INTO collector_runs (run_id, panel_version, logical_due_bucket, mode, collection_context, started_at_utc, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, panel_version, int(logical_due_bucket), mode, context, _iso(started_at), "RUNNING"),
            )
            self._connection.commit()

    def complete_collector_run(self, *, run_id: str, status: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE collector_runs SET completed_at_utc = ?, status = ?, summary_json = ? WHERE run_id = ?",
                (_iso(completed_at), status, json.dumps(dict(summary), sort_keys=True, default=str), run_id),
            )
            self._connection.execute(
                "UPDATE dispatcher_claims SET completed_at_utc = ?, status = ? WHERE collector_run_id = ?",
                (_iso(completed_at), status, run_id),
            )
            self._connection.commit()

    def fail_collector_run(self, *, run_id: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        self.complete_collector_run(run_id=run_id, status="FAILED", completed_at=completed_at, summary=summary)

    def record_run_skip(self, *, run_id: str, reason: str, completed_at: datetime) -> None:
        self.complete_collector_run(
            run_id=run_id,
            status="SKIPPED",
            completed_at=completed_at,
            summary={"skip_reason": str(reason)},
        )

    def claim_dispatcher_bucket(
        self,
        *,
        run_id: str,
        panel_version: str,
        logical_due_bucket: int,
        claimed_at: datetime,
    ) -> DispatcherClaimResult:
        """Atomically claim one panel/bucket before any target work is submitted."""

        key = (panel_version, int(logical_due_bucket))
        token = str(uuid.uuid4())
        stale_after = claimed_at.astimezone(timezone.utc).timestamp() + 300
        stale_at = datetime.fromtimestamp(stale_after, tz=timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO dispatcher_claims
                    (panel_version, logical_due_bucket, collector_run_id, claim_token,
                     claimed_at_utc, stale_after_utc, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*key, run_id, token, _iso(claimed_at), _iso(stale_at), "CLAIMED"),
                )
                won = cursor.rowcount == 1
                existing_status = None
                if not won:
                    existing = self._connection.execute(
                        "SELECT status FROM dispatcher_claims WHERE panel_version = ? AND logical_due_bucket = ?",
                        key,
                    ).fetchone()
                    existing_status = str(existing["status"]) if existing else "UNKNOWN"
                outcome = "CLAIMED" if won else "DUPLICATE_DISPATCHER_CLAIM"
                event = self._connection.execute(
                    """
                    INSERT INTO dispatcher_claim_events
                    (panel_version, logical_due_bucket, collector_run_id,
                     event_at_utc, outcome, existing_status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (*key, run_id, _iso(claimed_at), outcome, existing_status),
                )
                event_id = int(event.lastrowid)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return DispatcherClaimResult(won, key, existing_status, token if won else None, event_id)

    atomic_dispatcher_claim = claim_dispatcher_bucket

    def claim_target(self, *, run_id: str, target: Target, logical_due_bucket: int, claimed_at: datetime) -> ClaimResult:
        key = target.claim_key(logical_due_bucket)
        token = str(uuid.uuid4())
        stale_after = claimed_at.astimezone(timezone.utc).timestamp() + 300
        stale_at = datetime.fromtimestamp(stale_after, tz=timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO target_claims
                    (panel_version, source_parent_station_id, logical_due_bucket,
                     collector_run_id, claim_token, claimed_at_utc, stale_after_utc, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*key, run_id, token, _iso(claimed_at), _iso(stale_at), "CLAIMED"),
                )
                won = cursor.rowcount == 1
                existing_status = None
                if not won:
                    existing = self._connection.execute(
                        "SELECT status FROM target_claims WHERE panel_version = ? AND source_parent_station_id = ? AND logical_due_bucket = ?",
                        key,
                    ).fetchone()
                    existing_status = str(existing["status"]) if existing else "UNKNOWN"
                outcome = "CLAIMED" if won else "DUPLICATE_CLAIM"
                event = self._connection.execute(
                    """
                    INSERT INTO claim_events
                    (panel_version, source_parent_station_id, logical_due_bucket, collector_run_id, event_at_utc, outcome, existing_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*key, run_id, _iso(claimed_at), outcome, existing_status),
                )
                event_id = int(event.lastrowid)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return ClaimResult(won, key, existing_status, token if won else None, event_id)

    atomic_claim = claim_target

    def record_duplicate_target_claim_event(
        self,
        *,
        panel_version: str,
        target: Target,
        logical_due_bucket: int,
        run_id: str,
        event_at: datetime,
    ) -> int:
        """Audit a target-level duplicate without attempting to claim it.

        A losing dispatcher must not create target claims while the winning
        dispatcher is still running. This event is only used after a terminal
        dispatcher outcome for compatibility/readability of target audit logs.
        """

        with self._lock:
            event = self._connection.execute(
                """
                INSERT INTO claim_events
                (panel_version, source_parent_station_id, logical_due_bucket,
                 collector_run_id, event_at_utc, outcome, existing_status)
                VALUES (?, ?, ?, ?, ?, 'DUPLICATE_CLAIM', 'DISPATCHER_ALREADY_CLAIMED')
                """,
                (
                    panel_version,
                    target.source_parent_station_id,
                    int(logical_due_bucket),
                    run_id,
                    _iso(event_at),
                ),
            )
            self._connection.commit()
            return int(event.lastrowid)

    def start_target_audit(
        self,
        *,
        run_id: str,
        target: Target,
        logical_due_bucket: int,
        started_at: datetime,
        collection_context: str | None = None,
    ) -> int:
        context = str(collection_context or self.collection_context)
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO target_audits
                (collector_run_id, panel_version, source_parent_station_id, station_name, tier,
                 phase_index, number_of_results, logical_due_bucket, sampling_era,
                 continuity_classification, collection_context, started_at_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, target.panel_version, target.source_parent_station_id, target.station_name, target.tier,
                 target.phase_index, target.number_of_results, int(logical_due_bucket), target.sampling_era.value,
                 target.continuity_classification, context, _iso(started_at), "RUNNING"),
            )
            self._connection.execute(
                "UPDATE target_claims SET status = ?, started_at_utc = ? WHERE panel_version = ? AND source_parent_station_id = ? AND logical_due_bucket = ?",
                ("RUNNING", _iso(started_at), target.panel_version, target.source_parent_station_id, int(logical_due_bucket)),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def persist_skipped_target_audits(
        self,
        *,
        run_id: str,
        logical_due_bucket: int,
        skipped_audits: Sequence[Mapping[str, Any]],
        collection_context: str | None = None,
    ) -> None:
        """Persist a pre-claim skip tail without creating target claims.

        The collector uses this path after the absolute bucket budget can no
        longer accommodate another request.  It deliberately writes the
        target-audit rows directly: a target that never reached the just-in-
        time claim gate must not appear as claimed or RUNNING in
        ``target_claims``.  Existing terminal rows are left untouched so a
        retry after an ambiguous commit is idempotent.
        """

        context = str(collection_context or self.collection_context)
        with self._lock:
            try:
                for entry in skipped_audits:
                    target = entry["target"]
                    outcome = dict(entry["outcome"])
                    started_at = entry["started_at"]
                    completed_at = entry.get("completed_at", started_at)
                    target_identity = target.source_parent_station_id
                    row = self._connection.execute(
                        """
                        SELECT audit_id, status
                        FROM target_audits
                        WHERE collector_run_id = ?
                          AND source_parent_station_id = ?
                          AND logical_due_bucket = ?
                        ORDER BY audit_id
                        LIMIT 1
                        """,
                        (run_id, target_identity, int(logical_due_bucket)),
                    ).fetchone()
                    encoded = json.dumps(outcome, sort_keys=True, default=str)
                    duration_ms = float(outcome.get("duration_seconds", 0) or 0) * 1000.0
                    if row is None:
                        self._connection.execute(
                            """
                            INSERT INTO target_audits
                            (collector_run_id, panel_version, source_parent_station_id,
                             station_name, tier, phase_index, number_of_results,
                             logical_due_bucket, sampling_era, continuity_classification,
                             collection_context, started_at_utc, completed_at_utc,
                             status, http_attempts, http_status, stop_event_result_count,
                             parser_outcome, persistence_outcome, duration_ms,
                             skip_reason, error_category, retry_decision, retry_outcome,
                             outcome_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                    ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                target.panel_version,
                                target_identity,
                                target.station_name,
                                target.tier,
                                int(target.phase_index),
                                int(target.number_of_results),
                                int(logical_due_bucket),
                                target.sampling_era.value,
                                target.continuity_classification,
                                context,
                                _iso(started_at),
                                _iso(completed_at),
                                "SKIPPED",
                                int(outcome.get("http_attempts", 0) or 0),
                                outcome.get("http_status"),
                                outcome.get("stop_event_result_count"),
                                outcome.get("parser_outcome"),
                                outcome.get("persistence_outcome"),
                                duration_ms,
                                outcome.get("skip_reason"),
                                _safe_error_category(outcome.get("error_category")),
                                outcome.get("retry_decision"),
                                outcome.get("retry_outcome"),
                                encoded,
                            ),
                        )
                    elif str(row["status"] or "") in {"RUNNING", "SKIPPED"}:
                        self._connection.execute(
                            """
                            UPDATE target_audits
                            SET completed_at_utc = ?, status = ?, http_status = ?,
                                http_attempts = ?, stop_event_result_count = ?,
                                parser_outcome = ?, persistence_outcome = ?,
                                duration_ms = ?, skip_reason = ?, error_category = ?,
                                retry_decision = ?, retry_outcome = ?, outcome_json = ?
                            WHERE audit_id = ?
                            """,
                            (
                                _iso(completed_at),
                                "SKIPPED",
                                outcome.get("http_status"),
                                int(outcome.get("http_attempts", 0) or 0),
                                outcome.get("stop_event_result_count"),
                                outcome.get("parser_outcome"),
                                outcome.get("persistence_outcome"),
                                duration_ms,
                                outcome.get("skip_reason"),
                                _safe_error_category(outcome.get("error_category")),
                                outcome.get("retry_decision"),
                                outcome.get("retry_outcome"),
                                encoded,
                                int(row["audit_id"]),
                            ),
                        )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def record_http_attempt(
        self,
        *,
        audit_id: int,
        attempt_number: int,
        started_at: datetime,
        completed_at: datetime,
        response: HttpResponse | None = None,
        transport_failure: TransportFailure | None = None,
        retry_decision: str | None = None,
        retry_after_seconds: float | None = None,
        transport_kind: str = "UNKNOWN",
        quota_permit_id: Any = None,
        quota_finalization: str | None = None,
        finalized_at: datetime | None = None,
        live_client_invocation: bool | None = None,
    ) -> None:
        if response is not None:
            http_status = response.status_code
            category = None
            outcome = "HTTP_RESPONSE"
            duration = float(response.elapsed_seconds or (completed_at - started_at).total_seconds())
            network_attempted = bool(response.network_attempted)
            http_response_received = bool(response.http_response_received)
            result_transport_kind = str(response.transport_kind or transport_kind)
            result_live_client_invocation = bool(response.live_client_invocation)
        elif transport_failure is not None:
            http_status = None
            category = _safe_error_category(transport_failure.category)
            outcome = "TRANSPORT_FAILURE"
            duration = (completed_at - started_at).total_seconds()
            network_attempted = bool(transport_failure.network_attempted)
            http_response_received = bool(transport_failure.http_response_received)
            result_transport_kind = str(transport_failure.transport_kind or transport_kind)
            result_live_client_invocation = bool(transport_failure.live_client_invocation)
        else:
            http_status = None
            category = "SANITIZED_ERROR"
            outcome = "UNKNOWN_ATTEMPT"
            duration = (completed_at - started_at).total_seconds()
            network_attempted = False
            http_response_received = False
            result_transport_kind = str(transport_kind)
            result_live_client_invocation = False
        if result_transport_kind == "UNKNOWN" and transport_kind != "UNKNOWN":
            result_transport_kind = str(transport_kind)
        if live_client_invocation is not None:
            result_live_client_invocation = bool(live_client_invocation)
        provider_attempt_confirmed = network_attempted and http_response_received
        transport_execution_entered = bool(
            getattr(response, "transport_execution_entered", False)
            if response is not None
            else getattr(transport_failure, "transport_execution_entered", False)
            if transport_failure is not None
            else False
        )
        conservative_quota_accounted = (network_attempted and not http_response_received) or (
            result_live_client_invocation
            and transport_execution_entered
            and not http_response_received
        )
        provider_quota_status = (
            "CONFIRMED"
            if provider_attempt_confirmed
            else "UNKNOWN"
            if conservative_quota_accounted
            else "NOT_CONSUMED"
        )
        live_client_invocation = result_live_client_invocation
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO http_attempts
                    (audit_id, attempt_number, started_at_utc, completed_at_utc, http_status,
                     transport_failure_category, outcome, retry_decision, retry_after_seconds,
                     duration_ms, transport_kind, network_attempted, http_response_received,
                     live_client_invocation, conservative_quota_accounted,
                     provider_quota_status, provider_attempt_confirmed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        int(attempt_number),
                        _iso(started_at),
                        _iso(completed_at),
                        http_status,
                        category,
                        outcome,
                        retry_decision,
                        retry_after_seconds,
                        duration * 1000,
                        result_transport_kind,
                        int(network_attempted),
                        int(http_response_received),
                        int(live_client_invocation),
                        int(conservative_quota_accounted),
                        provider_quota_status,
                        int(provider_attempt_confirmed),
                    ),
                )
                self._connection.execute(
                    "UPDATE target_audits SET http_attempts = http_attempts + 1, http_status = COALESCE(?, http_status) WHERE audit_id = ?",
                    (http_status, audit_id),
                )
                if quota_permit_id is not None and quota_finalization is not None:
                    self._finalize_quota_permit_locked(
                        permit_id=quota_permit_id,
                        finalization=quota_finalization,
                        finalized_at=finalized_at or completed_at,
                        live_client_invocation=live_client_invocation,
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def record_http_attempt_and_finalize_quota(self, **kwargs: Any) -> None:
        """Atomically persist an attempt and transition its quota permit.

        A future production adapter must provide the same transaction
        boundary. Keeping the operation on the persistence port prevents a
        live permit from becoming reusable between transport invocation and
        durable attempt accounting.
        """

        self.record_http_attempt(**kwargs)

    def record_attempt(self, **kwargs: Any) -> None:
        """Backend-neutral alias used by the persistence port."""

        self.record_http_attempt(**kwargs)

    def persist_snapshot(
        self,
        *,
        audit_id: int,
        target: Target,
        logical_due_bucket: int,
        parsed_snapshot: Mapping[str, Any],
        persisted_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        context = str(collection_context or self.collection_context)
        snapshot = dict(parsed_snapshot)
        for key in list(snapshot):
            lowered = str(key).lower()
            if key != "raw_payload_persisted" and any(marker in lowered for marker in ("raw", "authorization", "api_key", "secret")):
                snapshot.pop(key, None)
        snapshot["raw_payload_persisted"] = False
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot_cursor = self._connection.execute(
                    """
                    INSERT INTO parsed_snapshots
                    (audit_id, panel_version, source_parent_station_id, logical_due_bucket,
                     persisted_at_utc, snapshot_json, raw_payload_persisted, collection_context)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (audit_id, target.panel_version, target.source_parent_station_id, int(logical_due_bucket), _iso(persisted_at), encoded, context),
                )
                snapshot_id = int(snapshot_cursor.lastrowid)
                timing_rows = iter_timing_observations(snapshot)
                collector_run_row = self._connection.execute(
                    "SELECT collector_run_id FROM target_audits WHERE audit_id = ?",
                    (audit_id,),
                ).fetchone()
                collector_run_id = (
                    str(collector_run_row[0])
                    if collector_run_row and collector_run_row[0] is not None
                    else None
                )
                self._persist_timing_observations(
                    audit_id=audit_id,
                    snapshot_id=snapshot_id,
                    collector_run_id=collector_run_id,
                    target=target,
                    logical_due_bucket=logical_due_bucket,
                    timing_rows=timing_rows,
                    observed_at=persisted_at,
                    collection_context=context,
                )
                self._persist_recorded_time_evidence(
                    audit_id=audit_id,
                    snapshot_id=snapshot_id,
                    target=target,
                    logical_due_bucket=logical_due_bucket,
                    parsed_snapshot=snapshot,
                    timing_rows=timing_rows,
                    observed_at=persisted_at,
                    collection_context=context,
                )
                self._persist_service_section_observations(
                    audit_id=audit_id,
                    snapshot_id=snapshot_id,
                    collector_run_id=collector_run_id,
                    target=target,
                    logical_due_bucket=logical_due_bucket,
                    parsed_snapshot=snapshot,
                    collection_context=context,
                )
                self._connection.execute(
                    "UPDATE target_audits SET persistence_outcome = ?, stop_event_result_count = ? WHERE audit_id = ?",
                    ("PERSISTED_PARSED_SNAPSHOT", len(snapshot.get("events", [])), audit_id),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def _persist_recorded_time_evidence(
        self,
        *,
        audit_id: int,
        snapshot_id: int,
        target: Target,
        logical_due_bucket: int,
        parsed_snapshot: Mapping[str, Any],
        timing_rows: list[Mapping[str, Any]] | None = None,
        observed_at: datetime,
        collection_context: str,
    ) -> None:
        rows = timing_rows if timing_rows is not None else iter_timing_observations(parsed_snapshot)
        for row in rows:
            if row.get("recorded_at_time") is None:
                continue
            source_path = row.get("recorded_at_time_source_path") or row.get("source_path")
            lineage = (
                snapshot_id,
                row.get("event_ordinal"),
                str(row.get("call_scope", "")),
                int(row.get("call_ordinal", 0)),
                str(row.get("timing_context", "")),
                str(row["recorded_at_time"]),
                source_path,
            )
            already_present = self._connection.execute(
                """
                SELECT 1
                FROM recorded_at_time_evidence
                WHERE snapshot_id = ?
                  AND event_ordinal IS ?
                  AND call_scope = ?
                  AND call_ordinal = ?
                  AND timing_context = ?
                  AND recorded_at_time = ?
                  AND source_path IS ?
                LIMIT 1
                """,
                lineage,
            ).fetchone()
            if already_present is not None:
                continue
            self._connection.execute(
                """
                INSERT INTO recorded_at_time_evidence
                (audit_id, snapshot_id, panel_version, source_parent_station_id,
                 logical_due_bucket, event_ordinal, result_id, call_scope,
                 call_ordinal, timing_context, recorded_at_time, source_path,
                 observed_at_utc, collection_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    snapshot_id,
                    target.panel_version,
                    target.source_parent_station_id,
                    int(logical_due_bucket),
                    row.get("event_ordinal"),
                    row.get("result_id"),
                    str(row.get("call_scope", "")),
                    int(row.get("call_ordinal", 0)),
                    str(row.get("timing_context", "")),
                    str(row["recorded_at_time"]),
                    source_path,
                    _iso(observed_at),
                    collection_context,
                ),
            )

    def _persist_timing_observations(
        self,
        *,
        audit_id: int,
        snapshot_id: int,
        collector_run_id: str | None,
        target: Target,
        logical_due_bucket: int,
        timing_rows: list[Mapping[str, Any]],
        observed_at: datetime,
        collection_context: str,
    ) -> int:
        observed_at_text = _iso(observed_at)
        inserted = 0
        for row in timing_rows:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO timing_observations
                (snapshot_id, audit_id, collector_run_id, panel_version,
                 source_parent_station_id, logical_due_bucket, collection_context,
                 event_ordinal, result_id, source_event_identity,
                 source_event_identity_version, source_event_identity_status,
                 source_event_identity_basis_json, source_event_identity_missing_fields_json,
                 source_event_identity_ambiguity_json, call_scope,
                 call_ordinal, timing_context, operating_day_ref, journey_ref,
                 vehicle_journey_ref, dated_vehicle_journey_ref, line_ref,
                 direction_ref, operator_ref, stop_point_ref, stop_seq_number,
                 timetabled_time, estimated_time, estimated_time_low,
                 estimated_time_high, recorded_at_time, observed_at_utc,
                 captured_at_utc, source_path, timetabled_time_source_path,
                 estimated_time_source_path, estimated_time_low_source_path,
                 estimated_time_high_source_path, recorded_at_time_source_path,
                 stop_point_ref_source_path, stop_seq_number_source_path,
                 source_paths_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?)
                """,
                (
                    snapshot_id,
                    audit_id,
                    collector_run_id,
                    target.panel_version,
                    target.source_parent_station_id,
                    int(logical_due_bucket),
                    collection_context,
                    row.get("event_ordinal"),
                    row.get("result_id"),
                    row.get("source_event_identity"),
                    row.get("source_event_identity_version"),
                    str(row.get("source_event_identity_status", "SOURCE_EVENT_IDENTITY_INSUFFICIENT")),
                    json.dumps(
                        row.get("source_event_identity_basis", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                    json.dumps(
                        row.get("source_event_identity_missing_fields", []),
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                    json.dumps(
                        row.get("source_event_identity_ambiguity", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                    str(row.get("call_scope", "")),
                    int(row.get("call_ordinal", 0)),
                    str(row.get("timing_context", "")),
                    row.get("operating_day_ref"),
                    row.get("journey_ref"),
                    row.get("vehicle_journey_ref"),
                    row.get("dated_vehicle_journey_ref"),
                    row.get("line_ref"),
                    row.get("direction_ref"),
                    row.get("operator_ref"),
                    row.get("stop_point_ref"),
                    row.get("stop_seq_number"),
                    row.get("timetabled_time"),
                    row.get("estimated_time"),
                    row.get("estimated_time_low"),
                    row.get("estimated_time_high"),
                    row.get("recorded_at_time"),
                    observed_at_text,
                    observed_at_text,
                    row.get("source_path"),
                    row.get("timetabled_time_source_path"),
                    row.get("estimated_time_source_path"),
                    row.get("estimated_time_low_source_path"),
                    row.get("estimated_time_high_source_path"),
                    row.get("recorded_at_time_source_path"),
                    row.get("stop_point_ref_source_path"),
                    row.get("stop_seq_number_source_path"),
                    json.dumps(
                        row.get("source_paths", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                ),
            )
            inserted += int(cursor.rowcount > 0)
        return inserted

    @staticmethod
    def _json_or_none(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)

    @classmethod
    def _source_text_or_none(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (Mapping, list, tuple)):
            return cls._json_or_none(value)
        return str(value)

    def _persist_service_section_observations(
        self,
        *,
        audit_id: int,
        snapshot_id: int,
        collector_run_id: str | None,
        target: Target,
        logical_due_bucket: int,
        parsed_snapshot: Mapping[str, Any],
        collection_context: str,
        service_rows: list[Mapping[str, Any]] | None = None,
    ) -> int:
        rows = (
            service_rows
            if service_rows is not None
            else iter_service_section_observations(parsed_snapshot)
        )
        inserted = 0
        for row in rows:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO service_section_observations
                (snapshot_id, audit_id, collector_run_id, panel_version,
                 source_parent_station_id, logical_due_bucket, collection_context,
                 event_ordinal, result_ordinal, result_id, result_source_path,
                 event_source_path, service_source_path, section_ordinal, source_path,
                 line_ref, line_ref_source_path, line_ref_source_representation_json,
                 direction_ref, direction_ref_source_path,
                 direction_ref_source_representation_json, operator_ref,
                 operator_ref_source_path, operator_ref_source_representation_json,
                 mode_source_path, mode_source_representation_json,
                 mode_source_paths_json, mode_source_representations_json,
                 pt_mode, pt_mode_source_path, rail_submode, rail_submode_source_path,
                 bus_submode, bus_submode_source_path, tram_submode,
                 tram_submode_source_path, name, name_source_path,
                 name_source_representation_json, short_name, short_name_source_path,
                 short_name_source_representation_json, description,
                 description_source_path, description_source_representation_json,
                 route_description, route_description_source_path,
                 route_description_source_representation_json, via, via_source_path,
                 via_source_representation_json, published_line_name_json,
                 published_line_name_source_path,
                 published_line_name_source_representation_json,
                 published_line_name_text_json,
                 published_line_name_text_source_paths_json, source_paths_json,
                 source_field_names_json, source_representation_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    audit_id,
                    collector_run_id,
                    target.panel_version,
                    target.source_parent_station_id,
                    int(logical_due_bucket),
                    collection_context,
                    row.get("event_ordinal"),
                    row.get("result_ordinal", row.get("event_ordinal")),
                    row.get("result_id"),
                    row.get("result_source_path"),
                    row.get("event_source_path"),
                    row.get("service_source_path"),
                    int(row.get("section_ordinal", 0)),
                    str(row.get("source_path", "")),
                    self._source_text_or_none(row.get("line_ref")),
                    row.get("line_ref_source_path"),
                    self._json_or_none(row.get("line_ref_source_representation")),
                    self._source_text_or_none(row.get("direction_ref")),
                    row.get("direction_ref_source_path"),
                    self._json_or_none(row.get("direction_ref_source_representation")),
                    self._source_text_or_none(row.get("operator_ref")),
                    row.get("operator_ref_source_path"),
                    self._json_or_none(row.get("operator_ref_source_representation")),
                    row.get("mode_source_path"),
                    self._json_or_none(row.get("mode_source_representation", row.get("mode"))),
                    self._json_or_none(row.get("mode_source_paths", {})),
                    self._json_or_none(row.get("mode_source_representations", {})),
                    self._source_text_or_none(row.get("pt_mode")),
                    row.get("pt_mode_source_path"),
                    self._source_text_or_none(row.get("rail_submode")),
                    row.get("rail_submode_source_path"),
                    self._source_text_or_none(row.get("bus_submode")),
                    row.get("bus_submode_source_path"),
                    self._source_text_or_none(row.get("tram_submode")),
                    row.get("tram_submode_source_path"),
                    self._source_text_or_none(row.get("name")),
                    row.get("name_source_path"),
                    self._json_or_none(row.get("name_source_representation")),
                    self._source_text_or_none(row.get("short_name")),
                    row.get("short_name_source_path"),
                    self._json_or_none(row.get("short_name_source_representation")),
                    self._source_text_or_none(row.get("description")),
                    row.get("description_source_path"),
                    self._json_or_none(row.get("description_source_representation")),
                    self._source_text_or_none(row.get("route_description")),
                    row.get("route_description_source_path"),
                    self._json_or_none(row.get("route_description_source_representation")),
                    self._source_text_or_none(row.get("via")),
                    row.get("via_source_path"),
                    self._json_or_none(row.get("via_source_representation")),
                    self._json_or_none(row.get("published_line_name")),
                    row.get("published_line_name_source_path"),
                    self._json_or_none(row.get("published_line_name_source_representation")),
                    self._json_or_none(row.get("published_line_name_text")),
                    self._json_or_none(row.get("published_line_name_text_source_paths", [])),
                    self._json_or_none(row.get("source_paths", {})),
                    self._json_or_none(row.get("source_field_names", [])),
                    self._json_or_none(row.get("source_representation", {})),
                ),
            )
            inserted += int(cursor.rowcount > 0)
        return inserted

    def persist_parsed_snapshot(self, **kwargs: Any) -> None:
        self.persist_snapshot(**kwargs)

    def _target_for_snapshot_lineage(self, row: Mapping[str, Any]) -> Target:
        row = dict(row)
        manifest_row = self._connection.execute(
            """
            SELECT * FROM manifest_targets
            WHERE panel_version = ? AND source_parent_station_id = ?
            LIMIT 1
            """,
            (row.get("panel_version"), row.get("source_parent_station_id")),
        ).fetchone()
        source = dict(manifest_row) if manifest_row is not None else {}
        try:
            sampling_era = SamplingEra(str(row.get("sampling_era") or source.get("sampling_era")))
        except ValueError:
            sampling_era = SamplingEra.TIERED50
        return Target(
            panel_version=str(row.get("panel_version") or ""),
            source_parent_station_id=str(row.get("source_parent_station_id") or ""),
            station_name=str(row.get("station_name") or ""),
            tier=str(row.get("tier") or source.get("tier") or ""),
            cadence_minutes=int(source.get("cadence_minutes") or 0),
            number_of_results=int(row.get("number_of_results") or source.get("number_of_results") or 0),
            phase_index=int(row.get("phase_index") or source.get("phase_index") or 0),
            phase_count=int(row.get("phase_count") or source.get("phase_count") or 0),
            continuity_classification=str(
                row.get("continuity_classification")
                or source.get("continuity_classification")
                or ""
            ),
            sampling_era=sampling_era,
            production_sampling_target_id=source.get("production_sampling_target_id"),
            legacy_sampling_target_id=source.get("legacy_sampling_target_id"),
        )

    def _raw_service_sections_for_snapshot(
        self,
        *,
        audit_id: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Reconstruct ServiceSection rows from still-retained sanitized raw."""

        raw_rows = self._connection.execute(
            """
            SELECT file_path, content_type
            FROM raw_evidence
            WHERE audit_id = ? AND status = 'RETAINED' AND file_path IS NOT NULL
            ORDER BY raw_evidence_id
            """,
            (audit_id,),
        ).fetchall()
        for raw_row in raw_rows:
            file_path = Path(str(raw_row[0]))
            try:
                body = file_path.read_bytes()
                parsed = parse_trias_response(body, str(raw_row[1] or ""))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                return [], f"retained raw ServiceSection reconstruction failed: {type(exc).__name__}"
            rows = iter_service_section_observations(parsed.to_dict())
            if rows:
                return rows, None
        return [], "no retained sanitized raw response was available for ServiceSection reconstruction"

    def backfill_existing_snapshots(self) -> Mapping[str, Any]:
        """Add missing normalized projections without rewriting source snapshots.

        The operation is deliberately idempotent.  Snapshot-local unique
        grains make a repeated run a no-op while the RecordedAtTime evidence
        reconciliation remains able to fill a missing evidence row.
        """

        with self._lock:
            snapshot_rows = self._connection.execute(
                """
                SELECT parsed_snapshots.*, target_audits.collector_run_id,
                       target_audits.station_name, target_audits.tier,
                       target_audits.phase_index,
                       target_audits.sampling_era,
                       target_audits.continuity_classification,
                       target_audits.number_of_results
                FROM parsed_snapshots
                JOIN target_audits ON target_audits.audit_id = parsed_snapshots.audit_id
                ORDER BY parsed_snapshots.snapshot_id
                """
            ).fetchall()
            report: dict[str, Any] = {
                "status": "COMPLETE",
                "snapshots_considered": len(snapshot_rows),
                "snapshots_with_missing_timing_rows": 0,
                "timing_rows_inserted": 0,
                "timing_rows_already_present": 0,
                "service_section_snapshots_considered": 0,
                "service_section_rows_inserted": 0,
                "service_section_rows_already_present": 0,
                "service_section_rows_reconstructed_from_raw": 0,
                "service_section_reconstruction_limitations": [],
                "invalid_snapshot_count": 0,
                "original_snapshot_json_unchanged": True,
            }
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for snapshot_row in snapshot_rows:
                    snapshot = json.loads(str(snapshot_row["snapshot_json"]))
                    if not isinstance(snapshot, Mapping):
                        raise ValueError("persisted snapshot JSON is not an object")
                    timing_rows = iter_timing_observations(snapshot)
                    existing_timing = int(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM timing_observations WHERE snapshot_id = ?",
                            (int(snapshot_row["snapshot_id"]),),
                        ).fetchone()[0]
                    )
                    target = self._target_for_snapshot_lineage(snapshot_row)
                    collection_context = str(
                        snapshot_row["collection_context"] or self.collection_context
                    )
                    if existing_timing < len(timing_rows):
                        report["snapshots_with_missing_timing_rows"] += 1
                    timing_rows_inserted = self._persist_timing_observations(
                        audit_id=int(snapshot_row["audit_id"]),
                        snapshot_id=int(snapshot_row["snapshot_id"]),
                        collector_run_id=(
                            str(snapshot_row["collector_run_id"])
                            if snapshot_row["collector_run_id"] is not None
                            else None
                        ),
                        target=target,
                        logical_due_bucket=int(snapshot_row["logical_due_bucket"]),
                        timing_rows=timing_rows,
                        observed_at=datetime.fromisoformat(
                            str(snapshot_row["persisted_at_utc"]).replace("Z", "+00:00")
                        ),
                        collection_context=collection_context,
                    )
                    report["timing_rows_inserted"] += timing_rows_inserted
                    report["timing_rows_already_present"] += max(
                        0, len(timing_rows) - timing_rows_inserted
                    )
                    persisted_at = datetime.fromisoformat(
                        str(snapshot_row["persisted_at_utc"]).replace("Z", "+00:00")
                    )
                    self._persist_recorded_time_evidence(
                        audit_id=int(snapshot_row["audit_id"]),
                        snapshot_id=int(snapshot_row["snapshot_id"]),
                        target=target,
                        logical_due_bucket=int(snapshot_row["logical_due_bucket"]),
                        parsed_snapshot=snapshot,
                        timing_rows=timing_rows,
                        observed_at=persisted_at,
                        collection_context=collection_context,
                    )

                    existing_sections = int(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM service_section_observations WHERE snapshot_id = ?",
                            (int(snapshot_row["snapshot_id"]),),
                        ).fetchone()[0]
                    )
                    report["service_section_rows_already_present"] += existing_sections
                    service_rows = iter_service_section_observations(snapshot)
                    report["service_section_snapshots_considered"] += 1
                    if not service_rows:
                        service_rows, limitation = self._raw_service_sections_for_snapshot(
                            audit_id=int(snapshot_row["audit_id"])
                        )
                        if service_rows:
                            report["service_section_rows_reconstructed_from_raw"] += len(service_rows)
                        elif limitation:
                            report["service_section_reconstruction_limitations"].append(
                                {
                                    "snapshot_id": int(snapshot_row["snapshot_id"]),
                                    "audit_id": int(snapshot_row["audit_id"]),
                                    "limitation": limitation,
                                }
                            )
                    report["service_section_rows_inserted"] += self._persist_service_section_observations(
                        audit_id=int(snapshot_row["audit_id"]),
                        snapshot_id=int(snapshot_row["snapshot_id"]),
                        collector_run_id=(
                            str(snapshot_row["collector_run_id"])
                            if snapshot_row["collector_run_id"] is not None
                            else None
                        ),
                        target=target,
                        logical_due_bucket=int(snapshot_row["logical_due_bucket"]),
                        parsed_snapshot=snapshot,
                        collection_context=collection_context,
                        service_rows=service_rows,
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                report["status"] = "FAILED"
                raise
            reconciliation = self.reconcile_recorded_at_time_evidence()
            report["recorded_at_time_reconciliation"] = reconciliation
            if not isinstance(reconciliation, Mapping) or reconciliation.get("status") != "PASS":
                report["status"] = "FAILED"
            return report

    def reconcile_recorded_at_time_evidence(self) -> Mapping[str, Any]:
        """Report exact timing/evidence lineage reconciliation without mutation."""

        with self._lock:
            timing_rows = self._connection.execute(
                """
                SELECT snapshot_id, event_ordinal, call_scope, call_ordinal,
                       timing_context, recorded_at_time,
                       COALESCE(recorded_at_time_source_path, source_path) AS evidence_source_path
                FROM timing_observations
                WHERE recorded_at_time IS NOT NULL
                ORDER BY timing_observation_id
                """
            ).fetchall()
            unmatched = 0
            for row in timing_rows:
                found = self._connection.execute(
                    """
                    SELECT 1 FROM recorded_at_time_evidence
                    WHERE snapshot_id = ? AND event_ordinal IS ?
                      AND call_scope = ? AND call_ordinal = ?
                      AND timing_context = ? AND recorded_at_time = ?
                      AND source_path IS ?
                    LIMIT 1
                    """,
                    (
                        row["snapshot_id"],
                        row["event_ordinal"],
                        row["call_scope"],
                        row["call_ordinal"],
                        row["timing_context"],
                        row["recorded_at_time"],
                        row["evidence_source_path"],
                    ),
                ).fetchone()
                unmatched += int(found is None)
            duplicate_rows = int(
                self._connection.execute(
                    """
                    SELECT COALESCE(SUM(duplicate_count - 1), 0)
                    FROM (
                        SELECT COUNT(*) AS duplicate_count
                        FROM recorded_at_time_evidence
                        GROUP BY snapshot_id, event_ordinal, call_scope,
                                 call_ordinal, timing_context, recorded_at_time,
                                 source_path
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
                or 0
            )
            evidence_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM recorded_at_time_evidence"
                ).fetchone()[0]
            )
            timing_total = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM timing_observations"
                ).fetchone()[0]
            )
        return {
            "timing_rows": timing_total,
            "timing_nonnull_recorded_at_time_rows": len(timing_rows),
            "recorded_at_time_evidence_rows": evidence_count,
            "unmatched_timing_rows": unmatched,
            "duplicate_evidence_rows": duplicate_rows,
            "status": "PASS" if unmatched == 0 and duplicate_rows == 0 else "FAIL",
        }

    def complete_target_audit(self, *, audit_id: int, outcome: Mapping[str, Any], completed_at: datetime) -> None:
        status = str(outcome.get("status", "FAILED"))
        with self._lock:
            row = self._connection.execute("SELECT panel_version, source_parent_station_id, logical_due_bucket FROM target_audits WHERE audit_id = ?", (audit_id,)).fetchone()
            if row is None:
                raise KeyError(audit_id)
            self._connection.execute(
                """
                UPDATE target_audits SET completed_at_utc = ?, status = ?, http_status = ?,
                 http_attempts = ?, stop_event_result_count = ?, parser_outcome = ?,
                 persistence_outcome = ?, duration_ms = ?, skip_reason = ?, error_category = ?,
                 retry_decision = ?, retry_outcome = ?, outcome_json = ? WHERE audit_id = ?
                """,
                (_iso(completed_at), status, outcome.get("http_status"), int(outcome.get("http_attempts", 0)), outcome.get("stop_event_result_count"), outcome.get("parser_outcome"), outcome.get("persistence_outcome"), float(outcome.get("duration_seconds", 0) or 0) * 1000, outcome.get("skip_reason"), _safe_error_category(outcome.get("error_category")), outcome.get("retry_decision"), outcome.get("retry_outcome"), json.dumps(dict(outcome), sort_keys=True, default=str), audit_id),
            )
            self._connection.execute(
                "UPDATE target_claims SET status = ?, completed_at_utc = ? WHERE panel_version = ? AND source_parent_station_id = ? AND logical_due_bucket = ?",
                (status, _iso(completed_at), row["panel_version"], row["source_parent_station_id"], row["logical_due_bucket"]),
            )
            self._connection.commit()

    def record_quota_decision(
        self,
        *,
        run_id: str,
        source_parent_station_id: str | None,
        logical_due_bucket: int | None,
        decision_kind: str,
        decision: Mapping[str, Any],
        recorded_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        context = str(collection_context or self.collection_context)
        with self._lock:
            self._connection.execute(
                "INSERT INTO quota_audit (collector_run_id, source_parent_station_id, logical_due_bucket, decision_kind, decision_json, recorded_at_utc, collection_context) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, source_parent_station_id, logical_due_bucket, decision_kind, json.dumps(dict(decision), sort_keys=True, default=str), _iso(recorded_at), context),
            )
            self._connection.commit()

    def _finalize_quota_permit_locked(
        self,
        *,
        permit_id: Any,
        finalization: str,
        finalized_at: datetime,
        live_client_invocation: bool = False,
    ) -> None:
        """Finalize one pending permit; caller owns the SQLite transaction."""

        permit = self._connection.execute(
            "SELECT permit_id, month_key, amount, status FROM quota_permits WHERE permit_id = ?",
            (str(permit_id),),
        ).fetchone()
        if permit is None:
            raise KeyError(f"unknown quota permit: {permit_id}")
        if str(permit["status"]) != PENDING_AUTHORIZATION:
            # Idempotent finalization is safe for recovery/replay paths. A
            # terminal state is never converted into another accounting type.
            return
        if finalization not in {
            RELEASED_NO_NETWORK,
            CONSERVATIVE_LIVE_ATTEMPT,
            HTTP_PROVIDER_ATTEMPT_CONFIRMED,
        }:
            raise ValueError(f"unknown quota finalization: {finalization}")

        month_key = str(permit["month_key"])
        amount = int(permit["amount"])
        timestamp = _iso(finalized_at)
        self._connection.execute(
            """
            INSERT OR IGNORE INTO shared_usage
            (month_key, confirmed_provider_attempts, reserved_shared_usage,
             projected_non_retry_attempts, conservative_live_attempts,
             live_client_invocations, http_responses_received,
             provider_quota_unknown_attempts, state_available, source)
            VALUES (?, 0, 0, 0, 0, 0, 0, 0, ?, ?)
            """,
            (
                month_key,
                0 if self.is_live_research else 1,
                UNKNOWN_SHARED_QUOTA_SOURCE if self.is_live_research else "sqlite-development",
            ),
        )
        if finalization == CONSERVATIVE_LIVE_ATTEMPT:
            self._connection.execute(
                """
                UPDATE shared_usage
                SET conservative_live_attempts = conservative_live_attempts + ?,
                    live_client_invocations = live_client_invocations + ?,
                    provider_quota_unknown_attempts = provider_quota_unknown_attempts + ?
                WHERE month_key = ?
                """,
                (amount, amount, amount, month_key),
            )
        elif finalization == HTTP_PROVIDER_ATTEMPT_CONFIRMED:
            self._connection.execute(
                """
                UPDATE shared_usage
                SET confirmed_provider_attempts = confirmed_provider_attempts + ?,
                    live_client_invocations = live_client_invocations + ?,
                    http_responses_received = http_responses_received + ?
                WHERE month_key = ?
                """,
                (amount, amount, amount, month_key),
            )
        elif finalization == RELEASED_NO_NETWORK and live_client_invocation:
            self._connection.execute(
                """
                UPDATE shared_usage
                SET live_client_invocations = live_client_invocations + ?
                WHERE month_key = ?
                """,
                (amount, month_key),
            )
        self._connection.execute(
            """
            UPDATE quota_permits
            SET status = ?, released_at_utc = ?, finalized_at_utc = ?, finalization_reason = ?
            WHERE permit_id = ? AND status = ?
            """,
            (finalization, timestamp, timestamp, finalization, str(permit_id), PENDING_AUTHORIZATION),
        )

    def reserve_quota_attempt(
        self,
        *,
        decision_kind: str,
        boundary: int,
        proposed_attempts: int = 1,
        at_utc: datetime | None = None,
        reservation_scope: str = "LIVE_PROVIDER",
        authorization_key: str | None = None,
    ) -> Mapping[str, Any]:
        """Atomically reserve quota headroom in SQLite.

        The unique permit row and the ``BEGIN IMMEDIATE`` transaction make a
        boundary decision safe across independent connections/processes. Local
        simulated transport rows are intentionally absent from this equation.
        """

        at = (at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        month = _month_key(at)
        amount = int(proposed_attempts)
        if amount <= 0:
            raise ValueError("proposed_attempts must be positive")
        scope = str(reservation_scope)
        key = str(authorization_key) if authorization_key else None
        if key is not None and len(key) > 240:
            raise ValueError("authorization_key must be at most 240 characters")
        effective_boundary = min(int(boundary), 250_000)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if key is not None:
                    existing = self._connection.execute(
                        "SELECT permit_id, month_key, decision_kind, reservation_scope, amount, status "
                        "FROM quota_permits WHERE authorization_key = ?",
                        (key,),
                    ).fetchone()
                    if existing is not None:
                        same_contract = (
                            str(existing["month_key"]) == month
                            and str(existing["decision_kind"]) == str(decision_kind)
                            and str(existing["reservation_scope"]) == scope
                            and int(existing["amount"]) == amount
                        )
                        if not same_contract:
                            decision = {
                                "allowed": False,
                                "decision": "DUPLICATE_AUTHORIZATION_CONFLICT",
                                "proposed_attempts": amount,
                                "known_total": None,
                                "boundary": effective_boundary,
                                "reason": "authorization key was already used with a different permit contract",
                                "permit_id": None,
                            }
                        elif str(existing["status"]) == PENDING_AUTHORIZATION:
                            decision = {
                                "allowed": True,
                                "decision": "DUPLICATE_AUTHORIZATION_REUSED",
                                "proposed_attempts": amount,
                                "known_total": None,
                                "boundary": effective_boundary,
                                "reason": "existing pending permit reused idempotently",
                                "permit_id": str(existing["permit_id"]),
                            }
                        else:
                            decision = {
                                "allowed": False,
                                "decision": "DUPLICATE_AUTHORIZATION_FINALIZED",
                                "proposed_attempts": amount,
                                "known_total": None,
                                "boundary": effective_boundary,
                                "reason": "authorization key already has a finalized permit",
                                "permit_id": None,
                            }
                        self._connection.commit()
                        return decision
                shared = self._connection.execute(
                    "SELECT * FROM shared_usage WHERE month_key = ?", (month,)
                ).fetchone()
                # A new LIVE_RESEARCH file says nothing about provider-wide
                # usage. Missing shared baseline is unknown, not a confirmed
                # zero. Development simulations retain their old default.
                state_available = bool(
                    shared["state_available"]
                    if shared is not None
                    else not self.is_live_research
                )
                confirmed = int(shared["confirmed_provider_attempts"] if shared else 0)
                reserved = int(shared["reserved_shared_usage"] if shared else 0)
                projected_non_retry = int(shared["projected_non_retry_attempts"] if shared else 0)
                conservative_live = int(shared["conservative_live_attempts"] if shared else 0)
                active = self._connection.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS amount
                    FROM quota_permits
                    WHERE month_key = ? AND reservation_scope = ? AND status = ?
                    """,
                    (month, scope, PENDING_AUTHORIZATION),
                ).fetchone()
                active_reserved = int(active["amount"] if active else 0)
                known_total = confirmed + reserved + projected_non_retry + conservative_live + active_reserved
                if not state_available and decision_kind == "RETRY":
                    decision = {
                        "allowed": False,
                        "decision": "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": effective_boundary,
                        "reason": "safe shared-credential state is unavailable",
                        "permit_id": None,
                    }
                elif not state_available:
                    permit_id = str(uuid.uuid4())
                    self._connection.execute(
                        """
                        INSERT INTO quota_permits
                        (permit_id, month_key, decision_kind, reservation_scope, amount,
                         boundary, reserved_at_utc, authorization_key, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (permit_id, month, decision_kind, scope, amount, effective_boundary, _iso(at), key, PENDING_AUTHORIZATION),
                    )
                    decision = {
                        "allowed": True,
                        "decision": "INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": effective_boundary,
                        "reason": "initial scheduled request is not silently converted into a retry",
                        "permit_id": permit_id,
                    }
                elif known_total + amount > effective_boundary:
                    decision = {
                        "allowed": False,
                        "decision": (
                            "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM"
                            if decision_kind == "INITIAL"
                            else "RETRY_DENIED_PROTECTION_BOUNDARY"
                        ),
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": effective_boundary,
                        "reason": "quota boundary would be exceeded",
                        "permit_id": None,
                    }
                else:
                    permit_id = str(uuid.uuid4())
                    self._connection.execute(
                        """
                        INSERT INTO quota_permits
                        (permit_id, month_key, decision_kind, reservation_scope, amount,
                         boundary, reserved_at_utc, authorization_key, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (permit_id, month, decision_kind, scope, amount, effective_boundary, _iso(at), key, PENDING_AUTHORIZATION),
                    )
                    decision = {
                        "allowed": True,
                        "decision": "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": effective_boundary,
                        "reason": "within atomic quota headroom",
                        "permit_id": permit_id,
                    }
                self._connection.commit()
                return decision
            except Exception:
                self._connection.rollback()
                raise

    authorize_attempt = reserve_quota_attempt

    def _finalize_quota_permit(
        self,
        *,
        permit_id: Any,
        finalization: str,
        at_utc: datetime | None = None,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        if permit_id is None:
            return {"applied": False, "status": finalization, "permit_id": None}
        finalized_at = (at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT status, amount FROM quota_permits WHERE permit_id = ?",
                    (str(permit_id),),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown quota permit: {permit_id}")
                self._finalize_quota_permit_locked(
                    permit_id=permit_id,
                    finalization=finalization,
                    finalized_at=finalized_at,
                    live_client_invocation=live_client_invocation,
                )
                self._connection.commit()
                return {
                    "applied": str(row["status"]) == PENDING_AUTHORIZATION,
                    "status": finalization if str(row["status"]) == PENDING_AUTHORIZATION else str(row["status"]),
                    "permit_id": str(permit_id),
                    "amount": int(row["amount"]),
                }
            except Exception:
                self._connection.rollback()
                raise

    def finalize_quota_permit_no_network(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        return self._finalize_quota_permit(
            permit_id=permit_id,
            finalization=RELEASED_NO_NETWORK,
            at_utc=at_utc,
            live_client_invocation=live_client_invocation,
        )

    def finalize_quota_permit_conservative(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize_quota_permit(
            permit_id=permit_id,
            finalization=CONSERVATIVE_LIVE_ATTEMPT,
            at_utc=at_utc,
            live_client_invocation=live_client_invocation,
        )

    def finalize_quota_permit_http_response(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize_quota_permit(
            permit_id=permit_id,
            finalization=HTTP_PROVIDER_ATTEMPT_CONFIRMED,
            at_utc=at_utc,
            live_client_invocation=live_client_invocation,
        )

    def release_quota_attempt(self, *, permit_id: Any) -> None:
        self.finalize_quota_permit_no_network(permit_id=permit_id)

    def get_target(self, *, panel_version: str, source_parent_station_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM manifest_targets WHERE panel_version = ? AND source_parent_station_id = ?",
                (panel_version, source_parent_station_id),
            ).fetchone()
        return dict(row) if row else None

    def record_sampling_era_cutover(
        self,
        *,
        era_code: str,
        panel_version: str,
        effective_at_utc: datetime,
        is_synthetic: bool = True,
    ) -> None:
        """Local simulation hook; a real adapter owns the production timestamp."""

        self.record_sampling_era(
            era_code=era_code,
            panel_version=panel_version,
            effective_at_utc=effective_at_utc,
            timestamp_kind="SYNTHETIC_TEST" if is_synthetic else "PRODUCTION_ADAPTER",
            is_active=True,
            is_synthetic=is_synthetic,
        )

    def skip_target(self, *, audit_id: int, reason: str, completed_at: datetime) -> None:
        self.complete_target_audit(
            audit_id=audit_id,
            outcome={"status": "SKIPPED", "skip_reason": reason, "persistence_outcome": "NOT_ATTEMPTED"},
            completed_at=completed_at,
        )

    def list_stale_claims(self, *, now_utc: datetime) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM target_claims WHERE status IN ('CLAIMED', 'RUNNING') AND stale_after_utc < ? ORDER BY claimed_at_utc",
                (_iso(now_utc),),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_shared_usage(
        self,
        *,
        month_key: str,
        confirmed_provider_attempts: int = 0,
        actual_attempts: int | None = None,
        reserved_shared_usage: int = 0,
        projected_non_retry_attempts: int = 0,
        conservative_live_attempts: int = 0,
        live_client_invocations: int = 0,
        http_responses_received: int = 0,
        provider_quota_unknown_attempts: int = 0,
        state_available: bool = True,
        source: str = "sqlite-development",
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO shared_usage
                (month_key, confirmed_provider_attempts, reserved_shared_usage,
                 projected_non_retry_attempts, conservative_live_attempts,
                 live_client_invocations, http_responses_received,
                 provider_quota_unknown_attempts, state_available, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    month_key,
                    int(actual_attempts if actual_attempts is not None else confirmed_provider_attempts),
                    int(reserved_shared_usage),
                    int(projected_non_retry_attempts),
                    int(conservative_live_attempts),
                    int(live_client_invocations),
                    int(http_responses_received),
                    int(provider_quota_unknown_attempts),
                    int(state_available),
                    source,
                ),
            )
            self._connection.commit()

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        at = at_utc or datetime.now(timezone.utc)
        month = _month_key(at)
        with self._lock:
            shared = self._connection.execute("SELECT * FROM shared_usage WHERE month_key = ?", (month,)).fetchone()
            active = self._connection.execute(
                "SELECT COALESCE(SUM(amount), 0) AS amount FROM quota_permits WHERE month_key = ? AND reservation_scope LIKE 'LIVE%' AND status = ?",
                (month, PENDING_AUTHORIZATION),
            ).fetchone()
        state_available = bool(
            shared["state_available"] if shared is not None else not self.is_live_research
        )
        known_confirmed = (
            int(shared["confirmed_provider_attempts"])
            if shared is not None and state_available
            else None
        )
        source = str(
            shared["source"]
            if shared is not None
            else UNKNOWN_SHARED_QUOTA_SOURCE
            if self.is_live_research
            else "sqlite-development"
        )
        return {
            "confirmed_provider_attempts": known_confirmed,
            "confirmed_provider_http_attempts": known_confirmed,
            "actual_attempts": known_confirmed,
            "reserved_shared_usage": int(shared["reserved_shared_usage"] if shared else 0) + int(active["amount"] if active else 0),
            "projected_non_retry_attempts": int(shared["projected_non_retry_attempts"] if shared else 0),
            "conservative_live_attempts": int(shared["conservative_live_attempts"] if shared else 0),
            "conservative_quota_attempts": int(shared["conservative_live_attempts"] if shared else 0),
            "live_client_invocations": int(shared["live_client_invocations"] if shared else 0),
            "http_responses_received": int(shared["http_responses_received"] if shared else 0),
            "provider_quota_unknown_attempts": int(shared["provider_quota_unknown_attempts"] if shared else 0),
            "provider_quota_consumption_unknown": int(shared["provider_quota_unknown_attempts"] if shared else 0),
            "known_total": None if not state_available else (
                int(shared["confirmed_provider_attempts"] if shared else 0)
                + int(shared["reserved_shared_usage"] if shared else 0)
                + int(shared["projected_non_retry_attempts"] if shared else 0)
                + int(shared["conservative_live_attempts"] if shared else 0)
                + int(active["amount"] if active else 0)
            ),
            "state_available": state_available,
            "source": source,
        }

    get_current_month_usage = current_month_usage

    @property
    def database_role(self) -> str:
        return self.profile.value

    def get_metadata(self) -> dict[str, str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata ORDER BY metadata_key"
            ).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    database_metadata = get_metadata

    def migration_report(self) -> Mapping[str, Any]:
        """Return the report from the constructor's historical migration.

        The report is intentionally read-only and exists so an offline
        orchestrator can reconcile projection count changes with the exact
        idempotent backfill that completed before schema metadata was finalized.
        """

        return dict(self._migration_report or {})

    def integrity_check(self, *, quick: bool = True) -> str:
        pragma = "quick_check" if quick else "integrity_check"
        with self._lock:
            row = self._connection.execute(f"PRAGMA {pragma}").fetchone()
        result = str(row[0]) if row else ""
        if result.casefold() != "ok":
            raise LiveResearchSafetyError(f"SQLite {pragma} failed: {result or 'no result'}")
        return result

    def _verify_directory_writable(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._owner_only_directory(directory)
        probe: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".storage-preflight-", dir=str(directory))
            os.close(descriptor)
            probe = Path(name)
            self._owner_only_file(probe)
        except OSError as exc:
            raise LiveResearchSafetyError(f"storage directory is not writable: {directory}") from exc
        finally:
            if probe is not None:
                try:
                    probe.unlink()
                except FileNotFoundError:
                    pass

    def preflight_storage(self) -> Mapping[str, Any]:
        """Validate durable live-research storage before transport."""

        if not self.is_live_research:
            return {
                "ready": True,
                "profile": self.profile.value,
                "database_role": self.database_role,
            }
        validate_live_research_path(self.path, repository_root=self.repository_root)
        if not self.path.exists() or not self.path.is_file():
            raise LiveResearchSafetyError(f"SQLite database is not available: {self.path}")
        self._verify_directory_writable(self.path.parent)
        self._verify_directory_writable(self.backup_dir)
        self._verify_directory_writable(self.raw_evidence_dir)
        metadata = self.get_metadata()
        if metadata.get("database_role") != PersistenceProfile.LIVE_RESEARCH.value:
            raise LiveResearchSafetyError("SQLite metadata does not identify LIVE_RESEARCH")
        if metadata.get("schema_version") != SCHEMA_VERSION:
            raise LiveResearchSafetyError("SQLite schema metadata is not supported")
        self.integrity_check(quick=True)
        cleanup = self.cleanup_expired_raw_evidence()
        return {
            "ready": True,
            "profile": self.profile.value,
            "database_role": metadata.get("database_role"),
            "schema_version": metadata.get("schema_version"),
            "raw_cleanup": cleanup,
            "backup_directory": str(self.backup_dir),
            "raw_evidence_directory": str(self.raw_evidence_dir),
        }

    storage_preflight = preflight_storage

    def verify_live_research_heartbeat(
        self,
        *,
        run_id: str,
        panel_version: str,
        logical_due_bucket: int,
        target_id: str,
    ) -> Mapping[str, Any]:
        """Reconcile one pilot heartbeat without changing persisted evidence."""

        if not self.is_live_research:
            return {"status": "DISABLED_OFFLINE"}
        with self._lock:
            run = self._connection.execute(
                "SELECT * FROM collector_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            audits = self._connection.execute(
                """
                SELECT * FROM target_audits
                WHERE collector_run_id = ? AND panel_version = ? AND logical_due_bucket = ?
                ORDER BY audit_id
                """,
                (run_id, panel_version, int(logical_due_bucket)),
            ).fetchall()
            attempts = self._connection.execute(
                """
                SELECT http_attempts.*
                FROM http_attempts
                JOIN target_audits ON target_audits.audit_id = http_attempts.audit_id
                WHERE target_audits.collector_run_id = ?
                  AND target_audits.panel_version = ?
                  AND target_audits.logical_due_bucket = ?
                ORDER BY http_attempts.attempt_id
                """,
                (run_id, panel_version, int(logical_due_bucket)),
            ).fetchall()
            snapshots = self._connection.execute(
                """
                SELECT parsed_snapshots.*
                FROM parsed_snapshots
                JOIN target_audits ON target_audits.audit_id = parsed_snapshots.audit_id
                WHERE target_audits.collector_run_id = ?
                  AND target_audits.panel_version = ?
                  AND target_audits.logical_due_bucket = ?
                ORDER BY parsed_snapshots.snapshot_id
                """,
                (run_id, panel_version, int(logical_due_bucket)),
            ).fetchall()
            timing = self._connection.execute(
                """
                SELECT timing_observations.*
                FROM timing_observations
                JOIN target_audits ON target_audits.audit_id = timing_observations.audit_id
                WHERE target_audits.collector_run_id = ?
                  AND target_audits.panel_version = ?
                  AND target_audits.logical_due_bucket = ?
                ORDER BY timing_observations.timing_observation_id
                """,
                (run_id, panel_version, int(logical_due_bucket)),
            ).fetchall()
            recorded = self._connection.execute(
                """
                SELECT recorded_at_time_evidence.*
                FROM recorded_at_time_evidence
                JOIN target_audits ON target_audits.audit_id = recorded_at_time_evidence.audit_id
                WHERE target_audits.collector_run_id = ?
                  AND target_audits.panel_version = ?
                  AND target_audits.logical_due_bucket = ?
                ORDER BY recorded_at_time_evidence.evidence_id
                """,
                (run_id, panel_version, int(logical_due_bucket)),
            ).fetchall()
            active_tiered50 = self._connection.execute(
                "SELECT COUNT(*) AS count FROM sampling_eras WHERE era_code = 'Tiered50' AND is_active = 1"
            ).fetchone()

        audit_rows = [dict(row) for row in audits]
        attempt_rows = [dict(row) for row in attempts]
        snapshot_rows = [dict(row) for row in snapshots]
        timing_rows = [dict(row) for row in timing]
        recorded_rows = [dict(row) for row in recorded]
        run_row = dict(run) if run is not None else None
        exact_audit = len(audit_rows) == 1 and audit_rows[0].get("source_parent_station_id") == target_id
        audit = audit_rows[0] if exact_audit else None
        parsed_snapshot = None
        if snapshot_rows:
            try:
                parsed_snapshot = json.loads(str(snapshot_rows[0]["snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed_snapshot = None
        parse_success = bool(audit and audit.get("parser_outcome") == "PARSED")
        expected_recorded_count = len(
            parsed_snapshot.get("recorded_at_time_occurrences", ())
            if isinstance(parsed_snapshot, Mapping)
            else ()
        )
        expected_timing_count = len(
            iter_timing_observations(parsed_snapshot)
            if isinstance(parsed_snapshot, Mapping)
            else ()
        )
        evidence_fields_present = bool(
            isinstance(parsed_snapshot, Mapping)
            and "parser_gaps" in parsed_snapshot
            and "recorded_at_time_occurrences" in parsed_snapshot
            and all(
                isinstance(event, Mapping)
                and "service_attribute_evidence" in event
                and "situation_evidence" in event
                and "parser_gaps" in parsed_snapshot
                for event in parsed_snapshot.get("events", ())
            )
        )
        context_values = {
            str(row.get("collection_context"))
            for row in (
                ([run_row] if run_row else [])
                + audit_rows
                + snapshot_rows
                + timing_rows
                + recorded_rows
            )
            if row.get("collection_context") is not None
        }
        checks = {
            "collector_run_row_exists": run_row is not None,
            "exactly_one_pilot_target_audit": exact_audit,
            "attempt_accounting_consistent": bool(
                audit
                and int(audit.get("http_attempts", 0)) == len(attempt_rows)
                and all(int(row.get("attempt_number", 0)) <= MAX_ATTEMPTS for row in attempt_rows)
            ),
            "parsed_snapshot_exists_if_successful_parse": (not parse_success) or bool(snapshot_rows),
            "recorded_at_time_reconciled": expected_recorded_count == len(recorded_rows),
            "timing_observations_reconciled": (not snapshot_rows) or expected_timing_count == len(timing_rows),
            "nonnull_timing_rows_have_recorded_evidence": sum(
                row.get("recorded_at_time") is not None for row in timing_rows
            ) == len(recorded_rows),
            "source_evidence_fields_present": (not snapshot_rows) or evidence_fields_present,
            "no_second_target_executed": len({str(row.get("source_parent_station_id")) for row in audit_rows}) <= 1,
            "no_active_tiered50_cutover_row": int(active_tiered50["count"] if active_tiered50 else 0) == 0,
            "collection_context_is_live_research": context_values <= {LIVE_RESEARCH_COLLECTION_CONTEXT},
        }
        status = "COMPLETE" if all(checks.values()) else "FAILED"
        usage = self.current_month_usage()
        return {
            "status": status,
            "checks": checks,
            "run_id": run_id,
            "logical_due_bucket": int(logical_due_bucket),
            "target_id": target_id,
            "collection_context": LIVE_RESEARCH_COLLECTION_CONTEXT,
            "audit_count": len(audit_rows),
            "attempt_count": len(attempt_rows),
            "snapshot_count": len(snapshot_rows),
            "recorded_at_time_occurrence_count": expected_recorded_count,
            "recorded_at_time_evidence_count": len(recorded_rows),
            "timing_observation_count": len(timing_rows),
            "timing_observation_nonnull_recorded_at_time_count": sum(
                row.get("recorded_at_time") is not None for row in timing_rows
            ),
            "parser_gap_count": len(parsed_snapshot.get("parser_gaps", ())) if isinstance(parsed_snapshot, Mapping) else 0,
            "attribute_evidence_count": sum(
                len(event.get("service_attribute_evidence", ()))
                for event in parsed_snapshot.get("events", ())
                if isinstance(event, Mapping)
            ) if isinstance(parsed_snapshot, Mapping) else 0,
            "situation_evidence_count": sum(
                len(event.get("situation_evidence", ()))
                for event in parsed_snapshot.get("events", ())
                if isinstance(event, Mapping)
            ) if isinstance(parsed_snapshot, Mapping) else 0,
            "network_attempt_count": sum(int(row.get("network_attempted", 0)) for row in attempt_rows),
            "http_response_count": sum(int(row.get("http_response_received", 0)) for row in attempt_rows),
            "provider_attempt_confirmed_count": sum(int(row.get("provider_attempt_confirmed", 0)) for row in attempt_rows),
            "quota": dict(usage) if usage is not None else None,
        }

    @staticmethod
    def _sanitize_raw_body(body: bytes) -> bytes:
        """Remove obvious credential-shaped fields before forensic storage."""

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body
        prefix = r"(?i)(\b(?:x-api-key|api[_-]?key|authorization|password|token|secret)\b\s*[=:]\s*)"
        quoted = re.compile(prefix + r"([\"'])(.*?)(\2)", re.DOTALL)
        text = quoted.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>{match.group(2)}", text)
        unquoted = re.compile(prefix + r"([^\"'\s<,}][^\r\n,}]*)")
        text = unquoted.sub(lambda match: f"{match.group(1)}<redacted>", text)
        return text.encode("utf-8")

    def _raw_index_row(
        self,
        *,
        audit_id: int | None,
        collector_run_id: str | None,
        target: Target | None,
        logical_due_bucket: int | None,
        reason: str,
        created_at: datetime,
        content_type: str | None,
        byte_length: int,
        file_path: str | None,
        status: str,
        refusal_reason: str | None = None,
    ) -> int:
        delete_after = created_at.astimezone(timezone.utc) + timedelta(hours=RAW_RETENTION_HOURS)
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO raw_evidence
                (audit_id, collector_run_id, source_parent_station_id, station_name,
                 logical_due_bucket, collection_context, reason, created_at_utc,
                 delete_after_utc, content_type, byte_length, file_path, status,
                 refusal_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    collector_run_id,
                    target.source_parent_station_id if target else None,
                    target.station_name if target else None,
                    logical_due_bucket,
                    self.collection_context,
                    str(reason),
                    _iso(created_at),
                    _iso(delete_after),
                    content_type,
                    int(byte_length),
                    file_path,
                    status,
                    refusal_reason,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def retain_raw_evidence(
        self,
        *,
        body: bytes,
        reason: str = "UNSPECIFIED",
        observed_at: datetime | None = None,
        content_type: str | None = None,
        audit_id: int | None = None,
        run_id: str | None = None,
        collector_run_id: str | None = None,
        target: Target | None = None,
        logical_due_bucket: int | None = None,
        response_status: int | None = None,
        **_: Any,
    ) -> str:
        """Store one bounded sanitized raw artifact only in live research."""

        if not self.is_live_research:
            return "DISABLED_OFFLINE"
        created_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        effective_run_id = collector_run_id or run_id
        payload = bytes(body or b"")
        secret = os.environ.get("MDD_API_KEY")
        if secret:
            try:
                secret_bytes = secret.encode("utf-8")
            except UnicodeEncodeError:
                secret_bytes = b""
            if secret_bytes and secret_bytes in payload:
                self._raw_index_row(
                    audit_id=audit_id,
                    collector_run_id=effective_run_id,
                    target=target,
                    logical_due_bucket=logical_due_bucket,
                    reason=reason,
                    created_at=created_at,
                    content_type=content_type,
                    byte_length=0,
                    file_path=None,
                    status="REFUSED_SECRET_DETECTED",
                    refusal_reason="current MDD_API_KEY value detected in response payload",
                )
                return "REFUSED_SECRET_DETECTED"
        sanitized = self._sanitize_raw_body(payload)
        if not sanitized:
            self._raw_index_row(
                audit_id=audit_id,
                collector_run_id=effective_run_id,
                target=target,
                logical_due_bucket=logical_due_bucket,
                reason=reason,
                created_at=created_at,
                content_type=content_type,
                byte_length=0,
                file_path=None,
                status="NO_BODY",
            )
            return "NO_BODY"
        self._verify_directory_writable(self.raw_evidence_dir)
        final_path = self.raw_evidence_dir / f"raw-{created_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.bin"
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".raw-", suffix=".tmp", dir=str(self.raw_evidence_dir)
            )
            temporary_path = Path(temporary_name)
            self._owner_only_file(temporary_path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(sanitized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, final_path)
            self._owner_only_file(final_path)
        except OSError:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
            self._raw_index_row(
                audit_id=audit_id,
                collector_run_id=effective_run_id,
                target=target,
                logical_due_bucket=logical_due_bucket,
                reason=reason,
                created_at=created_at,
                content_type=content_type,
                byte_length=0,
                file_path=None,
                status="REFUSED_WRITE_FAILURE",
                refusal_reason="raw evidence file could not be written",
            )
            return "REFUSED_WRITE_FAILURE"
        self._raw_index_row(
            audit_id=audit_id,
            collector_run_id=effective_run_id,
            target=target,
            logical_due_bucket=logical_due_bucket,
            reason=reason,
            created_at=created_at,
            content_type=content_type,
            byte_length=len(sanitized),
            file_path=str(final_path),
            status="RETAINED",
        )
        return "RETAINED"

    def cleanup_expired_raw_evidence(self, *, now_utc: datetime | None = None) -> Mapping[str, Any]:
        if not self.is_live_research:
            return {"status": "DISABLED_OFFLINE", "expired": 0, "deleted": 0}
        now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            rows = self._connection.execute(
                "SELECT raw_evidence_id, file_path FROM raw_evidence WHERE status = 'RETAINED' AND delete_after_utc <= ? ORDER BY raw_evidence_id",
                (_iso(now),),
            ).fetchall()
            deleted = 0
            missing = 0
            for row in rows:
                file_path = row[1]
                if file_path:
                    try:
                        Path(str(file_path)).unlink()
                        deleted += 1
                    except FileNotFoundError:
                        missing += 1
                self._connection.execute(
                    "UPDATE raw_evidence SET status = 'DELETED', deleted_at_utc = ? WHERE raw_evidence_id = ?",
                    (_iso(now), int(row[0])),
                )
            self._connection.commit()
        return {
            "status": "COMPLETE",
            "expired": len(rows),
            "deleted": deleted,
            "missing_already_deleted": missing,
        }

    cleanup_raw_evidence = cleanup_expired_raw_evidence

    def backup_live_database(self, *, now_utc: datetime | None = None) -> Mapping[str, Any]:
        """Publish a validated same-day online SQLite backup and rotate three dates."""

        if not self.is_live_research:
            return {"status": "DISABLED_OFFLINE"}
        now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self._verify_directory_writable(self.backup_dir)
        date_text = now.strftime("%Y-%m-%d")
        final_path = self.backup_dir / f"collector-{date_text}.sqlite3"
        temporary_path = self.backup_dir / f".collector-{date_text}-{uuid.uuid4().hex}.tmp"
        temporary_sidecars = tuple(
            Path(f"{temporary_path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
        )

        def remove_temporary_backup_artifacts() -> None:
            for candidate in (temporary_path, *temporary_sidecars):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass

        def remove_temporary_backup_sidecars() -> None:
            for candidate in temporary_sidecars:
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass

        destination: sqlite3.Connection | None = None
        try:
            # A process can be interrupted after closing a temporary SQLite
            # backup but before its sidecars are removed. Only remove files
            # matching this implementation's exact hidden-temp pattern.
            for stale in self.backup_dir.iterdir():
                if _BACKUP_TEMP_NAME.fullmatch(stale.name) and stale.is_file():
                    stale.unlink()
            destination = sqlite3.connect(str(temporary_path), timeout=15.0)
            destination.execute("PRAGMA journal_mode = DELETE")
            with self._lock:
                self._connection.backup(destination)
            # The source database uses WAL. Force the published copy back to
            # a self-contained rollback-journal database before closing it so
            # no hidden temporary sidecars survive the atomic rename.
            destination.execute("PRAGMA journal_mode = DELETE")
            destination.commit()
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).casefold() != "ok":
                raise sqlite3.DatabaseError("backup integrity check failed")
            destination.close()
            destination = None
            remove_temporary_backup_sidecars()
            self._owner_only_file(temporary_path)
            for sidecar in temporary_sidecars:
                if sidecar.exists():
                    raise sqlite3.DatabaseError(
                        f"temporary backup sidecar remained before publish: {sidecar.name}"
                    )
            os.replace(temporary_path, final_path)
            self._owner_only_file(final_path)
        except Exception as exc:
            if destination is not None:
                destination.close()
            remove_temporary_backup_artifacts()
            return {
                "status": "FAILED",
                "error": type(exc).__name__,
                "reason": "validated SQLite backup could not be published",
                "backup_path": str(final_path),
                "rotated": [],
            }

        available: list[tuple[str, Path]] = []
        for candidate in self.backup_dir.iterdir():
            match = _BACKUP_NAME.match(candidate.name)
            if match and candidate.is_file():
                available.append((match.group("date"), candidate))
        available.sort(key=lambda item: item[0], reverse=True)
        keep = {path for _date, path in available[:3]}
        removed: list[str] = []
        for _date, candidate in available[3:]:
            if candidate not in keep:
                try:
                    candidate.unlink()
                    removed.append(str(candidate))
                except FileNotFoundError:
                    pass
        return {
            "status": "COMPLETE",
            "backup_path": str(final_path),
            "integrity_check": "ok",
            "available_backup_dates": [date for date, _path in available if _path in keep],
            "rotated": removed,
        }

    create_daily_backup = backup_live_database

    def maintenance_summary(self, *, now_utc: datetime | None = None) -> Mapping[str, Any]:
        if not self.is_live_research:
            return {"status": "DISABLED_OFFLINE"}
        cleanup = self.cleanup_expired_raw_evidence(now_utc=now_utc)
        backup = self.backup_live_database(now_utc=now_utc)
        return {"status": "COMPLETE" if backup.get("status") == "COMPLETE" else "FAILED", "raw_cleanup": cleanup, "backup": backup}

    def fetch_claims(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM target_claims ORDER BY logical_due_bucket, source_parent_station_id").fetchall()
        return [dict(row) for row in rows]

    def fetch_claim_events(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM claim_events ORDER BY event_id").fetchall()
        return [dict(row) for row in rows]

    def fetch_dispatcher_claims(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM dispatcher_claims ORDER BY logical_due_bucket"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_dispatcher_claim_events(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM dispatcher_claim_events ORDER BY event_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_target_audits(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM target_audits ORDER BY audit_id").fetchall()
        return [dict(row) for row in rows]

    def fetch_attempts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM http_attempts ORDER BY attempt_id").fetchall()
        return [dict(row) for row in rows]

    def fetch_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM parsed_snapshots ORDER BY snapshot_id").fetchall()
        return [dict(row) for row in rows]

    def fetch_recorded_time_evidence(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM recorded_at_time_evidence ORDER BY evidence_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_timing_observations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM timing_observations ORDER BY timing_observation_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_service_section_observations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM service_section_observations ORDER BY service_section_observation_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_timing_recorded_at_time_reporting(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM timing_recorded_at_time_reporting ORDER BY SourceEventIdentityGroup"
            ).fetchall()
        return [dict(row) for row in rows]

    # Explicit aliases keep the projection vocabulary stable for a future
    # read-only adapter without making the evidence table itself mutable.
    fetch_recorded_at_time_reporting = fetch_timing_recorded_at_time_reporting
    timing_recorded_at_time_reporting = fetch_timing_recorded_at_time_reporting

    def fetch_quota_permits(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM quota_permits ORDER BY reserved_at_utc, permit_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_quota_audit(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM quota_audit ORDER BY quota_audit_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_raw_evidence(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM raw_evidence ORDER BY raw_evidence_id"
            ).fetchall()
        return [dict(row) for row in rows]

    # Port vocabulary aliases keep the future adapter contract explicit.
    start_run = start_collector_run
    complete_run = complete_collector_run
    fail_run = fail_collector_run
    run_failure = fail_collector_run
    run_skip = record_run_skip
    complete_target = complete_target_audit
    fail_target = complete_target_audit

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLitePersistence":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

"""Disposable local SQLite development backend for collector contracts."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .domain import HttpResponse, Target, TransportFailure
from .quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
    UsageSnapshot,
)


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
    """Durable behavior test backend; not a production schema or adapter."""

    is_production_ready = False
    is_disposable_development_backend = True
    supports_atomic_quota_finalization = True
    claim_identity_kind = "development dispatcher identity: (panel_version, logical_due_bucket); target identity adds source_parent_station_id"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15.0)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA busy_timeout = 15000")
            self._create_schema()

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
                FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id)
            );
            CREATE TABLE IF NOT EXISTS quota_audit (
                quota_audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_run_id TEXT NOT NULL,
                source_parent_station_id TEXT,
                logical_due_bucket INTEGER,
                decision_kind TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                recorded_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quota_permits (
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
                FOREIGN KEY (audit_id) REFERENCES target_audits(audit_id),
                FOREIGN KEY (snapshot_id) REFERENCES parsed_snapshots(snapshot_id)
            );
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
        self._ensure_column("shared_usage", "confirmed_provider_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "conservative_live_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "live_client_invocations", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "http_responses_received", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("shared_usage", "provider_quota_unknown_attempts", "INTEGER NOT NULL DEFAULT 0")
        self._connection.execute(
            "UPDATE quota_permits SET status = ? WHERE status = 'PENDING'",
            (PENDING_AUTHORIZATION,),
        )
        self._connection.commit()

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

    def start_collector_run(self, *, run_id: str, panel_version: str, logical_due_bucket: int, mode: str, started_at: datetime) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO collector_runs (run_id, panel_version, logical_due_bucket, mode, started_at_utc, status) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, panel_version, int(logical_due_bucket), mode, _iso(started_at), "RUNNING"),
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

    def start_target_audit(self, *, run_id: str, target: Target, logical_due_bucket: int, started_at: datetime) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO target_audits
                (collector_run_id, panel_version, source_parent_station_id, station_name, tier,
                 phase_index, number_of_results, logical_due_bucket, sampling_era,
                 continuity_classification, started_at_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, target.panel_version, target.source_parent_station_id, target.station_name, target.tier,
                 target.phase_index, target.number_of_results, int(logical_due_bucket), target.sampling_era.value,
                 target.continuity_classification, _iso(started_at), "RUNNING"),
            )
            self._connection.execute(
                "UPDATE target_claims SET status = ?, started_at_utc = ? WHERE panel_version = ? AND source_parent_station_id = ? AND logical_due_bucket = ?",
                ("RUNNING", _iso(started_at), target.panel_version, target.source_parent_station_id, int(logical_due_bucket)),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

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
        conservative_quota_accounted = network_attempted and not http_response_received
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

    def persist_snapshot(self, *, audit_id: int, target: Target, logical_due_bucket: int, parsed_snapshot: Mapping[str, Any], persisted_at: datetime) -> None:
        snapshot = dict(parsed_snapshot)
        for key in list(snapshot):
            lowered = str(key).lower()
            if key != "raw_payload_persisted" and any(marker in lowered for marker in ("raw", "authorization", "api_key", "secret")):
                snapshot.pop(key, None)
        snapshot["raw_payload_persisted"] = False
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
        with self._lock:
            snapshot_cursor = self._connection.execute(
                """
                INSERT INTO parsed_snapshots
                (audit_id, panel_version, source_parent_station_id, logical_due_bucket, persisted_at_utc, snapshot_json, raw_payload_persisted)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (audit_id, target.panel_version, target.source_parent_station_id, int(logical_due_bucket), _iso(persisted_at), encoded),
            )
            snapshot_id = int(snapshot_cursor.lastrowid)
            self._persist_recorded_time_evidence(
                audit_id=audit_id,
                snapshot_id=snapshot_id,
                target=target,
                logical_due_bucket=logical_due_bucket,
                parsed_snapshot=snapshot,
                observed_at=persisted_at,
            )
            self._connection.execute(
                "UPDATE target_audits SET persistence_outcome = ?, stop_event_result_count = ? WHERE audit_id = ?",
                ("PERSISTED_PARSED_SNAPSHOT", len(snapshot.get("events", [])), audit_id),
            )
            self._connection.commit()

    def _persist_recorded_time_evidence(
        self,
        *,
        audit_id: int,
        snapshot_id: int,
        target: Target,
        logical_due_bucket: int,
        parsed_snapshot: Mapping[str, Any],
        observed_at: datetime,
    ) -> None:
        for event in parsed_snapshot.get("events", []) or []:
            if not isinstance(event, Mapping):
                continue
            event_ordinal = event.get("event_ordinal", event.get("result_ordinal"))
            result_id = event.get("result_id")
            occurrences = event.get("recorded_at_time_occurrences", []) or []
            for occurrence in occurrences:
                if not isinstance(occurrence, Mapping) or occurrence.get("recorded_at_time") is None:
                    continue
                self._connection.execute(
                    """
                    INSERT INTO recorded_at_time_evidence
                    (audit_id, snapshot_id, panel_version, source_parent_station_id,
                     logical_due_bucket, event_ordinal, result_id, call_scope,
                     call_ordinal, timing_context, recorded_at_time, source_path,
                     observed_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        snapshot_id,
                        target.panel_version,
                        target.source_parent_station_id,
                        int(logical_due_bucket),
                        event_ordinal,
                        str(occurrence.get("result_id", result_id)) if occurrence.get("result_id", result_id) is not None else None,
                        str(occurrence.get("call_scope", "")),
                        int(occurrence.get("call_ordinal", 0)),
                        str(occurrence.get("timing_context", "")),
                        str(occurrence["recorded_at_time"]),
                        occurrence.get("source_path"),
                        _iso(observed_at),
                    ),
                )

    def persist_parsed_snapshot(self, **kwargs: Any) -> None:
        self.persist_snapshot(**kwargs)

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
                 retry_decision = ?, retry_outcome = ? WHERE audit_id = ?
                """,
                (_iso(completed_at), status, outcome.get("http_status"), int(outcome.get("http_attempts", 0)), outcome.get("stop_event_result_count"), outcome.get("parser_outcome"), outcome.get("persistence_outcome"), float(outcome.get("duration_seconds", 0) or 0) * 1000, outcome.get("skip_reason"), _safe_error_category(outcome.get("error_category")), outcome.get("retry_decision"), outcome.get("retry_outcome"), audit_id),
            )
            self._connection.execute(
                "UPDATE target_claims SET status = ?, completed_at_utc = ? WHERE panel_version = ? AND source_parent_station_id = ? AND logical_due_bucket = ?",
                (status, _iso(completed_at), row["panel_version"], row["source_parent_station_id"], row["logical_due_bucket"]),
            )
            self._connection.commit()

    def record_quota_decision(self, *, run_id: str, source_parent_station_id: str | None, logical_due_bucket: int | None, decision_kind: str, decision: Mapping[str, Any], recorded_at: datetime) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO quota_audit (collector_run_id, source_parent_station_id, logical_due_bucket, decision_kind, decision_json, recorded_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, source_parent_station_id, logical_due_bucket, decision_kind, json.dumps(dict(decision), sort_keys=True, default=str), _iso(recorded_at)),
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
            VALUES (?, 0, 0, 0, 0, 0, 0, 0, 1, 'sqlite-development')
            """,
            (month_key,),
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
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                shared = self._connection.execute(
                    "SELECT * FROM shared_usage WHERE month_key = ?", (month,)
                ).fetchone()
                state_available = bool(shared["state_available"] if shared else True)
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
                        "boundary": int(boundary),
                        "reason": "safe shared-credential state is unavailable",
                        "permit_id": None,
                    }
                elif not state_available:
                    permit_id = str(uuid.uuid4())
                    self._connection.execute(
                        """
                        INSERT INTO quota_permits
                        (permit_id, month_key, decision_kind, reservation_scope, amount,
                         boundary, reserved_at_utc, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (permit_id, month, decision_kind, scope, amount, int(boundary), _iso(at), PENDING_AUTHORIZATION),
                    )
                    decision = {
                        "allowed": True,
                        "decision": "INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": int(boundary),
                        "reason": "initial scheduled request is not silently converted into a retry",
                        "permit_id": permit_id,
                    }
                elif known_total + amount > int(boundary):
                    decision = {
                        "allowed": False,
                        "decision": (
                            "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM"
                            if decision_kind == "INITIAL"
                            else "RETRY_DENIED_PROTECTION_BOUNDARY"
                        ),
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": int(boundary),
                        "reason": "quota boundary would be exceeded",
                        "permit_id": None,
                    }
                else:
                    permit_id = str(uuid.uuid4())
                    self._connection.execute(
                        """
                        INSERT INTO quota_permits
                        (permit_id, month_key, decision_kind, reservation_scope, amount,
                         boundary, reserved_at_utc, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (permit_id, month, decision_kind, scope, amount, int(boundary), _iso(at), PENDING_AUTHORIZATION),
                    )
                    decision = {
                        "allowed": True,
                        "decision": "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": int(boundary),
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
                "SELECT COALESCE(SUM(amount), 0) AS amount FROM quota_permits WHERE month_key = ? AND reservation_scope = 'LIVE_PROVIDER' AND status = ?",
                (month, PENDING_AUTHORIZATION),
            ).fetchone()
        return {
            "confirmed_provider_attempts": int(shared["confirmed_provider_attempts"] if shared else 0),
            "confirmed_provider_http_attempts": int(shared["confirmed_provider_attempts"] if shared else 0),
            "actual_attempts": int(shared["confirmed_provider_attempts"] if shared else 0),
            "reserved_shared_usage": int(shared["reserved_shared_usage"] if shared else 0) + int(active["amount"] if active else 0),
            "projected_non_retry_attempts": int(shared["projected_non_retry_attempts"] if shared else 0),
            "conservative_live_attempts": int(shared["conservative_live_attempts"] if shared else 0),
            "conservative_quota_attempts": int(shared["conservative_live_attempts"] if shared else 0),
            "live_client_invocations": int(shared["live_client_invocations"] if shared else 0),
            "http_responses_received": int(shared["http_responses_received"] if shared else 0),
            "provider_quota_unknown_attempts": int(shared["provider_quota_unknown_attempts"] if shared else 0),
            "provider_quota_consumption_unknown": int(shared["provider_quota_unknown_attempts"] if shared else 0),
            "state_available": bool(shared["state_available"] if shared else True),
            "source": str(shared["source"] if shared else "sqlite-development"),
        }

    get_current_month_usage = current_month_usage

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

    def fetch_quota_permits(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM quota_permits ORDER BY reserved_at_utc, permit_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def retain_raw_evidence(self, **kwargs: Any) -> str:
        """Explicit future-live hook; ordinary SQLite never stores raw payloads."""

        return "DISABLED_OFFLINE"

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

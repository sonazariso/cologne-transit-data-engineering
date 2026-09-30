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
from .quota import UsageSnapshot


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
    }
    return text if text in allowed else "SANITIZED_ERROR"


@dataclass(frozen=True)
class ClaimResult:
    won: bool
    claim_key: tuple[str, str, int]
    existing_status: str | None
    claim_token: str | None
    event_id: int


class SQLitePersistence:
    """Durable behavior test backend; not a production schema or adapter."""

    is_production_ready = False
    is_disposable_development_backend = True
    claim_identity_kind = "development claim identity: (panel_version, source_parent_station_id, logical_due_bucket)"

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
            CREATE TABLE IF NOT EXISTS shared_usage (
                month_key TEXT PRIMARY KEY,
                reserved_shared_usage INTEGER NOT NULL DEFAULT 0,
                projected_non_retry_attempts INTEGER NOT NULL DEFAULT 0,
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
        self._connection.commit()

    def materialize_manifest(self, manifest: Any) -> None:
        with self._lock:
            for target in manifest.targets:
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO manifest_targets
                    (panel_version, source_parent_station_id, station_name, tier,
                     cadence_minutes, number_of_results, phase_index, phase_count,
                     continuity_classification, sampling_era, production_sampling_target_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> None:
        if response is not None:
            http_status = response.status_code
            category = None
            outcome = "HTTP_RESPONSE"
            duration = float(response.elapsed_seconds or (completed_at - started_at).total_seconds())
        elif transport_failure is not None:
            http_status = None
            category = _safe_error_category(transport_failure.category)
            outcome = "TRANSPORT_FAILURE"
            duration = (completed_at - started_at).total_seconds()
        else:
            http_status = None
            category = "SANITIZED_ERROR"
            outcome = "UNKNOWN_ATTEMPT"
            duration = (completed_at - started_at).total_seconds()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO http_attempts
                (audit_id, attempt_number, started_at_utc, completed_at_utc, http_status,
                 transport_failure_category, outcome, retry_decision, retry_after_seconds, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (audit_id, int(attempt_number), _iso(started_at), _iso(completed_at), http_status, category, outcome, retry_decision, retry_after_seconds, duration * 1000),
            )
            self._connection.execute(
                "UPDATE target_audits SET http_attempts = http_attempts + 1, http_status = COALESCE(?, http_status) WHERE audit_id = ?",
                (http_status, audit_id),
            )
            self._connection.commit()

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
            self._connection.execute(
                """
                INSERT INTO parsed_snapshots
                (audit_id, panel_version, source_parent_station_id, logical_due_bucket, persisted_at_utc, snapshot_json, raw_payload_persisted)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (audit_id, target.panel_version, target.source_parent_station_id, int(logical_due_bucket), _iso(persisted_at), encoded),
            )
            self._connection.execute(
                "UPDATE target_audits SET persistence_outcome = ?, stop_event_result_count = ? WHERE audit_id = ?",
                ("PERSISTED_PARSED_SNAPSHOT", len(snapshot.get("events", [])), audit_id),
            )
            self._connection.commit()

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
        reserved_shared_usage: int = 0,
        projected_non_retry_attempts: int = 0,
        state_available: bool = True,
        source: str = "sqlite-development",
    ) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO shared_usage (month_key, reserved_shared_usage, projected_non_retry_attempts, state_available, source) VALUES (?, ?, ?, ?, ?)",
                (month_key, int(reserved_shared_usage), int(projected_non_retry_attempts), int(state_available), source),
            )
            self._connection.commit()

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        at = at_utc or datetime.now(timezone.utc)
        month = _month_key(at)
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM http_attempts WHERE completed_at_utc LIKE ?", (f"{month}-%",)).fetchone()
            shared = self._connection.execute("SELECT * FROM shared_usage WHERE month_key = ?", (month,)).fetchone()
        return {
            "actual_attempts": int(row["count"] if row else 0),
            "reserved_shared_usage": int(shared["reserved_shared_usage"] if shared else 0),
            "projected_non_retry_attempts": int(shared["projected_non_retry_attempts"] if shared else 0),
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

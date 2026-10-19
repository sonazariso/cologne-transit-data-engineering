#!/usr/bin/env python3
"""Run a bounded, repeated Heumarkt live-research collection.

The collector remains a single current-heartbeat operation.  This foreground
wrapper only waits for the next current UTC five-minute bucket, invokes that
existing operation once, records external session metadata, and performs the
final read-only analysis after the requested window ends.

The wrapper never loads a credential file and never includes credential-shaped
values in session metadata, reports, or console output.  The existing live
client is responsible for the inherited environment credential at transport
time.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cologne_transit_collector.collector import (  # noqa: E402
    Collector,
    LiveResearchModeBlocked,
    retry_eligible,
)
from cologne_transit_collector.configuration import CollectorSettings  # noqa: E402
from cologne_transit_collector.domain import (  # noqa: E402
    MAX_ATTEMPTS,
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    LIVE_RESEARCH_LEGACY_ID,
    LIVE_RESEARCH_STATION_NAME,
    LIVE_RESEARCH_TARGET_ID,
    HttpResponse,
    PersistenceProfile,
    TransportFailure,
)
from cologne_transit_collector.manifest import RuntimeManifest, load_default_manifest  # noqa: E402
from cologne_transit_collector.persistence import (  # noqa: E402
    LiveResearchSafetyError,
    SCHEMA_VERSION,
    SQLitePersistence,
    validate_live_research_path,
)
from cologne_transit_collector.quota import QuotaGuard  # noqa: E402
from cologne_transit_collector.schedule import bucket_for  # noqa: E402
from cologne_transit_collector.transport import LiveMddClient  # noqa: E402
from cologne_transit_stage15.analyzer import (  # noqa: E402
    Stage15Analyzer,
    duration_statistics,
)
from cologne_transit_stage15.evidence import (  # noqa: E402
    EvidenceData,
    InMemoryEvidenceAdapter,
    SQLiteEvidenceAdapter,
    evidence_from_mappings,
)


REPORT_SCHEMA_VERSION = "live-research-session-report-1.0"
SESSION_SCHEMA_VERSION = "live-research-session-1.0"
_DURATION_RE = re.compile(
    r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)?\s*$",
    re.IGNORECASE,
)
_BACKUP_RE = re.compile(r"^collector-(?P<date>\d{4}-\d{2}-\d{2})\.sqlite3$")
_FATAL_ERROR_CATEGORIES = frozenset(
    {
        "CREDENTIAL_MISSING",
        "CREDENTIAL_FORMAT_INVALID",
        "PERSISTENCE_FAILURE",
    }
)


class RunnerBlocked(RuntimeError):
    """The bounded runner failed closed before or during collection."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _parse_duration(value: str) -> float:
    """Parse a finite positive duration such as ``4h`` or ``21600`` seconds."""

    match = _DURATION_RE.fullmatch(str(value))
    if match is None:
        raise argparse.ArgumentTypeError(
            "duration must be a positive number with optional s/m/h/d suffix"
        )
    amount = float(match.group("value"))
    unit = (match.group("unit") or "s").lower()
    multiplier = 1.0
    if unit.startswith("m"):
        multiplier = 60.0
    elif unit.startswith("h"):
        multiplier = 3600.0
    elif unit.startswith("d"):
        multiplier = 86400.0
    seconds = amount * multiplier
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and greater than zero")
    return seconds


def _expected_bucket_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Return every UTC bucket intersecting the requested half-open window."""

    if end <= start:
        return []
    first = bucket_for(start).index
    last = bucket_for(end - timedelta(microseconds=1)).index
    return [
        {
            "logical_due_bucket": index,
            "identity": bucket_for(datetime.fromtimestamp(index * 300, tz=timezone.utc)).identity,
        }
        for index in range(first, last + 1)
    ]


def _owner_only_directory(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except (NotImplementedError, OSError):
        pass


def _owner_only_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except (NotImplementedError, OSError):
        pass


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _owner_only_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _owner_only_file(temporary)
        os.replace(temporary, path)
        _owner_only_file(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _resolve_report_directory(
    requested: Path | None,
    settings: CollectorSettings,
    repository_root: Path,
) -> Path:
    candidate = (
        requested.expanduser().resolve()
        if requested is not None
        else (settings.state_path.parent / "reports").resolve()
    )
    try:
        candidate.relative_to(repository_root.resolve())
    except ValueError:
        return candidate
    raise LiveResearchSafetyError("research reports must be outside the Git repository")


def _read_only_sqlite_status(path: Path) -> dict[str, Any]:
    """Inspect one SQLite file without opening it for writes."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return {"path": str(resolved), "exists": False, "integrity_check": None}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata"
            ).fetchall()
        }
        return {
            "path": str(resolved),
            "exists": True,
            "integrity_check": integrity,
            "schema_version": metadata.get("schema_version"),
            "database_role": metadata.get("database_role"),
        }
    except (OSError, sqlite3.Error) as exc:
        return {
            "path": str(resolved),
            "exists": True,
            "integrity_check": None,
            "error_type": type(exc).__name__,
        }
    finally:
        if connection is not None:
            connection.close()


def _validate_current_backup(backup_directory: Path) -> dict[str, Any]:
    candidates = [
        path
        for path in backup_directory.glob("collector-*.sqlite3")
        if path.is_file() and _BACKUP_RE.fullmatch(path.name)
    ]
    candidates.sort(key=lambda path: path.name, reverse=True)
    if not candidates:
        raise RunnerBlocked("no validated daily SQLite backup is available")
    status = _read_only_sqlite_status(candidates[0])
    if status.get("integrity_check", "").casefold() != "ok":
        raise RunnerBlocked("the current daily SQLite backup failed read-only integrity validation")
    if status.get("schema_version") != SCHEMA_VERSION:
        raise RunnerBlocked("the current daily SQLite backup has an unsupported schema version")
    return {
        **status,
        "available_backup_count": len(candidates),
        "available_backup_paths": [str(path.resolve()) for path in candidates[:3]],
    }


def _session_template(
    *,
    session_id: str,
    start: datetime,
    end: datetime,
    duration_seconds: float,
    report_json: Path,
    summary_markdown: Path,
    session_metadata: Path,
) -> dict[str, Any]:
    expected = _expected_bucket_rows(start, end)
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "collection_context": LIVE_RESEARCH_COLLECTION_CONTEXT,
        "target": {
            "station": LIVE_RESEARCH_STATION_NAME,
            "source_parent_station_id": LIVE_RESEARCH_TARGET_ID,
            "legacy_sampling_lineage_id": LIVE_RESEARCH_LEGACY_ID,
            "number_of_results": 20,
        },
        "requested_window": {
            "start_utc": _iso(start),
            "end_utc": _iso(end),
            "duration_seconds": duration_seconds,
            "duration_hours": round(duration_seconds / 3600.0, 6),
        },
        "expected_current_buckets": expected,
        "observed_collector_runs": [],
        "actual_first_heartbeat_utc": None,
        "actual_last_heartbeat_utc": None,
        "completion_state": "RUNNING",
        "graceful_stop_requested": False,
        "stop_signal": None,
        "fatal_stop_reason": None,
        "storage_preflight": None,
        "initial_backup_validation": None,
        "final_maintenance": None,
        "database_path": None,
        "report_paths": {
            "session_metadata": str(session_metadata),
            "machine_report": str(report_json),
            "markdown_summary": str(summary_markdown),
        },
    }


def _result_record(
    result: Any,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    encoded = result.to_dict()
    return {
        "run_id": encoded.get("run_id"),
        "logical_due_bucket": encoded.get("logical_due_bucket"),
        "logical_due_bucket_identity": encoded.get("logical_due_bucket_identity"),
        "started_at_utc": _iso(started_at),
        "completed_at_utc": _iso(completed_at),
        "status": encoded.get("status"),
        "dispatcher_claim_status": encoded.get("dispatcher_claim_status"),
        "http_attempt_count": encoded.get("http_attempt_count"),
        "live_network_calls": encoded.get("live_network_calls"),
        "live_client_invocations": encoded.get("live_client_invocations"),
        "outcomes": encoded.get("outcomes", []),
        "maintenance": encoded.get("maintenance", {}),
    }


def _fatal_result_reason(result: Any) -> str | None:
    for outcome in result.outcomes:
        if outcome.http_status in {401, 403}:
            return f"HTTP_{outcome.http_status}_STOP"
        if outcome.error_category in _FATAL_ERROR_CATEGORIES:
            return str(outcome.error_category)
        if outcome.persistence_outcome == "PERSISTENCE_FAILURE":
            return "PERSISTENCE_FAILURE"
    maintenance = result.maintenance or {}
    if maintenance.get("status") == "FAILED":
        return "LIVE_RESEARCH_MAINTENANCE_FAILURE"
    return None


def _wait_until(
    *,
    stop_event: threading.Event,
    end_at: datetime,
) -> bool:
    """Wait in interruptible bounded pieces; return whether a stop was asked."""

    while not stop_event.is_set():
        remaining = (end_at - _utc_now()).total_seconds()
        if remaining <= 0:
            return False
        if stop_event.wait(min(remaining, 15.0)):
            return True
    return True


def _filter_session_evidence(evidence: EvidenceData, run_ids: set[str]) -> EvidenceData:
    """Keep only rows belonging to this external research session."""

    def by_run(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        filtered: list[Mapping[str, Any]] = []
        for row in rows:
            row_run_id = row.get("collector_run_id")
            if row_run_id is None:
                row_run_id = row.get("run_id")
            if str(row_run_id or "") in run_ids:
                filtered.append(row)
        return tuple(filtered)

    runs = by_run(evidence.collector_runs)
    dispatcher_claims = by_run(evidence.dispatcher_claims)
    dispatcher_events = by_run(evidence.dispatcher_claim_events)
    target_claims = by_run(evidence.target_claims)
    claim_events = by_run(evidence.claim_events)
    audits = by_run(evidence.target_audits)
    audit_ids = {_int(row.get("audit_id")) for row in audits}
    attempts = tuple(row for row in evidence.http_attempts if _int(row.get("audit_id")) in audit_ids)
    snapshots = tuple(row for row in evidence.parsed_snapshots if _int(row.get("audit_id")) in audit_ids)
    snapshot_ids = {_int(row.get("snapshot_id")) for row in snapshots}
    quota_audit = by_run(evidence.quota_audit)
    permit_ids: set[str] = set()
    for row in quota_audit:
        try:
            decision = json.loads(str(row.get("decision_json", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            decision = {}
        if decision.get("permit_id") is not None:
            permit_ids.add(str(decision["permit_id"]))
    quota_permits = tuple(
        row for row in evidence.quota_permits if str(row.get("permit_id")) in permit_ids
    )
    timing = tuple(row for row in evidence.timing_observations if _int(row.get("snapshot_id")) in snapshot_ids)
    recorded = tuple(
        row for row in evidence.recorded_at_time_evidence if _int(row.get("snapshot_id")) in snapshot_ids
    )
    service = tuple(
        row for row in evidence.service_section_observations if _int(row.get("snapshot_id")) in snapshot_ids
    )
    reporting = tuple(
        row
        for row in evidence.timing_recorded_at_time_reporting
        if _int(row.get("LatestSourceRecordedAtTimeSnapshotId")) in snapshot_ids
    )
    raw = tuple(
        row
        for row in evidence.raw_evidence
        if str(row.get("collector_run_id", "")) in run_ids
        or _int(row.get("audit_id")) in audit_ids
    )
    return evidence_from_mappings(
        source=evidence.source,
        available=evidence.available,
        limitations=evidence.limitations,
        manifest_targets=evidence.manifest_targets,
        collector_runs=runs,
        dispatcher_claims=dispatcher_claims,
        dispatcher_claim_events=dispatcher_events,
        target_claims=target_claims,
        claim_events=claim_events,
        target_audits=audits,
        http_attempts=attempts,
        parsed_snapshots=snapshots,
        quota_audit=quota_audit,
        quota_permits=quota_permits,
        shared_usage=evidence.shared_usage,
        sampling_eras=evidence.sampling_eras,
        recorded_at_time_evidence=recorded,
        timing_observations=timing,
        timing_recorded_at_time_reporting=reporting,
        service_section_observations=service,
        raw_evidence=raw,
        database_metadata=evidence.database_metadata,
    )


def _row_in_window(row: Mapping[str, Any], start: datetime, end: datetime) -> bool:
    bucket = _int(row.get("logical_due_bucket", row.get("LogicalDueBucket")), -1)
    expected = {item["logical_due_bucket"] for item in _expected_bucket_rows(start, end)}
    if bucket not in expected:
        return False
    for field in (
        "observed_at_utc",
        "captured_at_utc",
        "persisted_at_utc",
        "started_at_utc",
    ):
        parsed = _parse_datetime(row.get(field))
        if parsed is not None:
            return start <= parsed < end
    return True


def _timing_rows_in_window(
    rows: Sequence[Mapping[str, Any]],
    start: datetime,
    end: datetime,
) -> list[Mapping[str, Any]]:
    return [row for row in rows if _row_in_window(row, start, end)]


def _recorded_time_reconciliation(
    timing_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    nonnull = [row for row in timing_rows if row.get("recorded_at_time") is not None]
    keys = Counter(
        (
            row.get("snapshot_id"),
            row.get("event_ordinal"),
            str(row.get("call_scope", "")),
            row.get("call_ordinal"),
            str(row.get("timing_context", "")),
            row.get("recorded_at_time"),
            row.get("source_path"),
        )
        for row in evidence_rows
    )
    unmatched = 0
    for row in nonnull:
        source_path = row.get("recorded_at_time_source_path") or row.get("source_path")
        key = (
            row.get("snapshot_id"),
            row.get("event_ordinal"),
            str(row.get("call_scope", "")),
            row.get("call_ordinal"),
            str(row.get("timing_context", "")),
            row.get("recorded_at_time"),
            source_path,
        )
        if not keys.get(key):
            unmatched += 1
    duplicate_evidence = sum(max(0, count - 1) for count in keys.values())
    missing_leaf_path = sum(
        row.get("recorded_at_time_source_path") in (None, "") for row in nonnull
    )
    return {
        "timing_rows": len(timing_rows),
        "timing_nonnull_recorded_at_time_rows": len(nonnull),
        "recorded_at_time_evidence_rows": len(evidence_rows),
        "unmatched_timing_rows": unmatched,
        "duplicate_evidence_rows": duplicate_evidence,
        "nonnull_rows_missing_exact_leaf_source_path": missing_leaf_path,
        "status": "PASS" if unmatched == 0 and duplicate_evidence == 0 else "FAIL",
    }


def _timing_lifecycle_report(
    timing_rows: Sequence[Mapping[str, Any]],
    recorded_projection: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    established: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    status_counts: Counter[str] = Counter()
    for row in timing_rows:
        status = str(row.get("source_event_identity_status") or "UNKNOWN")
        status_counts[status] += 1
        if status == "SOURCE_EVENT_IDENTITY_ESTABLISHED" and row.get("source_event_identity"):
            established[str(row["source_event_identity"])].append(row)

    repeated_snapshot_events = 0
    events_with_estimate_revisions = 0
    total_estimate_revisions = 0
    events_with_recorded_time = 0
    for rows in established.values():
        ordered = sorted(
            rows,
            key=lambda row: (
                _parse_datetime(row.get("observed_at_utc"))
                or datetime.min.replace(tzinfo=timezone.utc),
                _int(row.get("timing_observation_id")),
            ),
        )
        if len({_int(row.get("snapshot_id")) for row in ordered}) > 1:
            repeated_snapshot_events += 1
        estimates = [str(row.get("estimated_time")) for row in ordered if row.get("estimated_time") is not None]
        revisions = sum(previous != current for previous, current in zip(estimates, estimates[1:]))
        if revisions:
            events_with_estimate_revisions += 1
            total_estimate_revisions += revisions
        if any(row.get("recorded_at_time") is not None for row in ordered):
            events_with_recorded_time += 1

    projection_rows = list(recorded_projection)
    latest_null_historical_nonnull = sum(
        row.get("LatestSourceRecordedAtTime") is None
        and row.get("LastObservedRecordedAtTime") is not None
        for row in projection_rows
    )
    first_last_checks = {
        "latest_non_null_rows_have_leaf_paths": all(
            row.get("LatestSourceRecordedAtTime") is None
            or row.get("LatestSourceRecordedAtTimeSourcePath")
            for row in projection_rows
        ),
        "latest_null_rows_have_null_source_paths": all(
            row.get("LatestSourceRecordedAtTime") is not None
            or row.get("LatestSourceRecordedAtTimeSourcePath") is None
            for row in projection_rows
        ),
        "last_non_null_rows_have_leaf_paths": all(
            row.get("LastObservedRecordedAtTime") is None
            or row.get("LastObservedRecordedAtTimeSourcePath")
            for row in projection_rows
        ),
        "first_non_null_rows_have_leaf_paths": all(
            row.get("FirstObservedRecordedAtTime") is None
            or row.get("FirstObservedRecordedAtTimeSourcePath")
            for row in projection_rows
        ),
    }
    return {
        "distinct_established_source_event_identities": len(established),
        "ambiguous_identity_observations": status_counts.get("SOURCE_EVENT_IDENTITY_AMBIGUOUS_CORE", 0),
        "insufficient_identity_observations": sum(
            count
            for status, count in status_counts.items()
            if status not in {"SOURCE_EVENT_IDENTITY_ESTABLISHED", "SOURCE_EVENT_IDENTITY_AMBIGUOUS_CORE"}
        ),
        "identity_status_distribution": dict(sorted(status_counts.items())),
        "events_seen_in_more_than_one_snapshot": repeated_snapshot_events,
        "events_with_estimated_time_revisions": events_with_estimate_revisions,
        "total_estimated_time_revisions": total_estimate_revisions,
        "events_with_non_null_recorded_at_time": events_with_recorded_time,
        "total_non_null_recorded_at_time_occurrences": sum(
            row.get("recorded_at_time") is not None for row in timing_rows
        ),
        "events_with_latest_recorded_at_time_null_and_historical_non_null": latest_null_historical_nonnull,
        "first_last_recorded_at_time_lineage_checks": first_last_checks,
        "reporting_projection_event_count": len(projection_rows),
    }


def _source_evidence_report(
    snapshots: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
    service_sections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    parser_gaps: Counter[str] = Counter()
    situation_count = 0
    attribute_count = 0
    application_error_snapshots = 0
    application_error_evidence = 0
    for row in snapshots:
        try:
            payload = json.loads(str(row.get("snapshot_json", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for gap in payload.get("parser_gaps", ()) or ():
            if isinstance(gap, Mapping):
                name = str(
                    gap.get("normalized_field_name")
                    or gap.get("field_name")
                    or gap.get("source_path")
                    or "UNKNOWN"
                )
            else:
                name = "UNKNOWN"
            parser_gaps[name] += 1
        application_error = bool(payload.get("application_error"))
        if application_error:
            application_error_snapshots += 1
        response_errors = payload.get("response_errors", ()) or ()
        if isinstance(response_errors, (list, tuple)):
            application_error_evidence += len(response_errors)
        for event in payload.get("events", ()) or ():
            if not isinstance(event, Mapping):
                continue
            situations = event.get("situation_evidence", ()) or ()
            attributes = event.get("service_attribute_evidence", ()) or ()
            if isinstance(situations, (list, tuple)):
                situation_count += len(situations)
            if isinstance(attributes, (list, tuple)):
                attribute_count += len(attributes)
    return {
        "parser_gap_total": sum(parser_gaps.values()),
        "parser_gap_groups": dict(sorted(parser_gaps.items())),
        "service_section_evidence_count": len(service_sections),
        "situation_evidence_count": situation_count,
        "attribute_general_attribute_evidence_count": attribute_count,
        "stop_event_result_count": sum(
            _int(row.get("stop_event_result_count"))
            for row in audits
            if row.get("stop_event_result_count") is not None
        ),
        "application_error_snapshot_count": application_error_snapshots,
        "application_error_evidence_count": application_error_evidence,
    }


def _duration_report(
    runs: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    run_durations: list[float] = []
    for row in runs:
        started = _parse_datetime(row.get("started_at_utc"))
        completed = _parse_datetime(row.get("completed_at_utc"))
        if started is not None and completed is not None and completed >= started:
            run_durations.append((completed - started).total_seconds())
    target_durations = [
        duration / 1000.0
        for row in audits
        if (duration := _float(row.get("duration_ms"))) is not None
    ]
    attempt_durations = [
        duration / 1000.0
        for row in attempts
        if (duration := _float(row.get("duration_ms"))) is not None
    ]
    return {
        "target_duration": {"unit": "seconds", **duration_statistics(target_durations)},
        "http_attempt_duration": {"unit": "seconds", **duration_statistics(attempt_durations)},
        "heartbeat_collector_run_duration": {"unit": "seconds", **duration_statistics(run_durations)},
    }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attempt_is_retry_policy_eligible(row: Mapping[str, Any]) -> bool:
    http_status = _optional_int(row.get("http_status"))
    if http_status is not None:
        return retry_eligible(response=HttpResponse(status_code=http_status))
    category = row.get("transport_failure_category")
    if category:
        return retry_eligible(transport_failure=TransportFailure(category=str(category)))
    return False


def _retry_diagnostics(
    attempts: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
    quota_audit: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Expose provider/transport failures separately from retry safety decisions."""

    audit_context = {
        _int(row.get("audit_id"), -1): (
            str(row.get("collector_run_id") or ""),
            str(row.get("source_parent_station_id") or ""),
            _int(row.get("logical_due_bucket"), -1),
        )
        for row in audits
    }
    retry_denials: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in quota_audit:
        if str(row.get("decision_kind") or "").upper() != "RETRY":
            continue
        try:
            decision = json.loads(str(row.get("decision_json", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            decision = {}
        if not isinstance(decision, Mapping) or decision.get("allowed", True):
            continue
        key = (
            str(row.get("collector_run_id") or ""),
            str(row.get("source_parent_station_id") or ""),
            _int(row.get("logical_due_bucket"), -1),
        )
        retry_denials[key].append(decision)

    failure_attempts: list[dict[str, Any]] = []
    suppression_counts: Counter[str] = Counter()
    for row in attempts:
        http_status = _optional_int(row.get("http_status"))
        category = str(row.get("transport_failure_category") or "") or None
        is_http_failure = http_status is not None and not 200 <= http_status < 300
        if not is_http_failure and category is None:
            continue

        policy_eligible = _attempt_is_retry_policy_eligible(row)
        attempt_number = _int(row.get("attempt_number"), 0)
        retry_available = policy_eligible and attempt_number < MAX_ATTEMPTS
        context = audit_context.get(_int(row.get("audit_id"), -1))
        denial: Mapping[str, Any] | None = None
        if context is not None:
            denial_candidates = retry_denials.get(context)
            if denial_candidates:
                denial = denial_candidates[0]

        suppression_reason: str | None = None
        suppression_detail: str | None = None
        if denial is not None:
            suppression_reason = str(denial.get("decision") or "RETRY_DENIED")
            suppression_detail = str(denial.get("reason") or "") or None
        elif not policy_eligible:
            suppression_reason = "NOT_RETRY_POLICY_ELIGIBLE"
        elif attempt_number >= MAX_ATTEMPTS:
            suppression_reason = "MAX_ATTEMPTS_REACHED"
        if suppression_reason is not None:
            suppression_counts[suppression_reason] += 1

        if http_status is not None:
            classification = (
                "TRANSIENT_PROVIDER_SERVER_HTTP_FAILURE"
                if http_status == 500
                else "HTTP_RESPONSE_FAILURE"
            )
            failure_kind = "HTTP_RESPONSE"
        else:
            classification = "TRANSPORT_FAILURE"
            failure_kind = "TRANSPORT_FAILURE"
        failure_attempts.append(
            {
                "audit_id": _int(row.get("audit_id"), -1),
                "attempt_number": attempt_number,
                "original_failure": {
                    "kind": failure_kind,
                    "http_status": http_status,
                    "transport_failure_category": category,
                    "classification": classification,
                    "cause_inferred": False,
                },
                "retry_eligible": policy_eligible,
                "retry_policy_eligible": policy_eligible,
                "retry_available_under_max_attempts": retry_available,
                "retry_decision": row.get("retry_decision"),
                "retry_suppression_reason": suppression_reason,
                "retry_suppression_detail": suppression_detail,
            }
        )

    return {
        "http_500_semantics": {
            "classification": "TRANSIENT_PROVIDER_SERVER_HTTP_FAILURE",
            "approved_retryable_status": True,
            "cause_inferred": False,
        },
        "failure_attempt_count": len(failure_attempts),
        "retry_policy_eligible_failure_attempt_count": sum(
            bool(row["retry_policy_eligible"]) for row in failure_attempts
        ),
        "retry_suppressed_failure_attempt_count": sum(
            row["retry_suppression_reason"] is not None for row in failure_attempts
        ),
        "retry_suppression_distribution": dict(sorted(suppression_counts.items())),
        "failure_attempts": failure_attempts,
        "statement": (
            "HTTP/transport failure evidence, retry-policy eligibility, and quota-safety "
            "suppression are separate facts; quota-state denial does not reclassify an HTTP 500."
        ),
    }


def _build_read_only_analysis(
    *,
    manifest: RuntimeManifest,
    repository_root: Path,
    evidence: EvidenceData,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    contract_path = repository_root / "config" / "stage15_validation_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return Stage15Analyzer(
        manifest,
        contract=contract,
        evidence_adapter=InMemoryEvidenceAdapter(evidence),
        window_start_utc=start,
        window_end_utc=end,
    ).analyze()


def build_final_report(
    *,
    session: Mapping[str, Any],
    manifest: RuntimeManifest,
    repository_root: Path,
    full_evidence: EvidenceData,
    session_evidence: EvidenceData,
    database_status: Mapping[str, Any],
    backup_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    requested = session["requested_window"]
    start = _parse_datetime(requested["start_utc"])
    end = _parse_datetime(requested["end_utc"])
    if start is None or end is None:
        raise ValueError("session requested window is not timezone-aware")

    run_ids = {
        str(row.get("run_id"))
        for row in session_evidence.collector_runs
        if row.get("run_id") is not None
    }
    runs = list(session_evidence.collector_runs)
    audits = list(session_evidence.target_audits)
    audit_ids = {_int(row.get("audit_id")) for row in audits}
    attempts = [row for row in session_evidence.http_attempts if _int(row.get("audit_id")) in audit_ids]
    snapshots = list(session_evidence.parsed_snapshots)
    timing_rows = _timing_rows_in_window(session_evidence.timing_observations, start, end)
    recorded_rows = _timing_rows_in_window(session_evidence.recorded_at_time_evidence, start, end)
    service_rows = list(session_evidence.service_section_observations)
    analyzer_report = _build_read_only_analysis(
        manifest=manifest,
        repository_root=repository_root,
        evidence=session_evidence,
        start=start,
        end=end,
    )
    quality_history = analyzer_report.get("quality", {}).get("timing_history", {})
    reporting_projection = quality_history.get("reporting_projection", [])
    timing_lifecycle = _timing_lifecycle_report(timing_rows, reporting_projection)
    reconciliation = _recorded_time_reconciliation(timing_rows, recorded_rows)
    source_evidence = _source_evidence_report(snapshots, audits, service_rows)

    observed_bucket_indexes = sorted({_int(row.get("logical_due_bucket")) for row in runs})
    expected_bucket_indexes = [
        _int(item.get("logical_due_bucket")) for item in session.get("expected_current_buckets", [])
    ]
    missing_bucket_indexes = sorted(set(expected_bucket_indexes) - set(observed_bucket_indexes))
    status_counts = Counter(str(row.get("status")) for row in audits)
    skip_budget = sum(
        "HEARTBEAT_BUDGET" in str(row.get("skip_reason", ""))
        or "HEARTBEAT_BUDGET" in str(row.get("retry_outcome", ""))
        for row in audits
    )
    skipped_quota = sum("QUOTA" in str(row.get("skip_reason", "")) for row in audits)
    duplicate_dispatcher = sum(
        str(row.get("outcome")) == "DUPLICATE_DISPATCHER_CLAIM"
        for row in session_evidence.dispatcher_claim_events
    )
    duplicate_target = sum(
        str(row.get("outcome")) == "DUPLICATE_CLAIM"
        for row in session_evidence.claim_events
    )
    duplicate_runs = sum(str(row.get("status")) == "DUPLICATE_NO_WORK" for row in runs)
    status_distribution = Counter(
        str(_int(row.get("http_status")))
        for row in attempts
        if row.get("http_status") is not None
    )
    transport_distribution = Counter(
        str(row.get("transport_failure_category"))
        for row in attempts
        if row.get("transport_failure_category")
    )
    retry_attempts = sum(_int(row.get("attempt_number")) > 1 for row in attempts)

    quota_decisions: list[Mapping[str, Any]] = []
    for row in session_evidence.quota_audit:
        try:
            decision = json.loads(str(row.get("decision_json", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            decision = {}
        if isinstance(decision, Mapping):
            quota_decisions.append(decision)
    initial_unknown = sum(
        str(row.get("decision")) == "INITIAL_ALLOWED_WITHOUT_RETRY_STATE"
        for row in quota_decisions
    )
    retry_unknown_denied = sum(
        str(row.get("decision")) == "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE"
        for row in quota_decisions
    )
    shared_state_unknown = any(not _bool(row.get("state_available")) for row in full_evidence.shared_usage)
    if not full_evidence.shared_usage and any(
        _bool(row.get("network_attempted")) for row in attempts
    ):
        shared_state_unknown = True
    quota_limitations = {
        "shared_provider_state_unknown": shared_state_unknown,
        "initial_requests_allowed_while_state_unknown": initial_unknown,
        "automatic_retries_denied_while_state_unknown": retry_unknown_denied,
        "provider_wide_remaining_quota_claimed": False,
        "statement": "Provider-wide remaining quota was not established by this research session.",
    }

    total_live_snapshots = len(full_evidence.parsed_snapshots)
    total_timing_rows = len(full_evidence.timing_observations)
    total_service_rows = len(full_evidence.service_section_observations)
    database_reconciliation = _recorded_time_reconciliation(
        _timing_rows_in_window(full_evidence.timing_observations, start, end),
        _timing_rows_in_window(full_evidence.recorded_at_time_evidence, start, end),
    )
    actual_first = _parse_datetime(session.get("actual_first_heartbeat_utc"))
    actual_last = _parse_datetime(session.get("actual_last_heartbeat_utc"))
    actual_duration = (
        max(0.0, (actual_last - actual_first).total_seconds())
        if actual_first is not None and actual_last is not None
        else 0.0
    )
    repeated_lifecycle_count = timing_lifecycle["events_seen_in_more_than_one_snapshot"]
    nonnull_count = timing_lifecycle["total_non_null_recorded_at_time_occurrences"]
    extension_recommended = nonnull_count == 0 or repeated_lifecycle_count == 0
    retry_diagnostics = _retry_diagnostics(
        attempts,
        audits,
        session_evidence.quota_audit,
    )
    structural_failures: list[str] = []
    if str(database_status.get("integrity_check", "")).casefold() != "ok":
        structural_failures.append("database_integrity_check")
    if reconciliation["status"] != "PASS":
        structural_failures.append("RecordedAtTime_reconciliation")
    if backup_status is not None and str(backup_status.get("integrity_check", "")).casefold() != "ok":
        structural_failures.append("backup_integrity_check")
    if not all(timing_lifecycle["first_last_recorded_at_time_lineage_checks"].values()):
        structural_failures.append("RecordedAtTime_leaf_lineage")
    if structural_failures:
        report_status = "FAIL"
    elif str(session.get("completion_state")) == "INTERRUPTED":
        report_status = "INTERRUPTED"
    elif session.get("fatal_stop_reason"):
        report_status = "STOPPED_FATAL"
    else:
        report_status = "COMPLETE"

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": report_status,
        "session": {
            "session_id": session.get("session_id"),
            "collection_context": LIVE_RESEARCH_COLLECTION_CONTEXT,
            "station": LIVE_RESEARCH_STATION_NAME,
            "source_parent_station_id": LIVE_RESEARCH_TARGET_ID,
            "legacy_sampling_lineage_id": LIVE_RESEARCH_LEGACY_ID,
            "requested_start_utc": requested.get("start_utc"),
            "requested_end_utc": requested.get("end_utc"),
            "duration_requested_seconds": requested.get("duration_seconds"),
            "duration_requested_hours": requested.get("duration_hours"),
            "actual_first_heartbeat_utc": session.get("actual_first_heartbeat_utc"),
            "actual_last_heartbeat_utc": session.get("actual_last_heartbeat_utc"),
            "actual_observation_duration_seconds": round(actual_duration, 6),
            "completion_state": session.get("completion_state"),
            "graceful_stop_requested": session.get("graceful_stop_requested"),
            "stop_signal": session.get("stop_signal"),
            "fatal_stop_reason": session.get("fatal_stop_reason"),
            "database_path": session.get("database_path"),
            "report_paths": session.get("report_paths"),
        },
        "collection_execution": {
            "expected_five_minute_bucket_count": len(expected_bucket_indexes),
            "expected_five_minute_buckets": session.get("expected_current_buckets", []),
            "observed_bucket_count": len(observed_bucket_indexes),
            "observed_bucket_indexes": observed_bucket_indexes,
            "missing_buckets": missing_bucket_indexes,
            "collector_run_count": len(runs),
            "collector_run_status_distribution": dict(sorted(Counter(str(row.get("status")) for row in runs).items())),
            "successful_target_executions": status_counts.get("SUCCEEDED", 0),
            "failed_target_executions": status_counts.get("FAILED", 0),
            "skipped_budget_executions": skip_budget,
            "skipped_quota_executions": skipped_quota,
            "duplicate_invocation_claim_prevention": {
                "duplicate_dispatcher_claim_events": duplicate_dispatcher,
                "duplicate_target_claim_events": duplicate_target,
                "duplicate_no_work_runs": duplicate_runs,
                "transport_replayed_by_runner": False,
            },
            "actual_live_network_attempts": sum(_bool(row.get("network_attempted")) for row in attempts),
            "live_client_invocations": sum(_bool(row.get("live_client_invocation")) for row in attempts),
            "http_response_status_distribution": dict(sorted(status_distribution.items())),
            "transport_failure_distribution": dict(sorted(transport_distribution.items())),
            "retry_attempts": retry_attempts,
            "retry_diagnostics": retry_diagnostics,
            "quota_state_limitations": quota_limitations,
            "observed_collector_runs": session.get("observed_collector_runs", []),
        },
        "persistence": {
            "new_parsed_snapshots": len(snapshots),
            "total_parsed_snapshots": total_live_snapshots,
            "timing_observations": len(timing_rows),
            "total_timing_observations": total_timing_rows,
            "service_section_observations": len(service_rows),
            "total_service_section_observations": total_service_rows,
            "database_integrity": dict(database_status),
            "recorded_at_time_reconciliation": reconciliation,
            "full_database_window_reconciliation": database_reconciliation,
            "backup_result": session.get("final_maintenance", {}).get("backup") if isinstance(session.get("final_maintenance"), Mapping) else None,
            "backup_integrity": dict(backup_status) if backup_status is not None else None,
            "raw_evidence_cleanup_result": session.get("final_maintenance", {}).get("raw_cleanup") if isinstance(session.get("final_maintenance"), Mapping) else None,
        },
        "timing_lifecycle": timing_lifecycle,
        "source_evidence": source_evidence,
        "durations": _duration_report(runs, audits, attempts),
        "read_only_analysis": analyzer_report,
        "extension_rule": {
            "status": "EVIDENCE_WINDOW_EXTENSION_RECOMMENDED" if extension_recommended else "NOT_RECOMMENDED",
            "recommend_total_duration_hours": 8 if extension_recommended else None,
            "extension_requires_explicit_later_invocation": True,
            "reason": (
                "No non-null RecordedAtTime was observed or no established event lifecycle repeated across snapshots; "
                "an explicit later invocation may extend the same Heumarkt research window to a total of 8 hours."
                if extension_recommended
                else "The requested bounded window contained non-null RecordedAtTime and repeated established event lifecycles."
            ),
            "automatic_extension_performed": False,
        },
        "structural_invariant_violations": structural_failures,
        "thresholds": {
            "failure_rate_threshold_invented": False,
            "retry_rate_threshold_invented": False,
            "parser_gap_rate_threshold_invented": False,
            "latency_threshold_invented": False,
        },
        "scope_confirmation": {
            "postgresql_activity": False,
            "production_live_activity": False,
            "tiered50_production_activation": False,
            "production_cutover_timestamp_created": False,
            "post_cutover_validation_started": False,
            "power_bi_activity": False,
            "other_station_executed": False,
        },
    }


def _markdown_summary(report: Mapping[str, Any]) -> str:
    session = report.get("session", {})
    execution = report.get("collection_execution", {})
    persistence = report.get("persistence", {})
    timing = report.get("timing_lifecycle", {})
    source = report.get("source_evidence", {})
    extension = report.get("extension_rule", {})
    durations = report.get("durations", {})
    backup = persistence.get("backup_integrity") or {}
    return "\n".join(
        [
            "# Heumarkt live-research session summary",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Session: `{session.get('session_id')}`",
            f"- Requested window: `{session.get('requested_start_utc')}` to `{session.get('requested_end_utc')}`",
            f"- Actual first/last heartbeat: `{session.get('actual_first_heartbeat_utc')}` / `{session.get('actual_last_heartbeat_utc')}`",
            f"- Requested duration: `{session.get('duration_requested_hours')} hours`; actual heartbeat span: `{session.get('actual_observation_duration_seconds')} seconds`",
            "",
            "## Collection execution",
            "",
            f"- Expected/observed/missing buckets: `{execution.get('expected_five_minute_bucket_count')}` / `{execution.get('observed_bucket_count')}` / `{len(execution.get('missing_buckets', []))}`",
            f"- Collector runs: `{execution.get('collector_run_count')}`; successful/failed/skipped-budget targets: `{execution.get('successful_target_executions')}` / `{execution.get('failed_target_executions')}` / `{execution.get('skipped_budget_executions')}`",
            f"- Live network attempts: `{execution.get('actual_live_network_attempts')}`; HTTP statuses: `{execution.get('http_response_status_distribution')}`; retries: `{execution.get('retry_attempts')}`",
            f"- Duplicate claim prevention: `{execution.get('duplicate_invocation_claim_prevention')}`",
            f"- HTTP 500 remains a transient provider/server failure; retry eligibility and quota-state suppression are separate: `{execution.get('retry_diagnostics')}`",
            "",
            "## Persistence and timing",
            "",
            f"- New/total snapshots: `{persistence.get('new_parsed_snapshots')}` / `{persistence.get('total_parsed_snapshots')}`; timing observations: `{persistence.get('timing_observations')}`; ServiceSection observations: `{persistence.get('service_section_observations')}`",
            f"- EstimatedTime revision events/total revisions: `{timing.get('events_with_estimated_time_revisions')}` / `{timing.get('total_estimated_time_revisions')}`",
            f"- RecordedAtTime events/occurrences: `{timing.get('events_with_non_null_recorded_at_time')}` / `{timing.get('total_non_null_recorded_at_time_occurrences')}`; latest NULL with historical value: `{timing.get('events_with_latest_recorded_at_time_null_and_historical_non_null')}`",
            f"- RecordedAtTime reconciliation: `{persistence.get('recorded_at_time_reconciliation', {}).get('status')}`; database integrity: `{persistence.get('database_integrity', {}).get('integrity_check')}`; backup: `{backup.get('path')}` (`{backup.get('integrity_check')}`)",
            "",
            "## Source evidence",
            "",
            f"- Parser gaps: `{source.get('parser_gap_total')}` grouped as `{source.get('parser_gap_groups')}`",
            f"- Situation / Attribute-GeneralAttribute / ServiceSection evidence: `{source.get('situation_evidence_count')}` / `{source.get('attribute_general_attribute_evidence_count')}` / `{source.get('service_section_evidence_count')}`",
            f"- StopEventResult / application-error evidence: `{source.get('stop_event_result_count')}` / `{source.get('application_error_evidence_count')}`",
            "",
            "## Durations (seconds)",
            "",
            f"- Target: `{durations.get('target_duration')}`",
            f"- HTTP attempt: `{durations.get('http_attempt_duration')}`",
            f"- Collector run: `{durations.get('heartbeat_collector_run_duration')}`",
            "",
            f"Extension rule: `{extension.get('status')}`; recommended total duration: `{extension.get('recommend_total_duration_hours')} hours`; automatic extension: `{extension.get('automatic_extension_performed')}`; explicit later invocation required: `{extension.get('extension_requires_explicit_later_invocation')}`.",
            "",
            "PostgreSQL, production live, Tiered50 activation, production cutover, post-cutover validation, and Power BI activity remained untouched.",
            "",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded repeated Heumarkt LIVE_RESEARCH heartbeats."
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="required explicit opt-in for live research",
    )
    parser.add_argument(
        "--duration",
        type=_parse_duration,
        default=_parse_duration("4h"),
        help="bounded duration, for example 4h or 14400 (default: 4h)",
    )
    parser.add_argument("--live-research-db", "--live-db", dest="live_research_db", type=Path)
    parser.add_argument("--live-data-root", "--live-root", dest="live_data_root", type=Path)
    parser.add_argument("--report-dir", type=Path)
    return parser


def run_bounded_collection(args: argparse.Namespace) -> dict[str, Any]:
    if not bool(args.allow_live):
        raise RunnerBlocked("explicit --allow-live opt-in is required")
    duration_seconds = float(args.duration)
    settings = CollectorSettings.for_live_research(
        REPOSITORY_ROOT,
        database_path=args.live_research_db,
        data_root=args.live_data_root,
    )
    validate_live_research_path(settings.state_path, repository_root=REPOSITORY_ROOT)
    if not settings.state_path.is_file():
        raise RunnerBlocked("the existing LIVE_RESEARCH SQLite database is required; no database was created")
    initial_database = _read_only_sqlite_status(settings.state_path)
    if initial_database.get("integrity_check", "").casefold() != "ok":
        raise RunnerBlocked("the existing LIVE_RESEARCH SQLite database failed read-only integrity validation")
    if initial_database.get("database_role") != PersistenceProfile.LIVE_RESEARCH.value:
        raise RunnerBlocked("the existing SQLite database is not marked LIVE_RESEARCH")
    if initial_database.get("schema_version") != SCHEMA_VERSION:
        raise RunnerBlocked("the existing LIVE_RESEARCH SQLite database has an unsupported schema version")
    initial_backup = _validate_current_backup(settings.backup_path or settings.state_path.parent / "backups")
    report_dir = _resolve_report_directory(args.report_dir, settings, REPOSITORY_ROOT)
    session_id = f"live-research-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    session_metadata_path = report_dir / f"{session_id}.session.json"
    report_json_path = report_dir / f"{session_id}.report.json"
    summary_path = report_dir / f"{session_id}.summary.md"
    started = _utc_now()
    ended = started + timedelta(seconds=duration_seconds)
    session: dict[str, Any] = _session_template(
        session_id=session_id,
        start=started,
        end=ended,
        duration_seconds=duration_seconds,
        report_json=report_json_path,
        summary_markdown=summary_path,
        session_metadata=session_metadata_path,
    )
    session["database_path"] = str(settings.state_path)
    session["initial_backup_validation"] = initial_backup
    _write_json_atomic(session_metadata_path, session)

    manifest = load_default_manifest(REPOSITORY_ROOT)
    manifest.live_research_target()
    persistence: SQLitePersistence | None = None
    full_evidence: EvidenceData | None = None
    session_evidence: EvidenceData | None = None
    database_status: Mapping[str, Any] = {}
    backup_status: Mapping[str, Any] | None = None
    stop_event = threading.Event()
    collector: Collector | None = None
    previous_handlers: dict[int, Any] = {}
    try:
        persistence = SQLitePersistence(
            settings.state_path,
            profile=PersistenceProfile.LIVE_RESEARCH,
            repository_root=REPOSITORY_ROOT,
            backup_dir=settings.backup_path,
            raw_evidence_dir=settings.raw_evidence_path,
            collection_context=LIVE_RESEARCH_COLLECTION_CONTEXT,
        )
        persistence.materialize_manifest(manifest)
        preflight = persistence.preflight_storage()
        if not bool(preflight.get("ready")):
            raise RunnerBlocked("LIVE_RESEARCH storage preflight did not return ready")
        session["storage_preflight"] = preflight
        _validate_current_backup(settings.backup_path or settings.state_path.parent / "backups")
        quota = QuotaGuard(persistence, reservation_scope="LIVE_RESEARCH")
        collector = Collector(
            manifest=manifest,
            persistence=persistence,
            mdd_client=LiveMddClient(settings.endpoint, enabled=True),
            quota_guard=quota,
            safety_margin_seconds=settings.safety_margin_seconds,
            shutdown_event=stop_event,
        )

        def request_stop(signum: int, _frame: Any) -> None:
            session["graceful_stop_requested"] = True
            session["stop_signal"] = signal.Signals(signum).name
            stop_event.set()
            if collector is not None:
                collector.request_shutdown()

        for signal_name in ("SIGINT", "SIGTERM"):
            signal_number = getattr(signal, signal_name, None)
            if signal_number is None:
                continue
            try:
                previous_handlers[signal_number] = signal.getsignal(signal_number)
                signal.signal(signal_number, request_stop)
            except ValueError as exc:
                raise RunnerBlocked("the foreground runner must install signal handlers in its main thread") from exc

        session["completion_state"] = "RUNNING"
        _write_json_atomic(session_metadata_path, session)
        attempted_buckets: set[int] = set()
        fatal_reason: str | None = None
        while not stop_event.is_set():
            now = _utc_now()
            if now >= ended:
                session["completion_state"] = "COMPLETED_DURATION"
                break
            current_bucket = bucket_for(now)
            if current_bucket.index in attempted_buckets:
                if _wait_until(stop_event=stop_event, end_at=min(current_bucket.end_utc, ended)):
                    break
                continue
            attempted_buckets.add(current_bucket.index)
            heartbeat_started = now
            run_id = f"{session_id}-{uuid.uuid4().hex}"
            result = collector.run_once(
                mode="live-research",
                allow_live=True,
                pilot_target=LIVE_RESEARCH_TARGET_ID,
                run_id=run_id,
                at_utc=heartbeat_started,
            )
            heartbeat_completed = _utc_now()
            session["observed_collector_runs"].append(
                _result_record(result, started_at=heartbeat_started, completed_at=heartbeat_completed)
            )
            if session.get("actual_first_heartbeat_utc") is None:
                session["actual_first_heartbeat_utc"] = _iso(heartbeat_started)
            session["actual_last_heartbeat_utc"] = _iso(heartbeat_completed)
            fatal_reason = _fatal_result_reason(result)
            if fatal_reason is not None:
                session["fatal_stop_reason"] = fatal_reason
            _write_json_atomic(session_metadata_path, session)
            print(
                json.dumps(
                    {
                        "event": "heartbeat_complete",
                        "run_id": run_id,
                        "logical_due_bucket": current_bucket.index,
                        "status": result.status,
                        "http_attempt_count": result.http_attempt_count,
                        "live_network_calls": result.live_network_calls,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if fatal_reason is not None or result.status == "INTERRUPTED" or stop_event.is_set():
                break
            if _wait_until(stop_event=stop_event, end_at=min(current_bucket.end_utc, ended)):
                break
        if session.get("fatal_stop_reason"):
            session["completion_state"] = "STOPPED_FATAL"
        elif session.get("graceful_stop_requested"):
            session["completion_state"] = "INTERRUPTED"
        elif session.get("completion_state") == "RUNNING":
            session["completion_state"] = "COMPLETED_DURATION"
    except (LiveResearchModeBlocked, LiveResearchSafetyError, RunnerBlocked, OSError, ValueError) as exc:
        session["fatal_stop_reason"] = type(exc).__name__
        session["completion_state"] = "NOT_STARTED" if not session["observed_collector_runs"] else "STOPPED_FATAL"
        session["error"] = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        for signal_number, handler in previous_handlers.items():
            try:
                signal.signal(signal_number, handler)
            except (ValueError, OSError):
                pass
        if persistence is not None:
            try:
                session["final_maintenance"] = persistence.maintenance_summary(now_utc=_utc_now())
            except (OSError, sqlite3.Error, LiveResearchSafetyError) as exc:
                session["final_maintenance"] = {
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "reason": "final live-research maintenance failed",
                }
            try:
                persistence.close()
            except Exception:
                pass
        _write_json_atomic(session_metadata_path, session)

    try:
        full_evidence = SQLiteEvidenceAdapter(settings.state_path).read_evidence()
        run_ids = {
            str(row.get("run_id"))
            for row in session.get("observed_collector_runs", [])
            if row.get("run_id") is not None
        }
        session_evidence = _filter_session_evidence(full_evidence, run_ids)
        database_status = _read_only_sqlite_status(settings.state_path)
        final_backup_path = None
        final_maintenance = session.get("final_maintenance")
        if isinstance(final_maintenance, Mapping):
            backup = final_maintenance.get("backup")
            if isinstance(backup, Mapping):
                final_backup_path = backup.get("backup_path")
        backup_status = (
            _read_only_sqlite_status(Path(str(final_backup_path)))
            if final_backup_path
            else _validate_current_backup(settings.backup_path or settings.state_path.parent / "backups")
        )
        report = build_final_report(
            session=session,
            manifest=manifest,
            repository_root=REPOSITORY_ROOT,
            full_evidence=full_evidence,
            session_evidence=session_evidence,
            database_status=database_status,
            backup_status=backup_status,
        )
    except (OSError, sqlite3.Error, ValueError, RunnerBlocked) as exc:
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "FAIL",
            "session": {
                "session_id": session.get("session_id"),
                "report_paths": session.get("report_paths"),
            },
            "error": {"type": type(exc).__name__, "reason": str(exc)},
            "scope_confirmation": {
                "postgresql_activity": False,
                "production_live_activity": False,
                "tiered50_production_activation": False,
                "production_cutover_timestamp_created": False,
                "post_cutover_validation_started": False,
                "power_bi_activity": False,
            },
        }
    _write_json_atomic(report_json_path, report)
    summary_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    summary_path.write_text(_markdown_summary(report), encoding="utf-8")
    _owner_only_file(summary_path)
    session["report_status"] = report.get("status")
    _write_json_atomic(session_metadata_path, session)
    return {
        "status": report.get("status"),
        "session_id": session_id,
        "session_metadata": str(session_metadata_path),
        "machine_report": str(report_json_path),
        "markdown_summary": str(summary_path),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_bounded_collection(args)
    except (RunnerBlocked, LiveResearchSafetyError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_type": type(exc).__name__,
                    "reason": str(exc),
                    "live_network_calls": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"COMPLETE", "INTERRUPTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

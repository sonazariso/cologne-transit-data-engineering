"""Read-only proofing and explicit SQLite-to-SQL Server import support.

The live-research SQLite file is an historical evidence source, not a runtime
database for the Mac collector.  This module intentionally requires a caller
to provide the source path and keeps the default command mode dry-run.  The
SQL Server side uses generic import lineage tables so a historical row is not
mistaken for a native Mac production observation.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

from .domain import (
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    LIVE_RESEARCH_LEGACY_ID,
    LIVE_RESEARCH_STATION_NAME,
    LIVE_RESEARCH_TARGET_ID,
)
from .mssql_persistence import (
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from .mssql_persistence import build_mssql_snapshot_rows


IMPORTER_VERSION = "sqlite-live-research-import-1.0"
EXPECTED_SQL_DATABASE = "CologneTransitIntelligence"
EXPECTED_SQLITE_SCHEMA_VERSION = 5
MIN_SESSION_SECONDS = 4 * 60 * 60
MAX_SESSION_SECONDS = 7 * 60 * 60
CONSERVATIVE_IMPORT_MULTIPLIER = 2
MIN_PHYSICAL_FREE_PERCENT = 5.0
MIN_PHYSICAL_FREE_RESERVE_BYTES = 1024 * 1024 * 1024


class SQLiteImportError(RuntimeError):
    """The source cannot be proven safe or the import cannot reconcile."""


@dataclass(frozen=True)
class ResearchSession:
    """The contiguous completed approximately six-hour source run."""

    run_ids: tuple[str, ...]
    start_utc: str
    end_utc: str
    duration_seconds: float
    logical_bucket_count: int
    collector_run_count: int
    target_audit_count: int
    http_attempt_count: int
    successful_run_count: int
    failed_run_count: int
    skipped_run_count: int
    parsed_snapshot_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_ids": list(self.run_ids),
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "duration_seconds": self.duration_seconds,
            "duration_hours": self.duration_seconds / 3600.0,
            "logical_bucket_count": self.logical_bucket_count,
            "collector_run_count": self.collector_run_count,
            "target_audit_count": self.target_audit_count,
            "http_attempt_count": self.http_attempt_count,
            "successful_run_count": self.successful_run_count,
            "failed_run_count": self.failed_run_count,
            "skipped_run_count": self.skipped_run_count,
            "parsed_snapshot_count": self.parsed_snapshot_count,
        }


@dataclass(frozen=True)
class SourceTableInventory:
    table_name: str
    row_count: int
    classification: str
    explicit_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "row_count": self.row_count,
            "classification": self.classification,
            "explicit_reason": self.explicit_reason,
        }


@dataclass(frozen=True)
class SQLiteSourceProof:
    path: str
    byte_size: int
    sha256: str
    integrity_check: str
    quick_check: str
    pragma_schema_version: int
    application_schema_version: int
    database_role: str
    collection_context: str
    target_identity: dict[str, Any]
    session: ResearchSession
    table_names: tuple[str, ...]
    source_counts: Mapping[str, int]
    session_counts: Mapping[str, int]
    run_status_counts: Mapping[str, int]
    audit_status_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "integrity_check": self.integrity_check,
            "quick_check": self.quick_check,
            "pragma_schema_version": self.pragma_schema_version,
            "application_schema_version": self.application_schema_version,
            "database_role": self.database_role,
            "collection_context": self.collection_context,
            "target_identity": dict(self.target_identity),
            "session": self.session.to_dict(),
            "table_names": list(self.table_names),
            "source_counts": dict(self.source_counts),
            "session_counts": dict(self.session_counts),
            "run_status_counts": dict(self.run_status_counts),
            "audit_status_counts": dict(self.audit_status_counts),
        }


@dataclass
class ImportCounters:
    source_rows: int = 0
    eligible_rows: int = 0
    inserted_rows: int = 0
    duplicate_rows: int = 0
    overlap_rows: int = 0
    lineage_only_rows: int = 0

    @property
    def unexplained_difference(self) -> int:
        return self.eligible_rows - self.inserted_rows - self.duplicate_rows - self.overlap_rows

    def to_dict(self) -> dict[str, int]:
        return {
            "source_rows": self.source_rows,
            "eligible_rows": self.eligible_rows,
            "inserted_rows": self.inserted_rows,
            "duplicate_rows": self.duplicate_rows,
            "overlap_rows": self.overlap_rows,
            "lineage_only_rows": self.lineage_only_rows,
            "unexplained_difference": self.unexplained_difference,
        }


FULL_IMPORT_TABLES = frozenset(
    {
        "collector_runs",
        "dispatcher_claims",
        "dispatcher_claim_events",
        "target_claims",
        "claim_events",
        "target_audits",
        "http_attempts",
        "quota_permits",
        "quota_audit",
        "parsed_snapshots",
        "timing_observations",
        "recorded_at_time_evidence",
        "service_section_observations",
    }
)

LINEAGE_ONLY_TABLE_REASONS = {
    "database_metadata": "Copied into import-batch metadata; SQLite metadata is not a Mac runtime table.",
    "manifest_targets": "Historical research manifest is retained as lineage; it is not the authoritative seven-target SQL catalog.",
    "sampling_eras": "Historical table is lineage-only; no active Tiered50 era may be created by import.",
    "shared_usage": "Point-in-time SQLite quota state has no equivalent authoritative runtime row; exact source row is retained in import lineage.",
    "raw_evidence": "Raw forensic bodies remain outside SQL Server; only the durable SQLite metadata index is retained as lineage.",
    "sqlite_sequence": "SQLite internal autoincrement bookkeeping is not source evidence and is retained only as an explicit inventory item.",
    "timing_recorded_at_time_reporting": "Derived SQLite reporting view; source timing rows are imported instead of materializing a second derived copy.",
    "recorded_at_time_reporting": "Compatibility alias view; preserved in inventory only because it is not a durable source table.",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", identifier):
        raise SQLiteImportError(f"unsafe SQLite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(path.as_posix(), safe='/')}?mode=ro"


def _parse_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("[GMT]"):
        text = text[:-5]
    text = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: Any) -> str | None:
    parsed = _parse_utc(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(row)


def _table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    )


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}").fetchone()[0])


def _iter_rows(connection: sqlite3.Connection, table_name: str) -> Iterable[dict[str, Any]]:
    cursor = connection.execute(f"SELECT * FROM {_quote_identifier(table_name)} ORDER BY rowid")
    for row in cursor:
        yield _row_dict(row)


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _classify_table(table_name: str) -> tuple[str, str | None]:
    if table_name in FULL_IMPORT_TABLES:
        return "FULL_IMPORT", None
    if table_name in LINEAGE_ONLY_TABLE_REASONS:
        return "LINEAGE_ONLY", LINEAGE_ONLY_TABLE_REASONS[table_name]
    return "NOT_IMPORTED_WITH_EXPLICIT_REASON", "Unknown source table; importer refuses until its evidence role is explicitly classified."


def _session_groups(runs: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    candidates = [
        dict(row)
        for row in runs
        if str(row.get("mode")) == "live-research"
        and str(row.get("collection_context")) == LIVE_RESEARCH_COLLECTION_CONTEXT
        and str(row.get("run_id", "")).startswith("live-research-")
    ]
    candidates.sort(key=lambda row: (int(row["logical_due_bucket"]), str(row["started_at_utc"])))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_bucket: int | None = None
    for row in candidates:
        bucket = int(row["logical_due_bucket"])
        if current and previous_bucket is not None and bucket > previous_bucket + 1:
            groups.append(current)
            current = []
        current.append(row)
        previous_bucket = bucket
    if current:
        groups.append(current)
    return groups


def _identify_session(connection: sqlite3.Connection) -> ResearchSession:
    rows = [_row_dict(row) for row in connection.execute("SELECT * FROM collector_runs ORDER BY started_at_utc")]
    valid: list[list[dict[str, Any]]] = []
    for group in _session_groups(rows):
        start = min(_parse_utc(row["started_at_utc"]) for row in group)
        end = max(_parse_utc(row["completed_at_utc"]) or _parse_utc(row["started_at_utc"]) for row in group)
        duration = (end - start).total_seconds()
        if MIN_SESSION_SECONDS <= duration <= MAX_SESSION_SECONDS and len(group) >= 50:
            valid.append(group)
    if len(valid) != 1:
        spans = []
        for group in valid:
            spans.append(
                {
                    "start": min(row["started_at_utc"] for row in group),
                    "end": max(row["completed_at_utc"] for row in group),
                    "run_count": len(group),
                }
            )
        raise SQLiteImportError(
            "could not identify exactly one completed approximately six-hour LIVE_RESEARCH session; "
            + _canonical_json(spans)
        )
    group = valid[0]
    run_ids = tuple(str(row["run_id"]) for row in group)
    start = min(_parse_utc(row["started_at_utc"]) for row in group)
    end = max(_parse_utc(row["completed_at_utc"]) or _parse_utc(row["started_at_utc"]) for row in group)
    placeholders = ",".join("?" for _ in run_ids)
    audits = [
        _row_dict(row)
        for row in connection.execute(
            f"SELECT * FROM target_audits WHERE collector_run_id IN ({placeholders})", run_ids
        )
    ]
    audit_ids = tuple(int(row["audit_id"]) for row in audits)
    if audit_ids:
        audit_placeholders = ",".join("?" for _ in audit_ids)
        attempts = int(
            connection.execute(
                f"SELECT COUNT(*) FROM http_attempts WHERE audit_id IN ({audit_placeholders})", audit_ids
            ).fetchone()[0]
        )
        snapshots = int(
            connection.execute(
                f"SELECT COUNT(*) FROM parsed_snapshots WHERE audit_id IN ({audit_placeholders})", audit_ids
            ).fetchone()[0]
        )
    else:
        attempts = 0
        snapshots = 0
    status_counts = Counter(str(row.get("status")) for row in audits)
    return ResearchSession(
        run_ids=run_ids,
        start_utc=start.isoformat().replace("+00:00", "Z"),
        end_utc=end.isoformat().replace("+00:00", "Z"),
        duration_seconds=(end - start).total_seconds(),
        logical_bucket_count=len({int(row["logical_due_bucket"]) for row in group}),
        collector_run_count=len(group),
        target_audit_count=len(audits),
        http_attempt_count=attempts,
        successful_run_count=status_counts.get("SUCCEEDED", 0),
        failed_run_count=status_counts.get("FAILED", 0),
        skipped_run_count=status_counts.get("SKIPPED", 0),
        parsed_snapshot_count=snapshots,
    )


def _session_counts(
    connection: sqlite3.Connection,
    session: ResearchSession,
    source_counts: Mapping[str, int],
) -> dict[str, int]:
    run_ids = session.run_ids
    placeholders = ",".join("?" for _ in run_ids)
    counts: dict[str, int] = {
        "collector_runs": session.collector_run_count,
        "logical_buckets": session.logical_bucket_count,
        "target_audits": session.target_audit_count,
        "http_attempts": session.http_attempt_count,
        "successful_runs": session.successful_run_count,
        "failed_runs": session.failed_run_count,
        "skipped_runs": session.skipped_run_count,
        "parsed_snapshots": session.parsed_snapshot_count,
    }
    for table in ("dispatcher_claims", "dispatcher_claim_events", "target_claims", "claim_events", "quota_audit", "raw_evidence"):
        if table in source_counts:
            column = "collector_run_id" if table not in {"dispatcher_claim_events", "claim_events"} else "collector_run_id"
            if table == "quota_audit":
                column = "collector_run_id"
            counts[table] = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)} WHERE {_quote_identifier(column)} IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
            )
    return counts


def _validate_target_identity(connection: sqlite3.Connection) -> dict[str, Any]:
    target_rows = [
        _row_dict(row)
        for row in connection.execute(
            """
            SELECT source_parent_station_id, station_name, legacy_sampling_target_id,
                   panel_version
            FROM manifest_targets
            WHERE source_parent_station_id = ?
            """,
            (LIVE_RESEARCH_TARGET_ID,),
        )
    ]
    if not any(
        row.get("station_name") == LIVE_RESEARCH_STATION_NAME
        and int(row.get("legacy_sampling_target_id") or 0) == LIVE_RESEARCH_LEGACY_ID
        for row in target_rows
    ):
        raise SQLiteImportError("source manifest does not prove the Köln Heumarkt legacy target identity")
    for table in ("collector_runs", "target_audits", "parsed_snapshots", "timing_observations", "service_section_observations"):
        if table not in _table_names(connection):
            continue
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")}
        if "collection_context" in columns:
            contexts = {
                str(row[0])
                for row in connection.execute(
                    f"SELECT DISTINCT collection_context FROM {_quote_identifier(table)}"
                )
                if row[0] is not None
            }
            if contexts != {LIVE_RESEARCH_COLLECTION_CONTEXT}:
                raise SQLiteImportError(f"{table} contains unexpected collection context(s): {sorted(contexts)}")
    audit_targets = {
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT DISTINCT source_parent_station_id, station_name, collection_context FROM target_audits"
        )
    }
    if audit_targets != {(LIVE_RESEARCH_TARGET_ID, LIVE_RESEARCH_STATION_NAME, LIVE_RESEARCH_COLLECTION_CONTEXT)}:
        raise SQLiteImportError(f"target audit identity is not the single expected Heumarkt target: {sorted(audit_targets)}")
    return {
        "source_parent_station_id": LIVE_RESEARCH_TARGET_ID,
        "station_name": LIVE_RESEARCH_STATION_NAME,
        "legacy_sampling_target_id": LIVE_RESEARCH_LEGACY_ID,
        "manifest_rows": target_rows,
    }


def inspect_sqlite_source(path: str | Path) -> tuple[SQLiteSourceProof, tuple[SourceTableInventory, ...]]:
    """Prove one explicitly supplied SQLite file without modifying it."""

    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise SQLiteImportError(f"explicit SQLite source does not exist: {source_path}")
    byte_size = source_path.stat().st_size
    sha256 = _source_sha256(source_path)
    connection = sqlite3.connect(_sqlite_uri(source_path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if integrity.casefold() != "ok" or quick.casefold() != "ok":
            raise SQLiteImportError(
                f"SQLite integrity validation failed: integrity_check={integrity!r}, quick_check={quick!r}"
            )
        table_names = _table_names(connection)
        # The reporting aliases are SQLite views, not durable evidence tables.
        # Inventory and import requirements are deliberately limited to the
        # durable tables returned by _table_names().
        required = set(FULL_IMPORT_TABLES) | {
            table_name
            for table_name in LINEAGE_ONLY_TABLE_REASONS
            if table_name not in {"timing_recorded_at_time_reporting", "recorded_at_time_reporting"}
        } | {"database_metadata"}
        missing = sorted(required - set(table_names))
        if missing:
            raise SQLiteImportError("required SQLite source tables are missing: " + ", ".join(missing))
        metadata = {
            str(row["metadata_key"]): str(row["metadata_value"])
            for row in connection.execute("SELECT metadata_key, metadata_value FROM database_metadata")
        }
        try:
            app_schema = int(metadata.get("schema_version", ""))
        except ValueError as exc:
            raise SQLiteImportError("SQLite application schema version is not numeric") from exc
        if app_schema != EXPECTED_SQLITE_SCHEMA_VERSION:
            raise SQLiteImportError(
                f"SQLite application schema {app_schema} is not the supported source schema {EXPECTED_SQLITE_SCHEMA_VERSION}"
            )
        if metadata.get("database_role") != "LIVE_RESEARCH":
            raise SQLiteImportError("SQLite database_role is not LIVE_RESEARCH")
        if metadata.get("collection_context") != LIVE_RESEARCH_COLLECTION_CONTEXT:
            raise SQLiteImportError("SQLite collection_context is not LIVE_RESEARCH_SINGLE_1")
        target_identity = _validate_target_identity(connection)
        source_counts = {table: _table_count(connection, table) for table in table_names}
        runs = [_row_dict(row) for row in connection.execute("SELECT status, COUNT(*) AS n FROM collector_runs GROUP BY status")]
        audits = [_row_dict(row) for row in connection.execute("SELECT status, COUNT(*) AS n FROM target_audits GROUP BY status")]
        session = _identify_session(connection)
        session_counts = _session_counts(connection, session, source_counts)
        proof = SQLiteSourceProof(
            path=str(source_path),
            byte_size=byte_size,
            sha256=sha256,
            integrity_check=integrity,
            quick_check=quick,
            pragma_schema_version=int(connection.execute("PRAGMA schema_version").fetchone()[0]),
            application_schema_version=app_schema,
            database_role=metadata["database_role"],
            collection_context=metadata["collection_context"],
            target_identity=target_identity,
            session=session,
            table_names=table_names,
            source_counts=source_counts,
            session_counts=session_counts,
            run_status_counts={str(row["status"]): int(row["n"]) for row in runs},
            audit_status_counts={str(row["status"]): int(row["n"]) for row in audits},
        )
        inventory = tuple(
            SourceTableInventory(
                table_name=table,
                row_count=source_counts[table],
                classification=_classify_table(table)[0],
                explicit_reason=_classify_table(table)[1],
            )
            for table in table_names
        )
        unknown = [row.table_name for row in inventory if row.classification == "NOT_IMPORTED_WITH_EXPLICIT_REASON"]
        if unknown:
            raise SQLiteImportError("SQLite source contains unclassified tables: " + ", ".join(unknown))
        return proof, inventory
    finally:
        connection.close()


def _sanitize_source_path(path: Path) -> str:
    parts = list(path.parts)
    if "live-research" in parts:
        return "<local>/live-research/" + "/".join(parts[parts.index("live-research") + 1 :])
    return "<local>/" + path.name


def _sanitize_lineage_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize_lineage_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize_lineage_value(child) for child in value]
    if isinstance(value, str) and ("/raw-evidence/" in value or value.endswith("collector.sqlite3")):
        return _sanitize_source_path(Path(value))
    return value


def _source_row_json(row: Mapping[str, Any], *, sanitize_paths: bool = False) -> str:
    value: Any = dict(row)
    if sanitize_paths:
        value = _sanitize_lineage_value(value)
    return _canonical_json(value)


def _source_key(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return _canonical_json(value)
    return str(value)


def _identity_hash(source_sha256: str, source_table: str, source_key: str) -> str:
    return hashlib.sha256(f"{source_sha256}|{source_table}|{source_key}".encode("utf-8")).hexdigest()


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _historical_run_id(source_sha256: str, source_run_id: str) -> str:
    readable = f"LR_{source_sha256[:10]}_{source_run_id}"
    if len(readable) <= 100:
        return readable
    return f"LR_{source_sha256[:10]}_{hashlib.sha256(source_run_id.encode()).hexdigest()[:32]}"


def _child_key(snapshot_id: int, *parts: Any) -> str:
    return _source_key([int(snapshot_id), *parts])


def _drain_cursor(cursor: Any) -> None:
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        try:
            fetchall()
        except Exception:
            pass
    nextset = getattr(cursor, "nextset", None)
    if callable(nextset):
        try:
            while nextset():
                try:
                    cursor.fetchall()
                except Exception:
                    pass
        except Exception:
            pass


def _result_snapshot_id(cursor: Any) -> int | None:
    row = cursor.fetchone()
    value = _row_value(row, "SnapshotId", 0)
    if value is None:
        return None
    return int(value)


def _json_object(value: Any) -> Any:
    """Decode an audit JSON field without allowing malformed history to hide."""

    if value is None or value == "":
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return {"_unparsed": str(value)}


def _capacity_file_row(row: Any, *, file_type: str) -> dict[str, Any]:
    """Normalize one SQL Server file/volume metadata row for reports."""

    allocated = int(_row_value(row, "allocated_bytes", 0) or 0)
    space_used_pages = _row_value(row, "space_used_pages", 1)
    space_used = None
    if space_used_pages is not None:
        space_used = max(0, int(space_used_pages)) * 8192
    volume_total = int(_row_value(row, "total_bytes", 8) or 0)
    volume_free = int(_row_value(row, "available_bytes", 9) or 0)
    growth = int(_row_value(row, "growth", 6) or 0)
    percent_growth = bool(_row_value(row, "is_percent_growth", 7))
    max_size_bytes = _row_value(row, "max_size_bytes", 5)
    if max_size_bytes is not None:
        max_size_bytes = int(max_size_bytes)
    return {
        "logical_file_name": str(_row_value(row, "logical_name", 4) or ""),
        "physical_path": str(_row_value(row, "physical_name", 2) or ""),
        "file_type": file_type,
        "allocated_bytes": allocated,
        "allocated_mb": allocated / (1024 * 1024),
        "space_used_bytes": space_used,
        "space_used_mb": None if space_used is None else space_used / (1024 * 1024),
        "internal_free_bytes": None if space_used is None else max(0, allocated - space_used),
        "internal_free_mb": (
            None
            if space_used is None
            else max(0, allocated - space_used) / (1024 * 1024)
        ),
        "growth": growth,
        "is_percent_growth": percent_growth,
        "growth_setting": f"{growth}%" if percent_growth else f"{growth * 8 / 1024:.6g} MB",
        "growth_bytes": None if percent_growth else growth * 8192,
        "max_size_bytes": max_size_bytes,
        "max_size": "UNLIMITED" if max_size_bytes is None else max_size_bytes,
        "volume_name": str(_row_value(row, "volume_mount_point", 3) or ""),
        "volume_total_bytes": volume_total,
        "volume_free_bytes": volume_free,
        "volume_free_percent": (volume_free / volume_total * 100.0) if volume_total else None,
    }


def _capacity_report(
    *,
    proof: SQLiteSourceProof,
    rows_files: Sequence[Mapping[str, Any]],
    log_files: Sequence[Mapping[str, Any]],
    log_usage: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the read-only SQL Server capacity model.

    Only ROWS internal free space can satisfy the import requirement.  Physical
    free space is evaluated independently as the guardrail for any required
    data-file autogrowth.  LOG reusable space is reported but deliberately
    excluded from all ROWS calculations.
    """

    all_files = [*rows_files, *log_files]
    volume_map: dict[str, dict[str, Any]] = {}
    for file_row in all_files:
        volume = str(file_row.get("volume_name") or "")
        if not volume:
            continue
        current = volume_map.setdefault(
            volume,
            {
                "volume_name": volume,
                "total_bytes": int(file_row.get("volume_total_bytes") or 0),
                "free_bytes": int(file_row.get("volume_free_bytes") or 0),
                "file_types": set(),
            },
        )
        current["total_bytes"] = min(
            int(current["total_bytes"] or 0), int(file_row.get("volume_total_bytes") or 0)
        ) if current["total_bytes"] else int(file_row.get("volume_total_bytes") or 0)
        current["free_bytes"] = min(
            int(current["free_bytes"] or 0), int(file_row.get("volume_free_bytes") or 0)
        ) if current["free_bytes"] else int(file_row.get("volume_free_bytes") or 0)
        current["file_types"].add(str(file_row.get("file_type") or ""))

    volumes: list[dict[str, Any]] = []
    for value in sorted(volume_map.values(), key=lambda item: str(item["volume_name"])):
        total = int(value["total_bytes"])
        free = int(value["free_bytes"])
        reserve = max(MIN_PHYSICAL_FREE_RESERVE_BYTES, int(total * 0.05))
        volumes.append(
            {
                "volume_name": value["volume_name"],
                "total_bytes": total,
                "free_bytes": free,
                "free_percent": (free / total * 100.0) if total else None,
                "required_safety_reserve_bytes": reserve,
                "file_types": sorted(value["file_types"]),
            }
        )

    source_size = int(proof.byte_size)
    conservative_requirement = source_size * CONSERVATIVE_IMPORT_MULTIPLIER
    rows_internal_free = sum(int(row.get("internal_free_bytes") or 0) for row in rows_files)
    expected_physical_growth = max(0, conservative_requirement - rows_internal_free)

    rows_volumes = [volume for volume in volumes if "ROWS" in volume["file_types"]]
    projected_volumes: list[dict[str, Any]] = []
    growth_remaining = expected_physical_growth
    # SQL Server may have multiple ROWS volumes.  The importer does not control
    # file-growth allocation, so the report applies the conservative amount to
    # the least-free ROWS volume and leaves the others unchanged.
    for volume in sorted(rows_volumes, key=lambda item: (item["free_bytes"], item["volume_name"])):
        growth = min(growth_remaining, int(volume["free_bytes"]))
        growth_remaining -= growth
        projected_free = int(volume["free_bytes"]) - growth
        projected = {
            **volume,
            "projected_growth_bytes": growth,
            "projected_free_bytes": projected_free,
            "projected_free_percent": (
                projected_free / int(volume["total_bytes"]) * 100.0
                if int(volume["total_bytes"])
                else None
            ),
            "projected_safety_reserve_bytes": int(volume["required_safety_reserve_bytes"]),
        }
        projected_volumes.append(projected)

    projected_volume_names = {item["volume_name"] for item in projected_volumes}
    for volume in volumes:
        if volume["volume_name"] not in projected_volume_names:
            projected_volumes.append(
                {
                    **volume,
                    "projected_growth_bytes": 0,
                    "projected_free_bytes": int(volume["free_bytes"]),
                    "projected_free_percent": volume["free_percent"],
                    "projected_safety_reserve_bytes": int(volume["required_safety_reserve_bytes"]),
                }
            )

    volume_failures = []
    for volume in projected_volumes:
        if int(volume["free_bytes"]) < int(volume["required_safety_reserve_bytes"]):
            volume_failures.append(
                {
                    "volume_name": volume["volume_name"],
                    "reason": "CURRENT_PHYSICAL_FREE_SPACE_BELOW_SAFETY_RESERVE",
                    "additional_free_bytes_required": int(volume["required_safety_reserve_bytes"])
                    - int(volume["free_bytes"]),
                }
            )
        if int(volume["projected_free_bytes"]) < int(volume["projected_safety_reserve_bytes"]):
            volume_failures.append(
                {
                    "volume_name": volume["volume_name"],
                    "reason": "PROJECTED_PHYSICAL_FREE_SPACE_BELOW_SAFETY_RESERVE",
                    "additional_free_bytes_required": int(volume["projected_safety_reserve_bytes"])
                    - int(volume["projected_free_bytes"]),
                }
            )
        projected_percent = volume["projected_free_percent"]
        if projected_percent is not None and projected_percent < MIN_PHYSICAL_FREE_PERCENT:
            volume_failures.append(
                {
                    "volume_name": volume["volume_name"],
                    "reason": "PROJECTED_PHYSICAL_FREE_PERCENT_BELOW_SAFETY_THRESHOLD",
                    "projected_free_percent": projected_percent,
                    "required_free_percent": MIN_PHYSICAL_FREE_PERCENT,
                }
            )

    row_physical_free = min((int(item["free_bytes"]) for item in rows_volumes), default=0)
    row_physical_total = min((int(item["total_bytes"]) for item in rows_volumes), default=0)
    physical_reserve_after = min(
        (int(item["projected_free_bytes"])
         for item in projected_volumes),
        default=0,
    )
    physical_reserve_after_percent = min(
        (float(item["projected_free_percent"])
         for item in projected_volumes
         if item["projected_free_percent"] is not None),
        default=0.0,
    )
    physical_shortfall = max(
        [
            int(item["projected_safety_reserve_bytes"]) - int(item["projected_free_bytes"])
            for item in projected_volumes
        ]
        or [0]
    )
    rows_shortfall = max(0, expected_physical_growth - row_physical_free)
    additional_free_required = max(rows_shortfall, physical_shortfall)
    log_allocated = sum(int(row.get("allocated_bytes") or 0) for row in log_files)
    log_used = int(log_usage.get("used_log_space_in_bytes") or 0)
    log_reusable = max(0, log_allocated - log_used)
    log_used_percent = float(log_usage.get("used_log_space_in_percent") or 0.0)
    status = (
        "PASS"
        if rows_files
        and log_files
        and not volume_failures
        and growth_remaining == 0
        and rows_internal_free + row_physical_free >= conservative_requirement
        and physical_reserve_after_percent >= MIN_PHYSICAL_FREE_PERCENT
        else "FAIL"
    )
    return {
        "status": status,
        "windows_volumes": volumes,
        "projected_volumes": sorted(projected_volumes, key=lambda item: str(item["volume_name"])),
        "rows_files": list(rows_files),
        "log_files": list(log_files),
        "log_usage": {
            "allocated_bytes": log_allocated,
            "allocated_mb": log_allocated / (1024 * 1024),
            "used_bytes": log_used,
            "used_mb": log_used / (1024 * 1024),
            "used_percent": log_used_percent,
            "reusable_internal_free_bytes": log_reusable,
            "reusable_internal_free_mb": log_reusable / (1024 * 1024),
            "recovery_model": log_usage.get("recovery_model_desc"),
            "log_reuse_wait_desc": log_usage.get("log_reuse_wait_desc"),
        },
        "import_capacity": {
            "source_byte_size": source_size,
            "conservative_multiplier": CONSERVATIVE_IMPORT_MULTIPLIER,
            "conservative_import_requirement_bytes": conservative_requirement,
            "projected_import_storage_requirement_bytes": conservative_requirement,
            "rows_internal_free_capacity_bytes": rows_internal_free,
            "rows_internal_free_capacity_mb": rows_internal_free / (1024 * 1024),
            "expected_physical_growth_requirement_bytes": expected_physical_growth,
            "expected_physical_growth_requirement_mb": expected_physical_growth / (1024 * 1024),
            "rows_volume_free_bytes": row_physical_free,
            "rows_volume_total_bytes": row_physical_total,
            "physical_os_reserve_after_projected_import_bytes": physical_reserve_after,
            "physical_os_reserve_after_projected_import_mb": physical_reserve_after / (1024 * 1024),
            "physical_os_reserve_after_projected_import_percent": physical_reserve_after_percent,
            "minimum_required_os_reserve_bytes": min(
                (int(item["required_safety_reserve_bytes"]) for item in projected_volumes),
                default=0,
            ),
            "additional_free_bytes_required": additional_free_required,
            "capacity_blockers": volume_failures
            + ([{"reason": "ROWS_PHYSICAL_FREE_SPACE_INSUFFICIENT", "additional_free_bytes_required": rows_shortfall}]
               if rows_shortfall else [])
            + ([{"reason": "ROWS_VOLUME_NOT_FOUND"}] if not rows_files else [])
            + ([{"reason": "LOG_FILE_NOT_FOUND"}] if not log_files else []),
        },
        "volume_safety": {
            "minimum_allowed_free_percent": MIN_PHYSICAL_FREE_PERCENT,
            "minimum_allowed_free_reserve_bytes": MIN_PHYSICAL_FREE_RESERVE_BYTES,
            "failures": volume_failures,
        },
        "log_space_excluded_from_rows_capacity": True,
    }


class SQLiteToMssqlImporter:
    """Explicit importer whose default is never apply."""

    def __init__(
        self,
        source_path: str | Path,
        settings: MssqlConnectionSettings | None = None,
        *,
        apply: bool = False,
        connection_factory: Any | None = None,
    ) -> None:
        self.source_path = Path(source_path).expanduser().resolve()
        self.settings = settings
        self.apply = bool(apply)
        self.connection_factory = connection_factory
        self.source_connection: sqlite3.Connection | None = None
        self.connection: Any | None = None
        self.cursor: Any | None = None
        self.proof: SQLiteSourceProof | None = None
        self.inventory: tuple[SourceTableInventory, ...] = ()
        self.batch_id: str | None = None
        self.run_map: dict[str, str] = {}
        self.audit_map: dict[int, int] = {}
        self.snapshot_map: dict[int, int] = {}
        self.manifest_sampling_ids: dict[str, int | None] = {}
        self.table_counters: dict[str, ImportCounters] = {}
        self.evidence_counters: dict[str, ImportCounters] = {}
        self.lineage_cache: dict[tuple[str, str], Any | None] = {}
        self._lineage_cache_loaded = False
        self.timing_destination_cache: dict[int, dict[tuple[Any, ...], int]] = {}

    def _source_rows(self, table_name: str) -> Iterable[dict[str, Any]]:
        if self.source_connection is None:
            raise SQLiteImportError("source connection is not open")
        yield from _iter_rows(self.source_connection, table_name)

    def _counter(self, table_name: str, *, evidence: bool = False) -> ImportCounters:
        target = self.evidence_counters if evidence else self.table_counters
        if table_name not in target:
            target[table_name] = ImportCounters()
        return target[table_name]

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        if self.cursor is None:
            raise SQLiteImportError("SQL Server connection is not open")
        self.cursor.execute(sql, tuple(params))
        return self.cursor

    def _commit(self) -> None:
        if self.connection is not None:
            self.connection.commit()

    def _prime_lineage_cache(self) -> None:
        """Load this source's durable lineage once before row-level work."""

        rows = self._execute(
            """
            SELECT SourceTableName, SourceIdentityHash, RowStatus,
                   DestinationObjectName, DestinationKey
            FROM ctl.MddImportSourceRow
            WHERE SourceDatabaseSha256 = %s
            """,
            (self.proof.sha256,),  # type: ignore[union-attr]
        ).fetchall()
        for row in rows:
            if isinstance(row, Mapping):
                table_name = str(row.get("SourceTableName"))
                identity = str(row.get("SourceIdentityHash"))
            else:
                table_name = str(row[0])
                identity = str(row[1])
            self.lineage_cache[(table_name, identity)] = row
        self._lineage_cache_loaded = True

    def _existing_lineage(self, source_table: str, source_key: str) -> Any | None:
        identity = _identity_hash(self.proof.sha256, source_table, source_key)  # type: ignore[union-attr]
        cache_key = (source_table, identity)
        if cache_key in self.lineage_cache:
            return self.lineage_cache[cache_key]
        if self._lineage_cache_loaded:
            self.lineage_cache[cache_key] = None
            return None
        cursor = self._execute(
            """
            SELECT TOP (1) ImportSourceRowId, ImportBatchId, RowStatus,
                           DestinationObjectName, DestinationKey
            FROM ctl.MddImportSourceRow
            WHERE SourceDatabaseSha256 = %s
              AND SourceTableName = %s
              AND SourceIdentityHash = %s
            """,
            (self.proof.sha256, source_table, identity),  # type: ignore[union-attr]
        )
        row = cursor.fetchone()
        # connect_mssql requests as_dict=True, while test doubles and other
        # DB-API clients may return tuples.  Keep the native row shape because
        # _row_value() handles both forms and converting a tuple with dict()
        # would incorrectly require key/value pairs.
        self.lineage_cache[cache_key] = row
        return row

    def _record_lineage(
        self,
        *,
        source_table: str,
        source_key: str,
        classification: str,
        new_status: str,
        destination_object: str | None = None,
        destination_key: str | None = None,
        source_row: Mapping[str, Any] | None = None,
        overlap_classification: str | None = None,
        evidence: bool = False,
    ) -> tuple[str, str | None]:
        counter = self._counter(source_table, evidence=evidence)
        if evidence:
            # Evidence families are projections of the parsed source document
            # rather than SQLite tables.  Count every source occurrence in the
            # current import attempt, including an idempotent second attempt.
            counter.source_rows += 1
            counter.eligible_rows += 1
        existing = self._existing_lineage(source_table, source_key)
        if existing is not None:
            counter.duplicate_rows += 1
            return "DUPLICATE", _row_value(existing, "DestinationKey", 4)
        source_hash = _identity_hash(self.proof.sha256, source_table, source_key)  # type: ignore[union-attr]
        row_hash = (
            hashlib.sha256(_source_row_json(source_row).encode("utf-8")).hexdigest()
            if source_row is not None
            else None
        )
        raw_json = None
        if classification != "FULL_IMPORT" and source_row is not None:
            raw_json = _source_row_json(source_row, sanitize_paths=True)
        self._execute(
            """
            INSERT INTO ctl.MddImportSourceRow
                (ImportBatchId, SourceDatabaseSha256, SourceTableName,
                 SourcePrimaryKey, SourceIdentityHash, SourceRowHashSha256,
                 Classification, RowStatus, OverlapClassification,
                 DestinationObjectName, DestinationKey, SourceRowJson)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.batch_id,
                self.proof.sha256,  # type: ignore[union-attr]
                source_table,
                source_key,
                source_hash,
                row_hash,
                classification,
                new_status,
                overlap_classification,
                destination_object,
                destination_key,
                raw_json,
            ),
        )
        self.lineage_cache[(source_table, source_hash)] = {
            "SourceTableName": source_table,
            "SourceIdentityHash": source_hash,
            "RowStatus": new_status,
            "DestinationObjectName": destination_object,
            "DestinationKey": destination_key,
        }
        if new_status == "INSERTED":
            counter.inserted_rows += 1
        elif new_status == "OVERLAP":
            counter.overlap_rows += 1
        elif new_status == "LINEAGE_ONLY":
            counter.lineage_only_rows += 1
        elif new_status == "DUPLICATE":
            counter.duplicate_rows += 1
        return new_status, destination_key

    def _lineage_only_table(self, inventory: SourceTableInventory) -> None:
        counter = self._counter(inventory.table_name)
        counter.source_rows = inventory.row_count
        counter.eligible_rows = 0
        for row in self._source_rows(inventory.table_name):
            primary = self._lineage_primary_key(inventory.table_name, row)
            self._record_lineage(
                source_table=inventory.table_name,
                source_key=primary,
                classification=inventory.classification,
                new_status="LINEAGE_ONLY",
                source_row=row,
            )

    @staticmethod
    def _lineage_primary_key(table_name: str, row: Mapping[str, Any]) -> str:
        keys = {
            "collector_runs": ["run_id"],
            "dispatcher_claims": ["panel_version", "logical_due_bucket"],
            "dispatcher_claim_events": ["event_id"],
            "target_claims": ["panel_version", "source_parent_station_id", "logical_due_bucket"],
            "claim_events": ["event_id"],
            "target_audits": ["audit_id"],
            "http_attempts": ["attempt_id"],
            "quota_permits": ["permit_id"],
            "quota_audit": ["quota_audit_id"],
            "parsed_snapshots": ["snapshot_id"],
            "timing_observations": ["timing_observation_id"],
            "recorded_at_time_evidence": ["evidence_id"],
            "service_section_observations": ["service_section_observation_id"],
            "raw_evidence": ["raw_evidence_id"],
            "manifest_targets": ["panel_version", "source_parent_station_id"],
            "database_metadata": ["metadata_key"],
            "sampling_eras": ["era_code", "panel_version"],
            "shared_usage": ["month_key"],
            "sqlite_sequence": ["name"],
        }
        selected = keys.get(table_name)
        if selected is None:
            return _canonical_json(row)
        return _source_key([row.get(key) for key in selected])

    def _create_batch(self) -> None:
        self.batch_id = str(uuid.uuid4())
        self._execute(
            """
            INSERT INTO ctl.MddImportBatch
                (ImportBatchId, SourceDatabaseSha256, SourceByteSize,
                 SourceSchemaVersion, SourceDatabaseRole, SourceCollectionContext,
                 SourceSessionStartUtc, SourceSessionEndUtc, SourcePathLineage,
                 ImportStartedAtUtc, Status, SourceCountsJson, ImporterVersion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, SYSUTCDATETIME(), 'IN_PROGRESS', %s, %s)
            """,
            (
                self.batch_id,
                self.proof.sha256,
                self.proof.byte_size,
                self.proof.application_schema_version,
                self.proof.database_role,
                self.proof.collection_context,
                _parse_utc(self.proof.session.start_utc),
                _parse_utc(self.proof.session.end_utc),
                _sanitize_source_path(Path(self.proof.path)),
                _canonical_json(self.proof.source_counts),
                IMPORTER_VERSION,
            ),
        )
        self._commit()

    def _create_audits(self) -> None:
        for inventory in self.inventory:
            counter = self._counter(inventory.table_name)
            counter.source_rows = inventory.row_count
            counter.eligible_rows = inventory.row_count if inventory.classification == "FULL_IMPORT" else 0
            self._execute(
                """
                INSERT INTO ctl.MddImportTableAudit
                    (ImportBatchId, SourceTableName, Classification,
                     ExplicitReason, SourceRowCount, EligibleRowCount,
                     InsertedRowCount, DuplicateRowCount,
                     OverlapClassifiedRowCount, DestinationRowCount,
                     UnexplainedDifference, ReconciliationStatus)
                VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 0, 0, 0, %s)
                """,
                (
                    self.batch_id,
                    inventory.table_name,
                    inventory.classification,
                    inventory.explicit_reason,
                    inventory.row_count,
                    counter.eligible_rows,
                    "PENDING" if inventory.classification == "FULL_IMPORT" else "NOT_APPLICABLE",
                ),
            )
        self._commit()

    def _create_evidence_audit(self, family: str, source_rows: int) -> None:
        counter = self._counter(family, evidence=True)
        counter.source_rows = source_rows
        counter.eligible_rows = source_rows
        self._execute(
            """
            INSERT INTO ctl.MddImportEvidenceAudit
                (ImportBatchId, EvidenceFamily, SourceRowCount, EligibleRowCount,
                 InsertedRowCount, DuplicateRowCount, OverlapClassifiedRowCount,
                 DestinationRowCount, UnexplainedDifference, ReconciliationStatus)
            VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0, 'PENDING')
            """,
            (self.batch_id, family, source_rows, source_rows),
        )

    def _import_runs(self) -> None:
        for row in self._source_rows("collector_runs"):
            source_id = str(row["run_id"])
            destination_id = _historical_run_id(self.proof.sha256, source_id)  # type: ignore[union-attr]
            self.run_map[source_id] = destination_id
            if self._existing_lineage("collector_runs", self._lineage_primary_key("collector_runs", row)) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacCollectorRun
                        (RunId, PanelVersion, LogicalDueBucket, Mode,
                         CollectionContext, StartedAtUtc, CompletedAtUtc,
                         Status, SummaryJson)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        destination_id,
                        row["panel_version"],
                        int(row["logical_due_bucket"]),
                        row["mode"],
                        row["collection_context"],
                        _parse_utc(row["started_at_utc"]),
                        _parse_utc(row["completed_at_utc"]),
                        row["status"],
                        row["summary_json"],
                    ),
                )
                status = "INSERTED"
            else:
                status = "DUPLICATE"
            self._record_lineage(
                source_table="collector_runs",
                source_key=self._lineage_primary_key("collector_runs", row),
                classification="FULL_IMPORT",
                new_status=status,
                destination_object="ctl.MddMacCollectorRun",
                destination_key=destination_id,
                source_row=row,
            )
        self._commit()

    def _import_dispatcher_claims(self) -> None:
        for row in self._source_rows("dispatcher_claims"):
            source_key = self._lineage_primary_key("dispatcher_claims", row)
            existing = self._existing_lineage("dispatcher_claims", source_key)
            destination_key = _source_key([row["panel_version"], int(row["logical_due_bucket"])])
            status = "DUPLICATE"
            if existing is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacDispatcherClaim
                        (PanelVersion, LogicalDueBucket, RunId, ClaimToken,
                         ClaimedAtUtc, StaleAfterUtc, Status, CompletedAtUtc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["panel_version"], int(row["logical_due_bucket"]),
                        self.run_map[str(row["collector_run_id"])], row["claim_token"],
                        _parse_utc(row["claimed_at_utc"]), _parse_utc(row["stale_after_utc"]),
                        row["status"], _parse_utc(row["completed_at_utc"]),
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="dispatcher_claims", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacDispatcherClaim", destination_key=destination_key,
                source_row=row,
            )
        self._commit()

    def _import_dispatcher_claim_events(self) -> None:
        for row in self._source_rows("dispatcher_claim_events"):
            source_key = self._lineage_primary_key("dispatcher_claim_events", row)
            status = "DUPLICATE"
            if self._existing_lineage("dispatcher_claim_events", source_key) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacDispatcherClaimEvent
                        (PanelVersion, LogicalDueBucket, RunId, EventAtUtc,
                         Outcome, ExistingStatus)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["panel_version"], int(row["logical_due_bucket"]),
                        self.run_map[str(row["collector_run_id"])], _parse_utc(row["event_at_utc"]),
                        row["outcome"], row["existing_status"],
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="dispatcher_claim_events", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacDispatcherClaimEvent", destination_key=source_key,
                source_row=row,
            )
        self._commit()

    def _import_target_claims(self) -> None:
        for row in self._source_rows("target_claims"):
            source_key = self._lineage_primary_key("target_claims", row)
            destination_key = _source_key([row["panel_version"], row["source_parent_station_id"], int(row["logical_due_bucket"])])
            status = "DUPLICATE"
            if self._existing_lineage("target_claims", source_key) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacTargetClaim
                        (PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                         ClaimToken, ClaimedAtUtc, StaleAfterUtc, Status,
                         StartedAtUtc, CompletedAtUtc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["panel_version"], row["source_parent_station_id"], int(row["logical_due_bucket"]),
                        self.run_map[str(row["collector_run_id"])], row["claim_token"],
                        _parse_utc(row["claimed_at_utc"]), _parse_utc(row["stale_after_utc"]), row["status"],
                        _parse_utc(row["started_at_utc"]), _parse_utc(row["completed_at_utc"]),
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="target_claims", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacTargetClaim", destination_key=destination_key,
                source_row=row,
            )
        self._commit()

    def _import_target_claim_events(self) -> None:
        for row in self._source_rows("claim_events"):
            source_key = self._lineage_primary_key("claim_events", row)
            status = "DUPLICATE"
            if self._existing_lineage("claim_events", source_key) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacTargetClaimEvent
                        (PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                         EventAtUtc, Outcome, ExistingStatus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["panel_version"], row["source_parent_station_id"], int(row["logical_due_bucket"]),
                        self.run_map[str(row["collector_run_id"])], _parse_utc(row["event_at_utc"]),
                        row["outcome"], row["existing_status"],
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="claim_events", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacTargetClaimEvent", destination_key=source_key,
                source_row=row,
            )
        self._commit()

    def _import_quota_permits(self) -> None:
        for row in self._source_rows("quota_permits"):
            source_key = self._lineage_primary_key("quota_permits", row)
            status = "DUPLICATE"
            if self._existing_lineage("quota_permits", source_key) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacQuotaPermit
                        (PermitId, MonthKey, DecisionKind, ReservationScope,
                         Amount, Boundary, ReservedAtUtc, Status, FinalizedAtUtc,
                         LiveClientInvocation, ReleasedAtUtc, FinalizationReason,
                         SourceRepresentationJson)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
                    """,
                    (
                        row["permit_id"], row["month_key"], row["decision_kind"], row["reservation_scope"],
                        int(row["amount"]), int(row["boundary"]), _parse_utc(row["reserved_at_utc"]), row["status"],
                        _parse_utc(row["finalized_at_utc"]), _parse_utc(row["released_at_utc"]),
                        row["finalization_reason"], _source_row_json(row),
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="quota_permits", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacQuotaPermit", destination_key=str(row["permit_id"]),
                source_row=row,
            )
        self._commit()

    def _import_quota_audit(self) -> None:
        for row in self._source_rows("quota_audit"):
            source_key = self._lineage_primary_key("quota_audit", row)
            status = "DUPLICATE"
            if self._existing_lineage("quota_audit", source_key) is None:
                self._execute(
                    """
                    INSERT INTO ctl.MddMacQuotaAudit
                        (RunId, TargetIdentity, LogicalDueBucket, DecisionKind,
                         DecisionJson, RecordedAtUtc, CollectionContext)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.run_map[str(row["collector_run_id"])], row["source_parent_station_id"],
                        row["logical_due_bucket"], row["decision_kind"], row["decision_json"],
                        _parse_utc(row["recorded_at_utc"]), row["collection_context"],
                    ),
                )
                status = "INSERTED"
            self._record_lineage(
                source_table="quota_audit", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacQuotaAudit", destination_key=source_key,
                source_row=row,
            )
        self._commit()

    def _import_target_audits(self) -> None:
        for row in self._source_rows("target_audits"):
            source_key = self._lineage_primary_key("target_audits", row)
            status = "DUPLICATE"
            destination_id: int | None = None
            if self._existing_lineage("target_audits", source_key) is None:
                self._execute(
                    """
                    SET NOCOUNT ON;
                    INSERT INTO ctl.MddMacTargetAudit
                        (RunId, PanelVersion, TargetIdentity, StationName, Tier,
                         PhaseIndex, NumberOfResults, LogicalDueBucket, SamplingEra,
                         SamplingTargetId, ContinuityClassification, CollectionContext,
                         StartedAtUtc, CompletedAtUtc, Status, HttpAttempts,
                         HttpStatus, StopEventResultCount, ParserOutcome,
                         PersistenceOutcome, ErrorCategory, OutcomeJson,
                         DurationMs, SkipReason, RetryDecision, RetryOutcome)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS AuditId
                    """,
                    (
                        self.run_map[str(row["collector_run_id"])], row["panel_version"],
                        row["source_parent_station_id"], row["station_name"], row["tier"],
                        int(row["phase_index"]), int(row["number_of_results"]), int(row["logical_due_bucket"]),
                        row["sampling_era"], self.manifest_sampling_ids.get(str(row["source_parent_station_id"])),
                        row["continuity_classification"], row["collection_context"],
                        _parse_utc(row["started_at_utc"]), _parse_utc(row["completed_at_utc"]), row["status"],
                        int(row["http_attempts"] or 0), row["http_status"], row["stop_event_result_count"],
                        row["parser_outcome"], row["persistence_outcome"], row["error_category"],
                        _source_row_json(row), row["duration_ms"], row["skip_reason"],
                        row["retry_decision"], row["retry_outcome"],
                    ),
                )
                destination_id = int(_row_value(self.cursor.fetchone(), "AuditId", 0))
                self.audit_map[int(row["audit_id"])] = destination_id
                status = "INSERTED"
            else:
                lineage = self._existing_lineage("target_audits", source_key)
                destination_id = int(_row_value(lineage, "DestinationKey", 4)) if lineage and _row_value(lineage, "DestinationKey", 4) else None
                if destination_id is not None:
                    self.audit_map[int(row["audit_id"])] = destination_id
            self._record_lineage(
                source_table="target_audits", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacTargetAudit", destination_key=str(destination_id) if destination_id else None,
                source_row=row,
            )
        self._commit()

    def _import_http_attempts(self) -> None:
        for row in self._source_rows("http_attempts"):
            source_key = self._lineage_primary_key("http_attempts", row)
            status = "DUPLICATE"
            destination_key = source_key
            if self._existing_lineage("http_attempts", source_key) is None:
                audit_id = self.audit_map[int(row["audit_id"])]
                self._execute(
                    """
                    SET NOCOUNT ON;
                    INSERT INTO ctl.MddMacHttpAttempt
                        (AuditId, AttemptNumber, StartedAtUtc, CompletedAtUtc,
                         HttpStatus, TransportFailureCategory, Outcome,
                         RetryDecision, RetryAfterSeconds, DurationMs,
                         TransportKind, NetworkAttempted, HttpResponseReceived,
                         LiveClientInvocation, ConservativeQuotaAccounted,
                         ProviderQuotaStatus, ProviderAttemptConfirmed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    ; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS HttpAttemptId
                    """,
                    (
                        audit_id, int(row["attempt_number"]), _parse_utc(row["started_at_utc"]),
                        _parse_utc(row["completed_at_utc"]), row["http_status"], row["transport_failure_category"],
                        row["outcome"], row["retry_decision"], row["retry_after_seconds"], row["duration_ms"],
                        row["transport_kind"], _bool_int(row["network_attempted"]), _bool_int(row["http_response_received"]),
                        _bool_int(row["live_client_invocation"]), _bool_int(row["conservative_quota_accounted"]),
                        row["provider_quota_status"], _bool_int(row["provider_attempt_confirmed"]),
                    ),
                )
                destination_key = str(_row_value(self.cursor.fetchone(), "HttpAttemptId", 0))
                status = "INSERTED"
            self._record_lineage(
                source_table="http_attempts", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="ctl.MddMacHttpAttempt", destination_key=destination_key,
                source_row=row,
            )
        self._commit()

    def _persist_snapshot(self, row: Mapping[str, Any], parsed: Mapping[str, Any]) -> int:
        if self.cursor is None:
            raise SQLiteImportError("SQL Server cursor is not open")
        snapshot_json = str(row["snapshot_json"])
        row_sets = build_mssql_snapshot_rows(parsed)
        audit_id = self.audit_map[int(row["audit_id"])]
        self._execute(
            """
            EXEC stg.uspPersistMddMacSnapshot
                @RunId=%s, @AuditId=%s, @TargetIdentity=%s,
                @SamplingTargetId=%s, @LogicalDueBucket=%s, @ObservedAtUtc=%s,
                @ResponseTimestampSourceText=%s, @ParserVersion=%s,
                @ApplicationStatus=%s, @PayloadHashSha256=%s, @SnapshotJson=%s,
                @CallsJson=%s, @TimingsJson=%s, @SituationEvidenceJson=%s,
                @ServiceSectionsJson=%s, @AttributesJson=%s,
                @ResponseErrorsJson=%s, @ParserGapsJson=%s
            """,
            (
                self.run_map[str(row["collector_run_id"])] if row.get("collector_run_id") else self._audit_run_id(audit_id),
                audit_id,
                row["source_parent_station_id"],
                self.manifest_sampling_ids.get(str(row["source_parent_station_id"])),
                int(row["logical_due_bucket"]),
                _parse_utc(row["persisted_at_utc"]),
                parsed.get("response_timestamp"), parsed.get("parser_version", "unknown"),
                parsed.get("application_status"), hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                snapshot_json,
                _canonical_json(row_sets["calls"]), _canonical_json(row_sets["timings"]),
                _canonical_json(row_sets["situations"]), _canonical_json(row_sets["service_sections"]),
                _canonical_json(row_sets["attributes"]), _canonical_json(row_sets["response_errors"]),
                _canonical_json(row_sets["parser_gaps"]),
            ),
        )
        snapshot_id = _result_snapshot_id(self.cursor)
        _drain_cursor(self.cursor)
        if snapshot_id is None:
            row_found = self._execute(
                "SELECT SnapshotId FROM stg.MddMacSourceSnapshot WHERE AuditId = %s", (audit_id,)
            ).fetchone()
            snapshot_id = int(_row_value(row_found, "SnapshotId", 0)) if row_found else None
        if snapshot_id is None:
            raise SQLiteImportError(f"SQL Server snapshot procedure did not return SnapshotId for source audit {row['audit_id']}")
        return snapshot_id

    def _audit_run_id(self, audit_id: int) -> str:
        row = self._execute("SELECT RunId FROM ctl.MddMacTargetAudit WHERE AuditId = %s", (audit_id,)).fetchone()
        value = _row_value(row, "RunId", 0)
        if not value:
            raise SQLiteImportError(f"destination audit {audit_id} has no RunId")
        return str(value)

    def _record_snapshot_children(self, source_row: Mapping[str, Any], parsed: Mapping[str, Any], destination_id: int) -> None:
        row_sets = build_mssql_snapshot_rows(parsed)
        source_snapshot_id = int(source_row["snapshot_id"])
        families = {
            "call_observations": row_sets["calls"],
            "situation_evidence": row_sets["situations"],
            "attribute_evidence": row_sets["attributes"],
            "response_errors": row_sets["response_errors"],
            "parser_gaps": row_sets["parser_gaps"],
        }
        for family, rows in families.items():
            for index, child in enumerate(rows):
                if family == "call_observations":
                    key = _child_key(source_snapshot_id, child.get("event_ordinal"), child.get("call_scope"), child.get("call_ordinal"))
                    object_name = "stg.MddMacCallObservation"
                elif family == "situation_evidence":
                    key = _child_key(source_snapshot_id, child.get("event_ordinal"), index, child.get("source_path"))
                    object_name = "stg.MddMacSituationEvidence"
                elif family == "attribute_evidence":
                    key = _child_key(source_snapshot_id, child.get("event_ordinal"), child.get("attribute_ordinal"), child.get("source_path"))
                    object_name = "stg.MddMacAttributeEvidence"
                elif family == "response_errors":
                    key = _child_key(source_snapshot_id, child.get("source_path"), child.get("field_name"))
                    object_name = "stg.MddMacResponseError"
                else:
                    key = _child_key(source_snapshot_id, child.get("source_path"), child.get("normalized_field_name"))
                    object_name = "stg.MddMacParserGap"
                self._record_lineage(
                    source_table=f"parsed_snapshots.{family}", source_key=key,
                    classification="FULL_IMPORT", new_status="OVERLAP" if destination_id else "INSERTED",
                    destination_object=object_name, destination_key=f"SnapshotId={destination_id}|{key}",
                    source_row=child, evidence=True,
                )

    def _import_snapshots(self) -> None:
        # The source snapshot JSON is the authoritative parent document.  Its
        # child arrays are passed through the same SQL procedure as native Mac
        # snapshots; source-table projections are reconciled below.
        for row in self._source_rows("parsed_snapshots"):
            source_key = self._lineage_primary_key("parsed_snapshots", row)
            lineage = self._existing_lineage("parsed_snapshots", source_key)
            parsed = json.loads(str(row["snapshot_json"]))
            if lineage is None:
                destination_id = self._persist_snapshot(row, parsed)
                status = "INSERTED"
            else:
                destination_id = int(_row_value(lineage, "DestinationKey", 4))
                status = "DUPLICATE"
            self.snapshot_map[int(row["snapshot_id"])] = destination_id
            self._record_lineage(
                source_table="parsed_snapshots", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacSourceSnapshot", destination_key=str(destination_id),
                source_row=row,
            )
            self._record_snapshot_children(row, parsed, destination_id)
        self._commit()

    def _find_timing(self, destination_snapshot_id: int, row: Mapping[str, Any]) -> int | None:
        if destination_snapshot_id not in self.timing_destination_cache:
            rows = self._execute(
                """
                SELECT TimingObservationId, EventOrdinal, CallScope,
                       CallOrdinal, TimingContext
                FROM stg.MddMacTimingObservation
                WHERE SnapshotId = %s
                """,
                (destination_snapshot_id,),
            ).fetchall()
            self.timing_destination_cache[destination_snapshot_id] = {
                (
                    row.get("EventOrdinal") if isinstance(row, Mapping) else row[1],
                    row.get("CallScope") if isinstance(row, Mapping) else row[2],
                    row.get("CallOrdinal") if isinstance(row, Mapping) else row[3],
                    row.get("TimingContext") if isinstance(row, Mapping) else row[4],
                ): int(row.get("TimingObservationId") if isinstance(row, Mapping) else row[0])
                for row in rows
            }
        return self.timing_destination_cache[destination_snapshot_id].get(
            (
                row["event_ordinal"],
                row["call_scope"],
                row["call_ordinal"],
                row["timing_context"],
            )
        )

    def _import_timing_rows(self) -> None:
        for row in self._source_rows("timing_observations"):
            source_key = self._lineage_primary_key("timing_observations", row)
            existing = self._existing_lineage("timing_observations", source_key)
            destination_snapshot_id = self.snapshot_map[int(row["snapshot_id"])]
            destination_id: int | None = None
            status = "DUPLICATE"
            if existing is None:
                destination_id = self._find_timing(destination_snapshot_id, row)
                if destination_id is not None:
                    status = "OVERLAP"
                else:
                    # A current schema-5 source should reconcile to the SQL
                    # procedure's normalized timing rows.  A mismatch is
                    # imported additively with the source fields unchanged.
                    self._execute(
                        """
                        SET NOCOUNT ON;
                        INSERT INTO stg.MddMacTimingObservation
                            (SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                             CallScope, CallOrdinal, TimingContext,
                             OperatingDayRef, JourneyRef, VehicleJourneyRef,
                             DatedVehicleJourneyRef, LineRef, DirectionRef,
                             OperatorRef, StopPointRef, StopSeqNumber,
                             TimetabledTimeSourceText, EstimatedTimeSourceText,
                             EstimatedTimeLowSourceText, EstimatedTimeHighSourceText,
                             RecordedAtTimeSourceText, TimingSourcePath,
                             TimetabledTimeSourcePath, EstimatedTimeSourcePath,
                             EstimatedTimeLowSourcePath, EstimatedTimeHighSourcePath,
                             RecordedAtTimeSourcePath, StopPointRefSourcePath,
                             StopSeqNumberSourcePath, SourceEventIdentity,
                             SourceEventIdentityVersion, SourceEventIdentityStatus,
                             SourceEventIdentityBasisJson, SourceEventIdentityMissingFieldsJson,
                             SourceEventIdentityAmbiguityJson, SourcePathsJson)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s)
                        ; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS TimingObservationId
                        """,
                        (
                            destination_snapshot_id, row["result_id"], row["event_ordinal"], row["event_ordinal"],
                            row["call_scope"], row["call_ordinal"], row["timing_context"], row["operating_day_ref"],
                            row["journey_ref"], row["vehicle_journey_ref"], row["dated_vehicle_journey_ref"],
                            row["line_ref"], row["direction_ref"], row["operator_ref"], row["stop_point_ref"],
                            row["stop_seq_number"], row["timetabled_time"], row["estimated_time"], row["estimated_time_low"],
                            row["estimated_time_high"], row["recorded_at_time"], row["source_path"],
                            row["timetabled_time_source_path"], row["estimated_time_source_path"],
                            row["estimated_time_low_source_path"], row["estimated_time_high_source_path"],
                            row["recorded_at_time_source_path"], row["stop_point_ref_source_path"],
                            row["stop_seq_number_source_path"], row["source_event_identity"],
                            row["source_event_identity_version"], row["source_event_identity_status"],
                            row["source_event_identity_basis_json"], row["source_event_identity_missing_fields_json"],
                            row["source_event_identity_ambiguity_json"], row["source_paths_json"],
                        ),
                    )
                    destination_id = int(_row_value(self.cursor.fetchone(), "TimingObservationId", 0))
                    status = "INSERTED"
            else:
                destination_id = int(_row_value(existing, "DestinationKey", 4)) if _row_value(existing, "DestinationKey", 4) else None
            self._record_lineage(
                source_table="timing_observations", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacTimingObservation", destination_key=str(destination_id) if destination_id else None,
                source_row=row,
            )
            self._record_lineage(
                source_table="parsed_snapshots.timing_observations", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacTimingObservation", destination_key=str(destination_id) if destination_id else None,
                source_row=row, evidence=True,
            )
        self._commit()

    def _find_service_section(self, destination_snapshot_id: int, row: Mapping[str, Any]) -> int | None:
        result = self._execute(
            """
            SELECT TOP (1) ServiceSectionEvidenceId
            FROM stg.MddMacServiceSectionEvidence
            WHERE SnapshotId = %s AND EventOrdinal = %s
              AND SectionOrdinal = %s AND SourcePath = %s
            """,
            (destination_snapshot_id, row["event_ordinal"], row["section_ordinal"], row["source_path"]),
        ).fetchone()
        value = _row_value(result, "ServiceSectionEvidenceId", 0)
        return int(value) if value is not None else None

    def _import_service_sections(self) -> None:
        for row in self._source_rows("service_section_observations"):
            source_key = self._lineage_primary_key("service_section_observations", row)
            existing = self._existing_lineage("service_section_observations", source_key)
            destination_snapshot_id = self.snapshot_map[int(row["snapshot_id"])]
            destination_id: int | None = None
            status = "DUPLICATE"
            overlap = None
            if existing is None:
                destination_id = self._find_service_section(destination_snapshot_id, row)
                if destination_id is not None:
                    status = "OVERLAP"
                    overlap = "SNAPSHOT_PROCEDURE_EQUIVALENT_OCCURRENCE"
                else:
                    self._execute(
                        """
                        SET NOCOUNT ON;
                        INSERT INTO stg.MddMacServiceSectionEvidence
                            (SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                             SectionOrdinal, SourcePath, LineRef, DirectionRef,
                             OperatorRef, PtMode, RailSubmode, BusSubmode,
                             TramSubmode, Name, ShortName, Description,
                             RouteDescription, Via, PublishedLineName,
                             ModeSourceJson, SourcePathsJson,
                             SourceRepresentationJson, SemanticRole)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS ServiceSectionEvidenceId
                        """,
                        (
                            destination_snapshot_id, row["result_id"], row["result_ordinal"], row["event_ordinal"],
                            row["section_ordinal"], row["source_path"], row["line_ref"], row["direction_ref"],
                            row["operator_ref"], row["pt_mode"], row["rail_submode"], row["bus_submode"],
                            row["tram_submode"], row["name"], row["short_name"], row["description"],
                            row["route_description"], row["via"], row.get("published_line_name_text_json"),
                            row.get("mode_source_representation_json"), row["source_paths_json"],
                            row["source_representation_json"], "HISTORICAL_SOURCE_PROJECTION",
                        ),
                    )
                    destination_id = int(_row_value(self.cursor.fetchone(), "ServiceSectionEvidenceId", 0))
                    status = "INSERTED"
            else:
                destination_id = int(_row_value(existing, "DestinationKey", 4)) if _row_value(existing, "DestinationKey", 4) else None
            self._record_lineage(
                source_table="service_section_observations", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacServiceSectionEvidence", destination_key=str(destination_id) if destination_id else None,
                source_row=row, overlap_classification=overlap,
            )
            self._record_lineage(
                source_table="parsed_snapshots.service_section_observations", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacServiceSectionEvidence", destination_key=str(destination_id) if destination_id else None,
                source_row=row, overlap_classification=overlap, evidence=True,
            )
        self._commit()

    def _import_recorded_at_time_evidence(self) -> None:
        for row in self._source_rows("recorded_at_time_evidence"):
            source_key = self._lineage_primary_key("recorded_at_time_evidence", row)
            existing = self._existing_lineage("recorded_at_time_evidence", source_key)
            destination_id = None
            status = "DUPLICATE"
            if existing is None:
                destination_snapshot_id = self.snapshot_map[int(row["snapshot_id"])]
                timing_row = self._execute(
                    """
                    SELECT TOP (1) TimingObservationId
                    FROM stg.MddMacTimingObservation
                    WHERE SnapshotId = %s AND EventOrdinal = %s
                      AND CallScope = %s AND CallOrdinal = %s
                      AND TimingContext = %s
                    """,
                    (destination_snapshot_id, row["event_ordinal"], row["call_scope"], row["call_ordinal"], row["timing_context"]),
                ).fetchone()
                destination_id = _row_value(timing_row, "TimingObservationId", 0)
                if destination_id is None:
                    raise SQLiteImportError(f"RecordedAtTime source row {row['evidence_id']} has no timing destination")
                status = "OVERLAP"
            else:
                destination_id = _row_value(existing, "DestinationKey", 4)
            self._record_lineage(
                source_table="recorded_at_time_evidence", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacTimingObservation", destination_key=str(destination_id), source_row=row,
            )
            self._record_lineage(
                source_table="parsed_snapshots.recorded_at_time_occurrences", source_key=source_key,
                classification="FULL_IMPORT", new_status=status,
                destination_object="stg.MddMacTimingObservation", destination_key=str(destination_id), source_row=row, evidence=True,
            )
        self._commit()

    def _build_manifest_map(self) -> None:
        self.manifest_sampling_ids = {
            str(row["source_parent_station_id"]): (
                int(row["legacy_sampling_target_id"]) if row.get("legacy_sampling_target_id") is not None else None
            )
            for row in self._source_rows("manifest_targets")
        }

    def _import_all(self) -> None:
        self._build_manifest_map()
        self._import_runs()
        self._import_dispatcher_claims()
        self._import_dispatcher_claim_events()
        self._import_target_claims()
        self._import_target_claim_events()
        self._import_quota_permits()
        self._import_quota_audit()
        self._import_target_audits()
        self._import_http_attempts()
        self._import_snapshots()
        self._import_timing_rows()
        self._import_service_sections()
        self._import_recorded_at_time_evidence()
        for inventory in self.inventory:
            if inventory.classification != "FULL_IMPORT":
                self._lineage_only_table(inventory)
        self._commit()

    def _destination_lineage_count(self, source_table: str) -> int:
        row = self._execute(
            """
            SELECT COUNT_BIG(*) AS n
            FROM ctl.MddImportSourceRow
            WHERE SourceDatabaseSha256 = %s AND SourceTableName = %s
            """,
            (self.proof.sha256, source_table),  # type: ignore[union-attr]
        ).fetchone()
        return int(_row_value(row, "n", 0) or 0)

    def _finish_audits(self) -> dict[str, Any]:
        table_result: dict[str, Any] = {}
        all_differences: dict[str, int] = {}
        for inventory in self.inventory:
            counter = self._counter(inventory.table_name)
            destination_count = self._destination_lineage_count(inventory.table_name)
            difference = counter.unexplained_difference if inventory.classification == "FULL_IMPORT" else 0
            status = "PASS" if difference == 0 else "FAIL"
            self._execute(
                """
                UPDATE ctl.MddImportTableAudit
                SET InsertedRowCount = %s, DuplicateRowCount = %s,
                    OverlapClassifiedRowCount = %s, DestinationRowCount = %s,
                    UnexplainedDifference = %s, ReconciliationStatus = %s
                WHERE ImportBatchId = %s AND SourceTableName = %s
                """,
                (
                    counter.inserted_rows, counter.duplicate_rows, counter.overlap_rows,
                    destination_count, difference, status,
                    self.batch_id, inventory.table_name,
                ),
            )
            table_result[inventory.table_name] = {
                **counter.to_dict(),
                "destination_rows_attributable_to_source": destination_count,
                "reconciliation_status": status,
                "explicit_reason": inventory.explicit_reason,
            }
            all_differences[inventory.table_name] = difference
        evidence_result: dict[str, Any] = {}
        for family, counter in self.evidence_counters.items():
            destination_count = self._destination_lineage_count(f"parsed_snapshots.{family}")
            difference = counter.unexplained_difference
            status = "PASS" if difference == 0 else "FAIL"
            self._execute(
                """
                UPDATE ctl.MddImportEvidenceAudit
                SET InsertedRowCount = %s, DuplicateRowCount = %s,
                    OverlapClassifiedRowCount = %s, DestinationRowCount = %s,
                    UnexplainedDifference = %s, ReconciliationStatus = %s
                WHERE ImportBatchId = %s AND EvidenceFamily = %s
                """,
                (
                    counter.inserted_rows, counter.duplicate_rows, counter.overlap_rows,
                    destination_count, difference, status, self.batch_id, family,
                ),
            )
            evidence_result[family] = {
                **counter.to_dict(),
                "destination_rows_attributable_to_source": destination_count,
                "reconciliation_status": status,
            }
            all_differences[f"evidence:{family}"] = difference
        if any(value != 0 for value in all_differences.values()):
            raise SQLiteImportError("SQLite-to-SQL reconciliation has unexplained differences: " + _canonical_json(all_differences))
        self._commit()
        return {"tables": table_result, "evidence": evidence_result, "unexplained_differences": all_differences}

    def _update_batch(self, *, status: str, reconciliation: Mapping[str, Any], failure_reason: str | None = None) -> None:
        inserted = {key: value.inserted_rows for key, value in self.table_counters.items()}
        duplicates = {key: value.duplicate_rows for key, value in self.table_counters.items()}
        overlaps = {key: value.overlap_rows for key, value in self.table_counters.items()}
        self._execute(
            """
            UPDATE ctl.MddImportBatch
            SET ImportCompletedAtUtc = SYSUTCDATETIME(), Status = %s,
                InsertedCountsJson = %s, DuplicateCountsJson = %s,
                OverlapCountsJson = %s, ReconciliationResultJson = %s,
                FailureReason = %s
            WHERE ImportBatchId = %s
            """,
            (
                status, _canonical_json(inserted), _canonical_json(duplicates), _canonical_json(overlaps),
                _canonical_json(reconciliation), failure_reason, self.batch_id,
            ),
        )
        self._commit()

    def run(self) -> dict[str, Any]:
        self.proof, self.inventory = inspect_sqlite_source(self.source_path)
        inventory_dict = [item.to_dict() for item in self.inventory]
        if not self.apply:
            return {
                "status": "DRY_RUN",
                "apply": False,
                "importer_version": IMPORTER_VERSION,
                "source": self.proof.to_dict(),
                "inventory": inventory_dict,
                "reconciliation_mode": "read-only source proof and planned import; no SQL Server writes",
            }
        if self.settings is None:
            raise SQLiteImportError("explicit SQL Server settings are required with --apply")
        if self.settings.database != EXPECTED_SQL_DATABASE:
            raise SQLiteImportError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
        # These are all read-only gates.  Capacity and failed-batch history are
        # intentionally evaluated before a new ImportBatchId is created.
        failed_import_audit = read_only_failed_import_audit(self.settings, self.proof.sha256)
        if not failed_import_audit["resume_reconcile_safe"]:
            raise SQLiteImportError(
                "existing FAILED import history contains inconsistent inserted lineage; "
                "refusing to create a new batch"
            )
        capacity = read_only_capacity_audit(self.settings, self.proof)
        if capacity["status"] != "PASS":
            blockers = capacity.get("import_capacity", {}).get("capacity_blockers", [])
            raise SQLiteImportError(
                "SQL Server capacity preflight failed before batch creation: "
                + _canonical_json(blockers)
            )
        adapter = MssqlPersistence(self.settings)
        schema = adapter.validate_full_schema()
        before = adapter.baseline()
        overlap = read_only_overlap_audit(self.settings, self.proof)
        # Reopen the exact proven file through SQLite's read-only URI.  The
        # importer never uses an implicitly discovered or writable source.
        self.source_connection = sqlite3.connect(_sqlite_uri(self.source_path), uri=True)
        self.source_connection.row_factory = sqlite3.Row
        connection: Any | None = None
        try:
            connection = self.connection_factory() if self.connection_factory else connect_mssql(self.settings)
            self.connection = connection
            self.cursor = connection.cursor()
            self._prime_lineage_cache()
            database_name = _row_value(self._execute("SELECT DB_NAME() AS database_name").fetchone(), "database_name", 0)
            if str(database_name) != EXPECTED_SQL_DATABASE:
                raise SQLiteImportError(f"connected SQL Server database is {database_name!r}, not {EXPECTED_SQL_DATABASE}")
            self._create_batch()
            self._create_audits()
            self._create_evidence_audit("call_observations", 0)
            self._create_evidence_audit("timing_observations", 0)
            self._create_evidence_audit("service_section_observations", 0)
            self._create_evidence_audit("recorded_at_time_occurrences", 0)
            self._create_evidence_audit("situation_evidence", 0)
            self._create_evidence_audit("attribute_evidence", 0)
            self._create_evidence_audit("response_errors", 0)
            self._create_evidence_audit("parser_gaps", 0)
            self._import_all()
            # Child evidence counts are known after the source JSON is read;
            # update their source/eligible counts before reconciliation.
            for family, counter in self.evidence_counters.items():
                self._execute(
                    """
                    UPDATE ctl.MddImportEvidenceAudit
                    SET SourceRowCount = %s, EligibleRowCount = %s
                    WHERE ImportBatchId = %s AND EvidenceFamily = %s
                    """,
                    (counter.source_rows, counter.eligible_rows, self.batch_id, family),
                )
            reconciliation = self._finish_audits()
            self._execute(
                """
                INSERT INTO ctl.MddImportOverlap
                    (ImportBatchId, EvidenceFamily, SourceIdentityHash,
                     Classification, ExistingObjectName, ExistingKey, DetailsJson)
                VALUES (%s, 'OVERLAP_AUDIT', %s, 'READ_ONLY_SUMMARY',
                        'ctl.MddCollectorRun / stg.MddRealtimeStopObservation', NULL, %s)
                """,
                (
                    self.batch_id,
                    hashlib.sha256(f"{self.proof.sha256}|overlap".encode()).hexdigest(),
                    _canonical_json(overlap),
                ),
            )
            reconciliation = {**reconciliation, "overlap_audit": overlap}
            self._update_batch(status="COMPLETE", reconciliation=reconciliation)
            after = adapter.baseline()
            return {
                "status": "COMPLETE",
                "apply": True,
                "importer_version": IMPORTER_VERSION,
                "import_batch_id": self.batch_id,
                "source": self.proof.to_dict(),
                "inventory": inventory_dict,
                "schema": schema,
                "capacity_audit": capacity,
                "previous_failed_import_audit": failed_import_audit,
                "overlap_audit": overlap,
                "reconciliation": reconciliation,
                "before": before,
                "after": after,
                "sampling_target_ids_preserved": before.get("sampling_target_ids") == after.get("sampling_target_ids") == [1, 2, 3, 4, 5, 6, 7],
            }
        except BaseException as exc:
            try:
                if connection is not None:
                    connection.rollback()
            except BaseException:
                pass
            if self.batch_id is not None:
                try:
                    self._update_batch(
                        status="FAILED",
                        reconciliation={"status": "FAILED"},
                        failure_reason=f"{type(exc).__name__}: {str(exc)[:1800]}",
                    )
                except BaseException:
                    pass
            raise
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                if self.source_connection is not None:
                    self.source_connection.close()
                    self.source_connection = None
                self.connection = None
                self.cursor = None


def read_only_overlap_audit(settings: MssqlConnectionSettings, proof: SQLiteSourceProof) -> dict[str, Any]:
    """Inspect existing Windows-era rows without merging or changing them."""

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        start = _parse_utc(proof.session.start_utc)
        end = _parse_utc(proof.session.end_utc)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS n
            FROM ctl.MddCollectorRun
            WHERE StartedAtUtc <= %s
              AND COALESCE(CompletedAtUtc, StartedAtUtc) >= %s
            """,
            (end, start),
        )
        existing_runs = int(_row_value(cursor.fetchone(), "n", 0) or 0)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS n
            FROM stg.MddRealtimeStopObservation
            WHERE ObservedAtUtc >= %s AND ObservedAtUtc <= %s
              AND StopPointRef LIKE %s
            """,
            (start, end, LIVE_RESEARCH_TARGET_ID + "%"),
        )
        existing_heumarkt_observations = int(_row_value(cursor.fetchone(), "n", 0) or 0)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS n
            FROM stg.MddRealtimeSituationObservation
            WHERE ObservedAtUtc >= %s AND ObservedAtUtc <= %s
            """,
            (start, end),
        )
        existing_situations = int(_row_value(cursor.fetchone(), "n", 0) or 0)
        return {
            "status": "READ_ONLY_COMPLETE",
            "research_interval": {
                "start_utc": proof.session.start_utc,
                "end_utc": proof.session.end_utc,
                "duration_seconds": proof.session.duration_seconds,
            },
            "existing_windows_collector_runs": existing_runs,
            "existing_windows_heumarkt_observations": existing_heumarkt_observations,
            "existing_windows_situation_observations": existing_situations,
            "source_events_present_in_both": "NOT_PROVABLE_WITHOUT_SOURCE_EVENT_IDENTITY_ON_LEGACY_TABLES",
            "exact_equivalent_evidence": "NOT_PROVABLE_WITHOUT_LEGACY_PAYLOAD_HASH",
            "different_snapshots_same_event": "NOT_PROVABLE_WITHOUT_SOURCE_EVENT_IDENTITY_CORRELATION",
            "research_only_evidence": "PRESERVE_UNDER_LIVE_RESEARCH_LINEAGE",
            "ambiguous_identity_cases": "LEGACY_ROWS_ARE_NOT_DESTRUCTIVELY_DEDUPLICATED",
            "destructive_merge_performed": False,
        }
    finally:
        connection.close()


def read_only_failed_import_audit(
    settings: MssqlConnectionSettings,
    source_sha256: str,
) -> dict[str, Any]:
    """Return immutable history and partial-destination evidence for failed batches."""

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT ImportBatchId, ImportStartedAtUtc, ImportCompletedAtUtc,
                   FailureReason, SourceCountsJson, InsertedCountsJson,
                   DuplicateCountsJson, OverlapCountsJson,
                   ReconciliationResultJson, ImporterVersion
            FROM ctl.MddImportBatch
            WHERE SourceDatabaseSha256 = %s AND Status = 'FAILED'
            ORDER BY ImportStartedAtUtc, ImportBatchId
            """,
            (source_sha256,),
        )
        batches = cursor.fetchall()
        audited: list[dict[str, Any]] = []
        for batch in batches:
            batch_id = _row_value(batch, "ImportBatchId", 0)
            cursor.execute(
                """
                SELECT SourceTableName, Classification, SourceRowCount,
                       EligibleRowCount, InsertedRowCount, DuplicateRowCount,
                       OverlapClassifiedRowCount, DestinationRowCount,
                       UnexplainedDifference, ReconciliationStatus
                FROM ctl.MddImportTableAudit
                WHERE ImportBatchId = %s
                ORDER BY SourceTableName
                """,
                (batch_id,),
            )
            table_audits = [dict(row) if isinstance(row, Mapping) else row for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT EvidenceFamily, SourceRowCount, EligibleRowCount,
                       InsertedRowCount, DuplicateRowCount,
                       OverlapClassifiedRowCount, DestinationRowCount,
                       UnexplainedDifference, ReconciliationStatus
                FROM ctl.MddImportEvidenceAudit
                WHERE ImportBatchId = %s
                ORDER BY EvidenceFamily
                """,
                (batch_id,),
            )
            evidence_audits = [dict(row) if isinstance(row, Mapping) else row for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS source_row_count,
                       SUM(CASE WHEN RowStatus = 'INSERTED' THEN 1 ELSE 0 END) AS inserted_lineage_count,
                       SUM(CASE WHEN RowStatus = 'OVERLAP' THEN 1 ELSE 0 END) AS overlap_lineage_count,
                       COUNT(DISTINCT NULLIF(DestinationObjectName, '')) AS destination_object_count,
                       COUNT(DISTINCT NULLIF(DestinationKey, '')) AS destination_key_count
                FROM ctl.MddImportSourceRow
                WHERE ImportBatchId = %s
                """,
                (batch_id,),
            )
            source_summary = cursor.fetchone()
            cursor.execute(
                """
                SELECT DISTINCT DestinationObjectName
                FROM ctl.MddImportSourceRow
                WHERE ImportBatchId = %s AND DestinationObjectName IS NOT NULL
                ORDER BY DestinationObjectName
                """,
                (batch_id,),
            )
            destination_objects = [
                str(_row_value(row, "DestinationObjectName", 0))
                for row in cursor.fetchall()
                if _row_value(row, "DestinationObjectName", 0) is not None
            ]
            inserted_lineage_count = int(_row_value(source_summary, "inserted_lineage_count", 1) or 0)
            overlap_lineage_count = int(_row_value(source_summary, "overlap_lineage_count", 2) or 0)
            destination_key_count = int(_row_value(source_summary, "destination_key_count", 4) or 0)
            partial_evidence = inserted_lineage_count > 0
            # A source-lineage row is unique across all batches for this exact
            # source SHA.  That makes a subsequent run a reconciliation/resume
            # operation, provided no INSERTED lineage row lost its destination.
            inconsistent_inserted_rows = sum(
                1
                for row in table_audits
                if isinstance(row, Mapping)
                and str(row.get("ReconciliationStatus")) == "FAIL"
                and int(row.get("InsertedRowCount") or 0) > int(row.get("DestinationRowCount") or 0)
            )
            audited.append(
                {
                    "import_batch_id": str(batch_id),
                    "started_at": _row_value(batch, "ImportStartedAtUtc", 1),
                    "completed_at": _row_value(batch, "ImportCompletedAtUtc", 2),
                    "failure_reason": _row_value(batch, "FailureReason", 3),
                    "source_counts": _json_object(_row_value(batch, "SourceCountsJson", 4)),
                    "inserted_counts": _json_object(_row_value(batch, "InsertedCountsJson", 5)),
                    "duplicate_counts": _json_object(_row_value(batch, "DuplicateCountsJson", 6)),
                    "overlap_counts": _json_object(_row_value(batch, "OverlapCountsJson", 7)),
                    "reconciliation_result": _json_object(_row_value(batch, "ReconciliationResultJson", 8)),
                    "importer_version": _row_value(batch, "ImporterVersion", 9),
                    "source_row_count": int(_row_value(source_summary, "source_row_count", 0) or 0),
                    "inserted_lineage_count": inserted_lineage_count,
                    "overlap_lineage_count": overlap_lineage_count,
                    "destination_objects_touched": destination_objects,
                    "destination_key_count": destination_key_count,
                    "table_audits": table_audits,
                    "evidence_audits": evidence_audits,
                    "partial_destination_evidence_committed": partial_evidence,
                    "resume_reconcile_safe": inconsistent_inserted_rows == 0,
                    "history_immutable": True,
                }
            )
        return {
            "status": "READ_ONLY_COMPLETE",
            "source_sha256": source_sha256,
            "failed_batch_count": len(audited),
            "failed_batches": audited,
            "all_failed_batches_immutable": True,
            "resume_reconcile_safe": all(item["resume_reconcile_safe"] for item in audited),
        }
    finally:
        connection.close()


def read_only_capacity_audit(settings: MssqlConnectionSettings, proof: SQLiteSourceProof) -> dict[str, Any]:
    """Run the complete read-only SQL Server capacity preflight."""

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        # Keep the tuple fallback order aligned with _capacity_file_row().
        cursor.execute(
            """
            SELECT
                CAST(f.size AS bigint) * 8192 AS allocated_bytes,
                FILEPROPERTY(f.name, 'SpaceUsed') AS space_used_pages,
                f.physical_name,
                v.volume_mount_point,
                f.name AS logical_name,
                CASE WHEN f.max_size = -1 THEN NULL ELSE CAST(f.max_size AS bigint) * 8192 END AS max_size_bytes,
                f.growth,
                f.is_percent_growth,
                v.total_bytes,
                v.available_bytes
            FROM sys.database_files AS f
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), f.file_id) AS v
            WHERE f.type_desc = 'ROWS'
            ORDER BY f.file_id
            """
        )
        rows_files = [_capacity_file_row(row, file_type="ROWS") for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT
                CAST(f.size AS bigint) * 8192 AS allocated_bytes,
                NULL AS space_used_pages,
                f.physical_name,
                v.volume_mount_point,
                f.name AS logical_name,
                CASE WHEN f.max_size = -1 THEN NULL ELSE CAST(f.max_size AS bigint) * 8192 END AS max_size_bytes,
                f.growth,
                f.is_percent_growth,
                v.total_bytes,
                v.available_bytes
            FROM sys.database_files AS f
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), f.file_id) AS v
            WHERE f.type_desc = 'LOG'
            ORDER BY f.file_id
            """
        )
        log_files = [_capacity_file_row(row, file_type="LOG") for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT d.recovery_model_desc, d.log_reuse_wait_desc,
                   CAST(u.total_log_size_in_bytes AS bigint) AS total_log_size_in_bytes,
                   CAST(u.used_log_space_in_bytes AS bigint) AS used_log_space_in_bytes,
                   CAST(u.used_log_space_in_percent AS decimal(18,6)) AS used_log_space_in_percent
            FROM sys.databases AS d
            CROSS JOIN sys.dm_db_log_space_usage AS u
            WHERE d.database_id = DB_ID()
            """
        )
        usage_row = cursor.fetchone()
        if usage_row is None:
            raise SQLiteImportError("SQL Server returned no transaction-log capacity row")
        log_usage = (
            dict(usage_row)
            if isinstance(usage_row, Mapping)
            else {
                "recovery_model_desc": _row_value(usage_row, "recovery_model_desc", 0),
                "log_reuse_wait_desc": _row_value(usage_row, "log_reuse_wait_desc", 1),
                "total_log_size_in_bytes": _row_value(usage_row, "total_log_size_in_bytes", 2),
                "used_log_space_in_bytes": _row_value(usage_row, "used_log_space_in_bytes", 3),
                "used_log_space_in_percent": _row_value(usage_row, "used_log_space_in_percent", 4),
            }
        )
        return _capacity_report(
            proof=proof,
            rows_files=rows_files,
            log_files=log_files,
            log_usage=log_usage,
        )
    finally:
        connection.close()


__all__ = [
    "EXPECTED_SQL_DATABASE",
    "EXPECTED_SQLITE_SCHEMA_VERSION",
    "CONSERVATIVE_IMPORT_MULTIPLIER",
    "FULL_IMPORT_TABLES",
    "IMPORTER_VERSION",
    "ImportCounters",
    "LINEAGE_ONLY_TABLE_REASONS",
    "SQLiteImportError",
    "SQLiteSourceProof",
    "SQLiteToMssqlImporter",
    "SourceTableInventory",
    "inspect_sqlite_source",
    "read_only_capacity_audit",
    "read_only_failed_import_audit",
    "read_only_overlap_audit",
]

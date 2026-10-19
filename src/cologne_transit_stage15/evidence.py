"""Read-only evidence adapters for Stage 15 validation.

Only the adapter knows how evidence is stored.  The analyzer consumes the
small, row-oriented :class:`EvidenceData` shape and can therefore be reused
with a later storage adapter without changing validation logic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class EvidenceAdapterError(RuntimeError):
    """Raised when an evidence adapter cannot provide a read-only view."""


@dataclass(frozen=True)
class EvidenceData:
    """Backend-neutral, sanitized rows used by the analyzer.

    Rows are mappings rather than backend-specific model instances.  This is
    intentional: a future production adapter can expose the same evidence
    vocabulary while keeping its connection, transaction, and SQL details
    private.
    """

    source: str
    available: bool = True
    limitations: tuple[str, ...] = ()
    manifest_targets: tuple[Mapping[str, Any], ...] = ()
    collector_runs: tuple[Mapping[str, Any], ...] = ()
    dispatcher_claims: tuple[Mapping[str, Any], ...] = ()
    dispatcher_claim_events: tuple[Mapping[str, Any], ...] = ()
    target_claims: tuple[Mapping[str, Any], ...] = ()
    claim_events: tuple[Mapping[str, Any], ...] = ()
    target_audits: tuple[Mapping[str, Any], ...] = ()
    http_attempts: tuple[Mapping[str, Any], ...] = ()
    parsed_snapshots: tuple[Mapping[str, Any], ...] = ()
    quota_audit: tuple[Mapping[str, Any], ...] = ()
    quota_permits: tuple[Mapping[str, Any], ...] = ()
    shared_usage: tuple[Mapping[str, Any], ...] = ()
    sampling_eras: tuple[Mapping[str, Any], ...] = ()
    recorded_at_time_evidence: tuple[Mapping[str, Any], ...] = ()
    timing_observations: tuple[Mapping[str, Any], ...] = ()
    timing_recorded_at_time_reporting: tuple[Mapping[str, Any], ...] = ()
    service_section_observations: tuple[Mapping[str, Any], ...] = ()
    raw_evidence: tuple[Mapping[str, Any], ...] = ()
    database_metadata: tuple[Mapping[str, Any], ...] = ()

    @property
    def has_execution_evidence(self) -> bool:
        return bool(
            self.collector_runs
            or self.dispatcher_claims
            or self.target_claims
            or self.target_audits
            or self.http_attempts
        )


class EvidenceAdapter(Protocol):
    """Read-only adapter contract consumed by :class:`Stage15Analyzer`."""

    source: str
    read_only: bool

    def read_evidence(self) -> EvidenceData:
        """Return evidence without mutating collector state."""


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f"SELECT * FROM {table}")
    return [dict(row) for row in cursor.fetchall()]


class SQLiteEvidenceAdapter:
    """Read a Stage 14 SQLite evidence file without opening it for writes."""

    source = "sqlite-development"
    read_only = True

    _TABLES = (
        "manifest_targets",
        "collector_runs",
        "dispatcher_claims",
        "dispatcher_claim_events",
        "target_claims",
        "claim_events",
        "target_audits",
        "http_attempts",
        "parsed_snapshots",
        "quota_audit",
        "quota_permits",
        "shared_usage",
        "sampling_eras",
        "recorded_at_time_evidence",
        "timing_observations",
        "timing_recorded_at_time_reporting",
        "service_section_observations",
        "raw_evidence",
        "database_metadata",
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.exists():
            raise EvidenceAdapterError(f"SQLite evidence file does not exist: {self.path}")
        if not self.path.is_file():
            raise EvidenceAdapterError(f"SQLite evidence path is not a file: {self.path}")

    def _connect(self) -> sqlite3.Connection:
        # ``mode=ro`` is the important boundary.  The adapter never invokes
        # CREATE, INSERT, UPDATE, DELETE, transaction, or checkpoint SQL.
        uri = f"file:{self.path.as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=15.0)
        except sqlite3.Error as exc:
            raise EvidenceAdapterError(f"cannot open SQLite evidence read-only: {self.path}") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def read_evidence(self) -> EvidenceData:
        connection = self._connect()
        limitations: list[str] = []
        values: dict[str, tuple[Mapping[str, Any], ...]] = {}
        try:
            for table in self._TABLES:
                try:
                    values[table] = tuple(_rows(connection, table))
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc).lower():
                        values[table] = ()
                        limitations.append(f"SQLite table unavailable: {table}")
                    else:
                        raise EvidenceAdapterError(f"cannot read SQLite evidence table: {table}") from exc
        finally:
            connection.close()
        metadata_rows = values.get("database_metadata", ())
        role = next(
            (
                str(row.get("metadata_value"))
                for row in metadata_rows
                if str(row.get("metadata_key")) == "database_role"
            ),
            None,
        )
        source = "sqlite-live-research" if role == "LIVE_RESEARCH" else self.source
        return EvidenceData(
            source=source,
            available=True,
            limitations=tuple(limitations),
            manifest_targets=values.get("manifest_targets", ()),
            collector_runs=values.get("collector_runs", ()),
            dispatcher_claims=values.get("dispatcher_claims", ()),
            dispatcher_claim_events=values.get("dispatcher_claim_events", ()),
            target_claims=values.get("target_claims", ()),
            claim_events=values.get("claim_events", ()),
            target_audits=values.get("target_audits", ()),
            http_attempts=values.get("http_attempts", ()),
            parsed_snapshots=values.get("parsed_snapshots", ()),
            quota_audit=values.get("quota_audit", ()),
            quota_permits=values.get("quota_permits", ()),
            shared_usage=values.get("shared_usage", ()),
            sampling_eras=values.get("sampling_eras", ()),
            recorded_at_time_evidence=values.get("recorded_at_time_evidence", ()),
            timing_observations=values.get("timing_observations", ()),
            timing_recorded_at_time_reporting=values.get("timing_recorded_at_time_reporting", ()),
            service_section_observations=values.get("service_section_observations", ()),
            raw_evidence=values.get("raw_evidence", ()),
            database_metadata=values.get("database_metadata", ()),
        )

    def __enter__(self) -> "SQLiteEvidenceAdapter":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None


@dataclass
class InMemoryEvidenceAdapter:
    """Deterministic test/fixture adapter with the same read-only contract."""

    evidence: EvidenceData
    source: str = "deterministic-fixture"
    read_only: bool = True

    def read_evidence(self) -> EvidenceData:
        return self.evidence


def evidence_from_mappings(
    *,
    source: str = "deterministic-fixture",
    available: bool = True,
    limitations: Sequence[str] = (),
    **tables: Sequence[Mapping[str, Any]],
) -> EvidenceData:
    """Build an evidence object for deterministic offline fixtures.

    Unknown table names are rejected so fixture authors cannot accidentally
    create an analyzer input that the backend-neutral contract cannot describe.
    """

    allowed = {
        "manifest_targets",
        "collector_runs",
        "dispatcher_claims",
        "dispatcher_claim_events",
        "target_claims",
        "claim_events",
        "target_audits",
        "http_attempts",
        "parsed_snapshots",
        "quota_audit",
        "quota_permits",
        "shared_usage",
        "sampling_eras",
        "recorded_at_time_evidence",
        "timing_observations",
        "timing_recorded_at_time_reporting",
        "service_section_observations",
        "raw_evidence",
        "database_metadata",
    }
    unknown = set(tables) - allowed
    if unknown:
        raise ValueError(f"unknown evidence table(s): {sorted(unknown)}")
    return EvidenceData(
        source=source,
        available=available,
        limitations=tuple(str(item) for item in limitations),
        **{name: tuple(tables.get(name, ())) for name in allowed},
    )

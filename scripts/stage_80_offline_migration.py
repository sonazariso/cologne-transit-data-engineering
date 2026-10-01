#!/usr/bin/env python3
"""Perform the S80 SQLite migration/backfill without transport activity.

This script is intentionally limited to an existing LIVE_RESEARCH SQLite file.
It never imports a network client, reads credentials, starts a collector, or
creates replacement S78 evidence when the requested database is absent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import PersistenceProfile
from cologne_transit_collector.persistence import SCHEMA_VERSION, SQLitePersistence


DAILY_BACKUP_PATTERN = re.compile(r"^collector-\d{4}-\d{2}-\d{2}\.sqlite3$")
COLLECTOR_MARKERS = (
    "stage_14_collector.py",
    "cologne_transit_collector.cli",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_only_summary(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        metadata = {
            str(row["metadata_key"]): str(row["metadata_value"])
            for row in connection.execute(
                "SELECT metadata_key, metadata_value FROM database_metadata ORDER BY metadata_key"
            )
        }
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        counts: dict[str, int] = {}
        for table in (
            "parsed_snapshots",
            "timing_observations",
            "recorded_at_time_evidence",
            "service_section_observations",
        ):
            if table in tables:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "schema_version": metadata.get("schema_version"),
            "metadata": metadata,
            "integrity_check": integrity,
            "quick_check": quick,
            "tables": sorted(tables),
            "counts": counts,
        }
    finally:
        connection.close()


def _verify_corrected_reporting_view(path: Path) -> dict[str, Any]:
    """Verify the schema-5 view uses exact RecordedAtTime leaf lineage."""

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        definitions = {
            str(row["name"]): str(row["sql"] or "")
            for row in connection.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'view'
                  AND name IN ('timing_recorded_at_time_reporting', 'recorded_at_time_reporting')
                """
            )
        }
        required = {"timing_recorded_at_time_reporting", "recorded_at_time_reporting"}
        if set(definitions) != required:
            raise RuntimeError("schema-5 RecordedAtTime reporting views are incomplete")
        if "timing_recorded_at_time_reporting" not in definitions[
            "recorded_at_time_reporting"
        ]:
            raise RuntimeError("RecordedAtTime compatibility alias is not linked to the current view")
        timing_sql = definitions["timing_recorded_at_time_reporting"]
        for required_text in (
            "recorded_at_time_source_path",
            "LatestSourceRecordedAtTimeSourcePath",
            "LastObservedRecordedAtTimeSourcePath",
            "FirstObservedRecordedAtTimeSourcePath",
        ):
            if required_text not in timing_sql:
                raise RuntimeError(
                    f"corrected reporting view does not expose {required_text}"
                )

        timing_rows = {
            int(row["timing_observation_id"]): row
            for row in connection.execute("SELECT * FROM timing_observations")
        }
        reporting_rows = connection.execute(
            "SELECT * FROM timing_recorded_at_time_reporting"
        ).fetchall()
        path_checks = (
            ("LatestSourceRecordedAtTimeObservationId", "LatestSourceRecordedAtTimeSourcePath"),
            ("LastObservedRecordedAtTimeObservationId", "LastObservedRecordedAtTimeSourcePath"),
            ("FirstObservedRecordedAtTimeObservationId", "FirstObservedRecordedAtTimeSourcePath"),
        )
        for report in reporting_rows:
            for observation_id_field, source_path_field in path_checks:
                observation_id = report[observation_id_field]
                if observation_id is None:
                    expected_path = None
                else:
                    observation = timing_rows.get(int(observation_id))
                    if observation is None:
                        raise RuntimeError(
                            f"reporting view references missing timing observation {observation_id}"
                        )
                    expected_path = observation["recorded_at_time_source_path"]
                    if source_path_field.startswith("Latest") and observation["recorded_at_time"] is None:
                        expected_path = None
                if report[source_path_field] != expected_path:
                    raise RuntimeError(
                        f"{source_path_field} does not match recorded_at_time_source_path"
                    )
        return {
            "status": "PASS",
            "reporting_row_count": len(reporting_rows),
            "view_names": sorted(definitions),
        }
    finally:
        connection.close()


def _ensure_no_live_collector_process() -> None:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("could not verify that no live collector process is running") from exc
    current_pid = str(os.getpid())
    active = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith(current_pid + " "):
            continue
        if any(marker in text for marker in COLLECTOR_MARKERS) and "stage_80_offline_migration.py" not in text:
            active.append(text)
    if active:
        raise RuntimeError("live collector process detected; migration refused")


def _validated_consistent_backup(path: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".s80-pre-migration-",
        suffix=".sqlite3",
        dir=str(path.parent),
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary_sidecars = tuple(
        Path(f"{temporary}{suffix}") for suffix in ("-wal", "-shm", "-journal")
    )
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        destination = sqlite3.connect(str(temporary))
        try:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode = DELETE")
            destination.commit()
            result = str(destination.execute("PRAGMA integrity_check").fetchone()[0])
            if result.casefold() != "ok":
                raise RuntimeError(f"temporary pre-migration backup failed integrity check: {result}")
        finally:
            destination.close()
            source.close()
        for sidecar in temporary_sidecars:
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        return temporary
    except Exception:
        for candidate in (temporary, *temporary_sidecars):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        raise


def _daily_backups(path: Path) -> list[str]:
    backup_dir = path.parent / "backups"
    if not backup_dir.is_dir():
        return []
    return sorted(
        candidate.name
        for candidate in backup_dir.iterdir()
        if candidate.is_file() and DAILY_BACKUP_PATTERN.fullmatch(candidate.name)
    )


def migrate(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "UNAVAILABLE",
            "database_path": str(path),
            "message": "LIVE_RESEARCH database is absent; no replacement S78 evidence was created.",
            "offline_fixtures": [
                str(ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.json"),
                str(ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.xml"),
                str(ROOT / "tests" / "test_stage_80_corrections.py"),
            ],
        }
    if not path.is_file():
        raise RuntimeError(f"LIVE_RESEARCH database path is not a file: {path}")

    _ensure_no_live_collector_process()
    pre = _read_only_summary(path)
    if pre["integrity_check"].casefold() != "ok" or pre["quick_check"].casefold() != "ok":
        raise RuntimeError("pre-migration SQLite integrity/quick check failed")
    safety_backup = _validated_consistent_backup(path)
    persistence: SQLitePersistence | None = None
    try:
        persistence = SQLitePersistence(
            path,
            profile=PersistenceProfile.LIVE_RESEARCH,
            repository_root=ROOT,
            backup_dir=path.parent / "backups",
            raw_evidence_dir=path.parent / "raw-evidence",
        )
        backfill = persistence.backfill_existing_snapshots()
        reconciliation = persistence.reconcile_recorded_at_time_evidence()
        post_integrity = persistence.integrity_check(quick=False)
        backup = persistence.backup_live_database(now_utc=_utc_now())
        if backup.get("status") != "COMPLETE":
            raise RuntimeError("validated current-day backup could not be refreshed")
        backup_path = Path(str(backup["backup_path"]))
        backup_connection = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
        try:
            backup_integrity = str(backup_connection.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            backup_connection.close()
        if backup_integrity.casefold() != "ok":
            raise RuntimeError("refreshed daily backup failed integrity check")
        post = _read_only_summary(path)
        required_count_tables = (
            "parsed_snapshots",
            "timing_observations",
            "recorded_at_time_evidence",
            "service_section_observations",
        )
        count_comparison = {
            table: {
                "before": pre["counts"].get(table),
                "after": post["counts"].get(table),
                "unchanged": pre["counts"].get(table) == post["counts"].get(table),
            }
            for table in required_count_tables
        }
        missing_counts = [
            table
            for table, values in count_comparison.items()
            if values["before"] is None or values["after"] is None
        ]
        changed_counts = [
            table
            for table, values in count_comparison.items()
            if not values["unchanged"]
        ]
        if missing_counts or changed_counts:
            raise RuntimeError(
                "required durable row counts changed or were unavailable: "
                + json.dumps(count_comparison, sort_keys=True)
            )
        if reconciliation.get("status") != "PASS":
            raise RuntimeError("RecordedAtTime reconciliation failed")
        reporting_view = _verify_corrected_reporting_view(path)
        result = {
            "status": "COMPLETE",
            "database_path": str(path),
            "pre_migration": pre,
            "post_migration": post,
            "schema_version_expected": SCHEMA_VERSION,
            "backfill": backfill,
            "required_row_counts": count_comparison,
            "recorded_at_time_reconciliation": reconciliation,
            "corrected_reporting_view": reporting_view,
            "post_migration_integrity_check": post_integrity,
            "refreshed_daily_backup": {
                "path": str(backup_path),
                "integrity_check": backup_integrity,
                "permanent_daily_backups": _daily_backups(path),
            },
            "temporary_safety_backup": str(safety_backup),
            "network_activity": {"mdd_requests": 0, "dns_activity": 0, "live_network_attempts": 0},
        }
        persistence.close()
        persistence = None
        safety_backup.unlink()
        result["temporary_safety_backup"] = {"path": str(safety_backup), "removed_after_validation": True}
        return result
    finally:
        if persistence is not None:
            persistence.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path.home() / "cologne-transit-data" / "live-research" / "collector.sqlite3",
    )
    args = parser.parse_args(argv)
    try:
        result = migrate(args.database.expanduser().resolve())
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__, "message": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

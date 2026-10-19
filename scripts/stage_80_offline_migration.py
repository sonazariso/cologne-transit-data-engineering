#!/usr/bin/env python3
"""Perform the S80 SQLite migration/backfill without transport activity.

This script is intentionally limited to an existing LIVE_RESEARCH SQLite file.
It never imports a network client, reads credentials, starts a collector, or
creates replacement S78 evidence when the requested database is absent.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import shutil
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _rows_digest(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(sorted(_canonical_json(row) for row in rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _table_rows(path: Path, table: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        if table not in tables:
            return []
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
    finally:
        connection.close()


def _schema_number(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_diagnostic(exc: BaseException) -> dict[str, str]:
    message = " ".join(str(exc).split())
    message = re.sub(r"(?i)(password|token|secret|authorization|api[_-]?key)\s*[=:]\s*[^,; ]+", r"\1=<redacted>", message)
    return {"error": type(exc).__name__, "message": message[:1000]}


def _remove_sqlite_sidecars(path: Path) -> None:
    for sidecar in (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    ):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass


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
        table_digests: dict[str, str] = {}
        snapshot_json_digest: str | None = None
        for table in (
            "parsed_snapshots",
            "timing_observations",
            "recorded_at_time_evidence",
            "service_section_observations",
        ):
            if table in tables:
                rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
                counts[table] = len(rows)
                table_digests[table] = _rows_digest(rows)
                if table == "parsed_snapshots":
                    snapshot_json_digest = _rows_digest(
                        [
                            {
                                "snapshot_id": row.get("snapshot_id"),
                                "snapshot_json": row.get("snapshot_json"),
                            }
                            for row in rows
                        ]
                    )
        return {
            "schema_version": metadata.get("schema_version"),
            "metadata": metadata,
            "integrity_check": integrity,
            "quick_check": quick,
            "tables": sorted(tables),
            "counts": counts,
            "table_digests": table_digests,
            "snapshot_json_digest": snapshot_json_digest,
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
        reported_leaf_paths = sorted(
            {
                str(report[field])
                for report in reporting_rows
                for field in (
                    "LatestSourceRecordedAtTimeSourcePath",
                    "LastObservedRecordedAtTimeSourcePath",
                    "FirstObservedRecordedAtTimeSourcePath",
                )
                if report[field] is not None
            }
        )
        return {
            "status": "PASS",
            "reporting_row_count": len(reporting_rows),
            "view_names": sorted(definitions),
            "recorded_at_time_source_paths": reported_leaf_paths,
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
            quick_result = str(destination.execute("PRAGMA quick_check").fetchone()[0])
            if quick_result.casefold() != "ok":
                raise RuntimeError(
                    f"temporary pre-migration backup failed quick check: {quick_result}"
                )
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


def _restore_primary_from_safety_backup(primary: Path, safety_backup: Path) -> dict[str, Any]:
    """Restore the primary file through a same-directory atomic replacement."""

    if not safety_backup.is_file():
        raise RuntimeError("validated migration safety backup is unavailable")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".s80-restore-",
        suffix=".sqlite3",
        dir=str(primary.parent),
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    displaced: Path | None = None
    replacement_installed = False
    try:
        shutil.copyfile(safety_backup, temporary)
        with temporary.open("rb") as restored_file:
            os.fsync(restored_file.fileno())
        restored_summary = _read_only_summary(temporary)
        if (
            restored_summary["integrity_check"].casefold() != "ok"
            or restored_summary["quick_check"].casefold() != "ok"
        ):
            raise RuntimeError("safety backup failed integrity verification before restore")

        if primary.exists():
            descriptor, displaced_name = tempfile.mkstemp(
                prefix=f".{primary.name}.s80-displaced-",
                suffix=".sqlite3",
                dir=str(primary.parent),
            )
            os.close(descriptor)
            displaced = Path(displaced_name)
            displaced.unlink()
            os.replace(primary, displaced)
        _remove_sqlite_sidecars(primary)
        os.replace(temporary, primary)
        temporary = Path()
        replacement_installed = True

        verified = _read_only_summary(primary)
        if (
            verified["integrity_check"].casefold() != "ok"
            or verified["quick_check"].casefold() != "ok"
        ):
            raise RuntimeError("restored primary database failed integrity verification")
        if displaced is not None:
            displaced.unlink()
            displaced = None
        return {
            "status": "RESTORED",
            "database_path": str(primary),
            "integrity_check": verified["integrity_check"],
            "quick_check": verified["quick_check"],
        }
    except Exception:
        # If the replacement or its verification failed, put the displaced
        # primary back where it was.  The validated safety backup is retained
        # by the caller regardless of this recovery attempt's outcome.
        if replacement_installed and primary.exists():
            try:
                primary.unlink()
            except OSError:
                pass
        if displaced is not None and displaced.exists():
            try:
                os.replace(displaced, primary)
                displaced = None
            except OSError:
                pass
        raise
    finally:
        if temporary and temporary != Path():
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        if displaced is not None:
            try:
                displaced.unlink()
            except FileNotFoundError:
                pass


def _daily_backups(path: Path) -> list[str]:
    backup_dir = path.parent / "backups"
    if not backup_dir.is_dir():
        return []
    return sorted(
        candidate.name
        for candidate in backup_dir.iterdir()
        if candidate.is_file() and DAILY_BACKUP_PATTERN.fullmatch(candidate.name)
    )


def _validate_count_reconciliation(
    *,
    source_schema_version: int | None,
    pre: dict[str, Any],
    post: dict[str, Any],
    backfill: dict[str, Any],
) -> dict[str, Any]:
    required_tables = (
        "parsed_snapshots",
        "timing_observations",
        "recorded_at_time_evidence",
        "service_section_observations",
    )
    pre_counts = pre["counts"]
    post_counts = post["counts"]
    comparison: dict[str, Any] = {}

    for table in required_tables:
        before = pre_counts.get(table)
        after = post_counts.get(table)
        delta = None if before is None or after is None else int(after) - int(before)
        comparison[table] = {
            "before": before,
            "after": after,
            "delta": delta,
            "unchanged": before is not None and after is not None and delta == 0,
        }

    if source_schema_version == 4:
        invalid = [
            table
            for table, values in comparison.items()
            if values["before"] is None
            or values["after"] is None
            or not values["unchanged"]
        ]
        if invalid:
            raise RuntimeError(
                "schema-4 durable row counts were not preserved: "
                + json.dumps(comparison, sort_keys=True)
            )
        return {
            "source_schema_version": source_schema_version,
            "rule": "all_four_durable_tables_unchanged",
            "status": "PASS",
            "tables": comparison,
            **comparison,
        }

    if source_schema_version in {2, 3}:
        for table in ("timing_observations", "service_section_observations"):
            values = comparison[table]
            if values["before"] is None and values["after"] is not None:
                # These projection tables legitimately did not exist in the
                # oldest contracts.  Their effective pre-migration count is
                # zero for reconciliation purposes, while the raw value stays
                # None in the diagnostic report to show that absence.
                values["delta"] = int(values["after"])
        for table in ("parsed_snapshots", "recorded_at_time_evidence"):
            values = comparison[table]
            if values["before"] is None or values["after"] is None:
                raise RuntimeError(
                    f"required schema-{source_schema_version} table is unavailable: {table}"
                )
            if table == "parsed_snapshots" and not values["unchanged"]:
                raise RuntimeError(
                    "parsed snapshot row count changed during historical migration: "
                    + json.dumps(values, sort_keys=True)
                )

        expected_projection_increases = {
            "timing_observations": int(backfill.get("timing_rows_inserted", 0) or 0),
            "service_section_observations": int(
                backfill.get("service_section_rows_inserted", 0) or 0
            ),
        }
        for table, expected in expected_projection_increases.items():
            values = comparison[table]
            if values["after"] is None:
                raise RuntimeError(
                    f"historical migration did not create required projection table: {table}"
                )
            values["expected_backfill_increase"] = expected
            values["reconciles_with_backfill"] = values["delta"] == expected
            if not values["reconciles_with_backfill"]:
                raise RuntimeError(
                    "historical projection count does not reconcile with backfill report: "
                    + json.dumps(comparison, sort_keys=True)
                )
        comparison["recorded_at_time_evidence"]["original_rows_may_increase"] = True
        return {
            "source_schema_version": source_schema_version,
            "rule": "snapshots_and_original_recorded_evidence_preserved_projection_increases_reconciled",
            "status": "PASS",
            "tables": comparison,
            **comparison,
        }

    if source_schema_version == int(SCHEMA_VERSION):
        invalid = [
            table
            for table, values in comparison.items()
            if values["before"] is None
            or values["after"] is None
            or not values["unchanged"]
        ]
        if invalid:
            raise RuntimeError(
                "current-schema durable row counts changed unexpectedly: "
                + json.dumps(comparison, sort_keys=True)
            )
        return {
            "source_schema_version": source_schema_version,
            "rule": "all_four_durable_tables_unchanged",
            "status": "PASS",
            "tables": comparison,
            **comparison,
        }

    raise RuntimeError(
        f"unsupported source schema for offline migration validation: {source_schema_version}"
    )


def _validate_row_preservation(
    *,
    source_schema_version: int | None,
    pre: dict[str, Any],
    post: dict[str, Any],
    original_recorded_rows: list[dict[str, Any]],
    migrated_recorded_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot_unchanged = (
        pre.get("snapshot_json_digest") == post.get("snapshot_json_digest")
    )
    if not snapshot_unchanged:
        raise RuntimeError("original parsed snapshot JSON or durable snapshot rows changed")

    original_columns = set().union(*(row.keys() for row in original_recorded_rows))
    migrated_row_signatures = Counter(
        _canonical_json({key: row.get(key) for key in original_columns})
        for row in migrated_recorded_rows
    )
    missing_recorded_rows = []
    for original_row in original_recorded_rows:
        # Additive migrations may add a defaulted column to an older evidence
        # table.  Compare the original row's columns only so that this check
        # proves preservation without mistaking additive schema work for loss.
        projected = {
            key: original_row.get(key)
            for key in original_row
        }
        signature = _canonical_json(projected)
        if migrated_row_signatures[signature] == 0:
            missing_recorded_rows.append(signature)
        else:
            migrated_row_signatures[signature] -= 1
    if missing_recorded_rows:
        raise RuntimeError("original RecordedAtTime evidence rows were lost during migration")

    evidence_exact = (
        source_schema_version == 4
        and pre["table_digests"].get("recorded_at_time_evidence")
        == post["table_digests"].get("recorded_at_time_evidence")
    )
    if source_schema_version == 4 and not evidence_exact:
        raise RuntimeError("schema-4 RecordedAtTime evidence rows changed unexpectedly")
    return {
        "status": "PASS",
        "parsed_snapshot_rows_unchanged": snapshot_unchanged,
        "original_parsed_snapshot_digest": pre.get("snapshot_json_digest"),
        "migrated_parsed_snapshot_digest": post.get("snapshot_json_digest"),
        "original_recorded_at_time_evidence_count": len(original_recorded_rows),
        "migrated_recorded_at_time_evidence_count": len(migrated_recorded_rows),
        "original_recorded_at_time_evidence_preserved": not missing_recorded_rows,
        "recorded_at_time_evidence_unchanged": evidence_exact
        if source_schema_version == 4
        else None,
    }


def _validate_migration_metadata(
    *, source_schema_version: int | None, pre: dict[str, Any], post: dict[str, Any]
) -> dict[str, Any]:
    metadata = post["metadata"]
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("migration completion metadata does not report the current schema")
    if metadata.get("schema_migration_status") != "COMPLETE":
        raise RuntimeError("migration completion metadata is not durably complete")
    if source_schema_version in {2, 3, 4}:
        if metadata.get("schema_migration_source_version") != str(source_schema_version):
            raise RuntimeError("migration source schema metadata is missing or incorrect")
        before_timestamp = pre["metadata"].get("last_schema_migration_at_utc")
        after_timestamp = metadata.get("last_schema_migration_at_utc")
        if not after_timestamp or after_timestamp == before_timestamp:
            raise RuntimeError("schema migration timestamp was not updated exactly once")
    return {
        "status": "PASS",
        "schema_version": metadata.get("schema_version"),
        "schema_migration_status": metadata.get("schema_migration_status"),
        "schema_migration_source_version": metadata.get("schema_migration_source_version"),
        "last_schema_migration_at_utc": metadata.get("last_schema_migration_at_utc"),
    }


def _verify_backup_integrity(path: Path) -> dict[str, Any]:
    summary = _read_only_summary(path)
    if summary["integrity_check"].casefold() != "ok" or summary["quick_check"].casefold() != "ok":
        raise RuntimeError("refreshed daily backup failed integrity or quick check")
    return {
        "path": str(path),
        "integrity_check": summary["integrity_check"],
        "quick_check": summary["quick_check"],
    }


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

    safety_backup: Path | None = None
    persistence: SQLitePersistence | None = None
    primary_may_have_changed = False
    pre: dict[str, Any] | None = None
    try:
        _ensure_no_live_collector_process()
        pre = _read_only_summary(path)
        if pre["integrity_check"].casefold() != "ok" or pre["quick_check"].casefold() != "ok":
            raise RuntimeError("pre-migration SQLite integrity/quick check failed")
        source_schema_version = _schema_number(pre["schema_version"])
        safety_backup = _validated_consistent_backup(path)
        primary_may_have_changed = True
        original_recorded_rows = _table_rows(path, "recorded_at_time_evidence")
        persistence = SQLitePersistence(
            path,
            profile=PersistenceProfile.LIVE_RESEARCH,
            repository_root=ROOT,
            backup_dir=path.parent / "backups",
            raw_evidence_dir=path.parent / "raw-evidence",
        )
        backfill = dict(persistence.migration_report())
        if not backfill:
            backfill = dict(persistence.backfill_existing_snapshots())
        if backfill.get("status") == "FAILED":
            raise RuntimeError("historical projection backfill failed")
        # A second pass is part of the migration validation contract.  It
        # should be a no-op because all projection grains are idempotent.
        backfill_rerun = dict(persistence.backfill_existing_snapshots())
        if backfill_rerun.get("status") == "FAILED":
            raise RuntimeError("historical projection backfill rerun failed")
        if int(backfill_rerun.get("timing_rows_inserted", 0) or 0) != 0:
            raise RuntimeError("timing projection backfill was not idempotent")
        if int(backfill_rerun.get("service_section_rows_inserted", 0) or 0) != 0:
            raise RuntimeError("ServiceSection projection backfill was not idempotent")
        reconciliation = persistence.reconcile_recorded_at_time_evidence()
        if reconciliation.get("status") != "PASS":
            raise RuntimeError("RecordedAtTime reconciliation failed")
        post_integrity = persistence.integrity_check(quick=False)
        post_quick_check = persistence.integrity_check(quick=True)
        if post_integrity.casefold() != "ok" or post_quick_check.casefold() != "ok":
            raise RuntimeError("post-migration integrity verification failed")
        post = _read_only_summary(path)
        if post["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("post-migration schema version is not current")
        if post["integrity_check"].casefold() != "ok" or post["quick_check"].casefold() != "ok":
            raise RuntimeError("post-migration SQLite integrity/quick check failed")
        count_comparison = _validate_count_reconciliation(
            source_schema_version=source_schema_version,
            pre=pre,
            post=post,
            backfill=backfill,
        )
        migrated_recorded_rows = _table_rows(path, "recorded_at_time_evidence")
        preservation = _validate_row_preservation(
            source_schema_version=source_schema_version,
            pre=pre,
            post=post,
            original_recorded_rows=original_recorded_rows,
            migrated_recorded_rows=migrated_recorded_rows,
        )
        migration_metadata = _validate_migration_metadata(
            source_schema_version=source_schema_version,
            pre=pre,
            post=post,
        )
        reporting_view = _verify_corrected_reporting_view(path)
        if reporting_view.get("status") != "PASS":
            raise RuntimeError("reporting-view validation failed")

        # The primary database has now passed every migration check.  This is
        # deliberately the first point at which the normal rotating backup is
        # allowed to publish a current-day replacement.
        backup = persistence.backup_live_database(now_utc=_utc_now())
        if backup.get("status") != "COMPLETE":
            raise RuntimeError(
                "validated current-day backup could not be refreshed: "
                + json.dumps(dict(backup), sort_keys=True, default=str)
            )
        backup_path = Path(str(backup["backup_path"]))
        backup_integrity = _verify_backup_integrity(backup_path)
        persistence.close()
        persistence = None

        safety_cleanup = {"path": str(safety_backup), "removed_after_validation": False}
        try:
            safety_backup.unlink()
            safety_cleanup["removed_after_validation"] = True
        except FileNotFoundError:
            safety_cleanup["removed_after_validation"] = True
        except OSError as exc:
            # The migration and normal backup are already valid.  Retaining a
            # known-good safety copy is safe and preferable to claiming it was
            # removed when the filesystem refused the cleanup.
            safety_cleanup["cleanup_error"] = _safe_diagnostic(exc)
        result = {
            "status": "COMPLETE",
            "database_path": str(path),
            "pre_migration": pre,
            "post_migration": post,
            "schema_version_expected": SCHEMA_VERSION,
            "source_schema_version": source_schema_version,
            "backfill": backfill,
            "backfill_rerun": backfill_rerun,
            "required_row_counts": count_comparison,
            "snapshot_and_evidence_preservation": preservation,
            "recorded_at_time_reconciliation": reconciliation,
            "corrected_reporting_view": reporting_view,
            "migration_metadata": migration_metadata,
            "post_migration_integrity_check": post_integrity,
            "post_migration_quick_check": post_quick_check,
            "refreshed_daily_backup": {
                "path": str(backup_path),
                **backup_integrity,
                "permanent_daily_backups": _daily_backups(path),
            },
            "temporary_safety_backup": safety_cleanup,
            "network_activity": {"mdd_requests": 0, "dns_activity": 0, "live_network_attempts": 0},
        }
        return result
    except Exception as exc:
        if persistence is not None:
            try:
                persistence.close()
            except Exception:
                pass
            persistence = None
        diagnostic = _safe_diagnostic(exc)
        if safety_backup is None or pre is None or not primary_may_have_changed:
            if safety_backup is not None:
                try:
                    safety_backup.unlink()
                except OSError:
                    pass
            return {
                "status": "FAILED",
                "database_path": str(path),
                "failure": diagnostic,
                "primary_database_modified": False,
                "rollback": {"status": "NOT_REQUIRED"},
            }
        try:
            restored = _restore_primary_from_safety_backup(path, safety_backup)
            restored_summary = _read_only_summary(path)
            exact_restore = (
                restored_summary["schema_version"] == pre["schema_version"]
                and restored_summary["counts"] == pre["counts"]
                and restored_summary["table_digests"] == pre["table_digests"]
            )
            if not exact_restore:
                raise RuntimeError("restored primary did not match the pre-migration durable state")
            safety_cleanup = {"path": str(safety_backup), "removed_after_restore": False}
            try:
                safety_backup.unlink()
                safety_cleanup["removed_after_restore"] = True
            except FileNotFoundError:
                safety_cleanup["removed_after_restore"] = True
            except OSError as cleanup_exc:
                safety_cleanup["cleanup_error"] = _safe_diagnostic(cleanup_exc)
            return {
                "status": "FAILED",
                "database_path": str(path),
                "failure": diagnostic,
                "primary_database_modified": True,
                "rollback": {
                    **restored,
                    "exact_pre_migration_state": True,
                },
                "temporary_safety_backup": safety_cleanup,
            }
        except Exception as restore_exc:
            return {
                "status": "RECOVERY_REQUIRED",
                "database_path": str(path),
                "failure": diagnostic,
                "primary_database_modified": True,
                "rollback": {
                    "status": "RECOVERY_REQUIRED",
                    "error": _safe_diagnostic(restore_exc),
                },
                "validated_safety_backup": str(safety_backup),
                "message": "Primary database may contain a partial migration; retain the validated safety backup and recover explicitly.",
            }
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
        print(json.dumps({"status": "FAILED", **_safe_diagnostic(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("status") == "COMPLETE" or result.get("status") == "UNAVAILABLE":
        return 0
    return 2 if result.get("status") == "RECOVERY_REQUIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

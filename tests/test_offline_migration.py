from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import PersistenceProfile
from cologne_transit_collector.manifest import load_default_manifest
from cologne_transit_collector.persistence import SQLitePersistence


_SPEC = importlib.util.spec_from_file_location(
    "offline_migration_utility",
    ROOT / "scripts" / "stage_80_offline_migration.py",
)
assert _SPEC is not None and _SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MIGRATION)


OLD_TIMESTAMP = "2026-10-01T00:00:00Z"
LEAF_PATH = "$.result.thisCall.serviceArrival.recordedAtTime"


def _snapshot() -> dict:
    return {
        "parser_version": "pre-s79-contract-test",
        "events": [
            {
                "event_ordinal": 0,
                "result_id": "schema2-result",
                "operating_day_ref": "2026-10-01",
                "journey_ref": "journey-1",
                "calls": [
                    {
                        "call_scope": "THIS",
                        "call_ordinal": 0,
                        "source_path": "$.result.thisCall",
                        "stop_point_ref": "stop-1",
                        "stop_seq_number": 7,
                        "arrival": {
                            "timetabled_time": "2026-10-01T12:05:00Z[GMT]",
                            "estimated_time": "2026-10-01T12:06:00Z[GMT]",
                            "recorded_at_time": "2026-10-01T12:04:00Z[GMT]",
                            "source_path": "$.result.thisCall.serviceArrival",
                            "source_paths": {
                                "timetabled_time": "$.result.thisCall.serviceArrival.timetabledTime",
                                "estimated_time": "$.result.thisCall.serviceArrival.estimatedTime",
                                "recorded_at_time": LEAF_PATH,
                            },
                        },
                    }
                ],
                "service_section_evidence": [
                    {
                        "section_ordinal": 0,
                        "source_path": "$.result.stopEvent.service.serviceSection[0]",
                        "result_id": "schema2-result",
                        "line_ref": "line-1",
                        "line_ref_source_path": "$.result.stopEvent.service.serviceSection[0].lineRef",
                        "direction_ref": "out",
                        "operator_ref": "op-1",
                        "mode_source_representation": {"ptMode": "TRAM"},
                        "pt_mode": "TRAM",
                        "published_line_name": [{"text": "9"}],
                        "published_line_name_text": "9",
                        "source_representation": {"lineRef": {"value": "line-1"}},
                    }
                ],
            }
        ],
        "parser_gaps": [],
        "recorded_at_time_occurrences": [],
        "raw_payload_persisted": False,
    }


def _create_schema2_database(path: Path) -> str:
    manifest = load_default_manifest(ROOT)
    target = manifest.target_by_id("de:05315:11201")
    snapshot = _snapshot()
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (ROOT / "tests" / "fixtures" / "schema_v2_live_research.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.executemany(
            "INSERT INTO database_metadata (metadata_key, metadata_value) VALUES (?, ?)",
            (
                ("database_role", "LIVE_RESEARCH"),
                ("schema_version", "2"),
                ("created_at_utc", OLD_TIMESTAMP),
                ("last_schema_migration_at_utc", OLD_TIMESTAMP),
                ("collection_context", "LIVE_RESEARCH"),
            ),
        )
        connection.execute(
            """
            INSERT INTO manifest_targets
            (panel_version, source_parent_station_id, station_name, tier,
             cadence_minutes, number_of_results, phase_index, phase_count,
             continuity_classification, sampling_era,
             production_sampling_target_id, legacy_sampling_target_id)
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
        connection.execute(
            """
            INSERT INTO target_audits
            (audit_id, collector_run_id, panel_version, source_parent_station_id,
             station_name, tier, phase_index, number_of_results, logical_due_bucket,
             sampling_era, continuity_classification, collection_context,
             started_at_utc, status, parser_outcome, persistence_outcome)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'COMPLETE', 'PARSED',
                    'PERSISTED_PARSED_SNAPSHOT')
            """,
            (
                "schema2-run",
                target.panel_version,
                target.source_parent_station_id,
                target.station_name,
                target.tier,
                target.phase_index,
                target.number_of_results,
                target.sampling_era.value,
                target.continuity_classification,
                "LIVE_RESEARCH",
                OLD_TIMESTAMP,
            ),
        )
        connection.execute(
            """
            INSERT INTO parsed_snapshots
            (snapshot_id, audit_id, panel_version, source_parent_station_id,
             logical_due_bucket, persisted_at_utc, snapshot_json,
             raw_payload_persisted, collection_context)
            VALUES (1, 1, ?, ?, 1, ?, ?, 0, 'LIVE_RESEARCH')
            """,
            (
                target.panel_version,
                target.source_parent_station_id,
                OLD_TIMESTAMP,
                snapshot_json,
            ),
        )
        connection.execute(
            """
            INSERT INTO recorded_at_time_evidence
            (evidence_id, audit_id, snapshot_id, panel_version,
             source_parent_station_id, logical_due_bucket, event_ordinal,
             result_id, call_scope, call_ordinal, timing_context,
             recorded_at_time, source_path, observed_at_utc, collection_context)
            VALUES (1, 1, 1, ?, ?, 1, 0, 'schema2-result', 'THIS', 0, 'ARRIVAL',
                    '2026-10-01T12:04:00Z[GMT]', ?, ?, 'LIVE_RESEARCH')
            """,
            (target.panel_version, target.source_parent_station_id, LEAF_PATH, OLD_TIMESTAMP),
        )
        connection.commit()
    finally:
        connection.close()
    return snapshot_json


def _create_schema4_database(path: Path) -> tuple[str, dict[str, int]]:
    manifest = load_default_manifest(ROOT)
    target = manifest.target_by_id("de:05315:11201")
    persistence = SQLitePersistence(
        path,
        profile=PersistenceProfile.LIVE_RESEARCH,
        repository_root=ROOT,
        backup_dir=path.parent / "backups",
        raw_evidence_dir=path.parent / "raw-evidence",
    )
    try:
        persistence.materialize_manifest(manifest)
        persistence.start_collector_run(
            run_id="schema4-run",
            panel_version=target.panel_version,
            logical_due_bucket=1,
            mode="offline",
            started_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        persistence.claim_dispatcher_bucket(
            run_id="schema4-run",
            panel_version=target.panel_version,
            logical_due_bucket=1,
            claimed_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        persistence.claim_target(
            run_id="schema4-run",
            target=target,
            logical_due_bucket=1,
            claimed_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        audit_id = persistence.start_target_audit(
            run_id="schema4-run",
            target=target,
            logical_due_bucket=1,
            started_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        snapshot = _snapshot()
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        persistence.persist_snapshot(
            audit_id=audit_id,
            target=target,
            logical_due_bucket=1,
            parsed_snapshot=snapshot,
            persisted_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        counts = {
            table: int(
                persistence._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "parsed_snapshots",
                "timing_observations",
                "recorded_at_time_evidence",
                "service_section_observations",
            )
        }
    finally:
        persistence.close()

    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (ROOT / "tests" / "fixtures" / "schema_v4_old_reporting_views.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.execute(
            "UPDATE database_metadata SET metadata_value = '4' WHERE metadata_key = 'schema_version'"
        )
        connection.execute(
            "UPDATE database_metadata SET metadata_value = ? WHERE metadata_key = 'last_schema_migration_at_utc'",
            (OLD_TIMESTAMP,),
        )
        connection.commit()
    finally:
        connection.close()
    return snapshot_json, counts


class OfflineMigrationUtilityTests(unittest.TestCase):
    def _database(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        directory = tempfile.TemporaryDirectory(prefix="offline-migration-")
        path = Path(directory.name) / "collector.sqlite3"
        original_json = _create_schema2_database(path)
        return directory, path, original_json

    def test_schema2_utility_migration_is_version_aware_and_atomic(self) -> None:
        directory, path, original_json = self._database()
        self.addCleanup(directory.cleanup)
        with patch.object(MIGRATION, "_ensure_no_live_collector_process"):
            result = MIGRATION.migrate(path)

        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(result["source_schema_version"], 2)
        self.assertEqual(result["required_row_counts"]["status"], "PASS")
        self.assertEqual(result["backfill"]["timing_rows_inserted"], 1)
        self.assertEqual(result["backfill"]["service_section_rows_inserted"], 1)
        self.assertEqual(result["backfill_rerun"]["timing_rows_inserted"], 0)
        self.assertEqual(result["backfill_rerun"]["service_section_rows_inserted"], 0)
        self.assertTrue(result["snapshot_and_evidence_preservation"]["parsed_snapshot_rows_unchanged"])
        self.assertTrue(result["snapshot_and_evidence_preservation"]["original_recorded_at_time_evidence_preserved"])
        self.assertEqual(result["recorded_at_time_reconciliation"]["status"], "PASS")
        self.assertEqual(result["refreshed_daily_backup"]["integrity_check"], "ok")
        self.assertTrue(result["temporary_safety_backup"]["removed_after_validation"])
        self.assertEqual(result["network_activity"]["mdd_requests"], 0)

        connection = sqlite3.connect(path)
        try:
            metadata = dict(connection.execute("SELECT metadata_key, metadata_value FROM database_metadata"))
            self.assertEqual(metadata["schema_version"], "5")
            self.assertEqual(metadata["schema_migration_status"], "COMPLETE")
            self.assertEqual(
                connection.execute("SELECT snapshot_json FROM parsed_snapshots WHERE snapshot_id = 1").fetchone()[0],
                original_json,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM timing_observations").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM service_section_observations").fetchone()[0],
                1,
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        self.assertEqual(len(list((path.parent / "backups").glob("collector-*.sqlite3"))), 1)
        self.assertEqual(list(path.parent.glob(".s80-pre-migration-*.sqlite3")), [])

    def test_schema4_utility_preserves_counts_replaces_views_and_keeps_timestamp_on_reopen(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="offline-migration-v4-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "collector.sqlite3"
        original_json, counts_before = _create_schema4_database(path)
        with patch.object(MIGRATION, "_ensure_no_live_collector_process"):
            result = MIGRATION.migrate(path)

        self.assertEqual(result["status"], "COMPLETE", result)
        self.assertEqual(result["source_schema_version"], 4)
        self.assertEqual(result["required_row_counts"]["rule"], "all_four_durable_tables_unchanged")
        for values in result["required_row_counts"]["tables"].values():
            self.assertTrue(values["unchanged"])
        self.assertIn(LEAF_PATH, result["corrected_reporting_view"]["recorded_at_time_source_paths"])
        self.assertTrue(result["temporary_safety_backup"]["removed_after_validation"])

        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                dict(connection.execute("SELECT metadata_key, metadata_value FROM database_metadata"))["schema_version"],
                "5",
            )
            self.assertEqual(
                connection.execute("SELECT snapshot_json FROM parsed_snapshots WHERE snapshot_id = 1").fetchone()[0],
                original_json,
            )
            counts_after = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in counts_before
            }
            self.assertEqual(counts_after, counts_before)
        finally:
            connection.close()

        reopened = SQLitePersistence(
            path,
            profile=PersistenceProfile.LIVE_RESEARCH,
            repository_root=ROOT,
            backup_dir=path.parent / "backups",
            raw_evidence_dir=path.parent / "raw-evidence",
        )
        try:
            self.assertEqual(
                reopened.get_metadata()["last_schema_migration_at_utc"],
                result["migration_metadata"]["last_schema_migration_at_utc"],
            )
        finally:
            reopened.close()

    def _assert_failure_restores_original(
        self,
        failure_patch: object,
        *,
        expect_recovery_required: bool = False,
    ) -> None:
        directory, path, original_json = self._database()
        self.addCleanup(directory.cleanup)
        with patch.object(MIGRATION, "_ensure_no_live_collector_process"), failure_patch:
            result = MIGRATION.migrate(path)
        if expect_recovery_required:
            self.assertEqual(result["status"], "RECOVERY_REQUIRED", result)
            safety_path = Path(result["validated_safety_backup"])
            self.assertTrue(safety_path.exists())
            safety_path.unlink()
            return
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"]["status"], "RESTORED")
        self.assertTrue(result["rollback"]["exact_pre_migration_state"])
        self.assertTrue(result["temporary_safety_backup"]["removed_after_restore"])
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                dict(connection.execute("SELECT metadata_key, metadata_value FROM database_metadata"))["schema_version"],
                "2",
            )
            self.assertEqual(
                connection.execute("SELECT snapshot_json FROM parsed_snapshots WHERE snapshot_id = 1").fetchone()[0],
                original_json,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'timing_observations'"
                ).fetchone(),
                None,
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        self.assertEqual(list(path.parent.glob(".s80-pre-migration-*.sqlite3")), [])

    def test_failure_injection_backfill_restores_primary(self) -> None:
        failure = patch.object(
            MIGRATION.SQLitePersistence,
            "backfill_existing_snapshots",
            side_effect=RuntimeError("injected backfill failure"),
        )
        self._assert_failure_restores_original(failure)

    def test_failure_injection_recorded_at_time_reconciliation_restores_primary(self) -> None:
        failure = patch.object(
            MIGRATION.SQLitePersistence,
            "reconcile_recorded_at_time_evidence",
            return_value={"status": "FAIL", "unmatched_timing_rows": 1},
        )
        self._assert_failure_restores_original(failure)

    def test_failure_injection_reporting_validation_restores_primary(self) -> None:
        failure = patch.object(
            MIGRATION,
            "_verify_corrected_reporting_view",
            side_effect=RuntimeError("injected reporting-view failure"),
        )
        self._assert_failure_restores_original(failure)

    def test_failure_injection_integrity_verification_restores_primary(self) -> None:
        failure = patch.object(
            MIGRATION.SQLitePersistence,
            "integrity_check",
            side_effect=RuntimeError("injected integrity failure"),
        )
        self._assert_failure_restores_original(failure)

    def test_failure_injection_daily_backup_restores_primary_and_keeps_old_backup(self) -> None:
        directory, path, original_json = self._database()
        self.addCleanup(directory.cleanup)
        backup_dir = path.parent / "backups"
        backup_dir.mkdir()
        old_backup = backup_dir / "collector-2026-09-30.sqlite3"
        shutil.copyfile(path, old_backup)
        old_bytes = old_backup.read_bytes()
        failure = patch.object(
            MIGRATION.SQLitePersistence,
            "backup_live_database",
            return_value={
                "status": "FAILED",
                "backup_path": str(backup_dir / "collector-2026-10-01.sqlite3"),
            },
        )
        with patch.object(MIGRATION, "_ensure_no_live_collector_process"), failure:
            result = MIGRATION.migrate(path)
        self.assertEqual(result["status"], "FAILED", result)
        self.assertEqual(result["rollback"]["status"], "RESTORED")
        self.assertEqual(old_backup.read_bytes(), old_bytes)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("SELECT snapshot_json FROM parsed_snapshots WHERE snapshot_id = 1").fetchone()[0], original_json)
        finally:
            connection.close()

    def test_restore_failure_reports_recovery_required_and_retains_safety_backup(self) -> None:
        directory, path, _original_json = self._database()
        self.addCleanup(directory.cleanup)
        failure = patch.object(
            MIGRATION.SQLitePersistence,
            "backfill_existing_snapshots",
            side_effect=RuntimeError("injected backfill failure"),
        )
        restore_failure = patch.object(
            MIGRATION,
            "_restore_primary_from_safety_backup",
            side_effect=RuntimeError("injected restore failure"),
        )
        with patch.object(MIGRATION, "_ensure_no_live_collector_process"), failure, restore_failure:
            result = MIGRATION.migrate(path)
        self.assertEqual(result["status"], "RECOVERY_REQUIRED")
        safety_path = Path(result["validated_safety_backup"])
        self.assertTrue(safety_path.exists())
        safety_path.unlink()


if __name__ == "__main__":
    unittest.main()

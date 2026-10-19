from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest
from cologne_transit_collector.persistence import SCHEMA_VERSION, SQLitePersistence
from cologne_transit_stage15.analyzer import _timing_reporting_projection


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PARENT_PATH = "$.call.arrival"
LEAF_PATH = "$.call.arrival.recordedAtTime"


def _event(*, recorded: str | None, result_id: str) -> dict:
    return {
        "event_ordinal": 0,
        "result_id": result_id,
        "operating_day_ref": "2026-10-01",
        "journey_ref": "journey-1",
        "calls": [
            {
                "call_scope": "THIS",
                "call_ordinal": 0,
                "source_path": "$.call",
                "stop_point_ref": "stop-1",
                "stop_seq_number": 7,
                "source_paths": {"stop_point_ref": "$.call.stopPointRef"},
                "arrival": {
                    "timetabled_time": "2026-10-01T12:05:00Z[GMT]",
                    "estimated_time": "2026-10-01T12:06:00Z[GMT]",
                    "estimated_time_low": None,
                    "estimated_time_high": None,
                    "recorded_at_time": recorded,
                    "source_path": PARENT_PATH,
                    "source_paths": {
                        "timetabled_time": f"{PARENT_PATH}.timetabledTime",
                        "estimated_time": f"{PARENT_PATH}.estimatedTime",
                        "recorded_at_time": LEAF_PATH if recorded is not None else None,
                    },
                },
            }
        ],
    }


def _snapshot(*, recorded: str | None, result_id: str) -> dict:
    return {
        "parser_version": "stage81-test",
        "events": [_event(recorded=recorded, result_id=result_id)],
        "parser_gaps": [],
        "recorded_at_time_occurrences": [],
        "raw_payload_persisted": False,
    }


class Stage81ReportingAndMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.target = cls.manifest.target_by_id("de:05315:11201")

    def _state(self, path: Path) -> tuple[SQLitePersistence, int]:
        persistence = SQLitePersistence(path)
        self.addCleanup(persistence.close)
        persistence.materialize_manifest(self.manifest)
        persistence.start_collector_run(
            run_id="stage81-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            mode="simulate",
            started_at=AT,
        )
        persistence.claim_dispatcher_bucket(
            run_id="stage81-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        persistence.claim_target(
            run_id="stage81-run",
            target=self.target,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        audit_id = persistence.start_target_audit(
            run_id="stage81-run",
            target=self.target,
            logical_due_bucket=1,
            started_at=AT,
        )
        return persistence, audit_id

    def _persist_lifecycle(self, persistence: SQLitePersistence, audit_id: int) -> None:
        for offset, recorded in enumerate(("R1", None)):
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=1,
                parsed_snapshot=_snapshot(
                    recorded=recorded,
                    result_id=f"result-{offset + 1}",
                ),
                persisted_at=AT + timedelta(minutes=offset),
            )

    def test_sqlite_and_stage15_reporting_use_recorded_at_time_leaf_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage81-reporting-") as directory:
            persistence, audit_id = self._state(Path(directory) / "evidence.sqlite3")
            self._persist_lifecycle(persistence, audit_id)

            sqlite_projection = persistence.fetch_timing_recorded_at_time_reporting()[0]
            self.assertIsNone(sqlite_projection["LatestSourceRecordedAtTime"])
            self.assertIsNone(sqlite_projection["LatestSourceRecordedAtTimeSourcePath"])
            self.assertEqual(sqlite_projection["LastObservedRecordedAtTime"], "R1")
            self.assertEqual(sqlite_projection["LastObservedRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertEqual(sqlite_projection["FirstObservedRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertNotEqual(sqlite_projection["LastObservedRecordedAtTimeSourcePath"], PARENT_PATH)

            stage15_projection = _timing_reporting_projection(
                persistence.fetch_timing_observations()
            )[0]
            for field in (
                "LatestSourceRecordedAtTime",
                "LatestSourceRecordedAtTimeSourcePath",
                "LastObservedRecordedAtTime",
                "LastObservedRecordedAtTimeSourcePath",
                "FirstObservedRecordedAtTime",
                "FirstObservedRecordedAtTimeSourcePath",
            ):
                self.assertEqual(stage15_projection[field], sqlite_projection[field])
            self.assertIsNone(stage15_projection["LatestSourceRecordedAtTimeSourcePath"])
            self.assertEqual(stage15_projection["LastObservedRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertEqual(stage15_projection["FirstObservedRecordedAtTimeSourcePath"], LEAF_PATH)

    def test_schema_v2_fixture_migrates_and_backfills_without_rewriting_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage81-v2-") as directory:
            path = Path(directory) / "evidence.sqlite3"
            old_timestamp = "2026-10-01T00:00:00Z"
            snapshot = _snapshot(recorded="R1", result_id="v2-result")
            original_json = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            conn = sqlite3.connect(path)
            try:
                conn.executescript(
                    (ROOT / "tests" / "fixtures" / "schema_v2_live_research.sql").read_text(
                        encoding="utf-8"
                    )
                )
                conn.executemany(
                    "INSERT INTO database_metadata (metadata_key, metadata_value) VALUES (?, ?)",
                    (
                        ("database_role", "DEVELOPMENT"),
                        ("schema_version", "2"),
                        ("created_at_utc", old_timestamp),
                        ("last_schema_migration_at_utc", old_timestamp),
                        ("collection_context", "OFFLINE_DEVELOPMENT"),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO manifest_targets
                    (panel_version, source_parent_station_id, station_name, tier,
                     cadence_minutes, number_of_results, phase_index, phase_count,
                     continuity_classification, sampling_era,
                     production_sampling_target_id, legacy_sampling_target_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.target.panel_version,
                        self.target.source_parent_station_id,
                        self.target.station_name,
                        self.target.tier,
                        self.target.cadence_minutes,
                        self.target.number_of_results,
                        self.target.phase_index,
                        self.target.phase_count,
                        self.target.continuity_classification,
                        self.target.sampling_era.value,
                        self.target.production_sampling_target_id,
                        self.target.legacy_sampling_target_id,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO target_audits
                    (audit_id, collector_run_id, panel_version, source_parent_station_id,
                     station_name, tier, phase_index, number_of_results, logical_due_bucket,
                     sampling_era, continuity_classification, collection_context,
                     started_at_utc, status, parser_outcome, persistence_outcome)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "v2-run",
                        self.target.panel_version,
                        self.target.source_parent_station_id,
                        self.target.station_name,
                        self.target.tier,
                        self.target.phase_index,
                        self.target.number_of_results,
                        1,
                        self.target.sampling_era.value,
                        self.target.continuity_classification,
                        "OFFLINE_DEVELOPMENT",
                        old_timestamp,
                        "COMPLETE",
                        "PARSED",
                        "PERSISTED_PARSED_SNAPSHOT",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO parsed_snapshots
                    (snapshot_id, audit_id, panel_version, source_parent_station_id,
                     logical_due_bucket, persisted_at_utc, snapshot_json,
                     raw_payload_persisted, collection_context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        self.target.panel_version,
                        self.target.source_parent_station_id,
                        1,
                        old_timestamp,
                        original_json,
                        0,
                        "OFFLINE_DEVELOPMENT",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO recorded_at_time_evidence
                    (evidence_id, audit_id, snapshot_id, panel_version,
                     source_parent_station_id, logical_due_bucket, event_ordinal,
                     result_id, call_scope, call_ordinal, timing_context,
                     recorded_at_time, source_path, observed_at_utc, collection_context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        1,
                        1,
                        self.target.panel_version,
                        self.target.source_parent_station_id,
                        1,
                        0,
                        "v2-result",
                        "THIS",
                        0,
                        "ARRIVAL",
                        "R1",
                        LEAF_PATH,
                        old_timestamp,
                        "OFFLINE_DEVELOPMENT",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            persistence = SQLitePersistence(path)
            self.addCleanup(persistence.close)
            metadata = persistence.get_metadata()
            self.assertEqual(metadata["schema_version"], SCHEMA_VERSION)
            self.assertEqual(metadata["created_at_utc"], old_timestamp)
            self.assertNotEqual(metadata["last_schema_migration_at_utc"], old_timestamp)
            self.assertEqual(persistence.fetch_snapshots()[0]["snapshot_json"], original_json)
            self.assertEqual(len(persistence.fetch_timing_observations()), 1)
            self.assertEqual(persistence.reconcile_recorded_at_time_evidence()["status"], "PASS")
            self.assertEqual(persistence.integrity_check(quick=False), "ok")

            rerun = persistence.backfill_existing_snapshots()
            self.assertEqual(rerun["timing_rows_inserted"], 0)
            self.assertEqual(rerun["service_section_rows_inserted"], 0)
            self.assertEqual(len(persistence.fetch_timing_observations()), 1)
            migrated_timestamp = metadata["last_schema_migration_at_utc"]
            persistence.close()

            reopened = SQLitePersistence(path)
            self.assertEqual(
                reopened.get_metadata()["last_schema_migration_at_utc"], migrated_timestamp
            )
            reopened.close()

    def test_schema4_old_reporting_view_is_replaced_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage81-v4-") as directory:
            path = Path(directory) / "evidence.sqlite3"
            persistence, audit_id = self._state(path)
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=1,
                parsed_snapshot=_snapshot(recorded="R1", result_id="v4-result"),
                persisted_at=AT,
            )
            snapshot_json = persistence.fetch_snapshots()[0]["snapshot_json"]
            counts_before = {
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
            data_before = {
                "parsed_snapshots": persistence.fetch_snapshots(),
                "timing_observations": persistence.fetch_timing_observations(),
                "recorded_at_time_evidence": persistence.fetch_recorded_time_evidence(),
                "service_section_observations": persistence.fetch_service_section_observations(),
            }
            persistence.close()

            raw = sqlite3.connect(path)
            try:
                raw.executescript(
                    (
                        ROOT
                        / "tests"
                        / "fixtures"
                        / "schema_v4_old_reporting_views.sql"
                    ).read_text(encoding="utf-8")
                )
                raw.execute(
                    "UPDATE database_metadata SET metadata_value = '4' WHERE metadata_key = 'schema_version'"
                )
                raw.execute(
                    "UPDATE database_metadata SET metadata_value = '2026-10-01T00:00:00Z' "
                    "WHERE metadata_key = 'last_schema_migration_at_utc'"
                )
                raw.commit()
                old_path = raw.execute(
                    "SELECT LastObservedRecordedAtTimeSourcePath FROM timing_recorded_at_time_reporting"
                ).fetchone()[0]
                self.assertEqual(old_path, PARENT_PATH)
            finally:
                raw.close()

            migrated = SQLitePersistence(path)
            self.addCleanup(migrated.close)
            self.assertEqual(migrated.get_metadata()["schema_version"], SCHEMA_VERSION)
            self.assertNotEqual(
                migrated.get_metadata()["last_schema_migration_at_utc"],
                "2026-10-01T00:00:00Z",
            )
            report = migrated.fetch_timing_recorded_at_time_reporting()[0]
            self.assertEqual(report["LatestSourceRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertEqual(report["LastObservedRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertEqual(report["FirstObservedRecordedAtTimeSourcePath"], LEAF_PATH)
            self.assertEqual(
                migrated.fetch_snapshots()[0]["snapshot_json"], snapshot_json
            )
            counts_after = {
                table: int(
                    migrated._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in counts_before
            }
            self.assertEqual(counts_after, counts_before)
            data_after = {
                "parsed_snapshots": migrated.fetch_snapshots(),
                "timing_observations": migrated.fetch_timing_observations(),
                "recorded_at_time_evidence": migrated.fetch_recorded_time_evidence(),
                "service_section_observations": migrated.fetch_service_section_observations(),
            }
            self.assertEqual(data_after, data_before)
            self.assertIn(
                "recorded_at_time_source_path",
                str(
                    migrated._connection.execute(
                        "SELECT sql FROM sqlite_master WHERE name = 'timing_recorded_at_time_reporting'"
                    ).fetchone()[0]
                ),
            )
            migration_timestamp = migrated.get_metadata()["last_schema_migration_at_utc"]
            migrated.close()

            reopened = SQLitePersistence(path)
            self.assertEqual(
                reopened.get_metadata()["last_schema_migration_at_utc"], migration_timestamp
            )
            reopened.close()


if __name__ == "__main__":
    unittest.main()

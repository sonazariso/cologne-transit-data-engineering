from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.sqlite_import import (  # noqa: E402
    FULL_IMPORT_TABLES,
    LINEAGE_ONLY_TABLE_REASONS,
    ImportCounters,
    SQLiteImportError,
    SQLiteToMssqlImporter,
    inspect_sqlite_source,
)


def _create_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE database_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
            CREATE TABLE manifest_targets (
                panel_version TEXT, source_parent_station_id TEXT, station_name TEXT,
                legacy_sampling_target_id INTEGER
            );
            CREATE TABLE collector_runs (
                run_id TEXT PRIMARY KEY, panel_version TEXT, logical_due_bucket INTEGER,
                mode TEXT, collection_context TEXT, started_at_utc TEXT,
                completed_at_utc TEXT, status TEXT, summary_json TEXT
            );
            CREATE TABLE target_audits (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT, collector_run_id TEXT,
                panel_version TEXT, source_parent_station_id TEXT, station_name TEXT,
                tier TEXT, phase_index INTEGER, number_of_results INTEGER,
                logical_due_bucket INTEGER, sampling_era TEXT,
                continuity_classification TEXT, collection_context TEXT,
                started_at_utc TEXT, completed_at_utc TEXT, status TEXT
            );
            CREATE TABLE http_attempts (attempt_id INTEGER, audit_id INTEGER);
            CREATE TABLE parsed_snapshots (snapshot_id INTEGER, audit_id INTEGER, collection_context TEXT, snapshot_json TEXT);
            CREATE TABLE timing_observations (timing_observation_id INTEGER, snapshot_id INTEGER, collection_context TEXT);
            CREATE TABLE service_section_observations (service_section_observation_id INTEGER, snapshot_id INTEGER, collection_context TEXT);
            CREATE TABLE recorded_at_time_evidence (evidence_id INTEGER, snapshot_id INTEGER);
            CREATE TABLE dispatcher_claims (collector_run_id TEXT);
            CREATE TABLE dispatcher_claim_events (event_id INTEGER, collector_run_id TEXT);
            CREATE TABLE target_claims (collector_run_id TEXT);
            CREATE TABLE claim_events (event_id INTEGER, collector_run_id TEXT);
            CREATE TABLE quota_permits (permit_id TEXT);
            CREATE TABLE quota_audit (quota_audit_id INTEGER, collector_run_id TEXT);
            CREATE TABLE raw_evidence (raw_evidence_id INTEGER, collector_run_id TEXT);
            CREATE TABLE sampling_eras (era_code TEXT, panel_version TEXT);
            CREATE TABLE shared_usage (month_key TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO database_metadata (metadata_key, metadata_value) VALUES (?, ?)",
            (
                ("schema_version", "5"),
                ("database_role", "LIVE_RESEARCH"),
                ("collection_context", "LIVE_RESEARCH_SINGLE_1"),
            ),
        )
        connection.execute(
            "INSERT INTO manifest_targets VALUES (?, ?, ?, ?)",
            ("TIERED50_PANEL_V1", "de:05315:11110", "Köln Heumarkt", 1),
        )
        for index in range(50):
            start_value = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=index * 360)
            end_value = start_value + timedelta(seconds=1)
            start = start_value.isoformat().replace("+00:00", "Z")
            end = end_value.isoformat().replace("+00:00", "Z")
            run_id = f"live-research-fixture-{index:03d}"
            connection.execute(
                "INSERT INTO collector_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, "TIERED50_PANEL_V1", index, "live-research", "LIVE_RESEARCH_SINGLE_1", start, end, "COMPLETE", "{}"),
            )
            connection.execute(
                """
                INSERT INTO target_audits
                (collector_run_id, panel_version, source_parent_station_id, station_name,
                 tier, phase_index, number_of_results, logical_due_bucket, sampling_era,
                 continuity_classification, collection_context, started_at_utc,
                 completed_at_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, "TIERED50_PANEL_V1", "de:05315:11110", "Köln Heumarkt", "A", 0, 20, index,
                 "LEGACY7", "LEGACY7_CONTINUITY", "LIVE_RESEARCH_SINGLE_1", start, end, "SUCCEEDED"),
            )
            audit_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO http_attempts VALUES (?, ?)", (index + 1, audit_id))
            connection.execute("INSERT INTO parsed_snapshots VALUES (?, ?, ?, ?)", (index + 1, audit_id, "LIVE_RESEARCH_SINGLE_1", json.dumps({})))
        connection.execute("INSERT INTO timing_observations VALUES (?, ?, ?)", (1, 1, "LIVE_RESEARCH_SINGLE_1"))
        connection.execute("INSERT INTO service_section_observations VALUES (?, ?, ?)", (1, 1, "LIVE_RESEARCH_SINGLE_1"))
        connection.commit()
    finally:
        connection.close()


class S91SQLiteImportTests(unittest.TestCase):
    def test_explicit_source_proof_finds_one_approximately_six_hour_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s91-source-") as directory:
            path = Path(directory) / "collector.sqlite3"
            _create_source(path)
            proof, inventory = inspect_sqlite_source(path)

        self.assertEqual(proof.application_schema_version, 5)
        self.assertEqual(proof.database_role, "LIVE_RESEARCH")
        self.assertEqual(proof.collection_context, "LIVE_RESEARCH_SINGLE_1")
        self.assertEqual(proof.session.collector_run_count, 50)
        self.assertGreaterEqual(proof.session.duration_seconds, 4 * 60 * 60)
        self.assertEqual(proof.target_identity["source_parent_station_id"], "de:05315:11110")
        self.assertEqual(proof.integrity_check, "ok")
        self.assertEqual(len(inventory), len(FULL_IMPORT_TABLES) + len(LINEAGE_ONLY_TABLE_REASONS) - 2)

    def test_inventory_has_explicit_full_and_lineage_only_classifications(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s91-inventory-") as directory:
            path = Path(directory) / "collector.sqlite3"
            _create_source(path)
            _proof, inventory = inspect_sqlite_source(path)

        by_name = {item.table_name: item for item in inventory}
        self.assertEqual(by_name["timing_observations"].classification, "FULL_IMPORT")
        self.assertEqual(by_name["database_metadata"].classification, "LINEAGE_ONLY")
        self.assertTrue(by_name["sampling_eras"].explicit_reason)
        self.assertNotIn("recorded_at_time_reporting", by_name)

    def test_corrupted_or_unreadable_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s91-integrity-") as directory:
            original = Path(directory) / "collector.sqlite3"
            corrupted = Path(directory) / "corrupted.sqlite3"
            _create_source(original)
            shutil.copy2(original, corrupted)
            with corrupted.open("r+b") as handle:
                handle.seek(100)
                value = handle.read(1)
                handle.seek(100)
                handle.write(bytes([value[0] ^ 0xFF]))
            with self.assertRaises((sqlite3.DatabaseError, SQLiteImportError)):
                inspect_sqlite_source(corrupted)

    def test_default_import_is_read_only_and_source_path_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s91-dry-run-") as directory:
            path = Path(directory) / "collector.sqlite3"
            _create_source(path)
            result = SQLiteToMssqlImporter(path).run()
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertFalse(result["apply"])
        self.assertIn("no SQL Server writes", result["reconciliation_mode"])

        command = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "import_live_research_sqlite.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(command.returncode, 0)
        self.assertIn("--source", command.stderr)

    def test_lineage_counters_prove_idempotent_reconciliation(self) -> None:
        counters = ImportCounters(eligible_rows=10, duplicate_rows=7, overlap_rows=3)
        self.assertEqual(counters.unexplained_difference, 0)
        self.assertEqual(counters.to_dict()["unexplained_difference"], 0)


if __name__ == "__main__":
    unittest.main()

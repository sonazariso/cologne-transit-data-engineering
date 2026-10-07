from __future__ import annotations

import sys
import unittest
from unittest.mock import patch


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.sqlite_import import (  # noqa: E402
    SQLiteSourceProof,
    _capacity_report,
    read_only_failed_import_audit,
)
from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402


def _proof(size: int = 100) -> SQLiteSourceProof:
    # The capacity evaluator only consumes byte_size; the other fields keep
    # this fixture structurally equivalent to a proven source.
    from cologne_transit_collector.sqlite_import import ResearchSession

    return SQLiteSourceProof(
        path="/tmp/collector.sqlite3",
        byte_size=size,
        sha256="a" * 64,
        integrity_check="ok",
        quick_check="ok",
        pragma_schema_version=1,
        application_schema_version=5,
        database_role="LIVE_RESEARCH",
        collection_context="LIVE_RESEARCH_SINGLE_1",
        target_identity={"source_parent_station_id": "de:05315:11110"},
        session=ResearchSession(
            run_ids=("run",),
            start_utc="2026-10-01T00:00:00Z",
            end_utc="2026-10-01T05:00:00Z",
            duration_seconds=18_000,
            logical_bucket_count=1,
            collector_run_count=1,
            target_audit_count=1,
            http_attempt_count=1,
            successful_run_count=1,
            failed_run_count=0,
            skipped_run_count=0,
            parsed_snapshot_count=1,
        ),
        table_names=(),
        source_counts={},
        session_counts={},
        run_status_counts={},
        audit_status_counts={},
    )


def _rows_file(*, free: int, used: int = 100) -> dict[str, object]:
    return {
        "logical_file_name": "CologneTransitIntelligence",
        "physical_path": r"C:\SQL\CologneTransitIntelligence.mdf",
        "file_type": "ROWS",
        "allocated_bytes": 1_000,
        "space_used_bytes": used,
        "internal_free_bytes": 1_000 - used,
        "growth_setting": "64 MB",
        "is_percent_growth": False,
        "growth": 8192,
        "max_size_bytes": None,
        "max_size": "UNLIMITED",
        "volume_name": "C:\\",
        "volume_total_bytes": 100 * 1024 * 1024 * 1024,
        "volume_free_bytes": free,
        "volume_free_percent": free / (100 * 1024 * 1024 * 1024) * 100,
    }


def _log_file() -> dict[str, object]:
    return {
        "logical_file_name": "CologneTransitIntelligence_log",
        "physical_path": r"C:\SQL\CologneTransitIntelligence.ldf",
        "file_type": "LOG",
        "allocated_bytes": 9_000,
        "space_used_bytes": None,
        "internal_free_bytes": None,
        "growth_setting": "64 MB",
        "is_percent_growth": False,
        "growth": 8192,
        "max_size_bytes": None,
        "max_size": "UNLIMITED",
        "volume_name": "C:\\",
        "volume_total_bytes": 100 * 1024 * 1024 * 1024,
        "volume_free_bytes": 20 * 1024 * 1024 * 1024,
        "volume_free_percent": 20.0,
    }


class _FailedAuditCursor:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def execute(self, statement: str, params=()) -> None:
        if "FROM ctl.MddImportBatch" in statement:
            self.rows = [
                {
                    "ImportBatchId": "batch-1",
                    "ImportStartedAtUtc": "2026-10-01T00:00:00Z",
                    "ImportCompletedAtUtc": "2026-10-01T00:01:00Z",
                    "FailureReason": "simulated later-family failure",
                    "SourceCountsJson": '{"collector_runs": 2}',
                    "InsertedCountsJson": '{"collector_runs": 2}',
                    "DuplicateCountsJson": '{}',
                    "OverlapCountsJson": '{}',
                    "ReconciliationResultJson": '{"status":"FAILED"}',
                    "ImporterVersion": "test",
                }
            ]
        elif "FROM ctl.MddImportTableAudit" in statement:
            self.rows = [
                {
                    "SourceTableName": "collector_runs",
                    "Classification": "FULL_IMPORT",
                    "SourceRowCount": 2,
                    "EligibleRowCount": 2,
                    "InsertedRowCount": 2,
                    "DuplicateRowCount": 0,
                    "OverlapClassifiedRowCount": 0,
                    "DestinationRowCount": 2,
                    "UnexplainedDifference": 0,
                    "ReconciliationStatus": "PASS",
                }
            ]
        elif "FROM ctl.MddImportEvidenceAudit" in statement:
            self.rows = [
                {
                    "EvidenceFamily": "timing_observations",
                    "SourceRowCount": 1,
                    "EligibleRowCount": 1,
                    "InsertedRowCount": 1,
                    "DuplicateRowCount": 0,
                    "OverlapClassifiedRowCount": 0,
                    "DestinationRowCount": 1,
                    "UnexplainedDifference": 0,
                    "ReconciliationStatus": "PASS",
                }
            ]
        elif "COUNT_BIG(*) AS source_row_count" in statement:
            self.rows = [
                {
                    "source_row_count": 3,
                    "inserted_lineage_count": 3,
                    "overlap_lineage_count": 0,
                    "destination_object_count": 2,
                    "destination_key_count": 3,
                }
            ]
        elif "SELECT DISTINCT DestinationObjectName" in statement:
            self.rows = [
                {"DestinationObjectName": "ctl.MddMacCollectorRun"},
                {"DestinationObjectName": "stg.MddMacTimingObservation"},
            ]
        else:  # pragma: no cover - defensive fixture failure
            raise AssertionError(statement)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _FailedAuditConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FailedAuditCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class S98OperationalCompletionTests(unittest.TestCase):
    def test_capacity_separates_rows_internal_free_from_log_reusable_space(self) -> None:
        result = _capacity_report(
            proof=_proof(100),
            rows_files=[_rows_file(free=20 * 1024 * 1024 * 1024, used=800)],
            log_files=[_log_file()],
            log_usage={
                "recovery_model_desc": "FULL",
                "log_reuse_wait_desc": "NOTHING",
                "used_log_space_in_bytes": 100,
                "used_log_space_in_percent": 1.1,
            },
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["import_capacity"]["rows_internal_free_capacity_bytes"], 200)
        self.assertEqual(result["import_capacity"]["expected_physical_growth_requirement_bytes"], 0)
        self.assertEqual(result["log_usage"]["reusable_internal_free_bytes"], 8_900)
        self.assertTrue(result["log_space_excluded_from_rows_capacity"])

    def test_capacity_fails_when_physical_volume_is_critically_full_even_with_mdf_free_space(self) -> None:
        result = _capacity_report(
            proof=_proof(100),
            rows_files=[_rows_file(free=10, used=800)],
            log_files=[_log_file()],
            log_usage={"used_log_space_in_bytes": 100, "used_log_space_in_percent": 1.1},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["import_capacity"]["capacity_blockers"])
        self.assertGreater(result["import_capacity"]["additional_free_bytes_required"], 0)

    def test_failed_import_audit_reports_partial_destination_evidence_without_mutating_history(self) -> None:
        connection = _FailedAuditConnection()
        settings = MssqlConnectionSettings(host="127.0.0.1", user="u", password="p")
        with patch("cologne_transit_collector.sqlite_import.connect_mssql", return_value=connection):
            result = read_only_failed_import_audit(settings, "a" * 64)
        self.assertEqual(result["failed_batch_count"], 1)
        batch = result["failed_batches"][0]
        self.assertEqual(batch["import_batch_id"], "batch-1")
        self.assertTrue(batch["partial_destination_evidence_committed"])
        self.assertTrue(batch["resume_reconcile_safe"])
        self.assertEqual(batch["destination_objects_touched"], ["ctl.MddMacCollectorRun", "stg.MddMacTimingObservation"])
        self.assertTrue(batch["history_immutable"])
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()

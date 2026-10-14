from __future__ import annotations

import re
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    COMPACT_ENVELOPE_TYPE,
    CompactEnvelopeError,
    build_compact_envelope,
    capacity_projection,
    compare_row_family,
    decode_compact_envelope,
    encode_compact_envelope,
    normalize_sql_row,
    nested_gzip_hex_benchmark,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    MssqlConnectionSettings,
    MssqlPersistence,
    MssqlSafetyError,
)

sys.path.insert(0, str(ROOT / "scripts"))

from migrate_historical_compact_observations import (  # noqa: E402
    _capacity_gate,
    _expected_physical_field_records,
    _load_capacity_plan,
    _read_capacity,
    _recovery_decision,
)
from apply_compact_storage_migration import _batches  # noqa: E402
from reclaim_sql_datafile_space import _approval_gate, _parity_gate  # noqa: E402
from calculate_tiered50_storage_capacity import calculate as calculate_tiered50  # noqa: E402


class CompactObservationStorageTests(unittest.TestCase):
    def test_compact_sql_guard_covers_every_go_batch_and_never_uses_noexec(self) -> None:
        compact = (ROOT / "sql/mdd-mac-collector/compact-observation-storage-v1.sql").read_text(encoding="utf-8")
        batches = _batches(apply=False)
        self.assertGreaterEqual(len(batches), 20)
        self.assertEqual(compact.count("DECLARE @ApplyCompactStorage BIT = 0;"), 1)
        self.assertNotIn("NOEXEC", compact.upper())
        self.assertIn("BEGIN TRY", batches[0])
        self.assertIn("BEGIN CATCH", batches[0])
        for batch in batches[1:]:
            self.assertIn("COALESCE(TRY_CONVERT(INT, SESSION_CONTEXT", batch)
            self.assertIn("EXEC sys.sp_executesql", batch)
            self.assertIn("BEGIN TRY", batch)
            self.assertIn("BEGIN CATCH", batch)
        self.assertIn("CTI.CompactStorage.Committed", batches[-1])

    def test_capacity_readback_uses_bigint_for_a_29_gib_file(self) -> None:
        class CapacityCursor:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def execute(self, query: str, params: object = ()) -> None:
                self.queries.append(query)

            def fetchall(self) -> list[dict[str, object]]:
                query = self.queries[-1]
                if "volume_mount_point" in query:
                    return [{"volume_mount_point": "C:", "total_bytes": 64 * 1024**3, "available_bytes": 14 * 1024**3}]
                if "file_id, name, type_desc" in query:
                    return [{
                        "file_id": 1,
                        "name": "CologneTransitIntelligence",
                        "type_desc": "ROWS",
                        "allocated_bytes": 29 * 1024**3,
                        "used_bytes": 28 * 1024**3,
                    }]
                return []

            def fetchone(self) -> dict[str, object]:
                query = self.queries[-1]
                if "dm_db_log_space_usage" in query:
                    return {
                        "total_log_size_in_bytes": 8 * 1024**3,
                        "used_log_space_in_bytes": 2 * 1024**3,
                        "used_log_space_in_percent": 25.0,
                    }
                if "active_user_transactions" in query:
                    return {"active_user_transactions": 0}
                return {"active_collector_sessions": 0}

        cursor = CapacityCursor()
        capacity = _read_capacity(cursor)
        self.assertEqual(capacity["files"][0]["allocated_bytes"], 29 * 1024**3)
        file_query = next(query for query in cursor.queries if "file_id, name, type_desc" in query)
        self.assertIn("CONVERT(BIGINT, size)", file_query)
        self.assertIn("CONVERT(BIGINT, FILEPROPERTY", file_query)

    def test_capacity_gate_blocks_13_86_gib_when_peak_needs_two_gib(self) -> None:
        available = int(13.86 * 1024**3)
        result = _capacity_gate(
            {
                "volumes": [{"volume": "C:", "available_bytes": available}],
                "log": {"used_percent": 25.0},
                "active_user_transactions": 0,
                "active_collector_sessions": 0,
            },
            log_limit=70.0,
            required_extra_bytes=2 * 1024**3,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertAlmostEqual(result["peak_migration_space_gib"], 2.0, places=5)

    def test_sql_physical_field_contract_preserves_null_empty_binary_and_duplicates(self) -> None:
        records = _expected_physical_field_records(
            {"snapshot_id": 7, "empty": "", "null": None, "payload_gzip_hex": "00ff"},
            {"calls": [{"call_id": 1, "value": "same"}, {"call_id": 2, "value": "same"}],
             "timings": [], "situations": [], "service_sections": [], "attributes": [],
             "response_errors": [], "parser_gaps": []},
        )
        self.assertIn(("source_snapshot", 0, "empty", 1, "", None), records)
        self.assertIn(("source_snapshot", 0, "null", 0, None, None), records)
        self.assertIn(("source_snapshot", 0, "payload_gzip_hex", 1, "00ff", "00ff"), records)
        self.assertEqual(sum(1 for record in records if record[0] == "calls"), 4)

    def test_failure_injection_keeps_built_unit_retryable_and_quarantines_only_corruption(self) -> None:
        network = _recovery_decision("BUILT", ConnectionError("socket closed"))
        self.assertEqual(network["status"], "BLOCKED")
        self.assertEqual(network["recovery"], "RETRY_BUILT_UNIT")
        corruption = _recovery_decision("ALREADY_BUILT", CompactEnvelopeError("bad gzip"))
        self.assertEqual(corruption["status"], "QUARANTINED")
        self.assertIsNone(_recovery_decision("ALREADY_VERIFIED", ConnectionError("late failure"))["recovery"])

    def test_envelope_round_trip_preserves_unicode_nulls_and_duplicates(self) -> None:
        source = {
            "snapshot_id": 41,
            "run_id": "run-α",
            "snapshot_json": '{"empty":"","null":null,"city":"Köln"}',
            "created_at_utc": "2026-10-13T12:00:00.1234560",
        }
        rows = {
            "calls": [{"call_observation_id": 7, "value": "same"}, {"call_observation_id": 8, "value": "same"}],
            "timings": [{"timing_observation_id": 9, "recorded_at_time": None}],
            "situations": [],
            "service_sections": [],
            "attributes": [],
            "response_errors": [{"response_error_id": 1, "value": ""}],
            "parser_gaps": [],
        }
        envelope = build_compact_envelope(source_snapshot=source, row_sets=rows)
        encoded = encode_compact_envelope(envelope)
        decoded = decode_compact_envelope(encoded.gzip_bytes, expected_sha256=encoded.sha256)
        self.assertEqual(decoded["envelope_type"], COMPACT_ENVELOPE_TYPE)
        self.assertEqual(decoded["source_snapshot"], source)
        self.assertEqual(decoded["calls"], rows["calls"])
        for family in rows:
            self.assertTrue(compare_row_family(family, rows[family], decoded[family]).passed)

    def test_parity_detects_same_id_with_changed_field(self) -> None:
        result = compare_row_family(
            "timings",
            [{"timing_observation_id": 1, "recorded_at_time": "a"}],
            [{"timing_observation_id": 1, "recorded_at_time": "b"}],
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.expected_count, result.actual_count)
        self.assertEqual(len(result.changed_rows), 1)

    def test_decode_rejects_corrupt_gzip_as_compact_error(self) -> None:
        with self.assertRaises(CompactEnvelopeError):
            decode_compact_envelope(b"not-gzip")

    def test_decode_rejects_envelope_with_wrong_serialization_contract(self) -> None:
        envelope = build_compact_envelope(
            source_snapshot={"snapshot_id": 1},
            row_sets={family: [] for family in ("calls", "timings", "situations", "service_sections", "attributes", "response_errors", "parser_gaps")},
        )
        envelope["serialization"] = "UTF8"
        with self.assertRaises(CompactEnvelopeError):
            encode_compact_envelope(envelope)

    def test_sql_normalization_retains_aliases_and_binary_payloads(self) -> None:
        normalized = normalize_sql_row(
            {
                "TimingObservationId": 3,
                "TimetabledTimeSourceText": None,
                "SourceEventIdentityBasisJsonGzip": b"\x01\xff",
                "ObservedAtUtc": datetime(2026, 10, 13, 12, 0, 0, 123456),
            },
            family="timings",
        )
        self.assertEqual(normalized["timing_observation_id"], 3)
        self.assertIsNone(normalized["timetabled_time"])
        self.assertEqual(normalized["source_event_identity_basis_json_gzip_hex"], "01ff")
        self.assertEqual(normalized["observed_at_utc"], "2026-10-13T12:00:00.1234560")

    def test_capacity_projection_exposes_budget_and_floor_gates(self) -> None:
        projection = capacity_projection(
            requests_per_hour=220,
            hours=24 * 7,
            bytes_per_snapshot=1024,
            weekly_budget_gib=10,
            windows_free_gib=18.983,
            windows_reserve_gib=8,
        )
        self.assertEqual(projection["requests"], 36960)
        self.assertTrue(projection["weekly_budget_pass"])
        self.assertTrue(projection["windows_reserve_pass"])
        self.assertTrue(projection["operational_floor_pass"])

    def test_capacity_calculator_rejects_logical_bytes_as_physical_evidence(self) -> None:
        args = type(
            "CapacityArgs",
            (),
            {
                "measurement_json": None,
                "bytes_per_snapshot": 1024,
                "measurement_basis": "SQL logical compressed bytes",
                "windows_free_gib": 18.983,
                "current_allocation_gib": None,
                "guard_baseline_gib": 21.62,
                "guard_max_cumulative_growth_gib": 17.0,
            },
        )()
        self.assertEqual(calculate_tiered50(args)["status"], "NO_GO")

    def test_capacity_plan_requires_full_history_peak_scope(self) -> None:
        plan = {
            "sample_snapshots": 10,
            "planned_snapshot_count": 100,
            "measurement_basis": "SQL Server physical allocation measurement",
            "physical_allocation_measured": True,
            "envelope_allocation_bytes": 10,
            "index_overhead_bytes": 20,
            "transaction_log_bytes": 30,
            "tempdb_bytes": 40,
            "rollback_bytes": 50,
            "peak_temporary_duplication_bytes": 60,
            "peak_additional_bytes": 210,
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(plan, handle)
            handle.flush()
            with self.assertRaises(ValueError):
                _load_capacity_plan(Path(handle.name))
            plan["allocation_scope"] = "FULL_HISTORY_MIGRATION_PEAK"
            handle.seek(0)
            handle.truncate()
            json.dump(plan, handle)
            handle.flush()
            self.assertEqual(_load_capacity_plan(Path(handle.name))["status"], "PASS")

    def test_nested_gzip_hex_representation_is_measured_explicitly(self) -> None:
        result = nested_gzip_hex_benchmark(
            '{"event":"TRIAS","stop":"Köln","payload":"' + ("same evidence " * 80) + '"}'
        )
        self.assertGreater(result["inner_gzip_bytes"], 0)
        self.assertGreater(result["nested_hex_wrapper_utf16le_bytes"], result["inner_gzip_bytes"])
        self.assertEqual(
            result["nested_overhead_bytes"],
            result["nested_outer_gzip_bytes"] - result["plain_outer_gzip_bytes"],
        )

    def test_retirement_parity_accepts_verified_units_only_with_field_comparison(self) -> None:
        evidence = {
            "status": "PASS",
            "run": {
                "units": [
                    {
                        "status": "VERIFIED",
                        "offline_parity": {"status": "PASS"},
                        "sql_readback_parity": {"status": "PASS"},
                    }
                ]
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(evidence, handle)
            handle.flush()
            self.assertEqual(_parity_gate(Path(handle.name))["status"], "PASS")

    def test_retirement_parity_rejects_verified_unit_without_comparison(self) -> None:
        evidence = {"status": "PASS", "units": [{"status": "VERIFIED"}]}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(evidence, handle)
            handle.flush()
            self.assertEqual(_parity_gate(Path(handle.name))["status"], "BLOCKED")

    def test_retirement_parity_rejects_offline_only_evidence(self) -> None:
        evidence = {
            "status": "PASS",
            "units": [
                {"status": "VERIFIED", "offline_parity": {"status": "PASS"}}
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(evidence, handle)
            handle.flush()
            self.assertEqual(_parity_gate(Path(handle.name))["status"], "BLOCKED")

    def test_retirement_approval_requires_explicit_recovery_details(self) -> None:
        evidence = {
            "approved": True,
            "approved_by": "human",
            "approved_at_utc": "2026-10-13T12:00:00Z",
            "sql_objects": ["stg.MddMacTimingObservation"],
            "verified_row_counts": {"timings": 1},
            "backup_location": "\\\\server\\share\\backup.bak",
            "backup_verification": "RESTORE VERIFYONLY PASS",
            "expected_space_recovery_gib": 1.0,
            "worst_case_temporary_storage_gib": 0.1,
            "rollback_procedure": "restore backup and recreate legacy objects",
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(evidence, handle)
            handle.flush()
            self.assertEqual(_approval_gate(Path(handle.name))["status"], "PASS")

    def test_compact_write_requires_explicit_compatibility_gate(self) -> None:
        settings = MssqlConnectionSettings(host="127.0.0.1", user="u", password="p")
        with self.assertRaises(MssqlSafetyError):
            MssqlPersistence(
                settings,
                collection_context=MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
                compact_observation_enabled=True,
                compact_observation_compatibility_verified=False,
            )

    def test_sql_artifacts_are_fail_closed_and_separate_cleanup(self) -> None:
        compact = (ROOT / "sql/mdd-mac-collector/compact-observation-storage-v1.sql").read_text(encoding="utf-8")
        retirement = (ROOT / "sql/mdd-mac-collector/retire-legacy-observation-storage-v1.sql").read_text(encoding="utf-8")
        self.assertIn("DECLARE @ApplyCompactStorage BIT = 0;", compact)
        self.assertIn("FutureWriteEnabled BIT", compact)
        self.assertIn("uspMddMacCompactStoreHistoricalEnvelope", compact)
        self.assertIn("incompatible column contract", compact)
        self.assertIn("Compact envelope identity or hash does not match", compact)
        self.assertNotRegex(compact, r"\b(?:DROP\s+TABLE|TRUNCATE\s+TABLE|DBCC\s+SHRINKFILE)\b")
        self.assertIn("DECLARE @ApplyLegacyRetirement BIT = 0;", retirement)
        self.assertRegex(retirement, r"\bDROP\s+TABLE\b")
        self.assertIn("LegacyRetirementApproved", retirement)
        self.assertIn("PermissionName", retirement)
        self.assertIn("CASE WHEN StateDesc = N'DENY' THEN N'DENY '", retirement)
        approval = (ROOT / "sql/mdd-mac-collector/approve-compact-observation-gates-v1.sql").read_text(encoding="utf-8")
        self.assertIn("Legacy retirement requires explicit Power BI/warehouse compatibility confirmation", approval)
        self.assertIn("ELSE LegacyRetirementApproved END", approval)
        backfill = (ROOT / "sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql").read_text(encoding="utf-8")
        self.assertIn("Another SQL session has an active transaction", backfill)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
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
    CAPACITY_PLAN_FORMAT_VERSION,
    COMPACT_MIGRATION_IMPLEMENTATION_VERSION,
    _capacity_gate,
    _expected_physical_field_records,
    _load_capacity_plan,
    _read_capacity,
    _recovery_decision,
    _schema_contract,
    _validate_capacity_plan,
)
from apply_compact_storage_migration import _batches  # noqa: E402
from reclaim_sql_datafile_space import _approval_gate, _parity_gate  # noqa: E402
from calculate_tiered50_storage_capacity import calculate as calculate_tiered50  # noqa: E402


def _capacity_plan_fixture() -> dict[str, object]:
    sample_ids = list(range(1, 11))
    components = {
        "envelope_allocation_bytes": 100,
        "index_overhead_bytes": 50,
        "transaction_log_bytes": 200,
        "tempdb_bytes": 300,
        "rollback_bytes": 400,
        "autogrowth_reserve_bytes": 50,
        "peak_temporary_duplication_bytes": 600,
    }
    file_sizes = [
        {
            "file_id": 1,
            "name": "CologneTransitIntelligence",
            "type": "ROWS",
            "volume": "C:",
            "physical_name": r"C:\CologneTransitIntelligence.mdf",
            "allocated_bytes": 10 * 1024**3,
            "used_bytes": 9 * 1024**3,
            "growth_value": 8192,
            "is_percent_growth": False,
            "max_size_pages": -1,
            "available_bytes": 16 * 1024**3,
        },
        {
            "file_id": 2,
            "name": "CologneTransitIntelligence_log",
            "type": "LOG",
            "volume": "C:",
            "physical_name": r"C:\CologneTransitIntelligence_log.ldf",
            "allocated_bytes": 2 * 1024**3,
            "used_bytes": 512 * 1024**2,
            "growth_value": 8192,
            "is_percent_growth": False,
            "max_size_pages": -1,
            "available_bytes": 16 * 1024**3,
        },
    ]
    plan: dict[str, object] = {
        "capacity_plan_format_version": CAPACITY_PLAN_FORMAT_VERSION,
        "migration_implementation_version": COMPACT_MIGRATION_IMPLEMENTATION_VERSION,
        "database_identity": {
            "server_name": "DataAnalyst-VM",
            "database_name": "CologneTransitIntelligence",
        },
        "current_production_file_sizes": file_sizes,
        "current_tempdb_file_sizes": [
            {
                "file_id": 1,
                "name": "tempdev",
                "type": "ROWS",
                "volume": "C:",
                "physical_name": r"C:\tempdb.mdf",
                "allocated_bytes": 1 * 1024**3,
                "used_bytes": 512 * 1024**2,
                "growth_value": 8192,
                "is_percent_growth": False,
                "max_size_pages": -1,
                "available_bytes": 16 * 1024**3,
            }
        ],
        "snapshot_population": {
            "count": 100,
            "first_snapshot_id": 1,
            "last_snapshot_id": 100,
        },
        "measurement_timestamp_utc": "2026-10-14T12:00:00Z",
        "measured_sample_selection": {
            "snapshot_ids": sample_ids,
            "selection_sha256": hashlib.sha256(
                json.dumps(sample_ids, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
        "physical_allocation_deltas": dict(components),
        "sample_snapshots": len(sample_ids),
        "planned_snapshot_count": 100,
        "measurement_basis": "SQL Server physical allocation measurement on a restored production-shape copy",
        "physical_allocation_measured": True,
        "allocation_scope": "FULL_HISTORY_MIGRATION_PEAK",
        **components,
    }
    plan["peak_additional_bytes"] = sum(components.values())
    return plan


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
                if "FROM tempdb.sys.database_files" in query:
                    return [{
                        "file_id": 1,
                        "name": "tempdev",
                        "type_desc": "ROWS",
                        "allocated_bytes": 1 * 1024**3,
                        "used_bytes": 512 * 1024**2,
                        "growth_value": 8192,
                        "is_percent_growth": False,
                        "max_size_pages": -1,
                        "physical_name": r"C:\\tempdb.mdf",
                        "volume_mount_point": "C:",
                        "available_bytes": 14 * 1024**3,
                    }]
                if "file_id, name, type_desc" in query:
                    return [{
                        "file_id": 1,
                        "name": "CologneTransitIntelligence",
                        "type_desc": "ROWS",
                        "allocated_bytes": 29 * 1024**3,
                        "used_bytes": 28 * 1024**3,
                        "growth_value": 8192,
                        "is_percent_growth": False,
                        "max_size_pages": -1,
                        "physical_name": r"C:\\CologneTransitIntelligence.mdf",
                        "volume_mount_point": "C:",
                        "available_bytes": 14 * 1024**3,
                    }]
                if "volume_mount_point" in query:
                    return [{"volume_mount_point": "C:", "total_bytes": 64 * 1024**3, "available_bytes": 14 * 1024**3}]
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

    def test_envelope_hash_is_invariant_to_sql_row_return_order(self) -> None:
        source = {"snapshot_id": 41, "run_id": "run-α"}
        rows = {
            "calls": [
                {"call_observation_id": 2, "value": "second"},
                {"call_observation_id": 1, "value": "first"},
            ],
            "timings": [
                {"timing_observation_id": 4, "value": "fourth"},
                {"timing_observation_id": 3, "value": "third"},
            ],
            "situations": [],
            "service_sections": [],
            "attributes": [],
            "response_errors": [],
            "parser_gaps": [],
        }
        reversed_rows = {
            family: list(reversed(values)) for family, values in rows.items()
        }
        first = encode_compact_envelope(
            build_compact_envelope(source_snapshot=source, row_sets=rows)
        )
        second = encode_compact_envelope(
            build_compact_envelope(source_snapshot=source, row_sets=reversed_rows)
        )
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(
            [row["call_observation_id"] for row in build_compact_envelope(source_snapshot=source, row_sets=rows)["calls"]],
            [1, 2],
        )

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

    def test_capacity_gate_credits_measured_growth_once_and_tracks_external_consumption(self) -> None:
        plan = _capacity_plan_fixture()
        gib = 1024**3
        baseline = {
            "volumes": [{"volume": "C:", "available_bytes": 16 * gib}],
            "files": [
                {"type": "ROWS", "volume": "C:", "allocated_bytes": 10 * gib},
                {"type": "LOG", "volume": "C:", "allocated_bytes": 2 * gib},
            ],
            "tempdb_files": [{"type": "ROWS", "volume": "C:", "allocated_bytes": 1 * gib}],
        }
        current = {
            "volumes": [{"volume": "C:", "available_bytes": 16 * gib - 250}],
            "files": [
                {"type": "ROWS", "volume": "C:", "allocated_bytes": 10 * gib + 100},
                {"type": "LOG", "volume": "C:", "allocated_bytes": 2 * gib + 20},
            ],
            "tempdb_files": [{"type": "ROWS", "volume": "C:", "allocated_bytes": 1 * gib + 30}],
            "log": {"used_percent": 25.0},
            "active_user_transactions": 0,
            "active_collector_sessions": 0,
        }
        result = _capacity_gate(
            current,
            log_limit=70.0,
            required_extra_bytes=int(plan["peak_additional_bytes"]),
            capacity_plan=plan,
            baseline_capacity=baseline,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["observed_allocation_deltas"], {"data": 100, "log": 20, "tempdb": 30})
        self.assertEqual(result["unexpected_external_consumption_bytes"], 100)
        self.assertEqual(result["remaining_allocation_bytes"], 1550)
        self.assertLess(result["remaining_allocation_bytes"], plan["peak_additional_bytes"])
        blocked_files = [dict(item) for item in current["files"]]
        blocked_files[0]["max_size_pages"] = (10 * gib + 100) // 8192
        blocked_capacity = {**current, "files": blocked_files}
        blocked = _capacity_gate(
            blocked_capacity,
            log_limit=70.0,
            capacity_plan=plan,
            baseline_capacity=baseline,
        )
        self.assertEqual(blocked["autogrowth_status"], "BLOCKED")
        self.assertEqual(blocked["status"], "BLOCKED")

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
        plan = _capacity_plan_fixture()
        plan.pop("allocation_scope")
        with self.assertRaises(ValueError):
            _validate_capacity_plan(plan, require_independent_evidence=False)
        plan["allocation_scope"] = "FULL_HISTORY_MIGRATION_PEAK"
        self.assertEqual(
            _validate_capacity_plan(plan, require_independent_evidence=False)["status"],
            "PASS",
        )

    def test_capacity_plan_rejects_unsigned_self_declared_evidence(self) -> None:
        plan = _capacity_plan_fixture()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as handle:
            json.dump(plan, handle)
            handle.flush()
            with self.assertRaises(ValueError):
                _load_capacity_plan(Path(handle.name))

    def test_schema_contract_compares_physical_metadata_and_strict_nullability(self) -> None:
        source = [
            {
                "column_id": 1,
                "name": "SnapshotId",
                "key": "snapshot_id",
                "type_name": "bigint",
                "max_length": 8,
                "precision": 19,
                "scale": 0,
                "is_nullable": False,
            },
            {
                "column_id": 2,
                "name": "Value",
                "key": "value",
                "type_name": "nvarchar",
                "max_length": 200,
                "precision": 0,
                "scale": 0,
                "is_nullable": True,
            },
        ]
        view = [dict(item) for item in source]
        self.assertEqual(_schema_contract("calls", source, view)["status"], "PASS")
        view[0]["precision"] = 18
        view[0]["is_nullable"] = True
        result = _schema_contract("calls", source, view)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item["kind"] == "precision" for item in result["mismatches"]))
        self.assertTrue(any(item["kind"] == "nullability" for item in result["mismatches"]))

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

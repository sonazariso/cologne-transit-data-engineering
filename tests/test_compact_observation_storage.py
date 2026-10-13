from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    COMPACT_ENVELOPE_TYPE,
    build_compact_envelope,
    capacity_projection,
    compare_row_family,
    decode_compact_envelope,
    encode_compact_envelope,
    normalize_sql_row,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    MssqlConnectionSettings,
    MssqlPersistence,
    MssqlSafetyError,
)


class CompactObservationStorageTests(unittest.TestCase):
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
        self.assertNotRegex(compact, r"\b(?:DROP\s+TABLE|TRUNCATE\s+TABLE|DBCC\s+SHRINKFILE)\b")
        self.assertIn("DECLARE @ApplyLegacyRetirement BIT = 0;", retirement)
        self.assertRegex(retirement, r"\bDROP\s+TABLE\b")
        self.assertIn("LegacyRetirementApproved", retirement)
        self.assertIn("PermissionName", retirement)


if __name__ == "__main__":
    unittest.main()

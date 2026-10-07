from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    SamplingEra,
)
from cologne_transit_collector.macos_runner import require_cutover_gate  # noqa: E402
from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings, MssqlPersistence  # noqa: E402


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.cursor_instance = _Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class MssqlRuntimeTests(unittest.TestCase):
    def test_cutover_gate_defaults_closed_without_network(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            require_cutover_gate({})

    def test_cutover_gate_rejects_unconfirmed_prior_runtime(self) -> None:
        values = {
            MAC_SQL_ENABLED_ENV: "1",
            MAC_SQL_RUNTIME_STATE_ENV: "UNCONFIRMED",
            MAC_SQL_CUTOVER_APPROVED_ENV: "1",
            MAC_SQL_TARGET_SCOPE_ENV: "LEGACY7_EXPLICIT_APPROVAL",
            "MDD_API_KEY": "valid-placeholder",
        }
        with self.assertRaisesRegex(RuntimeError, "not confirmed retired"):
            require_cutover_gate(values)

    def test_loader_preserves_existing_seven_target_identity_and_limits(self) -> None:
        rows = [
            {
                "SamplingTargetId": index,
                "StopPointRef": stop_ref,
                "TargetName": name,
                "NumberOfResults": 5,
                "IsEnabled": True,
            }
            for index, stop_ref, name in (
                (1, "de:05315:11110", "Köln Heumarkt"),
                (2, "de:05315:11201", "Köln Hbf"),
                (3, "de:05315:12711", "Köln Rodenkirchen Bf"),
                (4, "de:05315:14201", "Köln Bf Ehrenfeld"),
                (5, "de:05315:16601", "Köln Worringen S-Bahn"),
                (6, "de:05315:17311", "Köln Porz Markt"),
                (7, "de:05315:19201", "Köln Bf Mülheim"),
            )
        ]
        connection = _Connection(rows)
        adapter = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: connection,
            raw_evidence_directory=None,
        )
        targets = adapter.load_existing_sampling_targets()
        self.assertEqual(len(targets), 7)
        self.assertEqual([target.legacy_sampling_target_id for target in targets], list(range(1, 8)))
        self.assertTrue(all(target.sampling_era is SamplingEra.LEGACY7 for target in targets))
        self.assertEqual({target.number_of_results for target in targets}, {5})
        self.assertTrue(connection.closed)

    def test_launchd_artifact_is_disabled_and_secret_free(self) -> None:
        path = ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-collector.plist"
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertTrue(plist["Disabled"])
        self.assertFalse(plist["RunAtLoad"])
        self.assertEqual(plist["StartInterval"], 300)
        self.assertEqual(plist["EnvironmentVariables"][MAC_SQL_ENABLED_ENV], "0")
        self.assertNotIn("MDD_API_KEY", plist["EnvironmentVariables"])


if __name__ == "__main__":
    unittest.main()

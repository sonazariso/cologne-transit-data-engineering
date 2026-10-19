from __future__ import annotations

import plistlib
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_SQL_VALIDATION_POLICY_ENV,
    MAC_SQL_REQUIRED_VALIDATION_POLICY,
    SamplingEra,
)
from apply_mssql_migration import (  # noqa: E402
    REQUIRED_MIGRATION_OBJECTS,
    execute_batches_atomically,
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
        if self.statements and "MddRealtimeSamplingSlot" in self.statements[-1][0]:
            return [
                {"SamplingSlot": slot, "SamplingTargetId": target_id}
                for slot, target_id in (
                    (1, 2),
                    (2, 7),
                    (3, 1),
                    (4, 4),
                    (5, 6),
                    (6, 2),
                    (7, 7),
                    (8, 1),
                    (9, 5),
                    (10, 3),
                )
            ]
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


class _AtomicCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.validation_query = False

    def execute(self, statement, params=None):
        self.statements.append(statement)
        self.validation_query = "ObjectName" in statement

    def fetchall(self):
        return self.rows if self.validation_query else []


class _AtomicConnection:
    def __init__(self, rows):
        self.cursor_instance = _AtomicCursor(rows)
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


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

    def test_cutover_gate_requires_offline_reconciliation_policy(self) -> None:
        values = {
            MAC_SQL_ENABLED_ENV: "1",
            MAC_SQL_RUNTIME_STATE_ENV: "CONFIRMED_RETIRED",
            MAC_SQL_CUTOVER_APPROVED_ENV: "1",
            MAC_SQL_TARGET_SCOPE_ENV: "LEGACY7_EXPLICIT_APPROVAL",
            "MDD_API_KEY": "valid-placeholder",
        }
        with self.assertRaisesRegex(RuntimeError, "offline SQLite reconciliation"):
            require_cutover_gate(values)
        values[MAC_SQL_VALIDATION_POLICY_ENV] = MAC_SQL_REQUIRED_VALIDATION_POLICY
        self.assertIsNone(require_cutover_gate(values))

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
        self.assertEqual([target.phase_count for target in targets], [10] * 7)
        self.assertEqual(targets[0].legacy_sampling_slots, (3, 8))
        self.assertTrue(connection.closed)

    def test_launchd_artifact_is_disabled_and_secret_free(self) -> None:
        path = ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-collector.plist"
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertTrue(plist["Disabled"])
        self.assertFalse(plist["RunAtLoad"])
        self.assertEqual(plist["StartInterval"], 300)
        self.assertEqual(plist["EnvironmentVariables"][MAC_SQL_ENABLED_ENV], "0")
        self.assertEqual(
            plist["EnvironmentVariables"][MAC_SQL_VALIDATION_POLICY_ENV],
            MAC_SQL_REQUIRED_VALIDATION_POLICY,
        )
        self.assertNotIn("MDD_API_KEY", plist["EnvironmentVariables"])

        interpreter = "/Library/Developer/CommandLineTools/usr/bin/python3"
        self.assertEqual(plist["ProgramArguments"][0], interpreter)
        self.assertTrue(Path(interpreter).is_absolute())
        self.assertNotEqual(plist["ProgramArguments"][0], "/usr/bin/env")
        if sys.platform == "darwin":
            self.assertTrue(Path(interpreter).is_file())
            self.assertTrue(os.access(interpreter, os.X_OK))
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                [interpreter, "-c", "import cologne_transit_collector, pymssql"],
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_migration_commits_once_after_required_object_validation(self) -> None:
        rows = [{"ObjectName": name} for name in REQUIRED_MIGRATION_OBJECTS]
        connection = _AtomicConnection(rows)
        self.assertEqual(execute_batches_atomically(connection, ["BATCH_1", "BATCH_2"]), 2)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(connection.cursor_instance.statements[0], "SET XACT_ABORT ON;")

    def test_migration_rolls_back_when_required_object_validation_fails(self) -> None:
        connection = _AtomicConnection([])
        with self.assertRaisesRegex(RuntimeError, "missing required objects"):
            execute_batches_atomically(connection, ["BATCH_1"])
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)


if __name__ == "__main__":
    unittest.main()

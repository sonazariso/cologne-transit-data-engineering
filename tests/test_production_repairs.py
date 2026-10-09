from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from apply_mssql_migration import compare_sampling_target_identities  # noqa: E402
from repair_mdd_mac_schema import (  # noqa: E402
    RESUME_EVENT_OBJECT,
    inventory_schema,
    main as repair_main,
    resume_event_contract,
    resume_event_ddl_batch,
)
from tiered50_production_evidence import (  # noqa: E402
    _evaluate_admin_permissions,
    _evaluate_runtime_health,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    FULL_SCHEMA_ROUTINES,
    FULL_SCHEMA_TABLES,
    MssqlConnectionSettings,
    MssqlPersistence,
)


class _InventoryCursor:
    def __init__(self) -> None:
        self.result: list[object] = []

    def execute(self, statement: str, *_args: object) -> None:
        normalized = statement.casefold()
        if "from sys.objects" in normalized and "sys.columns" not in normalized:
            expected = tuple(dict.fromkeys(FULL_SCHEMA_TABLES + FULL_SCHEMA_ROUTINES))
            self.result = [
                {
                    "object_name": name,
                    "object_type": "V" if ".vw" in name else "U",
                    "object_type_desc": "USER_TABLE",
                    "object_id": index + 1,
                }
                for index, name in enumerate(expected)
                if name != RESUME_EVENT_OBJECT
            ]
        else:
            self.result = []

    def fetchall(self) -> list[object]:
        return list(self.result)


class _InventoryConnection:
    def __init__(self) -> None:
        self.cursor_instance = _InventoryCursor()

    def cursor(self) -> _InventoryCursor:
        return self.cursor_instance


class _BaselineCursor:
    def __init__(self, target_ids: list[int]) -> None:
        self.target_ids = target_ids
        self.statement = ""

    def execute(self, statement: str, *_args: object) -> None:
        self.statement = statement

    def fetchone(self) -> object:
        if "CollectorRunId" in self.statement:
            return None
        return {
            "collector_runs": 0,
            "sampling_targets": 7,
            "stop_observations": 0,
            "situation_observations": 0,
            "stop_situation_links": 0,
            "latest_stop_observation_utc": None,
            "latest_situation_observation_utc": None,
        }

    def fetchall(self) -> list[object]:
        if "SamplingTargetId" in self.statement:
            return [
                {
                    "SamplingTargetId": target_id,
                    "identity_source": "LEGACY7" if target_id <= 7 else "TIERED50",
                }
                for target_id in self.target_ids
            ]
        return []


class _BaselineConnection:
    def __init__(self, target_ids: list[int]) -> None:
        self.cursor_instance = _BaselineCursor(target_ids)

    def cursor(self) -> _BaselineCursor:
        return self.cursor_instance

    def close(self) -> None:
        return None


class ProductionRepairTests(unittest.TestCase):
    def test_migration_preserves_historical_seven_target_identities(self) -> None:
        result = compare_sampling_target_identities(range(1, 8), range(1, 8))
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["preserved"])

    def test_migration_preserves_current_fifty_target_identities(self) -> None:
        identities = list(range(1, 51))
        result = compare_sampling_target_identities(identities, identities)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["after"], identities)
        self.assertNotIn("seven", str(result).casefold())

    def test_migration_reports_identity_loss_without_requiring_seven_targets(self) -> None:
        result = compare_sampling_target_identities(range(1, 51), list(range(1, 50)))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing_after_migration"], [50])

    def test_baseline_reads_materialized_tiered50_ids_when_present(self) -> None:
        connection = _BaselineConnection(list(range(1, 51)))
        adapter = MssqlPersistence(
            settings=MssqlConnectionSettings(host="sql.example", user="admin", password="unused"),
            connection_factory=lambda: connection,
        )
        baseline = adapter.baseline()
        self.assertEqual(baseline["sampling_target_ids"], list(range(1, 51)))
        self.assertEqual(baseline["sampling_target_id_sources"]["LEGACY7"], 7)
        self.assertEqual(baseline["sampling_target_id_sources"]["TIERED50"], 43)

    def test_baseline_reads_historical_seven_ids_when_tiered50_table_is_absent(self) -> None:
        connection = _BaselineConnection(list(range(1, 8)))
        adapter = MssqlPersistence(
            settings=MssqlConnectionSettings(host="sql.example", user="admin", password="unused"),
            connection_factory=lambda: connection,
        )
        baseline = adapter.baseline()
        self.assertEqual(baseline["sampling_target_ids"], list(range(1, 8)))
        self.assertEqual(baseline["sampling_target_id_sources"]["LEGACY7"], 7)

    def test_inventory_detects_resume_event_as_the_only_missing_source_object(self) -> None:
        result = inventory_schema(_InventoryConnection())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_defined_objects"]["missing"], [RESUME_EVENT_OBJECT])
        self.assertTrue(result["source_defined_objects"]["resume_event_is_only_missing"])

    def test_repair_uses_authoritative_idempotent_resume_event_batch(self) -> None:
        batch = resume_event_ddl_batch()
        contract = resume_event_contract(batch)
        self.assertIn("IF OBJECT_ID(N'ctl.MddMacProductionResumeEvent', N'U') IS NULL", batch)
        self.assertEqual(
            contract["columns"],
            [
                "ResumeEventId",
                "ResumeKey",
                "PanelVersion",
                "EraCode",
                "SamplingEraId",
                "OriginalActivationAtUtc",
                "ResumeAtUtc",
                "LaunchAgentLabel",
                "EvidenceJson",
                "RecordedAtUtc",
            ],
        )
        self.assertEqual(
            set(contract["constraints"]),
            {
                "PK_ctl_MddMacProductionResumeEvent",
                "UQ_ctl_MddMacProductionResumeEvent_Key",
                "DF_ctl_MddMacProductionResumeEvent_RecordedAtUtc",
                "CK_ctl_MddMacProductionResumeEvent_Status",
                "FK_ctl_MddMacProductionResumeEvent_Era",
            },
        )

    def test_repair_cli_requires_explicit_production_ddl_authorization(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = repair_main(["--apply"])
        self.assertEqual(status, 1)
        self.assertIn("--authorize-production-ddl", output.getvalue())

    def test_sql_server_2025_sysadmin_and_successful_dmvs_overcome_unknown_metadata(self) -> None:
        permissions = {
            "product_major_version": 17,
            "is_sysadmin": 1,
            "is_serveradmin": None,
            "is_securityadmin": None,
            "is_dbcreator": None,
            "is_db_owner": None,
            "has_view_definition": None,
            "has_view_database_state": None,
            "has_view_server_state": None,
            "has_view_server_performance_state": None,
            "has_backup_database": None,
            "has_create_any_database": None,
            "has_database_select": None,
            "has_msdb_backupset_select": None,
            "has_msdb_backupmediafamily_select": None,
        }
        dmv_queries = {
            "capacity.volume_capacity_dm_os_volume_stats": {"status": "PASS"},
            "capacity.database_log_space_dm_db_log_space_usage": {"status": "PASS"},
        }
        result = _evaluate_admin_permissions(permissions, query_statuses=dmv_queries)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["permission_checks"]["server_dmv_state"])
        self.assertEqual(
            result["permission_states"]["server_dmv_state"],
            "GRANTED_BY_SUCCESSFUL_DMV_ACCESS",
        )
        self.assertEqual(result["unknown_permissions"], [])

    def test_permission_denied_dmv_is_not_passed_by_sysadmin_metadata(self) -> None:
        permissions = {
            "product_major_version": 17,
            "is_sysadmin": 1,
            "has_view_server_performance_state": None,
            "has_view_definition": 1,
            "has_view_database_state": 1,
            "has_msdb_backupset_select": 1,
            "has_msdb_backupmediafamily_select": 1,
            "has_database_select": 1,
            "has_create_any_database": 1,
        }
        result = _evaluate_admin_permissions(
            permissions,
            query_statuses={
                "capacity.volume_capacity_dm_os_volume_stats": {
                    "status": "NOT_PROVEN",
                    "error_code": 297,
                },
                "capacity.database_log_space_dm_db_log_space_usage": {
                    "status": "NOT_PROVEN",
                    "error_code": 297,
                },
            },
        )
        self.assertEqual(result["status"], "NOT_PROVEN")
        self.assertIn("server_dmv_state", result["missing_permissions"])
        self.assertIn("server_dmv_state", result["denied_permissions"])

    def test_restricted_non_sysadmin_permissions_remain_not_proven(self) -> None:
        result = _evaluate_admin_permissions(
            {
                "product_major_version": 17,
                "is_sysadmin": 0,
                "has_view_definition": 0,
                "has_view_database_state": 0,
                "has_view_server_performance_state": 0,
                "has_msdb_backupset_select": 0,
                "has_msdb_backupmediafamily_select": 0,
                "has_database_select": 0,
                "has_create_any_database": 0,
                "is_dbcreator": 0,
            },
            query_statuses={
                "capacity.volume_capacity_dm_os_volume_stats": {"status": "NOT_PROVEN", "error_code": 297},
                "capacity.database_log_space_dm_db_log_space_usage": {"status": "NOT_PROVEN", "error_code": 297},
            },
        )
        self.assertEqual(result["status"], "NOT_PROVEN")
        self.assertGreaterEqual(len(result["denied_permissions"]), 3)

    def test_runtime_health_accepts_driver_bytes_for_online_simple_database(self) -> None:
        result = _evaluate_runtime_health(
            {"status": "PASS"},
            {
                "database_name": b"CologneTransitIntelligence",
                "database_status": b"ONLINE",
                "recovery_model": b"SIMPLE",
            },
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["database_status"], "ONLINE")
        self.assertEqual(result["recovery_model"], "SIMPLE")

    def test_runtime_health_accepts_valid_strings(self) -> None:
        result = _evaluate_runtime_health(
            {"status": "PASS"},
            {
                "database_name": "CologneTransitIntelligence",
                "database_status": "ONLINE",
                "recovery_model": "FULL",
            },
        )
        self.assertEqual(result["status"], "PASS")

    def test_runtime_health_rejects_null_malformed_offline_and_unavailable_values(self) -> None:
        cases = (
            (None, "NULL row"),
            ({"database_name": "CologneTransitIntelligence", "database_status": None, "recovery_model": "SIMPLE"}, "NULL status"),
            ({"database_name": "CologneTransitIntelligence", "database_status": "OFFLINE", "recovery_model": "SIMPLE"}, "offline"),
            ({"database_name": "CologneTransitIntelligence", "database_status": b"ONLINE", "recovery_model": b"\xff"}, "malformed"),
        )
        for row, label in cases:
            with self.subTest(label=label):
                result = _evaluate_runtime_health({"status": "PASS"}, row)
                self.assertEqual(result["status"], "NOT_PROVEN")
        unavailable = _evaluate_runtime_health(
            {"status": "NOT_PROVEN", "error_code": 297},
            {"database_name": "CologneTransitIntelligence", "database_status": "ONLINE", "recovery_model": "SIMPLE"},
        )
        self.assertEqual(unavailable["status"], "NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()

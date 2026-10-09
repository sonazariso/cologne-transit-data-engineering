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

import repair_mdd_mac_schema as repair_module  # noqa: E402
from apply_mssql_migration import compare_sampling_target_identities  # noqa: E402
from repair_mdd_mac_schema import (  # noqa: E402
    RESUME_EVENT_OBJECT,
    apply_resume_event_repair,
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


class _RepairConnection:
    def __init__(self, *, commit_error: BaseException | None = None, rollback_error: BaseException | None = None) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False
        self.cursor_value = _RepairCursor()

    def cursor(self) -> object:
        return self.cursor_value

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.closed = True


class _RepairCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, *_args: object) -> None:
        self.statements.append(statement)


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
        self.assertEqual(contract["column_definitions"][1]["data_type"], "NVARCHAR")
        self.assertEqual(contract["column_definitions"][1]["type_args"], 240)
        self.assertEqual(contract["foreign_keys"][0]["referenced_columns"], ["SamplingEraId"])
        self.assertEqual(
            {index["name"] for index in contract["indexes"]},
            {
                "PK_ctl_MddMacProductionResumeEvent",
                "UQ_ctl_MddMacProductionResumeEvent_Key",
            },
        )

    @staticmethod
    def _missing_resume_inventory() -> dict[str, object]:
        return {"source_defined_objects": {"missing": [RESUME_EVENT_OBJECT]}}

    @staticmethod
    def _verification(status: str, object_present: bool) -> dict[str, object]:
        return {"status": status, "checks": {"object_present": object_present}}

    def _apply_with_verifications(
        self,
        verifications: list[dict[str, object]],
        *,
        connection: _RepairConnection | None = None,
        reconciliation: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], _RepairConnection]:
        active_connection = connection or _RepairConnection()
        settings = MssqlConnectionSettings(host="sql.example", user="admin", password="unused")
        with patch.object(repair_module, "connect_mssql", return_value=active_connection), patch.object(
            repair_module, "_acquire_schema_repair_lock"
        ), patch.object(repair_module, "_release_schema_repair_lock"), patch.object(
            repair_module, "_schema_verification", side_effect=verifications
        ), patch.object(
            repair_module,
            "_reconcile_schema_state",
            return_value=reconciliation
            or {
                "status": "NOT_PROVEN",
                "object_exists": False,
                "schema_verification": self._verification("NOT_PROVEN", False),
            },
        ):
            result = apply_resume_event_repair(
                settings,
                inventory=self._missing_resume_inventory(),
                backup={"status": "PASS"},
                competing={"status": "PASS"},
            )
        return result, active_connection

    def test_repair_reports_successful_rollback_explicitly(self) -> None:
        result, connection = self._apply_with_verifications(
            [
                self._verification("NOT_PROVEN", False),
                self._verification("NOT_PROVEN", False),
            ]
        )
        self.assertEqual(result["creation_status"], "CREATION_FAILED")
        self.assertTrue(result["transaction_execution_started"])
        self.assertFalse(result["commit_attempted"])
        self.assertTrue(result["rollback_attempted"])
        self.assertTrue(result["rollback_confirmed"])
        self.assertTrue(result["transaction_rolled_back"])
        self.assertFalse(result["transaction_final_state_unknown"])
        self.assertEqual(connection.rollback_calls, 1)

    def test_repair_does_not_claim_rollback_when_rollback_fails(self) -> None:
        result, connection = self._apply_with_verifications(
            [
                self._verification("NOT_PROVEN", False),
                self._verification("NOT_PROVEN", False),
            ],
            connection=_RepairConnection(rollback_error=RuntimeError("rollback connection lost")),
        )
        self.assertEqual(result["creation_status"], "CREATION_FAILED")
        self.assertTrue(result["rollback_attempted"])
        self.assertFalse(result["rollback_confirmed"])
        self.assertFalse(result["transaction_rolled_back"])
        self.assertTrue(result["transaction_final_state_unknown"])
        self.assertEqual(result["rollback_error"]["reason"], "rollback connection lost")
        self.assertEqual(connection.rollback_calls, 1)

    def test_repair_preserves_verification_failure_and_rollback_state(self) -> None:
        result, _connection = self._apply_with_verifications(
            [
                self._verification("NOT_PROVEN", False),
                self._verification("NOT_PROVEN", False),
            ],
            reconciliation={
                "status": "NOT_PROVEN",
                "object_exists": False,
                "schema_verification": self._verification("NOT_PROVEN", False),
            },
        )
        self.assertIn("schema_verification_before_commit", result)
        self.assertEqual(result["schema_verification_before_commit"]["status"], "NOT_PROVEN")
        self.assertTrue(result["rollback_confirmed"])
        self.assertFalse(result["commit_confirmed"])

    def test_repair_treats_commit_exception_as_unknown_and_reconciles(self) -> None:
        result, connection = self._apply_with_verifications(
            [
                self._verification("NOT_PROVEN", False),
                self._verification("PASS", True),
            ],
            connection=_RepairConnection(commit_error=RuntimeError("commit acknowledgement lost")),
            reconciliation={
                "status": "PASS",
                "object_exists": True,
                "schema_verification": self._verification("PASS", True),
            },
        )
        self.assertEqual(result["creation_status"], "COMMIT_OUTCOME_UNKNOWN")
        self.assertTrue(result["commit_attempted"])
        self.assertFalse(result["commit_confirmed"])
        self.assertFalse(result["rollback_attempted"])
        self.assertTrue(result["transaction_final_state_unknown"])
        self.assertTrue(result["object_exists_after_operation"])
        self.assertIn("original_error", result)
        self.assertEqual(connection.rollback_calls, 0)

    def test_repair_does_not_call_idempotent_batch_when_another_operation_created_valid_table(self) -> None:
        result, connection = self._apply_with_verifications(
            [self._verification("PASS", True)]
        )
        self.assertEqual(result["creation_status"], "CREATED_OR_CHANGED_BY_ANOTHER_OPERATION")
        self.assertFalse(result["transaction_execution_started"])
        self.assertFalse(result["commit_attempted"])
        self.assertFalse(result["created_objects"])
        self.assertFalse(any("CREATE TABLE" in statement.upper() for statement in connection.cursor_value.statements))

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

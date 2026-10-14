from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_historical_compact_observations import (  # noqa: E402
    ISOLATED_LAB_EXECUTION_ENVIRONMENT,
    PRODUCTION_EXECUTION_ENVIRONMENT,
    _capacity_gate,
    _validate_capacity_plan,
)
from reclaim_sql_datafile_space import (  # noqa: E402
    _parity_gate,
    _powerbi_gate,
    _retirement_reserve_gate,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    RuntimeConfigurationError,
    resolve_isolated_lab_connection_settings,
)


class _SecretProvider:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[tuple[str, str]] = []

    def get(self, *, service: str, account: str) -> str:
        self.calls.append((service, account))
        return self.value


def _strict_plan() -> dict[str, object]:
    component_values = {
        "envelope_allocation_bytes": 100,
        "index_overhead_bytes": 50,
        "transaction_log_bytes": 200,
        "tempdb_bytes": 300,
        "rollback_bytes": 400,
        "autogrowth_reserve_bytes": 50,
        "peak_temporary_duplication_bytes": 600,
    }
    snapshot_ids = list(range(1, 101))
    file_sizes = [
        {
            "file_id": 1,
            "name": "data",
            "type": "ROWS",
            "volume": "C:",
            "physical_name": r"C:\data.mdf",
            "allocated_bytes": 10 * 1024**3,
            "used_bytes": 9 * 1024**3,
            "growth_value": 8192,
            "is_percent_growth": False,
            "max_size_pages": -1,
            "available_bytes": 20 * 1024**3,
        },
        {
            "file_id": 2,
            "name": "log",
            "type": "LOG",
            "volume": "C:",
            "physical_name": r"C:\data.ldf",
            "allocated_bytes": 2 * 1024**3,
            "used_bytes": 1 * 1024**3,
            "growth_value": 8192,
            "is_percent_growth": False,
            "max_size_pages": -1,
            "available_bytes": 20 * 1024**3,
        },
    ]
    population = {
        "count": len(snapshot_ids),
        "first_snapshot_id": snapshot_ids[0],
        "last_snapshot_id": snapshot_ids[-1],
        "snapshot_ids": snapshot_ids,
        "snapshot_ids_sha256": hashlib.sha256(
            json.dumps(snapshot_ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    plan: dict[str, object] = {
        "capacity_plan_format_version": "CTI_SQL_PHYSICAL_CAPACITY_PLAN_V2",
        "migration_implementation_version": "compact-historical-migration-v2",
        "execution_environment": PRODUCTION_EXECUTION_ENVIRONMENT,
        "database_identity": {
            "server_name": "DataAnalyst-VM",
            "instance_name": "MSSQLSERVER",
            "database_name": "CologneTransitIntelligence",
            "database_id": 7,
            "database_guid": "00000000-0000-0000-0000-000000000007",
        },
        "current_production_file_sizes": file_sizes,
        "current_tempdb_file_sizes": [dict(file_sizes[0], file_id=1, name="tempdev", physical_name=r"C:\tempdb.mdf")],
        "snapshot_population": population,
        "snapshot_ids_sha256": population["snapshot_ids_sha256"],
        "measurement_timestamp_utc": "2026-10-14T12:00:00Z",
        "measured_sample_selection": {
            "snapshot_ids": [1, 50, 100],
            "selection_sha256": hashlib.sha256(
                json.dumps([1, 50, 100], separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
        "physical_allocation_deltas": dict(component_values),
        "sample_snapshots": 3,
        "planned_snapshot_count": len(snapshot_ids),
        "measurement_basis": "measured SQL physical allocation on a restored full-history copy",
        "physical_allocation_measured": True,
        "allocation_scope": "FULL_HISTORY_MIGRATION_PEAK",
        "external_disk_consumption_reserve_bytes": 700,
        "reserve_measurements": {},
        **component_values,
    }
    plan["peak_additional_bytes"] = sum(component_values.values()) + 700
    plan["reserve_measurements"] = {
        name: {"status": "PASS", "bytes": value}
        for name, value in {
            **{f"{name}": value for name, value in component_values.items()},
            "external_disk_consumption_reserve_bytes": 700,
        }.items()
        if name in {
            "transaction_log_bytes",
            "tempdb_bytes",
            "rollback_bytes",
            "autogrowth_reserve_bytes",
            "peak_temporary_duplication_bytes",
            "external_disk_consumption_reserve_bytes",
        }
    }
    plan["reserve_measurements"]["index_overhead_bytes"] = {"status": "PASS", "bytes": 50}
    plan["reserve_measurements"]["ddl_index_reserve_bytes"] = {"status": "PASS", "bytes": 50}
    return plan


class S149StorageRecoveryTests(unittest.TestCase):
    def test_strict_plan_rejects_incomplete_snapshot_population(self) -> None:
        plan = _strict_plan()
        plan["snapshot_population"] = dict(plan["snapshot_population"], snapshot_ids=list(range(1, 100)))
        with self.assertRaisesRegex(ValueError, "snapshot_ids do not match snapshot count"):
            _validate_capacity_plan(
                plan,
                require_independent_evidence=False,
                require_live_binding=True,
                now=datetime(2026, 10, 14, 12, 10, tzinfo=timezone.utc),
            )

    def test_strict_plan_rejects_stale_evidence_and_wrong_environment(self) -> None:
        plan = _strict_plan()
        plan["measurement_timestamp_utc"] = "2026-10-14T10:00:00Z"
        with self.assertRaisesRegex(ValueError, "stale"):
            _validate_capacity_plan(
                plan,
                require_independent_evidence=False,
                require_live_binding=True,
                now=datetime(2026, 10, 14, 12, 10, tzinfo=timezone.utc),
            )
        plan = _strict_plan()
        plan["execution_environment"] = ISOLATED_LAB_EXECUTION_ENVIRONMENT
        with self.assertRaisesRegex(ValueError, "execution environment"):
            _validate_capacity_plan(
                plan,
                require_independent_evidence=False,
                require_live_binding=True,
                expected_execution_environment=PRODUCTION_EXECUTION_ENVIRONMENT,
                now=datetime(2026, 10, 14, 12, 10, tzinfo=timezone.utc),
            )

    def test_capacity_gate_preserves_windows_safety_floor(self) -> None:
        capacity = {
            "volumes": [{"volume": "C:\\", "available_bytes": int(13.86 * 1024**3)}],
            "files": [],
            "tempdb_files": [],
            "log": {"used_percent": 10},
            "active_user_transactions": 0,
            "active_collector_sessions": 0,
        }
        result = _capacity_gate(capacity, log_limit=70, required_extra_bytes=2 * 1024**3)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["required_windows_free_gib"], 12.0)

    def test_lab_credentials_are_separate_and_reject_production_host(self) -> None:
        provider = _SecretProvider("lab-secret")
        settings, source = resolve_isolated_lab_connection_settings(
            {
                "CTI_LAB_MSSQL_HOST": "lab-sql",
                "CTI_LAB_MSSQL_DATABASE": "CologneTransitIntelligence",
                "CTI_LAB_MSSQL_USER": "lab-admin",
                "CTI_LAB_MSSQL_INSTANCE_ID": "isolated-lab-20261014",
            },
            secret_provider=provider,
        )
        self.assertEqual(settings.host, "lab-sql")
        self.assertEqual(source, "CTI_LAB_MSSQL_*_KEYCHAIN")
        with self.assertRaisesRegex(RuntimeConfigurationError, "production"):
            resolve_isolated_lab_connection_settings(
                {
                    "CTI_LAB_MSSQL_HOST": "DataAnalyst-VM",
                    "CTI_LAB_MSSQL_DATABASE": "CologneTransitIntelligence",
                    "CTI_LAB_MSSQL_USER": "lab-admin",
                    "CTI_LAB_MSSQL_INSTANCE_ID": "isolated-lab-20261014",
                    "CTI_LAB_MSSQL_PASSWORD": "lab-secret",
                }
            )

    def test_full_history_parity_and_powerbi_evidence_are_required_for_apply(self) -> None:
        parity = {
            "status": "PASS",
            "run": {
                "selected_is_full_history": False,
                "total_source_snapshot_count": 2,
                "selected_snapshot_count": 1,
                "units": [
                    {
                        "status": "VERIFIED",
                        "offline_parity": {"status": "PASS"},
                        "sql_readback_parity": {"status": "PASS"},
                    }
                ],
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
            json.dump(parity, handle)
            handle.flush()
            self.assertEqual(_parity_gate(Path(handle.name), require_full_history=True)["status"], "BLOCKED")

        powerbi = {
            "status": "PASS",
            "approved": True,
            "consumer_id": "powerbi-prod-dataset",
            "dataset_or_report": "Transit history",
            "validated_at_utc": "2026-10-14T12:00:00Z",
            "approved_by": "operator",
            "evidence_source": "REAL_POWER_BI_CONSUMER",
            "schema_contract_status": "PASS",
            "refresh_verified": True,
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
            json.dump(powerbi, handle)
            handle.flush()
            self.assertEqual(
                _powerbi_gate(
                    Path(handle.name),
                    now=datetime(2026, 10, 14, 12, 10, tzinfo=timezone.utc),
                )["status"],
                "PASS",
            )

    def test_retirement_requires_measured_external_and_operation_reserves(self) -> None:
        result = _retirement_reserve_gate({"approved": True})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("capacity_reserves", result["missing_fields"])

    def test_read_only_audit_handles_v1_v2_without_storage_v3_columns(self) -> None:
        audit = (
            ROOT / "sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql"
        ).read_text(encoding="utf-8")
        metadata = re.findall(
            r"\(N'stg', N'([^']+)', N'[^']+', N'([^']+)', ([01]), ([01]), \d+\)",
            audit,
        )
        self.assertTrue(metadata)
        self.assertIn("sys.columns", audit)
        self.assertIn("MISSING_OPTIONAL", audit)
        self.assertIn("capacity.representative_payload_schema", audit)
        self.assertIn("capacity.representative_payload_audit", audit)
        self.assertEqual(
            audit.count("CONVERT(bigint, 12) * 1024 * 1024 * 1024"),
            2,
        )
        self.assertNotIn("CONVERT(bigint, 12 * 1024 * 1024 * 1024)", audit)
        self.assertIn(
            "COL_NAME(dep.referenced_id, dep.referenced_minor_id)",
            audit,
        )
        self.assertIn("EXEC sys.sp_executesql @RepresentativePayloadSql", audit)
        self.assertNotIn("DATALENGTH(s.SnapshotJsonGzip)", audit)
        self.assertNotIn("DATALENGTH(c.SourcePathsJsonGzip)", audit)
        self.assertNotIn("ALTER TABLE", audit.upper())

        expected: dict[str, set[tuple[str, int]]] = {}
        for object_name, column_name, is_optional, _ in metadata:
            expected.setdefault(object_name, set()).add((column_name, int(is_optional)))

        v1_v2_physical = {
            "MddMacSourceSnapshot": {"SnapshotJson"},
            "MddMacCallObservation": {"SourcePathsJson", "SourceEventJson"},
            "MddMacTimingObservation": {
                "SourceEventIdentityBasisJson",
                "SourceEventIdentityMissingFieldsJson",
                "SourceEventIdentityAmbiguityJson",
                "SourcePathsJson",
            },
            "MddMacSituationEvidence": {
                "Description",
                "Detail",
                "AffectsJson",
                "AffectedEntityJson",
                "AffectedJourneyJson",
                "AffectedLineJson",
                "FieldPathsJson",
                "AffectedEntityFieldPathsJson",
                "SourceRepresentationJson",
            },
            "MddMacServiceSectionEvidence": {
                "Description",
                "RouteDescription",
                "Via",
                "ModeSourceJson",
                "SourcePathsJson",
                "SourceRepresentationJson",
            },
            "MddMacAttributeEvidence": {
                "TextSourceText",
                "CodeSourcePathsJson",
                "TextSourcePathsJson",
                "SourceFieldPathsJson",
                "SourceRepresentationJson",
            },
            "MddMacResponseError": {"ErrorValue"},
            "MddMacParserGap": {"SourceRepresentationJson"},
        }
        self.assertEqual(set(expected), set(v1_v2_physical))
        missing_required = {
            (object_name, column_name)
            for object_name, entries in expected.items()
            for column_name, is_optional in entries
            if is_optional == 0 and column_name not in v1_v2_physical[object_name]
        }
        missing_optional = {
            (object_name, column_name)
            for object_name, entries in expected.items()
            for column_name, is_optional in entries
            if is_optional == 1 and column_name not in v1_v2_physical[object_name]
        }
        self.assertEqual(missing_required, set())
        self.assertIn(("MddMacSourceSnapshot", "SnapshotJsonGzip"), missing_optional)
        self.assertGreaterEqual(len(missing_optional), 10)


if __name__ == "__main__":
    unittest.main()

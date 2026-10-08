from __future__ import annotations

import importlib.util
import plistlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    TIERED50_SELECTION_POLICY_VERSION,
    SamplingEra,
)
from cologne_transit_collector.collector import (  # noqa: E402
    Collector,
    LiveModeBlocked,
)
from cologne_transit_collector.manifest import (  # noqa: E402
    LEGACY7_SAMPLING_TARGET_IDS,
    load_default_manifest,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from cologne_transit_collector.quota import (  # noqa: E402
    InMemoryUsageProvider,
    QuotaGuard,
    UsageSnapshot,
)
from package_source_zip import is_forbidden_source_member  # noqa: E402
from tiered50_production_preflight import (  # noqa: E402
    run_preflight,
    validate_claim_identity,
    validate_manifest,
    validate_schedule,
)
from validate_tiered50_migration import validate_static  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = _load_module("install_macos_launch_agent_tiered_test", ROOT / "scripts" / "install_macos_launch_agent.py")
AVAILABILITY = _load_module(
    "audit_legacy7_availability_tiered_test",
    ROOT / "scripts" / "audit_legacy7_availability.py",
)


class _ClaimCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement, params=None) -> None:
        self.statements.append(str(statement))

    def fetchone(self):
        if self.statements and "SCOPE_IDENTITY" in self.statements[-1]:
            return {"TargetClaimEventId": 17}
        return None


class _ClaimConnection:
    def __init__(self) -> None:
        self.cursor_instance = _ClaimCursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class Tiered50ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)

    def test_manifest_binds_legacy_ids_and_allocates_new_ids_8_through_50(self) -> None:
        identities = dict(LEGACY7_SAMPLING_TARGET_IDS)
        identities.update(
            {
                target.source_parent_station_id: sampling_target_id
                for sampling_target_id, target in enumerate(self.manifest.new_tiered50_targets, start=8)
            }
        )
        bound = self.manifest.bind_production_sampling_target_ids(identities)
        self.assertEqual(
            {int(target.production_sampling_target_id or 0) for target in bound.targets},
            set(range(1, 51)),
        )
        self.assertEqual(
            {target.source_parent_station_id: int(target.production_sampling_target_id or 0)
             for target in bound.legacy7_targets},
            LEGACY7_SAMPLING_TARGET_IDS,
        )
        self.assertTrue(all(target.selection_policy_version == TIERED50_SELECTION_POLICY_VERSION for target in bound.targets))

    def test_offline_preflight_reconciles_approved_invariants_without_live_requests(self) -> None:
        result = run_preflight()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["production_status"], "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED")
        self.assertTrue(result["legacy7_retired"])
        self.assertEqual(result["stations_actually_collecting"], 0)
        self.assertFalse(result["tiered50_active"])
        self.assertEqual(result["mdd_requests_issued"], 0)
        self.assertEqual(validate_manifest()["observed"]["target_count"], 50)
        self.assertEqual(validate_schedule()["reconciliation"]["super_cycle_total"], 110)
        self.assertEqual(validate_claim_identity()["unique_target_bucket_keys"], 300)

    def test_static_migration_is_additive_and_exact(self) -> None:
        result = validate_static()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["materialized_row_count"], 43)
        self.assertFalse(result["historical_sampling_target_rows_modified"])

    def test_tiered_launch_agent_is_disabled_and_separately_guarded(self) -> None:
        path = ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-tiered50-collector.plist"
        with path.open("rb") as handle:
            template = plistlib.load(handle)
        self.assertTrue(template["Disabled"])
        self.assertFalse(template["RunAtLoad"])
        self.assertEqual(template["StartInterval"], 300)
        self.assertEqual(template["EnvironmentVariables"]["COLLECTOR_MAC_SQL_MODE"], "TIERED50")
        self.assertEqual(template["EnvironmentVariables"]["COLLECTOR_MAC_TIERED50_ENABLED"], "0")
        self.assertNotIn("MDD_API_KEY", template["EnvironmentVariables"])

        rendered = INSTALLER.build_installed_plist(activate=False, mode="tiered50")
        self.assertTrue(rendered["Disabled"])
        self.assertEqual(rendered["ProgramArguments"][-2:], ["--mode", "tiered50"])
        self.assertEqual(rendered["EnvironmentVariables"]["COLLECTOR_TIERED50_TARGET_SCOPE"], "UNAPPROVED")

    def test_legacy7_production_mode_and_activation_are_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            INSTALLER.build_installed_plist(activate=True, mode="legacy7")

        collector = Collector(
            manifest=self.manifest,
            persistence=object(),
            mdd_client=object(),
            quota_guard=QuotaGuard(InMemoryUsageProvider(UsageSnapshot())),
        )
        with self.assertRaises(LiveModeBlocked):
            collector.run_once(mode="mac-sql", allow_live=True)

    def test_authorization_key_reuses_pending_permit_and_rejects_replay(self) -> None:
        provider = InMemoryUsageProvider(UsageSnapshot())
        guard = QuotaGuard(provider)
        key = "run-1:audit-7:attempt-1"
        first = guard.allow_initial(at_utc=datetime(2026, 10, 8, tzinfo=timezone.utc), authorization_key=key)
        second = guard.allow_initial(at_utc=datetime(2026, 10, 8, tzinfo=timezone.utc), authorization_key=key)
        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertEqual(first.permit_id, second.permit_id)
        self.assertEqual(second.decision, "DUPLICATE_AUTHORIZATION_REUSED")

        conflict = guard.allow_retry(at_utc=datetime(2026, 10, 8, tzinfo=timezone.utc), authorization_key=key)
        self.assertFalse(conflict.allowed)
        self.assertEqual(conflict.decision, "DUPLICATE_AUTHORIZATION_CONFLICT")

        guard.finalize_no_network(first)
        replay = guard.allow_initial(at_utc=datetime(2026, 10, 8, tzinfo=timezone.utc), authorization_key=key)
        self.assertFalse(replay.allowed)
        self.assertEqual(replay.decision, "DUPLICATE_AUTHORIZATION_FINALIZED")

    def test_source_package_excludes_runtime_state_and_nested_archives(self) -> None:
        for name in ("nested.zip", "collector.sqlite3", "mdd-collector.log", "credentials.json", ".env"):
            self.assertTrue(is_forbidden_source_member(name), name)
        self.assertFalse(is_forbidden_source_member("config/tiered50_runtime_manifest.json"))
        self.assertFalse(is_forbidden_source_member("prompts/S109.md"))

    def test_legacy_claims_keep_existing_identity_until_tiered_schema_is_applied(self) -> None:
        legacy_target = self.manifest.target_by_id("de:05315:11110")
        legacy_target = legacy_target.__class__(
            **{
                **legacy_target.__dict__,
                "sampling_era": SamplingEra.LEGACY7,
                "legacy_sampling_target_id": 1,
                "panel_version": "MDD_MAC_LEGACY7_EXISTING_V1",
            }
        )
        legacy_connection = _ClaimConnection()
        persistence = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: legacy_connection,
            collection_context="MAC_LEGACY7_PRODUCTION",
        )
        result = persistence.claim_target(
            run_id="legacy-run",
            target=legacy_target,
            logical_due_bucket=1,
            claimed_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result.won)
        inserts = [statement for statement in legacy_connection.cursor_instance.statements if "INSERT INTO" in statement]
        self.assertTrue(inserts)
        self.assertTrue(all("TargetIdentity" in statement for statement in inserts))
        self.assertTrue(all("SamplingTargetId" not in statement for statement in inserts))

    def test_tiered_claims_use_stable_sampling_identity(self) -> None:
        target = self.manifest.target_by_id("de:05315:11111")
        target = target.__class__(
            **{
                **target.__dict__,
                "production_sampling_target_id": "8",
                "selection_policy_version": TIERED50_SELECTION_POLICY_VERSION,
            }
        )
        connection = _ClaimConnection()
        persistence = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: connection,
            collection_context=MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
        )
        result = persistence.claim_target(
            run_id="tiered-run",
            target=target,
            logical_due_bucket=1,
            claimed_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        )
        self.assertTrue(result.won)
        inserts = [statement for statement in connection.cursor_instance.statements if "INSERT INTO" in statement]
        self.assertTrue(any("SamplingTargetId" in statement for statement in inserts))

    def test_legacy_availability_audit_does_not_infer_vm_sleep_or_stability(self) -> None:
        result = AVAILABILITY.reconcile()
        self.assertEqual(result["status"], "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED")
        self.assertEqual(result["missing_buckets_within_observed_window"], [])
        self.assertEqual(result["evidence_window"]["bucket_span_minutes"], 15)
        self.assertEqual(result["availability"]["missing_bucket_cause"], "NOT_INFERRED")
        self.assertFalse(result["launchd"]["continuous_execution_proven"])


if __name__ == "__main__":
    unittest.main()

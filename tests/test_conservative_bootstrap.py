from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.conservative_bootstrap import (  # noqa: E402
    BOOTSTRAP_APPROVAL_STATEMENT,
    BOOTSTRAP_AUTHORIZATION_MODE,
    ConservativeBootstrapError,
    build_conservative_bootstrap_authorization,
    validate_stored_bootstrap_row,
)
from cologne_transit_collector.quota_rollover import (  # noqa: E402
    AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
    CONSERVATIVE_ROLLOVER_MODE,
    QuotaRolloverError,
    build_automatic_rollover_period,
    build_reset_contract,
    bounded_provider_period_for_instant,
    global_utc_offset_envelope_period_for_instant,
    provider_period_for_instant,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from register_mdd_conservative_bootstrap import run as run_bootstrap_registration  # noqa: E402
from register_mdd_quota_reset_contract import run as run_reset_contract_registration  # noqa: E402
import register_mdd_quota_reset_contract as reset_contract_script  # noqa: E402


PROPOSAL = ROOT / "docs" / "operational" / "mdd-october-conservative-bootstrap-proposal.json"
DASHBOARD = ROOT / "docs" / "operational" / "mdd-october-monitoring-dashboard-evidence.json"
APPROVED_AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


class ConservativeBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
        cls.dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    def _authorization(self, local_usage: dict[str, object] | None = None) -> dict[str, object]:
        return build_conservative_bootstrap_authorization(
            proposal=self.proposal,
            dashboard=self.dashboard,
            local_usage=local_usage or {"confirmed": 7, "unknown": 2, "pending": 1},
            approved_at_utc=APPROVED_AT,
            now_utc=datetime(2026, 10, 8, 12, 1, tzinfo=timezone.utc),
        )

    def test_conservative_accounting_seeds_dashboard_and_all_local_classes(self) -> None:
        authorization = self._authorization({"confirmed": 7, "unknown": 2, "pending": 1, "released_no_network": 4})
        self.assertEqual(authorization["authorization_mode"], BOOTSTRAP_AUTHORIZATION_MODE)
        self.assertEqual(authorization["accounted_total_at_authorization"], 564)
        self.assertEqual(authorization["remaining_headroom"]["retry_protection_boundary"], 206936)
        self.assertFalse(authorization["provider_scope_verified"])
        self.assertFalse(authorization["provider_measurement_cutoff_established"])
        self.assertIn("NOT_ESTABLISHED", authorization["evidence_json"])
        research = self._authorization(
            {
                "confirmed": 7,
                "unknown": 2,
                "pending": 1,
                "known_untracked_research_attempts": 73,
                "untracked_research_attempt_contexts": ["LIVE_RESEARCH_SINGLE_1"],
            }
        )
        self.assertEqual(research["conservative_unknown_local_attempts"], 75)
        self.assertEqual(research["accounted_total_at_authorization"], 637)
        with self.assertRaises(ConservativeBootstrapError):
            self._authorization(
                {
                    "confirmed": 0,
                    "unknown": 0,
                    "pending": 0,
                    "known_untracked_research_attempts": 1,
                    "untracked_research_attempt_contexts": ["MAC_TIERED50_PRODUCTION"],
                }
            )

    def test_bootstrap_row_is_valid_only_inside_fixed_interval(self) -> None:
        authorization = self._authorization()
        row = {
            "AuthorizationId": 1,
            "AuthorizationKey": authorization["authorization_key"],
            "MonthKey": authorization["month_key"],
            "AuthorizationMode": authorization["authorization_mode"],
            "AccountingModel": authorization["accounting_model"],
            "ScreenshotCumulativeRequests": authorization["screenshot_cumulative_requests"],
            "ScreenshotMonthlyLimit": authorization["screenshot_monthly_limit"],
            "ScreenshotSha256": authorization["screenshot_sha256"],
            "ConfirmedLocalAttempts": authorization["confirmed_local_attempts"],
            "ConservativeUnknownLocalAttempts": authorization["conservative_unknown_local_attempts"],
            "PendingLocalReservations": authorization["pending_local_reservations"],
            "AccountedTotalAtAuthorization": authorization["accounted_total_at_authorization"],
            "ProviderQuotaCeiling": authorization["provider_quota_ceiling"],
            "NormalOperationalMaximum": authorization["normal_operational_maximum"],
            "RetryProtectionBoundary": authorization["retry_protection_boundary"],
            "ValidFromUtc": authorization["valid_from_utc"],
            "LocalUsageIncludedThroughUtc": authorization["local_usage_included_through_utc"],
            "StopAtUtc": authorization["stop_at_utc"],
            "OperatorApprovalAtUtc": authorization["operator_approval_at_utc"],
            "OperatorApprovalStatementHash": authorization["operator_approval_statement_hash"],
            "CredentialExclusivityConfirmed": authorization["credential_exclusivity_confirmed"],
            "ProviderScopeVerified": authorization["provider_scope_verified"],
            "ProviderMeasurementCutoffEstablished": authorization["provider_measurement_cutoff_established"],
            "ProviderResetTimezoneEstablished": authorization["provider_reset_timezone_established"],
            "UnexpectedExternalUsage": authorization["unexpected_external_usage"],
            "SourceEvidenceHash": authorization["source_evidence_hash"],
            "AuthorizationStatus": authorization["authorization_status"],
            "EvidenceJson": authorization["evidence_json"],
        }
        valid, reason = validate_stored_bootstrap_row(row, at_utc=APPROVED_AT)
        self.assertTrue(valid, reason)
        expired, reason = validate_stored_bootstrap_row(
            row,
            at_utc=datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(expired)
        self.assertIn("outside", reason)

        for field, value in (
            ("AccountingModel", "CENTRAL_AUTHORIZATION"),
            ("ScreenshotCumulativeRequests", 555),
            ("ScreenshotMonthlyLimit", 249999),
            ("ScreenshotSha256", "0" * 64),
            ("UnexpectedExternalUsage", True),
        ):
            invalid = dict(row)
            invalid[field] = value
            valid, _reason = validate_stored_bootstrap_row(invalid, at_utc=APPROVED_AT)
            self.assertFalse(valid, field)

    def test_bootstrap_rejects_unexpected_usage_and_expiry(self) -> None:
        with self.assertRaises(ConservativeBootstrapError):
            self._authorization({"confirmed": 7, "unknown": 0, "pending": 0, "unexpected_statuses": ["BROKEN"]})
        with self.assertRaises(ConservativeBootstrapError):
            self._authorization({"confirmed": 7, "unknown": 0, "pending": 0, "attempts_without_permit": 1})
        with self.assertRaises(ConservativeBootstrapError):
            self._authorization({"confirmed": 7, "unknown": 0, "pending": 0, "unexpected_external_usage": True})
        with self.assertRaises(ConservativeBootstrapError):
            build_conservative_bootstrap_authorization(
                proposal=self.proposal,
                dashboard=self.dashboard,
                local_usage={"confirmed": 0, "unknown": 0, "pending": 0},
                approved_at_utc=datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc),
                now_utc=datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc),
            )

    def test_explicit_apply_and_approval_are_both_required(self) -> None:
        result = run_bootstrap_registration(
            proposal_path=PROPOSAL,
            dashboard_path=DASHBOARD,
            apply=True,
            approve=False,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["sql_writes_performed"], 0)
        self.assertEqual(result["mdd_requests_issued"], 0)
        self.assertEqual(BOOTSTRAP_APPROVAL_STATEMENT.startswith("I explicitly approve"), True)


class AutomaticRolloverTests(unittest.TestCase):
    def _contract(self) -> dict[str, object]:
        return {
            "ContractKey": "MDD-RESET-CONTRACT-VERIFIED-1",
            "ContractStatus": "ACTIVE",
            "ResetModel": "CALENDAR_MONTH_PROVIDER_TIMEZONE",
            "ResetTimezone": "Europe/Berlin",
            "EffectiveFromUtc": datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc),
            "ActivationDelaySeconds": 1800,
            "ProviderQuotaCeiling": 250000,
            "CredentialExclusivityConfirmed": True,
            "ProviderResetBoundaryVerified": True,
            "SourceEvidenceHash": "a" * 64,
        }

    def test_rollover_calculates_dst_aware_period_and_delay(self) -> None:
        period = provider_period_for_instant(
            datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc),
            timezone_name="Europe/Berlin",
        )
        self.assertEqual(period.month_key, "2026-11")
        self.assertEqual(period.start_utc, datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(period.end_utc, datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(period.activation_not_before_utc, datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc))
        rollover = build_automatic_rollover_period(
            contract=self._contract(),
            at_utc=datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(rollover["accounting_model"], AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL)
        self.assertEqual(rollover["baseline_components"]["total"], 0)
        self.assertTrue(rollover["provider_metadata_verified"])

    def test_rollover_blocks_before_delay_or_without_verified_contract(self) -> None:
        with self.assertRaises(QuotaRolloverError):
            build_automatic_rollover_period(
                contract=self._contract(),
                at_utc=datetime(2026, 10, 31, 23, 29, tzinfo=timezone.utc),
            )
        with self.assertRaises(QuotaRolloverError):
            build_reset_contract(
                evidence=json.loads((ROOT / "docs" / "operational" / "mdd-quota-reset-contract.json").read_text()),
                contract_key="MDD-RESET-CONTRACT-UNVERIFIED",
                reset_timezone="Europe/Berlin",
                effective_from_utc=datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc),
                approved_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            )

    def test_rollover_registration_retains_approval_provenance_without_changing_source_hash(self) -> None:
        contract = build_reset_contract(
            evidence={
                "reset_contract_status": "VERIFIED",
                "provider_reset_boundary_verified": True,
                "credential_exclusivity_confirmed": True,
                "source_evidence_hash": "d" * 64,
            },
            contract_key="MDD-RESET-PROVENANCE-1",
            reset_timezone="Europe/Berlin",
            effective_from_utc=datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc),
            approved_at_utc=APPROVED_AT,
        )
        self.assertEqual(contract["source_evidence_hash"], "d" * 64)
        self.assertEqual(contract["approval_provenance"]["approved_at_utc"], "2026-10-08T12:00:00Z")
        self.assertIn("registration_approval", json.loads(contract["evidence_json"]))

    def test_reset_contract_cli_reports_unresolved_dependency_without_sql_write(self) -> None:
        result = run_reset_contract_registration()
        self.assertEqual(result["status"], "AWAITING_PROVIDER_RESET_EVIDENCE")
        self.assertEqual(result["sql_writes_performed"], 0)

    def test_reset_contract_registration_uses_the_separate_administrator_connection(self) -> None:
        evidence = {
            "reset_contract_status": "VERIFIED",
            "provider_reset_boundary_verified": True,
            "credential_exclusivity_confirmed": True,
            "source_evidence_hash": "c" * 64,
        }
        captured: dict[str, object] = {}

        class Persistence:
            def __init__(self, *, settings) -> None:  # type: ignore[no-untyped-def]
                captured["settings"] = settings

            def register_quota_reset_contract(self, *, contract):  # type: ignore[no-untyped-def]
                captured["contract"] = contract
                return {"status": "APPLIED", "writes_performed": 1}

        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "reset-evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            with patch.object(
                reset_contract_script,
                "resolve_administrator_connection_settings",
                return_value=(
                    MssqlConnectionSettings(
                        host="admin-host",
                        database="CologneTransitIntelligence",
                        user="separate-admin",
                        password="admin-secret",
                    ),
                    "MSSQL_ADMIN_*_KEYCHAIN",
                ),
            ), patch.object(reset_contract_script, "MssqlPersistence", Persistence):
                result = run_reset_contract_registration(
                    evidence_path=evidence_path,
                    contract_key="MDD-RESET-ADMIN-BOUND-1",
                    reset_timezone="Europe/Berlin",
                    effective_from=datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc),
                    approved_at=APPROVED_AT,
                    apply=True,
                    approve=True,
                )
        settings = captured["settings"]
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["administrator_credential_source"], "MSSQL_ADMIN_*_KEYCHAIN")
        self.assertEqual(settings.user, "separate-admin")  # type: ignore[union-attr]
        self.assertNotEqual(settings.user, "CologneTransitCollector")  # type: ignore[union-attr]
        self.assertNotIn("admin-secret", json.dumps(result))

    def test_runtime_rollover_calls_guarded_sql_procedure_with_contract_locking(self) -> None:
        contract = self._contract()

        class Cursor:
            def __init__(self) -> None:
                self.statements: list[str] = []
                self.params: list[object] = []
                self.result: list[dict[str, object]] = []

            def execute(self, statement: str, params=()) -> None:
                self.statements.append(statement)
                self.params.append(params)
                if "FROM ctl.MddMacQuotaResetContract" in statement:
                    self.result = [contract]
                else:
                    self.result = []

            def fetchone(self):
                return self.result[0] if self.result else None

            def fetchall(self):
                return list(self.result)

        class Connection:
            def __init__(self) -> None:
                self.cursor_value = Cursor()

            def cursor(self):
                return self.cursor_value

            def close(self) -> None:
                pass

        connection = Connection()
        adapter = MssqlPersistence(
            MssqlConnectionSettings(host="sql", user="runtime", password="not-used"),
            connection_factory=lambda: connection,
        )
        result = adapter.ensure_automatic_quota_rollover(
            at_utc=datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(result["status"], "APPLIED_OR_IDEMPOTENT")
        self.assertTrue(any("EXEC ctl.uspEnsureMddMacQuotaPeriod" in sql for sql in connection.cursor_value.statements))
        migration = (
            Path(__file__).parents[1]
            / "sql"
            / "mdd-mac-collector"
            / "create-source-faithful-persistence.sql"
        ).read_text(encoding="utf-8")
        procedure = migration[migration.index("CREATE OR ALTER PROCEDURE ctl.uspEnsureMddMacQuotaPeriod") :]
        self.assertIn("UPDLOCK", procedure)
        self.assertIn("HOLDLOCK", procedure)
        self.assertIn("sp_getapplock", procedure)
        self.assertIn("@LockOwner = N'Transaction'", procedure)
        self.assertIn("@LockMode = N'Exclusive'", procedure)

    def test_sql_rollover_boundary_uses_sql_server_utc_not_caller_clock(self) -> None:
        migration = (
            Path(__file__).parents[1]
            / "sql"
            / "mdd-mac-collector"
            / "create-source-faithful-persistence.sql"
        ).read_text(encoding="utf-8")
        procedure = migration[migration.index("CREATE OR ALTER PROCEDURE ctl.uspEnsureMddMacQuotaPeriod") :]
        self.assertIn("DECLARE @server_utc DATETIME2(7) = SYSUTCDATETIME();", procedure)
        self.assertIn("@server_utc < @ProviderPeriodStartUtc", procedure)
        self.assertIn("@server_utc >= @ProviderPeriodEndUtc", procedure)
        self.assertIn("@server_utc < DATEADD(SECOND, @activation_delay_seconds", procedure)
        self.assertIn("@ProviderPeriodStartUtc >= @ProviderPeriodEndUtc", procedure)
        self.assertNotIn("IF @AtUtc < DATEADD(SECOND, @activation_delay_seconds", procedure)

    def test_bounded_calendar_month_policy_waits_for_latest_supported_timezone(self) -> None:
        period = bounded_provider_period_for_instant(
            datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
            timezone_names=("America/Adak", "Europe/Berlin"),
        )
        self.assertEqual(period.month_key, "2026-11")
        self.assertEqual(period.activation_not_before_utc, datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc))
        contract = {
            "ContractKey": "MDD-RESET-BOUNDED-1",
            "ContractStatus": "ACTIVE",
            "ResetModel": CONSERVATIVE_ROLLOVER_MODE,
            "ResetTimezone": "BOUNDED_TIMEZONE_ENVELOPE",
            "SupportedResetTimezones": ["America/Adak", "Europe/Berlin"],
            "EffectiveFromUtc": datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            "ActivationDelaySeconds": 1800,
            "ProviderQuotaCeiling": 250000,
            "CredentialExclusivityConfirmed": True,
            "ProviderResetBoundaryVerified": False,
            "CalendarMonthResetModelEstablished": True,
            "OperatorAssumptionApproved": True,
            "SourceEvidenceHash": "b" * 64,
        }
        rollover = build_automatic_rollover_period(
            contract=contract,
            at_utc=datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
        )
        self.assertFalse(rollover["provider_metadata_verified"])
        self.assertTrue(rollover["bounded_timezone_uncertainty"])
        self.assertEqual(rollover["provider_period_timezone"], "BOUNDED_TIMEZONE_ENVELOPE")

    def test_global_utc_offset_envelope_waits_until_1230_and_stops_before_next_period(self) -> None:
        period = global_utc_offset_envelope_period_for_instant(
            datetime(2026, 11, 1, 12, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(period.month_key, "2026-11")
        self.assertEqual(period.start_utc, datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(period.activation_not_before_utc, datetime(2026, 11, 1, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(period.end_utc, datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc))

    def test_bounded_policy_requires_explicit_operator_approval(self) -> None:
        evidence = {
            "reset_contract_status": "APPROVED_ASSUMPTION",
            "provider_monthly_reset_model_established": True,
            "credential_exclusivity_confirmed": True,
            "source_evidence_hash": "c" * 64,
        }
        with self.assertRaises(QuotaRolloverError):
            build_reset_contract(
                evidence=evidence,
                contract_key="MDD-RESET-BOUNDED-NOT-APPROVED",
                reset_timezone="",
                supported_reset_timezones=["America/Adak", "Europe/Berlin"],
                effective_from_utc=datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc),
                approved_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            )
        contract = build_reset_contract(
            evidence=evidence,
            contract_key="MDD-RESET-BOUNDED-APPROVED",
            reset_timezone="",
            supported_reset_timezones=["America/Adak", "Europe/Berlin"],
            effective_from_utc=datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc),
            approved_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            approve_bounded_assumption=True,
        )
        self.assertEqual(contract["reset_model"], CONSERVATIVE_ROLLOVER_MODE)
        self.assertFalse(contract["provider_reset_boundary_verified"])


if __name__ == "__main__":
    unittest.main()

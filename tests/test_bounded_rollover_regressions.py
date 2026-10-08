from __future__ import annotations

import json
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings, MssqlPersistence  # noqa: E402
from cologne_transit_collector.quota import (  # noqa: E402
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
)
from cologne_transit_collector.quota_baseline import validate_stored_baseline_row  # noqa: E402
from cologne_transit_collector.quota_rollover import (  # noqa: E402
    AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
    BOUNDED_TIMEZONE_ENVELOPE_MARKER,
    CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL,
    GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
    RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION,
    RESET_CONTRACT_STATUS_VERIFIED_PROVIDER,
    bounded_provider_period_for_month,
    provider_period_for_month,
)


UTC = timezone.utc


def _contract(model: str, source_hash: str) -> dict[str, Any]:
    bounded = model == "CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY"
    return {
        "ContractId": 1,
        "ContractKey": "MDD-RESET-PRODUCTION-TEST",
        "ProviderName": "MDD NRW",
        "ResetModel": model,
        "ResetTimezone": BOUNDED_TIMEZONE_ENVELOPE_MARKER if bounded else "Europe/Berlin",
        "SqlServerResetTimezone": None if bounded else "W. Europe Standard Time",
        "EffectiveFromUtc": datetime(2026, 10, 1, tzinfo=UTC),
        "ActivationDelaySeconds": 1800,
        "ProviderQuotaCeiling": 250000,
        "CredentialExclusivityConfirmed": True,
        "ProviderResetBoundaryVerified": not bounded,
        "CalendarMonthResetModelEstablished": True,
        "OperatorAssumptionApproved": bounded,
        "SupportedResetTimezonesJson": (
            json.dumps(list(GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES), sort_keys=True) if bounded else None
        ),
        "SourceEvidenceHash": source_hash,
        "EvidenceJson": "{}",
        "ContractStatus": "ACTIVE",
        "RecordedAtUtc": datetime(2026, 10, 1, tzinfo=UTC),
        "RevokedAtUtc": None,
        "RevocationReason": None,
    }


def _baseline(contract: dict[str, Any], model: str, at: datetime) -> dict[str, Any]:
    month_key = "2026-11"
    if model == CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL:
        period = bounded_provider_period_for_month(
            month_key,
            timezone_names=GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
            activation_delay_seconds=1800,
        )
        timezone_name = BOUNDED_TIMEZONE_ENVELOPE_MARKER
    else:
        period = provider_period_for_month(month_key, timezone_name="Europe/Berlin", activation_delay_seconds=0)
        timezone_name = "Europe/Berlin"
    return {
        "MonthKey": month_key,
        "ProviderQuotaCeiling": 250000,
        "NormalOperationalMaximum": 210000,
        "RetryProtectionBoundary": 207500,
        "ConfirmedProviderAttempts": 0,
        "ConservativeUnknownAttempts": 0,
        "ExplicitSharedConsumerReservedAttempts": 0,
        "AccountingStatus": "COMPLETE",
        "IncludesAllSharedConsumers": True,
        "BaselineAsOfUtc": at - timedelta(minutes=1),
        "LocalUsageIncludedThroughUtc": period.start_utc,
        "SourceDescription": "Automatic approved MDD calendar-month rollover",
        "AccountingModel": model,
        "ProviderMeasurementCutoffUtc": period.start_utc,
        "StaleAfterUtc": period.end_utc,
        "SourceEvidenceHash": contract["SourceEvidenceHash"],
        "ReconciliationKey": f"AUTO-ROLLOVER:{contract['ContractKey']}:{month_key}",
        "ProviderPeriodTimezone": timezone_name,
        "ProviderPeriodStartUtc": period.start_utc,
        "ProviderPeriodEndUtc": period.end_utc,
    }


class _QuotaDatabase:
    """Stateful DB-API double exercising the production adapter's SQL path."""

    def __init__(self, *, baseline: dict[str, Any], contract: dict[str, Any], now: datetime) -> None:
        self.baseline = baseline
        self.contract = contract
        self.now = now
        self.permits: list[dict[str, Any]] = []
        self.quota_totals_override: dict[str, int] | None = None

    def connect(self) -> "_QuotaConnection":
        return _QuotaConnection(self)

    def totals(self, params: tuple[Any, ...], *, include_cutoff: bool) -> dict[str, int]:
        if self.quota_totals_override is not None:
            return {**self.quota_totals_override, "invocations": self.quota_totals_override.get("invocations", 0)}
        month = str(params[-3])
        cutoff = params[-2]
        observed = params[-1]
        totals = {"confirmed": 0, "conservative": 0, "pending": 0, "invocations": 0}
        for permit in self.permits:
            reserved = permit["ReservedAtUtc"]
            if permit["MonthKey"] != month or reserved > observed:
                continue
            if reserved < cutoff or (reserved == cutoff and not include_cutoff):
                continue
            status = permit["Status"]
            if status == HTTP_PROVIDER_ATTEMPT_CONFIRMED:
                totals["confirmed"] += int(permit["Amount"])
            elif status == CONSERVATIVE_LIVE_ATTEMPT:
                totals["conservative"] += int(permit["Amount"])
            elif status == PENDING_AUTHORIZATION:
                totals["pending"] += int(permit["Amount"])
            if permit.get("LiveClientInvocation"):
                totals["invocations"] += int(permit["Amount"])
        return totals


class _QuotaConnection:
    def __init__(self, database: _QuotaDatabase) -> None:
        self.database = database
        self.cursor_value = _QuotaCursor(database)

    def cursor(self) -> "_QuotaCursor":
        return self.cursor_value

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _QuotaCursor:
    def __init__(self, database: _QuotaDatabase) -> None:
        self.database = database
        self.result: list[Any] = []
        self.rowcount = 1

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.result = []
        self.rowcount = 1
        lowered = sql.casefold()
        if "sysutcdatetime" in lowered and "authoritativeutc" in lowered:
            self.result = [{"AuthoritativeUtc": self.database.now}]
        elif "from ctl.mddmacquotaresetcontract" in lowered:
            self.result = [self.database.contract]
        elif "from ctl.mddmacsharedquotaaccounting" in lowered:
            self.result = [self.database.baseline]
        elif "update ctl.mddmacquotapermit" in lowered:
            if "crash_recovery_conservative" in repr(params).casefold():
                self.rowcount = 0
            else:
                status = str(params[0])
                permit_id = str(params[-2])
                matches = [permit for permit in self.database.permits if str(permit["PermitId"]) == permit_id]
                self.rowcount = len(matches)
                for permit in matches:
                    if permit["Status"] == PENDING_AUTHORIZATION:
                        permit["Status"] = status
        elif "select permitid, monthkey, decisionkind" in lowered:
            key = str(params[0])
            self.result = [permit for permit in self.database.permits if permit.get("AuthorizationKey") == key]
        elif "select status, amount from ctl.mddmacquotapermit" in lowered:
            permit_id = str(params[0])
            self.result = [
                {"Status": permit["Status"], "Amount": permit["Amount"]}
                for permit in self.database.permits
                if str(permit["PermitId"]) == permit_id
            ]
        elif "select status from ctl.mddmacquotapermit" in lowered:
            permit_id = str(params[0])
            self.result = [
                {"Status": permit["Status"]}
                for permit in self.database.permits
                if str(permit["PermitId"]) == permit_id
            ]
        elif "insert into ctl.mddmacquotapermit" in lowered:
            permit_id, month, decision_kind, scope, amount, boundary, reserved_at, auth_key, status = params
            self.database.permits.append(
                {
                    "PermitId": str(permit_id),
                    "MonthKey": str(month),
                    "DecisionKind": str(decision_kind),
                    "ReservationScope": str(scope),
                    "Amount": int(amount),
                    "Boundary": int(boundary),
                    "ReservedAtUtc": reserved_at,
                    "AuthorizationKey": auth_key,
                    "Status": str(status),
                    "LiveClientInvocation": False,
                }
            )
        elif "from ctl.mddmacquotapermit" in lowered and "liveclientinvocation" in lowered:
            self.result = [self.database.totals(params, include_cutoff=">=" in lowered)]
        elif "exec ctl.uspensuremddmacquotaperiod" in lowered:
            self.result = []
        elif "set xact_abort on" in lowered or "commit transaction" in lowered:
            self.result = []
        else:
            raise AssertionError(f"unexpected quota SQL: {sql}")

    def fetchone(self) -> Any:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[Any]:
        return list(self.result)


class BoundedRolloverRegressionTests(unittest.TestCase):
    def _adapter(self, *, bounded: bool) -> tuple[MssqlPersistence, _QuotaDatabase, datetime, dict[str, Any]]:
        source_hash = ("b" if bounded else "a") * 64
        model = (
            "CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY"
            if bounded
            else "CALENDAR_MONTH_PROVIDER_TIMEZONE"
        )
        at = datetime(2026, 11, 1, 12, 30, tzinfo=UTC) if bounded else datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        contract = _contract(model, source_hash)
        accounting_model = (
            CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL if bounded else AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL
        )
        baseline = _baseline(contract, accounting_model, at)
        database = _QuotaDatabase(baseline=baseline, contract=contract, now=at)
        adapter = MssqlPersistence(
            MssqlConnectionSettings(host="sql", user="runtime", password="test-only"),
            connection_factory=database.connect,
        )
        return adapter, database, at, baseline

    def test_bounded_marker_is_not_interpreted_as_an_iana_timezone(self) -> None:
        adapter, database, at, baseline = self._adapter(bounded=True)
        valid, reason = validate_stored_baseline_row(baseline, at_utc=at)
        self.assertFalse(valid)
        self.assertIn("registered bounded reset contract", reason)
        valid, reason = validate_stored_baseline_row(
            baseline,
            at_utc=at,
            registered_contract=database.contract,
        )
        self.assertTrue(valid, reason)
        usage = adapter.current_month_usage(at_utc=at)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertTrue(usage["state_available"])
        self.assertFalse(usage["provider_period_metadata_verified"])
        self.assertEqual(usage["provider_period_metadata_source"], "OPERATOR_APPROVED_BOUNDED_ASSUMPTION")
        self.assertEqual(usage["contract_verification_status"], RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION)

    def test_bounded_contract_identity_envelope_and_marker_are_fail_closed(self) -> None:
        _adapter, database, at, baseline = self._adapter(bounded=True)
        for field, value in (
            ("SourceEvidenceHash", "c" * 64),
            ("ResetTimezone", "UTC"),
            ("SupportedResetTimezonesJson", json.dumps(["Europe/Berlin"])),
            ("ContractStatus", "REVOKED"),
        ):
            contract = dict(database.contract)
            contract[field] = value
            valid, _reason = validate_stored_baseline_row(
                baseline,
                at_utc=at,
                registered_contract=contract,
            )
            self.assertFalse(valid, field)

    def test_exact_contract_keeps_provider_verification_and_dst_boundaries(self) -> None:
        adapter, database, at, baseline = self._adapter(bounded=False)
        valid, reason = validate_stored_baseline_row(
            baseline,
            at_utc=at,
            registered_contract=database.contract,
        )
        self.assertTrue(valid, reason)
        usage = adapter.current_month_usage(at_utc=at)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertTrue(usage["provider_period_metadata_verified"])
        self.assertEqual(usage["contract_verification_status"], RESET_CONTRACT_STATUS_VERIFIED_PROVIDER)

    def test_bounded_period_start_is_counted_once_and_released_no_network_is_not_usage(self) -> None:
        adapter, database, at, baseline = self._adapter(bounded=True)
        start = baseline["ProviderPeriodStartUtc"]
        database.permits.extend(
            [
                {"PermitId": str(uuid.uuid4()), "MonthKey": "2026-11", "Amount": 1, "Status": HTTP_PROVIDER_ATTEMPT_CONFIRMED, "ReservedAtUtc": start, "LiveClientInvocation": True},
                {"PermitId": str(uuid.uuid4()), "MonthKey": "2026-11", "Amount": 1, "Status": CONSERVATIVE_LIVE_ATTEMPT, "ReservedAtUtc": start + timedelta(microseconds=1), "LiveClientInvocation": True},
                {"PermitId": str(uuid.uuid4()), "MonthKey": "2026-11", "Amount": 1, "Status": PENDING_AUTHORIZATION, "ReservedAtUtc": start + timedelta(minutes=1), "LiveClientInvocation": False},
                {"PermitId": str(uuid.uuid4()), "MonthKey": "2026-11", "Amount": 1, "Status": RELEASED_NO_NETWORK, "ReservedAtUtc": start + timedelta(minutes=2), "LiveClientInvocation": False},
                {"PermitId": str(uuid.uuid4()), "MonthKey": "2026-11", "Amount": 1, "Status": HTTP_PROVIDER_ATTEMPT_CONFIRMED, "ReservedAtUtc": start - timedelta(microseconds=1), "LiveClientInvocation": True},
            ]
        )
        usage = adapter.current_month_usage(at_utc=at)
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage["known_total"], 3)
        self.assertEqual(usage["confirmed_provider_attempts"], 1)
        self.assertEqual(usage["conservative_live_attempts"], 1)
        self.assertEqual(usage["reserved_shared_usage"], 1)
        self.assertEqual(adapter.current_month_usage(at_utc=at)["known_total"], 3)  # type: ignore[index]

    def test_reservation_finalization_and_later_reservation_count_each_permit_once(self) -> None:
        adapter, database, at, _baseline = self._adapter(bounded=True)
        first = adapter.reserve_quota_attempt(
            decision_kind="INITIAL",
            boundary=210000,
            at_utc=at,
            authorization_key="bounded-first",
        )
        self.assertTrue(first["allowed"])
        self.assertEqual(first["known_total"], 0)
        adapter.finalize_quota_permit_http_response(permit_id=first["permit_id"], at_utc=at + timedelta(seconds=1))
        second = adapter.reserve_quota_attempt(
            decision_kind="INITIAL",
            boundary=210000,
            at_utc=at + timedelta(seconds=2),
            authorization_key="bounded-second",
        )
        self.assertTrue(second["allowed"])
        self.assertEqual(second["known_total"], 1)
        self.assertEqual(
            sum(1 for permit in database.permits if permit["Status"] == HTTP_PROVIDER_ATTEMPT_CONFIRMED),
            1,
        )

    def test_exact_and_bounded_models_keep_operational_and_retry_boundaries(self) -> None:
        for bounded in (False, True):
            adapter, database, at, _baseline = self._adapter(bounded=bounded)
            database.quota_totals_override = {"confirmed": 210000, "conservative": 0, "pending": 0}
            initial = adapter.reserve_quota_attempt(
                decision_kind="INITIAL",
                boundary=210000,
                at_utc=at,
            )
            self.assertFalse(initial["allowed"])
            self.assertEqual(initial["decision"], "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM")
            database.quota_totals_override = {"confirmed": 207500, "conservative": 0, "pending": 0}
            retry = adapter.reserve_quota_attempt(
                decision_kind="RETRY",
                boundary=207500,
                at_utc=at,
            )
            self.assertFalse(retry["allowed"])
            self.assertEqual(retry["decision"], "RETRY_DENIED_PROTECTION_BOUNDARY")


if __name__ == "__main__":
    unittest.main()
